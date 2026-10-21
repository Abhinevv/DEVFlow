"""Per-project Git history: one commit per applied change set, used for history, diffs and rollback."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, root: Path):
        self.root = root
        self.enabled = shutil.which("git") is not None

    def _run(self, *args: str, check: bool = True) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "DEVFLOW"))}
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
        cmd = [
            "git", "-c", "user.name=DevFlow AI", "-c", "user.email=devflow@localhost",
            "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.devflow-no-hooks",
            *args,
        ]
        proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        if check and proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout

    def init(self) -> None:
        if not self.enabled or (self.root / ".git").exists():
            return
        self._run("init", "-q", "-b", "main")

    def commit_all(self, message: str) -> str | None:
        if not self.enabled:
            return None
        self._run("add", "-A")
        if not self._run("status", "--porcelain").strip():
            return self.head()
        self._run("commit", "-q", "-m", message)
        return self.head()

    def head(self) -> str | None:
        if not self.enabled:
            return None
        out = self._run("rev-parse", "HEAD", check=False).strip()
        return out if len(out) == 40 else None

    def log(self, limit: int = 100) -> list[dict]:
        if not self.enabled or not (self.root / ".git").exists():
            return []
        out = self._run("log", f"-n{limit}", "--pretty=format:%H%x1f%an%x1f%aI%x1f%s", "--shortstat", check=False)
        commits: list[dict] = []
        for line in out.splitlines():
            if "\x1f" in line:
                sha, author, date, subject = line.split("\x1f", 3)
                commits.append({"sha": sha, "author": author, "date": date, "message": subject, "stats": ""})
            elif line.strip() and commits:
                commits[-1]["stats"] = line.strip()
        return commits

    def show(self, sha: str) -> str:
        if not all(c in "0123456789abcdef" for c in sha) or not 7 <= len(sha) <= 40:
            raise GitError("invalid commit id")
        return self._run("show", "--stat", "--patch", "--format=%H%n%an %aI%n%n%s%n", sha)[:400_000]

    def reset_hard(self, sha: str) -> None:
        self._run("reset", "-q", "--hard", sha)
        self._run("clean", "-q", "-fd")
