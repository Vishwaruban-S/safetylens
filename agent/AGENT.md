# SafetyLens — Factory Safety Anomaly Detection Agent

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
