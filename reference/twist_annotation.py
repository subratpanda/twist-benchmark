"""TWIST Track B v1 human-annotation tooling (whitepaper §6.1).

build : produce two self-contained annotator HTML workbooks (A and B) plus a
        protocol README. Each workbook runs from a double-click — no server,
        no dependencies; progress lives in the browser's localStorage and is
        exported as JSON when done.

        Phase 1 (blind)  : Q1 verdict per item — the annotator sees ONLY the
                           recipient, the draft, and the dated evidence
                           excerpts. Item order is a per-annotator seeded
                           shuffle. Phase 2 stays locked until Phase 1 is
                           complete, so gold labels cannot bias verdicts.
        Phase 2 (gold)   : contradicting items -> Q2 self-containment check
                           (claim underlineable in the excerpts; quote right).
                           aligned/hard-negative items -> Q3 global check
                           against a searchable index of every turn the
                           recipient speaks in that conversation.

score : read two exported answer files, report Cohen's kappa on Q1 (target
        >= 0.8), and write the adjudication list — Q1 disagreements, Q2
        failures, Q3 conflicts — as JSONL for the third annotator.

council : LLM-council first cut — run N model annotators (default
        openai,anthropic,gemini) through the exact same Q1/Q2/Q3 protocol
        and item views as the human workbooks. Emits one answer file per
        model in the same export format (so `score` works on any pair) and
        `council_triage.jsonl` ranking items by council agreement vs gold.
        This is a PRE-SCREEN, not a substitute for §6.1 human annotation:
        gpt-4o generated and gate-verified the items, so its vote carries
        known circularity (disclosed; Claude/Gemini are the independent
        voices). Use the triage to focus human effort.

Usage:
    uv run python -m mindtwin.scripts.twist_annotation build \
        --data locomo10.json --items ../../benchmarks/twist/track_b_items_v1.jsonl \
        --outdir ../../benchmarks/twist/annotation
    uv run python -m mindtwin.scripts.twist_annotation score \
        --a twist_v1_answers_A.json --b twist_v1_answers_B.json \
        --items ../../benchmarks/twist/track_b_items_v1.jsonl \
        --out adjudication_queue.jsonl
"""
from __future__ import annotations

# ruff: noqa: E501, S311 — embedded HTML/JS template lines; seeded shuffle is
# for reproducible item order, not cryptography.
import argparse
import json
import random
from collections import Counter
from pathlib import Path

from mindtwin.scripts.twist_generate_b import conv_transcript

SEEDS = {"A": 41, "B": 97}

_README = """# TWIST Track B v1 — annotation pack

Protocol: whitepaper §6.1. Two annotators work independently (no discussion
until both export), a third adjudicates disagreements, items below agreement
are dropped, and the paper reports kappa + drop counts.

## Run
Open your file (`annotator_A.html` or `annotator_B.html`) in any browser.
Progress auto-saves locally; you can close and resume. When both phases are
done, click **Export answers** and send the JSON file back.

## Phase 1 — verdict (blind)
For each item: would SENDING the draft contradict what the recipient said in
the dated excerpts? Conventions:
- Asking whether something changed is NOT a contradiction.
- Acknowledging a change is NOT a contradiction.
- The record not mentioning an activity is NOT contradiction evidence.
- The excerpts are the latest known record — no imagined later changes.

## Phase 2 — gold checks (unlocks after Phase 1)
- Contradicting items (Q2): is the contradicted claim explicitly stated in
  the excerpts, in the recipient's own words — could you underline it? Is
  the quoted text the right words?
- Aligned / hard-negative items (Q3): use the recipient-turn index (filter
  box) to search everything they said in that conversation. Does ANY turn
  contradict the draft? If yes, record the turn id.

## Scoring
    uv run python -m mindtwin.scripts.twist_annotation score \\
        --a twist_v1_answers_A.json --b twist_v1_answers_B.json \\
        --items ../track_b_items_v1.jsonl --out adjudication_queue.jsonl
"""

_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<title>TWIST v1 annotation — __ANN__</title>
<style>
 body{font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;background:#f5f6f8;color:#1c2733}
 header{position:sticky;top:0;background:#1c2733;color:#fff;padding:10px 20px;display:flex;gap:18px;align-items:center;z-index:9}
 header b{font-size:17px} .tab{cursor:pointer;padding:4px 12px;border-radius:6px;background:#33475c}
 .tab.on{background:#4a90d9}.tab.lock{opacity:.45;cursor:not-allowed}
 #prog{margin-left:auto;font-variant-numeric:tabular-nums}
 main{max-width:880px;margin:18px auto;padding:0 16px}
 .card{background:#fff;border-radius:10px;padding:16px 18px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .card.done{border-left:4px solid #3aa15f}
 .draft{font-size:16px;background:#eef4fb;border-radius:8px;padding:10px 12px;margin:8px 0}
 .ev{color:#41525f;font-size:14px;background:#f2f2ee;border-radius:6px;padding:6px 10px;margin:4px 0}
 .gold{background:#fdf3e0;border-radius:6px;padding:6px 10px;margin:6px 0;font-size:14px}
 button.v{margin:4px 6px 0 0;padding:6px 14px;border:1px solid #b9c4cd;border-radius:7px;background:#fff;cursor:pointer}
 button.v.sel{background:#4a90d9;color:#fff;border-color:#4a90d9}
 input[type=text]{width:100%;box-sizing:border-box;margin-top:6px;padding:6px 8px;border:1px solid #ccd4da;border-radius:6px}
 .turns{max-height:300px;overflow:auto;border:1px solid #e2e6ea;border-radius:6px;margin-top:6px}
 .turns div{padding:3px 8px;font-size:13px;border-bottom:1px solid #f0f2f4}
 .meta{color:#7a8894;font-size:12.5px}
 #exp{background:#3aa15f;color:#fff;border:0;padding:6px 14px;border-radius:7px;cursor:pointer}
</style></head><body>
<header><b>TWIST v1 — annotator __ANN__</b>
 <span class="tab on" id="t1" onclick="show(1)">Phase 1 · verdicts</span>
 <span class="tab lock" id="t2" onclick="show(2)">Phase 2 · gold checks</span>
 <span id="prog"></span><button id="exp" onclick="exportAnswers()">Export answers</button>
</header>
<main id="m"></main>
<script>
const DATA=__DATA__;
const KEY='twist-v1-__ANN__';
let ans=JSON.parse(localStorage.getItem(KEY)||'{}');
let phase=1;
const save=()=>{localStorage.setItem(KEY,JSON.stringify(ans));refresh()};
const A=id=>ans[id]||(ans[id]={});
const p1done=()=>DATA.items.every(i=>A(i.id).q1);
const p2need=i=>i.gold.type==='contradicting'?['q2','q2quote']:['q3'];
const p2done=()=>DATA.items.every(i=>p2need(i).every(k=>A(i.id)[k]));
function refresh(){
 const d1=DATA.items.filter(i=>A(i.id).q1).length;
 const d2=DATA.items.filter(i=>p2need(i).every(k=>A(i.id)[k])).length;
 document.getElementById('prog').textContent=`P1 ${d1}/${DATA.items.length} · P2 ${d2}/${DATA.items.length}`;
 document.getElementById('t2').classList.toggle('lock',!p1done());
}
function show(p){if(p===2&&!p1done()){alert('Finish Phase 1 first — gold labels stay hidden until every verdict is in.');return}
 phase=p;document.getElementById('t1').classList.toggle('on',p===1);
 document.getElementById('t2').classList.toggle('on',p===2);render()}
function btn(id,field,val,label){const sel=A(id)[field]===val?' sel':'';
 return `<button class="v${sel}" onclick="pick('${id}','${field}','${val}')">${label}</button>`}
function pick(id,field,val){A(id)[field]=val;save();render()}
function note(id,field,el){A(id)[field]=el.value;localStorage.setItem(KEY,JSON.stringify(ans))}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function render(){
 const m=document.getElementById('m');let h='';
 if(phase===1){
  h+='<p class="meta">Would <b>sending the draft</b> contradict what this person said in the dated excerpts? Change-questions, acknowledged changes, and “never mentioned” are NOT contradictions. Excerpts are the latest known record.</p>';
  for(const it of DATA.items){const a=A(it.id);
   h+=`<div class="card${a.q1?' done':''}"><span class="meta">${it.id} · draft to <b>${it.recipient}</b></span>
   <div class="draft">${esc(it.draft)}</div>`;
   for(const e of it.evidence)h+=`<div class="ev">${e.id} — ${esc(e.text)}</div>`;
   h+=btn(it.id,'q1','contradicts','contradicts')+btn(it.id,'q1','consistent','consistent')+btn(it.id,'q1','cannot-tell','cannot tell');
   h+=`<input type="text" placeholder="optional note" value="${esc(a.q1note||'')}" onchange="note('${it.id}','q1note',this)"></div>`}
 }else{
  h+='<p class="meta">Gold labels now shown. Q2 (contradicting): is the contradicted claim explicitly stated in the excerpts, in the recipient’s own words? Q3 (aligned/hard-negative): search everything the recipient said — does ANY turn contradict the draft?</p>';
  for(const it of DATA.items){const a=A(it.id);const done=p2need(it).every(k=>a[k]);
   h+=`<div class="card${done?' done':''}"><span class="meta">${it.id} · to <b>${it.recipient}</b> · gold: <b>${it.gold.type}</b> (${it.gold.family})</span>
   <div class="draft">${esc(it.draft)}</div>`;
   for(const e of it.evidence)h+=`<div class="ev">${e.id} — ${esc(e.text)}</div>`;
   if(it.gold.type==='contradicting'){
    h+=`<div class="gold">gold quote: “${esc(it.gold.quote)}”</div>
    <div>Q2a claim explicitly stated in excerpts? ${btn(it.id,'q2','yes','yes')+btn(it.id,'q2','no','no')}</div>
    <div>Q2b quote is the right words? ${btn(it.id,'q2quote','yes','yes')+btn(it.id,'q2quote','no','no')}</div>`;
   }else{
    const turns=DATA.turns[it.conv+'|'+it.recipient]||[];
    h+=`<div>Q3 any turn by ${it.recipient} contradicting the draft? ${btn(it.id,'q3','clean','clean')+btn(it.id,'q3','conflict','conflict found')}</div>
    <input type="text" placeholder="if conflict: turn id (e.g. D7:5)" value="${esc(a.q3turn||'')}" onchange="note('${it.id}','q3turn',this)">
    <input type="text" placeholder="filter ${turns.length} turns…" oninput="filt('${it.id}',this.value)">
    <div class="turns" id="tl-${it.id}">${turns.map(t=>`<div>${t.id} ${esc(t.text)}</div>`).join('')}</div>`;
   }
   h+=`<input type="text" placeholder="optional note" value="${esc(a.p2note||'')}" onchange="note('${it.id}','p2note',this)"></div>`}
 }
 m.innerHTML=h;refresh();
}
function filt(id,q){const it=DATA.items.find(i=>i.id===id);
 const turns=(DATA.turns[it.conv+'|'+it.recipient]||[]).filter(t=>t.text.toLowerCase().includes(q.toLowerCase()));
 document.getElementById('tl-'+id).innerHTML=turns.map(t=>`<div>${t.id} ${esc(t.text)}</div>`).join('')}
function exportAnswers(){
 if(!p1done()||!p2done()){if(!confirm('Not all items are answered — export anyway?'))return}
 const blob=new Blob([JSON.stringify({annotator:'__ANN__',exported:new Date().toISOString(),answers:ans},null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);
 a.download='twist_v1_answers___ANN__.json';a.click()}
render();
</script></body></html>
"""


def _payload(data_path: str, items_path: str) -> tuple[list[dict], dict[str, list[dict]]]:
    """Item view + per-(conversation, recipient) turn index — the exact
    material both human workbooks and council annotators see."""
    with open(data_path) as f:
        data = {str(s["sample_id"]): s for s in json.load(f)}
    items = [json.loads(line) for line in open(items_path) if line.strip()]

    text_maps: dict[str, dict[str, str]] = {}
    speaker_maps: dict[str, dict[str, str]] = {}
    for conv_id in {i["conv"] for i in items}:
        _, _, text_maps[conv_id], speaker_maps[conv_id] = conv_transcript(
            data[conv_id]["conversation"])

    turns: dict[str, list[dict]] = {}
    for it in items:
        key = f"{it['conv']}|{it['speaker']}"
        if key in turns:
            continue
        tm, sm = text_maps[it["conv"]], speaker_maps[it["conv"]]
        turns[key] = [{"id": d, "text": tm[d]} for d in tm if sm[d] == it["speaker"]]

    payload_items = [{
        "id": it["id"],
        "conv": it["conv"],
        "recipient": it["speaker"],
        "draft": it["draft"],
        "evidence": [{"id": e, "text": text_maps[it["conv"]][e]}
                     for e in it["evidence"]],
        "gold": {"type": it["type"], "family": it["family"],
                 "quote": it.get("contradicted_quote", "")},
    } for it in items]
    return payload_items, turns


def build(args: argparse.Namespace) -> None:
    payload_items, turns = _payload(args.data, args.items)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    for ann, seed in SEEDS.items():
        shuffled = list(payload_items)
        random.Random(seed).shuffle(shuffled)
        blob = json.dumps({"items": shuffled, "turns": turns},
                          ensure_ascii=False).replace("</", "<\\/")
        html = _HTML.replace("__ANN__", ann).replace("__DATA__", blob)
        path = outdir / f"annotator_{ann}.html"
        path.write_text(html)
        print(f"wrote {path} ({len(shuffled)} items)")  # noqa: T201
    (outdir / "README.md").write_text(_README)
    print(f"wrote {outdir / 'README.md'}")  # noqa: T201


def _kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa for two equal-length label lists."""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1.0 else (po - pe) / (1 - pe)


def score(args: argparse.Namespace) -> None:
    with open(args.a) as f:
        ans_a = json.load(f)["answers"]
    with open(args.b) as f:
        ans_b = json.load(f)["answers"]
    items = [json.loads(line) for line in open(args.items) if line.strip()]

    missing = [i["id"] for i in items
               if not (ans_a.get(i["id"], {}).get("q1")
                       and ans_b.get(i["id"], {}).get("q1"))]
    if missing:
        print(f"WARNING: {len(missing)} items lack a Q1 answer from both "  # noqa: T201
              f"annotators (first few: {missing[:5]}); they are excluded "
              "from kappa and queued for adjudication.")

    scored = [i for i in items if i["id"] not in set(missing)]
    la = [ans_a[i["id"]]["q1"] for i in scored]
    lb = [ans_b[i["id"]]["q1"] for i in scored]
    kappa = _kappa(la, lb)

    gold_of = {"contradicting": "contradicts", "aligned": "consistent",
               "hard_negative": "consistent"}
    queue = []
    for it in scored:
        a, b = ans_a[it["id"]], ans_b[it["id"]]
        reasons = []
        if a["q1"] != b["q1"]:
            reasons.append("q1_disagreement")
        if a["q1"] == b["q1"] != gold_of[it["type"]]:
            reasons.append("q1_agreed_against_gold")
        if it["type"] == "contradicting":
            if "no" in (a.get("q2"), b.get("q2")):
                reasons.append("q2_not_self_contained")
            if "no" in (a.get("q2quote"), b.get("q2quote")):
                reasons.append("q2_quote_wrong")
        elif "conflict" in (a.get("q3"), b.get("q3")):
            reasons.append("q3_uncited_conflict")
        if reasons:
            queue.append(it | {
                "adjudication_reasons": reasons,
                "annotator_A": a, "annotator_B": b,
            })
    for mid in missing:
        it = next(i for i in items if i["id"] == mid)
        queue.append(it | {"adjudication_reasons": ["missing_answers"]})

    with open(args.out, "w") as f:
        for q in queue:
            f.write(json.dumps(q) + "\n")

    by_type: dict[str, tuple[int, int]] = {}
    for t in gold_of:
        rows = [i for i in scored if i["type"] == t]
        agree = sum(ans_a[i["id"]]["q1"] == ans_b[i["id"]]["q1"] for i in rows)
        by_type[t] = (agree, len(rows))
    print(f"\nQ1 Cohen's kappa: {kappa:.3f}  (target >= 0.8; n={len(scored)})")  # noqa: T201
    for t, (agree, n) in by_type.items():
        print(f"  {t:14s} raw agreement {agree}/{n}")  # noqa: T201
    print(f"adjudication queue: {len(queue)} items -> {args.out}")  # noqa: T201


# --------------------------------------------------------------- LLM council
_GOLD_OF = {"contradicting": "contradicts", "aligned": "consistent",
            "hard_negative": "consistent"}
_BAND_ORDER = {"majority_against": 0, "abstain_heavy": 1, "majority_match": 2,
               "unanimous_match": 3}


def _aggregate(payload_items: list[dict],
               files: dict[str, dict]) -> tuple[list[dict], Counter]:
    """Band every item by council votes vs gold. cannot-tell is an
    abstention, not an opposing vote — only active opposite votes put an
    item in majority_against."""
    names = list(files)
    opposite = {"contradicts": "consistent", "consistent": "contradicts"}
    rows, tally = [], Counter()
    for it in payload_items:
        gold = _GOLD_OF[it["gold"]["type"]]
        votes = {n: files[n][it["id"]]["q1"] for n in names}
        match = sum(v == gold for v in votes.values())
        opp = sum(v == opposite[gold] for v in votes.values())
        flags = [f"{n}:q2_no" for n in names
                 if files[n][it["id"]].get("q2") == "no"]
        flags += [f"{n}:q3_conflict({files[n][it['id']].get('q3turn', '')})"
                  for n in names if files[n][it["id"]].get("q3") == "conflict"]
        band = ("unanimous_match" if match == len(names) else
                "majority_against" if opp * 2 > len(names) else
                "majority_match" if match * 2 > len(names) else "abstain_heavy")
        tally[band] += 1
        rows.append({"id": it["id"], "type": it["gold"]["type"],
                     "family": it["gold"]["family"], "band": band,
                     "votes": votes, "flags": flags, "draft": it["draft"]})
    rows.sort(key=lambda r: _BAND_ORDER[r["band"]])
    return rows, tally


_Q1_SYSTEM = (
    "You are annotating an item for a benchmark of memory systems. You see "
    "the name of a message recipient, a proposed draft message to them, and "
    "dated excerpts of what the recipient said in a long conversation. "
    "Question: would SENDING the draft contradict what the recipient said in "
    "the excerpts — does it assume or assert something incompatible with "
    "their statements? Conventions: asking whether something changed is NOT "
    "a contradiction; a draft that acknowledges a change is NOT a "
    "contradiction; the excerpts not mentioning an activity is NOT "
    "contradiction evidence; the dated excerpts are the latest known record "
    "— do not imagine undocumented later changes. Output ONLY JSON: "
    '{"q1": "contradicts" | "consistent" | "cannot-tell"}.'
)

_Q2_SYSTEM = (
    "You are auditing the evidence of a benchmark item whose gold label says "
    "the draft contradicts the record. Judge two things strictly from the "
    "excerpt text: q2 — is the contradicted fact or stance EXPLICITLY stated "
    "in the excerpts, in the recipient's own words (could you underline it)? "
    "Answer no if it requires guessing whose plan something is, resolving a "
    "pronoun, or reading between the lines. q2quote — is the provided gold "
    "quote the right words for that claim? Output ONLY JSON: "
    '{"q2": "yes"|"no", "q2quote": "yes"|"no"}.'
)

_Q3_SYSTEM = (
    "You are auditing a benchmark item whose gold label says the draft is "
    "CONSISTENT with the record. Below is every turn the recipient spoke in "
    "the conversation. Does ANY turn contradict the draft — i.e. would "
    "sending the draft assert something incompatible with any statement? "
    "Asking about a change or acknowledging one is not contradiction. "
    "Output ONLY JSON: "
    '{"q3": "clean"|"conflict", "q3turn": "<turn id or empty>"}.'
)


def _ask_json(llm, system: str, user: str, max_tokens: int = 64) -> dict:
    from mindtwin.scripts.twist_generate_b import strip_fence
    try:
        return json.loads(strip_fence(llm.complete(system, user,
                                                   max_tokens=max_tokens)))
    except Exception:
        return {}


def _annotate_one(llm, it: dict, turns: dict[str, list[dict]]) -> dict:
    ev = "\n".join(f"- {e['id']} {e['text']}" for e in it["evidence"])
    q1_user = (f"Recipient: {it['recipient']}\n\nDraft message:\n"
               f"\"{it['draft']}\"\n\nDated excerpts of what "
               f"{it['recipient']} said:\n{ev}\n\nJSON:")
    ans: dict = {}
    q1 = _ask_json(llm, _Q1_SYSTEM, q1_user)
    ans["q1"] = q1.get("q1") if q1.get("q1") in (
        "contradicts", "consistent", "cannot-tell") else "cannot-tell"
    if it["gold"]["type"] == "contradicting":
        q2 = _ask_json(llm, _Q2_SYSTEM,
                       f"{q1_user[:-6]}\nGold quote: \"{it['gold']['quote']}\""
                       "\n\nJSON:")
        ans["q2"] = "yes" if q2.get("q2") == "yes" else "no"
        ans["q2quote"] = "yes" if q2.get("q2quote") == "yes" else "no"
    else:
        rec_turns = turns.get(f"{it['conv']}|{it['recipient']}", [])
        turn_text = "\n".join(f"- {t['id']} {t['text']}" for t in rec_turns)
        q3 = _ask_json(llm, _Q3_SYSTEM,
                       f"Recipient: {it['recipient']}\n\nDraft message:\n"
                       f"\"{it['draft']}\"\n\nEvery turn {it['recipient']} "
                       f"spoke:\n{turn_text}\n\nJSON:", max_tokens=96)
        ans["q3"] = "conflict" if q3.get("q3") == "conflict" else "clean"
        if ans["q3"] == "conflict":
            ans["q3turn"] = str(q3.get("q3turn") or "")
    return ans


def council(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from mindtwin.engine.llm import build_llm_client

    payload_items, turns = _payload(args.data, args.items)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    files = {}
    for spec in args.annotators.split(","):
        provider, _, model = spec.partition(":")
        llm = build_llm_client(provider, model=model or None)
        name = f"{provider}-{llm.model}"
        print(f"council annotator {name}: {len(payload_items)} items…")  # noqa: T201
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            answers = list(pool.map(
                lambda it, _llm=llm: _annotate_one(_llm, it, turns),
                payload_items))
        out = outdir / f"twist_v1_answers_{name}.json"
        with open(out, "w") as f:
            json.dump({"annotator": name,
                       "answers": {it["id"]: a for it, a in
                                   zip(payload_items, answers, strict=True)}},
                      f, indent=1)
        files[name] = {it["id"]: a for it, a in
                       zip(payload_items, answers, strict=True)}
        print(f"  -> {out}")  # noqa: T201

    # ---- aggregate: pairwise kappa, agreement vs gold, triage ----
    names = list(files)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            k = _kappa([files[a][it["id"]]["q1"] for it in payload_items],
                       [files[b][it["id"]]["q1"] for it in payload_items])
            print(f"kappa(q1) {a} vs {b}: {k:.3f}")  # noqa: T201

    rows, tally = _aggregate(payload_items, files)
    triage = [r for r in rows if r["band"] != "unanimous_match" or r["flags"]]
    tpath = outdir / "council_triage.jsonl"
    with open(tpath, "w") as f:
        for t in triage:
            f.write(json.dumps(t) + "\n")
    print(f"\ncouncil bands: {dict(tally)}")  # noqa: T201
    print(f"triage (non-unanimous or flagged): {len(triage)} items -> {tpath}")  # noqa: T201


_REPORT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>TWIST v1 — council report</title>
<style>
 body{font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;background:#f5f6f8;color:#1c2733}
 header{background:#1c2733;color:#fff;padding:14px 22px}
 header b{font-size:18px}.k{color:#9fb2c4;font-size:13px;margin-top:4px}
 main{max-width:920px;margin:16px auto;padding:0 16px}
 .chips{margin:10px 0}.chips span{display:inline-block;cursor:pointer;padding:4px 12px;margin:2px 6px 2px 0;border-radius:15px;background:#fff;border:1px solid #ccd4da;font-size:13.5px}
 .chips span.on{background:#1c2733;color:#fff;border-color:#1c2733}
 .card{background:#fff;border-radius:10px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .band{display:inline-block;padding:2px 10px;border-radius:11px;font-size:12.5px;color:#fff}
 .b0{background:#c0392b}.b1{background:#d68910}.b2{background:#2e86c1}.b3{background:#28884f}
 .draft{background:#eef4fb;border-radius:8px;padding:8px 12px;margin:8px 0;font-size:15.5px}
 .ev{color:#41525f;font-size:13.5px;background:#f2f2ee;border-radius:6px;padding:5px 10px;margin:4px 0}
 .quote{background:#fdf3e0;border-radius:6px;padding:5px 10px;margin:4px 0;font-size:13.5px}
 .vote{display:inline-block;padding:2px 9px;margin:2px 6px 2px 0;border-radius:10px;font-size:12.5px;color:#fff}
 .vg{background:#28884f}.vr{background:#c0392b}.va{background:#7a8894}
 .flag{color:#a04000;font-size:13px}.meta{color:#7a8894;font-size:12.5px}
</style></head><body>
<header><b>TWIST v1 — LLM-council report</b><div class="k" id="stats"></div></header>
<main><div class="chips" id="bandchips"></div><div class="chips" id="typechips"></div><div id="list"></div></main>
<script>
const DATA=__DATA__;
const BANDS=['majority_against','abstain_heavy','majority_match','unanimous_match'];
const BLAB={majority_against:'majority against gold',abstain_heavy:'abstain-heavy',majority_match:'majority match',unanimous_match:'unanimous match'};
const GOLD={contradicting:'contradicts',aligned:'consistent',hard_negative:'consistent'};
let fb=new Set(['majority_against','abstain_heavy']),ft=new Set(['contradicting','aligned','hard_negative']);
document.getElementById('stats').textContent=
 BANDS.map(b=>`${BLAB[b]}: ${DATA.rows.filter(r=>r.band===b).length}`).join(' · ')
 +'  |  '+DATA.kappas.map(k=>`κ ${k.pair}: ${k.value}`).join(' · ');
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function chips(el,vals,labels,set){document.getElementById(el).innerHTML=
 vals.map(v=>`<span class="${set.has(v)?'on':''}" onclick="tog('${el}','${v}')">${labels[v]||v}</span>`).join('')}
function tog(el,v){const s=el==='bandchips'?fb:ft;s.has(v)?s.delete(v):s.add(v);render()}
function voteCls(r,n){const v=r.votes[n],g=GOLD[r.type];
 return v===g?'vg':(v==='cannot-tell'?'va':'vr')}
function render(){
 chips('bandchips',BANDS,BLAB,fb);
 chips('typechips',['contradicting','aligned','hard_negative'],{},ft);
 const rows=DATA.rows.filter(r=>fb.has(r.band)&&ft.has(r.type));
 document.getElementById('list').innerHTML=rows.map(r=>{
  const ev=(DATA.evidence[r.id]||[]).map(e=>`<div class="ev">${e.id} — ${esc(e.text)}</div>`).join('');
  const q=DATA.quotes[r.id]?`<div class="quote">gold quote: “${esc(DATA.quotes[r.id])}”</div>`:'';
  const votes=Object.keys(r.votes).map(n=>`<span class="vote ${voteCls(r,n)}">${n.split('-')[0]}: ${r.votes[n]}</span>`).join('');
  const flags=r.flags.length?`<div class="flag">⚑ ${r.flags.map(esc).join(' · ')}</div>`:'';
  return `<div class="card"><span class="band b${BANDS.indexOf(r.band)}">${BLAB[r.band]}</span>
   <span class="meta"> ${r.id} · gold <b>${r.type}</b> (${esc(r.family)}) → expected “${GOLD[r.type]}”</span>
   <div class="draft">${esc(r.draft)}</div>${ev}${q}<div>${votes}</div>${flags}</div>`}).join('')
  ||'<p class="meta">nothing matches the current filters</p>';
}
render();
</script></body></html>
"""


def report(args: argparse.Namespace) -> None:
    payload_items, _turns = _payload(args.data, args.items)
    outdir = Path(args.outdir)
    files = {}
    for path in sorted(outdir.glob("twist_v1_answers_*.json")):
        with open(path) as f:
            blob = json.load(f)
        files[blob["annotator"]] = blob["answers"]
    if len(files) < 2:
        raise SystemExit(f"need >=2 council answer files in {outdir}")

    rows, _tally = _aggregate(payload_items, files)
    names = list(files)
    kappas = [{"pair": f"{a.split('-')[0]}↔{b.split('-')[0]}",
               "value": round(_kappa(
                   [files[a][it["id"]]["q1"] for it in payload_items],
                   [files[b][it["id"]]["q1"] for it in payload_items]), 3)}
              for i, a in enumerate(names) for b in names[i + 1:]]
    data = {
        "rows": rows,
        "kappas": kappas,
        "evidence": {it["id"]: it["evidence"] for it in payload_items},
        "quotes": {it["id"]: it["gold"]["quote"] for it in payload_items
                   if it["gold"]["quote"]},
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = outdir / "council_report.html"
    out.write_text(_REPORT_HTML.replace("__DATA__", blob))
    print(f"wrote {out} ({len(rows)} items, {len(names)} annotators)")  # noqa: T201


_ADJ_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>TWIST v1 — adjudication</title>
<style>
 body{font:15px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;background:#f5f6f8;color:#1c2733}
 header{position:sticky;top:0;background:#1c2733;color:#fff;padding:10px 20px;display:flex;gap:16px;align-items:center;z-index:9}
 #prog{margin-left:auto}#exp{background:#3aa15f;color:#fff;border:0;padding:6px 14px;border-radius:7px;cursor:pointer}
 main{max-width:920px;margin:16px auto;padding:0 16px}
 .card{background:#fff;border-radius:10px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}
 .card.done{border-left:4px solid #3aa15f}
 .sug{display:inline-block;padding:2px 10px;border-radius:11px;font-size:12.5px;color:#fff;background:#7a8894}
 .sug.drop{background:#c0392b}.sug.discuss{background:#d68910}
 .draft{background:#eef4fb;border-radius:8px;padding:8px 12px;margin:8px 0}
 .ev{color:#41525f;font-size:13.5px;background:#f2f2ee;border-radius:6px;padding:5px 10px;margin:4px 0}
 .quote{background:#fdf3e0;border-radius:6px;padding:5px 10px;margin:4px 0;font-size:13.5px}
 table.ann{border-collapse:collapse;margin:8px 0;font-size:13.5px}
 table.ann td,table.ann th{border:1px solid #e2e6ea;padding:3px 10px;text-align:left}
 .note{color:#41525f;font-style:italic;font-size:13px}
 button.v{margin:4px 6px 0 0;padding:6px 16px;border:1px solid #b9c4cd;border-radius:7px;background:#fff;cursor:pointer}
 button.v.keep.sel{background:#28884f;color:#fff;border-color:#28884f}
 button.v.drop.sel{background:#c0392b;color:#fff;border-color:#c0392b}
 input[type=text]{width:100%;box-sizing:border-box;margin-top:6px;padding:6px 8px;border:1px solid #ccd4da;border-radius:6px}
 .meta{color:#7a8894;font-size:12.5px}
</style></head><body>
<header><b>TWIST v1 — adjudication (__N__ items)</b><span id="prog"></span>
<button id="exp" onclick="exportDecisions()">Export decisions</button></header>
<main id="m"></main>
<script>
const DATA=__DATA__;
const KEY='twist-v1-adjudication';
let dec=JSON.parse(localStorage.getItem(KEY)||'{}');
const D=id=>dec[id]||(dec[id]={});
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function save(){localStorage.setItem(KEY,JSON.stringify(dec));
 const n=DATA.rows.filter(r=>D(r.id).action).length;
 document.getElementById('prog').textContent=`${n}/${DATA.rows.length} decided`}
function pick(id,a){D(id).action=a;save();render()}
function note(id,el){D(id).note=el.value;localStorage.setItem(KEY,JSON.stringify(dec))}
function row(l,o){return `<tr><th>${l}</th><td>${esc(o.q1||'')}</td><td>${esc(o.q2||o.q3||'')}</td><td>${esc(o.q2quote||o.q3turn||'')}</td><td class="note">${esc(o.q1note||'')} ${esc(o.p2note||'')}</td></tr>`}
function render(){
 document.getElementById('m').innerHTML=DATA.rows.map(r=>{
  const d=D(r.id);const cls=r.suggestion.startsWith('drop')?'drop':(r.suggestion==='discuss'?'discuss':'');
  return `<div class="card${d.action?' done':''}">
   <span class="sug ${cls}">${esc(r.suggestion)}</span>
   <span class="meta"> ${r.id} · gold <b>${r.type}</b> (${esc(r.family)}) · reasons: ${r.reasons.join(', ')}</span>
   <div class="draft">${esc(r.draft)}</div>
   ${r.evidence.map(e=>`<div class="ev">${e.id} — ${esc(e.text)}</div>`).join('')}
   ${r.quote?`<div class="quote">gold quote: “${esc(r.quote)}”</div>`:''}
   <table class="ann"><tr><th></th><th>q1</th><th>q2/q3</th><th>quote/turn</th><th>notes</th></tr>
   ${row('A',r.A)}${row('B',r.B)}
   <tr><th>council</th><td colspan="4">${Object.entries(r.council).map(([k,v])=>`${k.split('-')[0]}: ${v}`).join(' · ')||'unanimous match'}</td></tr></table>
   <div>${['keep','drop'].map(a=>`<button class="v ${a}${d.action===a?' sel':''}" onclick="pick('${r.id}','${a}')">${a}</button>`).join('')}</div>
   <input type="text" placeholder="adjudication note (why)" value="${esc(d.note||'')}" onchange="note('${r.id}',this)">
  </div>`}).join('');save()}
function exportDecisions(){
 const undecided=DATA.rows.filter(r=>!D(r.id).action).length;
 if(undecided&&!confirm(undecided+' items undecided — export anyway?'))return;
 const blob=new Blob([JSON.stringify({exported:new Date().toISOString(),decisions:dec},null,1)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);
 a.download='adjudication_decisions.json';a.click()}
render();
</script></body></html>
"""


def adjudicate(args: argparse.Namespace) -> None:
    """Build adjudication.html: queue items with A/B/council side by side."""
    payload_items, _ = _payload(args.data, args.items)
    by_id = {it["id"]: it for it in payload_items}
    with open(args.a) as f:
        ans_a = json.load(f)["answers"]
    with open(args.b) as f:
        ans_b = json.load(f)["answers"]
    queue = [json.loads(line) for line in open(args.queue) if line.strip()]
    council_dir = Path(args.council) if args.council else None
    council: dict[str, dict] = {}
    if council_dir:
        for path in sorted(council_dir.glob("twist_v1_answers_*.json")):
            with open(path) as f:
                blob = json.load(f)
            for iid, a in blob["answers"].items():
                council.setdefault(iid, {})[blob["annotator"]] = a["q1"]

    def suggestion(q: dict) -> str:
        a, b = ans_a[q["id"]], ans_b[q["id"]]
        if q["type"] == "contradicting" and a.get("q2") == b.get("q2") == "no":
            return "drop — both: not self-contained"
        if "q1_agreed_against_gold" in q["adjudication_reasons"]:
            return "drop — both judged against gold"
        if (q["type"] != "contradicting" and a.get("q3") == b.get("q3") == "conflict"
                and a.get("q3turn") and a.get("q3turn") == b.get("q3turn")):
            return f"drop — both found conflict at {a['q3turn']}"
        return "discuss"

    rows = [{
        "id": q["id"], "type": q["type"], "family": q["family"],
        "reasons": q["adjudication_reasons"],
        "draft": by_id[q["id"]]["draft"],
        "evidence": by_id[q["id"]]["evidence"],
        "quote": by_id[q["id"]]["gold"]["quote"],
        "A": ans_a[q["id"]], "B": ans_b[q["id"]],
        "council": council.get(q["id"], {}),
        "suggestion": suggestion(q),
    } for q in queue]
    order = {"drop": 0, "disc": 1}
    rows.sort(key=lambda r: order.get(r["suggestion"][:4], 2))
    blob = json.dumps({"rows": rows}, ensure_ascii=False).replace("</", "<\\/")
    out = Path(args.out)
    out.write_text(_ADJ_HTML.replace("__DATA__", blob)
                   .replace("__N__", str(len(rows))))
    sug = Counter(r["suggestion"].split(" — ")[0] for r in rows)
    print(f"wrote {out}: {len(rows)} items ({dict(sug)})")  # noqa: T201


def finalize(args: argparse.Namespace) -> None:
    """Apply adjudication decisions -> frozen v1.0 item file + stats."""
    items = [json.loads(line) for line in open(args.items) if line.strip()]
    with open(args.decisions) as f:
        decisions = json.load(f)["decisions"]
    with open(args.a) as f:
        ans_a = json.load(f)["answers"]
    with open(args.b) as f:
        ans_b = json.load(f)["answers"]
    queue_ids = {json.loads(line)["id"] for line in open(args.queue) if line.strip()}

    undecided = [i for i in queue_ids
                 if decisions.get(i, {}).get("action") not in ("keep", "drop")]
    if undecided:
        raise SystemExit(f"{len(undecided)} queued items lack a keep/drop "
                         f"decision (first few: {sorted(undecided)[:5]})")

    kept = [it for it in items
            if decisions.get(it["id"], {}).get("action") != "drop"]
    dropped = [it for it in items if it["id"] not in {k["id"] for k in kept}]
    with open(args.out, "w") as f:
        for it in kept:
            f.write(json.dumps(it) + "\n")

    gold_match = {"contradicting": "contradicts", "aligned": "consistent",
                  "hard_negative": "consistent"}
    stats = {
        "kappa_q1_all_items": round(_kappa(
            [ans_a[i["id"]]["q1"] for i in items],
            [ans_b[i["id"]]["q1"] for i in items]), 3),
        "kappa_q1_kept_items": round(_kappa(
            [ans_a[i["id"]]["q1"] for i in kept],
            [ans_b[i["id"]]["q1"] for i in kept]), 3),
        "n_generated": len(items), "n_kept": len(kept),
        "n_dropped": len(dropped),
        "dropped_ids": [d["id"] for d in dropped],
        "kept_by_type": dict(Counter(i["type"] for i in kept)),
        "dropped_by_type": dict(Counter(i["type"] for i in dropped)),
        "kept_gold_agreement_A": sum(
            ans_a[i["id"]]["q1"] == gold_match[i["type"]] for i in kept),
        "kept_gold_agreement_B": sum(
            ans_b[i["id"]]["q1"] == gold_match[i["type"]] for i in kept),
        "adjudication_notes": {i: d.get("note", "") for i, d in
                               decisions.items() if d.get("note")},
    }
    with open(args.stats, "w") as f:
        json.dump(stats, f, indent=1)
    print(f"v1.0 frozen: {len(kept)} items -> {args.out}")  # noqa: T201
    print(f"kappa all={stats['kappa_q1_all_items']} "  # noqa: T201
          f"kept={stats['kappa_q1_kept_items']}; "
          f"dropped {len(dropped)} {stats['dropped_by_type']}")
    print(f"stats -> {args.stats}")  # noqa: T201


def main() -> None:
    ap = argparse.ArgumentParser(description="TWIST v1 annotation tooling.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build annotator HTML workbooks")
    b.add_argument("--data", required=True)
    b.add_argument("--items", required=True)
    b.add_argument("--outdir", required=True)
    s = sub.add_parser("score", help="score two exported answer files")
    s.add_argument("--a", required=True)
    s.add_argument("--b", required=True)
    s.add_argument("--items", required=True)
    s.add_argument("--out", default="adjudication_queue.jsonl")
    c = sub.add_parser("council", help="LLM-council pre-screen (not §6.1)")
    c.add_argument("--data", required=True)
    c.add_argument("--items", required=True)
    c.add_argument("--outdir", required=True)
    c.add_argument("--annotators", default="openai,anthropic,gemini",
                   help="comma-separated provider[:model] specs")
    c.add_argument("--concurrency", type=int, default=6)
    r = sub.add_parser("report", help="render council_report.html from a council dir")
    r.add_argument("--data", required=True)
    r.add_argument("--items", required=True)
    r.add_argument("--outdir", required=True,
                   help="council dir holding twist_v1_answers_*.json")
    j = sub.add_parser("adjudicate",
                       help="build adjudication worksheet from the queue")
    j.add_argument("--data", required=True)
    j.add_argument("--items", required=True)
    j.add_argument("--a", required=True)
    j.add_argument("--b", required=True)
    j.add_argument("--queue", required=True)
    j.add_argument("--council", default=None, help="council answers dir")
    j.add_argument("--out", default="adjudication.html")
    f = sub.add_parser("finalize",
                       help="apply decisions -> frozen v1.0 items + stats")
    f.add_argument("--items", required=True)
    f.add_argument("--decisions", required=True)
    f.add_argument("--a", required=True)
    f.add_argument("--b", required=True)
    f.add_argument("--queue", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--stats", default="annotation_stats.json")
    args = ap.parse_args()
    {"build": build, "score": score, "council": council,
     "report": report, "adjudicate": adjudicate,
     "finalize": finalize}[args.cmd](args)


if __name__ == "__main__":
    main()
