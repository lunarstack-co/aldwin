# Contributing to Aldwin

Thanks for your interest in Aldwin. This guide covers how to set up the project, what the checks expect, and the few rules that every change has to follow.

If something here is unclear or out of date, that is a bug too. Open an issue or a pull request.

## Ways to contribute

- **Pick up an issue.** Issues labelled [`good first issue`](https://github.com/lunarstack-co/aldwin/labels/good%20first%20issue) are small and self-contained. Issues labelled [`help wanted`](https://github.com/lunarstack-co/aldwin/labels/help%20wanted) are larger but well defined. Leave a comment saying you'd like to take one, so two people don't do the same work.
- **Report a bug.** Use the bug report template and include your OS, Python version, `aldwin --version`, and the steps to reproduce it.
- **Suggest a feature.** Use the feature request template. For anything large, please open an issue to agree the approach before writing code.
- **Improve the docs.** Fixes to the README, the [protocol spec](docs/protocol.md) or this guide are always welcome.

Security problems should not go in a public issue. Email [contact@lunarstack.co](mailto:contact@lunarstack.co) instead.

## Set up

You need:

- macOS or Linux (Windows isn't supported yet)
- Python 3.11, 3.12 or 3.13
- [uv](https://docs.astral.sh/uv/)
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) on your `PATH`, which the `grep` tool and its tests use

Then:

```bash
git clone https://github.com/lunarstack-co/aldwin.git
cd aldwin
uv sync
uv run aldwin --version
```

`uv sync` installs the development dependencies, including the optional document libraries, so the whole test suite can run.

To try your changes by hand, run `uv run aldwin`. You need an OpenAI API key for a real conversation.

## Run the checks

CI runs these on macOS and Linux against every supported Python version. Run them locally before opening a pull request:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest --cov=aldwin --cov-fail-under=90
```

Use `uv run ruff format src tests` to fix formatting and `uv run ruff check --fix src tests` to fix import order.

Some useful test commands:

```bash
uv run pytest tests/test_web_fetch.py           # one file
uv run pytest -k approval                       # tests matching a name
uv run pytest tests/security -q                 # security and policy suites
uv run pytest -m perf -q                        # performance budgets
```

The default run needs no API keys and makes no network calls. Tests marked `live` or `e2e` call real APIs and are run by maintainers. You don't need to run them.

Every test runs against a temporary home folder, so your real `~/.aldwin` is never touched. A guard fails the run if it changes. Outbound network access is blocked in the default run.

See [tests/README.md](tests/README.md) for the test layout and shared fixtures (`aldwin_home`, `workspace`, `fake_model`, `host_client` and others).

## How the code is organised

Aldwin is split into packages that only talk to each other in one direction:

| Package | Job |
|---|---|
| `aldwin.core` | The assistant itself: the agent, models, tools, skills, memory, persona and the policy engine |
| `aldwin.protocol` | The typed messages that interfaces and the host exchange |
| `aldwin.host` | Runs turns, turns agent events into protocol events, handles approvals and sessions |
| `aldwin.client` | The Python API an interface uses to talk to the host |
| `aldwin.cli` | The terminal interface: commands, input and rendering |

The rules below are checked by [tests/test_architecture.py](tests/test_architecture.py), so a pull request that breaks them fails CI:

- **`cli` never imports `core`.** The terminal sends requests through `client` and draws protocol events. It never touches the agent, tools or skill files directly.
- **`client` and `protocol` never import `core` or Agentling.**
- **`core` never imports `protocol`, `host`, `client` or `cli`.**

If you're not sure where a change belongs, ask in the issue before you start.

## Rules for changes

### Safety lives in one place

Approvals, workspace boundaries, blocked paths and the handling of tool output all live in the policy engine (`core/policy.py`) and the host. Please don't add safety checks inside a single tool or in the CLI. In particular:

- Tools never ask the user for approval themselves and never assume a terminal exists. They implement `prepare` and `run`, and the policy wrapper does the rest.
- Check paths with `Workspace.resolve_safe`, never with string prefix checks such as `startswith`.
- Never pass `os.environ` to a subprocess. Use `clean_env()` from `core/policy.py`.
- Any error in a safety check must deny the action, never allow it.

Every new tool needs an entry in the policy `REGISTRY`, or Aldwin refuses to start. Changes to the policy engine get extra review, and the security suite in `tests/security/` must keep passing.

### No vendor lock-in

Aldwin has to stay usable without any one company's service. A new capability must either:

- work offline and for free using local libraries, or
- sit behind an interface in `core` with at least two backends, at least one of them free or self-hostable.

Open-source Python libraries that run locally (such as `pypdfium2` or `trafilatura`) are fine. Direct calls to a provider's API belong only in `core/models.py`. Actions that only work on one operating system should be skills, not built-in tools.

### Tests

Every change in behaviour comes with a test. Bug fixes should include a test that fails without the fix. Use the fake model and fake host in `tests/fakes/` rather than real API calls. Overall coverage must stay at or above 90%.

### Code style

- Ruff and mypy settings live in `pyproject.toml`. Code must pass both with no new ignores.
- Keep functions small and names clear. Match the style of the code around your change.
- Comments explain *why*, not *what*. Don't put issue numbers, names or notes to yourself in code comments, since they mean nothing to the next reader.
- Text the user sees (messages, errors, help) should be short, plain and say what to do next.

## Pull requests

1. Fork the repository and create a branch from `main`, for example `fix/max-steps-notice` or `docs/install-linux`.
2. Keep each pull request to one change. Small pull requests get reviewed faster.
3. Write commit messages and the pull request title in [Conventional Commits](https://www.conventionalcommits.org/) style, with the package as the scope:

   ```
   fix(cli): show the configured step limit in the max-steps notice
   feat(core): add a SearXNG search backend
   docs: explain how to run a single test
   ```

   Common types are `feat`, `fix`, `docs`, `test`, `refactor` and `chore`. Common scopes are `core`, `host`, `protocol`, `client`, `cli` and `release`.
4. In the pull request description, link the issue (`Closes #123`), say what changed and why, and say how you tested it. For CLI changes, a screenshot or a short terminal recording helps.
5. Make sure CI is green.

You don't need to edit `CHANGELOG.md` or bump the version. Maintainers do that when cutting a release.

A maintainer will try to respond within a few days. If a review asks for changes, push new commits to the same branch rather than opening a new pull request.

## Licence

Aldwin is licensed under [Apache-2.0](LICENSE). By opening a pull request you agree that your contribution is licensed under the same terms.
