#!/usr/bin/env bash
# Runs Playwright in the same Linux image CI uses, so Firefox, WebKit and
# Chromium all run. The repo is copied into the container and never written to.
#
#   scripts/playwright-docker.sh                     # the whole suite
#   PW_KIT=1 scripts/playwright-docker.sh --project=kit-light --project=kit-dark
set -euo pipefail

IMAGE="mcr.microsoft.com/playwright:v1.65.0-noble"
WEB="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm --ipc=host \
  -e CI=1 -e PW_KIT="${PW_KIT:-}" \
  -v "$WEB":/src:ro \
  "$IMAGE" bash -c '
    set -euo pipefail
    mkdir -p /w && cd /src
    tar --exclude=node_modules --exclude=.next --exclude=out --exclude=test-results --exclude=playwright-report -cf - . | tar -xf - -C /w
    cd /w && npm ci --no-audit --no-fund >/dev/null
    npx next build >/dev/null
    npx playwright test "$@"
  ' bash "$@"
