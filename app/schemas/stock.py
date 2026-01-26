# schemas/stock.py
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.stock import StockItemType

NameStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]

OptStr100 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=100),
    ]
    | None
)

OptStr50 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=50),
    ]
    | None
)

OptStr500 = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=500),
    ]
    | None
)


class StockItemBase(BaseModel):
    """
    Shared fields for create/response.

    - Optional strings accept None and are limited in length when present.
    - Empty strings from UI are normalized to None.
    """

    type: StockItemType
    name: NameStr

    generic_name: OptStr100 = None
    form: OptStr50 = None
    strength: OptStr50 = None
    route: OptStr50 = None

    default_dosage: OptStr50 = None
    default_frequency: OptStr50 = None
    default_duration: OptStr50 = None
    default_instructions: OptStr500 = None

    current_stock: int = Field(default=0, ge=0)
    reorder_level: int = Field(default=0, ge=0)
    is_active: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "generic_name",
        "form",
        "strength",
        "route",
        "default_dosage",
        "default_frequency",
        "default_duration",
        "default_instructions",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class StockItemCreate(StockItemBase):
    """Used when creating a new stock item."""

    @model_validator(mode="after")
    def validate_create_rules(self) -> "StockItemCreate":
        errors: list[str] = []

        # MEDICINE requires these fields on create
        if self.type == StockItemType.MEDICINE:
            if not self.form:
                errors.append("Form is required for MEDICINE type")
            if not self.strength:
                errors.append("Strength is required for MEDICINE type")
            if not self.default_dosage:
                errors.append("Default dosage is required for MEDICINE type")
            if not self.default_frequency:
                errors.append("Default frequency is required for MEDICINE type")
            if not self.default_duration:
                errors.append("Default duration is required for MEDICINE type")

        # Bundle rule: if any of the three is provided, all must be present
        trio = [self.default_dosage, self.default_frequency, self.default_duration]
        filled = sum(1 for x in trio if x is not None)
        if 0 < filled < 3:
            errors.append(
                "If any of default_dosage, default_frequency, or default_duration is provided, all three must be present"
            )

        if errors:
            raise ValueError("; ".join(errors))

        return self


class StockItemUpdate(BaseModel):
    """
    Used when updating a stock item (PATCH).
    All fields optional.

    MEDICINE completeness is validated in the endpoint because it needs existing values.
    Here we only enforce the dosage trio bundle rule when payload touches any of them.
    """

    type: StockItemType | None = None
    name: NameStr | None = None

    generic_name: OptStr100 = None
    form: OptStr50 = None
    strength: OptStr50 = None
    route: OptStr50 = None

    default_dosage: OptStr50 = None
    default_frequency: OptStr50 = None
    default_duration: OptStr50 = None
    default_instructions: OptStr500 = None

    current_stock: int | None = Field(default=None, ge=0)
    reorder_level: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "generic_name",
        "form",
        "strength",
        "route",
        "default_dosage",
        "default_frequency",
        "default_duration",
        "default_instructions",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def validate_bundle_rule_if_touched(self) -> "StockItemUpdate":
        touched = any(
            v is not None
            for v in (
                self.default_dosage,
                self.default_frequency,
                self.default_duration,
            )
        )
        if not touched:
            return self

        trio = [self.default_dosage, self.default_frequency, self.default_duration]
        filled = sum(1 for x in trio if x is not None)
        if 0 < filled < 3:
            raise ValueError(
                "Please provide default_dosage, default_frequency, and default_duration together"
            )

        return self


class StockItemResponse(StockItemBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")
