"""Keep a permanent record of everyone who attacks this box.

    python3 tools/attacker_log.py            # harvest since last run
    python3 tools/attacker_log.py --report   # what has been hitting us

WHY A FILE AND NOT JUST fail2ban

fail2ban forgets. A ban expires after an hour and the evidence goes with it, and
journald rotates, so "who has been attacking us" was a question this box could
not answer about last week. His ask was explicit: SAVE THE IPs.

This keeps them: first seen, last seen, how many attempts, which usernames were
tried, and whether fail2ban stopped them. Append-only in SQLite so a rotated
journal cannot erase history, and so the record survives a reboot, a restart,
and a ban expiry.

⚠️ DELIBERATELY NO EXTERNAL LOOKUPS. No geo-IP, no ASN, no abuse database.
Every one of those is an outbound request that tells a third party what this
machine is watching, and not doing that to people is the entire point of the
project. The raw addresses are here; enrich them somewhere else if you need to.

🚨 THESE ARE ATTACKERS, NOT USERS. Nothing here is a visitor to the map. The
map's visitors arrive through Cloudflare and are never logged by IP anywhere in
this project - see privacy.audit_ip. This file is failed SSH authentication and
firewall-blocked probes, nothing else, and it must never become a place where
"who looked at the map" could be answered.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "attackers.db"


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS attackers(
        ip TEXT PRIMARY KEY, first_seen REAL, last_seen REAL,
        attempts INTEGER DEFAULT 0, service TEXT, users TEXT,
        banned INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_last ON attackers(last_seen)")
    return c


# rhost= is the PAM form, "from <ip>" the sshd form. Both appear.
IP_RE = re.compile(r"(?:from|rhost=)\s*((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{6,})")
USER_RE = re.compile(r"(?:invalid user|user)\s+([A-Za-z0-9._-]+)")
HIT = ("failed password", "invalid user", "authentication failure",
       "not allowed because", "maximum authentication attempts")


def harvest(since: str = "-30 min") -> int:
    c = db()
    out = subprocess.run(
        ["journalctl", "-u", "ssh", "-u", "sshd", "--since", since,
         "--no-pager", "-o", "cat"],
        capture_output=True, text=True, timeout=300).stdout
    now, seen = time.time(), 0
    for line in out.splitlines():
        low = line.lower()
        if not any(h in low for h in HIT):
            continue
        m = IP_RE.search(line)
        if not m:
            continue
        ip = m.group(1)
        um = USER_RE.search(line)
        user = um.group(1) if um else ""
        row = c.execute("SELECT attempts, users FROM attackers WHERE ip=?",
                        (ip,)).fetchone()
        if row:
            users = set(filter(None, (row[1] or "").split(",")))
            if user:
                users.add(user)
            # Capped: one scanner can try thousands of names, and the useful
            # fact is WHICH names get tried, not the exhaustive list.
            c.execute("UPDATE attackers SET last_seen=?, attempts=attempts+1,"
                      " users=? WHERE ip=?",
                      (now, ",".join(sorted(users)[:25]), ip))
        else:
            c.execute("INSERT INTO attackers(ip,first_seen,last_seen,attempts,"
                      "service,users) VALUES(?,?,?,1,?,?)",
                      (ip, now, now, "ssh", user))
        seen += 1

    # Record who fail2ban is currently holding, so the file shows who got
    # stopped rather than only who tried.
    try:
        st = subprocess.run(["fail2ban-client", "status", "sshd"],
                            capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Banned IP list:\s*(.*)", st)
        if m:
            for ip in m.group(1).split():
                c.execute("UPDATE attackers SET banned=1 WHERE ip=?", (ip,))
    except Exception:
        pass          # fail2ban being absent must never lose the harvest

    c.commit()
    c.close()
    return seen


def report() -> None:
    c = db()
    tot, att = c.execute(
        "SELECT COUNT(*), COALESCE(SUM(attempts),0) FROM attackers").fetchone()
    ban = c.execute("SELECT COUNT(*) FROM attackers WHERE banned=1").fetchone()[0]
    print(f"  {tot} distinct attacking addresses, {att} attempts recorded, "
          f"{ban} currently banned")
    n24 = c.execute("SELECT COUNT(*) FROM attackers WHERE last_seen>?",
                    (time.time() - 86400,)).fetchone()[0]
    print(f"  {n24} active in the last 24 h")
    print("\n  worst offenders:")
    for ip, n, users, b in c.execute(
            "SELECT ip,attempts,users,banned FROM attackers "
            "ORDER BY attempts DESC LIMIT 12"):
        print(f"    {ip:<42} {n:>6} tries {'BANNED' if b else '      '} "
              f"{(users or '')[:40]}")
    names: dict = {}
    for (u,) in c.execute("SELECT users FROM attackers"):
        for x in filter(None, (u or "").split(",")):
            names[x] = names.get(x, 0) + 1
    print("\n  usernames being guessed:")
    for k, v in sorted(names.items(), key=lambda kv: -kv[1])[:12]:
        print(f"    {k:<26} tried by {v} addresses")
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--since", default="-30 min")
    a = ap.parse_args()
    if a.report:
        report()
    else:
        print(f"  recorded {harvest(a.since)} attack line(s)")
