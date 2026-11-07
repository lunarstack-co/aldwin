# Set up SearXNG for Aldwin

Aldwin can search the web through a self-hosted [SearXNG](https://docs.searxng.org/) instance, with no paid search API key. This guide covers what SearXNG is, what you need, how to run it, and how to point Aldwin at it.

There is no SearXNG prompt in `aldwin setup` yet. If SearXNG is already running locally with JSON on port **8080**, Aldwin auto-detects it. You can still set the URL in `config.yaml` or the environment for a non-default host or port.

## What SearXNG is

SearXNG is a free, open-source metasearch engine. You run it on your own machine (or a server you control). Aldwin sends the search query to **your** instance; the instance queries upstream engines and returns results as JSON.

Prefer an instance you run yourself. Public SearXNG sites often disable JSON, so they will not work with Aldwin.

## What you need

- **Docker** with Compose (Docker Desktop on macOS/Windows, or Docker Engine + Compose on Linux)
- A free port on your machine (this guide uses **8080**)
- Aldwin installed (`uv tool install aldwin` or `uv run aldwin` from a checkout)

Aldwin does **not** install Docker or SearXNG for you.

## 1. Install Docker (once)

| Device | Install |
|---|---|
| **macOS** | Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), open it, and wait until it says Engine is running. |
| **Linux** | Install [Docker Engine](https://docs.docker.com/engine/install/) and the [Compose plugin](https://docs.docker.com/compose/install/). |
| **Windows** | Aldwin itself is not supported on Windows yet. If you use WSL2, install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) with the WSL2 backend, then run the Linux commands inside WSL. |

Check:

```bash
docker --version
docker compose version
```

If `docker` is not found after installing Docker Desktop on macOS, open a new terminal. Desktop may put the CLI in `~/.docker/bin`; add that to your `PATH` if needed:

```bash
echo 'export PATH="$HOME/.docker/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## 2. Start SearXNG (all devices)

These commands work the same on macOS, Linux, and WSL once Docker works.

```bash
mkdir -p ~/searxng/core-config && cd ~/searxng

curl -fsSL \
  -O https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml \
  -O https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example

cp .env.example .env

cat > core-config/settings.yml <<'EOF'
use_default_settings: true

search:
  formats:
    - html
    - json

server:
  limiter: false
  secret_key: "replace-me-with-a-long-random-string"
EOF

docker compose up -d
```

Replace `secret_key` with any long random string. SearXNG refuses to start with the default `ultrasecretkey`.

**JSON is required.** Without `json` under `search.formats`, Aldwin gets HTTP 403 and a message about enabling JSON.

Check that JSON works:

```bash
curl -s 'http://127.0.0.1:8080/search?q=test&format=json' | head -c 200
```

You should see JSON with a `"results"` array, not an empty response or 403. The UI is also at http://127.0.0.1:8080 .

Useful commands:

```bash
cd ~/searxng
docker compose ps          # status
docker compose logs core   # logs if it will not start
docker compose down        # stop
```

## 3. Point Aldwin at SearXNG

If you used the default port above, skip this: run `aldwin`, then `/search`. SearXNG should appear as ready. Use `/search searxng` to prefer it over a Tavily key.

### Option A: config file (non-default host/port, or to pin the URL)

Add this block to `~/.aldwin/config.yaml`:

```yaml
search:
  provider: auto
  searxng_url: http://127.0.0.1:8080
```

Use `provider: searxng` if you also have a Tavily (or other hosted) key and want to force SearXNG. In chat, `/search searxng` does the same and saves the choice; `/search auto` restores auto-selection.

One-liner append (only if you do not already have a `search:` section):

```bash
cat >> ~/.aldwin/config.yaml <<'EOF'

search:
  provider: auto
  searxng_url: http://127.0.0.1:8080
EOF
```

### Option B: environment variable (one terminal session)

```bash
export SEARXNG_URL=http://127.0.0.1:8080
```

Aldwin resolves the URL in this order: `search.searxng_url` in config, then `SEARXNG_URL`, then `SEARXNG_BASE_URL`.

## 4. Try it in the CLI

```bash
aldwin
```

From a source checkout:

```bash
uv run aldwin
```

Ask something that needs the live web, for example:

> Use web_search to look up today's Python release news.

You should see a `web_search` tool result with numbered titles, URLs, and snippets. If search is not configured, Aldwin tells you to set `search.searxng_url` / `SEARXNG_URL` or add a hosted key via `aldwin setup`.

## Alternatives

- **Hosted key (for example Tavily):** run `aldwin setup` and paste the key when prompted. No SearXNG required.
- **Both configured:** with `search.provider: auto`, Aldwin prefers a hosted Tavily key over SearXNG when both are present.

## Privacy notes

- Queries go to the SearXNG URL you configure (your machine if you follow this guide).
- A localhost or private SearXNG URL is allowed on purpose; it is not blocked like `web_fetch` private addresses.
- Details: [Data and privacy](privacy.md).

## Rate limits

Upstream engines behind SearXNG (Brave, DuckDuckGo, Google CSE, …) can CAPTCHA or suspend your instance if Aldwin searches too fast. Aldwin mitigates this by:

- Serializing SearXNG calls with a short gap between them
- Capping `web_search` calls per turn (`limits.web_searches`, default **5**)
- Marking local search as not parallel-safe (no burst of concurrent searches)
- Returning a clear error when engines report rate limits / CAPTCHA so the agent stops retrying

If you still see empty results with "rate-limited or blocked", wait a minute or switch to a hosted provider with `/search tavily`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker: command not found` | Install/open Docker Desktop, new terminal, or add `~/.docker/bin` to `PATH`. |
| Container keeps restarting | Set a non-default `server.secret_key` in `core-config/settings.yml`, then `docker compose up -d`. |
| HTTP 403 / "Enable JSON" | Add `json` under `search.formats`, restart Compose. |
| Aldwin still uses Tavily | Set `search.provider: searxng`, or remove the Tavily key for the test. |
| Connection refused on 8080 | `docker compose ps` in `~/searxng`; wait until `searxng-core` is Up and ports show `8080`. |
| Rate-limited / CAPTCHA / empty results | Wait, broaden the query, or `/search tavily`. See **Rate limits** above. |
