# app/services/appointment_service.py
from uuid import UUID
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.appointment import Appointment, AppointmentStatus


def list_appointments(
    db: Session,
    *,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    doctor_id: UUID | None = None,
    status: AppointmentStatus | None = None,
) -> list[Appointment]:
    """
    Basic appointment listing helper with optional filters.
    """
    query = db.query(Appointment)

    if doctor_id is not None:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if status is not None:
        query = query.filter(Appointment.status == status)
    if from_date is not None:
        query = query.filter(Appointment.scheduled_at >= from_date)
    if to_date is not None:
        query = query.filter(Appointment.scheduled_at <= to_date)

    return query.order_by(Appointment.scheduled_at.desc()).all()