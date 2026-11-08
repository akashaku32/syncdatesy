from pydantic import BaseModel, Field, validator
from typing import List, Literal, Union, Dict, Any, Optional

class RowFilterCondition(BaseModel):
    """
    Represents a single row filter condition.
    """
    column: str
    operator: str
    value: Optional[Union[str, int, float, bool, List[str], Dict[str, Any]]] = None
    edm_type: Optional[str] = None

    @validator("operator", pre=True)
    def normalize_operator(cls, v):
        if not v:
            return v
        op_map = {
            "=": "eq",
            "!=": "ne",
            "<>": "ne",
            ">": "gt",
            ">=": "ge",
            "<": "lt",
            "<=": "le",
        }
        return op_map.get(str(v).strip(), v)

    @validator("value", always=True)
    def validate_value_by_operator(cls, v, values):
        operator = values.get("operator")
        if not operator:
            return v
        if operator in ("is_null", "is_not_null"):
            if v is not None:
                raise ValueError(f"Operator '{operator}' must not include a value.")
        elif operator == "in":
            if not isinstance(v, list) or len(v) == 0:
                raise ValueError(f"Operator 'in' requires a non-empty list of values.")
        elif operator in ("eq", "ne") and v is None:
            # SAP ODP accepts FIELD eq null / FIELD ne null.  The builder
            # limits this to verified string-field usage.
            return v
        else:
            if v is None:
                raise ValueError(f"Operator '{operator}' requires a non-null value.")
        return v

    class Config:
        extra = "ignore"


class RowFilterNot(BaseModel):
    """Unary logical NOT node; the child may be a condition or group."""
    not_: Any = Field(..., alias="not")

    class Config:
        allow_population_by_field_name = True
        extra = "ignore"


class RowFilterConfig(BaseModel):
    """
    Configuration model for row filters on SAP OData tables.
    Conditions may contain nested ``not`` groups while retaining the
    backwards-compatible flat ``logic``/``conditions`` shape.
    """
    logic: Literal["and", "or"] = "and"
    # ``Any`` keeps the public model recursive/backwards-compatible; the
    # builder performs the authoritative node validation against metadata.
    conditions: List[Any]

    class Config:
        extra = "ignore"


