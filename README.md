# TWIST — a benchmark for what memory benchmarks can't see

**T**racking **W**ithin-speaker **I**nconsistencies, **S**tance-reversals, and
**T**ensions. A proposed benchmark suite for conversational memory systems,
extending [LoCoMo](https://github.com/snap-research/locomo).

Recall benchmarks measure whether a memory system can *retrieve what was
said*. Nothing measures whether it *notices when what was said stops being
true* — the failure modes that actually cost money in deployed systems:

| Track | Capability | Paired failure it also scores |
|---|---|---|
| **A — Tension detection** | Flag a stance reversal *unprompted*, months apart | False-flagging nostalgia, hypotheticals, pressure signals |
| **B — Draft alignment** | Vet a *proposed outgoing message* against the record, with citations | Flagging drafts that merely acknowledge a change |
| **C — Belief supersession** | Answer with the *current* fact; retire the stale one; keep history | Deleting history; retiring facts that still coexist |
| **D — Safe recall** | PII / crisis content governed at write time | Over-blocking emotional-but-legitimate content |

**The pairing is the rule, not a convention:** a system that flags everything
aces detection and is unusable. Citing any TWIST metric without its pair is
not a valid TWIST citation.

Release post: https://mindtwin.me/blog/twist-benchmark

## Status: v0 — Track B live, preliminary

| | |
|---|---|
| Spec (all four tracks) | [`WHITEPAPER.md`](WHITEPAPER.md) |
| Scenario catalog (26 families + hard negatives) | [`SCENARIOS.md`](SCENARIOS.md) |
| Track B item set — 153 verified items | [`data/track_b_items_v0.jsonl`](data/track_b_items_v0.jsonl) |
| Track B first results + a negative result | [`TRACK_B_RESULTS.md`](TRACK_B_RESULTS.md) |
| Raw per-item outputs (both systems, both runs) | [`results/`](results/) |

v0 items are LLM-generated and LLM-verified but **not yet human-annotated**
(the whitepaper's §6.1 protocol); the hard-negative subset is small (n=19).
Treat all numbers as preliminary — they are published for methodology
transparency, not leaderboard claims.

### First results (Track B, 153 items)

| Metric (paired) | MindTwin | flat-RAG baseline |
|---|---|---|
| Caught true contradictions (n=93) | 0.14 | **1.00** |
| Didn't flag aligned drafts (n=41) | **0.98** | 0.78 |
| Didn't flag hard negatives (n=19) | **1.00** | 0.37 |

Neither system passes; they fail in opposite directions (miss vs cry-wolf) —
which is the benchmark's discriminative claim working. Both of the
whitepaper's pre-registered §9 predictions about flat RAG were confirmed on
the first run. The results doc also reports a **negative result** (a
mechanism we shipped that didn't move recall) and the item-quality defect the
investigation surfaced — the standard this benchmark commits to.

## Data format (Track B)

One JSON object per line:

```json
{
  "id": "conv-26:b3",
  "conv": "conv-26",
  "speaker": "Caroline",
  "type": "contradicting | aligned | hard_negative",
  "draft": "Hi Caroline, I heard you're not interested in volunteering ... anymore. Is that true?",
  "evidence": ["D15:9", "D19:7"],
  "rationale": "one-sentence gold rationale"
}
```

`evidence` ids reference turns (`dia_id`) in LoCoMo's public corpus
(`data/locomo10.json` in [snap-research/locomo](https://github.com/snap-research/locomo)),
which is **not redistributed here** — fetch it from the source.

## Running a system against Track B

A system under test wraps five calls (whitepaper §7); Track B needs two:

```
ingest(conversation_events)            # full LoCoMo conversation, in order
check_alignment(draft) -> {aligned: bool, conflicts: [{evidence_ids, rationale}]}
```

Score: verdict accuracy per item type (gold: `contradicting → aligned=false`,
everything else `aligned=true`) + attribution (a cited evidence id ∈ gold
evidence, on correctly flagged items). Report all paired metrics.

[`reference/`](reference/) contains the item generator and the harness we
used (including the flat-RAG baseline). They currently run inside the
MindTwin codebase (imports noted at top of each file) and are published as
reference implementations of the generation → independent-verification gate
and the scoring.

## Roadmap (whitepaper §10)

- **v1:** regenerate Track B under the self-containment rules (SCENARIOS.md,
  "item-quality rules"), grow hard negatives to ≥60, human double-annotation
  with adjudication, Tracks A/C/D injection pipelines, judge decoy set.
- **Ship criterion (pre-registered):** if reference baselines and
  contradiction-capable systems don't separate, v1 does not ship.

## Disputes and errata

Item keys are versioned. If you believe an item is wrong, open an issue with
the item `id` and your argument; accepted disputes land in a public errata
file and a version bump. Benchmarks authored by vendors (we are one — MindTwin
builds a memory product) are only credible under exactly this process.

## License

- Code (`reference/`): MIT.
- Dataset (`data/`) and documents: CC BY 4.0.
- LoCoMo conversations are upstream and under their own terms.

## Citation

```bibtex
@misc{twist2026,
  title  = {TWIST: A Benchmark for Tensions, Alignment, and Safe Recall in Conversational Memory},
  author = {Panda, Subrat},
  year   = {2026},
  url    = {https://github.com/subratpanda/twist-benchmark}
}
```

Contact: subrat@mindtwin.me · https://mindtwin.me
