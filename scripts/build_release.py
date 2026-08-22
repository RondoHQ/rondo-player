#!/usr/bin/env python3
"""Build the release archive and its checksum manifest."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def project_version() -> str:
    contents = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', contents, re.MULTILINE)
    if not match:
        raise SystemExit("Projectversie ontbreekt")
    return match.group(1)


def main() -> None:
    version = project_version()
    DIST.mkdir(exist_ok=True)
    artifact = DIST / f"rondo-player-{version}.tar.gz"
    with tarfile.open(artifact, "w:gz", compresslevel=9) as bundle:
        for path in sorted((ROOT / "rondo_player").rglob("*")):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            bundle.add(path, arcname=path.relative_to(ROOT), recursive=False)

    manifest = {
        "artifact": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "version": version,
    }
    (DIST / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(artifact)


if __name__ == "__main__":
    main()
