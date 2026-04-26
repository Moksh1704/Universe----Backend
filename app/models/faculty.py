"""
app/models/faculty.py

Models for faculty_db (read-only).
Uses its own SQLAlchemy Base so it never touches student_db.
"""
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base
import datetime

FacultyBase = declarative_base()


class FacultyMember(FacultyBase):
    """
    Read-only faculty identity record.
    Table: faculty
    No password column – auth uses default password or OTP.
    """
    __tablename__ = "faculty"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(200), nullable=False)
    email       = Column(String(255), unique=True, nullable=False, index=True)
    department  = Column(String(200), nullable=True)
    designation = Column(String(200), nullable=True)


class FacultyTimetable(FacultyBase):
    """
    Read-only timetable record stored in faculty_db.

    Table: timetable
    Structure matches the spec:
        id, faculty_id, day, time_slot, subject, section, year, created_at

    SQLAlchemy silently ignores extra columns present in the real DB.
    """
    __tablename__ = "timetable"

    id         = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, nullable=False, index=True)
    day        = Column(String(20),  nullable=False)   # e.g. "Monday"
    time_slot  = Column(String(50),  nullable=False)   # e.g. "9:00-10:40"
    subject    = Column(String(200), nullable=False)
    section    = Column(String(50),  nullable=True)    # e.g. "CSE 01"
    year       = Column(Integer,     nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)