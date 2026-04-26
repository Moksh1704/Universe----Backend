"""
app/models/__init__.py

UniVerse — SQLAlchemy ORM models.
All tables for the student_db live here.
MasterStudent is read-only (maps to master_db).
"""
import uuid
from datetime import datetime, date, time
from sqlalchemy import (
    Column, String, Boolean, Integer, Float, Text,
    DateTime, Date, Time, ForeignKey, Enum as SAEnum,
    Table, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
import enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    student = "student"
    faculty = "faculty"
    admin   = "admin"


class AnnouncementType(str, enum.Enum):
    exam     = "exam"
    result   = "result"
    holiday  = "holiday"
    general  = "general"


class EventCategory(str, enum.Enum):
    technical = "technical"
    cultural  = "cultural"
    sports    = "sports"


class AttendanceStatus(str, enum.Enum):
    present = "present"
    absent  = "absent"


class JobStatus(str, enum.Enum):
    open   = "open"
    closed = "closed"


# ─── Association Tables ───────────────────────────────────────────────────────

event_registrations = Table(
    "event_registrations",
    Base.metadata,
    Column("student_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("event_id",   UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
    Column("registered_at", DateTime, default=datetime.utcnow),
)

job_applications = Table(
    "job_applications",
    Base.metadata,
    Column("student_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("job_id",     UUID(as_uuid=True), ForeignKey("jobs.id",  ondelete="CASCADE"), primary_key=True),
    Column("applied_at", DateTime, default=datetime.utcnow),
)

post_likes = Table(
    "post_likes",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id",  ondelete="CASCADE"), primary_key=True),
    Column("post_id", UUID(as_uuid=True), ForeignKey("posts.id",  ondelete="CASCADE"), primary_key=True),
)


# ─── User Model ───────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name            = Column(String(200), nullable=False)
    nickname        = Column(String(200), nullable=True)   # short display name from master DB
    email           = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)   # nullable for Google/OTP users
    role            = Column(SAEnum(UserRole), nullable=False, default=UserRole.student)
    avatar_url      = Column(String(500), nullable=True)
    is_active       = Column(Boolean, default=True)
    is_first_login  = Column(Boolean, default=True)

    # Google auth
    google_id       = Column(String(255), nullable=True, unique=True)

    # OTP fields
    otp                  = Column(String(6),   nullable=True)
    otp_expiry           = Column(DateTime,    nullable=True)
    otp_request_count    = Column(Integer,     default=0)
    otp_last_request_at  = Column(DateTime,    nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Student-specific
    department          = Column(String(200), nullable=True)
    year                = Column(Integer,     nullable=True)
    section             = Column(String(10),  nullable=True)
    registration_number = Column(String(50),  unique=True, nullable=True)
    overall_attendance  = Column(Float,       default=0.0)

    # Faculty-specific
    designation = Column(String(200), nullable=True)

    # Relationships
    posts            = relationship("Post",         back_populates="user",   cascade="all, delete-orphan")
    comments         = relationship("Comment",      back_populates="user",   cascade="all, delete-orphan")
    notifications    = relationship("Notification", back_populates="user",   cascade="all, delete-orphan")
    timetable_entries= relationship("Timetable",    back_populates="faculty")
    registered_events= relationship("Event", secondary=event_registrations,  back_populates="registered_students")
    applied_jobs     = relationship("Job",  secondary=job_applications,       back_populates="applicants")
    liked_posts      = relationship("Post", secondary=post_likes,             back_populates="liked_by")


# ─── Master Database Model (READ-ONLY) ───────────────────────────────────────

class MasterStudent(Base):
    """
    Maps to the 'students' table in MASTER_DATABASE_URL.
    Used only for lookups — never write to this table.
    Alembic migrations must NOT alter it.
    """
    __tablename__ = "students"

    regnum     = Column(String(50),  primary_key=True, index=True)
    name       = Column(String(200), nullable=True)    # short / display name
    fullname   = Column(String(300), nullable=True)    # legal full name
    email      = Column(String(255), unique=True, nullable=False, index=True)
    course     = Column(String(200), nullable=True)    # e.g. "B.Tech (Integrated)"
    year       = Column(Integer,     nullable=True)
    department = Column(String(200), nullable=True)    # e.g. "CSE"
    section    = Column(String(50),  nullable=True)    # e.g. "CSE06"
    cgpa       = Column(Float,       nullable=True)

    @property
    def branch(self):
        """Compatibility shim — auth.py uses master.branch."""
        return self.department


# ─── Announcement Model ───────────────────────────────────────────────────────

class Announcement(Base):
    __tablename__ = "announcements"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title      = Column(String(300), nullable=False)
    body       = Column(Text,        nullable=False)
    type       = Column(SAEnum(AnnouncementType), nullable=False, default=AnnouncementType.general)
    date       = Column(Date,    default=date.today)
    is_urgent  = Column(Boolean, default=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    creator = relationship("User", foreign_keys=[created_by])


# ─── Event Model ──────────────────────────────────────────────────────────────

class Event(Base):
    __tablename__ = "events"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title            = Column(String(300), nullable=False)
    description      = Column(Text,        nullable=True)
    date             = Column(Date,        nullable=False)
    time             = Column(Time,        nullable=False)
    venue            = Column(String(300), nullable=True)
    category         = Column(SAEnum(EventCategory), nullable=False, default=EventCategory.technical)
    total_slots      = Column(Integer, default=100)
    registered_count = Column(Integer, default=0)
    image_url        = Column(String(500), nullable=True)
    created_by       = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    creator             = relationship("User", foreign_keys=[created_by])
    registered_students = relationship("User", secondary=event_registrations, back_populates="registered_events")


# ─── Attendance Models ────────────────────────────────────────────────────────
#
# Three-table design:
#
#  SubjectAttendance  – running totals per student per subject
#                       (total_classes, attended_classes, percentage)
#
#  DayAttendance      – one row per student per class period
#                       authoritative record used for percentage recalculation
#
#  Attendance         – snapshot per student per time-slot submitted by faculty
#                       used by /check and /update endpoints; also the source
#                       for "was this slot already submitted?" queries
#
# All three use registration_number (String) as the student key so faculty
# can mark attendance for students not yet registered in the app.

class SubjectAttendance(Base):
    __tablename__ = "subject_attendance"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registration_number = Column(String(50), nullable=False, index=True)
    subject             = Column(String(200), nullable=False)
    total_classes       = Column(Integer, default=0)
    attended_classes    = Column(Integer, default=0)
    percentage          = Column(Float,   default=0.0)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DayAttendance(Base):
    __tablename__ = "day_attendance"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    registration_number = Column(String(50), nullable=False, index=True)
    date                = Column(Date,       nullable=False)
    time_slot           = Column(String(50), nullable=True)
    subject             = Column(String(200), nullable=False)
    status              = Column(SAEnum(AttendanceStatus), nullable=False)
    section             = Column(String(20), nullable=True)
    marked_by           = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    faculty = relationship("User", foreign_keys=[marked_by])


class Attendance(Base):
    """
    Faculty-submission snapshot.

    One row per student per time-slot per submission. Allows the app to:
      • detect whether a slot has already been submitted  (GET /attendance/check)
      • atomically replace a submission                   (PUT /attendance/update)

    student_id  → registration_number string (NOT a FK to users.id)
    faculty_id  → timetable slot identifier (NOT a FK to users.id; timetable
                  stores integer slot IDs, not UUIDs)
    status      → True = present, False = absent  (boolean, not the enum)

    The unique constraint prevents duplicate submissions for the same
    student + slot combination.
    """
    __tablename__ = "attendance"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    faculty_id = Column(String(100), nullable=True)    # timetable slot id
    subject    = Column(String(200), nullable=False)
    section    = Column(String(50),  nullable=True,  index=True)
    year       = Column(Integer,     nullable=True)
    date       = Column(Date,        nullable=False,  index=True)
    time_slot  = Column(String(50),  nullable=True)
    student_id = Column(String(50),  nullable=False,  index=True)  # registration_number
    status     = Column(Boolean,     nullable=False)               # True = present
    created_at = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "section", "subject", "date", "time_slot", "student_id",
            name="uq_attendance_slot_student",
        ),
    )


# ─── Post / Social Feed Models ────────────────────────────────────────────────

class Post(Base):
    __tablename__ = "posts"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content        = Column(Text,    nullable=False)
    image_url      = Column(String(500), nullable=True)
    likes_count    = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Soft-delete fields ────────────────────────────────────────────────────
    is_deleted  = Column(Boolean,  nullable=False, default=False)
    deleted_by  = Column(String(20), nullable=True)   # "admin" | "user" | None
    deleted_at  = Column(DateTime, nullable=True)
    # ─────────────────────────────────────────────────────────────────────────

    user     = relationship("User",    back_populates="posts",    foreign_keys=[user_id])
    comments = relationship("Comment", back_populates="post",     cascade="all, delete-orphan")
    liked_by = relationship("User",    secondary=post_likes,      back_populates="liked_posts")


class Comment(Base):
    __tablename__ = "comments"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id    = Column(UUID(as_uuid=True), ForeignKey("posts.id",  ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id",  ondelete="CASCADE"), nullable=False)
    content    = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    post = relationship("Post",    back_populates="comments")
    user = relationship("User",    back_populates="comments")


# ─── Timetable Model ──────────────────────────────────────────────────────────

class Timetable(Base):
    __tablename__ = "timetable"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    day        = Column(String(20),  nullable=False)   # Monday, Tuesday, …
    subject    = Column(String(200), nullable=False)
    start_time = Column(Time,        nullable=False)
    end_time   = Column(Time,        nullable=False)
    faculty_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    room       = Column(String(100), nullable=True)
    department = Column(String(200), nullable=True)
    section    = Column(String(10),  nullable=True)
    year       = Column(Integer,     nullable=True)
    created_at = Column(DateTime,    default=datetime.utcnow)

    faculty = relationship("User", back_populates="timetable_entries")


# ─── Job / Placement Model ────────────────────────────────────────────────────

class Job(Base):
    __tablename__ = "jobs"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name = Column(String(300), nullable=False)
    role         = Column(String(300), nullable=False)
    package      = Column(String(100), nullable=True)
    deadline     = Column(Date,        nullable=False)
    description  = Column(Text,        nullable=True)
    status       = Column(SAEnum(JobStatus), default=JobStatus.open)
    created_by   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

    creator    = relationship("User", foreign_keys=[created_by])
    applicants = relationship("User", secondary=job_applications, back_populates="applied_jobs")


# ─── Notification Model ───────────────────────────────────────────────────────

class Notification(Base):
    __tablename__ = "notifications"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title      = Column(String(300), nullable=False)
    message    = Column(Text,        nullable=False)
    is_read    = Column(Boolean,     default=False)
    created_at = Column(DateTime,    default=datetime.utcnow)

    user = relationship("User", back_populates="notifications")