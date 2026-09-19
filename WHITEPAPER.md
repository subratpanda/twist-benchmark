# TWIST: A Benchmark for Tensions, Alignment, and Safe Recall in Conversational Memory

**White paper · draft v0.1 · 2026-07-08**
**Subrat Panda — MindTwin (mindtwin.me · subrat@mindtwin.me)**

---

## Abstract

Long-conversation memory benchmarks such as LoCoMo measure whether a memory
system can *recall* what was said. They do not measure whether a system
*notices when what is said stops being true* — when a speaker's stance
reverses months later, when a confident draft contradicts the record, when a
superseded belief keeps answering questions it should have retired, or when
sensitive content should never have been recallable at all. These are the
failure modes that actually cost money in deployed team-memory systems: the
churn signal nobody connected, the tone-deaf email that shouldn't have been
sent, the stale fact recalled as current, the customer's phone number
repeated back by a bot.

**TWIST** (**T**racking **W**ithin-speaker **I**nconsistencies,
**S**tance-reversals, and **T**ensions) is a proposed benchmark suite that
extends LoCoMo along exactly these axes. It reuses LoCoMo's corpora and
harness pattern, adds four task tracks — *unprompted contradiction detection,
output-time draft vetting, belief supersession, and safe recall* — and
specifies a validation protocol designed to avoid the answer-key and
judge-leniency weaknesses documented in LoCoMo itself. A system's TWIST
profile, reported alongside its LoCoMo score, distinguishes memory systems
that merely *store and retrieve* from systems that *maintain a coherent,
current, governable belief state*.

---

## 1. Motivation: what recall benchmarks cannot see

LoCoMo (Maharana et al., 2024) established the standard evaluation for
conversational memory: 10 multi-session conversations (~300–700 turns each),
1,540 answerable questions across single-hop, multi-hop, temporal, and
open-domain categories, graded by an LLM judge. Every serious memory vendor
reports against it, and it meaningfully rewards good ingest-time organization
(dated fact extraction, temporal modeling).

But LoCoMo's implicit model of memory is **append-only recall**: information
enters, information is retrieved, and correctness means faithfully returning
what was said. Deployed memory systems face a harder problem — beliefs
*change*, and the value of the system depends on how it behaves at those
change points:

| Real-world failure | Costs | Tested by LoCoMo? | TWIST track |
|---|---|---|---|
| A customer's champion reverses stance across months; nobody connects the two statements | A lost renewal | ✗ | **A — Tension detection** |
| A teammate drafts a message contradicting what the counterparty actually said | An unforced, trust-destroying error | ✗ | **B — Draft alignment** |
| A superseded fact ("we'll renew at current pricing") keeps answering questions as if current | Wrong decisions on stale data | partially (temporal QA asks *when*, not *which is current*) | **C — Belief supersession** |
| PII or crisis content becomes recallable to anyone who asks | A failed security review; real human harm | ✗ | **D — Safe recall** |

Several vendors ship features aimed at these behaviors — Mem0's graph
variant includes an ingest-time conflict detector; Zep/Graphiti invalidates
superseded edges bi-temporally; MindTwin surfaces a human-facing tension
ledger and vets outgoing drafts. **None of these capabilities has a public
benchmark.** Claims in this space are currently unfalsifiable — precisely the
condition LoCoMo fixed for recall. TWIST proposes to fix it for coherence,
alignment, and governance.

## 2. Design principles

1. **Extend, don't replace.** TWIST items are *injected into LoCoMo's
   existing conversations* wherever possible, so one corpus and one harness
   run produce both a LoCoMo score (recall) and a TWIST profile (coherence).
   A CS-domain track adds workplace realism without abandoning comparability.
2. **Every task must be falsifiable and separable.** A flat-RAG baseline
   (embed + retrieve + answer) must score near floor on Tracks A–C by
   construction; if it doesn't, the item is measuring recall, not the target
   capability, and is rejected during validation (§6.3).
3. **Hard negatives are first-class.** Detection tasks are trivially gamed by
   over-flagging. Every reversal arc is paired with a *consistent-but-similar*
   arc (nostalgia is not reversal; evolution-with-acknowledgment is not
   contradiction), and false-positive rates are scored symmetrically with
   detection rates.
4. **Do not repeat LoCoMo's validation failures.** LoCoMo's published
   weaknesses — ~6.4% incorrect gold answers and an LLM judge that accepted
   up to 63% of deliberately wrong answers (Penfield Labs audit, 2026) —
   are addressed with double annotation + adjudication, a judge stress-test
   reported *alongside* system scores, and a versioned errata process (§6).
5. **System-agnostic API.** Systems under test implement a minimal contract
   (§7); nothing assumes MindTwin's architecture. A system may decline a
   track (reported as N/A) — e.g., systems with no safety layer skip Track D
   rather than polluting the average.

## 3. Task tracks

### Track A — Unprompted tension detection

**Capability:** notice, *without being asked*, that a new statement
contradicts the speaker's own earlier statement, arbitrarily far back.

**Setup.** Each conversation contains *k* injected **reversal arcs**: an
anchor statement (session *i*), optional drift signals, and a reversal
statement (session *j*, with *j − i* spanning weeks to months of story
time). Each arc is paired with a **hard-negative arc** of matched topic and
surface form that is *not* a contradiction (consistent elaboration,
acknowledged change of mind, nostalgia, hypotheticals).

*Example (injected into LoCoMo).* Anchor (session 4): Caroline: "I'd never
move back to Europe — my whole life is here now." Reversal (session 17):
Caroline: "I've started applying for jobs in Stockholm." Hard negative
elsewhere: "I miss Sweden sometimes, especially in winter" (consistent
sentiment, not a reversal).

*Example (CS track).* Anchor: "Renewing is a no-brainer at this price."
Reversal: "At this price we're evaluating a competitor." Hard negative:
"Finance is reviewing all SaaS spend this year" (pressure signal, not a
stance reversal by itself).

**Protocol.** The system ingests the conversation session-by-session in
order. After each session, it may emit *tension objects*:
`{claim_evidence: [turn ids], new_evidence: [turn ids], rationale}`. No
prompt ever asks "is there a contradiction?" — emission must be unprompted
(ingest-time) or produced by a single generic end-of-corpus call
`flag_tensions()` for batch-only systems (reported as a separate mode).

**Metrics.**
- **Detection F1** over gold arcs (a detection matches if its evidence sets
  overlap the gold anchor and reversal turns).
- **False-flag rate** on hard-negative arcs (lower is better).
- **Detection latency**: sessions elapsed between the reversal turn and the
  flag (online mode only).
- **Attribution accuracy**: fraction of true detections citing the correct
  anchor turn (not merely *a* related turn).

### Track B — Output-time draft alignment

**Capability:** given a *proposed outgoing message*, decide whether sending
it would contradict what the counterparty/record actually says, and cite the
contradicted statements.

**Setup.** For each conversation, a balanced set of drafts:
- **Contradicting drafts** — fluent, plausible messages that contradict ≥1
  prior statement (gold: verdict + the contradicted turn ids).
- **Aligned drafts** — messages consistent with the record.
- **Hard-negative aligned drafts** — messages that *look* contradictory but
  are consistent (e.g., they explicitly acknowledge the stance change:
  "I know you said the price no longer works — here's a revised number").

*Example.* Draft: "Great that you're settled in the US for good!" → gold:
`aligned=false`, cites session-17 Stockholm applications. Hard negative:
"Exciting that you're exploring Stockholm — is a move on the table?" →
`aligned=true`.

**Protocol.** One call per item: `check_alignment(draft) →
{aligned: bool, conflicts: [{evidence: [turn ids], rationale}]}`. The full
conversation has already been ingested; no gold context is provided.

**Metrics.**
- **Balanced verdict accuracy** (aligned vs contradicting).
- **Hard-negative accuracy** reported separately (the anti-over-blocking
  number — a system that flags everything fails here).
- **Attribution accuracy** on true positives.

### Track C — Belief supersession

**Capability:** when a fact changes, answer with the *current* fact; retire
the superseded one; and (for systems that support it) respond to explicit
human resolution.

**Setup.** Injected **update chains**: fact F₀ stated, later superseded by
F₁ (sometimes F₂). Queries of three kinds:
- **Current-state**: "Where does Caroline want to live?" → gold: the latest
  belief (Stockholm), *not* the earlier one.
- **History-aware**: "Has her plan changed?" → gold acknowledges both states
  and their order (distinguishes supersession from deletion — the history
  must remain recallable, just not asserted as current).
- **Post-resolution** *(optional sub-track)*: the harness calls the system's
  resolution hook on a flagged tension, then re-queries; the refuted belief
  should stop being asserted as current.

**Metrics.**
- **Current-answer rate** (correct latest fact).
- **Stale-answer rate** (asserted the superseded fact as current — the
  headline failure).
- **History retention** (history-aware queries answered with both states).
- **Resolution responsiveness** (optional): stale-rate delta after the
  resolution hook.

### Track D — Safe recall

**Capability:** sensitive content is governed at write time, not filtered at
read time — and safety does not tax legitimate recall.

**Setup.** Injected into conversations:
- **PII plants**: emails, phone numbers, addresses appearing in natural
  turns. Probes: direct ("What is Dave's phone number?") and indirect
  ("Write down everything you know about Dave for a handoff doc").
- **Crisis plants**: a small number of self-harm-adjacent turns (constructed
  with clinical-review guidance; see §8 Ethics). Probes verify (a) the raw
  content is not recallable, (b) the ingest response carried an appropriate
  referral, (c) a content-free audit record exists.
- **Emotional hard negatives**: grief, stress, frustration turns that are
  *not* crisis and *must remain recallable* — the over-blocking control.

**Metrics.**
- **Leak rate**: probes returning raw planted PII (target: 0).
- **Suppression recall**: crisis plants correctly not stored / not
  recallable.
- **Over-blocking rate**: emotional hard negatives wrongly suppressed or
  unrecallable (MindTwin's measured baseline on 5,882 LoCoMo turns: 0
  false positives — the bar is provably reachable).
- **Referral correctness** (where applicable).

## 4. Dataset construction

### 4.1 Injection methodology (LoCoMo track)

Arcs, drafts, update chains, and safety plants are injected into the 10
existing LoCoMo conversations as *additional turns within existing sessions
and up to 3 appended sessions per conversation*, generated to match each
persona's voice and timeline. Injection rather than fresh generation
preserves: (a) LoCoMo comparability on the same corpus (original QA keys are
re-validated post-injection: any original question whose answer is perturbed
by an injection is flagged and versioned out); (b) realistic surrounding
noise — detections must survive ~600 turns of unrelated conversation.

Generation is LLM-drafted from parameterized templates (arc type, gap
length, drift signals, surface-similarity controls for hard negatives), then
**twice human-annotated** (§6.1). Every item carries machine-readable gold:
arc spans, evidence turn ids, current-fact labels, plant locations.

### 4.2 CS-domain track (TWIST-CS)

Six synthetic multi-channel workspaces (Slack threads + email + call
transcripts, 400–900 events each) model the deployed setting the LoCoMo
personas cannot: multiple speakers per side, channel switching (the anchor
in a call, the reversal in an email), and business arcs (renewal risk,
scope disputes, escalations). Same four tracks, same metrics. This track
exists because within-speaker tension detection is materially harder when
evidence is scattered across channels and interlocutors — the deployed
reality for team-memory products.

### 4.3 Proposed sizes (v1)

| Component | LoCoMo track | CS track |
|---|---|---|
| Reversal arcs (Track A) | 60 | 40 |
| Hard-negative arcs (Track A) | 60 | 40 |
| Drafts (Track B; ½ contradicting, ¼ aligned, ¼ hard-negative) | 200 | 120 |
| Update chains / queries (Track C) | 90 / 270 | 60 / 180 |
| PII plants / probes (Track D) | 40 / 80 | 30 / 60 |
| Crisis plants + emotional hard negatives (Track D) | 24 + 48 | 16 + 32 |

Sized so that a full run costs the same order as a LoCoMo run (≈2–3k LLM
calls for the system under test).

## 5. Scoring and reporting

- **Per-track scores only; no single headline number.** A composite invites
  gaming and hides trade-offs (a system can buy Track A recall with Track B
  over-blocking). The canonical report is a five-column profile: LoCoMo-J ·
  A (detection F1 / false-flag) · B (balanced / hard-negative) · C (current
  / stale) · D (leak / over-block).
- **Paired metrics are always reported together.** Detection without
  false-flag rate, or suppression without over-blocking, is not a valid
  TWIST citation — this rule is stated in the license/usage terms.
- **Modes are explicit.** Online (per-session emission) vs batch
  (end-of-corpus `flag_tensions()`) are separate leaderboard columns; batch
  is easier and must not be compared against online.

## 6. Benchmark validation (meta-evaluation)

The benchmark itself must be validated before any system score is meaningful.

### 6.1 Answer-key integrity
Every gold label is annotated independently by two humans; disagreements
adjudicated by a third. Items below κ = 0.8 category agreement are dropped.
The key is **versioned** (TWIST-v1.0, v1.1…) with a public errata process —
scores must cite the key version. *(Response to LoCoMo's 6.4% wrong-key
finding.)*

### 6.2 Judge robustness
Tracks A–C use structural matching (evidence-id overlap) wherever possible,
shrinking the judge's role to rationale checks and free-text answers. Where
an LLM judge is unavoidable (Track C phrasing), the judge ships with a
**decoy calibration set** — plausible-but-wrong answers that a sound judge
must reject ≥95% of — and every published run reports its judge's decoy
rejection rate next to its scores. *(Response to the 63% judge-leniency
finding.)*

### 6.3 Separability audit
Before release, three reference baselines run on every item: (i) flat RAG,
(ii) fact-extraction RAG (Mem0-style), (iii) an oracle given gold evidence.
Items where flat RAG succeeds on Tracks A/B without contradiction-specific
machinery, or where the oracle fails, are rejected or rewritten. Target
separability: flat RAG ≤ 15% detection F1 on Track A; oracle ≥ 95%.

### 6.4 Reproducibility standards
Pinned judge model + prompts, published harness, fixed retrieval budgets per
mode, one command per track. Vendor self-reports must link a raw per-item
output file — the norm the Mem0/Zep score dispute showed the field lacks.

## 7. System-under-test API

```
ingest(events)                      # ordered; may emit tension objects (online mode)
flag_tensions() -> [tension]        # batch mode alternative
recall(query) -> answer             # Tracks C, D probes
check_alignment(draft) -> verdict   # Track B
resolve(tension_id)                 # optional, Track C sub-track
```

Any memory system that can wrap these five calls can run TWIST. MindTwin
implements all five today (`process_incoming`, conflict ledger,
`engine.query`/bot, `check_draft_alignment`, `resolve_conflict`); Mem0/Zep
implement subsets — which is itself informative and reported as N/A columns.

## 8. Limitations and ethics

- **Synthetic data.** Injected arcs are LLM-drafted; naturalness is bounded
  by generation quality. Mitigations: persona-matched generation, human
  annotation, and the CS track's structural (not merely stylistic) realism.
  Real-workspace validation remains future work with consenting design
  partners.
- **Crisis content.** Track D's crisis plants use established, non-graphic
  test phrasings; the benchmark ships them in a separated, opt-in file with
  content warnings, and detection targets follow published crisis-line
  guidance. The goal is measuring *governance*, not building a crisis
  classifier corpus.
- **Author bias.** TWIST is proposed by a vendor whose product is built
  around these capabilities. The mitigations are structural: public gold
  keys, public harness, reference baselines run by the harness itself, and
  the separability audit (§6.3) that rejects items any recall system can
  solve. The benchmark is only credible if competitors can run it
  themselves and dispute items via errata — both are design requirements.

## 9. Expected baseline behavior (pre-registered predictions)

To make the benchmark's discriminative claim falsifiable, we pre-register
expectations: flat RAG scores near floor on A (no mechanism), moderately on
B (an LLM given draft + retrieved context catches blatant contradictions but
fails hard negatives and attribution), poorly on C stale-rate (retrieval has
no currency notion), and N/A on D. Fact-extraction systems improve C
substantially (dated facts) but remain weak on A unless they ship explicit
conflict detection. Systems with contradiction machinery (Mem0g's conflict
detector, Zep's edge invalidation, MindTwin's inner voice) should separate
from both — *from each other* on online-vs-batch mode, attribution, and the
Track B/D governance columns. If these separations do not materialize, the
benchmark has failed its purpose and v1 does not ship.

## 10. Roadmap

- **v0.1 (this document):** task and validation spec.
- **v0.5:** LoCoMo-track injection pipeline + Track B item set (cheapest to
  build, immediately runnable against MindTwin, Mem0, Zep) + reference
  baselines + judge decoy set. *Estimated effort: 2–3 weeks part-time.*
  *Status 2026-07-08: Track B shipped in preliminary form — 153 verified
  items, generator + harness + flat-RAG baseline in-repo, first results in
  [`TRACK_B_RESULTS.md`](TRACK_B_RESULTS.md). Both §9
  predictions about flat RAG confirmed; the item set also surfaced real
  product gaps (vetting recall at scale), which is the intended function.*
- **v1.0:** all four tracks on both corpora, double annotation complete,
  separability audit passed, public leaderboard + per-item outputs.
- **Community:** errata process, N≥2 external systems run by their own
  authors before "benchmark" is used without "proposed" in front of it.

## References

- Maharana et al., *Evaluating Very Long-Term Conversational Memory of LLM
  Agents* (LoCoMo), 2024 — github.com/snap-research/locomo
- Penfield Labs, *We audited LoCoMo* (answer-key and judge-leniency audit), 2026
- Mem0, *State of AI Agent Memory*, 2026 — mem0.ai/blog/state-of-ai-agent-memory-2026
- Zep/Graphiti bi-temporal knowledge-graph documentation, 2025–2026
- *Graph-Native Cognitive Memory for AI Agents: Formal Belief Revision
  Semantics*, arXiv:2603.17244, 2026
- MindTwin LoCoMo results and harness — `docs/BENCHMARKS.md`,
  `apps/api/mindtwin/scripts/locomo_bench.py` (this repository)
