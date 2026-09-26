#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
version="${SPEC_KIT_VERSION:-1.0.10}"
uv run --project "$root" python "$root/scripts/build_release.py"
archive="$(find "$root/dist" -maxdepth 1 -name '*.zip' -print -quit)"
unzip -q "$archive" -d "$scratch/extension"
uvx --from "specify-cli==$version" specify init "$scratch/project" --non-interactive \
  --integration codex --script py --ignore-agent-tools --extension "$scratch/extension"
uv run --script "$scratch/extension/scripts/run.py" --help
