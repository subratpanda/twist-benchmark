# NOTE (public reference copy): this script runs inside the MindTwin
# codebase (imports below). Published as a reference implementation of the
# TWIST generation/verification gate and scoring — see WHITEPAPER.md §7 for
# the system-agnostic API a fresh runner needs.
"""TWIST Track B item generator (draft-alignment; whitepaper §3B, §4).

For each LoCoMo conversation, generates a balanced set of *drafts* — messages
a friend might send to one of the two speakers — with machine-readable gold:

  contradicting  : sending it would contradict the speaker's own prior
                   statements (gold: aligned=false + evidence dia_ids)
  aligned        : consistent with the record (gold: aligned=true)
  hard_negative  : looks contradictory on the surface but is consistent —
                   e.g. it explicitly acknowledges the change (gold: aligned=true)

Every generated item then passes an independent LLM verification call (the
item is graded from ONLY its cited evidence turns + the draft); items whose
verdict disagrees with gold are dropped and reported. Output is JSONL, one
item per line:

  {id, conv, speaker, type, draft, evidence: [dia_ids], rationale}

Usage:
    uv run python -m mindtwin.scripts.twist_generate_b \
        --data locomo10.json --out benchmarks/twist/track_b_items_v0.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor

from mindtwin.engine.llm import build_llm_client

PER_CONV = {"contradicting": 10, "aligned": 5, "hard_negative": 5}

_GEN_SYSTEM = (
    "You create benchmark items that test whether an AI memory system can vet "
    "a PROPOSED outgoing message against a conversation record. You will read "
    "a long two-person conversation (each turn has an id like D1:3). Generate "
    "draft messages that a mutual friend might send to one of the speakers, "
    "with ground-truth labels.\n\n"
    "Item types:\n"
    "- contradicting: sending the draft would contradict something the "
    "RECIPIENT speaker clearly said earlier (wrong assumption about their "
    "plans, stance, situation, or preferences). Cite the 1-3 turn ids that the "
    "draft contradicts. The draft must be fluent and PLAUSIBLE — the kind of "
    "message someone slightly out of the loop would genuinely send.\n"
    "- aligned: a natural draft fully consistent with the record. Cite 1-3 "
    "turn ids it is consistent with.\n"
    "- hard_negative: a draft that SOUNDS like it might contradict the record "
    "(mentions the changed topic, uses surprised/contrastive framing, or "
    "explicitly acknowledges that something changed) but is actually "
    "consistent. Cite the relevant turn ids. These must NOT contradict "
    "anything.\n\n"
    "Rules: address the speaker by name; one to three sentences per draft; "
    "vary topics across items; base every item on things ACTUALLY SAID in the "
    "transcript (never invent facts); evidence ids must be real turn ids from "
    "the transcript. Output ONLY a JSON array of objects: "
    '{"type": "...", "speaker": "<recipient name>", "draft": "...", '
    '"evidence": ["D3:7", ...], "rationale": "<one sentence>"}.'
)

_VERIFY_SYSTEM = (
    "You are grading a benchmark item. Given (a) what a person actually said "
    "in a conversation (their exact prior statements) and (b) a draft message "
    "someone proposes to send them, decide: would sending this draft "
    "CONTRADICT what the person said — i.e., does it assume or assert "
    "something incompatible with their own statements? A draft that "
    "acknowledges a change, asks a question about it, or is merely about the "
    "same topic is NOT a contradiction. Reply with exactly one word: "
    "CONTRADICTS or CONSISTENT."
)


def conv_transcript(conv: dict) -> tuple[str, list[str], dict[str, str]]:
    """Flatten a LoCoMo conversation to an id-tagged transcript."""
    lines: list[str] = []
    ids: list[str] = []
    text_by_id: dict[str, str] = {}
    session_keys = sorted(
        (k for k in conv if re.fullmatch(r"session_\d+", k)),
        key=lambda k: int(k.split("_")[1]),
    )
    for skey in session_keys:
        date = conv.get(f"{skey}_date_time", "")
        for turn in conv[skey] or []:
            did, text = turn.get("dia_id"), turn.get("text") or ""
            if not did or not text:
                continue
            line = f"{did} [{date}] {turn['speaker']}: {text}"
            lines.append(line)
            ids.append(did)
            text_by_id[did] = f"{turn['speaker']}: {text}"
    return "\n".join(lines), ids, text_by_id


def strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.lower().startswith("json"):
            s = s[4:]
    return s.strip()


def generate_for_conv(llm, sample: dict) -> list[dict]:
    conv_id = str(sample.get("sample_id"))
    transcript, valid_ids, text_by_id = conv_transcript(sample["conversation"])
    ask = ", ".join(f"{n} {t}" for t, n in PER_CONV.items())
    user = (
        f"TRANSCRIPT:\n{transcript}\n\n"
        f"Generate exactly {ask} items as specified. JSON:"
    )
    raw = llm.complete(_GEN_SYSTEM, user, max_tokens=4096)
    try:
        items = json.loads(strip_fence(raw))
    except json.JSONDecodeError:
        return []
    valid = set(valid_ids)
    out = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict) or it.get("type") not in PER_CONV:
            continue
        ev = [e for e in (it.get("evidence") or []) if e in valid]
        if not ev or not it.get("draft") or not it.get("speaker"):
            continue
        out.append({
            "id": f"{conv_id}:b{i}",
            "conv": conv_id,
            "speaker": it["speaker"],
            "type": it["type"],
            "draft": str(it["draft"]).strip(),
            "evidence": ev,
            "rationale": str(it.get("rationale", ""))[:200],
            "_evidence_text": [text_by_id[e] for e in ev],
        })
    return out


def verify(llm, item: dict) -> bool:
    """Independent check: does the gold label hold given only the evidence?"""
    statements = "\n".join(f"- {t}" for t in item["_evidence_text"])
    user = (
        f"What {item['speaker']} actually said:\n{statements}\n\n"
        f"Proposed draft to send to {item['speaker']}:\n\"{item['draft']}\"\n\n"
        "One word:"
    )
    try:
        verdict = llm.complete(_VERIFY_SYSTEM, user, max_tokens=8)
    except Exception:
        return False
    contradicts = "CONTRADICT" in verdict.upper()  # tolerate truncation
    return contradicts if item["type"] == "contradicting" else not contradicts


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate TWIST Track B items.")
    ap.add_argument("--data", required=True, help="path to locomo10.json")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--conversations", type=int, default=None)
    args = ap.parse_args()

    with open(args.data) as f:
        data = json.load(f)
    if args.conversations:
        data = data[: args.conversations]

    llm = build_llm_client()
    generated: list[dict] = []
    for n, sample in enumerate(data, 1):
        items = generate_for_conv(llm, sample)
        generated.extend(items)
        print(f"[{n}/{len(data)}] {sample.get('sample_id')}: generated {len(items)}")  # noqa: T201

    with ThreadPoolExecutor(max_workers=8) as pool:
        keep_flags = list(pool.map(lambda it: verify(llm, it), generated))
    kept = [it for it, ok in zip(generated, keep_flags, strict=True) if ok]
    dropped = len(generated) - len(kept)

    with open(args.out, "w") as f:
        for it in kept:
            it = {k: v for k, v in it.items() if not k.startswith("_")}
            f.write(json.dumps(it) + "\n")

    by_type = {t: sum(1 for i in kept if i["type"] == t) for t in PER_CONV}
    print(f"\nkept {len(kept)} items ({by_type}), dropped {dropped} "  # noqa: T201
          f"failing independent verification -> {args.out}")


if __name__ == "__main__":
    main()
