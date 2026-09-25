"""Standalone TWIST Track B scorer — no dependencies beyond Python 3.9+.

Score YOUR system against the frozen key without any of this repository's
other code. Protocol:

  1. Ingest the 10 LoCoMo conversations into your system yourself
     (data/locomo10.json from github.com/snap-research/locomo — not
     redistributed here).
  2. For each item in data/track_b_items_v1.0.jsonl, call your system's
     draft-vetting surface with ONLY the item's "draft" text (never the
     gold evidence), and record its verdict and cited turn ids.
  3. Write one JSON object per line:
       {"item_id": "conv-26:v1b1", "aligned": false, "evidence": ["D19:1"]}
     ("aligned": would you send it? false = flagged. "evidence": cited
      turn ids, best-first; only the first 3 are scored.)
  4. Run:
       python3 score_track_b.py --items ../data/track_b_items_v1.0.jsonl \
           --predictions my_system.jsonl --out my_system_results.json

Reports contradiction recall, aligned specificity, hard-negative
specificity, grounded contradiction recall, conditional attribution
(top-3 budget), each with a Wilson 95% interval — the exact metrics of
the paper's Table 3. `--out` writes per-item results in this repo's
results-file schema, so `twist_stats.py` (also standalone) can compute
clustered bootstraps and McNemar tests against the published systems.
Paired metrics: never cite a detection number without its specificity
pair. Scores must cite the key version (TWIST-v1.0).
"""
from __future__ import annotations

import argparse
import json
import math

Z = 1.959964


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def main() -> None:
    ap = argparse.ArgumentParser(description="Score a system on TWIST Track B v1.0.")
    ap.add_argument("--items", required=True, help="track_b_items_v1.0.jsonl")
    ap.add_argument("--predictions", required=True,
                    help="your per-item verdicts (JSONL; see module docstring)")
    ap.add_argument("--system-name", default="external")
    ap.add_argument("--out", default=None,
                    help="write per-item results (repo schema) here")
    args = ap.parse_args()

    items = {}
    with open(args.items) as f:
        for line in f:
            if line.strip():
                it = json.loads(line)
                items[it["id"]] = it
    preds = {}
    with open(args.predictions) as f:
        for line in f:
            if line.strip():
                p = json.loads(line)
                preds[p["item_id"]] = p

    missing = sorted(set(items) - set(preds))
    if missing:
        raise SystemExit(f"{len(missing)} items lack predictions "
                         f"(first: {missing[:3]}). Every item must be "
                         "answered; abstention is not a Track B option.")

    rows = []
    for iid, it in items.items():
        p = preds[iid]
        aligned = bool(p["aligned"])
        gold_aligned = it["type"] != "contradicting"
        correct = aligned == gold_aligned
        attributed = None
        if it["type"] == "contradicting" and correct:
            cited = [e for e in (p.get("evidence") or []) if isinstance(e, str)]
            attributed = bool(set(cited[:3]) & set(it["evidence"]))
        rows.append({"item_id": iid, "conv": it["conv"], "type": it["type"],
                     "gold_aligned": gold_aligned, "pred_aligned": aligned,
                     "verdict_correct": correct, "attributed": attributed,
                     "cited_fact_only": False})

    def report(label: str, k: int, n: int) -> None:
        p, lo, hi = wilson(k, n)
        print(f"  {label:32s} {k:3d}/{n:<3d} = {p:.3f}  Wilson95 [{lo:.3f}, {hi:.3f}]")  # noqa: T201

    by = lambda t: [r for r in rows if r["type"] == t]  # noqa: E731
    contra, al, hn = by("contradicting"), by("aligned"), by("hard_negative")
    flagged = [r for r in contra if r["verdict_correct"]]
    print(f"TWIST-v1.0 Track B — {args.system_name} (n={len(rows)})")  # noqa: T201
    report("contradiction recall (CR)",
           sum(r["verdict_correct"] for r in contra), len(contra))
    report("aligned specificity (AS)",
           sum(r["verdict_correct"] for r in al), len(al))
    report("hard-negative specificity (HNS)",
           sum(r["verdict_correct"] for r in hn), len(hn))
    report("grounded contra recall (GCR)",
           sum(bool(r["attributed"]) for r in contra), len(contra))
    report("attribution (top-3, conditional)",
           sum(bool(r["attributed"]) for r in flagged), len(flagged))
    print("Paired-metric rule: CR is not citable without HNS (and AS).")  # noqa: T201

    if args.out:
        blob = {"scores": {}, "results": {args.system_name: rows}}
        with open(args.out, "w") as f:
            json.dump(blob, f, indent=1)
        print(f"per-item results -> {args.out} "  # noqa: T201
              "(compatible with twist_stats.py)")


if __name__ == "__main__":
    main()
