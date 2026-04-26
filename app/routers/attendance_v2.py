"""
app/routers/attendance_v2.py  ── CANONICAL ATTENDANCE ROUTER (v2)

This is the ONLY attendance router. attendance.py is fully retired.

Endpoints:
  POST /attendance/mark                – Faculty/Admin: mark attendance for a session
  GET  /attendance/student/{id}        – Student (own) or Faculty/Admin (any student)
  GET  /attendance/faculty/students    – Faculty/Admin: get student list for a section
  GET  /attendance/faculty/schedule    – Faculty/Admin: get timetable/schedule slots
  GET  /attendance/check               – Faculty/Admin: check if attendance already marked
  GET  /attendance/me/overview         – Student: own attendance overview
  GET  /attendance/me                  – Student: subject-wise attendance list
  GET  /attendance/me/daily            – Student: day-wise attendance list
  GET  /attendance/me/summary          – Student: attendance summary with percentage
  GET  /attendance/admin/overall       – Admin: overall attendance for all students (auth)
  GET  /attendance/admin/overall-dev   – Admin: overall attendance for all students (no auth)
  GET  /attendance/download/{regnum}   – Download CSV for a student (auth, role-gated)
  GET  /attendance/download-dev/{regnum} – Download CSV for a student (no auth)

FIXES IN THIS VERSION:
  - Added GET /attendance/faculty/schedule (was missing — caused "no timetable record found")
  - Schedule reads from faculty_db (FacultyTimetable) which is the authoritative source
  - Falls back to student_db Timetable if faculty_db returns nothing
  - All string fields stripped/normalized to handle dirty DB data
  - Full debug logging on every endpoint
  - db.commit() confirmed present on all write paths
  - No duplicate route conflicts (attendance.py must NOT be registered in main.py)
  - Added admin/overall, admin/overall-dev, download, download-dev routes
"""

import csv
import io
import logging
from typing import List, Optional
from datetime import date as date_type, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from app.database import get_student_db, get_master_db, get_faculty_db
from app.models import (
    User, UserRole,
    DayAttendance, SubjectAttendance,
    AttendanceStatus,
    MasterStudent,
    Timetable,
)
from app.models.faculty import FacultyTimetable, FacultyMember
from app.auth.dependencies import get_current_user, require_faculty_or_admin
from app.auth.utils import verify_password

router = APIRouter(prefix="/attendance", tags=["Attendance V2"])


# ══════════════════════════════════════════════════════════════════
# Pydantic schemas (local — no changes to shared schemas needed)
# ══════════════════════════════════════════════════════════════════

class AttendanceRecord(BaseModel):
    student_id: str   # registration_number / regnum
    status: str       # "present" | "absent"


class MarkAttendanceBody(BaseModel):
    section_id: str
    subject: str
    date: str         # "YYYY-MM-DD"
    time_slot: str
    attendance: List[AttendanceRecord]


class UnlockBody(BaseModel):
    password: str     # plain-text password; faculty_id comes from the JWT token


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _resolve_att_status(status_str: str) -> AttendanceStatus:
    s = status_str.lower().strip()
    if s == "present":
        return AttendanceStatus.present
    if s == "absent":
        return AttendanceStatus.absent
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Invalid status value '{status_str}'. Must be 'present' or 'absent'.",
    )


def _normalize_section(section: str) -> str:
    """
    Normalize section for comparison.
      'CSE-06' → 'CSE06'
      'CSE 06' → 'CSE06'
      'cse06'  → 'CSE06'
    """
    if not section:
        return section
    return section.replace(" ", "").replace("-", "").upper()


def _normalized_master_col():
    """SQLAlchemy expression matching _normalize_section() on MasterStudent.section."""
    return func.replace(
        func.replace(func.upper(MasterStudent.section), " ", ""),
        "-", ""
    )


def _parse_start_time(time_slot: str) -> datetime:
    """
    Parse the start time from a time_slot string.

    Handles both formats:
      "9:00 - 10:40"   (space-padded dash)
      "13:30-15:10"    (compact dash)
      "1:30 - 3:10"    (12-hour-style, no leading zero)

    Strategy: split on the FIRST '-' that is surrounded by digits/colons,
    keeping the colon in the start token intact.

    Falls back to datetime.max on parse failure so invalid slots sort last
    without crashing.
    """
    try:
        raw = (time_slot or "").strip()

        if " - " in raw:
            start_token = raw.split(" - ", 1)[0].strip()
        elif " -" in raw:
            start_token = raw.split(" -", 1)[0].strip()
        elif "- " in raw:
            start_token = raw.split("- ", 1)[0].strip()
        else:
            import re
            m = re.match(r"(\d{1,2}:\d{2})", raw)
            start_token = m.group(1) if m else raw.split("-")[0]

        return datetime.strptime(start_token.strip(), "%H:%M")
    except Exception:
        logger.warning(f"[attendance_v2] Could not parse time_slot: '{time_slot}' — sorting last")
        return datetime.max


def _build_student_attendance_summary(
    regnum: str,
    master_db: Session,
    student_db: Session,
) -> dict:
    """
    Shared helper: builds the per-student attendance summary dict used by both
    admin/overall and admin/overall-dev endpoints.

    Returns:
    {
        "name":    str,
        "regnum":  str,
        "year":    int | None,
        "section": str,
        "subjects": { subject: { present, total, percentage } },
        "avg":     float,
    }
    """
    master = master_db.query(MasterStudent).filter(
        MasterStudent.regnum == regnum
    ).first()

    name    = (
        getattr(master, "fullname", None) or
        getattr(master, "name", None) or
        regnum
    ).strip() if master else regnum
    year    = master.year    if master else None
    section = (master.section or "").strip() if master else ""

    subject_rows = student_db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == regnum
    ).all()

    subjects_dict = {}
    total_classes  = 0
    total_attended = 0

    for row in subject_rows:
        subjects_dict[row.subject] = {
            "present":    row.attended_classes,
            "total":      row.total_classes,
            "percentage": round(row.percentage, 2),
        }
        total_classes  += row.total_classes
        total_attended += row.attended_classes

    avg = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )

    return {
        "name":     name,
        "regnum":   regnum,
        "year":     year,
        "section":  section,
        "subjects": subjects_dict,
        "avg":      avg,
    }


def _generate_csv_for_regnum(
    regnum: str,
    master_db: Session,
    student_db: Session,
) -> StreamingResponse:
    """
    Shared helper: generates a CSV StreamingResponse for the given regnum.
    Columns: date, subject, time_slot, status
    """
    master = master_db.query(MasterStudent).filter(
        MasterStudent.regnum == regnum
    ).first()

    if not master:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Student '{regnum}' not found.",
        )

    day_rows = (
        student_db.query(DayAttendance)
        .filter(DayAttendance.registration_number == regnum)
        .order_by(DayAttendance.date.asc(), DayAttendance.subject.asc())
        .all()
    )

    subject_rows = student_db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == regnum
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header block
    student_name = (master.fullname or master.name or regnum).strip()
    writer.writerow(["Student Name", student_name])
    writer.writerow(["Registration Number", regnum])
    writer.writerow(["Section", (master.section or "").strip()])
    writer.writerow(["Year", master.year or ""])
    writer.writerow([])

    # Subject summary
    writer.writerow(["Subject", "Present", "Total", "Percentage"])
    total_classes  = 0
    total_attended = 0
    for s in subject_rows:
        writer.writerow([
            s.subject,
            s.attended_classes,
            s.total_classes,
            f"{round(s.percentage, 2)}%",
        ])
        total_classes  += s.total_classes
        total_attended += s.attended_classes

    overall_pct = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )
    writer.writerow(["OVERALL", total_attended, total_classes, f"{overall_pct}%"])
    writer.writerow([])

    # Day-wise detail
    writer.writerow(["Date", "Subject", "Time Slot", "Status"])
    for r in day_rows:
        writer.writerow([
            str(r.date),
            r.subject,
            r.time_slot or "",
            (r.status.value if hasattr(r.status, "value") else str(r.status)).capitalize(),
        ])

    output.seek(0)
    filename = f"attendance_{regnum}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ══════════════════════════════════════════════════════════════════
# POST /attendance/unlock
# ══════════════════════════════════════════════════════════════════

@router.post("/unlock", status_code=status.HTTP_200_OK)
def unlock_attendance(
    body: UnlockBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """
    Verify a faculty member's password before allowing attendance edits.

    Payload:  { "password": "plain-text-password" }   ← only field; no faculty_id
    Success:  { "status": "unlocked" }                (HTTP 200)
    Failure:  HTTP 401  "Invalid password"
              HTTP 404  "User not found"

    FIX HISTORY:
      v1 → v2: Removed faculty_id from payload. Was doing User.id == int(faculty_id)
               which Postgres rejected with "operator does not exist: uuid = integer".
               Now resolves identity from the JWT token via get_current_user (UUID-safe).

      v2 → v3: 422 Unprocessable Entity fix. Was using `password: str = Body(...)` which
               expects a raw string body. Frontend sends JSON { "password": "..." } so
               FastAPI couldn't bind the field → 422. Fix: use Pydantic model (UnlockBody)
               so FastAPI parses the JSON object correctly. UnlockBody.password is a str.
    """
    logger.info(
        f"[attendance/unlock] unlock attempt — "
        f"user={current_user.email} id={current_user.id}"
    )

    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        logger.warning(
            f"[attendance/unlock] user not found in student_db for id={current_user.id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    if not verify_password(body.password, user.hashed_password):
        logger.warning(
            f"[attendance/unlock] wrong password for user={current_user.email}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password.",
        )

    logger.info(
        f"[attendance/unlock] unlocked successfully for user={current_user.email}"
    )
    return {"status": "unlocked"}


# ══════════════════════════════════════════════════════════════════
# POST /attendance/mark
# ══════════════════════════════════════════════════════════════════

@router.post("/mark", status_code=status.HTTP_200_OK)
def mark_attendance(
    body: MarkAttendanceBody,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_student_db),
):
    """
    Faculty / Admin only.
    Inserts or updates DayAttendance for every record in the payload,
    then recalculates SubjectAttendance totals for each student.
    """
    logger.info(
        f"[attendance/mark] user={current_user.email} role={current_user.role} "
        f"section='{body.section_id}' subject='{body.subject}' "
        f"date='{body.date}' time_slot='{body.time_slot}' "
        f"records={len(body.attendance)}"
    )

    try:
        parsed_date = date_type.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format '{body.date}'. Expected YYYY-MM-DD.",
        )

    if not body.attendance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="attendance list cannot be empty.",
        )

    present_count = 0
    absent_count  = 0
    affected_reg_nos: set = set()

    # ── Pass 1: upsert every DayAttendance row ────────────────────────────────
    for rec in body.attendance:
        if not rec.student_id:
            continue

        reg_no     = rec.student_id
        att_status = _resolve_att_status(rec.status)
        is_present = att_status == AttendanceStatus.present

        existing_day = db.query(DayAttendance).filter(
            DayAttendance.registration_number == reg_no,
            DayAttendance.date                == parsed_date,
            DayAttendance.subject             == body.subject,
            DayAttendance.time_slot           == body.time_slot,
        ).first()

        if existing_day:
            existing_day.status    = att_status
            existing_day.marked_by = current_user.id
        else:
            db.add(DayAttendance(
                registration_number = reg_no,
                date                = parsed_date,
                time_slot           = body.time_slot,
                subject             = body.subject,
                section             = body.section_id,
                status              = att_status,
                marked_by           = current_user.id,
            ))

        affected_reg_nos.add(reg_no)

        if is_present:
            present_count += 1
        else:
            absent_count += 1

    db.flush()

    # ── Pass 2: recompute SubjectAttendance from DayAttendance (source of truth)
    for reg_no in affected_reg_nos:
        all_day_rows = db.query(DayAttendance).filter(
            DayAttendance.registration_number == reg_no,
            DayAttendance.subject             == body.subject,
        ).all()

        real_total   = len(all_day_rows)
        real_present = sum(
            1 for r in all_day_rows
            if (r.status.value if hasattr(r.status, "value") else str(r.status)).lower() == "present"
        )
        real_pct = round(
            (real_present / real_total * 100) if real_total > 0 else 0.0, 2
        )

        subj = db.query(SubjectAttendance).filter(
            SubjectAttendance.registration_number == reg_no,
            SubjectAttendance.subject             == body.subject,
        ).first()

        if subj:
            subj.total_classes    = real_total
            subj.attended_classes = real_present
            subj.percentage       = real_pct
        else:
            db.add(SubjectAttendance(
                registration_number = reg_no,
                subject             = body.subject,
                total_classes       = real_total,
                attended_classes    = real_present,
                percentage          = real_pct,
            ))

        logger.debug(
            f"[attendance/mark] SubjectAttendance recomputed — "
            f"reg_no={reg_no} subject={body.subject} "
            f"total={real_total} present={real_present} pct={real_pct}%"
        )

    db.commit()
    logger.info(
        f"[attendance/mark] committed — present={present_count} absent={absent_count} "
        f"students_updated={len(affected_reg_nos)}"
    )

    return {
        "success": True,
        "message": "Attendance marked successfully.",
        "total":   len(body.attendance),
        "present": present_count,
        "absent":  absent_count,
    }


# ══════════════════════════════════════════════════════════════════
# GET /attendance/faculty/schedule   ← CRITICAL FIX (was missing)
# ══════════════════════════════════════════════════════════════════

@router.get("/faculty/schedule", status_code=status.HTTP_200_OK)
def get_faculty_schedule(
    day: Optional[str] = Query(None, description="Filter by day e.g. 'Monday'"),
    current_user: User = Depends(require_faculty_or_admin),
    faculty_db: Session = Depends(get_faculty_db),
    student_db: Session = Depends(get_student_db),
):
    """
    Faculty / Admin only.
    Returns the timetable/schedule for the logged-in faculty member.

    Primary source: faculty_db (FacultyTimetable joined to FacultyMember via email).
    Fallback source: student_db Timetable (filtered by faculty_id == current_user.id).

    Response:
    [
      {
        "id":         1,
        "day":        "Monday",
        "subject":    "HCI",
        "section":    "CSE06",
        "year":       4,
        "time_slot":  "9:00-10:40",
        "source":     "faculty_db"   // or "student_db"
      }
    ]
    """
    logger.info(
        f"[attendance/faculty/schedule] user={current_user.email} "
        f"role={current_user.role} day={day}"
    )

    DAYS_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def sort_key(entry):
        d = (entry.get("day") or "").strip()
        t = (entry.get("time_slot") or "").strip()
        return (DAYS_ORDER.index(d) if d in DAYS_ORDER else 99, _parse_start_time(t))

    # ── PRIMARY: faculty_db ───────────────────────────────────────────────────
    try:
        faculty_member = faculty_db.query(FacultyMember).filter(
            FacultyMember.email == current_user.email
        ).first()

        logger.info(
            f"[attendance/faculty/schedule] faculty_db lookup: "
            f"email={current_user.email} found={faculty_member is not None}"
        )

        if faculty_member:
            q = faculty_db.query(FacultyTimetable).filter(
                FacultyTimetable.faculty_id == faculty_member.id
            )
            if day:
                q = q.filter(func.trim(FacultyTimetable.day) == day.strip())

            rows = q.all()
            logger.info(
                f"[attendance/faculty/schedule] faculty_db rows={len(rows)} "
                f"for faculty_id={faculty_member.id}"
            )

            if rows:
                results = [
                    {
                        "id":        r.id,
                        "day":       (r.day       or "").strip(),
                        "subject":   (r.subject   or "").strip(),
                        "section":   _normalize_section((r.section or "").strip()),
                        "year":      r.year,
                        "time_slot": (r.time_slot or "").strip(),
                        "source":    "faculty_db",
                    }
                    for r in rows
                ]
                results.sort(key=sort_key)
                logger.info(
                    f"[attendance/faculty/schedule] returning {len(results)} slots "
                    f"from faculty_db"
                )
                return results

        logger.warning(
            f"[attendance/faculty/schedule] no rows in faculty_db for "
            f"{current_user.email} — trying student_db Timetable fallback"
        )

    except Exception as exc:
        logger.error(
            f"[attendance/faculty/schedule] faculty_db error: {exc} — "
            f"falling back to student_db"
        )

    # ── FALLBACK: student_db Timetable ────────────────────────────────────────
    try:
        q2 = student_db.query(Timetable).filter(
            Timetable.faculty_id == current_user.id
        )
        if day:
            q2 = q2.filter(Timetable.day == day.strip())

        rows2 = q2.all()
        logger.info(
            f"[attendance/faculty/schedule] student_db Timetable rows={len(rows2)} "
            f"for faculty_id={current_user.id}"
        )

        if rows2:
            def _time_slot(r):
                st = str(r.start_time or "").strip()
                et = str(r.end_time   or "").strip()
                return f"{st}-{et}" if st and et else st or et

            results2 = [
                {
                    "id":        str(r.id),
                    "day":       (r.day     or "").strip(),
                    "subject":   (r.subject or "").strip(),
                    "section":   _normalize_section((r.section or "").strip()),
                    "year":      r.year,
                    "time_slot": _time_slot(r),
                    "source":    "student_db",
                }
                for r in rows2
            ]
            results2.sort(key=sort_key)
            logger.info(
                f"[attendance/faculty/schedule] returning {len(results2)} slots "
                f"from student_db fallback"
            )
            return results2

    except Exception as exc2:
        logger.error(
            f"[attendance/faculty/schedule] student_db fallback error: {exc2}"
        )

    logger.warning(
        f"[attendance/faculty/schedule] BOTH sources empty for "
        f"user={current_user.email} (faculty_id={current_user.id}). "
        f"Check that the timetable has been loaded into the DB."
    )
    return []


# ══════════════════════════════════════════════════════════════════
# GET /attendance/faculty/students
# ══════════════════════════════════════════════════════════════════

@router.get("/faculty/students", status_code=status.HTTP_200_OK)
def get_faculty_students(
    section:      str           = Query(..., description="Section e.g. CSE06, CSE-06, CSE 06"),
    year:         Optional[int] = Query(None, description="Academic year filter"),
    current_user: User          = Depends(require_faculty_or_admin),
    master_db:    Session       = Depends(get_master_db),
):
    """
    Faculty / Admin only.
    Returns the student list for a given section (and optional year).

    Section is normalized on both the incoming value and the DB column so that
    'CSE06', 'CSE-06', and 'CSE 06' all match correctly.

    Response: [{ id, registration_number, regnum, name, fullname }]
    """
    logger.info(
        f"[attendance/faculty/students] raw section='{section}' year={year} "
        f"user={current_user.email} role={current_user.role}"
    )

    normalized_section = _normalize_section(section)
    logger.info(
        f"[attendance/faculty/students] normalized section='{normalized_section}'"
    )

    try:
        total_rows = master_db.query(MasterStudent).count()
        logger.info(
            f"[attendance/faculty/students] MasterStudent total rows: {total_rows}"
        )
    except Exception as e:
        logger.error(f"[attendance/faculty/students] master_db unreachable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Master database error: {str(e)}",
        )

    sample_rows = master_db.query(MasterStudent.section, MasterStudent.year).limit(10).all()
    logger.info(
        f"[attendance/faculty/students] DB section samples (first 10): "
        f"{[(r.section, r.year) for r in sample_rows]}"
    )

    query = master_db.query(MasterStudent).filter(
        _normalized_master_col() == normalized_section
    )

    if year is not None:
        query = query.filter(MasterStudent.year == year)
        logger.info(f"[attendance/faculty/students] year filter applied: {year}")

    students = query.order_by(MasterStudent.regnum).all()
    logger.info(f"[attendance/faculty/students] students fetched: {len(students)}")

    if not students:
        section_only = master_db.query(MasterStudent).filter(
            _normalized_master_col() == normalized_section
        ).count()
        year_only = (
            master_db.query(MasterStudent).filter(MasterStudent.year == year).count()
            if year is not None else "N/A"
        )
        logger.warning(
            f"[attendance/faculty/students] EMPTY — "
            f"section-only={section_only} year-only={year_only} "
            f"normalized='{normalized_section}' year={year}"
        )

    return [
        {
            "id":                  s.regnum,
            "registration_number": s.regnum,
            "regnum":              s.regnum,
            "name":                (
                getattr(s, "fullname", None) or
                getattr(s, "name", None) or
                ""
            ).strip(),
            "fullname":            (
                getattr(s, "fullname", None) or
                getattr(s, "name", None) or
                ""
            ).strip(),
        }
        for s in students
    ]


# ══════════════════════════════════════════════════════════════════
# GET /attendance/check
# ══════════════════════════════════════════════════════════════════

@router.get("/check", status_code=status.HTTP_200_OK)
def check_attendance(
    section:      str     = Query(..., description="Section e.g. CSE06, CSE-06, CSE 06"),
    subject:      str     = Query(..., description="Subject name"),
    date:         str     = Query(..., description="Date in YYYY-MM-DD format"),
    time_slot:    str     = Query(..., description="Time slot e.g. 9:00-10:40"),
    current_user: User    = Depends(require_faculty_or_admin),
    db:           Session = Depends(get_student_db),
    master_db:    Session = Depends(get_master_db),
):
    """
    Faculty / Admin only.
    Checks whether attendance has already been marked for a given
    section / subject / date / time_slot combination.

    Response: { "exists": bool, "data": [{ "student_id": str, "status": str }] }
    """
    logger.info(
        f"[attendance/check] section='{section}' subject='{subject}' "
        f"date='{date}' time_slot='{time_slot}' user={current_user.email}"
    )

    try:
        parsed_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format '{date}'. Expected YYYY-MM-DD.",
        )

    normalized_section = _normalize_section(section)
    logger.info(f"[attendance/check] normalized section='{normalized_section}'")

    section_students = master_db.query(MasterStudent).filter(
        _normalized_master_col() == normalized_section
    ).all()

    logger.info(
        f"[attendance/check] section students found: {len(section_students)}"
    )

    if not section_students:
        logger.warning(
            f"[attendance/check] no students for section='{normalized_section}'"
        )
        return {"exists": False, "data": []}

    section_regnums = {s.regnum for s in section_students}

    records = (
        db.query(DayAttendance)
        .filter(
            DayAttendance.registration_number.in_(section_regnums),
            DayAttendance.subject   == subject,
            DayAttendance.date      == parsed_date,
            DayAttendance.time_slot == time_slot,
        )
        .all()
    )

    logger.info(
        f"[attendance/check] DayAttendance records: {len(records)} "
        f"subject='{subject}' date={parsed_date} time_slot='{time_slot}'"
    )

    if not records:
        return {"exists": False, "data": []}

    data = [
        {
            "student_id": row.registration_number,
            "status": (
                row.status.value if hasattr(row.status, "value") else str(row.status)
            ).lower(),
        }
        for row in records
    ]

    return {"exists": True, "data": data}


# ══════════════════════════════════════════════════════════════════
# GET /attendance/student/{student_id}
# ══════════════════════════════════════════════════════════════════

@router.get("/student/{student_id}", status_code=status.HTTP_200_OK)
def get_student_attendance(
    student_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """
    - Student: can only retrieve their own attendance (student_id must match
      their registration_number).
    - Faculty / Admin: can retrieve any student's attendance.

    Returns:
    {
      registration_number,
      overall_percentage,
      total_classes,
      attended_classes,
      subjects: [ { subject, present, total, percentage } ],
      records:  [ { date, subject, time_slot, status } ]
    }
    """
    role = current_user.role
    is_student    = role == UserRole.student
    is_privileged = role in (UserRole.faculty, UserRole.admin)

    logger.info(
        f"[attendance/student/{student_id}] requester={current_user.email} "
        f"role={role}"
    )

    if is_student:
        own_reg = current_user.registration_number
        if own_reg != student_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students can only view their own attendance.",
            )
    elif not is_privileged:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    subject_rows = (
        db.query(SubjectAttendance)
        .filter(SubjectAttendance.registration_number == student_id)
        .all()
    )

    subjects = [
        {
            "subject":    row.subject,
            "present":    row.attended_classes,
            "total":      row.total_classes,
            "percentage": row.percentage,
        }
        for row in subject_rows
    ]

    total_classes    = sum(r["total"]   for r in subjects)
    attended_classes = sum(r["present"] for r in subjects)
    overall_percentage = round(
        (attended_classes / total_classes * 100) if total_classes > 0 else 0.0,
        2,
    )

    day_rows = (
        db.query(DayAttendance)
        .filter(DayAttendance.registration_number == student_id)
        .order_by(DayAttendance.date.desc())
        .all()
    )

    records = [
        {
            "date":      str(row.date),
            "subject":   row.subject,
            "time_slot": row.time_slot or "",
            "status":    (
                row.status.value if hasattr(row.status, "value") else str(row.status)
            ).capitalize(),
        }
        for row in day_rows
    ]

    logger.info(
        f"[attendance/student/{student_id}] "
        f"subjects={len(subjects)} day_records={len(records)} "
        f"overall={overall_percentage}%"
    )

    return {
        "registration_number": student_id,
        "overall_percentage":  overall_percentage,
        "total_classes":       total_classes,
        "attended_classes":    attended_classes,
        "subjects":            subjects,
        "records":             records,
    }


# ══════════════════════════════════════════════════════════════════
# GET /attendance/me/overview
# ══════════════════════════════════════════════════════════════════

@router.get("/me/overview", status_code=status.HTTP_200_OK)
def get_student_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """
    Student: Returns overall attendance % + subject-wise + grouped day-wise list.

    FIX: Previously only returned subjects that already had a SubjectAttendance row,
         so a fresh student saw an empty list.  Now we also query the Timetable for
         the student's section + year and merge the two sets:
           * Subject exists in SubjectAttendance  -> real counts
           * Subject exists only in Timetable     -> zeroed placeholder row
         This guarantees every subject on the student's timetable always appears.
    """
    reg_no  = current_user.registration_number
    section = current_user.section
    year    = current_user.year
    logger.info(
        f"[attendance/me/overview] user={current_user.email} "
        f"reg_no={reg_no} section={section} year={year}"
    )

    if not reg_no:
        logger.warning(
            f"[attendance/me/overview] no registration_number for {current_user.email}"
        )
        return {
            "percentage":       0.0,
            "total_classes":    0,
            "attended_classes": 0,
            "subjects":         [],
            "days":             [],
        }

    subjects_db = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()
    attendance_map = {r.subject: r for r in subjects_db}
    logger.info(
        f"[attendance/me/overview] {len(attendance_map)} SubjectAttendance rows for {reg_no}"
    )

    timetable_subjects: set = set()
    if section and year is not None:
        try:
            rows = (
                db.query(Timetable.subject)
                .filter(
                    func.replace(
                        func.replace(func.upper(Timetable.section), " ", ""),
                        "-", ""
                    ) == _normalize_section(section),
                    Timetable.year == year,
                )
                .distinct()
                .all()
            )
            timetable_subjects = {r[0].strip() for r in rows if r[0]}
            logger.info(
                f"[attendance/me/overview] {len(timetable_subjects)} timetable subjects "
                f"for section={section} year={year}: {sorted(timetable_subjects)}"
            )
        except Exception as exc:
            logger.warning(
                f"[attendance/me/overview] timetable query failed ({exc}); "
                f"falling back to attendance records only"
            )

    all_subjects = timetable_subjects | set(attendance_map.keys())

    subject_list = []
    for subj in sorted(all_subjects):
        if subj in attendance_map:
            r = attendance_map[subj]
            subject_list.append({
                "subject":    r.subject,
                "present":    r.attended_classes,
                "total":      r.total_classes,
                "percentage": round(r.percentage, 2),
            })
        else:
            subject_list.append({
                "subject":    subj,
                "present":    0,
                "total":      0,
                "percentage": 0.0,
            })

    total_classes  = sum(s["total"]   for s in subject_list)
    total_attended = sum(s["present"] for s in subject_list)
    overall_pct    = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )

    day_records = (
        db.query(DayAttendance)
        .filter(DayAttendance.registration_number == reg_no)
        .order_by(DayAttendance.date.desc())
        .all()
    )

    from collections import OrderedDict
    grouped: dict = OrderedDict()
    for rec in day_records:
        key = str(rec.date)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append({
            "time_slot": rec.time_slot or "",
            "subject":   rec.subject,
            "status":    (
                rec.status.value if hasattr(rec.status, "value") else str(rec.status)
            ),
        })

    days = [{"date": d, "classes": cls} for d, cls in grouped.items()]

    logger.info(
        f"[attendance/me/overview] returning {len(subject_list)} subjects "
        f"({len(attendance_map)} with data, "
        f"{len(all_subjects) - len(attendance_map)} zeroed) "
        f"overall={overall_pct}%"
    )

    return {
        "percentage":       overall_pct,
        "total_classes":    total_classes,
        "attended_classes": total_attended,
        "subjects":         subject_list,
        "days":             days,
    }


# ══════════════════════════════════════════════════════════════════
# GET /attendance/me
# ══════════════════════════════════════════════════════════════════

@router.get("/me", status_code=status.HTTP_200_OK)
def get_my_attendance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """
    Student: Get subject-wise attendance list.

    FIX: Same timetable-merge as /me/overview — returns zeroed rows for
         subjects not yet tracked so the frontend always shows the full list.
    """
    reg_no  = current_user.registration_number
    section = current_user.section
    year    = current_user.year
    logger.info(f"[attendance/me] user={current_user.email} reg_no={reg_no}")

    if not reg_no:
        return []

    records = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()
    attendance_map = {r.subject: r for r in records}

    timetable_subjects: set = set()
    if section and year is not None:
        try:
            rows = (
                db.query(Timetable.subject)
                .filter(
                    func.replace(
                        func.replace(func.upper(Timetable.section), " ", ""),
                        "-", ""
                    ) == _normalize_section(section),
                    Timetable.year == year,
                )
                .distinct()
                .all()
            )
            timetable_subjects = {r[0].strip() for r in rows if r[0]}
        except Exception as exc:
            logger.warning(f"[attendance/me] timetable query failed ({exc}); using records only")

    all_subjects = timetable_subjects | set(attendance_map.keys())

    result = []
    for subj in sorted(all_subjects):
        if subj in attendance_map:
            r = attendance_map[subj]
            result.append({
                "subject":    r.subject,
                "present":    r.attended_classes,
                "total":      r.total_classes,
                "percentage": round(r.percentage, 2),
            })
        else:
            result.append({
                "subject":    subj,
                "present":    0,
                "total":      0,
                "percentage": 0.0,
            })
    return result


# ══════════════════════════════════════════════════════════════════
# GET /attendance/me/daily
# ══════════════════════════════════════════════════════════════════

@router.get("/me/daily", status_code=status.HTTP_200_OK)
def get_my_daily_attendance(
    subject:   Optional[str]       = Query(None),
    from_date: Optional[date_type] = Query(None),
    to_date:   Optional[date_type] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """Student: Get day-wise attendance, optionally filtered by subject/date range."""
    reg_no = current_user.registration_number
    logger.info(
        f"[attendance/me/daily] user={current_user.email} reg_no={reg_no} "
        f"subject={subject} from={from_date} to={to_date}"
    )

    if not reg_no:
        return []

    query = db.query(DayAttendance).filter(
        DayAttendance.registration_number == reg_no
    )
    if subject:
        query = query.filter(DayAttendance.subject == subject)
    if from_date:
        query = query.filter(DayAttendance.date >= from_date)
    if to_date:
        query = query.filter(DayAttendance.date <= to_date)

    records = query.order_by(DayAttendance.date.desc()).all()

    return [
        {
            "date":      str(r.date),
            "subject":   r.subject,
            "time_slot": r.time_slot or "",
            "status":    (
                r.status.value if hasattr(r.status, "value") else str(r.status)
            ).capitalize(),
        }
        for r in records
    ]


# ══════════════════════════════════════════════════════════════════
# GET /attendance/me/summary
# ══════════════════════════════════════════════════════════════════

@router.get("/me/summary", status_code=status.HTTP_200_OK)
def get_my_attendance_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_student_db),
):
    """Student: Get attendance summary with computed overall percentage."""
    reg_no = current_user.registration_number
    logger.info(
        f"[attendance/me/summary] user={current_user.email} reg_no={reg_no}"
    )

    if not reg_no:
        return {
            "studentId":         current_user.email,
            "studentName":       current_user.name,
            "overallPercentage": 0.0,
            "subjects":          [],
        }

    records = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()

    total_classes  = sum(r.total_classes    for r in records)
    total_attended = sum(r.attended_classes for r in records)
    overall_pct    = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )

    return {
        "studentId":         reg_no,
        "studentName":       current_user.name,
        "overallPercentage": overall_pct,
        "subjects": [
            {
                "subject":    r.subject,
                "present":    r.attended_classes,
                "total":      r.total_classes,
                "percentage": round(r.percentage, 2),
            }
            for r in records
        ],
    }


# ══════════════════════════════════════════════════════════════════
# GET /attendance/admin/overall  (AUTH REQUIRED)
# ══════════════════════════════════════════════════════════════════

@router.get("/admin/overall", status_code=status.HTTP_200_OK)
def get_admin_overall(
    section:      Optional[str] = Query(None, description="Filter by section e.g. CSE06"),
    year:         Optional[int] = Query(None, description="Filter by academic year"),
    current_user: User          = Depends(require_faculty_or_admin),
    master_db:    Session       = Depends(get_master_db),
    student_db:   Session       = Depends(get_student_db),
):
    """
    Admin / Faculty only.
    Returns overall attendance summary for all students, optionally filtered
    by section and/or year.

    Response: [
      {
        "name":     str,
        "regnum":   str,
        "year":     int | null,
        "section":  str,
        "subjects": { subject: { present, total, percentage } },
        "avg":      float
      }
    ]
    """
    logger.info(
        f"[attendance/admin/overall] user={current_user.email} "
        f"role={current_user.role} section={section} year={year}"
    )

    query = master_db.query(MasterStudent)

    if section:
        normalized = _normalize_section(section)
        query = query.filter(_normalized_master_col() == normalized)
        logger.info(f"[attendance/admin/overall] section filter: '{normalized}'")

    if year is not None:
        query = query.filter(MasterStudent.year == year)
        logger.info(f"[attendance/admin/overall] year filter: {year}")

    students = query.order_by(MasterStudent.regnum).all()
    logger.info(f"[attendance/admin/overall] students fetched: {len(students)}")

    result = []
    for s in students:
        summary = _build_student_attendance_summary(
            regnum=s.regnum,
            master_db=master_db,
            student_db=student_db,
        )
        result.append(summary)

    return result


# ══════════════════════════════════════════════════════════════════
# GET /attendance/admin/overall-dev  (NO AUTH)
# ══════════════════════════════════════════════════════════════════

@router.get("/admin/overall-dev", status_code=status.HTTP_200_OK)
def get_admin_overall_dev(
    section:    Optional[str] = Query(None, description="Filter by section e.g. CSE06"),
    year:       Optional[int] = Query(None, description="Filter by academic year"),
    master_db:  Session       = Depends(get_master_db),
    student_db: Session       = Depends(get_student_db),
):
    """
    DEV / no-auth version of /admin/overall.
    Same logic, same response format — authentication removed for development use.

    Response: [
      {
        "name":     str,
        "regnum":   str,
        "year":     int | null,
        "section":  str,
        "subjects": { subject: { present, total, percentage } },
        "avg":      float
      }
    ]
    """
    logger.info(
        f"[attendance/admin/overall-dev] section={section} year={year} (no-auth)"
    )

    query = master_db.query(MasterStudent)

    if section:
        normalized = _normalize_section(section)
        query = query.filter(_normalized_master_col() == normalized)
        logger.info(f"[attendance/admin/overall-dev] section filter: '{normalized}'")

    if year is not None:
        query = query.filter(MasterStudent.year == year)
        logger.info(f"[attendance/admin/overall-dev] year filter: {year}")

    students = query.order_by(MasterStudent.regnum).all()
    logger.info(f"[attendance/admin/overall-dev] students fetched: {len(students)}")

    result = []
    for s in students:
        summary = _build_student_attendance_summary(
            regnum=s.regnum,
            master_db=master_db,
            student_db=student_db,
        )
        result.append(summary)

    return result


# ══════════════════════════════════════════════════════════════════
# GET /attendance/download/{regnum}  (AUTH REQUIRED, role-gated)
# ══════════════════════════════════════════════════════════════════

@router.get("/download/{regnum}")
def download_student_csv(
    regnum:       str,
    current_user: User    = Depends(get_current_user),
    master_db:    Session = Depends(get_master_db),
    student_db:   Session = Depends(get_student_db),
):
    """
    Download attendance CSV for a student.

    Access rules:
      - Student role: may only download their own CSV (regnum must match
        their registration_number).
      - Faculty / Admin role: may download any student's CSV.

    Response: text/csv file download
    """
    role = current_user.role
    logger.info(
        f"[attendance/download/{regnum}] requester={current_user.email} role={role}"
    )

    if role == UserRole.student:
        own_reg = current_user.registration_number
        if own_reg != regnum:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Students can only download their own attendance CSV.",
            )
    elif role not in (UserRole.faculty, UserRole.admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    logger.info(f"[attendance/download/{regnum}] generating CSV")
    return _generate_csv_for_regnum(
        regnum=regnum,
        master_db=master_db,
        student_db=student_db,
    )


# ══════════════════════════════════════════════════════════════════
# GET /attendance/download-dev/{regnum}  (NO AUTH)
# ══════════════════════════════════════════════════════════════════

@router.get("/download-dev/{regnum}")
def download_student_csv_dev(
    regnum:     str,
    master_db:  Session = Depends(get_master_db),
    student_db: Session = Depends(get_student_db),
):
    """
    DEV / no-auth version of /download/{regnum}.
    Same CSV generation logic — authentication removed for development use.

    Response: text/csv file download
    """
    logger.info(
        f"[attendance/download-dev/{regnum}] generating CSV (no-auth)"
    )
    return _generate_csv_for_regnum(
        regnum=regnum,
        master_db=master_db,
        student_db=student_db,
    )