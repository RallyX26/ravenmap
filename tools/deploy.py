"""The only way to put code on the box.

    python tools/deploy.py                 # deploy origin/main to the box
    python tools/deploy.py --dry-run       # say what it would do
    python tools/deploy.py --no-restart    # deploy, skip the restart decision

Needs SPARROW_BOX (user@host) and SPARROW_KEY (path to the key). The address is
not in this repo.

🚨 WHY THIS EXISTS: `git checkout origin/main -- <paths>` IS HOW THE BOX DRIFTED.
Deploying by naming paths works, right up until it doesn't:

  * it never advances HEAD, so the box sat on a commit from days earlier while
    serving current code - the git state stopped being a record of anything;
  * it leaves every change STAGED, so `git status` was permanently dirty and
    therefore permanently ignorable;
  * it only copies the paths you remember, so files nobody thought about
    (tools/, a hand-installed review_auth.py) silently diverged;
  * and a future `git pull` would then land in a conflict, during whatever
    emergency made you reach for it.

None of that was visible from the outside. The site was serving byte-identical
code the whole time, which is exactly why it went unnoticed for days.

THE RULES THIS ENFORCES, in order:

  1. Local must be CLEAN and PUSHED. You cannot deploy something that only
     exists on this machine - if it is not in origin, the box cannot have it
     and nobody else can see what shipped.
  2. Preflight must pass. Same gate as committing.
  3. The box pulls with --ff-only. A fast-forward cannot silently rewrite
     local edits: if the box has diverged it FAILS, loudly, before anything
     changes, and you go and look at why.
  4. Afterwards the box tree must equal origin/main exactly. Verified, not
     assumed - `git diff --stat origin/main` has to be empty.
  5. Anything imported at startup gets a restart, because a fix that is not
     running is not a fix ([[feedback-never-run-stale-code]]). Static files are
     read from disk per request and deliberately do NOT trigger one.
  6. The site is checked after. A deploy that ends with a 502 is not a deploy.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOX = os.environ.get("SPARROW_BOX", "")
KEY = os.environ.get("SPARROW_KEY", "")
REMOTE = "/opt/sparrowmap"
SERVICE = "sparrowmap.service"
SITE = "https://map.sparrowmap.com"

# Imported once at startup: changing these means the running process is stale.
# Everything else on the box (public/*.html, *.js, *.css, *.json) is read from
# disk per request and needs no restart - restarting for those would be a
# gratuitous outage.
RESTART_TRIGGERS = (".py",)

ok = True


def say(tag: str, msg: str) -> None:
    global ok
    if tag == "fail":
        ok = False
    print(f"  [{tag:^4}] {msg}")


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True).stdout.strip()


def ssh(cmd: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-i", KEY, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", BOX, cmd],
        capture_output=True, text=True, timeout=timeout)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="only for deploying a revert in an emergency")
    a = ap.parse_args()

    if not BOX or not KEY:
        sys.exit("set SPARROW_BOX and SPARROW_KEY (the address is not in this repo)")

    print("1. local state")
    dirty = [l for l in git("status", "--porcelain").splitlines()
             if not l.endswith(".bak")]
    if dirty:
        say("fail", f"{len(dirty)} uncommitted change(s) - commit or stash first")
        for l in dirty[:5]:
            print(f"         {l}")
    else:
        say(" ok ", "working tree clean")
    git("fetch", "--quiet", "origin")
    ahead = git("rev-list", "--count", "origin/main..HEAD")
    behind = git("rev-list", "--count", "HEAD..origin/main")
    if ahead and ahead != "0":
        say("fail", f"{ahead} commit(s) not pushed - the box can only get what "
                    f"origin has")
    elif behind and behind != "0":
        say("fail", f"local is {behind} behind origin/main - pull first")
    else:
        say(" ok ", f"in sync with origin/main ({git('rev-parse', '--short', 'HEAD')})")

    if not a.skip_preflight:
        print("\n2. preflight")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "preflight.py")],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            say(" ok ", "preflight passed")
        else:
            say("fail", "preflight FAILED - not deploying")
            print(r.stdout[-800:])

    if not ok:
        sys.exit("\n⛔ refusing to deploy")

    print("\n3. box state before")
    before = ssh(f"cd {REMOTE} && sudo -u sparrow git rev-parse --short HEAD").stdout.strip()
    say("info", f"box HEAD {before or '?'}")
    target = git("rev-parse", "--short", "HEAD")
    if before == target:
        say(" ok ", "box already at this commit")
    changed = ssh(f"cd {REMOTE} && sudo -u sparrow git fetch --quiet origin && "
                  f"sudo -u sparrow git diff --name-only HEAD origin/main").stdout.split()
    if changed:
        say("info", f"{len(changed)} file(s) will change: "
                    + ", ".join(changed[:6]) + ("…" if len(changed) > 6 else ""))
    needs_restart = any(f.endswith(RESTART_TRIGGERS) for f in changed)
    say("info", "restart needed: " + ("YES (python changed)" if needs_restart
                                      else "no (static files only)"))

    if a.dry_run:
        print("\ndry run - nothing sent.")
        return

    print("\n4. pull (--ff-only: cannot silently rewrite the box)")
    r = ssh(f"cd {REMOTE} && sudo -u sparrow git pull --ff-only origin main 2>&1")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or "error" in out.lower() or "fatal" in out.lower():
        print(out[-600:])
        sys.exit("\n⛔ the box could not fast-forward. It has diverged - go and "
                 "look BEFORE forcing anything.")
    say(" ok ", out.splitlines()[-1] if out else "up to date")

    print("\n5. verify the box matches origin/main exactly")
    head = ssh(f"cd {REMOTE} && sudo -u sparrow git rev-parse --short HEAD").stdout.strip()
    drift = ssh(f"cd {REMOTE} && sudo -u sparrow git diff --stat origin/main").stdout.strip()
    say(" ok " if head == target else "fail", f"box HEAD {head} (want {target})")
    say(" ok " if not drift else "fail",
        "tree matches origin/main" if not drift else f"TREE DIFFERS:\n{drift[:400]}")

    if needs_restart and not a.no_restart:
        print("\n6. restart (python changed, so the running process is stale)")
        r = ssh(f"systemctl restart {SERVICE} && sleep 4 && systemctl is-active {SERVICE}")
        say(" ok " if "active" in r.stdout else "fail", r.stdout.strip() or "no reply")
    else:
        print("\n6. restart")
        say("skip", "not needed" if not needs_restart else "--no-restart given")

    print("\n7. is it actually up?")
    import json
    import urllib.request
    for path in ("/", "/api/stats"):
        try:
            req = urllib.request.Request(SITE + path,
                                         headers={"User-Agent": "SparrowMap/deploy"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                say(" ok " if resp.status == 200 else "fail",
                    f"{path} -> {resp.status}")
                if path == "/api/stats":
                    s = json.loads(resp.read())
                    say("info", f"{s.get('nodes_online')}/{s.get('nodes_active')} "
                                f"cameras online, {s.get('sightings_24h')} sightings/24h")
        except Exception as exc:
            say("fail", f"{path} -> {exc}")

    print()
    print("  ✅ deployed" if ok else "  ⛔ DEPLOY FINISHED WITH FAILURES - check above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
