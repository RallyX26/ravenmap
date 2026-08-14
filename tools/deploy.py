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
import time
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


# 🚨 ALWAYS NAME THE ENCODING WHEN CAPTURING OUTPUT ON WINDOWS.
# text=True decodes with the ANSI codepage (cp1252 here), so one emoji in a
# child process's output raises UnicodeDecodeError inside subprocess's reader
# thread and .stdout comes back as None - not an error you can see, just a
# value that is suddenly nothing. check_running_code.py prints a warning sign,
# and that alone crashed this tool.
#
# Same bug family as the health watch at midnight, arriving from the other
# direction: that one could not ENCODE an emoji to a redirected log, this one
# could not DECODE one from a pipe.
def _run(cmd: list, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True,
                          encoding="utf-8", errors="replace")


def git(*args: str, cwd: Path = ROOT) -> str:
    return _run(["git", *args], cwd=cwd).stdout.strip()


def ssh(cmd: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-i", KEY, "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=accept-new", BOX, cmd],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)



# What runs HERE, and how to start it again. The box is not the only place that
# can drift: the local hub, the detector and camctl all import modules at
# startup, so editing core.py leaves three processes running yesterday's rules
# while the repo says otherwise.
LOCAL = {
    # `port` is what makes a restart VERIFIABLE rather than merely issued: the
    # hub can leave a process behind and still not be serving, which is exactly
    # how a deploy came to report success over a dead :8150.
    "hub.py":      {"match": "*hub.py*", "args": "hub.py", "camera": False,
                    "port": 8150},
    "run_live.py": {"match": r"*detect\run_live.py*", "args": None, "camera": True},
    "camctl.py":   {"match": r"*camctl\camctl.py*", "args": r"camctl\camctl.py",
                    "camera": True},
}


def _ps(cmd: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, encoding="utf-8",
                          errors="replace").stdout.strip()


def _came_up(name: str, spec: dict, wait_s: int = 12) -> bool:
    """Did the thing we just started actually start?

    Two questions, because they fail separately. A process can exist and still
    be unable to serve - the hub binds a port, and the most likely reason a
    restarted hub dies is that the old one had not released :8150 yet, which
    leaves a process that exits a second later into a minimised window nobody
    reads. So for anything with a port, the port is the answer that counts.
    """
    port = spec.get("port")
    deadline = time.time() + wait_s
    seen_proc = False
    while time.time() < deadline:
        got = _ps(f"(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -like '{spec['match']}' -and "
                  f"$_.CommandLine -notlike '*Get-CimInstance*' }} | "
                  f"Measure-Object).Count")
        if (got or "0").strip() not in ("", "0"):
            seen_proc = True
            if not port:
                return True
            listening = _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                            f"-ErrorAction SilentlyContinue | Measure-Object).Count")
            if (listening or "0").strip() not in ("", "0"):
                return True
        time.sleep(1.5)
    if seen_proc and port:
        print(f"         (a {name} process exists but nothing is listening "
              f"on {port} - it is probably exiting on a bind error)")
    return False


def sync_local(a) -> None:
    """Restart anything here that is running code older than the repo.

    🚨 "IT ALL SHOULD MATCH REPO" INCLUDES THIS MACHINE. Deploying to the box
    and leaving the local hub on last night's core.py is the same drift in a
    different place, and harder to notice because nothing serves the public
    from here.

    ⚠️ THE CAMERA STACK IS OPT-IN. run_live and camctl own the USB capture
    graph, and restarting them can wedge the C920 into needing a physical
    replug. Doing that automatically, from a deploy, while nobody is standing
    at the camera, would turn a routine push into a dead camera. So they are
    REPORTED by default and restarted only with --restart-camera - not skipped
    quietly, which is the failure this rule exists to prevent.
    """
    r = _run([sys.executable, str(ROOT / "tools" / "check_running_code.py")])
    stale = [ln.split()[0] for ln in (r.stdout or "").splitlines()
             if "STALE" in ln]
    if not stale:
        say(" ok ", "everything here already runs the current code")
        return
    for name in stale:
        spec = LOCAL.get(name)
        if not spec:
            say("info", f"{name} is stale (not managed here - restart it yourself)")
            continue
        if spec["camera"] and not a.restart_camera:
            say("warn", f"{name} is STALE - owns the camera, so it needs "
                        f"--restart-camera (do it when you can reach the camera)")
            continue
        args = spec["args"]
        if args is None:      # the detector carries its node token in argv
            cmd = _ps(f"(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                      f"Where-Object {{ $_.CommandLine -like '{spec['match']}' -and "
                      f"$_.CommandLine -notlike '*Get-CimInstance*' }} | "
                      f"Select-Object -First 1).CommandLine")
            if not cmd:
                say("info", f"{name} not running")
                continue
            args = cmd.split(".exe", 1)[1].strip()
        # STOP FIRST. The launcher refuses a second detector for the same node,
        # so start-then-stop leaves the OLD one running and looks like success.
        _ps(f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '{spec['match']}' -and "
            f"$_.CommandLine -notlike '*Get-CimInstance*' }} | "
            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}")
        _ps("Start-Sleep -Seconds 3; Start-Process -FilePath "
            f"'{sys.executable}' -ArgumentList '{args}' "
            f"-WorkingDirectory '{ROOT}' -WindowStyle Minimized")
        # 🚨 ISSUING THE COMMAND IS NOT THE SAME AS THE THING RUNNING, AND THIS
        # LINE USED TO CLAIM OTHERWISE.
        #
        # It printed "restarted hub.py" unconditionally, straight after
        # Start-Process. Observed live: the deploy reported "✅ restarted
        # hub.py" and the local hub was NOT RUNNING afterwards - the old
        # process had been force-stopped and nothing replaced it, so :8150 was
        # simply dead and the deploy said it had succeeded. A stop that works
        # and a start that does not is strictly worse than doing nothing, and
        # reporting it as success is how it stays unnoticed.
        #
        # Step 7 already refuses to believe the BOX is up without asking it.
        # This machine gets the same treatment: wait for the process, and for
        # anything that listens, wait for the port to answer.
        if not _came_up(name, spec):
            say("fail", f"{name} was stopped and did NOT come back - "
                        f"start it yourself and check the window for the error")
        else:
            say(" ok ", f"restarted {name} (verified running)")

    check = _run([sys.executable, str(ROOT / "tools" / "check_running_code.py")])
    if "up to date" in (check.stdout or ""):
        say(" ok ", "verified: everything here matches the source")
    else:
        left = [ln.split()[0] for ln in (check.stdout or "").splitlines()
                if "STALE" in ln]
        say("info", "still stale: " + ", ".join(left))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--restart-camera", action="store_true",
                    help="also restart run_live/camctl here (they own the "
                         "camera; do it when you can reach it)")
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
        r = _run([sys.executable, str(ROOT / "tools" / "preflight.py")])
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

    print("\n8. this machine")
    sync_local(a)

    print()
    print("  ✅ deployed" if ok else "  ⛔ DEPLOY FINISHED WITH FAILURES - check above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
