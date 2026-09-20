"""Shortcut-leakage audit for TWIST Track B items (paper §5, item quality).

Question: can the gold label be predicted from the DRAFT TEXT ALONE,
without the conversation record? If yes, the benchmark contains
construction artifacts (the hypothesis-only / artifact test familiar from
NLI). A system under test only ever sees the draft plus whatever it
retrieves, so draft-only predictability is the leakage channel that
matters.

Method (no external dependencies):
  - Binary task: contradicting vs not (aligned + hard_negative).
  - Multinomial Naive Bayes over lowercase unigrams, Laplace smoothing.
  - Leave-one-conversation-out cross-validation (10 folds), so lexical
    quirks of a conversation cannot leak across the split.
  - Reported against the majority-class baseline; also reports the most
    class-discriminative tokens (log-odds) and surface stats (draft
    length by class, generation-batch composition) so reviewers can see
    WHAT any predictability consists of.

Usage:
    uv run python -m mindtwin.scripts.twist_shortcut_audit \
        --items ../../benchmarks/twist/track_b_items_v1.0.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict


def toks(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


def train_nb(rows: list[dict]) -> tuple[dict, dict, float]:
    counts = {True: Counter(), False: Counter()}
    n_docs = Counter()
    for r in rows:
        y = r["type"] == "contradicting"
        counts[y].update(toks(r["draft"]))
        n_docs[y] += 1
    vocab = set(counts[True]) | set(counts[False])
    tot = {y: sum(counts[y].values()) for y in (True, False)}
    loglik = {y: {w: math.log((counts[y][w] + 1) / (tot[y] + len(vocab)))
                  for w in vocab} for y in (True, False)}
    prior = math.log(n_docs[True] / len(rows)) - math.log(n_docs[False] / len(rows))
    return loglik, {"vocab": vocab}, prior


def predict(loglik: dict, vocab: set, prior: float, draft: str) -> bool:
    s = prior
    for w in toks(draft):
        if w in vocab:
            s += loglik[True][w] - loglik[False][w]
    return s > 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft-only shortcut audit.")
    ap.add_argument("--items", required=True)
    args = ap.parse_args()
    items = [json.loads(line) for line in open(args.items) if line.strip()]

    by_conv: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_conv[it["conv"]].append(it)

    n_pos = sum(it["type"] == "contradicting" for it in items)
    majority = max(n_pos, len(items) - n_pos) / len(items)
    correct = tp = fp = fn = tn = 0
    for held in sorted(by_conv):
        train = [it for c, rows in by_conv.items() if c != held for it in rows]
        loglik, meta, prior = train_nb(train)
        for it in by_conv[held]:
            pred = predict(loglik, meta["vocab"], prior, it["draft"])
            gold = it["type"] == "contradicting"
            correct += pred == gold
            tp += pred and gold
            fp += pred and not gold
            fn += (not pred) and gold
            tn += (not pred) and not gold
    acc = correct / len(items)
    pos_recall = tp / n_pos if n_pos else 0.0
    neg_recall = tn / (len(items) - n_pos)
    print(f"n={len(items)} ({n_pos} contradicting / {len(items)-n_pos} not)")  # noqa: T201
    print(f"majority baseline        : {majority:.3f}")  # noqa: T201
    print(f"draft-only NB (LOCO-CV)  : accuracy {acc:.3f}  "  # noqa: T201
          f"contradicting-recall {pos_recall:.3f}  other-recall {neg_recall:.3f}")
    print(f"balanced draft-only acc  : {(pos_recall+neg_recall)/2:.3f} "  # noqa: T201
          "(0.5 = no draft-only signal)")

    # class-discriminative tokens on the full set (descriptive, not CV)
    loglik, meta, _ = train_nb(items)
    diffs = {w: loglik[True][w] - loglik[False][w] for w in meta["vocab"]}
    top_pos = sorted(diffs, key=diffs.get, reverse=True)[:12]
    top_neg = sorted(diffs, key=diffs.get)[:12]
    print(f"\ntokens -> contradicting : {', '.join(top_pos)}")  # noqa: T201
    print(f"tokens -> not           : {', '.join(top_neg)}")  # noqa: T201

    # surface stats
    for label in ("contradicting", "aligned", "hard_negative"):
        lens = [len(toks(it["draft"])) for it in items if it["type"] == label]
        print(f"draft length {label:14s}: mean {sum(lens)/len(lens):.1f} tokens")  # noqa: T201
    batches = Counter((it["id"].split(":")[1][:3], it["type"]) for it in items)
    print("\ngeneration-batch composition (batch prefix x type):")  # noqa: T201
    for (b, t), n in sorted(batches.items()):
        print(f"  {b} {t:14s} {n}")  # noqa: T201
    print("note: systems never see item ids; batch composition matters only "  # noqa: T201
          "through stylistic correlates, which the draft-only test measures.")


if __name__ == "__main__":
    main()
