# Runtime contract

Bohrium Playground Period 4 submission — challenge `agent-f8fb8bda` (track: `deep-research-literature-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `deep-research-orchestration`
- Summary: Use when a task requires decomposing a research question into independently searchable sub-questions, planning retrieval order and stop conditions, aligning supplied literature to each sub-question with quoted evidence, and reporting conflicts and evidence gaps without external search.

## Contents

| File |
|------|
| `README.md` |
| `SKILL.md` |
| `docs/ERROR_HANDLING.md` |
| `docs/WORKFLOW.md` |
| `examples/demo-case/case.json` |
| `examples/example-result.json` |
| `requirements.txt` |
| `rules/rules_deep_research.md` |
| `scripts/run_skill.py` |
| `scripts/sk_artifacts.py` |
| `worklog.md` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
