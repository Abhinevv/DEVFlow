"""Static analysis (Ruff) and a lightweight AST security scan. Neither executes generated code."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.tools.workspace import Workspace

SECRET_RE = re.compile(r"""(?i)(api[_-]?key|secret|password|passwd|token)\s*=\s*['"][^'"]{8,}['"]""")


@dataclass
class Issue:
    source: str  # ruff | security
    severity: str
    code: str
    file: str
    line: int
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def run_ruff(workspace: Workspace) -> tuple[list[Issue], str | None]:
    """Return (issues, error). Ruff only parses files, so it is safe to run on the host."""
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if not ruff:
        return [], "ruff is not installed"
    try:
        proc = subprocess.run(
            [ruff, "check", "--no-cache", "--output-format", "json", "--select", "E9,F,B,S",
             # S101: asserts in tests; S311: non-crypto random; B008: FastAPI's Depends() default-argument idiom.
             "--ignore", "S101,S311,B008", "."],
            cwd=workspace.root, capture_output=True, timeout=60, text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"ruff failed: {exc}"
    try:
        records = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [], f"ruff produced unreadable output: {proc.stderr[:500]}"
    issues = []
    for r in records:
        code = r.get("code") or "?"
        severity = "high" if code.startswith(("E9", "S")) and code not in ("S105", "S106") else "low"
        if code.startswith("F8"):
            severity = "medium"  # undefined names
        rel = _rel(workspace, r.get("filename", ""))
        issues.append(Issue("ruff", severity, code, rel, (r.get("location") or {}).get("row", 0), r.get("message", "")))
    return issues, None


def security_scan(workspace: Workspace) -> list[Issue]:
    issues: list[Issue] = []
    for rel in workspace.list_files():
        if not rel.endswith(".py"):
            continue
        try:
            source = workspace.read(rel)
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        is_test = rel.startswith("tests/") or "/test_" in rel or rel.split("/")[-1].startswith("test_")
        for lineno, line in enumerate(source.splitlines(), 1):
            if SECRET_RE.search(line) and not is_test:
                issues.append(Issue("security", "high", "hardcoded-secret", rel, lineno, "Possible hard-coded credential"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in ("eval", "exec"):
                issues.append(Issue("security", "high", "dynamic-exec", rel, node.lineno, f"Use of {name}()"))
            elif name in ("os.system", "os.popen"):
                issues.append(Issue("security", "high", "shell-exec", rel, node.lineno, f"Use of {name}()"))
            elif name.startswith("subprocess.") and any(k.arg == "shell" and _truthy(k.value) for k in node.keywords):
                issues.append(Issue("security", "high", "shell-true", rel, node.lineno, "subprocess call with shell=True"))
            elif name in ("pickle.loads", "pickle.load", "marshal.loads"):
                issues.append(Issue("security", "medium", "unsafe-deserialization", rel, node.lineno, f"Use of {name}()"))
            elif name == "yaml.load" and not any(k.arg == "Loader" for k in node.keywords):
                issues.append(Issue("security", "medium", "yaml-load", rel, node.lineno, "yaml.load without an explicit safe Loader"))
            elif name in ("hashlib.md5", "hashlib.sha1") and not is_test:
                issues.append(Issue("security", "low", "weak-hash", rel, node.lineno, f"{name} is unsuitable for passwords"))
            elif name.endswith(".execute") and node.args and isinstance(node.args[0], (ast.JoinedStr, ast.BinOp)):
                issues.append(Issue("security", "high", "sql-injection", rel, node.lineno, "SQL built with string formatting"))
    return issues


def _call_name(func: ast.AST) -> str:
    parts = []
    while isinstance(func, ast.Attribute):
        parts.insert(0, func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.insert(0, func.id)
    return ".".join(parts)


def _truthy(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value)


def _rel(workspace: Workspace, filename: str) -> str:
    try:
        return Path(filename).resolve().relative_to(workspace.root).as_posix()
    except (ValueError, OSError):
        return filename
