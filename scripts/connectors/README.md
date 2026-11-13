# Connector catalogue

`import_catalog.py` builds the apps people can add from Settings → Tools and `/connectors`:

- `src/aldwin/core/assets/connectors/catalog.yaml`, the catalogue;
- `web/src/components/brand/logos/app-marks.ts`, the apps' logos.

Both files are generated. Change `allowlist.yaml` and run the script; don't edit them by hand. Aldwin never fetches anything at runtime. The script runs on a maintainer's machine, and the result is reviewed in a pull request like any other change.

## Running it

```sh
uv run python scripts/connectors/import_catalog.py --refresh
```

| Option | What it does |
|---|---|
| `--refresh` | Downloads the [MCP Registry](https://registry.modelcontextprotocol.io) again. Otherwise the copy in `.cache/registry.json` is used (git ignores it). |
| `--dry-run` | Prints the report and writes nothing. |
| `--only NAME` | Checks one app. Every other app stays as the catalogue has it. |
| `--offline` | Uses the cached registry, keeps each app's known sign-in method and leaves the logos alone. Useful for a quick check without network access. |

It prints what it added, changed and left out, and why. Exit codes: `0` every app made it in, `1` an app was left out, `2` the registry couldn't be reached.

## Adding an app

Add a block to `allowlist.yaml` where you want it to appear (the order is the display order):

```yaml
  - name: stripe                 # the connector name, as saved in connectors.yaml
    title: Stripe
    purpose: Customers, payments, invoices and products   # one plain line, at most 60 characters
    keywords: [stripe, payments, billing]                 # at least two; include a generic word
    category: payments           # work, developer, payments, design, automation, data or search
    docs_url: https://docs.stripe.com/mcp
    logo: simple-icons:stripe    # simple-icons:<slug>, lobehub:<id>, hand or letter
    source: registry
    namespace: com.stripe        # the vendor's own domain, reversed
```

Use `source: manual` with a `url` taken from the vendor's own docs when the vendor doesn't publish in the registry under its own domain.

Optional fields:
- `remote`: picks one address when the registry lists several.
- `expect_auth`: states the sign-in method (`oauth`, `bearer` or `none`) and fails if the server disagrees.
- `token_help_url`: the page where people create a token. Required for token apps.
- `oauth.client_id`: a pre-registered client, for vendors that support neither dynamic registration nor a client metadata document.

## What counts as verified

Anyone can publish to the registry. A name like `app.vercel.something` or `ai.smithery/...` proves nothing about who runs the server, and a search for "Linear" returns dozens of look-alikes. So the script uses a registry entry only when all of these hold:

- the vendor is in `allowlist.yaml`;
- the entry's namespace is that vendor's namespace, and not a shared host;
- the entry is active and the latest version;
- it has a streamable HTTP address on the vendor's own domain, with no `{placeholders}`.

It then checks sign-in the way Aldwin does when someone connects:

- **Browser sign-in:** the server advertises OAuth with PKCE, and Aldwin can register through dynamic registration, a client metadata document or a pre-registered client.
- **Token sign-in:** the server refuses an anonymous call and publishes no sign-in details.
- **No sign-in:** the server answers an anonymous call.

Anything else leaves the app out, with the reason. Before a release, `uv run pytest -m live tests/live/test_catalog_live.py` checks every shipped app again.

## Logos

Logos come from the pinned versions of [Simple Icons](https://simpleicons.org) (CC0) and [LobeHub icons](https://github.com/lobehub/lobe-icons) (MIT), as plain path data. An SVG with anything beyond paths, such as scripts, images, links, `<use>` or transforms, is refused, and the app gets a letter mark instead.
- **Colours:** near-black and near-white brand colours are dropped, so those marks follow the text colour in both themes.
- **Hand-kept marks:** the marks in `marks.ts` (models and search) and `hand-app-marks.ts` (apps) always win over generated ones. Use `logo: hand` for a mark drawn there from the brand's own assets when neither icon set has it; the report flags a `hand` app with no mark in `marks.ts`.
- **NOTICE:** list every new mark under its source in `NOTICE`.

## Apps that aren't in the catalogue yet

| App | Why |
|---|---|
| Asana, Slack, HubSpot | Their sign-in servers support neither dynamic registration nor a client metadata document, so Aldwin needs a registered app with each. |
| Box | Its sign-in server answers for a different issuer than it advertises. |
| Shopify | Each shop has its own address. |
| Google | No official remote server. |
