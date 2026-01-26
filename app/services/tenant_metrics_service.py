# app/services/tenant_metrics_service.py
"""
Service to maintain platform-level aggregated metrics in public.tenant_metrics.
These metrics are updated when records are created/deleted across tenant schemas.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.tenant_metrics import TenantMetrics


def get_or_create_metrics(db: Session) -> TenantMetrics:
    """Get or create the single metrics row."""
    metrics_id = UUID("00000000-0000-0000-0000-000000000001")
    metrics = db.query(TenantMetrics).filter(TenantMetrics.id == metrics_id).first()
    if not metrics:
        metrics = TenantMetrics(id=metrics_id)
        db.add(metrics)
        db.commit()
        db.refresh(metrics)
    return metrics


def increment_patients(db: Session, count: int = 1) -> None:
    """Increment total_patients counter."""
    metrics = get_or_create_metrics(db)
    metrics.total_patients = (metrics.total_patients or 0) + count
    db.commit()


def increment_appointments(db: Session, count: int = 1) -> None:
    """Increment total_appointments counter."""
    metrics = get_or_create_metrics(db)
    metrics.total_appointments = (metrics.total_appointments or 0) + count
    db.commit()


def increment_prescriptions(db: Session, count: int = 1) -> None:
    """Increment total_prescriptions counter."""
    metrics = get_or_create_metrics(db)
    metrics.total_prescriptions = (metrics.total_prescriptions or 0) + count
    db.commit()


def increment_users(db: Session, count: int = 1) -> None:
    """Increment total_users counter."""
    metrics = get_or_create_metrics(db)
    metrics.total_users = (metrics.total_users or 0) + count
    db.commit()


def increment_tenants(db: Session, count: int = 1) -> None:
    """Increment total_tenants counter."""
    metrics = get_or_create_metrics(db)
    metrics.total_tenants = (metrics.total_tenants or 0) + count
    db.commit()


def recalculate_all_metrics(db: Session) -> None:
    """
    Recalculate all metrics by querying all tenant schemas.
    Use this for initial setup or to fix discrepancies.
    """
    from sqlalchemy import func

    from app.models.appointment import Appointment
    from app.models.patient import Patient
    from app.models.prescription import Prescription
    from app.models.tenant_global import Tenant
    from app.models.user import User

    metrics = get_or_create_metrics(db)

    # Count tenants
    metrics.total_tenants = db.query(func.count(Tenant.id)).scalar() or 0

    # Count users (all, not deleted)
    metrics.total_users = (
        db.query(func.count(User.id))
        .filter(User.tenant_id.isnot(None), User.is_deleted.is_(False))
        .scalar()
        or 0
    )

    # Aggregate across all tenant schemas
    total_patients = 0
    total_appointments = 0
    total_prescriptions = 0

    all_tenants = db.query(Tenant).all()
    conn = db.connection()
    original_path = conn.execute(text("SHOW search_path")).scalar()

    try:
        for tenant in all_tenants:
            try:
                conn.execute(text(f'SET search_path TO "{tenant.schema_name}", public'))

                patients_count = db.query(func.count(Patient.id)).scalar() or 0
                total_patients += patients_count

                appointments_count = db.query(func.count(Appointment.id)).scalar() or 0
                total_appointments += appointments_count

                prescriptions_count = (
                    db.query(func.count(Prescription.id)).scalar() or 0
                )
                total_prescriptions += prescriptions_count
            except Exception as e:
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(f"Could not query metrics for tenant {tenant.name}: {e}")
                continue
    finally:
        conn.execute(text(f"SET search_path TO {original_path}"))

    metrics.total_patients = total_patients
    metrics.total_appointments = total_appointments
    metrics.total_prescriptions = total_prescriptions

    db.commit()
