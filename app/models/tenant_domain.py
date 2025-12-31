# app/models/tenant_domain.py
from app.models.admission import Admission
from app.models.appointment import Appointment
from app.models.department import Department
from app.models.document import Document
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.patient_audit import PatientAuditLog
from app.models.prescription import Prescription, PrescriptionItem
from app.models.stock import StockItem
from app.models.tenant_role import TenantRole, TenantRolePermission, TenantUserRole
from app.models.vital import Vital

# Order matters: tables with no dependencies first, then tables that depend on them
# Foreign key dependencies:
# - Patient depends on Department
# - PatientAuditLog, Document, Admission depend on Patient
# - Appointment depends on Patient AND Admission (via linked_ipd_admission_id FK)
# - Vital depends on Patient, Appointment, AND Admission (must come after Admission and Appointment)
# - Prescription depends on Patient, Appointment, and Admission
# - PrescriptionItem depends on Prescription and StockItem
# - TenantRolePermission depends on TenantRole
# - TenantUserRole depends on TenantRole
# - Notification has no dependencies
TENANT_TABLES = [
    # Tables with no dependencies (create first)
    Department.__table__,
    StockItem.__table__,
    TenantRole.__table__,
    Notification.__table__,
    # Tables that depend on Department
    Patient.__table__,
    # Tables that depend on Patient only
    PatientAuditLog.__table__,
    Document.__table__,
    Admission.__table__,  # Must come before Appointment (Appointment has FK to Admission)
    Appointment.__table__,  # Depends on Patient AND Admission (via linked_ipd_admission_id)
    # Tables that depend on Patient, Appointment, AND Admission (must come after Admission and Appointment)
    Vital.__table__,
    Prescription.__table__,
    # Tables that depend on Prescription
    PrescriptionItem.__table__,
    # Tables that depend on TenantRole
    TenantRolePermission.__table__,
    TenantUserRole.__table__,
]
