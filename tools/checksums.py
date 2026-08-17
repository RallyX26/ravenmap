"""Compute SHA-256 for every published release asset, and write the page.

🚨 WHY THIS EXISTS. Asked on Hacker News: "Are the .exe to install a always on
computer as part of a open source repo I can verify with a hash? Otherwise I am
not going to trust it." The repo is public and the build is reproducible from
it, but no checksum was ever published - so a downloader had nothing to check a
file AGAINST, and the honest answer was that the gap was real.

⚠️ THE HASH IS PUBLISHED ON A DIFFERENT HOST FROM THE BINARY, ON PURPOSE. The
installers live on GitHub releases; these hashes are served from
sparrowmap.com. Publishing both in the same place means one compromise covers
both, and the check stops being worth doing.

Future releases get a .sha256 generated in the build itself
(.github/workflows/build-node-installer.yml) - that is the durable half. This
tool covers what is already published and regenerates the page.

    python tools/checksums.py            # fetch, hash, write public/checksums.html
    python tools/checksums.py --check     # verify the page still matches reality
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "public" / "checksums.html"
API = "https://api.github.com/repos/SparrowMap/sparrowmap/releases"
UA = {"User-Agent": "SparrowMap-checksums/1.0"}


def _get(url: str) -> bytes:
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=300).read()


def assets() -> list[dict]:
    rels = json.loads(_get(API))
    out = []
    for r in rels:
        for a in r.get("assets") or []:
            if a["name"].endswith(".sha256"):
                continue          # the checksum file is not itself an asset
            out.append({"tag": r["tag_name"], "name": a["name"],
                        "size": a["size"], "url": a["browser_download_url"],
                        "published": r.get("published_at", "")})
    return out


def hashed(a: dict) -> dict:
    # Streamed rather than read whole: the business installer is ~76 MB and
    # there is no reason to hold it in memory to hash it.
    h = hashlib.sha256()
    with urllib.request.urlopen(
            urllib.request.Request(a["url"], headers=UA), timeout=600) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return {**a, "sha256": h.hexdigest()}


PAGE = """<!doctype html><meta charset="utf-8">
<title>SparrowMap - checksums</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/style.css?v=105">
<style>
  body{{padding:0}} .wrap{{max-width:820px;margin:0 auto;padding:18px}}
  code{{font:12px var(--mono);word-break:break-all}}
  table{{width:100%;border-collapse:collapse;margin:14px 0}}
  td,th{{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);
        vertical-align:top;font-size:13px}}
  th{{color:var(--dim);font-weight:600}}
  .n{{color:var(--dim2)}}
</style>
<div class="wrap">
<h1>Checksums</h1>
<p>SHA-256 for every published installer. Checked {when}.</p>
<p class="n">These are served from <b>sparrowmap.com</b> while the installers
are hosted on <b>GitHub</b>. That is deliberate: two different places to
compromise rather than one.</p>
<table>
<tr><th>File</th><th>SHA-256</th></tr>
{rows}
</table>
<h3>Verify a download</h3>
<p>Windows PowerShell:</p>
<p><code>Get-FileHash -Algorithm SHA256 .\\FILENAME.exe</code></p>
<p>Linux or macOS:</p>
<p><code>sha256sum FILENAME.exe</code></p>
<p class="n">If the hash does not match what is on this page, do not run the
file, and please <a href="/bugs">tell us</a>.</p>
<p class="n">You do not need any binary to take part: the browser camera needs
nothing installed, and the IP-camera relay is a single Python file that fetches
its own model and verifies it by SHA-256 on every run.</p>
<p><a href="/">&larr; back to the map</a></p>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify the page matches the live assets; exit 1 if not")
    a = ap.parse_args()

    got = [hashed(x) for x in assets()]
    if not got:
        print("no release assets found")
        return 1
    for g in got:
        print(f"{g['sha256']}  {g['name']}  ({g['size'] / 1e6:.1f} MB, {g['tag']})")

    if a.check:
        if not OUT.exists():
            print(f"\n{OUT.name} does not exist")
            return 1
        page = OUT.read_text(encoding="utf-8")
        bad = [g for g in got if g["sha256"] not in page]
        if bad:
            print(f"\n🚨 {len(bad)} asset(s) NOT on the page - it is STALE:")
            for g in bad:
                print(f"   {g['name']} {g['sha256']}")
            return 1
        print(f"\n{OUT.name} matches all {len(got)} asset(s)")
        return 0

    rows = "\n".join(
        f"<tr><td><b>{html.escape(g['name'])}</b><br>"
        f"<span class='n'>{g['tag']} &middot; {g['size'] / 1e6:.1f} MB</span></td>"
        f"<td><code>{g['sha256']}</code></td></tr>" for g in got)
    OUT.write_text(
        PAGE.format(when=time.strftime("%Y-%m-%d"), rows=rows), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
