"""StoreClient: talks to a v2 gateway's store, and stays silent about everything else.

The same option can point at a v2 gateway, a v1 gateway (no store), a gateway that is down,
or nothing. Only the first is a path to backfill; all the others must look exactly like
backfill not existing: no exception, nothing logged above DEBUG.

Run: python rate_of_rise/tests/test_backfill_client.py
"""
import json
import logging
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from app.backfill.client import ProbeState, StoreClient  # noqa: E402

TOKEN = "t" * 32


class Gateway(BaseHTTPRequestHandler):
    mode = "v2"          # v2 | 404 | html | sleep | wrongschema | 503
    records = {"node": [], "ecowitt": []}
    page_size = 500

    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        mode = type(self).mode
        if mode == "sleep":
            time.sleep(1.0)
        if mode == "404":
            return self._send(404, "not found", "text/plain")
        if mode == "503":
            return self._send(503, "bus busy", "text/plain")
        if mode == "html":
            return self._send(200, "<html><body>ESPHome</body></html>", "text/html")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, "unauthorized", "text/plain")
        recs = type(self).records
        if self.path.startswith("/store/status"):
            schema = 2 if mode == "wrongschema" else 1
            return self._send(200, json.dumps({
                "store_schema": schema, "device": "creek-gateway-v2", "now": 0, "ts_src": "ntp",
                "sd_ok": True, "sd_free_mb": 1,
                "streams": {s: {"first": 1 if r else 0, "last": r[-1]["seq"] if r else 0}
                            for s, r in recs.items()}}))
        if self.path.startswith("/store/records"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            stream, after, limit = q["stream"][0], int(q["after"][0]), int(q["limit"][0])
            limit = min(limit, type(self).page_size)
            page = [r for r in recs[stream] if r["seq"] > after][:limit]
            body = "".join(json.dumps(r) + "\n" for r in page)
            body += '{"seq":99999,"ts":1'   # a torn tail must be ignored, not crash
            last = recs[stream][-1]["seq"] if recs[stream] else 0
            return self._send(200, body, "application/x-ndjson", {"X-Store-Last": str(last)})
        return self._send(404, "not found", "text/plain")


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class Loud(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def quietly(fn):
    """Run fn and assert nothing under app.* logged at INFO or above."""
    h = Loud()
    lg = logging.getLogger("app")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    try:
        out = fn()
    finally:
        lg.removeHandler(h)
    assert not h.records, [r.getMessage() for r in h.records]
    return out


def free_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_v2_store_probes_ok():
    Gateway.mode = "v2"
    Gateway.records = {"node": [{"seq": 1, "ts": 1.0}], "ecowitt": []}
    srv, url = serve()
    try:
        probe = StoreClient(url, TOKEN).probe()
        assert probe.state is ProbeState.OK
        assert probe.status["streams"]["node"]["last"] == 1
    finally:
        srv.shutdown()


def test_v1_gateway_404_is_no_store_and_silent():
    Gateway.mode = "404"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_esphome_html_page_is_no_store_and_silent():
    Gateway.mode = "html"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_unknown_store_schema_is_no_store():
    Gateway.mode = "wrongschema"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.NO_STORE
    finally:
        srv.shutdown()


def test_connection_refused_is_unreachable_and_silent():
    probe = quietly(lambda: StoreClient(free_port_url(), TOKEN).probe())
    assert probe.state is ProbeState.UNREACHABLE


def test_timeout_is_unreachable_and_silent():
    Gateway.mode = "sleep"
    srv, url = serve()
    try:
        probe = quietly(lambda: StoreClient(url, TOKEN, timeout=0.2).probe())
        assert probe.state is ProbeState.UNREACHABLE
    finally:
        srv.shutdown()


def test_server_error_is_unreachable_and_silent():
    # A v2 gateway whose bus is busy (or any 5xx) is a v2 gateway having a bad moment, not a
    # v1 gateway: retry next pass, not in an hour.
    Gateway.mode = "503"
    srv, url = serve()
    try:
        assert quietly(lambda: StoreClient(url, TOKEN).probe()).state is ProbeState.UNREACHABLE
    finally:
        Gateway.mode = "v2"
        srv.shutdown()


def test_bad_token():
    Gateway.mode = "v2"
    srv, url = serve()
    try:
        assert StoreClient(url, "wrong" * 4).probe().state is ProbeState.BAD_TOKEN
    finally:
        srv.shutdown()


def test_records_pages_until_last_and_skips_torn_lines():
    Gateway.mode = "v2"
    Gateway.page_size = 3
    Gateway.records = {"node": [{"seq": i, "ts": float(i)} for i in range(1, 9)], "ecowitt": []}
    srv, url = serve()
    try:
        recs = StoreClient(url, TOKEN).records("node", after=2, max_records=100)
        assert [r["seq"] for r in recs] == [3, 4, 5, 6, 7, 8]
        capped = StoreClient(url, TOKEN).records("node", after=0, max_records=4)
        assert [r["seq"] for r in capped] == [1, 2, 3, 4]
    finally:
        Gateway.page_size = 500
        srv.shutdown()


def test_records_network_failure_raises():
    try:
        StoreClient(free_port_url(), TOKEN).records("node", 0, 10)
    except requests.RequestException:
        return
    raise AssertionError("expected RequestException")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
