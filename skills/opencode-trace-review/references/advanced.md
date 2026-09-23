# Optional workflows

## Task and verification materials

`import FILE --task TASK.json --diff FINAL.patch` accepts a user/runner-provided task manifest and final diff. A portable run bundle can embed `task`, `diff`, and `verifications` alongside `export` and `bundle_version: "1"`. Query the actual Pydantic contract with `run_review.py schema`; do not infer fields from a free-form transcript.

An external verification requires `id`, `provenance: "external_verifier"`, and a `result`. For acceptance matching it also needs `check_id`, `kind`, `phase: "final"`, `state_hash`, and `suite_hash` that match the manifest's required check, `final_state_hash`, and `suite_hash`. `cases`, when supplied, must describe the complete case set. Baseline reports use `phase: "baseline"` and the manifest's `initial_state_hash` (or `base_commit` when omitted). A newly skipped or missing baseline case prevents an unqualified pass.

These identifiers are supplied by the experiment runner; the importer does not compute or authenticate the actual repository state. Only add values supported by the user's materials. Without them, complete the trajectory analysis and identify the missing acceptance evidence.

For a separate JUnit XML, the existing CLI has no `--junit` flag. Use the local import UI with native JSON, Task Manifest, final diff and XML. The JUnit upload requires one test check and final-state/suite identifiers; multi-check experiments use a structured bundle. Do not silently replace XML with guessed pass/fail counts.

## Local viewer

When an interactive review or JUnit import is useful:

```sh
python3 "$REVIEW_SKILL/scripts/run_review.py" serve --data-dir "/absolute/review-data" --port 8765
```

Wait for startup success and open `http://127.0.0.1:8765`. If that port is in use, use another available port. Use the available app/browser opening tool when appropriate; do not assume the server is running from an earlier conversation. The server binds to localhost. It is not an authenticated public hosting service. The bundled wheel includes the production UI; Node.js is not needed.

## Optional Judge

The Scout Judge rubric applies to OpenCode coding traces. For generic tasks use the API + Token reviewer in [llm-review.md](llm-review.md), or implement an external evaluator using [custom-tasks.md](custom-tasks.md). Profile outcomes are preserved when adding a coding Judge review.

Only run when the user requests model-based review and permits sending visible excerpts to the selected provider. Existing explicit authorization can be reused. Do not turn ordinary “analyze this trace” into consent for an external model call.

The user configures `AGENT_REVIEW_JUDGE_MODEL=provider/model`, the provider's credential environment variables, and optionally `AGENT_REVIEW_JUDGE_BASE_URL`. Use environment variables without displaying credential values. Run:

```sh
python3 "$REVIEW_SKILL/scripts/run_review.py" judge RUN_ID --data-dir "/absolute/review-data"
```

The launcher includes pinned Scout dependencies for this command. For Judge in the UI use `run_review.py --with-scout serve ...` after configuration.

The Judge receives bounded task/diff/event excerpts, no hidden reasoning or execution tools. Limits: about 40,000 input characters, 1,600 output tokens, 90 seconds, no automatic retry. Character limits are not a precise monetary budget. Unknown/invalid references are rejected; the original evaluation remains. Review the stored input window and cite the model-generated findings as hypotheses. Judge use does not override deterministic acceptance. Identical material/model/rubric settings reuse the saved revision.

## Portable output

`report RUN_ID --format bundle --output FILE` exports re-importable, redacted source and acceptance materials. `--format json` preserves the selected latest evaluation and its evidence index; Markdown is a human-readable summary. Bundles do not preserve Judge revision history. Before sharing outside the user's chosen destination, follow their sharing scope; common-secret redaction is not complete business-data anonymization.

The embedded runtime version and SHA-256 are in `assets/runtime.json`. Rebuilding/upgrading the engine is a repository maintenance task, not part of evaluating a run; don't repair a damaged wheel by bypassing the launcher integrity check.
