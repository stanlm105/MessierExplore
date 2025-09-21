"""
Assessment logic for Messier target recommendations based on weather, moon,
altitude, and user progress. Scans an evening window and picks each object's
best hour for scoring and display.
"""

from __future__ import annotations

from datetime import datetime
from datetime import time as dtime
import re

# Project helpers (assumed available)
from utils.weather import *
from utils.moon import *          # expects: get_moon_state, ang_sep_deg, altaz_of, (optionally) get_sun_state/sun_alt_deg
from utils.time_helpers import *  # expects: when_9pm_local

# ---------------------------------------------------------------------------

EVENING_WINDOW = (20, 23)   # 20:00–23:00 local; adjust as desired
TWILIGHT_LIMIT = -12.0      # ignore hours when Sun alt > -12° (set -18 for astro-dark)

BORTLE_TO_NELM = {1:7.6, 2:7.1, 3:6.6, 4:6.1, 5:5.6, 6:5.1, 7:4.6, 8:4.1, 9:3.6}

# ---------------------------------------------------------------------------

def _sun_alt_deg_or_none(lat: float, lon: float, when: datetime) -> float | None:
    """
    Try to obtain Sun altitude (deg). If your utils don’t provide a Sun helper,
    return None so twilight filtering is skipped.
    """
    try:
        state = get_sun_state(lat, lon, when)  # type: ignore
        alt = state.get("alt_deg")
        if alt is not None:
            return float(alt)
    except Exception:
        pass
    try:
        return float(sun_alt_deg(lat, lon, when))  # type: ignore
    except Exception:
        return None


def _is_dark_enough(lat: float, lon: float, when: datetime, limit: float = TWILIGHT_LIMIT) -> bool:
    """Return True if Sun altitude is <= limit, or if Sun altitude cannot be obtained."""
    alt = _sun_alt_deg_or_none(lat, lon, when)
    return True if alt is None else (alt <= limit)

# ---------------------------------------------------------------------------

def coerce_seen_set(seen_numbers) -> set[int]:
    """
    Convert various representations of seen Messier numbers to a set of ints.
    Accepts string ('1,3'), list/set/tuple, single int-like, or None.
    """
    if not seen_numbers:
        return set()
    if isinstance(seen_numbers, (set, list, tuple)):
        out: set[int] = set()
        for x in seen_numbers:
            try:
                out.add(int(x))
            except Exception:
                pass
        return out
    if isinstance(seen_numbers, str):
        return {int(m) for m in re.findall(r"\d+", seen_numbers)}
    try:
        return {int(seen_numbers)}
    except Exception:
        return set()


def weather_mag_limit(cloud_pct: float) -> float:
    """
    Limiting magnitude due to clouds. 2-mag penalty at 100% clouds.
    """
    cloud_pct = max(0.0, min(100.0, float(cloud_pct)))
    return 8.8 - 0.02 * cloud_pct


def bortle_mag_limit(bortle_class: int) -> float:
    """
    Limiting magnitude due to sky brightness (Bortle). Uses simple NELM mapping.
    """
    nelm = BORTLE_TO_NELM.get(int(bortle_class or 5), 5.6)  # default B5
    return nelm + 1.8


def score_target(alt_deg: float, mag: float | None) -> float:
    """
    Score a target (higher is better). Boost altitude, penalize faintness.
    """
    mag_val = mag if mag is not None else 12.0
    return (max(0.0, alt_deg) / 90.0) - 0.02 * mag_val

# ---------------------------------------------------------------------------

def target_assessment(
    catalog, lat, lon, *,
    cloud_pct: float,
    bortle_class: int,
    seen_numbers: set[int] | None = None,
    top_n: int = 5,
    min_alt: float = 25.0,
    weather: dict | None = None,
    hard_kill_on_weather: bool = True,
):
    """
    Assess Messier targets for tonight, scanning an evening window and picking
    each object's best hour that clears altitude, darkness, and Moon proximity.

    Returns:
        tuple[str, list[dict], dict]: (narrative_html, top_targets, moon_state_at_9pm)
    """
    seen_numbers = coerce_seen_set(seen_numbers)
    weather = weather or {}

    # ---- Optional hard-stop for unsafe weather ----
    if hard_kill_on_weather:
        precip = float(weather.get("precip_mm_per_hr") or 0.0)
        snow   = float(weather.get("snow_mm_per_hr") or 0.0)
        wind   = float(weather.get("wind_kph") or 0.0)
        gust   = float(weather.get("gust_kph") or wind)
        vis    = weather.get("visibility_km")
        tprob  = float(weather.get("thunder_prob") or 0.0)

        reasons = []
        if precip >= 0.1: reasons.append(f"precip {precip:.1f} mm/h")
        if snow   >= 0.1: reasons.append(f"snow {snow:.1f} mm/h")
        if wind   >= 35:  reasons.append(f"wind {wind:.0f} km/h")
        if gust   >= 55:  reasons.append(f"gusts {gust:.0f} km/h")
        if vis is not None and vis < 5: reasons.append(f"visibility {vis:.1f} km")
        if tprob >= 0.3: reasons.append("thunder risk")

        if reasons:
            when = when_9pm_local(lat, lon)
            moon = get_moon_state(lat, lon, when)
            note = "; ".join(reasons)
            narrative = (
                "Target Assessment (9:00 PM):<br>"
                f"Weather unsafe/unfavorable — {note}.<br>"
                "No targets recommended."
            )
            return narrative, [], moon

    # Base time & Moon info for narrative/thresholds
    base = when_9pm_local(lat, lon)
    date_local = base.date()
    tz = base.tzinfo
    moon_9pm = get_moon_state(lat, lon, base)
    sep_min = 20.0 + 25.0 * moon_9pm["illum"]  # 20° @ new → 45° @ full

    # Normalize catalog → Messier only, with int numbers
    norm_pool = []
    for o in catalog:
        if str(o.get("catalog", "")).upper() != "M":
            continue
        try:
            num = int(o["number"])
        except Exception:
            continue
        norm_pool.append({**o, "number": num})

    pool = [o for o in norm_pool if str(o.get("catalog", "")).upper() == "M"]
    start_total = len(pool)

    # Layer 1: already seen
    pool1 = [o for o in pool if o["number"] not in seen_numbers]
    removed_seen = start_total - len(pool1)

    # Layer 2: weather faintness
    w_limit = weather_mag_limit(cloud_pct)
    pool2 = [o for o in pool1 if (o.get("magnitude") or 99.0) <= w_limit]
    removed_weather = len(pool1) - len(pool2)

    # Layer 3: Bortle faintness
    b_limit = bortle_mag_limit(bortle_class)
    pool3 = [o for o in pool2 if (o.get("magnitude") or 99.0) <= b_limit]
    removed_bortle = len(pool2) - len(pool3)

    # Layer 4: scan evening hours
    hours = range(EVENING_WINDOW[0], EVENING_WINDOW[1] + 1)

    survivors: list[dict] = []
    removed_altitude = 0
    removed_moon = 0

    for o in pool3:
        mag = o.get("magnitude")
        had_alt_ok = False

        # Track if any hour passes BOTH altitude+dark AND moon proximity (when Moon is up)
        passed_any_hour = False

        # Best *passing* hour (for display)
        best_pass: tuple | None = None

        for h in hours:
            when = datetime.combine(date_local, dtime(h, 0), tzinfo=tz)

            # Skip bright twilight if Sun helper available
            if not _is_dark_enough(lat, lon, when, TWILIGHT_LIMIT):
                continue

            alt, az = altaz_of(o["ra_deg"], o["dec_deg"], lat, lon, when)
            if alt < min_alt:
                continue
            had_alt_ok = True

            # Moon & separation — always compute sep; only penalize if Moon is up
            moon = get_moon_state(lat, lon, when)
            sep = ang_sep_deg(moon["ra_deg"], moon["dec_deg"], o["ra_deg"], o["dec_deg"])
            moon_up = (moon.get("alt_deg", -90.0) > 0.0)
            moon_alt = float(moon.get("alt_deg", -90.0))
            if moon_up and sep < sep_min:
                # too close this hour
                continue

            # This hour passes all gates
            passed_any_hour = True
            moon_pen = (max(0.0, (45.0 - sep)) / 45.0 * moon["illum"]) if moon_up else 0.0
            score = score_target(alt, mag) - 0.4 * moon_pen

            cand = (score, when, alt, az, sep, moon_up, moon_alt)
            if (best_pass is None) or (score > best_pass[0]):
                best_pass = cand

        # Bucket into altitude or moon removals if no passing hour found
        if not had_alt_ok:
            removed_altitude += 1
            continue
        if not passed_any_hour:
            removed_moon += 1
            continue

        # Keep survivor with data from best passing hour
        score, when_best, alt_best, az_best, sep_best, moon_up_best, moon_alt_best = best_pass  # type: ignore
        survivors.append({
            "number": o["number"],
            "name": o.get("name", ""),
            "type": o.get("type", "Deep-Sky Object"),
            "constellation": o.get("constellation", "—"),
            "magnitude": o.get("magnitude"),
            "alt": alt_best, "az": az_best,
            "moon_sep": sep_best,
            "moon_up": moon_up_best,
            "moon_alt": moon_alt_best,
            "score": score,
            "when": when_best,   # timezone-aware
        })

    survivors.sort(key=lambda r: r["score"], reverse=True)
    top = survivors[:top_n]

    # Narrative counts
    remaining_after_seen = len(pool1)
    remaining_after_weather = len(pool2)
    remaining_after_bortle = len(pool3)
    remaining_after_altitude = len(pool3) - removed_altitude
    remaining_final = len(survivors)

    phase_names = [
        "New Moon","Waxing Crescent","First Quarter","Waxing Gibbous",
        "Full Moon","Waning Gibbous","Last Quarter","Waning Crescent"
    ]
    phase = phase_names[moon_9pm["phase_idx"]]
    illum_pct = int(round(moon_9pm["illum"] * 100))

    weather_label = ("Clear" if cloud_pct < 20 else
                     "Fair" if cloud_pct < 50 else
                     "Poor" if cloud_pct < 80 else "Cloudy")

    narrative = []
    narrative.append("<table border=0><tr>")
    narrative.append(f"<td>Target Assessment Time Basis:</td><td>9:00 PM Local ({str(tz)})</td></tr>")
    narrative.append(f"<tr><td>Messier Targets:</td><td>{start_total} deep sky objects (*)</td></tr>")
    narrative.append(f"<tr><td>Already Seen Objects:</td><td>minus {removed_seen} objects already seen</td>"
                     f"<td><font color=lime size=2>Remaining targets: {remaining_after_seen}</font></td></tr>")
    narrative.append(f"<tr><td>Weather {weather_label}:</td><td>minus {removed_weather} faintest obj (mag &gt; {w_limit:.1f})</td>"
                     f"<td><font color=lime size=2>Remaining targets: {remaining_after_weather}</font></td></tr>")
    narrative.append(f"<tr><td>Bortle Class {bortle_class}:</td><td>minus {removed_bortle} objects (mag &gt; {b_limit:.1f})</td>"
                     f"<td><font color=lime size=2>Remaining targets: {remaining_after_bortle}</font></td></tr>")
    narrative.append(f"<tr><td>Altitude ≥ {int(min_alt)}° (best hour {EVENING_WINDOW[0]}–{EVENING_WINDOW[1]}):</td>"
                     f"<td>minus {removed_altitude} objects near/below horizon</td>"
                     f"<td><font color=lime size=2>Remaining targets: {remaining_after_altitude}</font></td></tr>")
    narrative.append(f"<tr><td>Moon Factor<br>({phase}, {illum_pct}% illum):</td>"
                     f"<td>minus {removed_moon} objects from<br>bright moon proximity (sep &lt; {sep_min:.0f}°)</td>"
                     f"<td><font color=lime size=2>Remaining targets: {remaining_final}</font></td></tr>")
    narrative.append("</table>")

    return ("".join(narrative), top, moon_9pm)

# ---------------------------------------------------------------------------

def _compass16(az: float) -> str:
    """Convert azimuth degrees to a 16-point compass string."""
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[int((az + 11.25) // 22.5) % 16]


def render_top_targets(top: list[dict]) -> str:
    """
    Render HTML for the top Messier targets list. Shows best hour, alt/az,
    and either separation from Moon (if Moon up) or 'Moon set (alt)'.
    """
    items = []
    for t in top:
        meta = f"<i>{t['type']} • {t['constellation']}</i> — "
        when_str = t["when"].strftime("%H:%M") if t.get("when") else "—"
        if t.get("moon_up", False):
            moon_txt = f"{t['moon_sep']:.0f}° from Moon"
        else:
            moon_txt = f"Moon set ({t.get('moon_alt', -99):.0f}°)"
        items.append(
            f"<li><b>M{t['number']}</b> {t['name']}<br>"
            f"<small>{meta}"
            f"best ~{when_str} • "
            f"mag {t['magnitude'] if t['magnitude'] is not None else '—'} • "
            f"{t['alt']:.0f}° alt • az {t['az']:.0f}° ({_compass16(t['az'])}) • "
            f"{moon_txt} — "
            f"<a target='_blank' href='https://stellarium-web.org/skysource/M{t['number']}'>View</a></small></li>"
        )
    return "<ul class='targets'>" + "\n".join(items) + "</ul>"
