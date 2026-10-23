# SafetyLens: A Factory Safety Agent That Proves Its Case and Argues Against Itself

Most safety tools alert. SafetyLens does something harder: it ingests factory near-miss reports, builds a per-asset anomaly timeline, red-teams every finding, lets Strands Decider 2B judge the argument, and then drafts a work order with every claim linked to its source.

## The Problem

Factories, hospitals, and airlines file thousands of near-miss reports, maintenance notes, and shift handovers that nobody analyses together. Major accidents are often preceded by months of small, repeated, tolerated anomalies buried in that pile. The pattern even has a name: *normalised deviance* — when the same safety barrier failure is logged, noted, and ignored so many times it stops feeling dangerous.

The cost is lives, shutdowns, fines, and insurance claims. The gap is not data — it is judgement at scale. A human safety manager cannot read every report. An LLM that hallucinates a severity score cannot be trusted to raise an alert. What is needed is a fast, cheap, calibrated decision that reads language and returns a probability.

That is exactly what Strands Decider 2B is built for.

## What SafetyLens Does

SafetyLens is a closed-loop safety agent built on Amazon Bedrock (Amazon Nova Pro) with Strands Decider 2B as its guardrail judge.

**The closed loop:**

1. **Ingest** — parse a raw near-miss report, maintenance note, or shift handover into a structured event (asset, hazard, which safety barrier failed or was bypassed).
2. **Timeline** — fetch the full anomaly history for the asset and flag normalised deviance when the same barrier failure appears three or more times, unresolved.
3. **Red-team** — the agent asks the LLM to argue, in one paragraph, that the finding is noise: measurement drift, a one-off event, an already-repaired fault.
4. **Judge** — Strands Decider 2B reads both the anomaly evidence and the noise counter-argument and decides.
5. **Act** — on a confirmed risk, draft a work order and a one-page evidence brief with every claim linked to its source line. Humans approve every action.
6. **Ledger** — the Ignored Warnings Ledger shows every open risk, how long it has been open, and its cost exposure. That one screen is what CEOs remember.

## The Decision Handed to Strands Decider 2B

The guardrail pattern answers three typed questions in one forward pass:

| Question | Type | What it decides |
|---|---|---|
| `noise_argument_convincing` | noul (yes/no) | Is the red-team's case strong enough to suppress the alert? |
| `risk_severity` | choice (low/medium/high/critical) | How dangerous is this anomaly? |
| `normalised_deviance` | noul (yes/no) | Has this barrier failure been tolerated three or more times? |

The outcome logic in code:

```python
if normalised and severity in ("high", "critical"):
    # Never suppress normalised deviance at real severity → escalate
    return Proceed()
elif severity == "critical":
    # Critical is never suppressed regardless of noise argument
    return Proceed()
elif noise_convincing and severity == "low":
    # Strong noise argument + low severity → suppress with logged reason
    return Deny(reason="Alert suppressed: noise argument convincing, severity low.")
elif noise_convincing and severity == "medium":
    # Noise convincing but non-trivial severity → human confirms
    return Confirm(prompt="Raise alert anyway?")
elif noise_unsure:
    # Decider uncertain → human confirms
    return Confirm(prompt="Decider unsure. Raise alert?")
else:
    # Noise not convincing → raise
    return Proceed()
```

This is the `#guardrails` pattern from the kit. The wiring is a Strands `InterventionHandler` — it fires before any alert-raising, work-order, or evidence-brief tool runs.

## Why a Decision Model Instead of a Second LLM Call

The red-team/judge loop runs on every anomaly. Calling the LLM twice — once to argue and once to judge — doubles latency and cost, and the LLM's judgement is uncalibrated (it does not return a probability).

Strands Decider 2B does one forward pass and returns calibrated probabilities. The code branches on a number. On the user group's hosted API, that costs **$0.035 per 1,000 decisions**.

## Measured Results (test split, 14 cases, hosted API)

| | Strands Decider 2B |
|---|---|
| Accuracy | 73.8% |
| Accuracy when confident (≥ 0.70) | **92.9%** |
| Cases answered alone | 67% |
| Cases sent to human (low confidence) | 33% |
| Latency p50 per decision | 3,876 ms |
| Latency p95 | 4,085 ms |
| Cost per 1,000 decisions | $0.035 |

**Key finding:** when the decider is confident, it is right 92.9% of the time. The 33% of cases it is unsure about go to a human rather than being guessed — which is exactly the right behaviour for a safety system. These numbers were produced on the held-out test split; questions were tuned only on the tune split.

Latency is dominated by the hosted API network round-trip (~1.2s model + ~2.7s network overhead). On a local GPU (RTX 3050, which this machine has), the same decision runs in ~0.2–0.3s.

## The Ignored Warnings Ledger

The ledger is a single table: every open risk, the asset, the hazard, the days it has been open, and the estimated cost exposure. For PUMP-07 in the demo — a pump seal leak with its containment tray bypassed four times over 101 days — the cost exposure is $85,000. Seeing that number next to "101 days open" is what turns a safety manager's conversation into a CEO decision.

## Architecture

```
User message
    │
    ▼
Amazon Bedrock (Nova Pro via Strands Agents SDK)
    │
    ├─ ingest_report()          ← parse raw text → structured event
    ├─ get_asset_timeline()     ← fetch history, flag normalised deviance
    ├─ get_risk_ledger()        ← the CEO screen
    │
    │  [before raise_alert / draft_work_order / send_evidence_brief]
    │
    ▼
SafetyAlertGuard (Strands InterventionHandler)
    │
    ├─ LLM: write red-team noise argument
    │
    ▼
Strands Decider 2B  ← one forward pass, three questions
    │
    ├─ noise_argument_convincing  → suppress / confirm / proceed
    ├─ risk_severity              → low / medium / high / critical
    └─ normalised_deviance        → flag for escalation
    │
    ▼
raise_alert() / draft_work_order() / send_evidence_brief()
(human approves every action)
```

## Reusable Decision Pattern

The `SafetyAlertGuard` in `agent/decision.py` is a reusable `#guardrails` pattern. Copy the file, replace `ALERTED_TOOLS` with your irreversible actions, and reword the three questions for your domain. The red-team prompt can be adapted to any adversarial-checking scenario: fraud review, content moderation, medical triage, compliance checking.

## How to Run It

```bash
# clone the repo
git clone https://github.com/<your-username>/safetylens
cd safetylens

# install (macOS/Linux/WSL)
curl -fsSL https://raw.githubusercontent.com/Vivek0712/decider-hackathon-kit/main/install/install.sh | sh

# set your API key in .env (DECIDER_API_KEY)
# run the demo
.\kit.cmd demo  # Windows
./kit demo      # macOS/Linux
```

Open http://localhost:8501. Try: *"Show me the risk ledger"*, then *"Analyse PUMP-07"*, then *"Raise an alert and draft a work order."*

## What Is Next

SafetyLens in its current form uses synthetic in-memory data. The production path is:
- Connect `ingest_report` to a real document store (S3, SharePoint, a CMMS API).
- Connect `get_asset_timeline` to a time-series database.
- Add AgentCore Memory to persist the ledger across sessions.
- Add AgentCore Gateway to call existing CMMS work-order APIs directly.

The decision pattern stays unchanged. That is the point of separating the judgement from the infrastructure.

---

*Built with the AWS User Group Madurai Strands Decider hackathon kit.*
*Tags: #strands-decider-2b #guardrails*
