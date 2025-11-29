# app/models/tenant_domain.py
from app.models.patient import Patient
from app.models.appointment import Appointment
from app.models.prescription import Prescription, PrescriptionItem
from app.models.document import Document
from app.models.notification import Notification
from app.models.stock import StockItem
from app.models.vital import Vital

TENANT_TABLES = [
    Patient.__table__,
    Appointment.__table__,
    Prescription.__table__,
    PrescriptionItem.__table__,
    Document.__table__,
    Notification.__table__,
    StockItem.__table__,
    Vital.__table__,
]