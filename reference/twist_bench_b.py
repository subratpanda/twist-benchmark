"""TWIST Track B harness — draft-alignment vetting (whitepaper §3B, §5).

Runs two systems over the Track B item set:

  mindtwin : the product path — LoCoMo conversation ingested via
             `ingest_events` (safety + facts), then
             `inner_voice.check_draft_alignment` per draft.
  flatrag  : reference baseline (whitepaper §6.3) — same stored turns, plain
             similarity retrieval, one LLM call asked directly whether the
             draft contradicts the retrieved excerpts. No tension machinery.

Scoring (paired metrics; see §5 — never cite one without its pair):
  - contradicting accuracy  : flagged the contradicting drafts
  - aligned accuracy        : did NOT flag ordinary aligned drafts
  - hard-negative accuracy  : did NOT flag look-contradictory-but-consistent
  - balanced accuracy       : mean(contradicting, aligned)
  - attribution accuracy    : among correctly flagged drafts, cited at least
                              one gold evidence turn (turn-id match; fact
                              citations that can't map to a turn are counted
                              and reported separately, not credited)

Usage:
    uv run python -m mindtwin.scripts.twist_bench_b \
        --data locomo10.json --items benchmarks/twist/track_b_items_v0.jsonl \
        --systems mindtwin,flatrag --out results.json
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from mindtwin.db.session import get_admin_session
from mindtwin.engine import inner_voice
from mindtwin.engine.engine import CognitiveEngine
from mindtwin.engine.llm import build_llm_client
from mindtwin.ingestion.service import ingest_events
from mindtwin.models import Team
from mindtwin.scripts.locomo_bench import conversation_events
from mindtwin.scripts.twist_generate_b import strip_fence

_BASELINE_SYSTEM = (
    "You review a proposed outgoing message against excerpts from a "
    "conversation record. Each excerpt is prefixed with its turn id. Decide "
    "whether SENDING the draft would contradict what the recipient previously "
    "said. Output ONLY JSON: {\"aligned\": true|false, \"evidence\": "
    "[\"<turn id>\", ...]} — evidence lists the turn ids the draft "
    "contradicts (empty if aligned)."
)


@dataclass
class ItemResult:
    item_id: str
    conv: str
    type: str
    gold_aligned: bool
    pred_aligned: bool
    verdict_correct: bool
    attributed: bool | None   # only for correctly-flagged contradicting items
    cited_fact_only: bool     # flagged, but all citations were fact-memories


def ingest_conversations(data: list[dict]) -> dict[str, uuid.UUID]:
    """Ingest each LoCoMo conversation into a fresh team (product path)."""
    teams: dict[str, uuid.UUID] = {}
    for n, sample in enumerate(data, 1):
        conv_id = str(sample.get("sample_id"))
        slug = f"twistb-{conv_id.lower()}-{uuid.uuid4().hex[:6]}"
        with get_admin_session() as session:
            team = Team(name=f"TWIST-B {conv_id}", slug=slug,
                        allowed_domains=[f"{slug}.example.com"])
            session.add(team)
            session.flush()
            teams[conv_id] = team.id
        events, _ = conversation_events(sample["conversation"])
        res = ingest_events(teams[conv_id], events, source_subkey=f"twistb-{conv_id}")
        print(f"[{n}/{len(data)}] {conv_id}: ingested {res.ingested} turns "  # noqa: T201
              f"(+{res.facts} facts)")
    return teams


VALID_ABLATIONS = {"no-probes", "no-fact-channel", "single-call-vet"}


def run_mindtwin(engine: CognitiveEngine, item: dict,
                 ablations: set[str] | None = None) -> tuple[bool, list[str], bool]:
    """Returns (pred_aligned, cited_turn_ids, cited_fact_only)."""
    ab = ablations or set()
    res = inner_voice.check_draft_alignment(
        engine, item["draft"],
        use_probes="no-probes" not in ab,
        fact_channel="no-fact-channel" not in ab,
        chunked="single-call-vet" not in ab,
    )
    cited_ids = [c.get("memory_id") for c in res.get("conflicts", []) if c.get("memory_id")]
    turn_ids: list[str] = []
    fact_only = False
    if cited_ids:
        got = engine.vault.collection.get(ids=cited_ids, include=["metadatas"])
        srcs = [m.get("source_id") for m in got.get("metadatas", [])]
        turn_ids = [s for s in srcs if s and not s.startswith("fact:")]
        fact_only = not turn_ids
    return bool(res.get("aligned", True)), turn_ids, fact_only


def run_flatrag(engine: CognitiveEngine, llm, item: dict,
                k: int = 12) -> tuple[bool, list[str], bool]:
    """Baseline: similarity-retrieve raw turns, single direct LLM judgment."""
    mems = engine.query(item["draft"], n_results=k, include_cold=True,
                        types=("episodic", "conversation")).memories
    excerpts = "\n".join(f"{m.source_id}: {m.document}" for m in mems if m.source_id)
    user = f"EXCERPTS:\n{excerpts}\n\nDRAFT:\n\"{item['draft']}\"\n\nJSON:"
    try:
        parsed = json.loads(strip_fence(llm.complete(_BASELINE_SYSTEM, user,
                                                     max_tokens=200)))
        aligned = bool(parsed.get("aligned", True))
        evidence = [e for e in (parsed.get("evidence") or []) if isinstance(e, str)]
    except Exception:
        aligned, evidence = True, []
    return aligned, evidence, False


def score(results: list[ItemResult]) -> dict:
    by_type: dict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_type[r.type].append(r)

    def acc(rows: list[ItemResult]) -> float:
        return sum(r.verdict_correct for r in rows) / len(rows) if rows else 0.0

    contra = by_type.get("contradicting", [])
    flagged_right = [r for r in contra if r.verdict_correct]
    attributed = [r for r in flagged_right if r.attributed]
    fact_only = [r for r in flagged_right if r.cited_fact_only]
    return {
        "n": len(results),
        "contradicting_acc": round(acc(contra), 3),
        "aligned_acc": round(acc(by_type.get("aligned", [])), 3),
        "hard_negative_acc": round(acc(by_type.get("hard_negative", [])), 3),
        "balanced_acc": round((acc(contra) + acc(by_type.get("aligned", []))) / 2, 3),
        "attribution_acc": round(len(attributed) / len(flagged_right), 3)
        if flagged_right else 0.0,
        "fact_only_citations": len(fact_only),
    }


def main() -> None:  # noqa: PLR0915
    ap = argparse.ArgumentParser(description="Run TWIST Track B.")
    ap.add_argument("--data", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--systems", default="mindtwin,flatrag",
                    help="comma-separated; flatrag accepts per-system "
                         "backend specs 'flatrag[:provider[:model]]' so one "
                         "ingest serves several backends, e.g. "
                         "mindtwin,flatrag:openai,flatrag:gemini")
    ap.add_argument("--conversations", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default=None)
    ap.add_argument("--provider", default=None,
                    help="LLM provider for the flatrag baseline judgment: "
                         "openai|anthropic|gemini (default: config "
                         "default_llm_provider). The mindtwin system path "
                         "uses the engine's configured LLM regardless.")
    ap.add_argument("--model", default=None,
                    help="model override (default: provider default)")
    ap.add_argument("--ablate", default="",
                    help="comma-separated vetting mechanisms to disable "
                         f"({', '.join(sorted(VALID_ABLATIONS))}); applies to "
                         "the mindtwin system only (docs/ABLATION_SPEC.md §4b)")
    args = ap.parse_args()

    ablations = {a.strip() for a in args.ablate.split(",") if a.strip()}
    unknown = ablations - VALID_ABLATIONS
    if unknown:
        raise SystemExit(f"unknown ablation(s): {sorted(unknown)}; "
                         f"valid: {sorted(VALID_ABLATIONS)}")

    with open(args.data) as f:
        data = json.load(f)
    if args.conversations:
        data = data[: args.conversations]
    conv_ids = {str(s.get("sample_id")) for s in data}
    with open(args.items) as f:
        items = [json.loads(line) for line in f if line.strip()]
    items = [i for i in items if i["conv"] in conv_ids]
    print(f"{len(items)} items across {len(conv_ids)} conversations")  # noqa: T201

    teams = ingest_conversations(data)
    engines = {c: CognitiveEngine(t) for c, t in teams.items()}
    t0 = time.time()
    all_scores: dict[str, dict] = {}
    all_results: dict[str, list[dict]] = {}

    for system in args.systems.split(","):
        kind, _, backend = system.partition(":")
        llm = None
        if kind != "mindtwin":
            prov, _, mod = backend.partition(":")
            llm = build_llm_client(prov or args.provider,
                                   model=mod or args.model)
            print(f"{system}: baseline LLM {llm.provider.value}/{llm.model}")  # noqa: T201

        def run_item(item: dict, *, _kind=kind, _llm=llm) -> ItemResult:
            eng = engines[item["conv"]]
            if _kind == "mindtwin":
                aligned, turn_ids, fact_only = run_mindtwin(eng, item, ablations)
            else:
                aligned, turn_ids, fact_only = run_flatrag(eng, _llm, item)
            gold_aligned = item["type"] != "contradicting"
            correct = aligned == gold_aligned
            attributed = None
            if item["type"] == "contradicting" and correct:
                attributed = bool(set(turn_ids) & set(item["evidence"]))
            return ItemResult(item["id"], item["conv"], item["type"],
                              gold_aligned, aligned, correct, attributed, fact_only)

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(run_item, items))
        all_scores[system] = score(results)
        all_results[system] = [asdict(r) for r in results]
        s = all_scores[system]
        print(f"\n=== {system} ===")  # noqa: T201
        for k2, v in s.items():
            print(f"  {k2:22s} {v}")  # noqa: T201

    print(f"\nwall {time.time() - t0:.0f}s")  # noqa: T201
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"scores": all_scores, "results": all_results}, f, indent=1)
        print(f"per-item results -> {args.out}")  # noqa: T201


if __name__ == "__main__":
    main()
