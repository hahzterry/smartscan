"""Scheduler: when sweeps and weekly plans are due.

Pure date logic — wire ``due_runs`` into cron (or any job runner) so a
daily sweep and a weekly plan fire on time. A new receipt always
triggers an immediate Acquire run.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional


@dataclass(frozen=True)
class Schedule:
    sweep_interval_hours: int = 24
    plan_interval_days: int = 7


def due_runs(now: datetime, last_sweep: Optional[datetime],
             last_plan: Optional[datetime], new_receipt: bool = False,
             schedule: Schedule = None) -> List[str]:
    schedule = schedule or Schedule()
    due: List[str] = []
    if new_receipt:
        due.append("new_receipt")
    if last_sweep is None or now - last_sweep >= timedelta(
            hours=schedule.sweep_interval_hours):
        due.append("scheduled:sweep")
    if last_plan is None or now - last_plan >= timedelta(
            days=schedule.plan_interval_days):
        due.append("scheduled:plan")
    return due
