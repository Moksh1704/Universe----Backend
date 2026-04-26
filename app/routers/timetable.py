import logging
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import User, Timetable, UserRole
from app.schemas import CreateTimetableRequest, TimetableResponse, MessageResponse
from app.auth.dependencies import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timetable", tags=["Timetable"])

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_time_for_sort(t) -> datetime:
    """
    Convert a start_time value (datetime.time, string, or None) to a datetime
    for chronological sorting. Falls back to datetime.max on failure.
    """
    if t is None:
        return datetime.max
    try:
        # If it's already a time object (SQLAlchemy Time column), combine with a dummy date
        if hasattr(t, "hour"):
            return datetime.combine(datetime.min.date(), t)
        # If it's a string like "9:00" or "13:30"
        raw = str(t).strip()
        return datetime.strptime(raw, "%H:%M")
    except Exception:
        logger.warning(f"[timetable] Could not parse start_time: '{t}' — sorting last")
        return datetime.max


def normalize_section(section: str) -> str:
    """Normalize section: remove spaces, uppercase. e.g. 'CSE 06' -> 'CSE06'"""
    if not section:
        return section
    return section.strip().replace(" ", "").upper()


@router.post("", response_model=TimetableResponse, status_code=status.HTTP_201_CREATED)
def create_timetable_entry(
    payload: CreateTimetableRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Add a timetable entry."""
    # Normalize section before storing
    normalized_section = normalize_section(payload.section) if payload.section else payload.section
    logger.info(f"[timetable] creating entry - section: '{payload.section}' -> normalized: '{normalized_section}'")

    entry = Timetable(
        day=payload.day,
        subject=payload.subject,
        start_time=payload.startTime,
        end_time=payload.endTime,
        faculty_id=payload.facultyId,
        room=payload.room,
        department=payload.department,
        section=normalized_section,
        year=payload.year,
    )
    db.add(entry)
    db.commit()
    entry = db.query(Timetable).filter(Timetable.id == entry.id).first()
    return TimetableResponse.from_orm(entry)


@router.get("", response_model=List[TimetableResponse])
def get_timetable(
    department: Optional[str] = Query(None),
    section: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    day: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get timetable. Students get their own section's; faculty see their assigned slots."""
    query = db.query(Timetable)

    if current_user.role == UserRole.student:
        # Auto-filter by student's own section (normalized)
        if current_user.department:
            query = query.filter(Timetable.department == current_user.department)
        if current_user.section:
            norm_section = normalize_section(current_user.section)
            logger.info(f"[timetable] student section: '{current_user.section}' -> '{norm_section}'")
            query = query.filter(
                func.replace(func.upper(Timetable.section), " ", "") == norm_section
            )
        if current_user.year:
            query = query.filter(Timetable.year == current_user.year)
    elif current_user.role == UserRole.faculty:
        # Faculty see their own classes
        query = query.filter(Timetable.faculty_id == current_user.id)
    else:
        # Admin can filter freely
        if department:
            query = query.filter(Timetable.department == department)
        if section:
            norm_section = normalize_section(section)
            logger.info(f"[timetable] admin section filter: '{section}' -> '{norm_section}'")
            query = query.filter(
                func.replace(func.upper(Timetable.section), " ", "") == norm_section
            )
        if year:
            query = query.filter(Timetable.year == year)

    if day:
        query = query.filter(Timetable.day == day)

    entries = query.all()
    logger.info(f"[timetable] timetable query result: {len(entries)} entries")

    # Sort by day order then chronological start time (not lexicographic string sort)
    entries.sort(key=lambda e: (
        DAYS_ORDER.index(e.day) if e.day in DAYS_ORDER else 99,
        _parse_time_for_sort(e.start_time),
    ))
    return [TimetableResponse.from_orm(e) for e in entries]


@router.put("/{entry_id}", response_model=TimetableResponse)
def update_timetable_entry(
    entry_id: str,
    payload: CreateTimetableRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Update a timetable entry."""
    entry = db.query(Timetable).filter(Timetable.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    # Normalize section before storing
    normalized_section = normalize_section(payload.section) if payload.section else payload.section
    logger.info(f"[timetable] updating entry {entry_id} - section: '{payload.section}' -> '{normalized_section}'")

    entry.day = payload.day
    entry.subject = payload.subject
    entry.start_time = payload.startTime
    entry.end_time = payload.endTime
    entry.faculty_id = payload.facultyId
    entry.room = payload.room
    entry.department = payload.department
    entry.section = normalized_section
    entry.year = payload.year

    db.commit()
    entry = db.query(Timetable).filter(Timetable.id == entry_id).first()
    return TimetableResponse.from_orm(entry)


@router.delete("/{entry_id}", response_model=MessageResponse)
def delete_timetable_entry(
    entry_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Delete a timetable entry."""
    entry = db.query(Timetable).filter(Timetable.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    db.delete(entry)
    db.commit()
    return MessageResponse(message="Timetable entry deleted")