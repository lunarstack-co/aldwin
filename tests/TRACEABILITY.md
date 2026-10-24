# Traceability

Maps product requirements for 0.0.2 tools, policy, security, and demos to tests.
Coverage floor: CI `--cov-fail-under=90` overall; aim ≥85% line on `core/`/`host/` and full branch coverage on `policy.py` / `workspace.py` (held by existing suites plus `tests/security/policy/`).

## Security, policy, e2e, perf (this change)

| Area | Requirement | Primary tests |
| -- | -- | -- |
| Tool units / tiers / summaries | Happy paths, refusals, caps, REGISTRY tiers, summaries | `tests/tools/*`, `test_registry_and_summaries.py`, `test_recall.py`, `test_remember.py` |
| Caps matrix | Just under / at / over per tool | `tests/tools/test_files.py`, `test_edits.py`, `test_download.py`, `test_todo.py`, `test_create_skill.py` |
| grep parity | rg vs Python identical | `tests/tools/test_files.py` |
| calculate safety | No imports/attrs; Hypothesis | `tests/tools/test_calculate.py` |
| Trimmed output | Oversize → `~/.aldwin/tmp` | `tests/tools/test_files.py`, security tmp readable |
| Memory FTS | Rank, scope, replace, forget | `tests/tools/test_memory_tools.py`, `test_memory_index.py` |
| Render snapshots | Rows, diff fold, todo, `@` | `tests/cli/*`, `tests/test_cli_render.py` |
| Path escapes | Outside / symlink / sibling / Hypothesis | `tests/security/test_path_escapes.py` |
| Blocked files | Secret paths refused or hidden | `tests/security/test_blocked_files.py` |
| Writes | Outside never grantable; stale/atomic/recheck | `tests/security/test_writes.py` |
| Prompt injection | Hardline + Diff ask + untrusted wrap | `tests/security/test_prompt_injection.py` |
| Download SSRF | Private/redirect refuse; 100 MB abort; no overwrite | `tests/security/test_downloads.py`, `tests/tools/test_download.py` |
| Skill lockdown | No `tools:` import; create_skill path | `tests/security/test_skills_lockdown.py` |
| Sequential approvals | One outstanding approval; batch deny | `tests/security/test_sequential_approvals.py` |
| Canary scan | No leak into logs/events/tmp/notes/sessions | `tests/security/test_canary_scan.py`, `tests/canary.py` |
| E2E harness | pexpect + skip without key | `tests/e2e/harness.py`, `test_harness_smoke.py`, `workspace_builder.py` |
| Six demos | Outcome asserts | `tests/e2e/test_demos.py` |
| Upgrade 0.0.1→0.0.2 | Notes/persona intact | `tests/e2e/test_upgrade.py` |
| Perf budgets | grep/doc/recall/catalogue/`@` | `tests/perf/test_budgets.py` |
| CI | 3.11-3.13 matrix, `security` job, nightly e2e | `.github/workflows/ci.yml`, `e2e.yml` |
| Fail closed | Exceptions deny; unknown tool | `tests/security/policy/test_fail_closed.py` |
| Policy table | Pin REGISTRY | `tests/security/policy/test_policy_table.py` |
| Commands | Hardlines, risky labels | `tests/security/policy/test_commands.py` |
| Clean env | Secrets scrubbed from `run_command` | `tests/security/policy/test_env_scrub.py` |
| Approval binding | Digest, grants, never grantable | `tests/security/policy/test_approval_binding.py` |
| Denials | Exact texts + 3-denial stop | `tests/security/policy/test_denials.py` |
| Approval log | Redaction, rotation, mode | `tests/security/policy/test_approval_log.py` |
| Display sanitise | ANSI/control/bidi stripped | `tests/security/policy/test_sanitise.py` |
| E2E refusals | No panel for blocked; canary absent | `tests/e2e/test_refusals.py` |
| Architecture / isolation | CLI never imports core; real home unchanged | `tests/test_architecture.py`, `tests/test_conftest_guards.py` |

## Prior ticket coverage (existing modules)

| Topic | Primary test modules |
| -- | -- |
| Policy / workspace / blocked paths | `tests/test_policy.py`, `tests/test_workspace.py`, `tests/test_run_command.py` |
| Read-only file tools | `tests/tools/test_files.py`, `tests/integration/test_file_tools.py` |
| File edits | `tests/tools/test_edits.py`, `tests/integration/test_edit_host.py` |
| Documents / downloads | `tests/tools/test_documents.py`, `tests/tools/test_download.py` |
| Memory | `tests/tools/test_memory_tools.py`, `tests/test_memory.py`, `tests/test_memory_index.py` |
| Planning | `tests/tools/test_todo.py`, `test_calculate.py`, `tests/cli/test_todo_view.py` |
| Skills / create_skill | `tests/test_skills.py`, `tests/tools/test_create_skill.py`, `tests/test_skill_lint.py` |
| Skill picker | `tests/test_skill_completer.py`, `tests/cli/test_parity_picker.py` |

## Manual follow-ups

| Item | Owner |
| -- | -- |
| Demos 1-2 on macOS with transcripts | Maintainer |
| Require the `security` GitHub check on `main` | Org admin |
| Laptop timing of default `uv run pytest` ≤90s | Maintainer |
| Two live E2E runs with `OPENAI_API_KEY`; attach `tests/e2e/out/*` | Maintainer |
