from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # =========================
    # DATABASE
    # =========================
    # Student app DB (main mutable data: users, posts, events, etc.)
    STUDENT_DATABASE_URL: str = ""

    # Master student records DB (read-only lookups for student validation)
    MASTER_DATABASE_URL: str = ""

    # Faculty DB (read-only for faculty identity lookups)
    FACULTY_DATABASE_URL: str = ""

    # Legacy alias so any existing code using MAIN_DATABASE_URL still works
    @property
    def MAIN_DATABASE_URL(self) -> str:
        return self.STUDENT_DATABASE_URL

    # =========================
    # AUTH
    # =========================
    SECRET_KEY: str = "change-this-secret-key-in-production-min-32-chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Default password for student first-time login.
    # ⚠️  Set this to a plain-text string in Render env vars — NOT a bcrypt hash.
    # pydantic-settings reads the env var automatically; os.getenv() is not needed.
    DEFAULT_PASSWORD: str = "Uni123"

    # Default password for faculty (no password column in faculty DB).
    # ⚠️  Same rule — plain text only.
    FACULTY_DEFAULT_PASSWORD: str = "faculty@123"

    # OTP expiry
    OTP_EXPIRY_MINUTES: int = 5

    # Google OAuth
    GOOGLE_CLIENT_ID: str | None = None

    # =========================
    # SMTP EMAIL CONFIG
    # =========================
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""

    # =========================
    # APP INFO
    # =========================
    APP_NAME: str = "UniVerse"
    APP_VERSION: str = "1.0.0"
    # Default False so production never leaks stack traces.
    # Set DEBUG=true in your local .env only.
    DEBUG: bool = False

    # =========================
    # CORS
    # =========================
    # Comma-separated list of allowed origins.
    # Example for Render: ALLOWED_ORIGINS=https://your-app.com,https://www.your-app.com
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",")]

    # =========================
    # FILE UPLOADS
    # =========================
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE_MB: int = 5

    # =========================
    # CONFIG
    # =========================
    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()