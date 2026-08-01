#!/usr/bin/env sh
# Rebuild app/static/app.css from app/static/src/input.css.
#
# The built CSS is committed so the Docker image needs no Node toolchain — the
# runtime image is Python only. Run this after editing input.css or after adding a
# Tailwind class that no template used before.
#
#   tools/build_css.sh          # one-shot
#   tools/build_css.sh --watch  # rebuild on change while working on templates
set -eu

CLI="${TAILWIND_CLI:-./tailwindcss}"

if [ ! -x "$CLI" ]; then
  echo "Tailwind standalone CLI not found at $CLI" >&2
  echo "Download it (no Node required):" >&2
  echo "  curl -sSL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64" >&2
  echo "  chmod +x tailwindcss" >&2
  exit 1
fi

exec "$CLI" -i app/static/src/input.css -o app/static/app.css --minify "$@"
