from datetime import datetime, timedelta, timezone

from utils.jupiter_grs import (
    GrsConfig,
    grs_last_next_transits,
    load_grs_config,
    parse_iso8601_utc,
)


def test_parse_iso8601_utc_accepts_z():
    dt = parse_iso8601_utc("2026-01-13T03:41:00Z")
    assert dt.tzinfo is not None
    assert dt == datetime(2026, 1, 13, 3, 41, 0, tzinfo=timezone.utc)


def test_parse_iso8601_utc_naive_treated_as_utc():
    dt = parse_iso8601_utc("2026-01-13T03:41:00")
    assert dt == datetime(2026, 1, 13, 3, 41, 0, tzinfo=timezone.utc)


def test_grs_transits_math_edges():
    ref = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    cfg = GrsConfig(reference_transit_utc=ref, rotation_period_seconds=10.0, source="test")

    out0 = grs_last_next_transits(ref, config=cfg)
    assert out0["last_transit_utc"].endswith("Z")
    assert out0["next_transit_utc"].endswith("Z")
    assert out0["last_transit_utc"].startswith("2026-01-01T00:00:00")
    assert out0["next_transit_utc"].startswith("2026-01-01T00:00:10")

    out5 = grs_last_next_transits(ref.replace(second=5), config=cfg)
    assert out5["last_transit_utc"].startswith("2026-01-01T00:00:00")
    assert out5["next_transit_utc"].startswith("2026-01-01T00:00:10")

    out10 = grs_last_next_transits(ref.replace(second=10), config=cfg)
    assert out10["last_transit_utc"].startswith("2026-01-01T00:00:10")
    assert out10["next_transit_utc"].startswith("2026-01-01T00:00:20")

    out_before = grs_last_next_transits(ref.replace(second=0) - timedelta(seconds=1), config=cfg)
    assert out_before["last_transit_utc"].startswith("2025-12-31T23:59:50")
    assert out_before["next_transit_utc"].startswith("2026-01-01T00:00:00")


def test_load_from_env_smoke(monkeypatch):
    monkeypatch.setenv("JUPITER_GRS_REFERENCE_TRANSIT_UTC", "2026-01-01T00:00:00Z")
    # Ensure SOURCE_URL not set.
    monkeypatch.delenv("JUPITER_GRS_SOURCE_URL", raising=False)
    out = grs_last_next_transits(datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc))
    assert out["ok"] is True
    assert out["method"] == "reference-period"


def test_load_from_local_file(tmp_path, monkeypatch):
    p = tmp_path / "jupiter_grs.json"
    p.write_text(
        '{"reference_transit_utc":"2026-01-14T05:32:00Z","rotation_period_seconds":35740.6,"updated_utc":"2026-01-14T00:00:00Z"}',
        encoding="utf-8",
    )

    monkeypatch.setenv("JUPITER_GRS_SOURCE_FILE", str(p))
    monkeypatch.delenv("JUPITER_GRS_SOURCE_URL", raising=False)
    monkeypatch.delenv("JUPITER_GRS_REFERENCE_TRANSIT_UTC", raising=False)

    cfg = load_grs_config()
    assert cfg.source.startswith("file:")
    assert cfg.reference_transit_utc == datetime(2026, 1, 14, 5, 32, 0, tzinfo=timezone.utc)
