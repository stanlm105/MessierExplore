"""
Moon phase, position, and Messier target recommendation utilities.

Provides functions to compute moon state, recommend Messier targets based on moon glare,
and generate human-readable moon narratives for observing guidance.
"""

# pip install astral skyfield
from datetime import date, datetime, timezone
from astral import moon
import math
from skyfield.api import load, wgs84, Star
from skyfield import almanac

# Load ephemeris and timescale once at module level for efficiency
ts  = load.timescale()
eph = load('data/de421.bsp')
EARTH, MOON, SUN = eph['earth'], eph['moon'], eph['sun']

def _f(x):
    """Convert to float, handling None and string 'None' gracefully."""
    if x is None:
        return None
    if isinstance(x, str):
        x = x.strip()
        if x.lower() == 'none' or x == '':
            return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None

def _phase_idx_from_angle(deg: float) -> int:
    """
    Map moon phase angle in degrees to phase index (0=new, ..., 7=waning crescent).

    Args:
        deg (float): Moon phase angle in degrees.

    Returns:
        int: Phase index (0-7).
    """
    deg = deg % 360.0
    if   deg < 22.5 or deg >= 337.5: return 0  # New
    elif deg < 67.5:  return 1                # Waxing Crescent
    elif deg < 112.5: return 2                # First Quarter
    elif deg < 157.5: return 3                # Waxing Gibbous
    elif deg < 202.5: return 4                # Full
    elif deg < 247.5: return 5                # Waning Gibbous
    elif deg < 292.5: return 6                # Last Quarter
    else:              return 7                # Waning Crescent

def _recommended_sep_min(illum: float, alt_deg: float) -> float:
    """
    Minimum DSO separation from the Moon, in degrees.

    - 0° if the Moon is below the horizon or essentially new (≤5% illuminated)
    - Otherwise scales roughly linearly from ~20° (thin crescent) to ~45° (full)
    """
    if alt_deg < 0 or illum <= 0.05:
        return 0.0
    # map 5%..100% -> 20°..45°
    # (subtract 0.05 so 5% starts at 0 on the scale)
    frac = max(0.0, min(1.0, (illum - 0.05) / 0.95))
    return 20.0 + 25.0 * frac

def get_moon_state(lat: float, lon: float, when: datetime) -> dict:
    """
    Compute moon state for given location and time.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        when (datetime): Date and time (timezone-aware recommended).

    Returns:
        dict: Moon state with RA, Dec, Alt, Az, illumination, phase angle, and phase index.
    """
    lat, lon = _f(lat), _f(lon)

    if lat is None or lon is None:
        print("Warning: Invalid coordinates for moon calculation, using default location")
        lat, lon = 40.0, -74.0  # Default to NYC
    
    t = ts.from_datetime(when)
    observer = EARTH + wgs84.latlon(lat, lon)

    app = observer.at(t).observe(MOON).apparent()
    alt, az, _ = app.altaz()
    ra,  dec, _ = app.radec()

    illum = float(almanac.fraction_illuminated(eph, 'moon', t))
    phase_angle = float(almanac.moon_phase(eph, t).degrees)  # 0..360
    phase_idx = _phase_idx_from_angle(phase_angle)

    return {
        "ra_deg": ra.hours * 15.0,
        "dec_deg": dec.degrees,
        "alt_deg": alt.degrees,
        "az_deg": az.degrees,
        "illum": illum,              # 0..1
        "phase_angle_deg": phase_angle,
        "phase_idx": phase_idx       # 0=new … 7=waning crescent
    }

def altaz_of(ra_deg: float, dec_deg: float, lat: float, lon: float, when: datetime) -> tuple:
    """
    Compute altitude and azimuth for a given RA/Dec at location and time.

    Args:
        ra_deg (float): Right Ascension in degrees.
        dec_deg (float): Declination in degrees.
        lat (float): Latitude.
        lon (float): Longitude.
        when (datetime): Date and time.

    Returns:
        tuple: (altitude, azimuth) in degrees.
    """
    lat = _f(lat); lon = _f(lon)
    t = ts.from_datetime(when)
    observer = EARTH + wgs84.latlon(lat, lon)
    star = Star(ra_hours=_f(ra_deg)/15.0, dec_degrees=_f(dec_deg))
    alt, az, _ = observer.at(t).observe(star).apparent().altaz()
    return alt.degrees, az.degrees

def ang_sep_deg(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    """
    Compute angular separation in degrees between two sky positions.

    Args:
        ra1_deg, dec1_deg: RA/Dec of first object (degrees).
        ra2_deg, dec2_deg: RA/Dec of second object (degrees).

    Returns:
        float: Angular separation in degrees.
    """
    r1, d1, r2, d2 = map(math.radians, map(_f, [ra1_deg, dec1_deg, ra2_deg, dec2_deg]))
    cossep = math.sin(d1)*math.sin(d2) + math.cos(d1)*math.cos(d2)*math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cossep))))

def moon_recommend_targets(
    catalog: list, lat: float, lon: float, when: datetime = None, min_alt: float = 25.0
) -> tuple:
    """
    Recommend Messier targets for tonight based on moon position and glare.

    Args:
        catalog (list): List of Messier objects (dicts with 'ra_deg', 'dec_deg', 'magnitude', etc.).
        lat (float): Latitude.
        lon (float): Longitude.
        when (datetime, optional): Date and time (defaults to now, UTC).
        min_alt (float): Minimum altitude for recommendation.

    Returns:
        tuple: (sorted list of targets, moon state dict)
    """
    when = when or datetime.now(timezone.utc)
    moon = get_moon_state(lat, lon, when)
    sep_min = _recommended_sep_min(moon["illum"], moon["alt_deg"])

    out = []
    for o in catalog:
        ra, dec = o["ra_deg"], o["dec_deg"]
        alt, az = altaz_of(ra, dec, lat, lon, when)
        if alt < min_alt:
            continue
        sep = ang_sep_deg(moon["ra_deg"], moon["dec_deg"], ra, dec)
        if sep < sep_min:
            continue
        mag = o.get("magnitude") or 99.0
        # Simple score: favor altitude, penalize faintness
        score = (alt / 90.0) - 0.02 * mag
        out.append({**o, "alt": alt, "az": az, "moon_sep": sep, "score": score})

    return sorted(out, key=lambda r: r["score"], reverse=True), moon

def az_to_compass(az: float) -> str:
    """
    Convert azimuth in degrees to compass direction (e.g., 'N', 'NE').

    Args:
        az (float): Azimuth in degrees.

    Returns:
        str: Compass direction.
    """
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[int((az + 11.25) // 22.5) % 16]

def moon_narrative(moon: dict) -> str:
    """
    Generate a human-readable narrative for the moon's phase, illumination, and position.

    Args:
        moon (dict): Moon state dictionary.

    Returns:
        str: Narrative HTML string.
    """
    if not moon:
        return "Moon data unavailable."

    phase_names = [
        "New Moon","Waxing Crescent","First Quarter","Waxing Gibbous",
        "Full Moon","Waning Gibbous","Last Quarter","Waning Crescent"
    ]
    phase = phase_names[moon["phase_idx"]]
    illum_pct = int(round(moon["illum"] * 100))
    alt = moon["alt_deg"]; az = moon["az_deg"]
    dir8 = az_to_compass(az)

    # How strong the glare will feel
    if illum_pct < 20:  glare = "minimal"
    elif illum_pct < 50: glare = "moderate"
    elif illum_pct < 80: glare = "strong"
    else:                glare = "very strong"

    # Where it is in the sky
    if alt < 0:
        pos = f"below the horizon toward {dir8}"
    elif alt < 15:
        pos = f"very low in the {dir8}"
    elif alt < 30:
        pos = f"low in the {dir8}"
    elif alt < 55:
        pos = f"mid-height in the {dir8}"
    else:
        pos = f"high in the {dir8}"

    sep_min = int(round(_recommended_sep_min(moon["illum"], moon["alt_deg"])))

    avoid_text = (
        "No need to avoid the Moon tonight." if sep_min == 0
        else f"Avoid DSOs within ~{sep_min}° of the Moon."
    )

    return (
        f"{illum_pct}% illuminated.<br>"
        f"At 9:00 PM it’s {pos} "
        f"({alt:.0f}° alt, az {az:.0f}°, {dir8}).<br>"
        f"Moon glare: <i>{glare}</i>. {avoid_text}"
    )