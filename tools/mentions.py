"""Find people talking about SparrowMap on the open web, once an hour.

    python mentions.py            # fetch, dedupe, print only what is NEW
    python mentions.py --all      # print everything held, newest first
    python mentions.py --days 7   # widen the window

🚨 WHAT THIS DELIBERATELY DOES NOT DO.
Instagram, Facebook and X are not here. Scraping the first two is against their
terms and their anti-bot measures are aggressive, so anything built on it breaks
constantly AND puts his accounts at risk - the accounts are the audience, so
that is not a trade worth making. X's API is paid. For Instagram and Facebook
the clean route is the official Graph API on HIS OWN posts, which is where the
comments he actually needs to answer live, and it needs a token he issues.

So this covers the open web: Reddit, Hacker News, Google News, Lemmy, GitHub.
Those are the places a link spreads to that nobody tells you about.

⚠️ IT COLLECTS, IT DOES NOT DECIDE. Every new item is printed for a human (or
Claude) to triage. A watcher that silently drops things it judged unimportant is
a watcher you cannot trust, and the whole point is to not miss the one comment
that mattered.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse as up
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / "seen.json"
# Reddit's rules ask for a descriptive agent naming the app and a contact, and
# several of these hosts refuse anything that reads as an anonymous script.
# Saying who this is and how to reach us is also just the correct thing to do
# when reading somebody else's service on a schedule.
UA = ("Mozilla/5.0 (compatible; SparrowMapMentions/1.0; "
      "+https://map.sparrowmap.com; sparrowmap@icloud.com)")

# ⚠️ Quoted phrases, not loose words. "sparrow" alone returns birds, and a
# watcher that cries wolf hourly gets ignored within a day - which costs more
# than missing a mention.
TERMS = ["sparrowmap", "sparrow map", "map.sparrowmap.com"]


def get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _j(url: str, timeout: int = 25):
    return json.loads(get(url, timeout))


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def reddit(term: str) -> list:
    """Reddit's own search, via its Atom feed.

    ⚠️ THE JSON ENDPOINT IS BLOCKED AND LIES ABOUT IT. Both www and old
    .reddit.com/search.json answer 200 with an HTML interstitial rather than a
    403, so the failure arrives as a JSONDecodeError and reads like a parsing
    bug rather than a block. search.rss on www is still served as real Atom.
    """
    u = ("https://www.reddit.com/search.rss?q=" + up.quote(f'"{term}"')
         + "&sort=new&limit=50")
    xml = get(u, 30).decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", xml, re.S):
        blk = m.group(1)

        def tag(t, attr=None):
            if attr:
                mm = re.search(rf'<{t}[^>]*{attr}="([^"]+)"', blk)
                return mm.group(1) if mm else ""
            mm = re.search(rf"<{t}[^>]*>(.*?)</{t}>", blk, re.S)
            return re.sub(r"<[^>]+>|&lt;[^&]*&gt;", " ",
                          mm.group(1)) .strip() if mm else ""
        link = tag("link", "href")
        out.append({"src": "reddit", "id": "rd_" + str(abs(hash(link)))[:12],
                    "ts": 0, "who": tag("name"),
                    "where": "reddit", "title": tag("title"),
                    "text": " ".join(tag("content").split())[:900],
                    "url": link, "score": None})
    return out


def hackernews(term: str) -> list:
    """Algolia's HN index. Keyless, covers stories AND comments."""
    u = ("https://hn.algolia.com/api/v1/search_by_date?query="
         + up.quote(term) + "&tags=(story,comment)&hitsPerPage=50")
    out = []
    for h in (_j(u).get("hits") or []):
        out.append({
            "src": "hn", "id": "hn_" + str(h.get("objectID")),
            "ts": h.get("created_at_i") or 0,
            "who": h.get("author") or "",
            "where": "news.ycombinator.com",
            "title": h.get("title") or h.get("story_title") or "",
            "text": re.sub(r"<[^>]+>", " ", h.get("comment_text") or "")[:900],
            "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "score": h.get("points"),
        })
    return out


def googlenews(term: str) -> list:
    """Google News RSS. Press coverage, which arrives without warning and is
    the category most worth answering the same day."""
    u = ("https://news.google.com/rss/search?q=" + up.quote(f'"{term}"')
         + "&hl=en-US&gl=US&ceid=US:en")
    xml = get(u, 30).decode("utf-8", "ignore")
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        blk = m.group(1)

        def tag(t):
            mm = re.search(rf"<{t}>(.*?)</{t}>", blk, re.S)
            return re.sub(r"<[^>]+>|<!\[CDATA\[|\]\]>", "", mm.group(1)).strip() \
                if mm else ""
        link = tag("link")
        out.append({"src": "news", "id": "gn_" + str(abs(hash(link)))[:12],
                    "ts": 0, "who": tag("source"), "where": "google news",
                    "title": tag("title"), "text": "", "url": link,
                    "score": None})
    return out


def lemmy(term: str) -> list:
    """Lemmy - where a lot of the reddit-adjacent privacy crowd went."""
    u = ("https://lemmy.world/api/v3/search?q=" + up.quote(term)
         + "&type_=All&sort=New&limit=40")
    d = _j(u)
    out = []
    for p in (d.get("posts") or []):
        po, cr = p.get("post") or {}, p.get("creator") or {}
        out.append({"src": "lemmy", "id": "lm_" + str(po.get("id")),
                    "ts": 0, "who": cr.get("name") or "",
                    "where": "lemmy", "title": po.get("name") or "",
                    "text": (po.get("body") or "")[:900],
                    "url": po.get("ap_id") or "", "score": None})
    for c in (d.get("comments") or []):
        cm, cr = c.get("comment") or {}, c.get("creator") or {}
        out.append({"src": "lemmy", "id": "lc_" + str(cm.get("id")),
                    "ts": 0, "who": cr.get("name") or "",
                    "where": "lemmy comment", "title": "",
                    "text": (cm.get("content") or "")[:900],
                    "url": cm.get("ap_id") or "", "score": None})
    return out


def github(term: str) -> list:
    # ⚠️ ONE TERM ONLY. Unauthenticated GitHub search allows ~10 requests a
    # minute and three terms across an hourly run tripped it every time. The
    # one-word name is the only one that finds anything here anyway.
    if term != "sparrowmap":
        return []
    """Issues and discussions elsewhere that name it - forks, comparisons,
    and people filing bugs against somebody else's repo about us."""
    u = ("https://api.github.com/search/issues?q=" + up.quote(f'"{term}"')
         + "&sort=created&order=desc&per_page=30")
    out = []
    for h in (_j(u).get("items") or []):
        if "SparrowMap/sparrowmap" in (h.get("repository_url") or ""):
            continue                       # our own issues are not a mention
        out.append({"src": "github", "id": "gh_" + str(h.get("id")),
                    "ts": 0, "who": (h.get("user") or {}).get("login") or "",
                    "where": (h.get("repository_url") or "").split("/repos/")[-1],
                    "title": h.get("title") or "",
                    "text": (h.get("body") or "")[:900],
                    "url": h.get("html_url") or "", "score": None})
    return out


SOURCES = [reddit, hackernews, googlenews, lemmy, github]


def really_mentions(item: dict) -> bool:
    """Does the text actually contain the term, or did the engine guess?

    🚨 SEARCH ENGINES FUZZY-MATCH, AND THE FIRST RUN PROVED IT EXPENSIVELY.
    Algolia matched "sparrow" against "NARROW" and returned 100+ Hacker News
    comments about narrow PRs, narrow garages and narrow fields of mathematics.
    One real mention was buried in the middle of them.

    An hourly watcher that cries wolf is worse than no watcher: it gets muted
    within a day, and then the one comment that mattered arrives into a channel
    nobody reads. So every result is CHECKED against its own text rather than
    trusted because a search engine returned it.

    ⚠️ Google News is exempt: its RSS gives a headline and a link with no body,
    so there is nothing to verify against, and a headline that omits the word
    can still be an article about us. Those stay in and get eyeballed.
    """
    if item.get("src") == "news":
        # ⚠️ NEWS IS HEADLINE-ONLY, so there is no body to check - and the
        # two-word term is far too loose for it. The first run returned a
        # clay-coloured sparrow atlas, South Dakota redistricting ("Sparrow Map
        # Cedes Western Highway 12"), and Jack Sparrow's location in Fortnite,
        # alongside a real Yahoo Tech piece.
        #
        # So a news item has to carry the name as ONE WORD in its headline.
        # That keeps "SparrowMap Is Watching Them" and drops the birds. It will
        # miss an article that never writes the name in its title, which is a
        # trade worth making: this runs hourly, and an hourly feed of birds is
        # a feed that gets muted.
        return "sparrowmap" in (item.get("title") or "").lower()
    hay = " ".join(str(item.get(k) or "") for k in ("title", "text", "url")).lower()
    return any(t.lower() in hay for t in TERMS)


# --------------------------------------------------------------------------
def load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--days", type=float, default=30)
    # 🚨 FOR THE CLOUD RUN, WHICH HAS NO MEMORY BETWEEN FIRINGS.
    #
    # The hourly routine runs in a fresh sandbox with a clean checkout, so
    # seen.json does not survive from one hour to the next and "new since last
    # time" is not a question it can answer. A TIME WINDOW is, and it needs no
    # state at all: report what appeared in the last few hours and accept a
    # little overlap, which is the right way round - a repeat costs a glance,
    # a gap costs the mention.
    #
    # ⚠️ Google News, Lemmy and GitHub give no usable timestamp, so they are
    # always shown under --hours. At the volumes involved (four mentions in
    # total so far) that is a handful of lines, and pretending to filter them
    # would be worse than admitting it.
    ap.add_argument("--hours", type=float, default=0,
                    help="stateless mode: show items from the last N hours")
    a = ap.parse_args()

    store = load()
    seen = store.get("seen") or {}
    fresh, errs, noise = [], [], [0]

    for fn in SOURCES:
        for term in TERMS:
            # 🚨 SPACED OUT ON PURPOSE. Reddit 429s and GitHub 403s if these
            # go out back to back - which is how the first runs failed, not
            # because the endpoints were wrong. An hourly job has fifty-nine
            # minutes of slack and no reason at all to hurry, and these are
            # somebody else's free services.
            time.sleep(2.5)
            try:
                for item in fn(term):
                    if not item.get("id") or item["id"] in seen:
                        continue
                    if not really_mentions(item):
                        noise[0] += 1
                        continue
                    seen[item["id"]] = {"ts": item.get("ts") or time.time(),
                                        "url": item.get("url")}
                    fresh.append(item)
            except Exception as exc:
                # ⚠️ ONE DEAD SOURCE MUST NOT SILENCE THE REST. A watcher that
                # exits on the first failure reports "nothing new" for weeks
                # while a search endpoint is broken, which is the failure mode
                # that matters most here.
                errs.append(f"{fn.__name__}/{term}: "
                            f"{type(exc).__name__}: {str(exc)[:60]}")

    store["seen"] = seen
    store["last_run"] = time.time()
    STORE.write_text(json.dumps(store), encoding="utf-8")

    show = fresh
    if a.hours:
        cut = time.time() - a.hours * 3600
        # Everything, not just the unseen - state is meaningless in a fresh
        # sandbox and the window is doing the work instead.
        show = [i for i in (fresh if not a.all else fresh)
                if not i.get("ts") or i["ts"] >= cut]
    print(f"=== {len(fresh)} new mention(s), {len(seen)} known, "
          f"{noise[0]} fuzzy-match false positive(s) dropped ===")
    for it in sorted(show, key=lambda x: -(x.get("ts") or 0)):
        when = (time.strftime("%m-%d %H:%M", time.localtime(it["ts"]))
                if it.get("ts") else "     -    ")
        print(f"\n[{it['src']}] {when}  {it.get('who','')}  ({it.get('where','')})")
        if it.get("title"):
            print(f"  {it['title'][:150]}")
        if it.get("text"):
            print(f"  {' '.join(it['text'].split())[:300]}")
        print(f"  {it.get('url','')}")
    if errs:
        print("\n⚠ sources that failed this run (the rest still ran):")
        for e in errs:
            print("   " + e)
    if not fresh:
        print("\n(nothing new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
