# SafetyLens

**An AI safety analyst that ingests factory near-miss reports and maintenance logs,**

An agent on AWS that hands one decision to **Strands Decider 2B** and the conversation to **Amazon Bedrock**. Decision pattern: `#guardrails`. Tags: `#strands-decider-2b` `#guardrails`.

- Video: (add the YouTube/Vimeo link)
- Article: (add the AWS Builder Center link)
- Deployed: run `./kit deploy`

## The project

- **Pitch:** An AI safety analyst that ingests factory near-miss reports and maintenance logs,
  detects normalised deviance, red-team argues every alert, and Strands Decider 2B judges
  whether to raise or suppress it — then drafts a work order and evidence brief.
- **Who it is for:** Safety managers, plant engineers and operations leads at small and
  mid-size factories that cannot afford enterprise safety software.
- **Features:**
  - Ingest messy near-miss reports and maintenance notes and extract structured events
    (asset, hazard, which safety barrier failed or was bypassed).
  - Build a per-asset anomaly timeline and detect normalised deviance (same anomaly
    tolerated repeatedly).
  - Red-team agent argues the finding is noise; Strands Decider 2B is the guardrail judge
    that decides whether to raise the alert, suppress it, or escalate to a human.
  - Draft a work order and one-page evidence brief with every claim linked to its source.
  - Ignored Warnings Ledger: every open risk, how long it has been open, and its cost
    exposure — the screen CEOs remember.
  - Deployed on Amazon Bedrock AgentCore Runtime.
- **The decision handed to Strands Decider 2B:** given the anomaly evidence AND the
  red-team's noise argument, is the alert signal strong enough to raise, or is the noise
  argument convincing enough to suppress? Also: how severe is the risk, and is this a
  case of normalised deviance? (`#guardrails`)
- **Why a decision model:** the red-team/judge loop runs on every anomaly, so it must be
  fast, cheap and consistent. The decider reads the evidence text and the counter-argument
  and returns calibrated probabilities the code can branch on, without the cost or
  latency of a second LLM call.
- **Amazon Bedrock:** the conversation, report ingestion and text generation
  (Amazon Nova Pro by default).



## How it is built

Strands Agents with Amazon Bedrock for the generative part, the tools in `agent/tools.py`, deployed to Amazon Bedrock AgentCore Runtime, and one decision handed to Strands Decider 2B at a clear seam in the agent's path.

Every Amazon Bedrock model id is set in `.env` (`BEDROCK_MODEL_ID`, and `BEDROCK_SMALL/MEDIUM/LARGE_MODEL_ID` for routing), Amazon Nova by default.



## Reusable decision pattern: safety-alert guardrail  (#guardrails)

The pattern lives in [`agent/decision.py`](agent/decision.py), labelled `REUSABLE DECISION PATTERN`. It contains the typed questions and their options, the wiring that puts the decision in the agent's path, and a note on how to reuse it:

```text
REUSABLE DECISION PATTERN: safety-alert guardrail  (#guardrails)

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
```

Typed questions:

```python
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
```

## Measured

Decider: strands-decider-2b-hobson-v19 (ugmdu). 14 labelled cases, 42 decisions.

| | Strands Decider 2B |   |
|---|---|---|
| Accuracy | 73.8% | - |
| Latency p50 per decision | 3876 ms | - |
| Latency p95 | 4085 ms | - |
| Cost per 1,000 decisions | $0.0350 | $0.0000 |
| Answered alone at confidence >= 0.7 | 67% | - |
| Accuracy when confident | 92.9% | - |

Reproduce with `./kit bench` (cases in `agent/eval_cases.jsonl`; charts in `submission/bench/`).

## Run it

```bash
./kit setup        # checks your machine and AWS, sets up Strands Decider 2B (local model or hosted API)
./kit demo         # web UI at http://localhost:8501
./kit bench        # the decision vs an Amazon Bedrock LLM
./kit deploy       # Amazon Bedrock AgentCore Runtime
```

Requirements: Python 3.10+, AWS credentials with Amazon Bedrock access, and for deploying, Node 20+ with the AgentCore CLI (`./kit agentcore-cli`).

## Credits

Built with the AWS User Group Madurai Strands Decider hackathon kit (https://github.com/awsugmdu). The kit's reusable library (`decider_kit/`, the demo UI, the video and article tooling, and the Kiro steering, skills and hooks) is the user group's; the agent in `agent/` is mine. Strands Decider: https://github.com/strands-labs/strands-decider (Apache-2.0).

Licence: Apache-2.0 (see LICENSE).
