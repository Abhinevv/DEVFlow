"""Approved project templates (spec 6.2) and the dependency policy (security 7)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.tools.workspace import Workspace

TEMPLATES_DIR = Path(__file__).resolve().parent
REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[^\]]*\])?")


@dataclass
class Template:
    id: str
    name: str
    version: str
    description: str
    language: str
    approved_dependencies: list[str]
    conventions: list[str]
    files_dir: Path

    def public(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version, "description": self.description,
            "language": self.language, "approved_dependencies": self.approved_dependencies, "conventions": self.conventions,
        }


def list_templates() -> list[Template]:
    return [load_template(p.parent.name) for p in sorted(TEMPLATES_DIR.glob("*/template.json"))]


def load_template(template_id: str) -> Template:
    if not re.fullmatch(r"[a-z0-9_]+", template_id):
        raise KeyError(template_id)
    meta_file = TEMPLATES_DIR / template_id / "template.json"
    if not meta_file.is_file():
        raise KeyError(template_id)
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    return Template(files_dir=meta_file.parent / "files", **meta)


def apply_template(template: Template, workspace: Workspace) -> list[str]:
    written = []
    for path in sorted(template.files_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(template.files_dir).as_posix()
            workspace.write(rel, path.read_text(encoding="utf-8"))
            written.append(rel)
    return written


def unapproved_requirements(content: str, approved: list[str]) -> list[str]:
    """Package names in a requirements.txt that are not on the approved list. Options and URLs are refused."""
    allowed = {_canon(a) for a in approved}
    bad = []
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-") or "://" in line or line.startswith((".", "/")) or " @ " in line:
            bad.append(line)
            continue
        match = REQ_NAME_RE.match(line)
        if not match or _canon(match.group(1)) not in allowed:
            bad.append(line)
    return bad


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()
