# Aldwin web UI

The browser interface for Aldwin: a Next.js App Router app written in TypeScript and styled with the Aldwin docs design system.

It is built as a static export. `npm run build` writes the export to `web/out/`, then copies it into `src/aldwin/web/static/`, where it ships inside the Python package. The `aldwin web` command serves those files from the installed package, so people who install Aldwin never need Node.

## Requirements

- Node 22 or later
- npm (the lockfile is `package-lock.json`)
- Python 3, which `npm run preview` uses to serve the export

`package.json` approves Cypress's install script for one pinned version, so `npm ci` downloads the Cypress binary. After upgrading Cypress, approve the new version with `npm install-scripts approve cypress`.

## Commands

Run these from `web/`:

| Command | What it does |
| --- | --- |
| `npm ci` | Install the exact locked dependencies |
| `npm run dev` | Start the dev server with hot reload |
| `npm run lint` | ESLint |
| `npm run typecheck` | TypeScript for the app and the Cypress specs |
| `npm run build` | Static export, copied into `src/aldwin/web/static/` |
| `npm run preview` | Serve `out/` on `http://127.0.0.1:4173` |
| `npm run test:e2e` | Serve the export and run the Cypress smoke test |
| `npm run cypress:open` | Open Cypress interactively (run `npm run preview` first) |

CI runs `lint`, `typecheck`, `build` and the Cypress smoke test in the `web` job. The release workflow runs `npm run build` before building the Python package.

## Developing against a running Aldwin

In production one Python process serves both the UI and the API. During development, run the Next dev server for hot reload and point it at a local `aldwin web`. The dev server forwards `/v1/*` to `http://127.0.0.1:7841`. `aldwin web` moves to another free port when 7841 is busy, so keep 7841 free while developing. That forwarding exists only in `npm run dev`; a static export cannot rewrite requests.

A static export also rules out route handlers, middleware, server actions and image optimisation. Anything that needs a server belongs in the Python API.

## The packaged page

`src/aldwin/web/static/` is tracked in git. The committed copy is a small placeholder page, so a Python-only `uv build` still ships a page. A local `npm run build` replaces it with the real export. Restore the placeholder before you commit, unless the change is meant to update it:

```bash
git restore --staged --worktree src/aldwin/web/static
git clean -fd src/aldwin/web/static
```

## Source layout

```
src/
  app/          routes only: each page.tsx stays thin and composes the pieces below
  components/
    atoms/      one element with its look: Button, Input, Select, Label, Chip
    molecules/  a few atoms that work together: TextField, SelectField, ToggleChip
    organisms/  a section with its own behaviour: ProfileStep, KeyForm, ModelPicker
  layouts/      reusable page frames: AppShell, CenteredLayout, OnboardingLayout
  providers/    React context providers: ThemeProvider, SetupProvider
  hooks/        reusable hooks: useSetup, useAction, useHostReady
  state/        client stores, added with the first store that needs one
  styles/       globals.css imports tokens, theme, base, recipes and motion
  utils/        reusable functions, one folder per topic: api/, cn/, gates/
  data/         all text the pages show, as JSON: one file per page, layout or section
  constants/    fixed values that are not text: routes, ids, theme colours, timings
  types/        shared types, such as the setup API shapes
```

Text never sits in TSX. Each page, layout or section reads its own file in `data/` (`data/home.json`, `data/onboarding/connect.json`, `data/onboarding/key-form.json`), named after where the text appears. Placeholders such as `{step}` are filled with `fill()` from `utils/text`. Atoms and molecules take their text as props.

Size decides the component level: a single element is an atom, a small group of atoms is a molecule, and anything with its own state or calls is an organism.

## Design system

The tokens in `src/styles/tokens.css` are ported from the Aldwin docs site: semantic colour variables for light (`:root`) and dark (`.dark`), the type scale, radii and shadows. Tailwind's stock palette is switched off, so use the semantic classes (`bg-base`, `text-fg-secondary`, `border-border-default`). Other pieces:

- Fonts: Mona Sans and Mona Sans Mono, bundled locally from the official release with their OFL licence in `src/assets/fonts/mona-sans/`.
- Theme: `next-themes` with the `class` strategy; dark by default, with light and system.
- Buttons: `buttonClass()` in `src/components/atoms/button.tsx` (`ghost`, `outline`, `primary`).
- Icons: `lucide-react`.

There is no component library. Add primitives only when accessibility needs them.

## Tests

`cypress/e2e/smoke.cy.ts` only checks that the export loads with the design tokens applied. Full journey coverage (onboarding, streaming, history, the `@` menu) is added with those screens before the 0.0.6 release.

If Cypress fails to start with `bad option: --no-sandbox`, your terminal was started by an Electron-based editor that sets `ELECTRON_RUN_AS_NODE`. Run it with that variable cleared:

```bash
env -u ELECTRON_RUN_AS_NODE npm run test:e2e
```
