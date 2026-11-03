"""Model router: one OpenAI-compatible layer over all model calls.

Routes each stage to Muse Spark 1.3 (standard), the self-hosted open
model, or a fallback. The Contributor tier is only ever used for
synthetic/test data — real receipts through Contributor raise.
"""

import json
import os
import urllib.request
from typing import Dict, Tuple

from .config import MODEL_ROUTING

REAL = "real"
SYNTHETIC = "synthetic"

# (input $/M tokens, output $/M tokens) from the plan's Oct 2026 check.
_PRICING = {
    "standard": (1.25, 4.25),
    "contributor": (0.10, 0.20),
    "open": (0.0, 0.0),
}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BaseBackend:
    name = "base"

    def complete(self, prompt: str, data_class: str = REAL,
                 use_cache: bool = True) -> Tuple[str, int]:
        raise NotImplementedError


class FakeBackend(BaseBackend):
    """Deterministic canned backend for tests, evals, and the demo."""

    name = "fake"

    def __init__(self, canned: str = "{}") -> None:
        self.canned = canned
        self.calls = 0

    def complete(self, prompt: str, data_class: str = REAL,
                 use_cache: bool = True) -> Tuple[str, int]:
        self.calls += 1
        tokens = _estimate_tokens(prompt) + _estimate_tokens(self.canned)
        return self.canned, tokens


class HttpBackend(BaseBackend):
    """OpenAI-compatible HTTP backend (Meta Model API).

    Reads ``META_MODEL_API_BASE`` and ``META_MODEL_API_KEY``. Never sends
    real user data when configured for the Contributor tier — the router
    enforces that before this is reached.
    """

    name = "http"

    def __init__(self, model: str, tier: str = "standard") -> None:
        self.model = model
        self.tier = tier

    def complete(self, prompt: str, data_class: str = REAL,
                 use_cache: bool = True) -> Tuple[str, int]:
        base = os.environ.get("META_MODEL_API_BASE", "").rstrip("/")
        key = os.environ.get("META_MODEL_API_KEY", "")
        if not base or not key:
            raise RuntimeError(
                "META_MODEL_API_BASE and META_MODEL_API_KEY must be set for "
                "live model calls; use FakeBackend for tests and the demo."
            )
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "use_cache": use_cache,
        }).encode("utf-8")
        req = urllib.request.Request(
            base + "/chat/completions",
            data=body,
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
        tokens = int(usage.get("total_tokens",
                               _estimate_tokens(prompt) + _estimate_tokens(text)))
        return text, tokens


class ModelRouter:
    def __init__(self, backends: Dict[str, BaseBackend] = None) -> None:
        self.backends: Dict[str, BaseBackend] = backends or {
            "standard": FakeBackend(),
            "contributor": FakeBackend(),
            "open": FakeBackend(),
        }

    def route(self, stage: str, data_class: str = REAL,
              tier_override: str = None) -> str:
        tier = tier_override or MODEL_ROUTING.get(stage, "standard")
        if tier not in self.backends:
            raise KeyError("unknown model tier: %s" % tier)
        if tier == "contributor" and data_class == REAL:
            raise ValueError(
                "Contributor tier must only see synthetic/test data; "
                "real data goes through standard or open."
            )
        return tier

    def complete(self, stage: str, prompt: str, data_class: str = REAL,
                 tier_override: str = None,
                 use_cache: bool = True) -> Tuple[str, int, float]:
        tier = self.route(stage, data_class, tier_override)
        text, tokens = self.backends[tier].complete(
            prompt, data_class, use_cache=use_cache)
        in_price, out_price = _PRICING.get(tier, (0.0, 0.0))
        in_tok = _estimate_tokens(prompt)
        out_tok = max(0, tokens - in_tok)
        cost = in_tok * in_price / 1e6 + out_tok * out_price / 1e6
        return text, tokens, cost
