# TWIST Track B — v0 preliminary results (draft-alignment vetting)

*2026-07-08 · items `benchmarks/twist/track_b_items_v0.jsonl` (v0, 153 items) ·
harness `apps/api/mindtwin/scripts/twist_bench_b.py` · engine @ `ba67221` ·
per-item outputs `benchmarks/twist/track_b_results_v0.json`*

**Status: preliminary.** v0 items are LLM-generated and LLM-verified but not
yet human-annotated (whitepaper §6.1), and the hard-negative subset is small
(n=19). Numbers below are for diagnosing systems and validating the
benchmark's discriminative power — not yet for public leaderboard claims.

## Setup

153 draft-vetting items over the 10 LoCoMo conversations (93 contradicting /
41 aligned / 19 hard-negative), each generated with dia_id gold evidence and
kept only if an independent LLM verification call — grading from *only* the
cited evidence — agreed with the gold label (45 of 198 generated items were
dropped by this gate). Two systems:

- **mindtwin** — the product path: conversation ingested via `ingest_events`
  (safety + fact extraction), then `check_draft_alignment` per draft
  (topic-probe expansion → dual-channel retrieval → vetting critic).
- **flatrag** — the whitepaper §6.3 reference baseline: same stored turns,
  plain similarity retrieval (top-12), one direct LLM judgment.

## Results (paired metrics — never cite one without its pair)

| Metric | mindtwin | flatrag |
|---|---|---|
| Contradicting accuracy (recall of true contradictions) | **0.108** | **1.000** |
| Aligned accuracy (didn't flag ordinary aligned drafts) | **1.000** | 0.780 |
| Hard-negative accuracy (didn't flag look-alike consistent drafts) | **1.000** | **0.368** |
| Balanced accuracy | 0.554 | 0.890 |
| Attribution accuracy (cited a gold turn when correctly flagging) | 0.500 | 0.419 |

## Reading the table honestly

**Neither system passes Track B. They fail in opposite directions — which is
the benchmark doing its job.**

- **flatrag cries wolf.** It flags every true contradiction (1.0) but also
  flags 22% of ordinary aligned drafts and **63% of hard negatives** —
  drafts that merely mention a change or acknowledge it. Deployed, this is
  an assistant that objects to safe messages two times out of three; users
  would disable it within a week. Its 0.89 balanced accuracy is exactly the
  kind of unpaired headline the whitepaper's reporting rules prohibit.
- **mindtwin stays silent too often.** It never over-flags (1.000 / 1.000 on
  the governance columns — deployed, it only speaks when it should) but at
  10-conversation scale it caught only 10 of 93 true contradictions.
  Diagnostic split of the 83 misses:
  - **40 retrieval misses** — the gold evidence never entered the candidate
    set. Probing by topic helps but does not solve coverage over ~600-turn
    histories.
  - **43 detection misses** — evidence was in the candidate list (~30
    claims) and the critic still didn't flag it: candidate dilution.

## Pre-registered predictions vs outcome (whitepaper §9)

| Prediction | Outcome |
|---|---|
| flat RAG "catches blatant contradictions but fails hard negatives" | **Confirmed** (1.0 contradicting, 0.368 hard-negative) |
| systems with contradiction machinery separate from flat RAG | **Confirmed on governance columns** (1.0/1.0 vs 0.78/0.368), **not yet on recall** |
| attribution is hard for retrieval-only systems | Directionally confirmed (0.419), but mindtwin's 0.5 is nothing to cite either |

## What this changes in the product roadmap

The three fixes this harness already produced (dual-channel vetting
retrieval, dedicated vetting critic, topic-probe expansion) were necessary
but insufficient at scale. The measured 48/52 detection/retrieval split
points at two next mechanisms:

1. **Pairwise (chunked) verification instead of one 30-claim critic call** —
   the item-verifier prompt, judging one draft against 3–5 claims at a time,
   was near-perfect during dataset validation; the same shape should recover
   most of the 43 detection misses at ~6× the LLM cost of the current single
   call (acceptable: vetting is a deliberate, user-invoked action).
2. **Fact-provenance ("facts with receipts")** — extracted facts should carry
   the dia_ids of the turns they came from; then the fact channel (which
   retrieves well) can contribute attribution instead of dead-ending, and
   fact-level hits can seed a second-pass turn lookup for the 40 retrieval
   misses.

Re-run against items v0 after each mechanism lands; **no prompt or parameter
may be tuned against these 153 items directly** (they are now the held
evaluation set; tuning items must be freshly generated).

## What this changes in the benchmark (v0 → v1)

- Hard negatives need volume (19 → ≥60) and human annotation before any
  public claim; they are the metric that separates systems.
- The generation→verification gate worked (23% rejection rate, and spot
  checks agree with the survivors), supporting the §4 methodology.
- Item difficulty correlates with conversation length — v1 should stratify
  reporting by history size, since vetting-at-scale is evidently the real
  problem.

## Update (same day): chunked verification — a negative result, and a corrected diagnosis

Mechanism #1 (pairwise chunked verification, commit `ef9d763`) was built and
re-run against the held set:

| Metric | single-call | chunked |
|---|---|---|
| Contradicting accuracy | 0.108 | 0.140 |
| Aligned accuracy | 1.000 | 0.976 |
| Hard-negative accuracy | 1.000 | 1.000 |
| Attribution accuracy | 0.500 | 0.231 |

**The predicted recall gain did not materialize** (+3 items, within noise),
and attribution *fell* — facts, judged in their own chunks, get flagged more
often and carry no turn linkage. Two follow-up diagnostics explain why the
original "43 detection misses = attention dilution" attribution was wrong:

1. **Truncation exonerated:** 0 of 80 missed items had evidence cut by the
   candidate cap (average pool 26 < cap 40).
2. **Reproducing failing chunks verbatim** (gold evidence + 4 distractors,
   5-claim context) shows the critic still not flagging — because in these
   items **the cited evidence turn's standalone text does not carry the
   contradicted claim.** Example: draft tells *Melanie* "good to hear you're
   getting into adoption"; the gold turn is Melanie saying "Thanks, Caroline!
   … what made you decide to adopt?" — the contradiction (it's *Caroline*
   adopting) lives in conversational role inference, not in the quoted text.
   The generation-time verifier accepted such items because its framing
   ("what X actually said") papered over the gap.

**Corrected read of the 80 misses:** ~half are retrieval misses, and most of
the remainder are items whose gold evidence is not self-contained — an
**item-quality defect**, the same lesson fact extraction taught at ingest
("standalone, names the person, carries the claim"). The system genuinely
catches direct stance contradictions; role/implication flips across turns
remain open for both the product and the benchmark's v1 item spec.

**Actions taken / queued:**
- Chunked verification is kept (principled, tested, no governance
  regression) but is documented as *not* a recall fix on v0 items.
- **Benchmark v1 requirement added:** evidence turns must be self-contained —
  the verification gate must grade items from the *standalone* evidence text
  exactly as a system under test will see it, with no generation context.
- Product next step unchanged: fact provenance ("facts with receipts"), which
  targets both the attribution collapse and the retrieval half of the misses.

## Reproduce

```bash
uv run python -m mindtwin.scripts.twist_generate_b \
  --data locomo10.json --out benchmarks/twist/track_b_items_v0.jsonl
uv run python -m mindtwin.scripts.twist_bench_b \
  --data locomo10.json --items benchmarks/twist/track_b_items_v0.jsonl \
  --systems mindtwin,flatrag --out benchmarks/twist/track_b_results_v0.json
```
