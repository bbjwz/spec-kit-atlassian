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
    for folder in ("src", "commands", "scripts"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".md") and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(root))
    for name in ("extension.yml", "README.md", "LICENSE", "pyproject.toml", "uv.lock"):
        archive.write(root / name, name)
print(output)
