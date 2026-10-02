#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
version="${SPEC_KIT_VERSION:-1.0.10}"
uv run --project "$root" python "$root/scripts/build_release.py"
archive="$root/dist/atlassian-0.2.0.zip"
unzip -q "$archive" -d "$scratch/extension"
uvx --from "specify-cli==$version" specify init "$scratch/project" --non-interactive \
  --integration codex --script py --ignore-agent-tools
(
  cd "$scratch/project"
  uvx --from "specify-cli==$version" specify extension add --dev "$scratch/extension"
)
test -f "$scratch/project/.specify/extensions/atlassian/extension.yml"
uv run --script "$scratch/extension/scripts/run.py" --help

uvx --from "specify-cli==$version" specify preset add --help >/dev/null
(
  cd "$scratch/project"
  uvx --from "specify-cli==$version" specify preset add --dev "$root/presets/constitution-gate"
)

# Compose both real presets; never install from the development checkout with its caches.
curl --connect-timeout 20 --max-time 90 -fsSL \
  https://codeload.github.com/bbjwz/agentstandards/zip/da5a2f029bd318ac96ad5682430ca6fbf4a01640 \
  -o "$scratch/core.zip"
unzip -q "$scratch/core.zip" -d "$scratch/core"
core="$scratch/core/agentstandards-da5a2f029bd318ac96ad5682430ca6fbf4a01640"
(
  cd "$scratch/project"
  uvx --from "specify-cli==$version" specify extension add --dev "$core"
  uvx --from "specify-cli==$version" specify preset add --dev "$core/presets/agentstandards-gate"
)
uv run --project "$root" python - "$scratch/project" <<'PYCODE'
import sys
from pathlib import Path
project = Path(sys.argv[1])
files = [p for p in project.rglob('*.md') if '.specify' not in p.parts]
for name in ('specify', 'clarify', 'plan', 'tasks', 'implement'):
    matches = [p for p in files if 'speckit-' + name in p.as_posix() or p.name == 'speckit.' + name + '.md']
    assert matches, (name, 'generated command missing')
    assert any('constitution gate --if-enabled' in p.read_text() for p in matches), (name, 'wrapper missing')
    if name == 'tasks':
        assert any('constitution gate --if-enabled' in p.read_text() and 'agentstandards.py gate' in p.read_text() for p in matches), 'Both gates must remain in the generated tasks command'
print('All five constitution guards and the composed Agentstandards gate are installed')
PYCODE
