# TWIST-v1.0 — human-validated Track B key

First frozen release of the TWIST benchmark for intervention quality in
conversational memory. Paper: https://arxiv.org/abs/2609.28575

**The key:** `data/track_b_items_v1.0.jsonl` — 161 draft-alignment items
(38 contradicting / 61 aligned / 62 hard-negative) over the 10 public
LoCoMo conversations. Two independent gold-blind annotators (raw
κ = 0.565), adjudication dropped 39 defective candidates, retained-set
κ = 0.851. Scores must cite the key version (TWIST-v1.0); the key is
frozen — tuning against it is prohibited.

**Included:**
- Complete annotation trail: both annotators' raw answers, adjudication
  queue + decisions, 3-model LLM-council pre-screen (`annotation/`)
- Per-item outputs for 13 baseline configurations + Wilson/clustered
  bootstrap/Holm-McNemar statistics + multi-ingest analysis (`results/`)
- Standalone, dependency-free scorer (`reference/score_track_b.py`) and
  statistics tool — run your own system in an afternoon (protocol in
  README)
- Generation candidates and rejects with gate verdicts, for item-quality
  audits (`data/`)

**Headline result:** no tested configuration simultaneously achieves
high contradiction recall, high hard-negative specificity, and high
grounded recall — including the authors' own product.

Licenses: data + docs CC BY 4.0, code MIT. LoCoMo corpus not
redistributed. Errata are versioned: dispute items via issues.
