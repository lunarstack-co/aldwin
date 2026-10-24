# Aldwin tests

## Layout

| Path | Role |
| -- | -- |
| `conftest.py` | Shared fixtures: `aldwin_home`, `workspace`, `fake_model`, `host_client`, `approver`, canary helpers, real-`~/.aldwin` guard |
| `canary.py` | Canary constant and `scan_for_canary` for the security suite |
| `fakes/` | `FakeModel`, `FakeHost` |
| `tools/` | Unit tests for built-in tools |
| `cli/` | Rich render / parity snapshots |
| `integration/` | In-process host turns |
| `security/` | Guardrail suites (must pass to release; dedicated CI job) |
| `security/policy/` | Policy-engine families (fail closed, table, commands, denials, …) |
| `perf/` | `@pytest.mark.perf` budgets (Linux CI) |
| `e2e/` | pexpect against installed `aldwin` + OpenAI (`@pytest.mark.e2e`) |
| `live/` | Real API smoke (`@pytest.mark.live`) |
| `TRACEABILITY.md` | Requirement → test map for 0.0.2 |

Policy table and approval-log coverage live under `tests/security/policy/`. Thin stubs remain at `tests/test_policy_table.py` and `tests/test_approval_log.py` pointing there.

## Commands

```bash
uv run pytest                          # unit + security + render (no live/perf/e2e)
uv run pytest tests/security -q        # security + policy suites
uv run pytest -m perf -q               # performance budgets
uv run pytest -m e2e -q                # OpenAI demos (needs OPENAI_API_KEY)
```

## Isolation

Every test redirects `ALDWIN_HOME` and `HOME` under `tmp_path`. A session fixture fingerprints the real `~/.aldwin` and fails if it changes. Default tests block outbound TCP; mark `live` or `e2e` to allow network.

## Canary

Security tests plant `ALDWIN_CANARY_7f3a` in `secrets.yaml`, `.env`, and the environment. After the suite, `scan_for_canary` must find zero leaks in events, logs, tmp files, notes, and sessions.
