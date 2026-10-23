"""REUSABLE DECISION PATTERN: safety-alert guardrail  (#guardrails)

What it does
    Before SafetyLens raises an alert, drafts a work order, or writes an evidence brief, it
    runs a red-team/judge loop. The red-team LLM writes a one-paragraph argument that the
    anomaly is noise (measurement drift, one-off event, already fixed). Strands Decider 2B
    then reads both the original anomaly evidence AND the counter-argument and decides:

    - noise_argument_convincing (noul): is the red-team's case strong enough to suppress?
    - risk_severity (choice):  low / medium / high / critical
    - normalised_deviance (noul): has the same barrier failure appeared three or more times
      on this asset in the recent timeline, tolerated each time?

    Outcome logic (in SafetyAlertGuard.before_tool_call):
      - noise convincing AND severity low    → suppress (Deny with logged reason)
      - noise convincing AND severity medium → human confirms (Confirm)
      - noise not convincing OR severity ≥ high → raise (Proceed)
      - normalised_deviance + severity ≥ medium → escalate immediately (Proceed + flag)
    Below 0.60 confidence on noise_argument_convincing the decider is unsure: Confirm.

Typed questions and options
    noise_argument_convincing (noul), risk_severity (choice), normalised_deviance (noul);
    see QUESTIONS below.

Wiring
    `SafetyAlertGuard` is a Strands `InterventionHandler`; agent/agent.py passes it to
    the Agent:
        Agent(..., interventions=[SafetyAlertGuard(decider)])
    It guards ALERTED_TOOLS (raise_alert, draft_work_order, send_evidence_brief).
    Read-only tools (ingest_report, get_asset_timeline, get_risk_ledger) run untouched.
    If the agent retries a guarded call unchanged after a Guide, the retry is denied.

How to reuse it
    Copy this file. Replace ALERTED_TOOLS with your irreversible or high-stakes actions.
    Reword the three questions for your domain. The red-team prompt in SafetyAlertGuard
    can be adapted to any adversarial-checking scenario (fraud, content moderation,
    medical triage). Thresholds are constants at the top of SafetyAlertGuard.
"""

from __future__ import annotations

import json
from typing import Any

from strands.interventions import Confirm, Deny, Guide, InterventionHandler, Proceed

from decider_kit import Decider
from decider_kit.llm import ask as llm_ask
from decider_kit.patterns.guardrails import render_messages

PATTERN_TAG = "#guardrails"
ALERTED_TOOLS = {"raise_alert", "draft_work_order", "send_evidence_brief"}

QUESTIONS = {
    "noise_argument_convincing": Decider.noul(
        "Is the red-team's argument that this anomaly is noise convincing enough to suppress the alert?",
        {
            "true": (
                "the counter-argument explains the anomaly as measurement drift, a known one-off "
                "event, an already-repaired fault, or normal process variation with no safety "
                "barrier failure"
            ),
            "false": (
                "the anomaly involves a real barrier failure or bypass, a recurring pattern, "
                "or physical evidence of degradation that the noise argument does not credibly explain"
            ),
        },
    ),
    "risk_severity": Decider.choice(
        "What is the risk severity of this anomaly, considering both the hazard and the evidence?",
        {
            "low": "minor deviation, no safety barrier involved, no injury or equipment damage plausible",
            "medium": "safety barrier partially degraded or bypassed; injury or equipment damage possible if unaddressed",
            "high": "safety barrier failed or absent; injury, fire, or significant equipment damage likely without action",
            "critical": "imminent risk of catastrophic failure, multi-person injury, or regulatory shutdown",
        },
    ),
    "normalised_deviance": Decider.noul(
        "Does the asset timeline show the same barrier failure or safety bypass tolerated three or more times?",
        {
            "true": "the same failure mode appears repeatedly across different shifts or weeks, each time without corrective action",
            "false": "this appears to be an isolated event with no clear repeated pattern on this asset",
        },
    ),
}


# ---- red-team prompt ------------------------------------------------------------------

_REDTEAM_PROMPT = """\
You are a safety sceptic. Your job is to argue, in one short paragraph (3–5 sentences), that the
following anomaly is noise and does NOT require an alert. Consider: measurement drift, already-known
and logged faults, one-off events with no recurrence risk, and normal process variation.
Be specific about WHY this particular evidence is not alarming. Do not fabricate facts.
If the evidence is genuinely alarming, still construct the strongest possible noise argument.

ANOMALY EVIDENCE:
{evidence}
"""


def build_redteam_argument(evidence: str) -> str:
    """Ask the LLM to argue that the evidence is noise (red-team step)."""
    try:
        return llm_ask(_REDTEAM_PROMPT.format(evidence=evidence))
    except Exception:  # noqa: BLE001
        return "No counter-argument available; treat evidence at face value."


# ---- guardrail handler ----------------------------------------------------------------


class SafetyAlertGuard(InterventionHandler):
    name = "safety-alert-guard"

    # Confidence thresholds
    NOISE_SUPPRESS_THRESHOLD = 0.65   # above this: noise argument is convincing
    NOISE_UNSURE_LOW = 0.40           # below NOISE_SUPPRESS_THRESHOLD but above this: unsure → Confirm
    SEVERITY_ESCALATE = {"high", "critical"}
    SEVERITY_CONFIRM = {"medium"}

    def __init__(self, decider: Decider):
        self.decider = decider
        self.log: list[dict[str, Any]] = []
        self._guided: set[str] = set()

    def before_invocation(self, event, **kwargs):
        self._guided.clear()
        return Proceed()

    def before_tool_call(self, event, **kwargs):
        name = event.tool_use["name"]
        if name not in ALERTED_TOOLS:
            return Proceed()

        args = event.tool_use.get("input") or {}
        sig = f"{name}:{json.dumps(args, sort_keys=True)}"

        if sig in self._guided:
            self.log.append({
                "pattern": PATTERN_TAG, "tool": name, "args": args,
                "verdict": "deny", "reason": "retried a guided call unchanged",
            })
            return Deny(reason="This action was already flagged. Revise the evidence or the risk assessment before trying again.")

        # Build the state: conversation + proposed action + evidence from args
        evidence = args.get("evidence", args.get("summary", json.dumps(args)))
        redteam = build_redteam_argument(evidence)

        state = (
            "A factory safety analysis. An anomaly was detected and the following evidence gathered.\n\n"
            f"--- ANOMALY EVIDENCE ---\n{evidence}\n\n"
            f"--- RED-TEAM NOISE ARGUMENT ---\n{redteam}\n\n"
            f"--- PROPOSED ACTION ---\ntool: {name}\narguments: {json.dumps(args)}\n\n"
            f"--- ASSET CONVERSATION CONTEXT ---\n{render_messages(event.agent.messages)}"
        )

        a = self.decider.ask(state, QUESTIONS)

        noise_p_yes = a["noise_argument_convincing"].p_yes or 0.0
        severity = a["risk_severity"].label or "medium"
        severity_conf = a["risk_severity"].confidence or 0.0
        deviance_p_yes = a["normalised_deviance"].p_yes or 0.0

        rec: dict[str, Any] = {
            "pattern": PATTERN_TAG,
            "tool": name,
            "args": args,
            "noise_argument_convincing": round(noise_p_yes, 3),
            "risk_severity": severity,
            "risk_severity_confidence": round(severity_conf, 3),
            "normalised_deviance": round(deviance_p_yes, 3),
            "redteam_argument": redteam,
            "decider_ms": round(self.decider.last_latency_ms or 0),
        }

        # ---- decision logic -----------------------------------------------------------
        noise_convincing = noise_p_yes >= self.NOISE_SUPPRESS_THRESHOLD
        noise_unsure = self.NOISE_UNSURE_LOW <= noise_p_yes < self.NOISE_SUPPRESS_THRESHOLD
        is_critical = severity in self.SEVERITY_ESCALATE
        is_medium = severity in self.SEVERITY_CONFIRM
        normalised = deviance_p_yes >= 0.60

        if normalised and (is_critical or is_medium):
            # Normalised deviance with real severity → escalate immediately, no suppression
            rec["verdict"] = "proceed"
            rec["flag"] = "normalised_deviance"
            action = Proceed()

        elif is_critical:
            # Critical severity is never suppressed
            rec["verdict"] = "proceed"
            action = Proceed()

        elif noise_convincing and severity == "low":
            # Strong noise argument + low severity → suppress
            rec["verdict"] = "suppress"
            self._guided.add(sig)
            action = Deny(
                reason=(
                    f"Alert suppressed: the red-team argument is convincing (p={noise_p_yes:.2f}) "
                    f"and severity is low. Log the finding and move on."
                )
            )

        elif noise_convincing and is_medium:
            # Noise convincing but medium severity → human confirms
            rec["verdict"] = "confirm"
            action = Confirm(
                prompt=f"Noise argument is convincing (p={noise_p_yes:.2f}) but severity={severity}. Raise alert anyway?",
                response="yes",
            )

        elif noise_unsure:
            # Decider is unsure → human confirms
            rec["verdict"] = "confirm"
            action = Confirm(
                prompt=f"Decider unsure (noise_p={noise_p_yes:.2f}, severity={severity}). Raise alert?",
                response="yes",
            )

        else:
            # Noise not convincing → raise
            rec["verdict"] = "proceed"
            action = Proceed()

        if rec["verdict"] == "guide":
            self._guided.add(sig)

        self.log.append(rec)
        return action
