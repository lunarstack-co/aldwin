# Aldwin web UI

The browser interface for Aldwin: a Next.js App Router app written in TypeScript, built from shadcn/ui primitives and assistant-ui components, and styled with the assistant-ui design system in light and dark.

It is built as a static export. `npm run build` writes the export to `web/out/`, then copies it into `src/aldwin/web/static/`, where it ships inside the Python package. The `aldwin web` command serves those files from the installed package, so people who install Aldwin never need Node.

## Requirements

- Node 22 or later
- npm (the lockfile is `package-lock.json`)
- Docker, only to run the suite in the Linux image CI uses

## Commands

Run these from `web/`:

| Command | What it does |
| --- | --- |
| `npm ci` | Install the exact locked dependencies |
| `npm run dev` | Start the dev server with hot reload |
| `npm run lint` | ESLint |
| `npm run typecheck` | TypeScript for the app and the Playwright specs |
| `npm run build` | Static export, copied into `src/aldwin/web/static/` |
| `npm run preview` | Serve `out/` on `http://127.0.0.1:4173` the way `aldwin web` does |
| `npm run test:pw` | Run the Playwright suite against the export (`test:e2e` is the same) |
| `npm run test:pw:ui` | Open Playwright's UI mode |
| `npm run test:pw:timing` | Run the `@timing` checks alone on one worker, as CI does |
| `npm run test:pw:kit` | Check the component gallery at `/dev/kit/` on `next dev` |
| `npm run test:pw:docker` | Run the suite in the Linux image CI uses |
| `npm run icons` | Redraw the favicon, Apple icon and app icons from the Aldwin mark |

CI runs `lint`, `typecheck`, `build`, the Playwright suite, the timing checks on their own and the kit check in the `web` job, and uploads the report and traces when a test fails. The release workflow runs `npm run build` before building the Python package.

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
  app/          routes only: (chat)/ holds / and /c/, which share one chat shell; onboarding/ the first run
  components/
    ui/           design-kit and shadcn primitives: Button, Input, Select, Dialog, Sheet, Callout, Steps…
    assistant-ui/ chat pieces: registry files in elements/, plus Aldwin's tool row, work chip, plan and states
    brand/        the Aldwin mark and lockup
    organisms/    sections with their own behaviour: chat/ChatShell, the rail, onboarding forms
  layouts/      page frames: AppShell, ChatLayout, OnboardingLayout, CenteredLayout
  providers/    React context providers: ThemeProvider, SetupProvider, ChatRuntimeProvider
  hooks/        reusable hooks: useChatSession, useConversationRoute, usePageTitle, useDensity…
  state/        Zustand stores: the transcript (chat-store) and the socket phase (connection-store)
  styles/       globals.css imports tokens, theme, base and motion
  utils/        reusable functions, one folder per topic: api/, ws/, history/, time/, page-title/…
  data/         all text the pages show, as JSON, named after where it appears
  constants/    fixed values that are not text: routes, ids, theme colours, timings
  types/        shared types, such as the protocol and the setup API shapes
```

Text never sits in TSX. Each page, layout or section reads its own file in `data/`. Placeholders such as `{seconds}` are filled with `fill()` from `utils/text`.

## Components

- `components/ui/` is copied from the assistant-ui design kit (`packages/ui/src/components/react/ui/base` in `assistant-ui/assistant-ui`; the commit is in `components/ui/README.md`), plus `radio-group` from shadcn's `base-nova` style and a small `Field`. They are Base UI components styled with Tailwind.
- `components/assistant-ui/elements/` comes from the assistant-ui registry through `components.json` (`npx shadcn@latest add @assistant-ui/<name>`). Controls for things the host does not support (attachments, dictation, edit and branch, feedback, suggestions) are deleted from these copies, not hidden.
- When re-adding a registry item, answer **no** to every overwrite prompt for `components/ui/*`, or the kit files are replaced by shadcn's own (`yes n | npx shadcn@latest add …`).

The dev-only gallery at `/dev/kit/` (`app/dev/kit/page.dev.tsx`) shows every `ui/` component in both themes. The `dev.tsx` extension is registered only for `next dev`, so the page never reaches the export.

## Chat runtime

The chat renders with `@assistant-ui/react` primitives and registry components. `providers/chat-runtime-provider.tsx` feeds them from the Zustand chat store through `useExternalStoreRuntime`, so the Aldwin protocol in `utils/ws/` stays the only transport: sending, stopping and retrying go back through the session, and Retry always adds a new turn. The runtime's thread list is fed the saved conversations; choosing one navigates.

Each conversation has its own address, `/c/?id=<id>`. `hooks/use-conversation-route.ts` keeps the address bar and the host in step: the rail and See all only navigate, the hook asks the host to open what the URL names, Back and Forward work, a first turn gives a new chat its address without a new history entry, and going to `/` from a conversation starts a new chat. A refused switch (busy, deleted) puts the address back. `/` and `/c/` share the `(chat)` layout, so moving between them keeps one socket.

Clean density (the default) folds a turn's tool calls into one "Working for 8s" / "Worked for 12s" chip; Detail shows each call. The rail shows the 15 most recent conversations; See all groups every one into Today, Yesterday, Previous 7 days and Older.

### Skills (`@`)

Typing `@` in the composer, or pressing its `@` button, opens the skills menu: `ComposerPrimitive.TriggerPopover` through the registry's `composer-trigger-popover`, in `components/organisms/chat/skills-popover.tsx`. The catalogue comes from `skills.list` the first time the menu opens and then from the host's `skills` events. Skills that failed to load are hidden, recently used ones come first, and at most eight rows show. A pick is written into the text as `@name`, the form the CLI uses, and on send the session reads every `@name` in the text into `turn.skills` (`utils/skills`), so Retry and pasted text carry the same skills. The host uses at most three per turn and reports the rest in `skills.used`; sent messages show `@name` as chips, with the used skills and any dropped ones underneath. Tools are never sent. The used and dropped lines come from the live `skills.used` event, which the host does not save, so after a reload only the chips remain.

## Design system

The tokens in `src/styles/tokens.css` are the assistant-ui set, copied value for value: every neutral sits on the `--tint` hue, `--live` (blue) marks only what is running, and shadows are zeroed except on floating surfaces. `src/styles/theme.css` maps them onto Tailwind's shadcn names (`bg-background`, `text-muted-foreground`, `border-border`, `rounded-thread`…). Retheming Aldwin later means editing `tokens.css`.

- Fonts: Public Sans and JetBrains Mono, bundled locally with their OFL licences in `src/assets/fonts/`.
- Theme: `next-themes` with the `class` strategy; dark by default, with light and system, kept in the browser only.
- Icons: `lucide-react`.

## Tests

Playwright runs against the static export (`playwright/serve.mjs` serves it like `aldwin web`, including 404s) with no host behind it. The `host` fixture in `playwright/fixtures/fake-host.ts` plays Aldwin: it answers the WebSocket protocol through `page.routeWebSocket` and the REST routes through `page.route`, records every frame the page sends, and lets each spec script the host (`host.stream()`, `host.toolCall()`, `host.drop()`, `host.notice()`…).

- Functional specs run on Chromium in light and dark; specs tagged `@cross` also run on Firefox, WebKit, Pixel 7 and iPhone 15.
- `a11y.spec.ts` runs axe (WCAG 2.2 AA) on each state and walks the app by keyboard.
- `units.spec.ts` checks the pure helpers in Node.
- `perf.spec.ts` fails when any page in `out/` loads more than 560 KiB of gzipped JS. The limit is per page because no page loads every chunk.
Full journeys with a real `aldwin web` behind them belong to the release smoke suite.
