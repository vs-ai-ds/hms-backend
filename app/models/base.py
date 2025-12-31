# app/models/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Base class for all ORM models.

    Global (public schema) models and tenant-specific models
    will both inherit from this class.
    """

    pass
