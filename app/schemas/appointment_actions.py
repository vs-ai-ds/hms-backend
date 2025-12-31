# app/schemas/appointment_actions.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AppointmentCheckInRequest(BaseModel):
    """Request to check in a patient for an appointment."""

    pass


class AppointmentStartConsultationRequest(BaseModel):
    """Request to start consultation for an appointment."""

    pass


class AppointmentCompleteRequest(BaseModel):
    """Request to complete an appointment."""

    with_rx: bool = False  # Whether prescription was written
    closure_note: Optional[str] = None  # Optional note about closure


class AppointmentCancelRequest(BaseModel):
    """Request to cancel an appointment."""

    reason: str  # PATIENT_REQUEST, ADMITTED_TO_IPD, DOCTOR_UNAVAILABLE, OTHER
    note: Optional[str] = None  # Optional cancellation note


class AppointmentNoShowRequest(BaseModel):
    """Request to mark an appointment as no-show."""

    pass


class AppointmentRescheduleRequest(BaseModel):
    """Request to reschedule an appointment."""

    scheduled_at: datetime  # New scheduled date/time
