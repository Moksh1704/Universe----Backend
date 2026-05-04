"""
UniVerse – University Management Platform
FastAPI Backend  |  REST API  |  JWT Auth  |  PostgreSQL
"""
import re
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from pathlib import Path

from app.config import settings
from app.database import engine, Base

# ── Startup validation ────────────────────────────────────────────────────────
_BCRYPT_RE = re.compile(r"^\$2[abxy]\$\d{2}\$.{53}$")


def _validate_plain_password(name: str, value: str) -> None:
    """Abort at boot if a password env var looks like a bcrypt hash or is too long."""
    if not value:
        print(f"[STARTUP WARNING] {name} is empty — default password will be blank!", flush=True)
        return
    if _BCRYPT_RE.match(value):
        print(
            f"[STARTUP FATAL] {name} is a bcrypt hash, not a plain password.\n"
            f"  → Go to Render › Environment and set {name} to a plain string like 'Uni123'.\n"
            f"  → Never store pre-hashed values here; hashing is done at runtime.",
            flush=True,
        )
        sys.exit(1)
    byte_len = len(value.encode("utf-8"))
    if byte_len > 72:
        print(
            f"[STARTUP FATAL] {name} is {byte_len} bytes — exceeds bcrypt's 72-byte hard limit.\n"
            f"  → Shorten the value in Render › Environment to ≤ 72 bytes.",
            flush=True,
        )
        sys.exit(1)
    print(f"[STARTUP OK] {name} looks valid ({byte_len} bytes)", flush=True)


_validate_plain_password("DEFAULT_PASSWORD", settings.DEFAULT_PASSWORD)
_validate_plain_password("FACULTY_DEFAULT_PASSWORD", settings.FACULTY_DEFAULT_PASSWORD)

# ── Import all models so SQLAlchemy can create tables ────────────────────────
import app.models  # noqa: F401

# ── Import routers ────────────────────────────────────────────────────────────
from app.routers import (
    auth, users, announcements, events,
    posts, timetable, notifications,
)
from app.routers import faculty_timetable
from app.routers import attendance_v2
from app.routers.students import router as students_router

# ── URL helper ────────────────────────────────────────────────────────────────
def build_file_url(path: str) -> str:
    """Convert a relative upload path to a full absolute URL.

    Handles both already-absolute URLs (passthrough) and relative paths.
    Example:
        "/uploads/avatars/foo.jpg"
        → "https://universe-mainbackend.onrender.com/uploads/avatars/foo.jpg"
    """
    if not path:
        return path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = settings.BASE_URL.rstrip("/")
    return f"{base}{path}"


# ── Ensure upload directories exist ──────────────────────────────────────────
def _ensure_upload_dirs() -> None:
    """Create uploads/ and uploads/avatars/ if they don't exist."""
    for subdir in ("", "avatars"):
        Path(settings.UPLOAD_DIR, subdir).mkdir(parents=True, exist_ok=True)
    print(f"[STARTUP OK] Upload directories verified under '{settings.UPLOAD_DIR}'.", flush=True)


_ensure_upload_dirs()

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        print("[STARTUP OK] Database tables verified.", flush=True)
    except Exception as e:
        print(
            f"[STARTUP FATAL] Could not create/verify DB tables: {e}\n"
            f"  → Check that STUDENT_DATABASE_URL is set correctly in Render › Environment.",
            flush=True,
        )
        sys.exit(1)
    yield


# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="UniVerse API",
    lifespan=lifespan,
    description="""
## 🎓 UniVerse – University Management System

A production-ready backend for managing university operations including:
- **Authentication** – JWT-based login, registration, token refresh
- **Users** – Student & Faculty profiles with role-based access
- **Announcements** – Exam, holiday, result, and general notices
- **Events** – Campus events with registration management
- **Attendance** – Subject-wise and day-wise attendance tracking (v2 only)
- **Social Feed** – Posts, likes, and comments
- **Timetable** – Class schedules for students and faculty
- **Placements** – Job listings and student applications
- **Notifications** – In-app notification system

### Authentication
All endpoints (except `/auth/register` and `/auth/login`) require a Bearer token.
Get your token from `POST /auth/login`, then use `Authorization: Bearer <token>`.

### Attendance (v2 — all attendance traffic)
- `POST /attendance/mark`              — Faculty/Admin: mark attendance for a session
- `GET  /attendance/faculty/students`  — Faculty/Admin: get student list for a section
- `GET  /attendance/faculty/schedule`  — Faculty/Admin: get timetable/schedule slots
- `GET  /attendance/check`             — Faculty/Admin: check if attendance already marked
- `GET  /attendance/student/{id}`      — Student (own) or Faculty/Admin (any)
- `GET  /attendance/me`                — Student: subject-wise attendance
- `GET  /attendance/me/overview`       — Student: full attendance overview
- `GET  /attendance/me/daily`          — Student: day-wise records
- `GET  /attendance/me/summary`        — Student: summary with overall percentage
    """,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static file serving for uploads ──────────────────────────────────────────
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# ── Global exception handlers ─────────────────────────────────────────────────
@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "A record with this data already exists.", "success": False},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if settings.DEBUG:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "trace": traceback.format_exc(), "success": False},
        )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred.", "success": False},
    )


# ── Register Routers ──────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(announcements.router)
app.include_router(events.router)

# ⚠️  attendance.router (v1) is INTENTIONALLY NOT registered here.
#     All attendance traffic is handled exclusively by attendance_v2.router.
app.include_router(attendance_v2.router)

app.include_router(posts.router)
app.include_router(timetable.router)
app.include_router(faculty_timetable.router)
app.include_router(students_router)
# app.include_router(jobs.router)  # ← Placements hidden from Swagger; files untouched
app.include_router(notifications.router)

# ── Root & Health ─────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/health", tags=["Health"])
def health_check():
    from app.database import SessionLocal
    try:
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "version": settings.APP_VERSION,
    }