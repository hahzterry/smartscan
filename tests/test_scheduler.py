"""Scheduler fires sweeps daily, plans weekly, receipts immediately."""

import unittest
from datetime import datetime, timezone

from smartpantry.scheduler import due_runs


class SchedulerTest(unittest.TestCase):
    def test_first_run_fires_both(self):
        now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        self.assertEqual(due_runs(now, None, None),
                         ["scheduled:sweep", "scheduled:plan"])

    def test_quiet_when_recent(self):
        now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        recent = datetime(2026, 11, 2, 7, tzinfo=timezone.utc)
        plan = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
        self.assertEqual(due_runs(now, recent, plan), [])

    def test_new_receipt_triggers_immediately(self):
        now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        recent = datetime(2026, 11, 2, 7, tzinfo=timezone.utc)
        plan = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
        self.assertEqual(due_runs(now, recent, plan, new_receipt=True),
                         ["new_receipt"])


if __name__ == "__main__":
    unittest.main()
