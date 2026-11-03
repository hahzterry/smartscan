"""T — Take action: alerts, weekly plan, approval-gated cart."""

from typing import Any, Dict, List, Optional, Sequence

from ..guardrails import GuardrailService
from ..schemas import Plan
from ..store import Store
from ..tools.notifier import Notifier

PROMPT = (
    "Write the weekly plan and alerts in plain language: cheapest store "
    "per item, expected total, and saving versus the last basket. Never "
    "place or submit an order; a draft cart always waits for approval."
)

TOOLS = frozenset({"notifier", "draft_cart"})


def render_plan(plan: Plan, store: Store, notes: Sequence[str] = ()) -> str:
    lines = ["Week of %s" % plan.week]
    for item in plan.items:
        product = store.get_product(item.product_id)
        name = product.canonical_name if product else item.product_id
        lines.append("- %s: %s @ $%.2f (%s)" % (
            name, item.store, item.expected_price,
            item.decision.replace("_", " ")))
    lines.append("Expected total: $%.2f" % plan.expected_total)
    lines.append("Saving vs last basket: $%.2f" % plan.saving_vs_last_basket)
    for note in notes:
        lines.append("Note: %s" % note)
    return "\n".join(lines)


def run(plan: Plan, store: Store, guards: GuardrailService,
        notifier: Notifier, run_id: str,
        anomalies: Sequence[Dict[str, Any]] = (),
        notes: Sequence[str] = (),
        build_cart: bool = False) -> Dict[str, Any]:
    guards.enforce_tool("take_action", "notifier")
    notifications: List[Dict[str, str]] = []
    notifications.append(notifier.send_plan(render_plan(plan, store, notes)))
    for anomaly in anomalies:
        product = store.get_product(anomaly["product_id"])
        name = product.canonical_name if product else anomaly["product_id"]
        if anomaly["kind"] == "price_drop":
            notifications.append(notifier.send_alert(
                "Price drop: %s" % name,
                "%s at %s is now $%.2f (was ~$%.2f)." % (
                    name, anomaly["store"], anomaly["price"],
                    anomaly["baseline"])))
        elif anomaly["kind"] == "spike":
            notifications.append(notifier.send_alert(
                "Price spike: %s" % name,
                "%s at %s is $%.2f, well above ~$%.2f. Consider waiting." % (
                    name, anomaly["store"], anomaly["price"],
                    anomaly["baseline"])))
    approval_id: Optional[str] = None
    if build_cart:
        guards.enforce_tool("take_action", "draft_cart")
        summary = "draft cart for week %s, total $%.2f" % (
            plan.week, plan.expected_total)
        approval_id = guards.approvals.request("build_cart", summary)
        plan.approval_status = "pending"
        guards.audit(run_id, "cart_pending_approval", approval_id)
    store.save_plan(plan)
    feedback = {"run_id": run_id, "plan_id": plan.plan_id,
                "expected_total": plan.expected_total,
                "cart_requested": build_cart, "approval_id": approval_id,
                "approval_status": plan.approval_status}
    guards.audit(run_id, "take_action_done",
                 "plan %s saved, %d notifications"
                 % (plan.plan_id, len(notifications)))
    return {"notifications": notifications, "approval_id": approval_id,
            "feedback": feedback}
