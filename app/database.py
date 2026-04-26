from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings


# Shared SQLAlchemy base for ORM models
Base = declarative_base()

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