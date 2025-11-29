# app/services/stock_service.py
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.stock import StockItem


def list_stock_items(db: Session) -> list[StockItem]:
    return db.query(StockItem).order_by(StockItem.name.asc()).all()