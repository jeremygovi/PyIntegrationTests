"""Prevent a tag from labeling a different package version."""

import os
import tomllib
from pathlib import Path

project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
expected = f"v{project['version']}"
if os.environ.get("RELEASE_TAG") != expected:
    raise SystemExit(f"Release tag must equal {expected}")
