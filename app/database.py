from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import sys

from app.config import settings


# Shared SQLAlchemy base for ORM models
Base = declarative_base()

# ── Guard: abort immediately if any DB URL is missing ────────────────────────
# CRITICAL FIX: create_engine("") does not raise immediately — SQLAlchemy
# accepts the empty string and defers the error until the first real connection.
# That deferred crash happens inside Base.metadata.create_all() at startup,
# which is outside any request context, so uvicorn treats it as a fatal import
# error and logs "Shutting down" + restarts. Checking here produces a clear
# message in Render logs and stops the restart loop immediately.
_REQUIRED_DB_VARS = [
    ("STUDENT_DATABASE_URL", settings.STUDENT_DATABASE_URL),
    ("MASTER_DATABASE_URL",  settings.MASTER_DATABASE_URL),
    ("FACULTY_DATABASE_URL", settings.FACULTY_DATABASE_URL),
]
for _var_name, _var_value in _REQUIRED_DB_VARS:
    if not _var_value:
        print(
            f"[STARTUP FATAL] {_var_name} is not set or is empty.\n"
            f"  → Go to Render › Your Service › Environment and add {_var_name}.\n"
            f"  → Format: postgresql://user:password@host:5432/dbname",
            flush=True,
        )
        sys.exit(1)

# ─── Shared engine kwargs ─────────────────────────────────────────────────────
# pool_pre_ping   – tests each connection before use; discards dead ones silently
# pool_recycle    – forces connections to be recycled after 10 minutes,
#                   preventing the "server closed the connection unexpectedly" error
#                   caused by Postgres dropping idle connections
# pool_size       – max persistent connections kept open
# max_overflow    – extra connections allowed beyond pool_size under load
# connect_args    – socket-level timeout so hung connections don't block forever
_ENGINE_KWARGS = dict(
    pool_pre_ping=True,
    pool_recycle=600,          # recycle every 10 minutes
    pool_size=5,
    max_overflow=10,
    connect_args={
        "connect_timeout": 10,
        "sslmode": "require",   # seconds before giving up on a new connection
    },
)


# ─── Student DB (main app DB) ────────────────────────────────────────────────
student_engine = create_engine(settings.STUDENT_DATABASE_URL, **_ENGINE_KWARGS)
StudentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=student_engine)


# ─── Master DB (read-only student registry) ──────────────────────────────────
master_engine = create_engine(settings.MASTER_DATABASE_URL, **_ENGINE_KWARGS)
MasterSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=master_engine)


# ─── Faculty DB (read-only faculty registry) ─────────────────────────────────
faculty_engine = create_engine(settings.FACULTY_DATABASE_URL, **_ENGINE_KWARGS)
FacultySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=faculty_engine)


# ─── Dependency: Student DB ───────────────────────────────────────────────────
def get_student_db():
    db = StudentSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Dependency: Master (student registry) DB ────────────────────────────────
def get_master_db():
    db = MasterSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Dependency: Faculty DB ───────────────────────────────────────────────────
def get_faculty_db():
    db = FacultySessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Backward-compatible aliases ─────────────────────────────────────────────
engine = student_engine
main_engine = student_engine
SessionLocal = StudentSessionLocal
MainSessionLocal = StudentSessionLocal
get_db = get_student_db