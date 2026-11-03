"""Guardrail service: the wrapper around every tool call.

Implements the plan's five layers (input, data, reasoning, action, cost)
plus privacy redaction, polite-fetch notes, and per-run audit logging.
Scraped content is wrapped in :class:`UntrustedText` so page/flyer text can
only ever be read as data, never dispatched as instructions or tool calls.
"""

import re
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    APPROVAL_REQUIRED_ACTIONS,
    TOOL_ALLOWLIST,
    GuardrailConfig,
    get_config,
)
from .schemas import ReceiptLine


class UntrustedText(str):
    """Text from scraped pages/flyers: data only, never instructions.

    The tool dispatcher refuses ``UntrustedText`` anywhere a tool name or
    callable would go. Call :meth:`as_data` to explicitly acknowledge the
    content is being read as plain data.
    """

    def as_data(self) -> str:
        return str(self)


def assert_safe_tool_call(tool_name: object, stage: str) -> str:
    """Reject tool dispatch through untrusted or non-string names."""
    if isinstance(tool_name, UntrustedText):
        raise ValueError(
            "refusing to dispatch tool from untrusted page text (stage %s)" % stage
        )
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("tool name must be a non-empty string")
    return tool_name


class RetryBudget:
    """Loop and step caps for one run."""

    def __init__(self, config: Optional[GuardrailConfig] = None) -> None:
        self.config = config or get_config()
        self.regathers_by_item: Dict[str, int] = {}
        self.steps = 0

    def record_step(self) -> None:
        self.steps += 1
        if self.steps > self.config.max_steps_per_run:
            raise RuntimeError(
                "step budget exhausted (%d steps)" % self.config.max_steps_per_run
            )

    def allow_regather(self, item_key: str) -> bool:
        used = self.regathers_by_item.get(item_key, 0)
        if used >= self.config.max_regathers_per_item:
            return False
        self.regathers_by_item[item_key] = used + 1
        return True


class TokenBudget:
    """Per-run token and cost meter."""

    def __init__(self, config: Optional[GuardrailConfig] = None) -> None:
        self.config = config or get_config()
        self.tokens = 0
        self.cost = 0.0

    def record(self, tokens: int, cost: float = 0.0) -> None:
        self.tokens += tokens
        self.cost += cost

    @property
    def over_budget(self) -> bool:
        return self.tokens > self.config.token_budget_per_run


class ApprovalGate:
    """Human approval for anything that spends money or changes an order."""

    def __init__(self) -> None:
        self._pending: Dict[str, Dict[str, str]] = {}
        self._counter = 0

    @staticmethod
    def requires_approval(action_type: str) -> bool:
        return action_type in APPROVAL_REQUIRED_ACTIONS

    def request(self, action_type: str, summary: str) -> str:
        self._counter += 1
        approval_id = "approval-%d" % self._counter
        self._pending[approval_id] = {
            "action": action_type,
            "summary": summary,
            "status": "pending",
        }
        return approval_id

    def decide(self, approval_id: str, approved: bool) -> str:
        if approval_id not in self._pending:
            raise KeyError("unknown approval: %s" % approval_id)
        self._pending[approval_id]["status"] = "approved" if approved else "rejected"
        return self._pending[approval_id]["status"]

    def status(self, approval_id: str) -> str:
        return self._pending[approval_id]["status"]

    def is_approved(self, approval_id: str) -> bool:
        entry = self._pending.get(approval_id)
        return bool(entry) and entry["status"] == "approved"


# Privacy: strip loyalty IDs, card digits, and street addresses before
# any model API call. Patterns are intentionally broad (fail closed);
# tune against real receipts.
_LOYALTY_RE = re.compile(r"\b(loyalty|member|rewards?|club)\s*(id|no|#)?\s*[:#]?\s*\d+",
                         re.IGNORECASE)
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_CARD_LAST4_RE = re.compile(r"\b(card|acct|account)\s*(ending|last\s*4)?\s*[:#]?\s*\*{0,4}\d{4}\b",
                            re.IGNORECASE)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+"
    r"(St|Street|Ave|Avenue|Rd|Road|Blvd|Ln|Lane|Dr|Drive|Way|Ct|Court)\b"
)


def redact(text: str) -> str:
    """Strip loyalty IDs, card digits, and street addresses."""
    redacted = _LOYALTY_RE.sub("[LOYALTY-ID]", text)
    redacted = _CARD_LAST4_RE.sub("[CARD]", redacted)
    redacted = _CARD_RE.sub("[CARD]", redacted)
    redacted = _ADDRESS_RE.sub("[ADDRESS]", redacted)
    return redacted


class GuardrailService:
    def __init__(self, config: Optional[GuardrailConfig] = None) -> None:
        self.config = config or get_config()
        self.approvals = ApprovalGate()
        self.audit_log: List[Dict[str, str]] = []

    # -- audit ------------------------------------------------------
    def audit(self, run_id: str, event: str, detail: str = "") -> None:
        self.audit_log.append({"run_id": run_id, "event": event, "detail": detail})

    # -- input layer ------------------------------------------------
    def check_line(self, line: ReceiptLine) -> Tuple[str, List[str]]:
        """Return (verdict, reasons): ok | review | reject."""
        errors = line.validate()
        if errors:
            return "reject", errors
        if line.confidence < self.config.confidence_threshold:
            return "review", [
                "confidence %.2f below threshold %.2f"
                % (line.confidence, self.config.confidence_threshold)
            ]
        return "ok", []

    # -- data layer -------------------------------------------------
    def needs_second_source(self, price: float, history: Sequence[float]) -> bool:
        """True when price moved more than the sanity band from history."""
        if not history:
            return True
        baseline = median(history)
        if baseline <= 0:
            return True
        return abs(price - baseline) / baseline > self.config.sanity_band

    def is_stale(self, fetched_at: str, source_type: str,
                 now: Optional[datetime] = None) -> bool:
        ttl_days = self.config.ttl_days_by_source.get(source_type, 1)
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (current - fetched).total_seconds() > ttl_days * 86400

    # -- reasoning layer --------------------------------------------
    def check_tool(self, stage: str, tool: str) -> bool:
        return tool in TOOL_ALLOWLIST.get(stage, frozenset())

    def enforce_tool(self, stage: str, tool_name: object) -> str:
        name = assert_safe_tool_call(tool_name, stage)
        if not self.check_tool(stage, name):
            raise PermissionError(
                "stage %r may not call tool %r" % (stage, name)
            )
        return name

    # -- action layer -----------------------------------------------
    def check_spend(self, total: float) -> bool:
        return total <= self.config.weekly_spend_cap

    @staticmethod
    def check_brand(brand: str, approved_brands: Sequence[str]) -> bool:
        approved = {b.strip().lower() for b in approved_brands}
        return brand.strip().lower() in approved
