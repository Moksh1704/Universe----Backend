from datetime import datetime, timedelta
from typing import Optional
import smtplib
import re
from email.mime.text import MIMEText
import secrets

from jose import JWTError, jwt
from passlib.context import CryptContext
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Matches a valid bcrypt hash — exactly 60 characters, correct prefix and structure.
# Used in verify_password to catch malformed/truncated hashes before passlib
# throws a misleading "password cannot be longer than 72 bytes" error.
_BCRYPT_RE = re.compile(r"^\$2[abxy]\$\d{2}\$.{53}$")


def hash_password(password: str) -> str:
    # Encode to bytes FIRST, then slice to 72 bytes.
    #
    # The original code did password[:72] which slices on characters, not bytes.
    # Multi-byte Unicode characters (e.g. accented letters, emoji) each occupy
    # 2–4 bytes, so a string that is ≤72 chars can still exceed 72 bytes and
    # cause passlib to raise "password cannot be longer than 72 bytes".
    #
    # Encoding first and slicing the byte array guarantees we never exceed
    # bcrypt's hard limit regardless of what characters the password contains.
    return pwd_context.hash(password.encode("utf-8")[:72])


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Securely compare a plain password against a bcrypt hash.

    Before calling passlib, validates that the stored hash is structurally
    correct (60 chars, valid bcrypt prefix). If the hash is malformed or
    truncated, passlib raises a misleading "password cannot be longer than
    72 bytes" error instead of a clear one. This guard catches that case,
    logs a diagnostic line to Render logs, and returns False (clean 401)
    instead of crashing with a 500.
    """
    if not hashed_password:
        return False

    # ── Hash integrity check ──────────────────────────────────────────────────
    if not _BCRYPT_RE.match(hashed_password):
        print(
            f"[AUTH ERROR] Malformed bcrypt hash detected in DB.\n"
            f"  → hash length : {len(hashed_password)} (expected 60)\n"
            f"  → hash prefix : {hashed_password[:7]!r}\n"
            f"  → Action      : This user's hashed_password column is corrupted or\n"
            f"                  truncated. Reset their password manually via the\n"
            f"                  recovery script or ask them to use Forgot Password.",
            flush=True,
        )
        return False  # returns clean 401 to client — no 500, no stack trace
    # ─────────────────────────────────────────────────────────────────────────

    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


def generate_otp() -> str:
    """Generate a 6-digit numeric OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def send_email(to_email: str, subject: str, body: str) -> None:
    """
    Send an email using SMTP settings if configured.
    Falls back to printing to console in development.
    """
    if (
        not settings.SMTP_HOST
        or not settings.SMTP_USERNAME
        or not settings.SMTP_PASSWORD
        or not settings.SMTP_FROM_EMAIL
    ):
        # Mock email sending – useful for local development and tests.
        print(f"[MOCK EMAIL] To: {to_email}, Subject: {subject}, Body: {body}")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(msg)


def send_otp_email(email: str, otp: str, purpose: str = "login") -> None:
    """Helper to send OTP emails with a consistent template."""
    subject = f"Your UniVerse OTP for {purpose}"
    body = (
        f"Your One-Time Password (OTP) is: {otp}\n\n"
        f"This code will expire in {settings.OTP_EXPIRY_MINUTES} minutes.\n"
        "If you did not request this, you can ignore this email."
    )
    send_email(email, subject, body)


def verify_google_id_token(id_token_str: str) -> Optional[dict]:
    """
    Verify a Google ID token and return its payload.
    The token is validated against Google's public keys and the configured client ID.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID is not configured")

    try:
        request = google_requests.Request()
        payload = id_token.verify_oauth2_token(
            id_token_str,
            request,
            audience=settings.GOOGLE_CLIENT_ID,
        )
        return payload
    except Exception:
        return None