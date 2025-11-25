"""
Authentication and account management utilities for Messier Target Guidance Computer.
Handles account creation, password hashing, and login verification.
"""

from sqlalchemy import select
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash
from services.target_guidance_computer.models import TgcAccount
from utils.geo import lookup_latlon

def create_account_if_missing(db, room_name: str, country: str, zipcode: str, plain_pass: str) -> int:
    """
    Create a new account if one does not already exist for the given room, country, and zipcode.
    Hashes the password and looks up latitude/longitude from country/zipcode.

    Args:
        db: SQLAlchemy session object.
        room_name (str): Room name identifier.
        country (str): Country code.
        zipcode (str): Zip code.
        plain_pass (str): Plaintext password.

    Returns:
        int: The account ID of the created or existing account.

    Raises:
        ValueError: If location lookup fails.
    """
    acct = db.execute(
        select(TgcAccount).where(
            TgcAccount.room_name==room_name,
            TgcAccount.country==country
        )
    ).scalar_one_or_none()
    if acct:
        return acct.id
    lat, lon = lookup_latlon(country, zipcode)
    if lat is None or lon is None:
        raise ValueError(
            "Could not determine location from provided country/zip value, please try again? "
            "Email stanlm@gmail.com if we need to add option for direct lat/lon entry."
        )
    acct = TgcAccount(
        room_name=room_name, country=country, zipcode=zipcode,
        passphrase=generate_password_hash(plain_pass),
        latitude=str(lat),
        longitude=str(lon)
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)
    return acct.id

def verify_login(db, room_name: str, country: str, zipcode: str, plain_pass: str) -> int | None:
    """
    Verify login credentials for an account. Checks password hash and updates last login time.

    Args:
        db: SQLAlchemy session object.
        room_name (str): Room name identifier.
        country (str): Country code.
        zipcode (str): Zip code.
        plain_pass (str): Plaintext password.

    Returns:
        int | None: The account ID if login is successful, otherwise None.
    """
    acct = db.execute(
        select(TgcAccount).where(
            TgcAccount.room_name==room_name,
            TgcAccount.country==country
        )
    ).scalar_one_or_none()
    if acct and check_password_hash(acct.passphrase, plain_pass):
        # If country or zipcode differ from stored values, update and re-geocode.
        updated_location = False
        new_country = country
        new_zip = zipcode
        if acct.country != country or (zipcode and acct.zipcode != zipcode):
            lat, lon = lookup_latlon(country, zipcode)
            if lat is not None and lon is not None:
                acct.country = new_country
                acct.zipcode = new_zip
                acct.latitude = str(lat)
                acct.longitude = str(lon)
                updated_location = True
        acct.last_login_at = func.now()
        db.commit()
        return acct.id
    return None