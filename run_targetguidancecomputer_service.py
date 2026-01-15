"""
Launcher for the Messier Target Guidance Computer Flask web service.

Run this script from the project root to start the Flask app.
Fetches DATABASE_URL from Google Secret Manager if available.
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly load .env from the subfolder
env_path = Path(__file__).resolve().parent / "services" / "target_guidance_computer" / ".env"
load_dotenv(dotenv_path=env_path)

# --- Set up project root and sys.path ---
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# --- Google Secret Manager integration ---
def set_database_url_from_gsm(secret_name: str, project_id: str):
    """
    Fetch the database URL from Google Secret Manager and set as environment variable.
    Must be called before importing any modules that use DATABASE_URL.
    """
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        db_bits = response.payload.data.decode("UTF-8").split(',')
        db_ip = os.environ.get("gcs-db-ip")
        db_name = os.environ.get("gcs-db-name")
        db_unix_socket = os.environ.get("gcs-db-unix-socket", "")
        db_ssl_ca = os.environ.get("gcs-ssl-ca", "")
        if db_unix_socket:
            db_url = f"mysql+pymysql://{db_bits[4]}:{db_bits[5]}@{db_ip}/{db_name}?unix_socket={db_unix_socket}"
        else:
            db_url = f"mysql+pymysql://{db_bits[4]}:{db_bits[5]}@{db_ip}:3306/{db_name}"
        os.environ["DATABASE_URL"] = db_url
        print("DATABASE_URL set from GSM.")
    except Exception as e:
        print(f"Warning: Could not set DATABASE_URL from GSM: {e}")

# --- Optionally set DATABASE_URL from GSM ---
# Only runs if env vars are present. This avoids startup failures in environments
# where Secret Manager isn't configured.
secret_name = os.environ.get("PROD_DATABASE_URL_SECRET_NAME") or os.environ.get("prod-database-url")
project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("your-gcp-project-id")
if secret_name and project_id:
    set_database_url_from_gsm(secret_name, project_id)

from services.target_guidance_computer.flask_target_guidance_service import app

# Pass project root to Flask app config for downstream use
app.config["PROJECT_ROOT"] = str(project_root)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)