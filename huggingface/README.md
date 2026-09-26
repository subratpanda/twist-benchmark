---
license: cc-by-4.0
task_categories:
- text-classification
language:
- en
tags:
- benchmark
- conversational-memory
- llm-agents
- memory-systems
- contradiction-detection
- belief-revision
- intervention-quality
- evaluation
pretty_name: TWIST Track B v1.0
size_categories:
- n<1K
configs:
- config_name: default
  data_files:
  - split: test
    path: test.jsonl
---

# TWIST Track B v1.0 — draft-alignment vetting for conversational memory

**TWIST** benchmarks *intervention quality* in conversational memory
systems: not "can you recall what was said" but "do you act correctly
when what was said stops being true — and stay silent when acting would
be wrong." Track B, the human-validated first track, tests **output-time
draft vetting**: given a proposed outgoing message and a long
conversation record, should it be flagged as contradicting the record?

- **Paper:** [arXiv:2609.28575](https://arxiv.org/abs/2609.28575)
- **Full benchmark, harness, annotation trail, per-item baseline
  outputs:** https://github.com/subratpanda/twist-benchmark
- **Key version:** TWIST-v1.0 (frozen 2026-09-20). Scores must cite the
  key version.

## What makes it different

Every detection metric ships with a **surface-matched hard-negative
control** — drafts that *look* contradictory but are safe. Flag
everything and you fail; stay silent and you fail. The paired metrics
are the benchmark: contradiction recall is not citable without
hard-negative specificity. Scoring is fully structural (boolean verdict
+ turn-id set intersection under a top-3 evidence budget) — no LLM
judge anywhere, so scores are reproducible bit-for-bit from output
files.

## Dataset

161 items over the 10 public [LoCoMo](https://github.com/snap-research/locomo)
conversations: **38 contradicting / 61 aligned / 62 hard-negative**,
each with machine-readable gold (evidence turn ids, verbatim
contradicted quote for contradicting items, scenario family,
history-length metadata).

| field | meaning |
|---|---|
| `id` | item id (`conv-26:v1b1`) |
| `conv` | LoCoMo conversation id |
| `speaker` | draft recipient (whose statements the item probes) |
| `type` | `contradicting` \| `aligned` \| `hard_negative` |
| `family` | scenario family (B1–B3, B-AL, HN-B1–B3) |
| `draft` | the proposed outgoing message (the ONLY text a system sees) |
| `evidence` | gold evidence turn ids (`dia_id`s in LoCoMo) |
| `contradicted_quote` | verbatim words from a cited turn the draft is incompatible with (empty for non-contradicting) |
| `history_turns` | conversation length, for stratified reporting |

**Human validation:** two independent, gold-blind annotators over 200
candidates (raw κ = 0.565), adjudication dropped 39 defective items;
retained-set agreement **κ = 0.851**. The complete annotation trail
(both annotators' raw answers, adjudication decisions, LLM-council
pre-screen) is public in the GitHub repository.

## How to evaluate a system

1. Fetch `locomo10.json` from
   [snap-research/locomo](https://github.com/snap-research/locomo)
   (not redistributed here) and ingest the 10 conversations into your
   system.
2. For each item, call your vetting surface with **only the `draft`**
   (never the gold fields). Record verdict + cited turn ids.
3. Score with the standalone, dependency-free scorer:
   [`reference/score_track_b.py`](https://github.com/subratpanda/twist-benchmark/blob/master/reference/score_track_b.py).

Rules: answer every item; only the first 3 cited ids count; the key is
frozen — **no tuning against it**; report paired metrics together.

## Baseline results (13 configurations)

No tested configuration simultaneously achieves high contradiction
recall, high hard-negative specificity, and high grounded recall. Flat
RAG detects 0.76–0.97 of contradictions but falsely flags 16–43% of
hard negatives; a restraint-oriented deployed system almost never
over-flags but catches 42%; given gold evidence, all backends reach
1.000 recall. Full ladder, per-item outputs, and statistics:
[results/](https://github.com/subratpanda/twist-benchmark/tree/master/results).

## Provenance & license

Items are LLM-generated against LoCoMo transcripts, filtered by
structural gates (recipient-spoken evidence, verbatim-quote grounding)
and an independent verification pass, then human-validated as above.
`contradicted_quote` fields contain short verbatim excerpts from LoCoMo
turns; the LoCoMo corpus itself is **not** redistributed — obtain it
from the source under its own terms. This dataset: **CC BY 4.0**.

## Citation

```bibtex
@article{panda2026twist,
  title         = {TWIST: A Proposed Benchmark for Intervention Quality in
                   Conversational Memory, with a Human-Validated
                   Draft-Alignment Track},
  author        = {Panda, Subrat},
  year          = {2026},
  eprint        = {2609.28575},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI}
}
```
