"""TWIST Track B item generator, v1 (draft-alignment; whitepaper §3B, §4).

For each LoCoMo conversation, generates a balanced set of *drafts* — messages
a friend might send to one of the two speakers — with machine-readable gold:

  contradicting  : sending it would contradict the speaker's own prior
                   statements (gold: aligned=false + evidence dia_ids)
  aligned        : consistent with the record (gold: aligned=true)
  hard_negative  : looks contradictory on the surface but is consistent —
                   e.g. it explicitly acknowledges the change (gold: aligned=true)

v1 enforces the cross-track item-quality rules (docs/TWIST_SCENARIOS.md)
that v0 violated:

  Rule 1 — self-contained evidence. Structural gate: every cited evidence
    turn must be spoken BY the draft's recipient (kills B4 role-flip items
    outright), and the verifier must confirm the excerpt text alone states
    the contradicted claim (`claim_stated`).
  Rule 2 — verification-as-seen. The verification call presents the evidence
    exactly as a system under test retrieves it — neutral
    "excerpts from the record" lines with speaker prefixes — never framed as
    "what X actually said".
  Rule 5 — difficulty stratification. Items carry `history_turns` so results
    can be stratified by conversation length.

Items also carry a scenario `family` label (B1-B3 / B-AL / HN-B1-B3; B4 is
banned by construction). v0 items (`track_b_items_v0.jsonl`) are a frozen
held set — regeneration writes a NEW file; never overwrite v0.

Usage:
    uv run python -m mindtwin.scripts.twist_generate_b \
        --data locomo10.json --out benchmarks/twist/track_b_items_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor

from mindtwin.engine.llm import build_llm_client

# Generation quotas per conversation. Sized above the v1 targets
# (100 contradicting / 60 aligned / >=60 hard-negative across 10
# conversations) to absorb verification-gate attrition (~23% in v0).
PER_CONV = {"contradicting": 13, "aligned": 8, "hard_negative": 8}

_FAMILIES = {
    "contradicting": "B1 invented-activity | B2 negated-commitment | B3 stale-assumption",
    "aligned": "B-AL consistent-message",
    "hard_negative": ("HN-B1 committed-despite-challenges | HN-B2 acknowledged-pause | "
                      "HN-B3 change-question"),
}

_GEN_SYSTEM = (
    "You create benchmark items that test whether an AI memory system can vet "
    "a PROPOSED outgoing message against a conversation record. You will read "
    "a long two-person conversation (each turn has an id like D1:3). Generate "
    "draft messages that a mutual friend might send to one of the speakers, "
    "with ground-truth labels.\n\n"
    "Item types (label each item with a `family` from the list for its type):\n"
    f"- contradicting ({_FAMILIES['contradicting']}): sending the draft would "
    "contradict something the RECIPIENT speaker clearly said earlier (wrong "
    "assumption about their plans, stance, situation, or preferences). Cite "
    "the 1-3 turn ids that the draft contradicts. The cited turns must state "
    "something POSITIVELY INCOMPATIBLE with the draft — a commitment the "
    "draft negates, a current state the draft gets wrong, a fact it "
    "misstates. Never build the contradiction on the record merely not "
    "mentioning an activity ('no mention' is not evidence — the person may "
    "simply not have brought it up). The draft must ASSERT or presuppose the "
    "incompatible thing as settled fact — congratulate on it, lock it in, "
    "sympathize with it, follow up on it. NEVER phrase it as a question "
    "about whether the person's stance changed: change-questions are "
    "hard_negative (HN-B3) by definition. ('So glad you're settled in the "
    "US for good!' asserts and can contradict; 'Is a move still on the "
    "table?' asks and cannot.) The draft must be fluent and PLAUSIBLE — the "
    "kind of message someone slightly out of the loop would genuinely "
    "send.\n"
    f"- aligned ({_FAMILIES['aligned']}): a natural draft fully consistent "
    "with the record. Cite 1-3 turn ids it is consistent with.\n"
    f"- hard_negative ({_FAMILIES['hard_negative']}): a draft that SOUNDS "
    "like it might contradict the record (mentions the changed topic, uses "
    "surprised/contrastive framing, or explicitly acknowledges that something "
    "changed) but is actually consistent. Cite the relevant turn ids. These "
    "must NOT contradict anything.\n\n"
    "SELF-CONTAINED EVIDENCE — hard requirement on every item:\n"
    "- Every cited turn must be spoken BY the recipient of the draft, never "
    "by the other speaker.\n"
    "- The cited turns' text alone, read with NO other context, must state "
    "the fact or stance the draft gets wrong (contradicting) or relies on "
    "(aligned / hard_negative). A grader who sees ONLY those lines must be "
    "able to point at the exact words. If the claim is only complete across "
    "two turns, cite both.\n"
    "- NEVER build an item on conversational inference: who is asking whom, "
    "pronouns resolved in uncited turns, or implications of a reply. If the "
    "contradiction depends on knowing which speaker a plan belongs to, the "
    "cited turns must say so in words.\n\n"
    "Rules: address the recipient by name; one to three sentences per draft; "
    "vary topics and families across items; base every item on things "
    "ACTUALLY SAID in the transcript (never invent facts); evidence ids must "
    "be real turn ids from the transcript. The recipient is ALWAYS the person "
    "whose own cited words the draft responds to — the same name that speaks "
    "every evidence turn. For contradicting items, additionally output "
    "contradicted_quote: the EXACT words, copied verbatim from one cited "
    "turn, that the draft is incompatible with (if you cannot copy such "
    "words, the item is invalid — do not produce it). Output ONLY a JSON "
    'array of objects: {"type": "...", "family": "...", "recipient": '
    '"<name>", "draft": "...", "evidence": ["D3:7", ...], '
    '"contradicted_quote": "<verbatim or omit>", '
    '"rationale": "<one sentence>"}.'
)

# Verification-as-seen (rule 2): the verifier gets the same view a vetting
# system gets after retrieval — bare record excerpts — with none of the
# generation context and no "what X actually said" framing.
_VERIFY_SYSTEM = (
    "You are validating an item for a benchmark of memory systems. You see "
    "ONLY (a) a few excerpts retrieved from a long conversation record, "
    "exactly as a vetting system would see them, and (b) a draft message "
    "someone proposes to send to a named person. Judge strictly from the "
    "excerpt text; assume no other knowledge of the conversation.\n\n"
    "Decide two things:\n"
    "1. contradicts — would sending the draft contradict the record, i.e. "
    "does it assume or assert something incompatible with what the excerpts "
    "state? The dated excerpts are the LATEST known record of this person — "
    "do not excuse an incompatibility by imagining undocumented later "
    "changes. Acknowledging a change, asking a question about a possible "
    "change, or merely discussing the same topic is NOT a contradiction.\n"
    "2. claim_stated — do the excerpts BY THEMSELVES explicitly state, in "
    "the named person's own words, the fact or stance at issue? Answer false "
    "if identifying it requires guessing who a plan belongs to, resolving a "
    "pronoun, or reading between the lines of a reply.\n\n"
    'Output ONLY JSON: {"contradicts": true|false, "claim_stated": true|false}.'
)


def conv_transcript(conv: dict) -> tuple[str, list[str], dict[str, str], dict[str, str]]:
    """Flatten a LoCoMo conversation to an id-tagged transcript.

    Returns (transcript, ids, text_by_id, speaker_by_id).
    """
    lines: list[str] = []
    ids: list[str] = []
    text_by_id: dict[str, str] = {}
    speaker_by_id: dict[str, str] = {}
    session_keys = sorted(
        (k for k in conv if re.fullmatch(r"session_\d+", k)),
        key=lambda k: int(k.split("_")[1]),
    )
    for skey in session_keys:
        date = conv.get(f"{skey}_date_time", "")
        # calendar part only, matching what ingestion stores ('[8 May, 2023]')
        day = date.split("on")[-1].strip() if "on" in date else date
        for turn in conv[skey] or []:
            did, text = turn.get("dia_id"), turn.get("text") or ""
            if not did or not text:
                continue
            line = f"{did} [{date}] {turn['speaker']}: {text}"
            lines.append(line)
            ids.append(did)
            text_by_id[did] = f"[{day}] {turn['speaker']}: {text}"
            speaker_by_id[did] = turn["speaker"]
    return "\n".join(lines), ids, text_by_id, speaker_by_id


def _norm(s: str) -> str:
    """Normalize for quote-membership checks: lowercase, collapse whitespace."""
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.lower().startswith("json"):
            s = s[4:]
    return s.strip()


def generate_for_conv(llm, sample: dict, quotas: dict[str, int],
                      id_prefix: str) -> tuple[list[dict], dict[str, int]]:
    """Generate raw items for one conversation. Returns (items, drop counts).

    Structural gates applied here (no LLM needed):
      wrong_speaker — a cited turn is not spoken by the draft's recipient
                      (rule 1's hard floor; kills role-flip items outright).
      bad_evidence  — cited id missing from the transcript, or no evidence.
      no_quote      — contradicting item whose contradicted_quote is not a
                      verbatim substring of any cited turn (absence-based or
                      paraphrased grounding; rule 1).
    """
    conv_id = str(sample.get("sample_id"))
    transcript, valid_ids, text_by_id, speaker_by_id = conv_transcript(
        sample["conversation"])
    history_turns = len(valid_ids)
    ask = ", ".join(f"{n} {t}" for t, n in quotas.items() if n)
    user = (
        f"TRANSCRIPT:\n{transcript}\n\n"
        f"Generate exactly {ask} items as specified. JSON:"
    )
    drops = {"parse": 0, "bad_evidence": 0, "wrong_speaker": 0, "no_quote": 0}
    items = None
    for _ in range(2):  # one retry on malformed JSON
        raw = llm.complete(_GEN_SYSTEM, user, max_tokens=8192)
        try:
            items = json.loads(strip_fence(raw))
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(items, list):
        drops["parse"] = 1
        return [], drops
    valid = set(valid_ids)
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict) or it.get("type") not in PER_CONV:
            continue
        ev = [e for e in (it.get("evidence") or []) if e in valid]
        draft = str(it.get("draft") or "").strip()
        named = str(it.get("recipient") or it.get("speaker") or "")
        if not ev or not draft or not named:
            drops["bad_evidence"] += 1
            continue
        # Rule 1's hard floor: all evidence turns share one speaker, and that
        # person is who the draft addresses. The recipient is DERIVED from the
        # evidence (generators reliably swap name fields; the cited turns
        # don't lie) and cross-checked against the draft text / named field.
        ev_speakers = {speaker_by_id[e] for e in ev}
        if len(ev_speakers) != 1:
            drops["wrong_speaker"] += 1
            continue
        recipient = next(iter(ev_speakers))
        if (recipient.lower() != named.lower()
                and recipient.lower() not in draft.lower()):
            drops["wrong_speaker"] += 1
            continue
        quote = str(it.get("contradicted_quote") or "").strip()
        if it["type"] == "contradicting":
            ev_norm = _norm(" | ".join(text_by_id[e] for e in ev))
            if len(_norm(quote)) < 10 or _norm(quote) not in ev_norm:
                drops["no_quote"] += 1
                continue
        out.append({
            "id": f"{conv_id}:{id_prefix}{i}",
            "conv": conv_id,
            "speaker": recipient,
            "type": it["type"],
            "family": str(it.get("family", ""))[:40],
            "draft": str(it["draft"]).strip(),
            "evidence": ev,
            "contradicted_quote": quote if it["type"] == "contradicting" else "",
            "rationale": str(it.get("rationale", ""))[:200],
            "history_turns": history_turns,
            "_evidence_text": [text_by_id[e] for e in ev],
        })
    return out, drops


def verify(llm, item: dict) -> bool:
    """Verification-as-seen (rule 2): grade from bare record excerpts only.

    Keep rule: contradicting items need contradicts AND claim_stated (the
    excerpts alone must carry the contradicted claim — rule 1); aligned and
    hard-negative items need NOT-contradicts.
    """
    excerpts = "\n".join(f"- {t}" for t in item["_evidence_text"])
    user = (
        f"Excerpts retrieved from the conversation record:\n{excerpts}\n\n"
        f"Draft message to send to {item['speaker']}:\n\"{item['draft']}\"\n\n"
        "JSON:"
    )
    try:
        parsed = json.loads(strip_fence(llm.complete(_VERIFY_SYSTEM, user,
                                                     max_tokens=64)))
        contradicts = bool(parsed.get("contradicts"))
        claim_stated = bool(parsed.get("claim_stated"))
    except Exception:
        item["_verdict"] = "error"
        return False
    item["_verdict"] = f"contradicts={contradicts} claim_stated={claim_stated}"
    if item["type"] == "contradicting":
        return contradicts and claim_stated
    return not contradicts


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate TWIST Track B items (v1 rules).")
    ap.add_argument("--data", required=True, help="path to locomo10.json")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--conversations", type=int, default=None)
    ap.add_argument("--provider", default=None,
                    help="LLM provider for generation+verification: "
                         "openai|anthropic|gemini (default: config)")
    ap.add_argument("--model", default=None,
                    help="model override (default: provider default)")
    ap.add_argument("--quota", action="append", default=None,
                    metavar="TYPE=N",
                    help="per-conversation quota override, repeatable "
                         "(e.g. --quota contradicting=15 --quota aligned=0); "
                         "types omitted from all --quota flags default to 0 "
                         "when any flag is given, else PER_CONV")
    ap.add_argument("--id-prefix", default="v1b",
                    help="item id prefix (use a distinct prefix per top-up "
                         "pass so ids never collide, e.g. v1c)")
    args = ap.parse_args()

    quotas = dict(PER_CONV)
    if args.quota:
        quotas = dict.fromkeys(PER_CONV, 0)
        for spec in args.quota:
            t, _, n = spec.partition("=")
            if t not in PER_CONV or not n.isdigit():
                ap.error(f"bad --quota {spec!r}")
            quotas[t] = int(n)

    with open(args.data) as f:
        data = json.load(f)
    if args.conversations:
        data = data[: args.conversations]

    llm = build_llm_client(args.provider, model=args.model)
    print(f"generator/verifier LLM: {llm.provider.value}/{llm.model}")  # noqa: T201
    generated: list[dict] = []
    gate_drops = {"parse": 0, "bad_evidence": 0, "wrong_speaker": 0, "no_quote": 0}
    for n, sample in enumerate(data, 1):
        items, drops = generate_for_conv(llm, sample, quotas, args.id_prefix)
        generated.extend(items)
        for k, v in drops.items():
            gate_drops[k] += v
        print(f"[{n}/{len(data)}] {sample.get('sample_id')}: generated "  # noqa: T201
              f"{len(items)} (structural drops {drops})")

    with ThreadPoolExecutor(max_workers=8) as pool:
        keep_flags = list(pool.map(lambda it: verify(llm, it), generated))
    kept = [it for it, ok in zip(generated, keep_flags, strict=True) if ok]
    dropped = len(generated) - len(kept)

    with open(args.out, "w") as f:
        for it in kept:
            it = {k: v for k, v in it.items() if not k.startswith("_")}
            f.write(json.dumps(it) + "\n")
    # Rejected items go to a sidecar for gate auditing / drop-rate reporting
    # (they are NOT benchmark items; the file exists to be read by humans).
    with open(f"{args.out}.dropped.jsonl", "w") as f:
        for it, ok in zip(generated, keep_flags, strict=True):
            if not ok:
                f.write(json.dumps(it | {"_dropped_by": "verify"}) + "\n")

    by_type = {t: sum(1 for i in kept if i["type"] == t) for t in quotas}
    print(f"\nstructural gate drops: {gate_drops}")  # noqa: T201
    print(f"kept {len(kept)} items ({by_type}), dropped {dropped} "  # noqa: T201
          f"failing verification-as-seen -> {args.out}")


if __name__ == "__main__":
    main()
