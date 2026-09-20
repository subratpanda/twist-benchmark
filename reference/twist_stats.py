"""Statistical analysis for TWIST Track B results (paper §6.3).

Computes, per system and per metric class:
  - Wilson 95% score intervals on each proportion (n per class is small —
    38 contradicting on v1.0 — so point estimates alone overstate certainty);
  - conversation-clustered bootstrap 95% CIs (items are nested in 10 LoCoMo
    conversations sharing personas and history; resampling conversations,
    not items, respects that dependence);
  - exact McNemar tests for pairwise system comparisons on the same items
    (contradiction recall and hard-negative specificity), so "A beats B"
    claims carry a paired p-value rather than eyeballed deltas.

No external dependencies. Usage:
    uv run python -m mindtwin.scripts.twist_stats \
        --results ../../benchmarks/twist/track_b_results_v1.0.json \
        --items ../../benchmarks/twist/track_b_items_v1.0.jsonl \
        --out ../../benchmarks/twist/track_b_stats_v1.0.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict

Z = 1.959964  # 95%
CLASSES = {"contradicting": "contradiction_recall",
           "aligned": "aligned_specificity",
           "hard_negative": "hard_negative_specificity"}


def wilson(k: int, n: int) -> tuple[float, float, float]:
    """(point, lo, hi) Wilson 95% interval."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + Z * Z / n
    center = (p + Z * Z / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def cluster_bootstrap(rows: list[dict], stat, n_boot: int = 10000,
                      seed: int = 7) -> tuple[float, float]:
    """95% percentile CI for `stat(rows)` resampling conversations."""
    by_conv: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_conv[r["conv"]].append(r)
    convs = sorted(by_conv)
    rng = random.Random(seed)  # noqa: S311 — reproducible bootstrap, not crypto
    vals = []
    for _ in range(n_boot):
        sample: list[dict] = []
        for _ in convs:
            sample.extend(by_conv[rng.choice(convs)])
        v = stat(sample)
        if v is not None:
            vals.append(v)
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main() -> None:
    ap = argparse.ArgumentParser(description="TWIST Track B statistics.")
    ap.add_argument("--results", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-boot", type=int, default=10000)
    args = ap.parse_args()

    with open(args.results) as f:
        blob = json.load(f)
    results = blob["results"]

    report: dict[str, dict] = {"per_system": {}, "pairwise_mcnemar": {}}
    print(f"{'system':22s} {'metric':28s} {'point':>6s}  Wilson 95%      "  # noqa: T201
          "cluster-bootstrap 95%")
    for system, rows in results.items():
        report["per_system"][system] = {}
        for cls, name in CLASSES.items():
            sub = [r for r in rows if r["type"] == cls]
            k = sum(r["verdict_correct"] for r in sub)
            p, lo, hi = wilson(k, len(sub))
            blo, bhi = cluster_bootstrap(
                sub, lambda rs: (sum(r["verdict_correct"] for r in rs) / len(rs))
                if rs else None, args.n_boot)
            report["per_system"][system][name] = {
                "point": round(p, 3), "n": len(sub), "k": k,
                "wilson95": [round(lo, 3), round(hi, 3)],
                "cluster_bootstrap95": [round(blo, 3), round(bhi, 3)],
            }
            print(f"{system:22s} {name:28s} {p:6.3f}  "  # noqa: T201
                  f"[{lo:.3f}, {hi:.3f}]  [{blo:.3f}, {bhi:.3f}]")
        # attribution over correctly flagged contradicting items
        flagged = [r for r in rows if r["type"] == "contradicting"
                   and r["verdict_correct"]]
        k = sum(bool(r["attributed"]) for r in flagged)
        p, lo, hi = wilson(k, len(flagged))
        report["per_system"][system]["attribution_accuracy"] = {
            "point": round(p, 3), "n": len(flagged), "k": k,
            "wilson95": [round(lo, 3), round(hi, 3)],
        }
        print(f"{system:22s} {'attribution_accuracy':28s} {p:6.3f}  "  # noqa: T201
              f"[{lo:.3f}, {hi:.3f}]  (n={len(flagged)} flagged)")

    systems = list(results)
    print("\npairwise exact McNemar (b = row-correct/col-wrong, c = reverse):")  # noqa: T201
    for cls in ("contradicting", "hard_negative"):
        for i, s1 in enumerate(systems):
            for s2 in systems[i + 1:]:
                r1 = {r["item_id"]: r["verdict_correct"]
                      for r in results[s1] if r["type"] == cls}
                r2 = {r["item_id"]: r["verdict_correct"]
                      for r in results[s2] if r["type"] == cls}
                common = sorted(set(r1) & set(r2))
                b = sum(r1[i2] and not r2[i2] for i2 in common)
                c = sum(r2[i2] and not r1[i2] for i2 in common)
                pv = mcnemar_exact(b, c)
                key = f"{cls}:{s1} vs {s2}"
                report["pairwise_mcnemar"][key] = {
                    "b": b, "c": c, "p": round(pv, 4)}
                if pv < 0.1:
                    print(f"  {key:70s} b={b:2d} c={c:2d} p={pv:.4f}")  # noqa: T201

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1)
        print(f"\nstats -> {args.out}")  # noqa: T201


if __name__ == "__main__":
    main()
