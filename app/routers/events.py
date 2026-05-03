"""
app/schemas/events.py

Event-related Pydantic v2 schemas.
"""
from __future__ import annotations

from datetime import date, time
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UpdateEventRequest(BaseModel):
    title:       Optional[str]  = Field(None, min_length=3, max_length=300)
    description: Optional[str]  = None
    date:        Optional[date] = None
    time:        Optional[time] = None
    venue:       Optional[str]  = None
    location:    Optional[str]  = None
    category:    Optional[str]  = None
    totalSlots:  Optional[int]  = Field(None, ge=1)
    form_url:    Optional[str]  = None

    @field_validator("date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            from datetime import date as _date
            try:
                return _date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"Invalid date format '{v}'. Expected YYYY-MM-DD.")
        return v

    @field_validator("time", mode="before")
    @classmethod
    def parse_time(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, str):
            for fmt in ("%H:%M:%S", "%H:%M"):
                try:
                    from datetime import datetime
                    return datetime.strptime(v, fmt).time()
                except ValueError:
                    continue
        return v


class EventResponse(BaseModel):
    id:           UUID
    title:        str
    description:  Optional[str]  = None
    date:         date
    time:         Optional[str]  = None
    venue:        Optional[str]  = None
    category:     str
    totalSlots:   int
    filledSlots:  int             = 0
    form_url:     Optional[str]  = None
    createdBy:    Optional[str]  = None
    isRegistered: bool            = False

    model_config = {"from_attributes": False}

    @classmethod
    def from_orm_with_user(cls, obj: Any, user_id: Optional[UUID] = None) -> "EventResponse":
        is_registered = False
        if user_id:
            try:
                is_registered = any(
                    str(s.id) == str(user_id)
                    for s in obj.registered_students
                )
            except Exception:
                is_registered = False

        filled = getattr(obj, "registered_count", None)
        if filled is None:
            try:
                filled = len(obj.registered_students)
            except Exception:
                filled = 0

        return cls(
            id=obj.id,
            title=obj.title,
            description=obj.description,
            date=obj.date,
            time=obj.time.strftime("%H:%M") if obj.time else None,
            venue=obj.venue,
            category=obj.category.value if hasattr(obj.category, "value") else obj.category,
            totalSlots=obj.total_slots,
            filledSlots=filled,
            form_url=getattr(obj, "form_url", None),
            createdBy=obj.creator.name if getattr(obj, "creator", None) else None,
            isRegistered=is_registered,
        )

    @classmethod
    def from_orm(cls, obj: Any) -> "EventResponse":
        return cls.from_orm_with_user(obj, None)