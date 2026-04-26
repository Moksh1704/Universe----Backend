import os
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # =========================
    # DATABASE
    # =========================
    # Student app DB (main mutable data: users, posts, events, etc.)
    STUDENT_DATABASE_URL: str = os.getenv("STUDENT_DATABASE_URL", "")

    # Master student records DB (read-only lookups for student validation)
    MASTER_DATABASE_URL: str = os.getenv("MASTER_DATABASE_URL", "")

    # Faculty DB (new – read-only for faculty identity lookups)
    FACULTY_DATABASE_URL: str = os.getenv("FACULTY_DATABASE_URL", "")

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

    # Default password for student first-time login
    DEFAULT_PASSWORD: str = os.getenv("DEFAULT_PASSWORD", "Uni123")

    # Default password for faculty (no password column in faculty DB)
    FACULTY_DEFAULT_PASSWORD: str = os.getenv("FACULTY_DEFAULT_PASSWORD", "faculty@123")

    # OTP expiry
    OTP_EXPIRY_MINUTES: int = int(os.getenv("OTP_EXPIRY_MINUTES", "5"))

    # Google OAuth
    GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_CLIENT_ID")

    # =========================
    # SMTP EMAIL CONFIG
    # =========================
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "nmoksha.17@gmail.com")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "yiks lhga sdza vtwo")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "nmoksha.17@gmail.com")

    # =========================
    # APP INFO
    # =========================
    APP_NAME: str = "UniVerse"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # =========================
    # CORS
    # =========================
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