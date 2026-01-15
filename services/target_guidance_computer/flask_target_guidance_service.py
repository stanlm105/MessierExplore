"""
Flask web service for Messier Target Guidance Computer.

Handles user login, settings, Messier recommendations, and main display logic.
Integrates weather, moon phase, and Bortle scale for personalized observing guidance.
"""

# Standard library imports
import os
import json
from datetime import datetime, timezone
from pathlib import Path

# Third-party imports
from flask import Flask, request, jsonify, send_file, session, url_for
from sqlalchemy import select, func
from dotenv import load_dotenv

# Local application imports
from services.target_guidance_computer.db import SessionLocal, init_db
from services.target_guidance_computer.models import GeocodeCache, TgcAccount
from services.target_guidance_computer.auth import create_account_if_missing, verify_login
from services.target_guidance_computer.assessment import (
    target_assessment,
    coerce_seen_set,
    render_top_targets,
)
from services.target_guidance_computer.catalog_types import normalize_catalog_types
from utils.weather import get_night_weather
from utils.bortle import clearoutside_link
from utils.time_helpers import local_date_iso, when_9pm_local, zoneinfo_for_coords
from utils.geo import lookup_latlon
from utils.moon import get_moon_state, moon_recommend_targets, moon_narrative
from utils.jupiter_grs import grs_last_next_transits, jupiter_altaz_degrees, parse_iso8601_utc
from utils.validation import (
    sanitize_country,
    sanitize_passphrase,
    sanitize_room,
    sanitize_zipcode,
    sanitize_bortle_score,
    sanitize_seen_list,
)

# Load environment variables from .env file
load_dotenv()

# Flask app setup
app = Flask(__name__, static_folder=str(Path(__file__).resolve().parent.parent.parent / "static"))
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
app.config["JSON_SORT_KEYS"] = False

# Load Messier catalog at startup
project_root = Path(app.config.get("PROJECT_ROOT", Path(__file__).resolve().parent.parent.parent))
catalog_path = project_root / "data" / "messier_catalog.json"
with open(catalog_path, "r") as f:
    CATALOG = json.load(f)
    CATALOG = normalize_catalog_types(CATALOG)
   
# Initialize database
init_db()

# Moon phase display dictionary: phase index → (image filename, narrative HTML)
MOON_FACTORS = {
    -1: ("moon_unknown.png", "<b>Unknown Moon Phase:</b> No data available."),
    0: ("moon_new.png", "<b>New Moon:</b> Dark skies, ideal moon for deep-sky observing."),
    1: ("moon_waxing_crescent.png", "<b>Waxing Crescent:</b> Dark skies, ideal moon for deep-sky observing."),
    2: ("moon_first_quarter.png", "<b>First Quarter:</b> Some moonlight, good moon for early evening observing."),
    3: ("moon_waxing_gibbous.png", "<b>Waxing Gibbous:</b> Increasing moonlight, may affect faint object visibility."),
    4: ("moon_full.png", "<b>Full Moon:</b> Bright moonlight, not ideal moon for deep-sky observing."),
    5: ("moon_waning_gibbous.png", "<b>Waning Gibbous:</b> Decreasing moonlight, better for late evening observing."),
    6: ("moon_last_quarter.png", "<b>Last Quarter:</b> Some moonlight, good for late evening observing."),
    7: ("moon_waning_crescent.png", "<b>Waning Crescent:</b> Dark skies, ideal moon for deep-sky observing.")
}

# ISO 3166-1 alpha-2 country codes for login dropdown. Using a static list avoids
# an extra dependency or network call. Default selection is 'US'.
COUNTRY_CODES = [
    'US','CA','MX','GB','FR','DE','IT','ES','PT','NL','BE','CH','AT','DK','SE','NO','FI','IS','IE','AU','NZ','JP','KR','CN','TW','HK','SG','IN','ZA','BR','AR','CL','CO','PE','VE','UY','PY','BO','EC','GT','CR','PA','SV','HN','NI','DO','HT','CU','JM','BS','TT','BB','PR','IL','SA','AE','QA','KW','BH','OM','PK','BD','LK','NP','MM','TH','VN','MY','PH','ID','KH','LA','IR','IQ','SY','JO','LB','EG','MA','TN','DZ','LY','SD','ET','KE','UG','TZ','RW','BI','GH','CI','SN','ML','BF','NE','NG','CM','GA','CG','CD','GQ','SL','GM','LR','ZW','ZM','MW','MZ','AO','NA','BW','MG','MU','CV','KY','FO','GL','EE','LV','LT','PL','CZ','SK','HU','RO','BG','GR','TR','RU','UA','BY','GE','AM','AZ','KZ','UZ','TM','TJ','KG','AF','AL','BA','HR','ME','MK','RS','SI','MD','LU','MC','SM','VA','LI','PS'
]

def refresh_data_then_induce_display_update(acct):
    """
    Gather weather, moon, and Messier recommendations for the current account,
    then render the main display HTML.

    Args:
        acct (TgcAccount): The user account object.

    Returns:
        str: Rendered HTML for the main display.
    """
    # Increment run counter
    with SessionLocal() as db:
        db_acct = db.get(TgcAccount, acct.id)
        db_acct.run_counter = (db_acct.run_counter or 0) + 1
        db.commit()
        # Update the passed acct object to reflect the change
        acct.run_counter = db_acct.run_counter

# Clean up string 'None' values
    if acct.latitude == "None" or acct.latitude == "":
        acct.latitude = None
    if acct.longitude == "None" or acct.longitude == "": 
        acct.longitude = None
    
    # Convert to float with validation
    try:
        lat = float(acct.latitude) if acct.latitude else None
        lon = float(acct.longitude) if acct.longitude else None
    except (ValueError, TypeError):
        lat, lon = None, None
    
    # If coordinates are missing, try geocoding
    if lat is None or lon is None:
        if acct.country and acct.zipcode:
            lat, lon = lookup_latlon(acct.country, acct.zipcode)
            if lat is not None and lon is not None:
                # Update database with valid coordinates
                with SessionLocal() as db:
                    db_acct = db.get(TgcAccount, acct.id)
                    db_acct.latitude = str(lat)
                    db_acct.longitude = str(lon)
                    db.commit()
                    acct.latitude = str(lat)
                    acct.longitude = str(lon)
    
    # Use fallback if still no coordinates
    if lat is None or lon is None:
        lat, lon = 40.0, -74.0  # NYC fallback
    

    when_9pm = when_9pm_local(lat, lon)
    weather_html, wx = get_night_weather(lat, lon, when_9pm)
    seen_set = coerce_seen_set(acct.seen_list)

    reason_html, top5, moon = target_assessment(
        CATALOG, lat, lon,
        cloud_pct=wx.get("cloud_pct", 0.0),
        bortle_class=acct.bortle,
        seen_numbers=seen_set,
        top_n=15,
        min_alt=25.0,
        weather=wx,
        hard_kill_on_weather=True
    )

    bortleLink = clearoutside_link(acct.latitude, acct.longitude)
    phase_idx = moon["phase_idx"]
    icon, moon_html = MOON_FACTORS.get(phase_idx, MOON_FACTORS[-1])
    moon_html += "<br>" + moon_narrative(moon)
    top5_html = render_top_targets(top5)

    return render_main_display(acct, weather_html, reason_html, top5_html, bortleLink, phase_idx, moon_html)

def html_style() -> str:
    """
    Return the common HTML style block for the web pages.

    Returns:
        str: HTML style block.
    """
    return """
    <style>
            body {
                background: #111;
                color: #fff;
                font-family: Arial, sans-serif;
            }
            .night-sky-table {
                border-collapse: collapse;
                margin: 0 auto;
            }
            .night-sky-table th, .night-sky-table td {
                padding: 8px 12px;
                border: 1px solid #444;
            }
            .night-sky-title {
                background: black;
                color: white;
                text-align: center;
                font-weight: bold;
            }
            .night-sky-header {
                background: #c65d3b;
                color: white;
                text-align: center;
                font-weight: bold;
            }
            .night-sky-label {
                background: #111;
                color: #ff5555;
                font-weight: bold;
                font-size: 12px;
            }
            .night-sky-value {
                background: #222;
                color: white;
                vertical-align: middle;
            }
            input[type="text"], input[type="password"], textarea {
                background: #444;
                color: #fff;
                border: 1px solid #666;
            }

            .footer {
                text-align: center;
                margin-top: 80px;
                padding: 40px 0;
                border-top: 1px solid rgba(255, 255, 255, 0.2);
                color: #8fa0b3;
            }
            
            .footer a {
                color: #4ecdc4;
                text-decoration: none;
            }
            
            .footer a:hover {
                text-decoration: underline;
            }
        
            ul.targets { margin:0; padding-left:1.1rem; }
            ul.targets li { margin:6px 0 10px; line-height:1.25; }
            ul.targets small { color:#cdd; }
            ul.targets a { color:#8ecbff; text-decoration:none; }
            ul.targets a:hover { text-decoration:underline; }
        </style>
    """

def render_main_display(acct, weather_html, reason_html, top5_html, bortleLink, phase_idx, moon_html):
    """
    Render the main HTML display for the user's Messier observing session.

    Args:
        acct (TgcAccount): The user account object.
        weather_html (str): Weather summary HTML.
        reason_html (str): Reasoning/narrative HTML.
        top5_html (str): Top 5 Messier targets HTML.
        bortleLink (str): Link to Bortle score lookup.
        phase_idx (int): Moon phase index.
        moon_html (str): Moon phase narrative HTML.

    Returns:
        str: Rendered HTML.
    """
    logo_url = url_for('static', filename='logo_circle_isolated.png')
    logo_moon_url = url_for('static', filename=f"moonphases/{MOON_FACTORS.get(phase_idx, MOON_FACTORS[-1])[0]}")
    bortlechart_url = url_for('static', filename='bortlechart.png')
    bortlebadge_url = url_for('static', filename=f'bortle/bortle_unknown.png')
    logo_favicon = url_for('static', filename='favicon.ico')
    jupiter_grs_url = url_for('static', filename='jupiter_grs.png')
    if acct.bortle:
        bortlebadge_url = url_for('static', filename=f'bortle/bortle_B{str(acct.bortle)}_overlay_bigtext.png')

    # Side Quest: Jupiter Great Red Spot transit times (local)
    try:
        lat = float(acct.latitude) if acct.latitude else None
        lon = float(acct.longitude) if acct.longitude else None
        tz = zoneinfo_for_coords(lat, lon) if (lat is not None and lon is not None) else timezone.utc

        # Get current time in UTC for accurate last/next calculation
        now_utc = datetime.now(timezone.utc)
        grs = grs_last_next_transits(now_utc)
        last_utc = parse_iso8601_utc(grs["last_transit_utc"])
        next_utc = parse_iso8601_utc(grs["next_transit_utc"])

        # Convert to local time with timezone abbreviation
        last_local_dt = last_utc.astimezone(tz)
        next_local_dt = next_utc.astimezone(tz)
        tz_abbr = last_local_dt.strftime("%Z")
        
        last_local = last_local_dt.strftime("%m/%d/%Y @ %I:%M %p") + f" {tz_abbr}"
        next_local = next_local_dt.strftime("%m/%d/%Y @ %I:%M %p") + f" {tz_abbr}"

        alt_deg, az_deg = (
            jupiter_altaz_degrees(next_utc, lat, lon) if (lat is not None and lon is not None) else (None, None)
        )
        if alt_deg is None:
            altaz_html = "Jupiter Alt/Az at next transit: unknown"
        elif alt_deg < 0.0:
            altaz_html = "Jupiter Alt/Az at next transit: <i>below horizon</i>"
        else:
            altaz_html = f"Jupiter Alt/Az at next transit: {alt_deg:.0f}° / {az_deg:.0f}°"

        grs_html = f"Last GRS transit: {last_local}<br>Next GRS transit: {next_local}<br>{altaz_html}"
    except Exception:
        grs_html = "GRS transit info unavailable. (Check data/jupiter_grs.json.)"

    return f"""
        <html>
        {html_style()}
        <head>
            <title>Messier Target Guidance Computer</title>
            <link rel="icon" type="image/x-icon" href="{logo_favicon}">
        </head>
        <body>
        <center>
        <table border=0><tr><td valign=center>
            <a href="https://www.messierexplore.com"><img src="{logo_url}" alt="Logo" width="75" style="vertical-align: middle;"></a>
            </td><td valign=center>
                <big><font face=arial color=white>
                    &nbsp;&nbsp;&nbsp;&nbsp;Welcome to Room <font color=lime>{acct.room_name}</font>, <font color=lime>{acct.country}</font>, <font color=lime>{acct.zipcode}</font>
                </font></big>
        </td></tr></table>
        <table border=0><tr><td>
            <table class="night-sky-table">
            <tr>
                 <td colspan="2" class="night-sky-title">Your night sky:</td>
            </tr>
            <tr>
                <td class="night-sky-label">Location:</td>
                <td class="night-sky-value">Latitude: {acct.latitude}, Longitude: {acct.longitude}</td>
            </tr>
            <tr>
                <td class="night-sky-label">Tonight's Weather:</td>
                <td class="night-sky-value">{weather_html.replace(", Precip","<br>Precip")}</td>
            </tr>
            <tr>
                <td class="night-sky-label">Bortle Dark-Sky<br>Scale Score:</td>
                <td class="night-sky-value">
                    <table border=0 cellpadding=0 cellspacing=0><tr><td>
                        <center>
                        <img src="{bortlebadge_url}" alt="Bortle Badge" width="100" style="vertical-align: middle;">
                        <br><br>
                        (<a href="{bortleLink}" target="_blank"><font color=cyan>Click here</font></a> for score.<br>If you see, for example, 'Class 6 Bortle',<br>then enter 6 in the form below)
                        </center>
                    </td><td>
                        <img src="{bortlechart_url}" alt="Bortle Chart" width="400" style="vertical-align: middle;">
                    </td></tr></table>
                </td>
            </tr>
            <tr>
                <td class="night-sky-label">Moon Factor:</td>
                <td class="night-sky-value">
                    <table border=0><tr><td bgcolor=black><img src="{logo_moon_url}" alt="Moon Phase" width="100">
                     </td><td>{moon_html}</td></tr></table>
                </td>
            </tr><tr><td colspan=2 bgcolor=black height=10></td></tr>
            <tr>
                <td class="night-sky-label" valign=top>Tonight's Target<br>Guidance Computer<br>Reasoning:</td>
                <td class="night-sky-value">{reason_html}</td>
            </tr>
            <tr>
                <td class="night-sky-label" valign=top>Top 15 Recommended<br>Targets:</td>
                <td class="night-sky-value">
                    {top5_html}
                </td>
            </tr>
            <tr>
                <td class="night-sky-label" valign=top>Side Quest:<br>Jupiter Great Red Spot</td>
                <td class="night-sky-value">
                    <table border=0><tr><td bgcolor=black><img src="{jupiter_grs_url}" alt="Jupiter GRS" width="100">
                     </td><td>{grs_html}</td></tr></table>
                </td>
            </tr>
            </table>
            <br>
            <form method="post" action="/settings">
                <table class="night-sky-table">
                    <tr>
                        <td colspan="2" class="night-sky-header">Updateable parameters:</td>
                    </tr>
                    <tr>
                        <td class="night-sky-label">Zipcode:</td>
                        <td class="night-sky-value"><input type="text" name="zipcode" maxlength="10" value="{acct.zipcode}"></td>
                    </tr>
                    <tr>
                        <td class="night-sky-label">Bortle Dark-Sky Scale Score:</td>
                        <td class="night-sky-value"><input type="text" name="bortle_score" maxlength="1" size="2" value="{acct.bortle or ''}"></td>
                    </tr>
                    <tr>
                        <td class="night-sky-label">Seen M# List<br>(enter a comma-separated list,<br>just the numbers, no 'M',<br>so if you already did M1 and M5,<br>just enter 1,5):</td>
                        <td class="night-sky-value">
                            <textarea name="seen_list" rows="3" cols="40" maxlength="500">{acct.seen_list or ''}</textarea>
                        </td>
                    </tr>
                    <tr>
                        <td colspan="2" class="night-sky-header" align="center"><button type="submit">Update</button></td>
                    </tr>
                </table>
            </form>
            <font color=white size=2>
            <p>Note: No personal data is stored. One can share their room/key with others to share tracking.<br>
            Country/Zipcode are used to approximate a location which drives all the information displayed.</p>
            </font>
            <br>
            <form method="get" action="/">
                <button type="submit">Logout</button>
            </form>
        </td></tr></table>
        </center>
        </body>
        </html>
    """

@app.get("/api/health")
def health():
    """
    Health check endpoint for service status.
    """
    return {"ok": True, "db": True, "catalog_items": len(CATALOG)}

@app.get("/api/catalog")
def catalog():
    """
    API endpoint to return the Messier catalog.
    """
    return jsonify(CATALOG)


@app.get("/api/jupiter/grs")
def jupiter_grs():
    """Return last/next Great Red Spot transit times (UTC).

    This endpoint does not scrape any third-party sites. It computes transits from
    a reference transit timestamp + System II rotation period.

    Query params:
        when: optional ISO8601 datetime (e.g. 2026-01-13T03:41:00Z). If omitted, uses now.

    Configuration:
        - JUPITER_GRS_REFERENCE_TRANSIT_UTC (or JUPITER_GRS_SOURCE_URL)
        - JUPITER_SYSTEM2_ROTATION_PERIOD_SECONDS (optional)
    """

    when_raw = (request.args.get("when") or "").strip()
    try:
        when_dt = parse_iso8601_utc(when_raw) if when_raw else None
        return jsonify(grs_last_next_transits(when_dt))
    except Exception as e:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(e),
                    "howto": "Set JUPITER_GRS_REFERENCE_TRANSIT_UTC / JUPITER_GRS_SOURCE_FILE / JUPITER_GRS_SOURCE_URL",
                }
            ),
            503,
        )

@app.route("/settings", methods=["POST"])
def update_settings():
    """
    Handle user settings update (zipcode, Bortle score, seen list).
    Refreshes location and updates account.
    """
    acct_id = session.get("acct_id")
    if not acct_id:
        return "<h3>Error: Session expired. Please log in again.</h3>", 400
    zipcode = ''.join(filter(str.isdigit, request.form.get("zipcode", "")))
    bortle_score = ''.join(filter(str.isdigit, request.form.get("bortle_score", "")))
    seen_list = cleanAndSortFreeTextNumberList(request.form.get("seen_list", ""))

    zipcode = sanitize_zipcode(zipcode)
    bortle_score = sanitize_bortle_score(bortle_score)
    seen_list = sanitize_seen_list(seen_list)

    with SessionLocal() as db:
        acct = db.get(TgcAccount, acct_id)
        acct.zipcode = zipcode
        acct.bortle = bortle_score
        acct.seen_list = seen_list
        
        # Try to refresh coordinates
        if zipcode and acct.country:
            lat, lon = lookup_latlon(acct.country, zipcode)
            if lat is not None and lon is not None:
                acct.latitude = str(lat)
                acct.longitude = str(lon)
                db.commit()
                return refresh_data_then_induce_display_update(acct)
            else:
                # Geocoding failed - save other settings but show error
                db.commit()
                error_html = f"""
                <h3 style="color: orange;">Settings Updated (Partial)</h3>
                <p>Your Bortle class and seen list were saved, but either we couldn't find coordinates for {acct.country}, {zipcode}; or we hit a temporary access limit with the source API.</p>
                <p>Please check your zipcode or try again later.</p>
                <p><a href="/">← Back to main page</a></p>
                """
                return error_html
        else:
            # No zipcode or country - save what we can
            db.commit()
            return refresh_data_then_induce_display_update(acct)

def cleanAndSortFreeTextNumberList(free_text):
    """
    Clean and sort a comma-separated list of Messier numbers from user input.

    Args:
        free_text (str): Comma-separated Messier numbers.

    Returns:
        str: Sorted, comma-separated Messier numbers.
    """
    numbers = set()
    for part in free_text.split(","):
        part = part.strip()
        if part.isdigit():
            numbers.add(int(part))
    return ",".join(str(num) for num in sorted(numbers))

@app.route("/", methods=["GET", "POST"])
def index():
    """
    Main entry point for login and display.
    GET: Show login form.
    POST: Handle login, create account if needed, and show main display.
    """
    if request.method == "POST":
        room = request.form.get("room_name", "").strip().upper()
        country = request.form.get("country", "").strip().upper()
        zipcode = request.form.get("zipcode", "").strip().upper()
        passphrase = request.form.get("passphrase", "").strip()

        room = sanitize_room(room)
        country = sanitize_country(country)
        zipcode = sanitize_zipcode(zipcode)
        passphrase = sanitize_passphrase(passphrase)

        if not all([room, country, zipcode, passphrase]):
            return "<h3>Error: All fields are required.</h3>", 400

        with SessionLocal() as db:
            acct_id = verify_login(db, room, country, zipcode, passphrase)
            if acct_id is None:
                try:
                    acct_id = create_account_if_missing(db, room, country, zipcode, passphrase)
                except ValueError as e:
                    return f"<h3>{str(e)}</h3>", 400
            acct = db.get(TgcAccount, acct_id)
            session["acct_id"] = acct.id
            return refresh_data_then_induce_display_update(acct)

    # GET: show login form
    logo_url = url_for('static', filename='logo_main_2_nobg.png')
    sample_url = url_for('static', filename='tcg_sample.png')
    logo_favicon = url_for('static', filename='favicon.ico')
    # Alphabetize the country codes for easier scanning; default select US.
    country_options = ''.join(
        f'<option value="{c}" {"selected" if c=="US" else ""}>{c}</option>'
        for c in sorted(COUNTRY_CODES)
    )
    # Isolated JS (not an f-string) to avoid brace interpolation issues.
    persistence_script = """
    <script>
    (function() {
        var FIELD_NAMES = ['room_name','country','zipcode','passphrase'];
        function storageAvailable() {
            try { var x='__tgc_test'; localStorage.setItem(x,x); localStorage.removeItem(x); return true; } catch(e){ return false; }
        }
        function loadFields() {
            if(!storageAvailable()) return;
            var form = document.querySelector('form[method="post"]');
            if(!form) return;
            FIELD_NAMES.forEach(function(name){
                var el = form.querySelector('[name="'+name+'"]');
                if(!el) return;
                var stored = localStorage.getItem('tgc_'+name);
                if(stored) { el.value = stored; }
            });
        }
        window.persistLoginFields = function persistLoginFields() {
            if(!storageAvailable()) return true;
            var form = document.querySelector('form[method="post"]');
            if(!form) return true;
            FIELD_NAMES.forEach(function(name){
                var el = form.querySelector('[name="'+name+'"]');
                if(!el) return;
                localStorage.setItem('tgc_'+name, (el.value||'').trim());
            });
            return true;
        };
        document.addEventListener('DOMContentLoaded', loadFields);
    })();
    </script>
    """
    # Mobile-friendly styles scoped to the login page
    login_style = """
        <style>
            .login-card { max-width: 560px; width: calc(100% - 24px); margin: 0 auto; padding: 12px; }
            .login-card img.logo { max-width: 100%; height: auto; }
            .night-sky-table { width: 100%; }
            .night-sky-table td { padding: 10px; }
            .night-sky-table input[type="text"], .night-sky-table select { width: 100%; box-sizing: border-box; font-size: 16px; padding: 10px; }
            .night-sky-table button { width: 100%; padding: 12px; font-size: 18px; }
            @media (max-width: 520px) {
                .night-sky-table tr { display: block; margin-bottom: 8px; }
                .night-sky-table td { display: block; width: 100%; }
                .night-sky-label { text-align: left; padding-top: 8px; }
            }
        </style>
        """
    return f"""
    <html>
    {html_style()}
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Messier Target Guidance Computer</title>
        <link rel="icon" type="image/x-icon" href="{logo_favicon}">
        {login_style}
    </head>
    <body>
    <center>
    <div class="login-card">
    <a href="https://github.com/stanlm105/MessierExplore"><img class="logo" src="{logo_url}" alt="Logo" width="400"></a><br>
    <font face=arial color=white><h2>Messier Target Guidance Computer</h2></font>
    <form method="post" onsubmit="return persistLoginFields();">
        <table class="night-sky-table"><tr>
    <td class="night-sky-label">Room name:</td><td><input type="text" name="room_name" maxlength="25" value="guest" required></td></tr>
    <tr><td class="night-sky-label">Country:</td><td><select name="country" required>{country_options}</select></td></tr>
    <tr><td class="night-sky-label">Zip code:</td><td><input type="text" name="zipcode" maxlength="10" value="90210" required></td></tr>
    <tr><td class="night-sky-label">Enter any room key:</td><td><input type="text" name="passphrase" maxlength="50" value="guest" required></td></tr>
            <tr><td colspan=2 align=center><br><button type="submit">Enter!</button><br></td></tr>
        </table>
    </form>
    <footer class="footer">
        <p>
            <b><font color=lime>No registration needed, hit Enter to demo! Or use your own room name/key to personalize.</font></b><br><br>
            Free to use • No ads • No tracking • Open source<br>
            Created with ❤️ for the astronomy community<br>
            <a href="https://github.com/stanlm105/MessierExplore">View on GitHub</a> | 
            <a href="mailto:stanlm@messierexplore.com">Contact</a> | 21 Sep 2025
        </p>
    </footer>
    {persistence_script}
        </div>
    </center>
    </body>
    </html>
    """

@app.route('/favicon.ico')
def favicon():
    """
    Serve the favicon.ico from the static folder.
    """
    return send_file(app.static_folder + '/favicon.ico')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
