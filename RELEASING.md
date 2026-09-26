# Publishing checklist (Tier 1 distribution)

## 1. Hugging Face dataset  — ready to upload

Everything is prepared in [`huggingface/`](huggingface/) (dataset card
with YAML metadata + `test.jsonl`). To publish (once, ~2 min):

```bash
pip install -U huggingface_hub
hf auth login              # paste a WRITE token from hf.co/settings/tokens
hf repo create twist-track-b --repo-type dataset
hf upload subratpanda/twist-track-b ./huggingface . --repo-type dataset
```

Then verify https://huggingface.co/datasets/subratpanda/twist-track-b
renders, and add the link to README.md here and to the arXiv comments
field (metadata edit).

## 2. GitHub release + Zenodo DOI

Zenodo archives releases created AFTER the integration is enabled, so
order matters:

1. Log in at https://zenodo.org with GitHub → Account → GitHub →
   flip the toggle for `subratpanda/twist-benchmark` (30 seconds).
2. Then create the release (or ask Claude to):

```bash
gh release create v1.0.0 --title "TWIST-v1.0 — human-validated Track B key" \
  --notes-file RELEASE_NOTES_v1.0.md
```

3. Zenodo mints a DOI within minutes → add the DOI badge to README.md
   and a `doi:` line to CITATION.cff.

## 3. Papers with Code (~15 min, needs your login)

- paperswithcode.com → add paper → arXiv:2609.28575 (it may already be
  auto-indexed; then just claim/edit).
- Link official code: github.com/subratpanda/twist-benchmark
- Add dataset: "TWIST Track B" → link the HF dataset page.
- Add task if missing: "Contradiction Detection" / "Conversational
  Memory Evaluation".
- Optionally seed the leaderboard with the Table 3 ladder (13 configs;
  metrics CR / AS / HNS / GCR — mark all as "paper results").
