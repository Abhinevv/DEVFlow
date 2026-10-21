"""Execution of generated code (security requirements 1-8).

DockerExecutor is the isolated path: no network, read-only workspace mount, resource limits,
dropped capabilities, non-root user. LocalExecutor exists for development on machines without
Docker; it runs on a throwaway copy with a scrubbed environment and a timeout, but it is NOT a
security boundary and must be enabled explicitly with DEVFLOW_ALLOW_LOCAL_EXECUTION=true.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

RESULT_MARKER = "@@DEVFLOW_RESULT_FILE@@"


class ExecutionUnavailable(RuntimeError):
    pass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    executor: str
    isolated: bool
    result_file: str | None = None  # contents of the requested result file, if any


def _truncate(data: bytes, limit: int) -> str:
    text = data.decode("utf-8", errors="replace")
    if len(data) > limit:
        return text[: limit // 2] + f"\n... [{len(data) - limit} bytes truncated] ...\n" + text[-limit // 2 :]
    return text


class Executor(ABC):
    name: str
    isolated: bool

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def run_python(self, args: list[str], workspace: Path, *, result_file: str | None = None, timeout: int | None = None) -> ExecResult:
        """Run `python <args>` against a copy of the workspace. If result_file is given (a path relative
        to the scratch directory), its contents are returned in ExecResult.result_file."""


class DockerExecutor(Executor):
    name = "docker"
    isolated = True

    @staticmethod
    def available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def run_python(self, args, workspace, *, result_file=None, timeout=None):
        s = self.settings
        timeout = timeout or s.exec_timeout_seconds
        container = f"devflow-{uuid.uuid4().hex[:12]}"
        # Write scratch output to tmpfs, then print it after a marker so the workspace stays read-only.
        quoted = " ".join(_sh_quote(a) for a in args)
        script = f"cd /workspace && python {quoted}; code=$?"
        if result_file:
            script += f"; echo '{RESULT_MARKER}'; cat /tmp/{result_file} 2>/dev/null"
        script += "; exit $code"
        cmd = [
            "docker", "run", "--rm", "--name", container,
            "--network", "none",
            "--memory", f"{s.exec_memory_mb}m", "--memory-swap", f"{s.exec_memory_mb}m",
            "--cpus", str(s.exec_cpus), "--pids-limit", str(s.exec_pids_limit),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m",
            "--user", "65534:65534",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp", "-e", "SCRATCH=/tmp",
            "-v", f"{workspace.resolve()}:/workspace:ro",
            s.sandbox_image, "sh", "-c", script,
        ]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", container], capture_output=True)
            proc = subprocess.CompletedProcess(cmd, 124, exc.stdout or b"", exc.stderr or b"")
            timed_out = True
        stdout = _truncate(proc.stdout, s.max_output_bytes)
        result = None
        if result_file and RESULT_MARKER in stdout:
            stdout, result = stdout.split(RESULT_MARKER, 1)
            result = result.strip() or None
        return ExecResult(proc.returncode, stdout, _truncate(proc.stderr, s.max_output_bytes),
                          int((time.monotonic() - started) * 1000), timed_out, self.name, self.isolated, result)


class LocalExecutor(Executor):
    name = "local"
    isolated = False

    def run_python(self, args, workspace, *, result_file=None, timeout=None):
        s = self.settings
        timeout = timeout or s.exec_timeout_seconds
        with tempfile.TemporaryDirectory(prefix="devflow-run-") as tmp:
            tmp_path = Path(tmp)
            work = tmp_path / "workspace"
            scratch = tmp_path / "scratch"
            scratch.mkdir()
            shutil.copytree(workspace, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            env = _scrubbed_env(work, scratch)
            cmd = [sys.executable, "-I", *[a.replace("{scratch}", str(scratch)) for a in args]]
            started = time.monotonic()
            kwargs: dict = {"cwd": work, "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "stdin": subprocess.DEVNULL}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
                kwargs["preexec_fn"] = _posix_limits(s)
            proc = subprocess.Popen(cmd, **kwargs)
            timed_out = False
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                out, err = proc.communicate()
            result = None
            if result_file and (scratch / result_file).is_file():
                result = (scratch / result_file).read_text(encoding="utf-8", errors="replace")
            return ExecResult(124 if timed_out else proc.returncode, _truncate(out, s.max_output_bytes), _truncate(err, s.max_output_bytes),
                              int((time.monotonic() - started) * 1000), timed_out, self.name, self.isolated, result)


def _scrubbed_env(work: Path, scratch: Path) -> dict[str, str]:
    """Minimal environment: no API keys, tokens or DEVFLOW_* settings reach generated code."""
    env = {
        "PATH": os.path.dirname(sys.executable),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "HOME": str(scratch),
        "TMP": str(scratch),
        "TEMP": str(scratch),
        "SCRATCH": str(scratch),
        "NO_PROXY": "*",
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def _posix_limits(s: Settings):
    def apply():  # pragma: no cover - runs in the child on POSIX only
        import resource

        mem = s.exec_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (s.exec_timeout_seconds, s.exec_timeout_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))

    return apply


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()


def _sh_quote(value: str) -> str:
    value = value.replace("{scratch}", "/tmp")
    return "'" + value.replace("'", "'\"'\"'") + "'"


def create_executor(settings: Settings) -> Executor:
    choice = settings.executor
    if choice in ("auto", "docker") and DockerExecutor.available():
        return DockerExecutor(settings)
    if choice == "docker":
        raise ExecutionUnavailable("Docker executor requested but the Docker daemon is not reachable")
    if settings.allow_local_execution:
        return LocalExecutor(settings)
    raise ExecutionUnavailable(
        "No isolated executor is available. Start Docker (and build the sandbox image), "
        "or set DEVFLOW_ALLOW_LOCAL_EXECUTION=true to accept non-isolated local execution."
    )
