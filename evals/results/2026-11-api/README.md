# API runs, 2026-11-11

Raw outputs from the first runs through the Claude API backend of [`evals/run_model.py`](../../run_model.py), summarized in [docs/model-comparison.md](../../../docs/model-comparison.md#the-api-path).

- **Prompt:** the version with the confidence fix.
- **Cases:** all 11. Six from [`evals/cases/`](../../cases/), five from [`2026-11-heldout/cases/`](../2026-11-heldout/cases/).
- **Runs:** three repeats per model, at high effort. Each `run-N/` folder has one output per case and a `run_summary.json` with tokens and timing per call.

**One recorded figure is wrong.** These runs predate per-model pricing, so `cost_usd` in the Sonnet summaries uses Opus rates and is about double the real cost. The token counts are correct. The cost figures in the write-up were recalculated from them at each model's published rates.

These runs also predate per-call provenance, so the summaries do not record a request ID or fallback flag. No call was refused, so no fallback ran.
