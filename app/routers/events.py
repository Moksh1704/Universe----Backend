from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Event, UserRole, event_registrations
from app.schemas import CreateEventRequest, UpdateEventRequest, EventResponse, MessageResponse
from app.auth.dependencies import get_current_user, require_faculty_or_admin, require_admin, require_student
from app.utils.files import save_upload_file

router = APIRouter(prefix="/events", tags=["Events"])


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN DEV ROUTES  (no authentication — for admin panel use only)
#  NOTE: These must be declared BEFORE /{event_id} wildcard routes so FastAPI
#        does not interpret "admin" as an event ID.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/admin/create", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event_admin(
    payload: CreateEventRequest,
    db: Session = Depends(get_db),
):
    """Admin dev route: Create event without authentication.

    Field mapping
    -------------
    location   -> venue        (frontend may send either name)
    totalSlots -> total_slots  (defaults to 100 if omitted)
    time       -> optional     (stored as None if not provided)
    """
    venue       = payload.location or payload.venue          # accept either field name
    total_slots = payload.totalSlots if payload.totalSlots is not None else 100

    event = Event(
        title=payload.title,
        description=payload.description,
        date=payload.date,
        time=payload.time,          # None is fine if the column is nullable
        venue=venue,
        category=payload.category,
        total_slots=total_slots,
        form_url=payload.form_url,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, None)


@router.get("/admin/list-dev", response_model=List[EventResponse])
def admin_list_events_dev(
    category: Optional[str] = Query(None),
    search:   Optional[str] = Query(None),
    upcoming: Optional[bool] = Query(None),
    page:     int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Admin dev route: Return all events without authentication."""
    from datetime import date
    query = db.query(Event)

    if category:
        query = query.filter(Event.category == category)
    if search:
        query = query.filter(
            (Event.title.ilike(f"%{search}%")) | (Event.description.ilike(f"%{search}%"))
        )
    if upcoming is True:
        query = query.filter(Event.date >= date.today())

    events = (
        query.order_by(Event.date.asc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    return [EventResponse.from_orm_with_user(e, None) for e in events]


@router.put("/admin/{event_id}", response_model=EventResponse)
def update_event_admin(
    event_id: str,
    payload: UpdateEventRequest,
    db: Session = Depends(get_db),
):
    """Admin dev route: Partial update of an event without authentication.
    Accepts 'location' (admin UI) and maps it to the 'venue' DB column.
    Only non-None fields are written — all others are left unchanged.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if payload.title       is not None: event.title       = payload.title
    if payload.description is not None: event.description = payload.description
    if payload.date        is not None: event.date        = payload.date
    if payload.time        is not None: event.time        = payload.time
    if payload.category    is not None: event.category    = payload.category
    if payload.totalSlots  is not None: event.total_slots = payload.totalSlots
    if payload.form_url    is not None: event.form_url    = payload.form_url

    # "location" (admin UI field name) takes precedence over "venue" if both sent
    new_venue = payload.location or payload.venue
    if new_venue is not None:
        event.venue = new_venue

    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, None)


@router.delete("/admin/{event_id}", response_model=MessageResponse)
def delete_event_admin(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Admin dev route: Delete event without authentication."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    db.delete(event)
    db.commit()
    return MessageResponse(message="Event deleted")


# ─────────────────────────────────────────────────────────────────────────────
#  USER ROUTES  (authenticated — for mobile app only)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: CreateEventRequest,
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
):
    """Create event (Faculty/Admin only)."""
    venue       = payload.location or payload.venue
    total_slots = payload.totalSlots if payload.totalSlots is not None else 100

    event = Event(
        title=payload.title,
        description=payload.description,
        date=payload.date,
        time=payload.time,
        venue=venue,
        category=payload.category,
        total_slots=total_slots,
        created_by=current_user.id,
        form_url=payload.form_url,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, current_user.id)


@router.post("/{event_id}/image", response_model=EventResponse)
async def upload_event_image(
    event_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(require_faculty_or_admin),
    db: Session = Depends(get_db),
):
    """Upload event banner image."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    url = await save_upload_file(file, folder="events")
    event.image_url = url
    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, current_user.id)


@router.get("", response_model=List[EventResponse])
def get_events(
    category: Optional[str] = Query(None),
    search:   Optional[str] = Query(None),
    upcoming: Optional[bool] = Query(None),
    page:     int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all events with optional filters (authenticated — mobile app)."""
    from datetime import date
    query = db.query(Event)

    if category:
        query = query.filter(Event.category == category)
    if search:
        query = query.filter(
            (Event.title.ilike(f"%{search}%")) | (Event.description.ilike(f"%{search}%"))
        )
    if upcoming is True:
        query = query.filter(Event.date >= date.today())

    events = (
        query.order_by(Event.date.asc())
        .offset((page - 1) * pageSize)
        .limit(pageSize)
        .all()
    )
    return [EventResponse.from_orm_with_user(e, current_user.id) for e in events]


@router.get("/my-registrations", response_model=List[EventResponse])
def get_my_registrations(
    current_user: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Student: Get all events the current user is registered for."""
    student = db.query(User).filter(User.id == current_user.id).first()
    return [EventResponse.from_orm_with_user(e, current_user.id) for e in student.registered_events]


@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get single event by ID."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return EventResponse.from_orm_with_user(event, current_user.id)


@router.post("/{event_id}/register", response_model=EventResponse)
def register_for_event(
    event_id: str,
    current_user: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Student: Register for an event."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    already = any(str(s.id) == str(current_user.id) for s in event.registered_students)
    if already:
        raise HTTPException(status_code=400, detail="Already registered for this event")

    if event.registered_count >= event.total_slots:
        raise HTTPException(status_code=400, detail="Event is full")

    event.registered_students.append(current_user)
    event.registered_count += 1
    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, current_user.id)


@router.delete("/{event_id}/register", response_model=EventResponse)
def cancel_event_registration(
    event_id: str,
    current_user: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Student: Cancel event registration."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    registered = any(str(s.id) == str(current_user.id) for s in event.registered_students)
    if not registered:
        raise HTTPException(status_code=400, detail="Not registered for this event")

    event.registered_students.remove(current_user)
    event.registered_count = max(0, event.registered_count - 1)
    db.commit()
    db.refresh(event)
    return EventResponse.from_orm_with_user(event, current_user.id)


@router.delete("/{event_id}", response_model=MessageResponse)
def delete_event(
    event_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin: Delete event (authenticated route — kept for completeness)."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    db.delete(event)
    db.commit()
    return MessageResponse(message="Event deleted")