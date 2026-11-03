"""Central configuration: guardrail starting values from the A.G.E.N.T plan.

All numbers are starting values to tune, not hard constants. Import
``get_config()`` anywhere a stage needs the current rules.
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet


@dataclass(frozen=True)
class GuardrailConfig:
    # Input layer
    confidence_threshold: float = 0.70
    # Data layer
    sanity_band: float = 0.40  # >40% move from history needs a second source
    ttl_days_by_source: Dict[str, int] = field(
        default_factory=lambda: {"api": 1, "flyer": 7, "browser": 1}
    )
    # Reasoning layer
    max_regathers_per_item: int = 3
    max_steps_per_run: int = 50
    # Action layer
    weekly_spend_cap: float = 250.0
    # Cost layer
    token_budget_per_run: int = 200_000


# Each stage may call only its own tools. Navigate has no fetch or
# purchase tools by design.
TOOL_ALLOWLIST: Dict[str, FrozenSet[str]] = {
    "acquire": frozenset({"receipt_parser", "email_reader", "redact"}),
    "gather": frozenset({"api_connector", "flyer_connector", "browser_connector"}),
    "evaluate": frozenset({"unit_price", "product_match"}),
    "navigate": frozenset({"unit_price"}),
    "take_action": frozenset({"notifier", "draft_cart"}),
}

# Actions that must wait for explicit human approval.
APPROVAL_REQUIRED_ACTIONS = frozenset({"build_cart", "submit_order", "spend"})

# Source priority: official APIs first, published flyers second,
# browser automation as last resort.
SOURCE_PRIORITY = ("api", "flyer", "browser")

# Stage -> model backend. "contributor" is only ever used for
# synthetic/test data; real receipts use "standard" or "open".
MODEL_ROUTING: Dict[str, str] = {
    "acquire": "standard",
    "gather": "standard",
    "evaluate": "open",
    "navigate": "standard",
    "take_action": "standard",
}

_CONFIG = GuardrailConfig()


def get_config() -> GuardrailConfig:
    return _CONFIG
