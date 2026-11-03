"""Notifier: alerts and the weekly plan.

Backends: ``console`` prints, ``file`` appends to a path, ``memory``
records for tests. Real push/email wiring plugs in behind ``send``.
"""

from typing import Dict, List


class Notifier:
    def __init__(self, backend: str = "console", path: str = "alerts.log") -> None:
        if backend not in ("console", "file", "memory"):
            raise ValueError("unknown notifier backend: %r" % backend)
        self.backend = backend
        self.path = path
        self.sent: List[Dict[str, str]] = []

    def send(self, title: str, body: str) -> Dict[str, str]:
        message = {"title": title, "body": body}
        self.sent.append(message)
        if self.backend == "console":
            print("=== %s ===\n%s\n" % (title, body))
        elif self.backend == "file":
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write("=== %s ===\n%s\n\n" % (title, body))
        return message

    def send_alert(self, title: str, body: str) -> Dict[str, str]:
        return self.send(title, body)

    def send_plan(self, body: str) -> Dict[str, str]:
        return self.send("Weekly shopping plan", body)
