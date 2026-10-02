"""Build an explicit-allowlist Spec Kit extension archive."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

root = Path(__file__).resolve().parents[1]
manifest = yaml.safe_load((root / "extension.yml").read_text())
name, version = manifest["extension"]["id"], manifest["extension"]["version"]
output = root / "dist" / f"{name}-{version}.zip"
output.parent.mkdir(exist_ok=True)
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for folder in ("src", "commands", "scripts", "docs"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".md") and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(root))
    for name in ("extension.yml", "README.md", "LICENSE", "pyproject.toml", "uv.lock"):
        archive.write(root / name, name)
print(output)

preset_root = root / "presets" / "constitution-gate"
preset_output = root / "dist" / "constitution-gate-0.2.0.zip"
with ZipFile(preset_output, "w", ZIP_DEFLATED) as archive:
    for path in sorted(preset_root.rglob("*")):
        if path.is_file() and path.suffix in (".md", ".yml"):
            archive.write(path, path.relative_to(preset_root))
print(preset_output)
