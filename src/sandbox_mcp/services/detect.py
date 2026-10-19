import json
from pathlib import Path
from typing import Literal

Kind = Literal["static", "node_build", "dockerfile", "unsupported"]
STATIC_DIRS = ("dist", "build", "public", "out")


def detect_kind(path: Path) -> Kind:
    if (path / "Dockerfile").is_file():
        return "dockerfile"
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        if "build" in scripts:
            return "node_build"
    if (path / "index.html").is_file():
        return "static"
    for d in STATIC_DIRS:
        if (path / d / "index.html").is_file():
            return "static"
    return "unsupported"


def publish_directory(path: Path) -> str:
    if (path / "index.html").is_file():
        return "/"
    for d in STATIC_DIRS:
        if (path / d / "index.html").is_file():
            return f"/{d}"
    return "/"
