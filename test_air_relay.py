"""Server-side contract for the tag-routed air relay (air_relay.py).

Proves the relay never decrypts, routes by opaque tag, reads non-destructively
by cursor, de-duplicates, bounds a tag and the whole air, validates tags, and
enforces the same hashcash the mailbox path uses. Pure module test, no HTTP.

Run: python test_air_relay.py
"""
import base64
import hashlib
import time

import air_relay
import send_relay

P = F = 0


def ok(name, cond):
    global P, F
    if cond:
        P += 1
        print("  ok  " + name)
    else:
        F += 1
        print("FAIL  " + name)


TAG = "a" * 32
TAG2 = "b" * 32


def frame(text):
    return base64.b64encode(text.encode()).decode()


def solve_pow(tag, env, bits):
    """Find a stamp the relay's pow_ok will accept (tests use low bits)."""
    ts = int(time.time())
    ch = hashlib.sha256(env.encode()).hexdigest()
    n = 0
    while True:
        pre = "%s|%s|%d|%s|%s" % (send_relay.POW_VERSION, tag, ts, ch, n)
        if send_relay._leading_zero_bits(hashlib.sha256(pre.encode()).digest()) >= bits:
            return ts, n
        n += 1


# --- tag validation ---------------------------------------------------------
ok("valid 32-hex tag accepted", air_relay.valid_tag(TAG))
ok("uppercase hex rejected", not air_relay.valid_tag("A" * 32))
ok("short tag rejected", not air_relay.valid_tag("a" * 31))
ok("trailing newline rejected", not air_relay.valid_tag("a" * 32 + "\n"))
ok("non-hex rejected", not air_relay.valid_tag("g" * 32))

# --- put / get round trip ---------------------------------------------------
s0 = air_relay.put(TAG, frame("first"))
s1 = air_relay.put(TAG, frame("second"))
ok("put returns monotonic seq 0,1", s0 == 0 and s1 == 1)

head, msgs = air_relay.get(TAG, 0)
ok("get from 0 returns both, in order", [m["b"] for m in msgs] == [frame("first"), frame("second")])
ok("head advances to 2", head == 2)
ok("stored frame is byte-identical (never transformed)", msgs[0]["b"] == frame("first"))

# cursor read is non-destructive and incremental
head2, msgs2 = air_relay.get(TAG, 1)
ok("cursor read returns only seq>=1", [m["s"] for m in msgs2] == [1])
head3, msgs3 = air_relay.get(TAG, 0)
ok("read did not delete (second full read still returns both)", len(msgs3) == 2)

# tags are isolated
h_b, msgs_b = air_relay.get(TAG2, 0)
ok("a different tag is empty (isolation)", msgs_b == [] and h_b == 0)

# --- dedup ------------------------------------------------------------------
dup = air_relay.put(TAG, frame("first"))
ok("identical frame de-duped to existing seq", dup == 0)
_, msgs_after = air_relay.get(TAG, 0)
ok("dedup did not grow the queue", len(msgs_after) == 2)

# --- malformed input --------------------------------------------------------
ok("bad tag put rejected", air_relay.put("zzz", frame("x")) is None)
ok("non-base64 frame rejected", air_relay.put(TAG2, "!!!not base64!!!") is None)
ok("empty frame rejected", air_relay.put(TAG2, "") is None)
oversize = base64.b64encode(b"x" * (air_relay.AIR_MSG_MAX + 1)).decode()
ok("oversize frame rejected", air_relay.put(TAG2, oversize) is None)

# --- per-tag cap ------------------------------------------------------------
CAPTAG = "c" * 32
for i in range(air_relay.TAG_MAX + 10):
    air_relay.put(CAPTAG, frame("m%d" % i))
head_c, msgs_c = air_relay.get(CAPTAG, 0)
ok("per-tag cap holds only TAG_MAX", len(msgs_c) == air_relay.TAG_MAX)
ok("oldest dropped, newest kept", msgs_c[-1]["b"] == frame("m%d" % (air_relay.TAG_MAX + 9)))
ok("head keeps counting past evictions", head_c == air_relay.TAG_MAX + 10)

# --- progressive PoW difficulty ---------------------------------------------
ok("empty tag needs base bits", air_relay.required_bits("d" * 32) == send_relay.POW_BITS)
ok("full tag needs more bits", air_relay.required_bits(CAPTAG) > send_relay.POW_BITS)

# --- PoW gate (the check hub.py performs before put) ------------------------
POWTAG = "e" * 32
fr = frame("stamped hello")
bits = 12   # low, just to exercise accept/reject quickly
ts, nonce = solve_pow(POWTAG, fr, bits)
ok("a valid stamp verifies", send_relay.pow_ok(POWTAG, fr, ts, nonce, bits=bits))
ok("a wrong nonce is refused", not send_relay.pow_ok(POWTAG, fr, ts, nonce + 1, bits=bits))
ok("a stamp for another frame is refused",
   not send_relay.pow_ok(POWTAG, frame("different"), ts, nonce, bits=bits))

# --- TTL prune (fast-forward by rewriting timestamps) -----------------------
TTLTAG = "f" * 32
air_relay.put(TTLTAG, frame("old"))
with air_relay._lock:
    for it in air_relay._tags[TTLTAG]:
        it["t"] -= air_relay.RETAIN_S + 1
_, msgs_ttl = air_relay.get(TTLTAG, 0)   # get() prunes on read
ok("a frame past RETAIN is dropped", msgs_ttl == [])

print("\n%d passed, %d failed" % (P, F))
raise SystemExit(1 if F else 0)
