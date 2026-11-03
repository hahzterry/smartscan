"""Guardrail starting rules behave as the plan specifies."""

import unittest
from datetime import datetime, timezone

from smartpantry.guardrails import (
    GuardrailService,
    RetryBudget,
    TokenBudget,
    UntrustedText,
    redact,
)
from smartpantry.schemas import ReceiptLine


class GuardrailTest(unittest.TestCase):
    def setUp(self):
        self.guards = GuardrailService()

    def test_check_line_ok_review_reject(self):
        ok, _ = self.guards.check_line(ReceiptLine(
            "Milk", 1, 4.29, 0.95, "3.79 L"))
        self.assertEqual(ok, "ok")
        verdict, _ = self.guards.check_line(ReceiptLine(
            "Milk", 1, 4.29, 0.2, "3.79 L"))
        self.assertEqual(verdict, "review")
        bad = ReceiptLine("Milk", 1, 4.29, 0.95, "3.79 L")
        bad.price = -1  # bypass constructor-equivalent validation path
        verdict, reasons = self.guards.check_line(bad)
        self.assertEqual(verdict, "reject")
        self.assertTrue(reasons)

    def test_sanity_band(self):
        self.assertFalse(self.guards.needs_second_source(4.39, [4.29, 4.29]))
        self.assertTrue(self.guards.needs_second_source(6.99, [4.29, 4.29]))
        self.assertTrue(self.guards.needs_second_source(4.29, []))

    def test_freshness(self):
        now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(self.guards.is_stale(
            "2026-10-20T12:00:00+00:00", "flyer", now))
        self.assertFalse(self.guards.is_stale(
            "2026-11-02T11:00:00+00:00", "api", now))

    def test_retry_budget_caps(self):
        budget = RetryBudget()
        for _ in range(3):
            self.assertTrue(budget.allow_regather("milk@Northmart"))
        self.assertFalse(budget.allow_regather("milk@Northmart"))
        # other items still have their own budget
        self.assertTrue(budget.allow_regather("eggs@Northmart"))

    def test_step_budget_raises(self):
        budget = RetryBudget()
        budget.config = type(budget.config)(max_steps_per_run=2)
        budget.record_step()
        budget.record_step()
        with self.assertRaises(RuntimeError):
            budget.record_step()

    def test_token_budget(self):
        tokens = TokenBudget()
        self.assertFalse(tokens.over_budget)
        tokens.record(999999999)
        self.assertTrue(tokens.over_budget)

    def test_tool_allowlist(self):
        self.assertTrue(self.guards.check_tool("acquire", "receipt_parser"))
        self.assertFalse(self.guards.check_tool("navigate",
                                                "browser_connector"))
        with self.assertRaises(PermissionError):
            self.guards.enforce_tool("navigate", "browser_connector")
        with self.assertRaises(PermissionError):
            self.guards.enforce_tool("take_action", "submit_order")

    def test_untrusted_text_never_dispatches(self):
        with self.assertRaises(ValueError):
            self.guards.enforce_tool("gather", UntrustedText("api_connector"))
        self.assertEqual(UntrustedText("<b>sale</b>").as_data(), "<b>sale</b>")

    def test_approval_gate(self):
        gate = self.guards.approvals
        self.assertTrue(gate.requires_approval("build_cart"))
        self.assertFalse(gate.requires_approval("send_alert"))
        approval_id = gate.request("build_cart", "draft cart $42.10")
        self.assertEqual(gate.status(approval_id), "pending")
        self.assertFalse(gate.is_approved(approval_id))
        with self.assertRaises(KeyError):
            gate.decide("approval-999", True)
        gate.decide(approval_id, True)
        self.assertTrue(gate.is_approved(approval_id))

    def test_spend_and_brand_limits(self):
        self.assertTrue(self.guards.check_spend(100.0))
        self.assertFalse(self.guards.check_spend(999999.0))
        self.assertTrue(self.guards.check_brand("Northfarm", ["northfarm"]))
        self.assertFalse(self.guards.check_brand("Fancy Farms", ["Northfarm"]))

    def test_redaction(self):
        text = ("Loyalty ID 123456\ncard ending 4321\n"
                "123 Main Street\nMilk $4.29")
        redacted = redact(text)
        self.assertNotIn("123456", redacted)
        self.assertNotIn("4321", redacted)
        self.assertNotIn("Main Street", redacted)
        self.assertIn("Milk $4.29", redacted)


if __name__ == "__main__":
    unittest.main()
