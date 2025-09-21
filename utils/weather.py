"""
Weather helpers for the Target Guidance Computer.

Fetches Open-Meteo hourly data and returns:
- A concise HTML summary for the UI panel
- A normalized dict (wx) used by assessment logic

Best practice: call this with a timezone-aware `when` produced by
utils.time_helpers.when_9pm_local(lat, lon) so all downstream calculations
refer to the same observing time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Tuple
import requests

# WMO thunderstorm weather codes used by Open-Meteo (95 thunderstorm, 96/99 with hail)
_THUNDER_CODES = {95, 96, 99}
_REQ_TIMEOUT_SECS = 8


def _safe_hour_label(dt: datetime) -> str:
    """Return a friendly hour label like '9pm' from a datetime."""
    # %I gives 01-12; strip leading zero, lowercase AM/PM
    return dt.strftime("%I%p").lstrip("0").lower()


def get_night_weather(lat: float, lon: float, when: datetime) -> Tuple[str, Dict]:
    """
    Get weather conditions around the requested observing time.

    Returns:
        tuple[str, dict]: (html_summary, wx_dict)
            - html_summary: Short colored summary + details line.
            - wx_dict keys (all optional floats unless noted):
                cloud_pct, visibility_km, temp_c, dewpoint_c, rel_humidity_pct,
                dew_delta_c, dew_risk (str), heater_setting (str),
                precip_mm_per_hr, snow_mm_per_hr, wind_kph, gust_kph,
                thunder_prob (0/1), precip_prob_pct (percent), hour_iso (str)
    """
    # ---- helpers for dewpoint/RH conversions (Magnus/Tetens) ----
    import math

    def _dewpoint_from_t_rh(t_c: float, rh_pct: float) -> float:
        rh = max(1e-6, min(100.0, rh_pct)) / 100.0
        a, b = 17.62, 243.12
        gamma = math.log(rh) + (a * t_c) / (b + t_c)
        return (b * gamma) / (a - gamma)

    def _rh_from_t_td(t_c: float, td_c: float) -> float:
        a, b = 17.62, 243.12
        es = math.exp((a * t_c) / (b + t_c))
        e = math.exp((a * td_c) / (b + td_c))
        return max(0.0, min(100.0, 100.0 * (e / es)))

    # Open-Meteo returns local-time ISO strings if timezone=auto is used.
    target_date = when.date().isoformat()
    target_hour_iso = when.strftime("%Y-%m-%dT%H:00")

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "timeformat": "iso8601",
        "hourly": ",".join(
            [
                "cloudcover",
                "visibility",
                "temperature_2m",
                # humidity & dew point (field names can vary by version; we’ll try both below)
                "relativehumidity_2m", "relative_humidity_2m",
                "dewpoint_2m", "dew_point_2m",
                "precipitation",
                "rain",
                "snowfall",
                "precipitation_probability",
                "windspeed_10m",
                "windgusts_10m",
                "weathercode",
            ]
        ),
        "windspeed_unit": "kmh",
        "start_date": target_date,
        "end_date": target_date,
    }

    try:
        resp = requests.get(url, params=params, timeout=_REQ_TIMEOUT_SECS)
        resp.raise_for_status()
        data = resp.json()

        hours = data["hourly"]["time"]  # e.g. ["2025-09-17T20:00", ...]
        # Exact match to the requested hour first
        try:
            idx = hours.index(target_hour_iso)
        except ValueError:
            # Fallback: prefer 21:00 on that date; if absent, choose nearest hour
            fallback_iso = f"{target_date}T21:00"
            if fallback_iso in hours:
                idx = hours.index(fallback_iso)
            else:
                idx = min(
                    range(len(hours)),
                    key=lambda i: abs(
                        datetime.fromisoformat(hours[i]) - datetime.fromisoformat(target_hour_iso)
                    ),
                )

        def pick(key: str, default=None):
            arr = data["hourly"].get(key)
            if not arr:
                return default
            val = arr[idx]
            return default if val is None else val

        # Try multiple possible names for the same concept
        def pick_any(keys, default=None):
            for k in keys:
                val = pick(k, None)
                if val is not None:
                    return val
            return default

        cloud = float(pick("cloudcover", 0.0))                    # %
        vis_m = pick("visibility", None)                          # meters
        vis_km = (vis_m / 1000.0) if isinstance(vis_m, (int, float)) else None
        temp_c = float(pick("temperature_2m", 0.0))               # °C

        rh_val = pick_any(["relative_humidity_2m", "relativehumidity_2m"], None)
        rh_pct = float(rh_val) if isinstance(rh_val, (int, float)) else None

        dp_val = pick_any(["dew_point_2m", "dewpoint_2m"], None)
        dewpoint_c = float(dp_val) if isinstance(dp_val, (int, float)) else None

        precip = float(pick("precipitation", 0.0))                # mm/h (water equiv)
        rain = float(pick("rain", 0.0))                           # mm/h
        snowfall_cm = float(pick("snowfall", 0.0))                # cm/h
        snow_mm_equiv = snowfall_cm * 10.0                        # approx 10:1 ratio
        wind = float(pick("windspeed_10m", 0.0))                  # km/h
        gust = float(pick("windgusts_10m", wind))                 # km/h
        wcode = int(pick("weathercode", 0))
        ppop = pick("precipitation_probability", None)            # %

        thunder_prob = 1.0 if wcode in _THUNDER_CODES else 0.0

        # Fill missing humidity/dewpoint if we can
        if dewpoint_c is None and rh_pct is not None:
            dewpoint_c = _dewpoint_from_t_rh(temp_c, rh_pct)
        if rh_pct is None and dewpoint_c is not None:
            rh_pct = _rh_from_t_td(temp_c, dewpoint_c)

        # Dew risk assessment (ΔT = T − Td)
        dew_delta_c = None
        heater_setting = "off"
        dew_risk = "Low"
        if dewpoint_c is not None:
            dew_delta_c = temp_c - dewpoint_c
            # base thresholds
            if dew_delta_c > 5.0:
                heater_setting, dew_risk = "off", "Low"
            elif 3.0 < dew_delta_c <= 5.0:
                heater_setting, dew_risk = "low", "Moderate"
            elif 1.0 < dew_delta_c <= 3.0:
                heater_setting, dew_risk = "med", "High"
            else:  # <= 1°C
                heater_setting, dew_risk = "high", "Very High"
            # nudge with RH if available
            if rh_pct is not None:
                if rh_pct >= 95 and heater_setting != "high":
                    heater_setting, dew_risk = "high", "Very High"
                elif rh_pct >= 85 and heater_setting == "off":
                    heater_setting, dew_risk = "low", "Moderate"

        # Normalize for assessment
        wx = {
            "cloud_pct": cloud,
            "visibility_km": vis_km,
            "temp_c": temp_c,
            "dewpoint_c": dewpoint_c,
            "rel_humidity_pct": rh_pct,
            "dew_delta_c": dew_delta_c,
            "dew_risk": dew_risk,
            "heater_setting": heater_setting,
            "precip_mm_per_hr": precip,
            "snow_mm_per_hr": snow_mm_equiv,
            "wind_kph": wind,
            "gust_kph": gust,
            "thunder_prob": thunder_prob,
            "precip_prob_pct": ppop,
            "hour_iso": hours[idx],
        }

        # Human-readable summary
        def c_to_f(c: float) -> float:
            return c * 9/5 + 32
        def km_to_mi(km: float) -> float:
            return km * 0.621371
        def kph_to_mph(kph: float) -> float:
            return kph * 0.621371

        hour_label = _safe_hour_label(when)

        # Dual-unit strings
        vis_txt = "—" if vis_km is None else f"{vis_km:.1f} km ({km_to_mi(vis_km):.1f} mi)"
        temp_txt = f"{temp_c:.1f}°C ({c_to_f(temp_c):.1f}°F)"
        dp_txt   = "—" if dewpoint_c is None else f"{dewpoint_c:.1f}°C ({c_to_f(dewpoint_c):.1f}°F)"
        dd_txt   = "—" if dew_delta_c is None else f"{dew_delta_c:.1f}°C ({dew_delta_c*9/5:.1f}°F)"
        rh_txt   = "—" if rh_pct is None else f"{rh_pct:.0f}%"
        wind_txt = f"{wind:.0f} km/h ({kph_to_mph(wind):.0f} mph)"
        gust_txt = f"{gust:.0f} km/h ({kph_to_mph(gust):.0f} mph)"

        # Small CSS (inline so you don’t need external files)
        style = """
        <style>
        .wx-wrap{font-size:0.95em;margin-top:6px}
        .wx-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:8px}
        table.wx{width:100%;border-collapse:collapse;background:#222;border:1px solid #3a3a3a}
        table.wx th{padding:6px 8px;text-align:left;color:#fff;font-weight:600}
        table.wx td{padding:6px 8px;border-top:1px solid #3a3a3a;vertical-align:top}
        .wx-sub{color:#bbb;font-weight:400;margin-left:8px}
        /* section colors */
        .hdr-sky{background:#37474f}
        .hdr-wind{background:#263238}
        .hdr-precip{background:#2e3b2e}
        .hdr-dew{background:#5d3a00}
        .dew-accent{color:#ffb74d} /* orange */
        </style>
        """

        def table_html(title: str, hdr_class: str, rows: list[tuple[str,str]]) -> str:
            trs = "\n".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k,v in rows)
            return f"""
            <table class="wx">
            <thead><tr><th class="{hdr_class}">{title}</th><th class="{hdr_class} wx-sub">At {hour_label}</th></tr></thead>
            <tbody>{trs}</tbody>
            </table>
            """

        sky_tbl = table_html(
            "Sky & Visibility", "hdr-sky",
            [
            ("Cloud cover", f"{cloud:.0f}%"),
            ("Visibility",  vis_txt),
            ("Temperature", temp_txt),
            ],
        )

        wind_tbl = table_html(
            "Wind", "hdr-wind",
            [
            ("Wind", wind_txt),
            ("Gust", gust_txt),
            ],
        )

        precip_tbl = table_html(
            "Precipitation", "hdr-precip",
            [
            ("Precip (all)", f"{precip:.1f} mm/h"),
            ("Rain", f"{rain:.1f} mm/h"),
            ("Snow", f"{snowfall_cm:.1f} cm/h"),
            ],
        )

        dew_tbl = table_html(
            "Dew & Condensation", "hdr-dew",
            [
            ("Dew point", f"<span class='dew-accent'>{dp_txt}</span>"),
            ("Relative humidity", f"<span class='dew-accent'>{rh_txt}</span>"),
            ("ΔT (T−Td)", f"<span class='dew-accent'>{dd_txt}</span>"),
            ("Assessment", f"<span class='dew-accent'>Dew Risk: {dew_risk}<br><small><b>Dew Shields/Heaters: {heater_setting.upper()}</b></small></span>"),
            ],
        )

        tables = f"""{style}
        <div class="wx-wrap">
        <div class="wx-grid">{sky_tbl}{wind_tbl}{precip_tbl}{dew_tbl}</div>
        </div>
        """

        details = tables

        # Traffic-light lead (quick read; hard-stops are enforced elsewhere)
        if thunder_prob >= 0.3 or precip >= 0.1 or snowfall_cm >= 0.1:
            lead = '<font color="red"><b>Poor/Unsafe</b>: Wet or stormy conditions.</font>'
        elif cloud < 30 and (vis_km is None or vis_km > 10) and wind < 25:
            lead = '<font color="green"><b>Good Conditions</b>: Promising for stargazing.</font>'
        elif cloud > 80 or (vis_km is not None and vis_km < 5):
            lead = '<font color="red"><b>Poor Conditions</b>: Too cloudy or low visibility.</font>'
        else:
            lead = '<font color="orange"><b>Mixed Conditions</b>: Check the sky before observing.</font>'

        return f"{lead}<br>{details}", wx

    except Exception as e:
        print(f"Weather fetch error: {e}")
        return "Unable to fetch weather conditions.", {}