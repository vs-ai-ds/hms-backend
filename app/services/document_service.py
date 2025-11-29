# app/services/document_service.py
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.document import Document
from app.models.patient import Patient
from app.utils.file_storage import save_bytes_to_storage
from app.schemas.document import DocumentResponse


class DocumentNotFoundError(Exception):
    pass


class PatientNotFoundError(Exception):
    pass


def create_document_for_patient(
    db: Session,
    *,
    schema_name: str,
    patient_id: UUID,
    uploaded_by_id: UUID | None,
    file_bytes: bytes,
    original_filename: str,
    mime_type: str | None,
) -> Document:
    """
    Store file bytes on disk and create a Document row in the tenant schema.
    """
    patient = db.query(Patient).filter(Patient.id == patient_id).first()
    if not patient:
        raise PatientNotFoundError("Patient not found")

    subdir = f"{schema_name}/patients/{patient_id}"
    storage_path = save_bytes_to_storage(
        data=file_bytes,
        original_filename=original_filename,
        subdir=subdir,
    )

    doc = Document(
        patient_id=patient_id,
        uploaded_by_id=uploaded_by_id,
        file_name=original_filename,
        mime_type=mime_type,
        storage_path=storage_path,
    )

    try:
        db.add(doc)
        db.commit()
        db.refresh(doc)
    except SQLAlchemyError:
        db.rollback()
        raise

    return doc


def list_documents_for_patient(
    db: Session,
    *,
    patient_id: UUID,
) -> list[Document]:
    return (
        db.query(Document)
        .filter(Document.patient_id == patient_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def get_document(
    db: Session,
    *,
    document_id: UUID,
) -> Document:
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise DocumentNotFoundError("Document not found")
    return doc