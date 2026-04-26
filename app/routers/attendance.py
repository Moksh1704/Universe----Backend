"""
app/routers/attendance.py

Attendance router for UniVerse.
Uses student_db for all reads/writes.
Does NOT touch faculty_db.

Existing endpoints (unchanged):
  POST /attendance/bulk
  GET  /attendance/me
  GET  /attendance/me/daily
  GET  /attendance/me/summary
  GET  /attendance/student/{reg_no}
  GET  /attendance/daily/{reg_no}

New / fixed endpoints (to match frontend service calls):
  GET  /attendance/me/overview     – student overview: overall % + day-wise list
  GET  /attendance/faculty/schedule – faculty: get classes for a date (from student_db timetable)
  POST /attendance/submit          – faculty: submit bulk attendance (alias for /bulk)

FIX LOG:
  - get_students_for_section(): changed order_by(MasterStudent.name)
    → order_by(MasterStudent.regnum) so the DB-level sort matches
    the frontend's numeric-aware regnum sort.  Previously students
    arrived name-sorted from the DB and the frontend re-sorted, but
    only if regnum was present — a missing/null regnum caused the
    localeCompare fallback to treat all students as equal and preserve
    the name-sorted order from the DB.  Sorting by regnum at the DB
    level makes both layers consistent and removes the ambiguity.
"""
from typing import Optional, List
from datetime import date as date_type, datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_student_db, get_master_db
from app.models import User, SubjectAttendance, DayAttendance, UserRole, AttendanceStatus, Timetable
from app.models import MasterStudent
from app.schemas import (
    MarkAttendanceRequest,
    AttendanceResponse, DayAttendanceResponse, AttendanceSummaryResponse,
    MessageResponse,
)
from app.auth.dependencies import get_current_user, require_faculty_or_admin, require_student

router = APIRouter(prefix="/attendance", tags=["Attendance"])

# Backward-compat alias: existing code uses get_db
get_db = get_student_db


def normalize_section(section: str) -> str:
    """
    Standardize section format: remove spaces and uppercase.
    'CSE 06'  → 'CSE06'
    'cse06'   → 'CSE06'
    'CSE 6'   → 'CSE6'   (kept as-is if DB stores without leading zero)
    """
    if not section:
        return section
    return section.replace(" ", "").upper()


# ─── GET /attendance ─────────────────────────────────────────────────────────
@router.get("")
def get_my_attendance_by_email(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Student: Retrieve own attendance records.

    Flow:
      1. Extract authenticated user from token.
      2. Use registration_number stored on the User row (set at login).
      3. Query attendance table using registration_number.
    """
    reg_no = current_user.registration_number
    print(f"[FETCH ATTENDANCE FOR] user={current_user.email}  reg_no={reg_no}")

    if not reg_no:
        return {
            "registration_number": None,
            "attendance": [],
        }

    # Fetch all day-level attendance records for this student
    records = (
        db.query(DayAttendance)
        .filter(DayAttendance.registration_number == reg_no)
        .order_by(DayAttendance.date.desc())
        .all()
    )

    attendance_list = [
        {
            "subject": rec.subject,
            "date":    str(rec.date),
            "status":  (
                rec.status.value.capitalize()
                if hasattr(rec.status, "value")
                else str(rec.status).capitalize()
            ),
        }
        for rec in records
    ]

    return {
        "registration_number": reg_no,
        "attendance": attendance_list,
    }


# ─── POST /attendance/bulk ────────────────────────────────────────────────────
@router.post("/bulk")
def mark_bulk_attendance(
    payload: dict,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
):
    """
    Faculty: Submit attendance for all students in a class.

    Expected payload:
    {
        "subject":  "Data Structures",
        "date":     "2024-03-25T00:00:00.000Z",
        "students": [
            { "registration_number": "322506402355", "present": true },
            ...
        ]
    }
    """
    subject  = payload.get("subject")
    date_raw = payload.get("date")
    students = payload.get("students", [])

    if not subject or not date_raw or not students:
        raise HTTPException(status_code=400, detail="subject, date, and students are required")

    try:
        parsed_date = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_raw}")

    for s in students:
        reg_no  = s.get("registration_number")
        present = s.get("present", False)
        if not reg_no:
            continue

        att_status = AttendanceStatus.present if present else AttendanceStatus.absent

        existing_day = db.query(DayAttendance).filter(
            DayAttendance.registration_number == reg_no,
            DayAttendance.date               == parsed_date,
            DayAttendance.subject            == subject,
        ).first()

        if existing_day:
            old_status = (
                existing_day.status.value
                if hasattr(existing_day.status, "value")
                else existing_day.status
            )
            new_status = att_status.value if hasattr(att_status, "value") else att_status

            if old_status != new_status:
                existing_day.status    = att_status
                existing_day.marked_by = current_user.id

                record = db.query(SubjectAttendance).filter(
                    SubjectAttendance.registration_number == reg_no,
                    SubjectAttendance.subject             == subject,
                ).first()

                if record:
                    if old_status == "present" and new_status == "absent":
                        record.attended_classes = max(0, record.attended_classes - 1)
                    elif old_status == "absent" and new_status == "present":
                        record.attended_classes += 1

                    record.percentage = round(
                        (record.attended_classes / record.total_classes * 100)
                        if record.total_classes > 0 else 0.0, 2
                    )
        else:
            new_day = DayAttendance(
                registration_number = reg_no,
                date                = parsed_date,
                subject             = subject,
                status              = att_status,
                marked_by           = current_user.id,
            )
            db.add(new_day)

            record = db.query(SubjectAttendance).filter(
                SubjectAttendance.registration_number == reg_no,
                SubjectAttendance.subject             == subject,
            ).first()

            if not record:
                record = SubjectAttendance(
                    registration_number = reg_no,
                    subject             = subject,
                    total_classes       = 0,
                    attended_classes    = 0,
                    percentage          = 0.0,
                )
                db.add(record)

            record.total_classes += 1
            if present:
                record.attended_classes += 1
            record.percentage = round(
                (record.attended_classes / record.total_classes * 100)
                if record.total_classes > 0 else 0.0, 2
            )

    db.commit()
    return {"message": "Attendance saved successfully"}


# ─── POST /attendance/submit ──────────────────────────────────────────────────
# Frontend calls /attendance/submit – this is an alias for the bulk endpoint
# with a slightly different payload shape.
@router.post("/submit")
def submit_attendance(
    payload: dict,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
):
    """
    Frontend-facing submit endpoint.

    Expected payload:
    {
        "subject":    "Data Structures",
        "section":    "CSE 01",
        "year":       2,
        "date":       "2024-03-25",        // YYYY-MM-DD or ISO string
        "time_slot":  "9:00-10:40",
        "students":   [
            { "registration_number": "322506402355", "present": true },
            ...
        ]
    }
    """
    subject   = payload.get("subject")
    date_raw  = payload.get("date")
    students  = payload.get("students", [])

    if not subject or not date_raw or not students:
        raise HTTPException(status_code=400, detail="subject, date, and students are required")

    # Parse – accept both YYYY-MM-DD and ISO datetime strings
    try:
        if "T" in date_raw or "Z" in date_raw:
            parsed_date = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).date()
        else:
            parsed_date = date_type.fromisoformat(date_raw)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_raw}")

    for s in students:
        reg_no  = s.get("registration_number")
        present = s.get("present", False)
        if not reg_no:
            continue

        att_status = AttendanceStatus.present if present else AttendanceStatus.absent

        existing_day = db.query(DayAttendance).filter(
            DayAttendance.registration_number == reg_no,
            DayAttendance.date               == parsed_date,
            DayAttendance.subject            == subject,
        ).first()

        if existing_day:
            old_status = (
                existing_day.status.value
                if hasattr(existing_day.status, "value")
                else existing_day.status
            )
            new_status = att_status.value if hasattr(att_status, "value") else att_status

            if old_status != new_status:
                existing_day.status    = att_status
                existing_day.marked_by = current_user.id

                record = db.query(SubjectAttendance).filter(
                    SubjectAttendance.registration_number == reg_no,
                    SubjectAttendance.subject             == subject,
                ).first()
                if record:
                    if old_status == "present" and new_status == "absent":
                        record.attended_classes = max(0, record.attended_classes - 1)
                    elif old_status == "absent" and new_status == "present":
                        record.attended_classes += 1
                    record.percentage = round(
                        (record.attended_classes / record.total_classes * 100)
                        if record.total_classes > 0 else 0.0, 2
                    )
        else:
            new_day = DayAttendance(
                registration_number = reg_no,
                date                = parsed_date,
                subject             = subject,
                status              = att_status,
                marked_by           = current_user.id,
            )
            db.add(new_day)

            record = db.query(SubjectAttendance).filter(
                SubjectAttendance.registration_number == reg_no,
                SubjectAttendance.subject             == subject,
            ).first()
            if not record:
                record = SubjectAttendance(
                    registration_number = reg_no,
                    subject             = subject,
                    total_classes       = 0,
                    attended_classes    = 0,
                    percentage          = 0.0,
                )
                db.add(record)

            record.total_classes += 1
            if present:
                record.attended_classes += 1
            record.percentage = round(
                (record.attended_classes / record.total_classes * 100)
                if record.total_classes > 0 else 0.0, 2
            )

    db.commit()
    return {"message": "Attendance submitted successfully"}


# ─── GET /attendance/me/overview ─────────────────────────────────────────────
@router.get("/me/overview")
def get_student_overview(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Student: Returns overall attendance + subject-wise + grouped day-wise list.
    Uses registration_number stored on the User row (set at login).
    """
    import logging
    logger = logging.getLogger(__name__)

    reg_no = current_user.registration_number
    print(f"[FETCH ATTENDANCE FOR] user={current_user.email}  reg_no={reg_no}")
    logger.info(f"[Attendance] Fetching overview for reg_no={reg_no}, email={current_user.email}")

    if not reg_no:
        logger.warning(f"[Attendance] No registration_number for user {current_user.email}")
        return {
            "percentage": 0.0,
            "total_classes": 0,
            "attended_classes": 0,
            "subjects": [],
            "days": [],
        }

    # Subject totals
    subjects = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()
    logger.info(f"[Attendance] Found {len(subjects)} subject records for {reg_no}")

    total_classes  = sum(r.total_classes    for r in subjects)
    total_attended = sum(r.attended_classes for r in subjects)
    overall_pct    = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )

    subject_list = [
        {
            "subject":    r.subject,
            "present":    r.attended_classes,
            "total":      r.total_classes,
            "percentage": round(r.percentage, 2),
        }
        for r in subjects
    ]

    # Day-wise records grouped by date
    day_records = db.query(DayAttendance).filter(
        DayAttendance.registration_number == reg_no
    ).order_by(DayAttendance.date.desc()).all()
    logger.info(f"[Attendance] Found {len(day_records)} day records for {reg_no}")

    # Group by date
    from collections import OrderedDict
    grouped: dict = OrderedDict()
    for rec in day_records:
        date_key = str(rec.date)
        if date_key not in grouped:
            grouped[date_key] = []
        grouped[date_key].append({
            "time_slot": rec.time_slot or "",
            "subject":   rec.subject,
            "status":    rec.status.value if hasattr(rec.status, "value") else rec.status,
        })

    days = [{"date": d, "classes": cls} for d, cls in grouped.items()]

    return {
        "percentage":       overall_pct,
        "total_classes":    total_classes,
        "attended_classes": total_attended,
        "subjects":         subject_list,
        "days":             days,
    }


# ─── GET /attendance/me ───────────────────────────────────────────────────────
@router.get("/me", response_model=List[AttendanceResponse])
def get_my_attendance(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Student: Get own subject-wise attendance."""
    reg_no = current_user.registration_number
    print(f"[FETCH ATTENDANCE FOR] user={current_user.email}  reg_no={reg_no}")

    if not reg_no:
        return []

    records = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()

    return [AttendanceResponse.from_orm(r) for r in records]


# ─── GET /attendance/me/daily ─────────────────────────────────────────────────
@router.get("/me/daily", response_model=List[DayAttendanceResponse])
def get_my_daily_attendance(
    subject:   Optional[str]       = Query(None),
    from_date: Optional[date_type] = Query(None),
    to_date:   Optional[date_type] = Query(None),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reg_no = current_user.registration_number
    print(f"[FETCH ATTENDANCE FOR] user={current_user.email}  reg_no={reg_no}")

    if not reg_no:
        return []

    """Student: Get own day-wise attendance."""
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
    return [DayAttendanceResponse.from_orm(r) for r in records]


# ─── GET /attendance/me/summary ───────────────────────────────────────────────
@router.get("/me/summary", response_model=AttendanceSummaryResponse)
def get_my_attendance_summary(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Student: Get attendance summary with computed overall percentage."""
    reg_no = current_user.registration_number
    print(f"[FETCH ATTENDANCE FOR] user={current_user.email}  reg_no={reg_no}")

    if not reg_no:
        return AttendanceSummaryResponse(
            studentId=current_user.email,
            studentName=current_user.name,
            overallPercentage=0.0,
            subjects=[],
        )

    # Fetch attendance using reg_no
    records = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()

    # Calculate totals
    total_classes  = sum(r.total_classes for r in records)
    total_attended = sum(r.attended_classes for r in records)

    overall_pct = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0,
        2
    )

    return AttendanceSummaryResponse(
        studentId         = reg_no,
        studentName       = current_user.name,
        overallPercentage = overall_pct,
        subjects          = [AttendanceResponse.from_orm(r) for r in records],
    )


# ─── GET /attendance/student/{reg_no} ────────────────────────────────────────
@router.get("/student/{reg_no}", response_model=AttendanceSummaryResponse)
def get_student_attendance(
    reg_no: str,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    """Faculty/Admin: Get attendance summary for any student."""
    records = db.query(SubjectAttendance).filter(
        SubjectAttendance.registration_number == reg_no
    ).all()

    if not records:
        raise HTTPException(status_code=404, detail=f"No attendance records found for {reg_no}")

    total_classes  = sum(r.total_classes    for r in records)
    total_attended = sum(r.attended_classes for r in records)
    overall_pct    = round(
        (total_attended / total_classes * 100) if total_classes > 0 else 0.0, 2
    )

    # ✅ FIXED: look up student name from master_db using regnum
    student = master_db.query(MasterStudent).filter(
        MasterStudent.regnum == reg_no
    ).first()

    return AttendanceSummaryResponse(
        studentId         = reg_no,
        studentName       = (student.name or student.fullname or reg_no) if student else reg_no,
        overallPercentage = overall_pct,
        subjects          = [AttendanceResponse.from_orm(r) for r in records],
    )


# ─── GET /attendance/daily/{reg_no} ──────────────────────────────────────────
@router.get("/daily/{reg_no}", response_model=List[DayAttendanceResponse])
def get_student_daily_attendance(
    reg_no:  str,
    subject: Optional[str]       = Query(None),
    date:    Optional[date_type] = Query(None),
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
):
    """Faculty/Admin: Get day-wise attendance for any student."""
    query = db.query(DayAttendance).filter(
        DayAttendance.registration_number == reg_no
    )
    if subject:
        query = query.filter(DayAttendance.subject == subject)
    if date:
        query = query.filter(DayAttendance.date == date)

    records = query.order_by(DayAttendance.date.desc()).all()
    return [DayAttendanceResponse.from_orm(r) for r in records]


# ─── GET /attendance/faculty/students ────────────────────────────────────────
@router.get("/faculty/students")
def get_students_for_section(
    section: str = Query(...),
    year:    Optional[int] = Query(None),
    current_user: User = Depends(require_faculty_or_admin),
    master_db: Session = Depends(get_master_db),
):
    """
    Faculty: Get list of students for a given section (and optional year).
    Returns: [{ id, fullname, name, registration_number, regnum }]
    Used by attendance screen to populate student list.

    ✅ FIXED: now queries MasterStudent via master_db instead of User table.
    regnum is the source of truth for both id and registration_number.

    ✅ FIX (Problem 1 — student sort order):
    Changed order_by(MasterStudent.name) → order_by(MasterStudent.regnum).

    Why: The frontend sorts students by regnum using a numeric-aware algorithm.
    If the backend delivers them name-sorted AND regnum is null/empty for some
    students, the frontend sort degenerates (localeCompare('','') === 0) and
    preserves the backend's name order — which is wrong.  Sorting by regnum
    at the DB level means both layers agree even in degenerate cases.

    ✅ FIX (Problem 3 — full name display):
    Added "fullname" as an explicit field in the response so the frontend
    can display the student's full legal name instead of the nickname stored
    in "name".  The frontend's resolveDisplayName() prefers fullname over name.
    Both fields are returned so older callers using "name" are unaffected.
    """
    import logging
    logger = logging.getLogger(__name__)

    normalized_section = normalize_section(section)
    logger.info(f"[Attendance] faculty/students: section received='{section}', normalized='{normalized_section}'")

    # ✅ FIXED: robust DB-side normalization (same pattern as students.py)
    normalized_db_section = func.replace(
        func.replace(
            func.upper(MasterStudent.section),
            " ", ""
        ),
        "-", ""
    )

    query = master_db.query(MasterStudent).filter(
        normalized_db_section == normalized_section
    )
    if year:
        query = query.filter(MasterStudent.year == year)

    # ✅ FIX: sort by regnum (ascending) instead of name so DB order matches
    # the frontend's numeric-aware regnum sort.
    students = query.order_by(MasterStudent.regnum).all()

    logger.info(f"[Attendance] faculty/students: found {len(students)} students")

    # ✅ FIXED: regnum → both id and registration_number
    # ✅ FIX (Problem 3): "fullname" is now an explicit field so the frontend
    #    can display the full legal name instead of the nickname in "name".
    #    Both fields are preserved for backward compatibility.
    return [
        {
            "id":                  s.regnum,
            "fullname":            (s.fullname or "").strip(),           # full legal name
            "name":                s.name or s.fullname or "",           # nickname / short name
            "registration_number": s.regnum,
            # Also expose as "regnum" so the frontend field-normalisation
            # in AttendanceScreen.js (st.regnum || st.registration_number)
            # picks it up with the shorter key first.
            "regnum":              s.regnum,
        }
        for s in students
    ]


# ─── POST /attendance ─────────────────────────────────────────────────────────
# New clean endpoint matching the frontend contract exactly.
#
# Body:
# {
#   "date":       "YYYY-MM-DD",
#   "time_slot":  "9:00-10:40",
#   "subject":    "DBMS",
#   "section":    "CSE 06",
#   "year":       4,
#   "faculty_id": 1,
#   "records": [
#     { "student_id": "regnum_string", "status": "present" },
#     { "student_id": "regnum_string", "status": "absent"  }
#   ]
# }

from pydantic import BaseModel as _BaseModel

class _AttendanceRecord(_BaseModel):
    student_id: str
    status: str  # "present" | "absent"

class _MarkAttendanceBody(_BaseModel):
    date:       str
    time_slot:  str
    subject:    str
    section:    str
    year:       int
    faculty_id: int
    records:    list[_AttendanceRecord]


@router.post("")
def post_attendance(
    body: _MarkAttendanceBody,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
    master_db: Session = Depends(get_master_db),
):
    import logging
    logger = logging.getLogger(__name__)

    from datetime import date as date_type

    # Normalize section
    normalized_section = normalize_section(body.section)
    logger.info(f"[Attendance] POST: section='{body.section}'→'{normalized_section}'")

    # 1. Parse date
    try:
        parsed_date = date_type.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format '{body.date}'. Use YYYY-MM-DD.",
        )

    # 2. Validate input
    if not body.records:
        raise HTTPException(status_code=400, detail="records list cannot be empty.")

    for rec in body.records:
        if rec.status not in ("present", "absent"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{rec.status}'",
            )

    # ✅ 3. FETCH STUDENTS FROM MASTER TABLE — query by regnum
    student_id_set = {rec.student_id for rec in body.records}

    students = master_db.query(MasterStudent).filter(
        MasterStudent.regnum.in_(student_id_set)
    ).all()

    # ✅ FIXED: use regnum for found_ids (no more s.id)
    found_ids = {s.regnum for s in students}
    missing = student_id_set - found_ids

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Students not found: {', '.join(missing)}",
        )

    # ✅ 4. VALIDATE ALL STUDENTS IN SECTION — use master_db + regnum
    normalized_db_section = func.replace(
        func.replace(
            func.upper(MasterStudent.section),
            " ", ""
        ),
        "-", ""
    )

    all_section = master_db.query(MasterStudent).filter(
        MasterStudent.year == body.year,
        normalized_db_section == normalized_section,
    ).all()

    # ✅ FIXED: use regnum for all_section_ids (no more s.id)
    all_section_ids = {s.regnum for s in all_section}
    unmarked = all_section_ids - student_id_set

    if unmarked:
        raise HTTPException(
            status_code=422,
            detail=f"{len(unmarked)} students not marked",
        )

    # ✅ 5. MAP student_id (regnum) → registration_number (also regnum)
    reg_map = {s.regnum: s.regnum for s in students}

    # 6. SAVE ATTENDANCE
    for rec in body.records:
        reg_no = reg_map.get(rec.student_id)

        att_status = (
            AttendanceStatus.present
            if rec.status == "present"
            else AttendanceStatus.absent
        )

        existing = db.query(DayAttendance).filter(
            DayAttendance.registration_number == reg_no,
            DayAttendance.date == parsed_date,
            DayAttendance.subject == body.subject,
            DayAttendance.time_slot == body.time_slot,
        ).first()

        if existing:
            existing.status = att_status
            existing.marked_by = current_user.id

        else:
            db.add(DayAttendance(
                registration_number = reg_no,
                date                = parsed_date,
                time_slot           = body.time_slot,
                subject             = body.subject,
                section             = body.section,
                status              = att_status,
                marked_by           = current_user.id,
            ))

        # UPDATE SUBJECT ATTENDANCE
        subj = db.query(SubjectAttendance).filter(
            SubjectAttendance.registration_number == reg_no,
            SubjectAttendance.subject == body.subject,
        ).first()

        if not subj:
            subj = SubjectAttendance(
                registration_number = reg_no,
                subject             = body.subject,
                total_classes       = 0,
                attended_classes    = 0,
                percentage          = 0.0,
            )
            db.add(subj)

        subj.total_classes += 1
        if rec.status == "present":
            subj.attended_classes += 1

        subj.percentage = round(
            (subj.attended_classes / subj.total_classes * 100)
            if subj.total_classes > 0 else 0.0,
            2
        )

    db.commit()

    return {
        "message": "Attendance submitted successfully",
        "total": len(body.records),
        "present": sum(1 for r in body.records if r.status == "present"),
        "absent": sum(1 for r in body.records if r.status == "absent"),
    }