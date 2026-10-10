"""Prune Gateway Store: StoreClient.prune() and app/backfill/prune.py against a fake gateway.

The fake applies the firmware's rule (creek_store.h prune_stream_, store_core.h
block_prunable): oldest block first, stop at the first block that fails, never the newest,
never a block with a seq above the cursor or a last record at/after the cutoff, a budget of
blocks per request.

Run: python rate_of_rise/tests/test_backfill_prune.py
"""
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backfill.client import (PruneUnsupported, StoreChanged,  # noqa: E402
                                 StoreClient)
from app.backfill.prune import prune_store  # noqa: E402
from app.config import _prune_days  # noqa: E402

TOKEN = "t" * 32
STORE_ID = "3f9a0c1e7b2d4a65"
DAY = 86400.0
NOW = 1_800_000_000.0
BLOCK = 10000


def blocks(n, newest_age_days=0.0, step_days=7.0):
    """n blocks of one stream, oldest first: (block, last_seq, last_ts). Block i's last record
    is (n-1-i)*step_days + newest_age_days old."""
    return [(i, (i + 1) * BLOCK - 1, NOW - ((n - 1 - i) * step_days + newest_age_days) * DAY)
            for i in range(n)]


class Gateway(BaseHTTPRequestHandler):
    mode = "v2"          # v2 | v1 | old_fw | conflict | busy
    sd_ok = True
    store_id = STORE_ID
    budget = 2
    streams: dict = {}
    posts: list = []

    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _first(self, s):
        b = type(self).streams[s]
        if not b:
            return 0
        return 1 if b[0][0] == 0 else b[0][0] * BLOCK

    def do_GET(self):
        cls = type(self)
        if cls.mode == "v1":
            return self._send(404, "not found", "text/plain")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, "unauthorized", "text/plain")
        if self.path.startswith("/store/status"):
            return self._send(200, json.dumps({
                "store_schema": 1, "device": "creek-gateway-v2", "now": NOW, "ts_src": "ntp",
                "sd_ok": cls.sd_ok, "store_id": cls.store_id if cls.sd_ok else "",
                "sd_free_mb": 1,
                "streams": {s: {"first": self._first(s), "last": b[-1][1] if b else 0}
                            for s, b in cls.streams.items()}}))
        if self.path.startswith("/store/prune"):
            return self._send(405, "POST only", "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        cls = type(self)
        length = self.headers.get("Content-Length")
        body = self.rfile.read(int(length)).decode() if length else ""
        cls.posts.append({"path": self.path, "length": length, "body": body,
                          "auth": self.headers.get("Authorization")})
        if cls.mode in ("v1", "old_fw"):
            return self._send(404, "not found", "text/plain")
        if length is None:   # the ESP-IDF handler answers 411 without one
            return self._send(411, "length required", "text/plain")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, "unauthorized", "text/plain")
        if self.path != "/store/prune":
            return self._send(404, "not found", "text/plain")
        if cls.mode == "busy":
            return self._send(503, "bus busy", "text/plain")
        q = {k: v[0] for k, v in parse_qs(body).items()}
        if cls.mode == "conflict" or q.get("store_id") != cls.store_id:
            return self._send(409, "store id mismatch", "text/plain")
        before = float(q["before"])
        deleted, more, budget = {}, False, cls.budget
        for s, b in cls.streams.items():
            through = int(q.get(f"{s}_through", 0))
            n = 0
            while len(b) > 1:   # never the newest
                _, last_seq, last_ts = b[0]
                if last_seq > through or last_ts >= before:
                    break
                if n >= budget:
                    more = True
                    break
                b.pop(0)
                n += 1
            deleted[s] = n
            budget -= n
        return self._send(200, json.dumps({
            "deleted": deleted, "first": {s: self._first(s) for s in cls.streams},
            "more": more}))


def serve(mode="v2", streams=None, sd_ok=True, store_id=STORE_ID, budget=2):
    Gateway.mode, Gateway.sd_ok, Gateway.store_id, Gateway.budget = mode, sd_ok, store_id, budget
    Gateway.streams = streams if streams is not None else {"node": blocks(5), "ecowitt": blocks(5)}
    Gateway.posts = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, StoreClient(f"http://127.0.0.1:{srv.server_address[1]}", TOKEN)


class FakeReconciler:
    def __init__(self, cursor=None, store_id=STORE_ID):
        self.cursor = cursor if cursor is not None else {"node": 10**9, "ecowitt": 10**9}
        self.store_id = store_id


def run(client, rec=None, days=8):
    return prune_store(client, rec or FakeReconciler(), days, now_fn=lambda: NOW)


class Loud(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def no_warnings(fn):
    h = Loud()
    lg = logging.getLogger("app")
    lg.addHandler(h)
    try:
        return fn()
    finally:
        lg.removeHandler(h)
        assert not h.records, [r.getMessage() for r in h.records]


# --- StoreClient.prune -------------------------------------------------------------------

def test_client_prune_posts_a_form_with_the_bearer_token():
    srv, client = serve()
    try:
        reply = client.prune(NOW - 8 * DAY, {"node": 5, "ecowitt": 6}, STORE_ID)
        assert set(reply) == {"deleted", "first", "more"}
        post = Gateway.posts[-1]
        assert post["path"] == "/store/prune"
        assert post["auth"] == f"Bearer {TOKEN}"
        assert post["length"] is not None
        q = {k: v[0] for k, v in parse_qs(post["body"]).items()}
        assert q == {"before": f"{NOW - 8 * DAY:.0f}", "store_id": STORE_ID,
                     "node_through": "5", "ecowitt_through": "6"}, q
    finally:
        srv.shutdown()


def test_client_prune_old_firmware_and_conflict():
    srv, client = serve(mode="old_fw")
    try:
        try:
            client.prune(NOW, {"node": 1, "ecowitt": 1}, STORE_ID)
            raise AssertionError("expected PruneUnsupported")
        except PruneUnsupported:
            pass
    finally:
        srv.shutdown()
    srv, client = serve(mode="conflict")
    try:
        try:
            client.prune(NOW, {"node": 1, "ecowitt": 1}, STORE_ID)
            raise AssertionError("expected StoreChanged")
        except StoreChanged:
            pass
    finally:
        srv.shutdown()


# --- prune_store -------------------------------------------------------------------------

def test_prunes_old_backfilled_blocks_across_rounds_and_keeps_the_newest():
    # 5 weekly blocks per stream, the newest current: last records 28, 21, 14, 7, 0 days old.
    # 8 days keeps the 7-day block and the newest, so 3 go from each stream; a budget of 2
    # per request makes that take several rounds.
    srv, client = serve()
    try:
        msg = no_warnings(lambda: run(client, days=8))
        assert msg.startswith("pruned 3 node / 3 ecowitt block(s) older than 8 d"), msg
        assert "node seq 30000" in msg and "ecowitt seq 30000" in msg, msg
        assert [b[0] for b in Gateway.streams["node"]] == [3, 4]
        assert len(Gateway.posts) > 1
        again = run(client, days=8)
        assert again.startswith("pruned 0 node / 0 ecowitt"), again
    finally:
        srv.shutdown()


def test_the_request_carries_the_cutoff_and_the_cursor():
    srv, client = serve()
    try:
        run(client, FakeReconciler({"node": 19999, "ecowitt": 0}), days=90)
        q = {k: v[0] for k, v in parse_qs(Gateway.posts[0]["body"]).items()}
        assert q["before"] == f"{NOW - 90 * DAY:.0f}"
        assert q["node_through"] == "19999" and q["ecowitt_through"] == "0"
    finally:
        srv.shutdown()


def test_never_prunes_past_the_backfill_cursor():
    # All blocks are ancient, but backfill has only written through the first block's end.
    old = {"node": blocks(5, newest_age_days=365), "ecowitt": blocks(5, newest_age_days=365)}
    srv, client = serve(streams=old)
    try:
        msg = run(client, FakeReconciler({"node": BLOCK - 1, "ecowitt": BLOCK - 2}), days=8)
        assert msg.startswith("pruned 1 node / 0 ecowitt"), msg
    finally:
        srv.shutdown()


def test_v1_gateway_refuses_without_posting():
    srv, client = serve(mode="v1")
    try:
        msg = no_warnings(lambda: run(client))
        assert msg == "not a v2 gateway store; nothing pruned", msg
        assert Gateway.posts == []
    finally:
        srv.shutdown()


def test_not_configured():
    srv, client = serve()
    try:
        assert run(client, days=None).startswith("pruning not configured")
        assert run(client, days=0).startswith("pruning not configured")
        assert Gateway.posts == []
    finally:
        srv.shutdown()


def test_sd_not_mounted():
    srv, client = serve(sd_ok=False)
    try:
        assert run(client) == "not pruned: gateway SD not mounted"
        assert Gateway.posts == []
    finally:
        srv.shutdown()


def test_card_backfill_has_not_read_is_refused():
    srv, client = serve(store_id="aaaaaaaaaaaaaaaa")
    try:
        assert run(client) == "not pruned: backfill has not read this card yet"
        assert run(client, FakeReconciler(store_id=None)) == \
            "not pruned: backfill has not read this card yet"
        assert Gateway.posts == []
    finally:
        srv.shutdown()


def test_nothing_backfilled_yet():
    srv, client = serve()
    try:
        msg = run(client, FakeReconciler({"node": 0, "ecowitt": 0}))
        assert msg == "nothing backfilled yet; nothing to prune", msg
        assert Gateway.posts == []
    finally:
        srv.shutdown()


def test_old_firmware_conflict_busy_unreachable():
    for mode, want in (("old_fw", "gateway firmware has no prune endpoint; update v2"),
                       ("conflict", "not pruned: gateway card changed"),
                       ("busy", "not pruned: gateway unreachable")):
        srv, client = serve(mode=mode)
        try:
            msg = no_warnings(lambda: run(client))
            assert msg == want, (mode, msg)
        finally:
            srv.shutdown()
    dead = StoreClient("http://127.0.0.1:9", TOKEN, timeout=0.5)
    assert run(dead) == "not pruned: gateway unreachable"


def test_bad_token():
    srv, _ = serve()
    try:
        client = StoreClient(f"http://127.0.0.1:{srv.server_address[1]}", "wrong" * 4)
        assert run(client) == "not pruned: gateway rejected gateway_store_token"
    finally:
        srv.shutdown()


def test_prune_days_option():
    assert _prune_days(90) == 90 and _prune_days("30") == 30
    # Blank, absent, bashio's "null", junk, or below the 7-day floor: not configured.
    for raw in (None, "", "null", "abc", 0, 6):
        assert _prune_days(raw) is None, raw


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
