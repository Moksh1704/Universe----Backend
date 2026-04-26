"""
app/routers/faculty_timetable.py

Timetable API reading from faculty_db.
Does NOT touch student_db.

FIX: The timetable table has dirty data — trailing/leading spaces in day,
time_slot, subject, section fields (e.g. "Monday " instead of "Monday").
All string fields are .strip()ed on serialization, and day-filtering uses
func.trim() at the DB level so "Monday " matches a query for "Monday".

ADDITIONAL FIXES:
- Section is normalized (spaces removed, uppercase) on serialization
- Added debug logging for section values
- Added faculty POST / (create own slot) route

ROUTE SUMMARY (in declaration order — order matters to avoid shadowing):
  ── ADMIN (no auth required) ─────────────────────────────────────────────
  GET    /timetable/faculty/admin                  — all rows
  POST   /timetable/faculty/admin/create           — create row for any faculty_id
  PUT    /timetable/faculty/admin/update/{id}      — update any row
  DELETE /timetable/faculty/admin/delete/{id}      — delete any row

  ── FACULTY — /me group (auth required) ──────────────────────────────────
  GET    /timetable/faculty/me                     — own full timetable
  GET    /timetable/faculty/me/slots               — own slots for a given day
  POST   /timetable/faculty/me                     — create own slot   ← NEW

  ── FACULTY — slot-level CRUD (auth required, ownership enforced) ─────────
  PUT    /timetable/faculty/{slot_id}              — update own slot (admin: any)
  DELETE /timetable/faculty/{slot_id}              — delete own slot (admin: any)

  ── LOOKUP (auth required) ───────────────────────────────────────────────
  GET    /timetable/faculty/faculty-list           — list distinct faculty members
  GET    /timetable/faculty/{faculty_id}           — timetable by faculty  ← LAST
"""
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.database import get_faculty_db
from app.models.faculty import FacultyTimetable, FacultyMember
from app.auth.dependencies import get_current_user
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timetable/faculty", tags=["Faculty Timetable"])

DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_start_time(time_slot: str) -> datetime:
    """
    Parse the start time from a time_slot string like '9:00 - 10:40' or '13:30 - 15:10'.
    Returns a datetime for use as a sort key.
    Falls back to datetime.max on parse failure so invalid slots sort last without crashing.
    """
    try:
        raw = (time_slot or "").split(" - ")[0].strip()
        return datetime.strptime(raw, "%H:%M")
    except Exception:
        logger.warning(f"[faculty_timetable] Could not parse time_slot: '{time_slot}' — sorting last")
        return datetime.max


def normalize_section(section: str) -> str:
    """Normalize section: remove spaces, uppercase. e.g. 'CSE 06' -> 'CSE06'"""
    if not section:
        return section
    return section.strip().replace(" ", "").upper()


def _serialize(entry: FacultyTimetable) -> dict:
    """Serialize and strip/normalize all string fields to handle dirty DB data."""
    raw_section = (entry.section or "").strip()
    norm_section = normalize_section(raw_section)
    return {
        "id":         entry.id,          # slot_id used for PUT/DELETE
        "faculty_id": entry.faculty_id,
        "day":        (entry.day       or "").strip(),
        "time_slot":  (entry.time_slot or "").strip(),
        "subject":    (entry.subject   or "").strip(),
        "section":    norm_section,
        "year":       entry.year,
    }


def _sort(entries):
    """Sort timetable entries by day order then chronological start time."""
    entries.sort(key=lambda e: (
        DAYS_ORDER.index(e.day.strip()) if (e.day or "").strip() in DAYS_ORDER else 99,
        parse_start_time((e.time_slot or "").strip()),
    ))
    return entries


def _get_faculty_by_email(email: str, faculty_db: Session) -> Optional[FacultyMember]:
    return faculty_db.query(FacultyMember).filter(
        FacultyMember.email == email
    ).first()


def _require_role(user: User, *allowed_roles: str) -> None:
    """Raise 403 if the user's role is not in allowed_roles."""
    role = getattr(user, "role", None)
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. Required role(s): {', '.join(allowed_roles)}. Your role: {role}",
        )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class UpdateTimetableRequest(BaseModel):
    """
    Shared payload for create/update operations.

    - faculty_id  : required by admin routes; ignored (auto-filled) on faculty /me routes.
    - day         : e.g. "Monday"
    - time_slot   : e.g. "9:00 - 10:40"
    - subject     : e.g. "Data Structures"
    - section     : e.g. "CSE02"  (normalized to uppercase, no spaces)
    - year        : academic year integer, e.g. 2
    """
    faculty_id: Optional[int] = None
    day:        str
    time_slot:  str
    subject:    str
    section:    str
    year:       Optional[int] = None


# ===========================================================================
# SECTION 1 — ADMIN ROUTES
# Declared first to prevent shadowing by /{slot_id} or /{faculty_id}.
# No authentication required (dev / internal use).
# ===========================================================================

@router.get("/admin", response_model=List[dict])
def get_all_timetables(
    faculty_id: Optional[int] = Query(None, description="Filter by faculty ID"),
    day:        Optional[str] = Query(None, description="Filter by day (e.g. 'Monday')"),
    section:    Optional[str] = Query(None, description="Filter by section (e.g. 'CSE02')"),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Admin — returns all timetable rows, with optional filters.

        GET /timetable/faculty/admin
        GET /timetable/faculty/admin?day=Monday
        GET /timetable/faculty/admin?faculty_id=6
        GET /timetable/faculty/admin?section=CSE02
    """
    query = faculty_db.query(FacultyTimetable)

    if faculty_id is not None:
        query = query.filter(FacultyTimetable.faculty_id == faculty_id)

    if day:
        query = query.filter(func.trim(FacultyTimetable.day) == day.strip())

    if section:
        norm = normalize_section(section)
        query = query.filter(
            func.replace(func.upper(func.trim(FacultyTimetable.section)), " ", "") == norm
        )

    entries = query.all()
    result  = [_serialize(e) for e in _sort(entries)]

    logger.info(
        f"[faculty_timetable] /admin fetched {len(result)} entries "
        f"(faculty_id={faculty_id}, day={day}, section={section})"
    )
    return result


@router.post("/admin/create", response_model=dict)
def admin_create_timetable_entry(
    payload: UpdateTimetableRequest,
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Admin — creates a new timetable row for any faculty_id.

        POST /timetable/faculty/admin/create
        {
          "faculty_id": 6,
          "day": "Monday",
          "time_slot": "9:00 - 10:40",
          "subject": "Data Structures",
          "section": "CSE02",
          "year": 2
        }
    """
    if payload.faculty_id is None:
        raise HTTPException(
            status_code=422,
            detail="faculty_id is required when creating a timetable entry via the admin route.",
        )

    new_entry = FacultyTimetable(
        faculty_id=payload.faculty_id,
        day=payload.day.strip(),
        time_slot=payload.time_slot.strip(),
        subject=payload.subject.strip(),
        section=normalize_section(payload.section),
        year=payload.year,
    )

    faculty_db.add(new_entry)
    faculty_db.commit()
    faculty_db.refresh(new_entry)

    logger.info(
        f"[faculty_timetable] /admin/create — created entry id={new_entry.id} "
        f"for faculty_id={new_entry.faculty_id}"
    )
    return _serialize(new_entry)


@router.put("/admin/update/{id}", response_model=dict)
def admin_update_timetable_entry(
    id: int,
    payload: UpdateTimetableRequest,
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Admin — updates any timetable row by slot id.
    Optionally reassigns the slot to a different faculty_id.

        PUT /timetable/faculty/admin/update/42
        {
          "day": "Tuesday",
          "time_slot": "11:00 - 12:40",
          "subject": "Algorithms",
          "section": "CSE03",
          "year": 3
        }
    """
    entry = faculty_db.query(FacultyTimetable).filter(FacultyTimetable.id == id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    entry.day       = payload.day.strip()
    entry.time_slot = payload.time_slot.strip()
    entry.subject   = payload.subject.strip()
    entry.section   = normalize_section(payload.section)
    entry.year      = payload.year

    if payload.faculty_id is not None:
        entry.faculty_id = payload.faculty_id

    faculty_db.commit()
    faculty_db.refresh(entry)

    logger.info(f"[faculty_timetable] /admin/update/{id} — updated entry id={id}")
    return _serialize(entry)


@router.delete("/admin/delete/{id}", response_model=dict)
def admin_delete_timetable_entry(
    id: int,
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Admin — hard-deletes any timetable slot by its primary key.

        DELETE /timetable/faculty/admin/delete/42
    """
    entry = faculty_db.query(FacultyTimetable).filter(FacultyTimetable.id == id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    serialized = _serialize(entry)
    faculty_db.delete(entry)
    faculty_db.commit()

    logger.info(f"[faculty_timetable] /admin/delete/{id} — deleted entry id={id}")
    return {"message": "Timetable entry deleted successfully", "deleted": serialized}


# ===========================================================================
# SECTION 2 — FACULTY /me ROUTES  (auth required)
# Must come before /{slot_id} to avoid route shadowing.
# ===========================================================================

@router.get("/me", response_model=List[dict])
def get_my_timetable(
    day: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Returns the authenticated faculty member's full timetable.
    Optionally filter by day.

        GET /timetable/faculty/me
        GET /timetable/faculty/me?day=Monday
    """
    _require_role(current_user, "admin", "faculty")

    faculty = _get_faculty_by_email(current_user.email, faculty_db)
    if not faculty:
        return []

    sql = """
        SELECT * FROM timetable
        WHERE faculty_id = :faculty_id
        {day_filter}
        ORDER BY
            CASE TRIM(day)
                WHEN 'Monday'    THEN 1
                WHEN 'Tuesday'   THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday'  THEN 4
                WHEN 'Friday'    THEN 5
                WHEN 'Saturday'  THEN 6
                ELSE 7
            END,
            (
                CASE
                    WHEN CAST(SPLIT_PART(SPLIT_PART(time_slot, ' - ', 1), ':', 1) AS INTEGER) < 8
                    THEN CAST(SPLIT_PART(SPLIT_PART(time_slot, ' - ', 1), ':', 1) AS INTEGER) + 12
                    ELSE CAST(SPLIT_PART(SPLIT_PART(time_slot, ' - ', 1), ':', 1) AS INTEGER)
                END
            ) * 60
            + CAST(SPLIT_PART(SPLIT_PART(time_slot, ' - ', 1), ':', 2) AS INTEGER)
    """

    params = {"faculty_id": faculty.id}
    if day:
        sql = sql.format(day_filter="AND TRIM(day) = :day")
        params["day"] = day.strip()
    else:
        sql = sql.format(day_filter="")

    rows = faculty_db.execute(text(sql), params).mappings().all()

    class _Row:
        __slots__ = ("id", "faculty_id", "day", "time_slot", "subject", "section", "year")
        def __init__(self, m):
            for col in self.__slots__:
                setattr(self, col, m.get(col))

    result = [_serialize(_Row(r)) for r in rows]
    logger.info(f"[faculty_timetable] GET /me — fetched {len(result)} entries for faculty={faculty.id}, day={day}")
    sections = list({e["section"] for e in result})
    logger.info(f"[faculty_timetable] sections in result: {sections}")
    return result


@router.get("/me/slots", response_model=List[dict])
def get_slots_for_day(
    day: str = Query(...),
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Returns the authenticated faculty member's slots for a specific day.

        GET /timetable/faculty/me/slots?day=Monday
    """
    _require_role(current_user, "admin", "faculty")

    faculty = _get_faculty_by_email(current_user.email, faculty_db)
    if not faculty:
        return []

    entries = faculty_db.query(FacultyTimetable).filter(
        FacultyTimetable.faculty_id == faculty.id,
        func.trim(FacultyTimetable.day) == day.strip(),
    ).all()

    entries.sort(key=lambda e: parse_start_time((e.time_slot or "").strip()))
    return [_serialize(e) for e in entries]


@router.post("/me", response_model=dict)
def create_faculty_slot(
    payload: UpdateTimetableRequest,
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Faculty — creates a new timetable slot assigned to the authenticated user.
    The faculty_id field in the payload is ignored; it is always taken from
    the current user's profile.

        POST /timetable/faculty/me
        {
          "day": "Monday",
          "time_slot": "9:00 - 10:40",
          "subject": "Data Structures",
          "section": "CSE02",
          "year": 2
        }
    """
    _require_role(current_user, "faculty")

    faculty = _get_faculty_by_email(current_user.email, faculty_db)
    if not faculty:
        raise HTTPException(status_code=404, detail="Faculty profile not found for the current user.")

    new_entry = FacultyTimetable(
        faculty_id=faculty.id,
        day=payload.day.strip(),
        time_slot=payload.time_slot.strip(),
        subject=payload.subject.strip(),
        section=normalize_section(payload.section),
        year=payload.year,
    )

    faculty_db.add(new_entry)
    faculty_db.commit()
    faculty_db.refresh(new_entry)

    logger.info(
        f"[faculty_timetable] POST /me — created entry id={new_entry.id} "
        f"for faculty_id={new_entry.faculty_id} by {current_user.email}"
    )
    return _serialize(new_entry)


# ===========================================================================
# SECTION 3 — SLOT-LEVEL CRUD  PUT / DELETE /{slot_id}  (auth required)
# Declared after /me/* but before /{faculty_id} (GET).
# Both admin and faculty may call these; faculty is restricted to own slots.
# ===========================================================================

@router.put("/{slot_id}", response_model=dict)
def update_timetable_entry(
    slot_id: int,
    payload: UpdateTimetableRequest,
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Update a timetable slot by its primary key.

    - faculty : may only update slots that belong to them.
    - admin   : may update any slot; may also reassign faculty_id.

        PUT /timetable/faculty/42
        {
          "day": "Wednesday",
          "time_slot": "13:30 - 15:10",
          "subject": "OS",
          "section": "CSE05",
          "year": 3
        }
    """
    _require_role(current_user, "admin", "faculty")

    entry = faculty_db.query(FacultyTimetable).filter(FacultyTimetable.id == slot_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    user_role = getattr(current_user, "role", None)

    if user_role == "faculty":
        faculty = _get_faculty_by_email(current_user.email, faculty_db)
        if not faculty or entry.faculty_id != faculty.id:
            raise HTTPException(status_code=403, detail="Not authorised to edit this entry")

    entry.day       = payload.day.strip()
    entry.time_slot = payload.time_slot.strip()
    entry.subject   = payload.subject.strip()
    entry.section   = normalize_section(payload.section)
    entry.year      = payload.year

    # Admin may reassign the slot to a different faculty member
    if user_role == "admin" and payload.faculty_id is not None:
        entry.faculty_id = payload.faculty_id

    faculty_db.commit()
    faculty_db.refresh(entry)

    logger.info(
        f"[faculty_timetable] PUT /{slot_id} — updated by {current_user.email} (role={user_role})"
    )
    return _serialize(entry)


@router.delete("/{slot_id}", response_model=dict)
def delete_timetable_entry(
    slot_id: int,
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Delete a timetable slot by its primary key.

    - faculty : may only delete slots that belong to them.
    - admin   : may delete any slot.

        DELETE /timetable/faculty/42
    """
    _require_role(current_user, "admin", "faculty")

    entry = faculty_db.query(FacultyTimetable).filter(FacultyTimetable.id == slot_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Timetable entry not found")

    user_role = getattr(current_user, "role", None)

    if user_role == "faculty":
        faculty = _get_faculty_by_email(current_user.email, faculty_db)
        if not faculty or entry.faculty_id != faculty.id:
            raise HTTPException(status_code=403, detail="Not authorised to delete this entry")

    serialized = _serialize(entry)
    faculty_db.delete(entry)
    faculty_db.commit()

    logger.info(
        f"[faculty_timetable] DELETE /{slot_id} — deleted by {current_user.email} (role={user_role})"
    )
    return {"message": "Timetable entry deleted successfully", "deleted": serialized}


# ===========================================================================
# SECTION 4 — LOOKUP ROUTES  (auth required)
# Declared LAST to prevent shadowing earlier string-literal segments.
# /{faculty_id} must always be the very last route registered.
# ===========================================================================

@router.get("/faculty-list", response_model=List[dict])
def get_faculty_list(
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Returns distinct faculty members with their integer faculty_id and name.
    No authentication required (dev / internal use).

        GET /timetable/faculty/faculty-list
    """
    rows = (
        faculty_db.query(FacultyTimetable.faculty_id)
        .distinct()
        .all()
    )

    result = []
    for (fid,) in rows:
        if fid is None:
            continue

        member = faculty_db.query(FacultyMember).filter(FacultyMember.id == fid).first()

        if member and getattr(member, "name", None):
            name = member.name.strip()
        else:
            sample = (
                faculty_db.query(FacultyTimetable)
                .filter(FacultyTimetable.faculty_id == fid)
                .first()
            )
            raw_name = getattr(sample, "faculty_name", None) if sample else None
            name = raw_name.strip() if raw_name else f"Faculty {fid}"

        result.append({"faculty_id": fid, "name": name})

    result.sort(key=lambda x: x["faculty_id"])
    logger.info(f"[faculty_timetable] /faculty-list returned {len(result)} faculty members")
    return result


@router.get("/{faculty_id}", response_model=List[dict])
def get_faculty_timetable(
    faculty_id: int,
    day: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    faculty_db: Session = Depends(get_faculty_db),
):
    """
    Returns a faculty member's timetable by their integer faculty_id.
    Accessible by admin or by the faculty member themselves.

    ⚠ Must remain the LAST route to avoid shadowing /admin/*, /me/*, /faculty-list.

        GET /timetable/faculty/6
        GET /timetable/faculty/6?day=Monday
    """
    _require_role(current_user, "admin", "faculty")

    user_role = getattr(current_user, "role", None)
    if user_role == "faculty":
        faculty = _get_faculty_by_email(current_user.email, faculty_db)
        if faculty and faculty.id != faculty_id:
            raise HTTPException(status_code=403, detail="Not authorised to view this timetable")

    query = faculty_db.query(FacultyTimetable).filter(
        FacultyTimetable.faculty_id == faculty_id
    )
    if day:
        query = query.filter(func.trim(FacultyTimetable.day) == day.strip())

    return [_serialize(e) for e in _sort(query.all())]