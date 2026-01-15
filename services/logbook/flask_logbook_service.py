"""
Flask web service for generating personalized Messier Log Book PDFs.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import Flask, redirect, request, send_file, render_template_string, Response, abort, url_for
from pathlib import Path
from io import BytesIO
from pypdf import PdfReader, PdfWriter
from utils.pdf_helpers import build_overlay, flatten_forms
from utils.validation import validate_name
import os

# Set up project root and static folder
project_root = Path(__file__).resolve().parent.parent.parent
app = Flask(__name__, static_folder=str(project_root / "static"))

# Path to the template PDF for logbook generation
TEMPLATE_PDF = project_root / "assets" / "templates" / "messier_logbook_template_final-6.pdf"

@app.before_request
def enforce_non_www():
    """
    Redirect requests from www. to non-www. domain for canonical URLs.
    """
    host = request.headers.get("Host", "")
    if host.startswith("www."):
        url = request.url.replace("://www.", "://", 1)
        return redirect(url, code=301)

# Initialize Flask-Limiter for rate limiting
limiter = Limiter(
    get_remote_address,
    app=app
)

@app.route("/generate", methods=["POST"])
@limiter.limit("20 per hour")
def generate() -> Response:
    """
    Handle form submission, validate the name, generate the personalized PDF,
    and return it as a downloadable file.

    Returns:
        Response: Flask response with the generated PDF or error message.
    """
    lang = request.args.get("lang", "en")
    name = request.form.get("name", "Anonymous")
    try:
        name = validate_name(name)
    except ValueError as e:
        # Return a simple HTML error message, localized
        msg = f"Invalid name: {e}" if lang == "en" else f"無効な名前: {e}"
        return render_template_string(
            f"<h3>{msg}</h3><a href='/?lang={lang}'>Try again</a>"
        ), 400

    # Read the template PDF and personalize the cover page
    reader = PdfReader(str(TEMPLATE_PDF))
    writer = PdfWriter()
    p0 = reader.pages[0]
    overlay = build_overlay(float(p0.mediabox.width), float(p0.mediabox.height), name)
    p0.merge_page(overlay)
    writer.add_page(p0)
    # Add remaining pages
    for i in range(1, len(reader.pages)):
        writer.add_page(reader.pages[i])
    # Flatten form fields for compatibility
    flatten_forms(writer)
    buf = BytesIO()
    writer.write(buf)
    buf.seek(0)
    # Send the personalized PDF as a download
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"MessierLogBook_{name}.pdf"
    )

@app.route("/", methods=["GET"])
def index() -> str:
    """
    Render the HTML form for user name input, with language switch.

    Returns:
        str: Rendered HTML form.
    """
    lang = request.args.get("lang", "en")
    # Localized text for English and Japanese
    if lang == "ja":
        texts = {
            "title": "メシエログブックジェネレーター",
            "name_label": (
                "以下のPDFは、夜空に浮かぶ110個のメシエ天体の観測記録を印刷できるログブックです。<br><br>"
                "ログブックの表紙にあなたの名前を入力してください（これは単なる楽しみとインスピレーションのためのもので、名前の記録はありません）。"
                "もしこれがあなたの旅の助けになったと感じたら、stanlm@messierexplore.comまでメッセージをお送りください。ありがとうございます！<br><br>"
                "お名前またはニックネームを入力してください："
            ),
            "button": "PDFをダウンロード",
            "language": "言語",
            "english": "英語",
            "japanese": "日本語"
        }
    else:
        texts = {
            "title": "Messier Log Book Generator",
            "name_label": (
                "The pdf below will be a printable log book for tracking observations of the 110 Messier objects in the night sky.<br><br>"
                "Enter your name for personalization of the logbook cover (this is just for fun and inspiration, there is no log saved here of any names). "
                "If you find this helps your journey, please send a little hello to stanlm@messierexplore.com, thanks!<br><br>"
                "Please enter your name or nickname:"
            ),
            "button": "Download PDF",
            "language": "Language",
            "english": "English",
            "japanese": "Japanese"
        }
    # Render the HTML form with logo, sample image, and language switch
    return render_template_string(f"""
    <!DOCTYPE html>
    <html lang="{lang}">
    <head>
        <meta charset="UTF-8">
        <title>{texts['title']}</title>
        <link rel="icon" type="image/x-icon" href="{{{{ url_for('static', filename='favicon.ico') }}}}">
    </head>
    <body>
    <center>
    <font face="Arial, Helvetica, sans-serif">
    <form method="post" action="/generate?lang={lang}">
        <a href="https://github.com/stanlm105/MessierExplore">
            <img src="{{{{ url_for('static', filename='logo_main_2_nobg.png') }}}}" alt="Logo" width="400">
        </a><br><br>
        <h2>{texts['title']}</h2>
        <table border=0>
            <tr>
                <td width=600 align=center>
                    <label for="name">{texts['name_label']}</label>
                </td>
            </tr>
        </table><br>
        <table border=0 cellpadding=0 cellspacing=0>
            <tr>
                <td>
                    <input type="text" id="name" name="name" value="John Doe" required>
                </td>
                <td width=10></td>
                <td>
                    <button type="submit">{texts['button']}</button>
                </td>
            </tr>
        </table><br>
        <label for="lang">{texts['language']}:</label>
        <select id="lang" name="lang" onchange="window.location='/?lang='+this.value;">
            <option value="en" {'selected' if lang == 'en' else ''}>{texts['english']}</option>
            <option value="ja" {'selected' if lang == 'ja' else ''}>{texts['japanese']}</option>
        </select>
    </form>
    <br><br>
    <img src="{{{{ url_for('static', filename='logbook_sample.png') }}}}" alt="Sample" width="600">
    </font>
    </center>
    </body>
    </html>
    """)

@app.route('/favicon.ico')
def favicon():
    """
    Serve the favicon.ico from the static folder.
    """
    return send_file(app.static_folder + '/favicon.ico')