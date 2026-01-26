# app/services/document_service.py
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.patient import Patient
from app.utils.file_storage import resolve_storage_path, save_bytes_to_storage


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
    document_type: str | None = None,
) -> Document:
    """
    Store file bytes on disk and create a Document row in the tenant schema.
    """
    from app.core.tenant_context import _set_tenant_search_path
    from app.services.tenant_service import ensure_tenant_tables_exist

    # Ensure tenant tables exist (defensive check)
    try:
        ensure_tenant_tables_exist(db, schema_name)
        _set_tenant_search_path(db, schema_name)
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            f"Could not ensure tenant tables exist before creating document: {e}",
            exc_info=True,
        )
        # Continue anyway - table might already exist

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
        document_type=document_type,
        storage_path=storage_path,
    )

    try:
        db.add(doc)
        db.flush()  # Get ID without committing
        doc_id = doc.id
        db.commit()

        # Re-query to ensure fresh state and correct search_path
        # After commit, search_path might be reset, so set it again
        _set_tenant_search_path(db, schema_name)

        # Re-query the document to ensure we have a fresh object
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            raise SQLAlchemyError("Failed to retrieve created document after commit")
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


def delete_document(
    db: Session,
    *,
    document_id: UUID,
) -> None:
    """
    Delete a document record and its underlying file from storage.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise DocumentNotFoundError("Document not found")

    # Best-effort deletion of file from storage
    try:
        file_path = resolve_storage_path(doc.storage_path)
        if file_path.exists():
            file_path.unlink()
    except Exception:
        # Don't block DB deletion if filesystem cleanup fails
        pass

    db.delete(doc)
    db.commit()
