"""Jupiter Great Red Spot (GRS) transit helper.

This module intentionally does NOT scrape Sky & Telescope (or any other site).
Instead, it computes last/next GRS transits from:
- a reference transit timestamp (UTC) and
- the System II rotation period.

You can provide the reference transit via env vars, or via a small JSON document
hosted somewhere you control.

Env configuration:
- JUPITER_GRS_REFERENCE_TRANSIT_UTC (required unless using SOURCE_URL)
- JUPITER_SYSTEM2_ROTATION_PERIOD_SECONDS (optional)
- JUPITER_GRS_SOURCE_URL (optional; if set, fetched JSON should contain
  "reference_transit_utc" and may contain "rotation_period_seconds" and "updated_utc")

The output is suitable for a lightweight API endpoint.
"""

from __future__ import annotations

import math
import os
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import requests
from skyfield.api import load, load_file, wgs84


# Jupiter System II rotation period is ~9h 55m 40.6s.
DEFAULT_SYSTEM2_ROTATION_PERIOD_SECONDS = 9 * 3600 + 55 * 60 + 40.6


@lru_cache(maxsize=1)
def _skyfield_context():
    """Load ephemeris + timescale once.

    Uses builtin timescale to avoid network downloads.
    """

    bsp_path = Path(__file__).resolve().parent.parent / "data" / "de421.bsp"
    eph = load_file(str(bsp_path))
    ts = load.timescale(builtin=True)
    return eph, ts


def jupiter_altaz_degrees(when_utc: datetime, lat: float, lon: float) -> tuple[float, float]:
    """Return (alt_deg, az_deg) of Jupiter at the given UTC time for an observer.

    Args:
        when_utc: aware datetime; naive is treated as UTC.
        lat/lon: observer coordinates in degrees.
    """

    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)

    eph, ts = _skyfield_context()
    t = ts.from_datetime(when_utc)

    observer = eph["earth"] + wgs84.latlon(latitude_degrees=float(lat), longitude_degrees=float(lon))
    target = eph["jupiter barycenter"]
    apparent = observer.at(t).observe(target).apparent()
    alt, az, _distance = apparent.altaz()
    return float(alt.degrees), float(az.degrees)


def parse_iso8601_utc(dt_str: str) -> datetime:
    """Parse an ISO8601 datetime, returning an aware UTC datetime.

    Accepts a trailing 'Z'. If no timezone is provided, the input is treated as UTC.

    Raises:
        ValueError: for empty/invalid input.
    """

    s = (dt_str or "").strip()
    if not s:
        raise ValueError("empty datetime")

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"invalid ISO8601 datetime: {dt_str}") from e

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def format_iso8601_z(dt: datetime) -> str:
    """Format a datetime as ISO8601 with trailing 'Z'."""

    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GrsConfig:
    reference_transit_utc: datetime
    rotation_period_seconds: float
    source: str
    updated_utc: Optional[datetime] = None


def load_grs_config() -> GrsConfig:
    """Load GRS configuration.

    Priority order:
      1) Local JSON file: env JUPITER_GRS_SOURCE_FILE or default data/jupiter_grs.json
      2) User-hosted JSON URL: JUPITER_GRS_SOURCE_URL
      3) Env var: JUPITER_GRS_REFERENCE_TRANSIT_UTC (+ optional period)
    """

    source_file = os.environ.get("JUPITER_GRS_SOURCE_FILE", "").strip()
    if not source_file:
        # Default location in this repo: <project_root>/data/jupiter_grs.json
        # utils/ is at <project_root>/utils
        default_path = Path(__file__).resolve().parent.parent / "data" / "jupiter_grs.json"
        source_file = str(default_path)

    try:
        p = Path(source_file)
        if p.exists() and p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            ref = parse_iso8601_utc(data["reference_transit_utc"])
            period = float(data.get("rotation_period_seconds", DEFAULT_SYSTEM2_ROTATION_PERIOD_SECONDS))
            updated_raw = data.get("updated_utc")
            updated = parse_iso8601_utc(updated_raw) if updated_raw else None
            return GrsConfig(
                reference_transit_utc=ref,
                rotation_period_seconds=period,
                source=f"file:{str(p)}",
                updated_utc=updated,
            )
    except Exception:
        # If the local file exists but is malformed/unreadable, fall through to other config.
        pass

    url = os.environ.get("JUPITER_GRS_SOURCE_URL", "").strip()
    if url:
        resp = requests.get(url, timeout=4)
        resp.raise_for_status()
        data = resp.json()

        ref = parse_iso8601_utc(data["reference_transit_utc"])
        period = float(data.get("rotation_period_seconds", DEFAULT_SYSTEM2_ROTATION_PERIOD_SECONDS))
        updated_raw = data.get("updated_utc")
        updated = parse_iso8601_utc(updated_raw) if updated_raw else None

        return GrsConfig(
            reference_transit_utc=ref,
            rotation_period_seconds=period,
            source=url,
            updated_utc=updated,
        )

    ref_env = os.environ.get("JUPITER_GRS_REFERENCE_TRANSIT_UTC", "").strip()
    if not ref_env:
        raise ValueError(
            "Missing GRS config: set JUPITER_GRS_REFERENCE_TRANSIT_UTC or JUPITER_GRS_SOURCE_URL"
        )

    ref = parse_iso8601_utc(ref_env)

    period_env = os.environ.get("JUPITER_SYSTEM2_ROTATION_PERIOD_SECONDS", "").strip()
    period = float(period_env) if period_env else float(DEFAULT_SYSTEM2_ROTATION_PERIOD_SECONDS)

    return GrsConfig(reference_transit_utc=ref, rotation_period_seconds=period, source="env")


def grs_last_next_transits(
    when_utc: Optional[datetime] = None,
    *,
    config: Optional[GrsConfig] = None,
) -> dict[str, Any]:
    """Compute last/next GRS transit times.

    Uses a reference transit time and constant rotation period (System II).

    Args:
        when_utc: time to evaluate at (aware or naive). Naive is treated as UTC.
        config: optional explicit config override.

    Returns:
        dict suitable for jsonify.
    """

    when_utc = when_utc or datetime.now(timezone.utc)
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)

    cfg = config or load_grs_config()
    ref = cfg.reference_transit_utc.astimezone(timezone.utc)
    period = float(cfg.rotation_period_seconds)
    if period <= 0:
        raise ValueError("rotation_period_seconds must be > 0")

    # Find integer k such that:
    #   last = ref + k*period  and  last <= when < last+period
    dt_sec = (when_utc - ref).total_seconds()
    k = math.floor(dt_sec / period)

    last_dt = ref + timedelta(seconds=k * period)
    next_dt = last_dt + timedelta(seconds=period)

    # Defensive adjustment for floating-point edge cases.
    if last_dt > when_utc:
        last_dt = last_dt - timedelta(seconds=period)
        next_dt = next_dt - timedelta(seconds=period)
    if next_dt <= when_utc:
        last_dt = last_dt + timedelta(seconds=period)
        next_dt = next_dt + timedelta(seconds=period)

    return {
        "ok": True,
        "method": "reference-period",
        "source": cfg.source,
        "updated_utc": format_iso8601_z(cfg.updated_utc) if cfg.updated_utc else None,
        "reference_transit_utc": format_iso8601_z(ref),
        "rotation_period_seconds": period,
        "when_utc": format_iso8601_z(when_utc),
        "last_transit_utc": format_iso8601_z(last_dt),
        "next_transit_utc": format_iso8601_z(next_dt),
        "seconds_since_last": (when_utc - last_dt).total_seconds(),
        "seconds_until_next": (next_dt - when_utc).total_seconds(),
    }
