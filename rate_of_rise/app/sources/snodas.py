"""SNODAS snow water equivalent (slice 2e).

Spec §1 flags rain-on-snow as a major regional flood driver and §5 wants snowpack state as a
model input, from SNODAS, with no added hardware. NOHRSC publishes no point API — the
web "nearest station" page returns HTML regardless of the documented `fmt` parameter — so
this reads the gridded product directly and pulls the single cell over the site.

  GET noaadata.apps.nsidc.org/NOAA/G02158/masked/{YYYY}/{MM_Mon}/SNODAS_{YYYYMMDD}.tar

The tar holds one `.dat.gz` per product; SWE is product `11034`. Each grid is a flat
big-endian int16 raster, 6935 x 3351 cells at 30 arc-seconds, no-data `-9999`, values in
millimetres (the header states "Meters / 1000"). Georeferencing comes from the header's
benchmark cell, whose centre is (-124.729166666662, 52.8708333333312).

Cost is modest for a daily product: the tar runs ~3 MB in summer and ~27 MB mid-winter,
and only one cell is needed, so the gzip member is streamed and discarded rather than
decompressed whole (the full raster would be ~46 MB).

SNODAS posts with a lag and occasionally skips a day, so this walks back up to
`MAX_LOOKBACK_DAYS` and caches the answer per date under `/data/state/` — a restart or a
same-day re-poll costs nothing.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import struct
import tarfile
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger("app.sources.snodas")

USER_AGENT = "ewfa-creek-modeling (github.com/ryanbuiltthat/rate-of-rise)"
BASE_URL = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked"
SWE_PRODUCT = "11034"

# Masked-grid geometry, from the product header (verified against a February file: the
# site read 42 mm, ocean and Florida cells read no-data, and a Cascades cell saturated).
NCOLS, NROWS = 6935, 3351
BENCHMARK_LON, BENCHMARK_LAT = -124.729166666662, 52.8708333333312
RESOLUTION_DEG = 0.008333333333333
NO_DATA = -9999
MM_PER_INCH = 25.4

MAX_LOOKBACK_DAYS = 5
CHUNK = 1 << 20


class SnodasSwe:
    name = "snodas"
    refresh_seconds = 6 * 3600      # daily product; a few tries a day covers late posting
    # SNODAS legitimately skips days, and the fetch already walks back MAX_LOOKBACK_DAYS,
    # so only a multi-day drought of postings is worth flagging.
    stale_after_seconds = 3 * 86400

    def __init__(self, lat: float, lon: float, state_dir: Path,
                 today_fn=date.today, timeout: float = 120.0):
        self._row, self._col = self._cell(lat, lon)
        self._state_path = state_dir / "state" / "snodas.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._today = today_fn
        self._timeout = timeout
        self._cache = self._load_cache()

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        col = round((lon - BENCHMARK_LON) / RESOLUTION_DEG)
        row = round((BENCHMARK_LAT - lat) / RESOLUTION_DEG)
        return row, col

    def _load_cache(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _save_cache(self) -> None:
        try:
            self._state_path.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist snodas state: %s", exc)

    def poll(self) -> dict:
        if not (0 <= self._row < NROWS and 0 <= self._col < NCOLS):
            log.warning("site falls outside the SNODAS grid — SWE unavailable")
            return {"snow_water_equivalent_in": None}

        today = self._today()
        for back in range(MAX_LOOKBACK_DAYS):
            day = today - timedelta(days=back)
            key = day.isoformat()
            if key in self._cache:
                return {"snow_water_equivalent_in": self._cache[key]}
            try:
                swe_in = self._fetch_day(day)
            except Exception as exc:  # not yet posted, or a transient network error
                log.debug("SNODAS %s unavailable: %s", key, exc)
                continue
            # Keep only the newest few days so the state file cannot grow without bound.
            self._cache[key] = swe_in
            for stale in sorted(self._cache)[:-MAX_LOOKBACK_DAYS]:
                del self._cache[stale]
            self._save_cache()
            log.info("SNODAS %s: SWE %s in", key, swe_in)
            return {"snow_water_equivalent_in": swe_in}

        log.warning("no SNODAS file in the last %d days", MAX_LOOKBACK_DAYS)
        return {"snow_water_equivalent_in": None}

    def _url(self, day: date) -> str:
        return (f"{BASE_URL}/{day.year}/{day.month:02d}_{day.strftime('%b')}"
                f"/SNODAS_{day.strftime('%Y%m%d')}.tar")

    def _fetch_day(self, day: date) -> float | None:
        r = requests.get(self._url(day), headers={"User-Agent": USER_AGENT},
                         timeout=self._timeout)
        r.raise_for_status()
        with tarfile.open(fileobj=io.BytesIO(r.content)) as tf:
            member = next(n for n in tf.getnames()
                          if SWE_PRODUCT in n and n.endswith(".dat.gz"))
            raw = self._read_cell(tf.extractfile(member))
        value = struct.unpack(">h", raw)[0]
        if value == NO_DATA:
            return None
        return round(value / MM_PER_INCH, 3)

    def _read_cell(self, stream) -> bytes:
        """Stream the gzip member up to our cell's offset without holding the raster."""
        offset = (self._row * NCOLS + self._col) * 2
        with gzip.GzipFile(fileobj=stream) as gz:
            remaining = offset
            while remaining > 0:
                chunk = gz.read(min(CHUNK, remaining))
                if not chunk:
                    raise ValueError("SNODAS grid ended before the target cell")
                remaining -= len(chunk)
            cell = gz.read(2)
        if len(cell) != 2:
            raise ValueError("SNODAS grid truncated at the target cell")
        return cell
