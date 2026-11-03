"""Strict JSON schemas for the shared store.

Every entity validates itself with ``validate()`` returning a list of
error strings (empty means valid). ``from_dict()`` raises ``ValueError``
on any violation so bad lines are rejected, never coerced silently.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _req(data: Dict[str, Any], key: str, errors: List[str]) -> Any:
    if key not in data:
        errors.append("missing required field: %s" % key)
        return None
    return data[key]


def _check_str(value: Any, key: str, errors: List[str], allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        errors.append("field %r must be a non-empty string" % key)


def _check_num(
    value: Any, key: str, errors: List[str], minimum: Optional[float] = None
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append("field %r must be a number" % key)
    elif minimum is not None and value < minimum:
        errors.append("field %r must be >= %s" % (key, minimum))


def _check_iso_datetime(value: Any, key: str, errors: List[str]) -> None:
    if not isinstance(value, str):
        errors.append("field %r must be an ISO-8601 string" % key)
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append("field %r is not valid ISO-8601: %r" % (key, value))


def _check_iso_date(value: Any, key: str, errors: List[str]) -> None:
    if not isinstance(value, str):
        errors.append("field %r must be an ISO date string" % key)
        return
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        errors.append("field %r is not a valid YYYY-MM-DD date: %r" % (key, value))


SOURCE_TYPES = ("api", "flyer", "browser")
DECISIONS = ("buy_now", "wait", "switch_store")
APPROVAL_STATUSES = ("pending", "approved", "rejected")


@dataclass
class Product:
    product_id: str
    canonical_name: str
    brand: str
    size_value: float
    size_unit: str
    category: str
    approved_substitutes: List[str] = field(default_factory=list)
    # Retailer linkage for live price lookups. UPC is the universal code;
    # external_ids maps store name -> that retailer's item id
    # (e.g. {"target": "<TCIN>", "walmart": "<itemId>"}).
    upc: str = ""
    external_ids: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.product_id, "product_id", errors)
        _check_str(self.canonical_name, "canonical_name", errors)
        _check_str(self.brand, "brand", errors)
        _check_num(self.size_value, "size_value", errors, minimum=0)
        _check_str(self.size_unit, "size_unit", errors)
        _check_str(self.category, "category", errors)
        if not isinstance(self.approved_substitutes, list) or not all(
            isinstance(s, str) for s in self.approved_substitutes
        ):
            errors.append("field 'approved_substitutes' must be a list of strings")
        if not isinstance(self.upc, str):
            errors.append("field 'upc' must be a string")
        if not isinstance(self.external_ids, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in self.external_ids.items()
        ):
            errors.append("field 'external_ids' must map strings to strings")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Product":
        errors: List[str] = []
        for key in ("product_id", "canonical_name", "brand", "size_value",
                    "size_unit", "category"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            product_id=data["product_id"],
            canonical_name=data["canonical_name"],
            brand=data["brand"],
            size_value=data["size_value"],
            size_unit=data["size_unit"],
            category=data["category"],
            approved_substitutes=list(data.get("approved_substitutes", [])),
            upc=data.get("upc", ""),
            external_ids=dict(data.get("external_ids", {})),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class ReceiptLine:
    """One raw extracted receipt line, before product matching."""

    description: str
    quantity: float
    price: float
    confidence: float
    size_text: str = ""

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.description, "description", errors)
        _check_num(self.quantity, "quantity", errors, minimum=0)
        _check_num(self.price, "price", errors, minimum=0)
        _check_num(self.confidence, "confidence", errors, minimum=0)
        if isinstance(self.confidence, (int, float)) and not (
            0.0 <= self.confidence <= 1.0
        ):
            errors.append("field 'confidence' must be between 0 and 1")
        if not isinstance(self.size_text, str):
            errors.append("field 'size_text' must be a string")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReceiptLine":
        errors: List[str] = []
        for key in ("description", "quantity", "price", "confidence"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            description=data["description"],
            quantity=data["quantity"],
            price=data["price"],
            confidence=data["confidence"],
            size_text=data.get("size_text", ""),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class Purchase:
    purchase_id: str
    date: str  # YYYY-MM-DD
    store: str
    product_id: str
    quantity: float
    price_paid: float
    receipt_id: str

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.purchase_id, "purchase_id", errors)
        _check_iso_date(self.date, "date", errors)
        _check_str(self.store, "store", errors)
        _check_str(self.product_id, "product_id", errors)
        _check_num(self.quantity, "quantity", errors, minimum=0)
        _check_num(self.price_paid, "price_paid", errors, minimum=0)
        _check_str(self.receipt_id, "receipt_id", errors)
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Purchase":
        errors: List[str] = []
        for key in ("purchase_id", "date", "store", "product_id",
                    "quantity", "price_paid", "receipt_id"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(**{k: data[k] for k in (
            "purchase_id", "date", "store", "product_id",
            "quantity", "price_paid", "receipt_id")})
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class PriceObservation:
    observation_id: str
    product_id: str
    store: str
    price: float
    unit_price: float
    unit: str
    source_type: str  # api | flyer | browser
    fetched_at: str  # ISO-8601 datetime
    confidence: float
    verified: bool = False
    # Weekly-ad bounding: YYYY-MM-DD dates ('' when not an ad price) and
    # condition flags such as loyalty_card, coupon, multi_buy, limit.
    valid_from: str = ""
    valid_to: str = ""
    conditions: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.observation_id, "observation_id", errors)
        _check_str(self.product_id, "product_id", errors)
        _check_str(self.store, "store", errors)
        _check_num(self.price, "price", errors, minimum=0)
        _check_num(self.unit_price, "unit_price", errors, minimum=0)
        _check_str(self.unit, "unit", errors)
        if self.source_type not in SOURCE_TYPES:
            errors.append("field 'source_type' must be one of %s" % (SOURCE_TYPES,))
        _check_iso_datetime(self.fetched_at, "fetched_at", errors)
        _check_num(self.confidence, "confidence", errors, minimum=0)
        if isinstance(self.confidence, (int, float)) and not (
            0.0 <= self.confidence <= 1.0
        ):
            errors.append("field 'confidence' must be between 0 and 1")
        if not isinstance(self.verified, bool):
            errors.append("field 'verified' must be a boolean")
        for key in ("valid_from", "valid_to"):
            value = getattr(self, key)
            if not isinstance(value, str):
                errors.append("field %r must be a string" % key)
            elif value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    errors.append(
                        "field %r must be YYYY-MM-DD or empty" % key)
        if not isinstance(self.conditions, list) or not all(
            isinstance(c, str) for c in self.conditions
        ):
            errors.append("field 'conditions' must be a list of strings")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceObservation":
        errors: List[str] = []
        for key in ("observation_id", "product_id", "store", "price",
                    "unit_price", "unit", "source_type", "fetched_at",
                    "confidence"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            observation_id=data["observation_id"],
            product_id=data["product_id"],
            store=data["store"],
            price=data["price"],
            unit_price=data["unit_price"],
            unit=data["unit"],
            source_type=data["source_type"],
            fetched_at=data["fetched_at"],
            confidence=data["confidence"],
            verified=bool(data.get("verified", False)),
            valid_from=data.get("valid_from", ""),
            valid_to=data.get("valid_to", ""),
            conditions=list(data.get("conditions", [])),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class WatchlistItem:
    product_id: str
    buy_frequency_days: int
    target_price: float
    preferred_stores: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.product_id, "product_id", errors)
        if isinstance(self.buy_frequency_days, bool) or not isinstance(
            self.buy_frequency_days, int
        ):
            errors.append("field 'buy_frequency_days' must be an integer")
        elif self.buy_frequency_days <= 0:
            errors.append("field 'buy_frequency_days' must be > 0")
        _check_num(self.target_price, "target_price", errors, minimum=0)
        if not isinstance(self.preferred_stores, list) or not all(
            isinstance(s, str) for s in self.preferred_stores
        ):
            errors.append("field 'preferred_stores' must be a list of strings")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WatchlistItem":
        errors: List[str] = []
        for key in ("product_id", "buy_frequency_days", "target_price"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            product_id=data["product_id"],
            buy_frequency_days=data["buy_frequency_days"],
            target_price=data["target_price"],
            preferred_stores=list(data.get("preferred_stores", [])),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class PlanItem:
    product_id: str
    store: str
    expected_price: float
    decision: str  # buy_now | wait | switch_store

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.product_id, "product_id", errors)
        _check_str(self.store, "store", errors)
        _check_num(self.expected_price, "expected_price", errors, minimum=0)
        if self.decision not in DECISIONS:
            errors.append("field 'decision' must be one of %s" % (DECISIONS,))
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanItem":
        errors: List[str] = []
        for key in ("product_id", "store", "expected_price", "decision"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            product_id=data["product_id"],
            store=data["store"],
            expected_price=data["expected_price"],
            decision=data["decision"],
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class Plan:
    plan_id: str
    week: str  # YYYY-MM-DD (week start)
    items: List[PlanItem] = field(default_factory=list)
    expected_total: float = 0.0
    saving_vs_last_basket: float = 0.0
    approval_status: str = "pending"

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.plan_id, "plan_id", errors)
        _check_iso_date(self.week, "week", errors)
        if not isinstance(self.items, list):
            errors.append("field 'items' must be a list")
        else:
            for i, item in enumerate(self.items):
                for err in item.validate():
                    errors.append("items[%d]: %s" % (i, err))
        _check_num(self.expected_total, "expected_total", errors, minimum=0)
        if isinstance(self.saving_vs_last_basket, bool) or not isinstance(
            self.saving_vs_last_basket, (int, float)
        ):
            errors.append("field 'saving_vs_last_basket' must be a number")
        if self.approval_status not in APPROVAL_STATUSES:
            errors.append(
                "field 'approval_status' must be one of %s" % (APPROVAL_STATUSES,)
            )
        return errors

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        errors: List[str] = []
        for key in ("plan_id", "week"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        items = [PlanItem.from_dict(d) for d in data.get("items", [])]
        obj = cls(
            plan_id=data["plan_id"],
            week=data["week"],
            items=items,
            expected_total=data.get("expected_total", 0.0),
            saving_vs_last_basket=data.get("saving_vs_last_basket", 0.0),
            approval_status=data.get("approval_status", "pending"),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj


@dataclass
class RunLog:
    run_id: str
    stage: str
    tool_calls: List[str] = field(default_factory=list)
    guardrail_events: List[str] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0

    def validate(self) -> List[str]:
        errors: List[str] = []
        _check_str(self.run_id, "run_id", errors)
        _check_str(self.stage, "stage", errors)
        if not isinstance(self.tool_calls, list) or not all(
            isinstance(t, str) for t in self.tool_calls
        ):
            errors.append("field 'tool_calls' must be a list of strings")
        if not isinstance(self.guardrail_events, list) or not all(
            isinstance(t, str) for t in self.guardrail_events
        ):
            errors.append("field 'guardrail_events' must be a list of strings")
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int):
            errors.append("field 'tokens' must be an integer")
        elif self.tokens < 0:
            errors.append("field 'tokens' must be >= 0")
        _check_num(self.cost, "cost", errors, minimum=0)
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunLog":
        errors: List[str] = []
        for key in ("run_id", "stage"):
            _req(data, key, errors)
        if errors:
            raise ValueError("; ".join(errors))
        obj = cls(
            run_id=data["run_id"],
            stage=data["stage"],
            tool_calls=list(data.get("tool_calls", [])),
            guardrail_events=list(data.get("guardrail_events", [])),
            tokens=data.get("tokens", 0),
            cost=data.get("cost", 0.0),
        )
        errors = obj.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return obj
