"""SafetyLens: factory safety anomaly detection agent.

Ingests near-miss reports and maintenance logs, detects normalised deviance, runs a
red-team / judge loop (Strands Decider 2B as the guardrail), then drafts work orders and
evidence briefs. Humans approve every alert.

    from agent.agent import HackathonAgent
    out = HackathonAgent().chat("Show me the risk ledger.")
    out["reply"], out["decisions"]
"""

from __future__ import annotations

import time
from typing import Any

from strands import Agent

from decider_kit import Decider
from decider_kit.llm import agent_model, default_model_id, image_blocks, strip_thinking

from .decision import SafetyAlertGuard
from .tools import TOOLS

NAME = "SafetyLens"

SYSTEM_PROMPT = """You are SafetyLens, an AI safety analyst for factory operations.

Your job:
1. Ingest near-miss reports, maintenance notes, and shift handovers and extract structured events.
2. Build a per-asset anomaly timeline and detect normalised deviance (same barrier failure repeated and tolerated).
3. For any finding worth alerting on, raise a formal safety alert, draft a work order, and send an evidence brief to the asset owner.
4. Show the Ignored Warnings Ledger when asked — every open risk, how long it has been open, and its cost exposure.

Rules:
- Every claim you make must be linked to a source line (report id, log entry, date).
- Never invent anomalies, dates, or cost figures. Use only what the tools return.
- Humans approve every alert (the guardrail will ask when the decision is uncertain).
- Be concise and factual. Safety managers do not have time for prose padding.
- When you detect normalised deviance, say so explicitly and flag it as higher priority."""

EAGER_SYSTEM_PROMPT = """You are SafetyLens, a factory safety analyst. Act immediately on every report:
ingest it, check the timeline, and raise an alert with a work order without asking for confirmation.
This eager mode is for demo recording only — the guardrail still checks every alert."""


class HackathonAgent:
    """One Strands agent per session, with the SafetyAlertGuard wired in."""

    def __init__(self, decider: Decider | None = None, model_id: str | None = None, eager: bool | None = None):
        import os

        self.decider = decider or Decider()
        self.eager = eager if eager is not None else os.environ.get("DEMO_EAGER") == "1"
        self.model_id = model_id or default_model_id()
        self.sessions: dict[str, tuple[Agent, SafetyAlertGuard]] = {}

    def _session(self, session_id: str) -> tuple[Agent, SafetyAlertGuard]:
        if session_id not in self.sessions:
            guard = SafetyAlertGuard(self.decider)
            agent = Agent(
                model=agent_model(self.model_id),
                system_prompt=EAGER_SYSTEM_PROMPT if self.eager else SYSTEM_PROMPT,
                tools=TOOLS,
                interventions=[guard],
                callback_handler=None,
            )
            self.sessions[session_id] = (agent, guard)
        return self.sessions[session_id]

    def chat(self, message: str, session_id: str = "default", images: list[Any] | None = None) -> dict[str, Any]:
        """`images` (optional): photos attached by the user (paths, bytes or base64)."""
        agent, guard = self._session(session_id)
        before = len(guard.log)
        t0 = time.perf_counter()
        result = agent(
            [*image_blocks(images), {"text": message}] if images else message,
            invocation_state={"images": list(images or [])},
        )
        reply = strip_thinking(str(result))
        return {
            "reply": reply,
            "decisions": guard.log[before:],
            "model_id": self.model_id,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "agent": NAME,
            "eager": self.eager,
        }


if __name__ == "__main__":
    import json
    import sys

    a = HackathonAgent()
    messages = sys.argv[1:] or [
        "Show me the risk ledger.",
        "Analyse PUMP-07 and tell me if we have a normalised deviance issue.",
        "Raise an alert and draft a work order for PUMP-07.",
    ]
    for msg in messages:
        print(json.dumps(a.chat(msg), indent=2, default=str))
