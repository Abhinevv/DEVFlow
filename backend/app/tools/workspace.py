"""Controlled project workspace. Every agent file operation goes through here (FR-07, security 3/6)."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALLOWED_SUFFIXES = {
    ".py", ".txt", ".md", ".ini", ".toml", ".cfg", ".json", ".yaml", ".yml",
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".sql", ".csv",
}
ALLOWED_DOTFILES = {".gitignore", ".env.example"}
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
MAX_PATH_LEN = 200
MAX_DEPTH = 8


class PathViolation(ValueError):
    """A requested path falls outside the workspace policy."""


@dataclass
class AppliedChange:
    path: str
    action: str  # create | modify | delete | rejected
    sha256: str | None
    size: int
    reason: str | None = None


class Workspace:
    def __init__(self, root: Path, *, max_file_bytes: int = 200_000, max_files: int = 300):
        self.root = root.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    # --- path policy --------------------------------------------------------------------

    def validate(self, rel: str) -> str:
        """Return the normalised relative path, or raise PathViolation."""
        if not isinstance(rel, str) or not rel.strip():
            raise PathViolation("empty path")
        if "\x00" in rel:
            raise PathViolation("NUL byte in path")
        if "\\" in rel:
            raise PathViolation("backslashes are not allowed; use forward slashes")
        if len(rel) > MAX_PATH_LEN:
            raise PathViolation("path too long")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or rel.startswith("~"):
            raise PathViolation("absolute paths are not allowed")
        # Split the raw string: PurePosixPath would silently drop "." and empty segments.
        parts = tuple(rel.split("/"))
        if any(p == "" for p in parts):
            raise PathViolation("empty path segment")
        if len(parts) > MAX_DEPTH:
            raise PathViolation("path too deep")
        for part in parts:
            if part in ("..", "."):
                raise PathViolation("path traversal is not allowed")
            if not SEGMENT_RE.match(part):
                raise PathViolation(f"illegal characters in path segment {part!r}")
            if part.split(".")[0].lower() in WINDOWS_RESERVED:
                raise PathViolation(f"reserved name {part!r}")
            if part.startswith(".") and part not in ALLOWED_DOTFILES:
                raise PathViolation(f"hidden path segment {part!r} is not allowed")
        name = parts[-1]
        if name not in ALLOWED_DOTFILES and PurePosixPath(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise PathViolation(f"file type of {name!r} is not permitted")
        target = (self.root / Path(*parts)).resolve()
        if not target.is_relative_to(self.root):
            raise PathViolation("path resolves outside the workspace")
        # Refuse to follow links that already exist inside the workspace.
        probe = self.root
        for part in parts:
            probe = probe / part
            if probe.is_symlink():
                raise PathViolation(f"symbolic link in path: {part}")
        return "/".join(parts)

    def _abs(self, rel: str) -> Path:
        return self.root / Path(*PurePosixPath(self.validate(rel)).parts)

    # --- operations ---------------------------------------------------------------------

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def exists(self, rel: str) -> bool:
        return self._abs(rel).is_file()

    def read(self, rel: str) -> str:
        return self._abs(rel).read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> AppliedChange:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes:
            raise PathViolation(f"{rel}: file exceeds {self.max_file_bytes} bytes")
        target = self._abs(rel)
        existed = target.exists()
        if not existed and len(self.list_files()) >= self.max_files:
            raise PathViolation("workspace file limit reached")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return AppliedChange(self.validate(rel), "modify" if existed else "create", hashlib.sha256(data).hexdigest(), len(data))

    def delete(self, rel: str) -> AppliedChange:
        target = self._abs(rel)
        if target.is_file():
            target.unlink()
        return AppliedChange(self.validate(rel), "delete", None, 0)

    def list_files(self) -> list[str]:
        if not self.root.exists():
            return []
        files = []
        for path in self.root.rglob("*"):
            rel = path.relative_to(self.root)
            if any(p == ".git" or p == "__pycache__" or p == ".pytest_cache" for p in rel.parts):
                continue
            if path.is_file() and not path.is_symlink():
                files.append(rel.as_posix())
        return sorted(files)

    def snapshot(self, max_total_chars: int = 400_000, only: list[str] | None = None) -> dict[str, str]:
        """Text contents of workspace files, for model context."""
        out: dict[str, str] = {}
        total = 0
        for rel in only or self.list_files():
            try:
                text = self.read(rel)
            except (PathViolation, UnicodeDecodeError, FileNotFoundError):
                continue
            if total + len(text) > max_total_chars:
                out[rel] = "<omitted: context budget exceeded>"
                continue
            out[rel] = text
            total += len(text)
        return out

    def copy_to(self, destination: Path) -> None:
        shutil.copytree(self.root, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"), dirs_exist_ok=True)


def render_snapshot(files: dict[str, str]) -> str:
    return "\n\n".join(f'<file path="{path}">\n{content}\n</file>' for path, content in files.items()) or "(workspace is empty)"
