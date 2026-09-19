# TWIST Track B v1 — annotation pack

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
    uv run python -m mindtwin.scripts.twist_annotation score \
        --a twist_v1_answers_A.json --b twist_v1_answers_B.json \
        --items ../track_b_items_v1.jsonl --out adjudication_queue.jsonl
