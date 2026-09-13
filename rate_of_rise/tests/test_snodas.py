"""Tests for the SNODAS SWE source (no HTTP — a synthetic grid stands in for the tar).
Run: python rate_of_rise/tests/test_snodas.py
"""
import gzip
import io
import struct
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sources import snodas as sn  # noqa: E402
from app.sources.snodas import SnodasSwe  # noqa: E402

SITE = (41.0, -75.5)


def build_tar(cell_value, row, col):
    """A tar shaped like SNODAS_YYYYMMDD.tar with one SWE grid holding `cell_value`."""
    offset = (row * sn.NCOLS + col) * 2
    raw = b"\x00\x00" * (offset // 2) + struct.pack(">h", cell_value) + b"\x00\x00"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, payload in (
            (f"us_ssmv1{sn.SWE_PRODUCT}tS__T0001TTNATS2026020105HP001.dat.gz",
             gzip.compress(raw)),
            ("us_ssmv11036tS__T0001TTNATS2026020105HP001.dat.gz", gzip.compress(b"\x00")),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def patch_get(monkey_content, calls=None):
    """Replace requests.get inside the module with one returning `monkey_content`."""
    def fake_get(url, headers=None, timeout=None):
        if calls is not None:
            calls.append(url)
        content = monkey_content(url) if callable(monkey_content) else monkey_content
        if content is None:
            raise RuntimeError("404 not posted yet")
        return FakeResponse(content)
    sn.requests.get = fake_get


def state_dir():
    return Path(tempfile.mkdtemp())


def test_cell_indexing_matches_the_published_geometry():
    # Verified by hand against a real February file: the site is row 1362, col 5880.
    assert SnodasSwe._cell(*SITE) == (1424, 5907)


def test_reads_the_site_cell_and_converts_mm_to_inches():
    row, col = SnodasSwe._cell(*SITE)
    patch_get(build_tar(42, row, col))
    src = SnodasSwe(*SITE, state_dir(), today_fn=lambda: date(2026, 2, 1))
    assert src.poll()["snow_water_equivalent_in"] == 1.654   # 42 mm


def test_no_data_cell_reports_none_not_zero():
    row, col = SnodasSwe._cell(*SITE)
    patch_get(build_tar(sn.NO_DATA, row, col))
    src = SnodasSwe(*SITE, state_dir(), today_fn=lambda: date(2026, 2, 1))
    assert src.poll()["snow_water_equivalent_in"] is None


def test_second_poll_is_served_from_cache():
    row, col = SnodasSwe._cell(*SITE)
    calls = []
    patch_get(build_tar(42, row, col), calls)
    src = SnodasSwe(*SITE, state_dir(), today_fn=lambda: date(2026, 2, 1))
    src.poll()
    src.poll()
    assert len(calls) == 1, calls   # daily product — one fetch per day, not per loop


def test_walks_back_when_today_is_not_posted_yet():
    row, col = SnodasSwe._cell(*SITE)
    tar = build_tar(42, row, col)
    calls = []
    # SNODAS posts with a lag: today 404s, yesterday is there.
    patch_get(lambda url: None if "20260201" in url else tar, calls)
    src = SnodasSwe(*SITE, state_dir(), today_fn=lambda: date(2026, 2, 1))
    assert src.poll()["snow_water_equivalent_in"] == 1.654
    assert "20260201" in calls[0] and "20260131" in calls[1]


def test_gives_up_after_the_lookback_window():
    calls = []
    patch_get(lambda url: None, calls)
    src = SnodasSwe(*SITE, state_dir(), today_fn=lambda: date(2026, 2, 1))
    assert src.poll()["snow_water_equivalent_in"] is None
    assert len(calls) == sn.MAX_LOOKBACK_DAYS


def test_url_uses_the_month_abbreviation_layout():
    src = SnodasSwe(*SITE, state_dir())
    url = src._url(date(2026, 2, 1))
    assert url.endswith("/2026/02_Feb/SNODAS_20260201.tar"), url


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
