"""Syntax checks and pytest execution with machine-readable results (FR-08, FR-11)."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from app.tools.executor import ExecResult, Executor
from app.tools.workspace import Workspace

PYTEST_EXIT_NO_TESTS = 5
JUNIT_FILE = "junit.xml"


@dataclass
class TestFailure:
    __test__ = False

    nodeid: str
    kind: str  # failure | error | syntax | collection
    message: str
    details: str = ""

    def as_dict(self) -> dict:
        return {"nodeid": self.nodeid, "kind": self.kind, "message": self.message, "details": self.details}


@dataclass
class TestReport:
    __test__ = False

    command: str
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_ms: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    log: str = ""
    executor: str = ""
    isolated: bool = False
    timed_out: bool = False
    no_tests: bool = False
    syntax_ok: bool = True

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def success(self) -> bool:
        return self.syntax_ok and not self.timed_out and self.failed == 0 and self.errors == 0 and self.exit_code in (0, PYTEST_EXIT_NO_TESTS)

    @property
    def summary(self) -> str:
        if not self.syntax_ok:
            return f"Syntax check failed in {len(self.failures)} file(s)"
        if self.timed_out:
            return "Test run timed out"
        if self.no_tests:
            return "No tests collected"
        return f"{self.passed} passed, {self.failed} failed, {self.errors} errors, {self.skipped} skipped"

    def failing_nodeids(self) -> list[str]:
        return [f.nodeid for f in self.failures if f.kind in ("failure", "error") and "::" in f.nodeid]


def syntax_check(workspace: Workspace) -> list[TestFailure]:
    """Parse every Python file without executing it."""
    problems = []
    for rel in workspace.list_files():
        if not rel.endswith(".py"):
            continue
        try:
            ast.parse(workspace.read(rel), filename=rel)
        except SyntaxError as exc:
            problems.append(TestFailure(f"{rel}:{exc.lineno}", "syntax", f"SyntaxError: {exc.msg}", (exc.text or "").strip()))
        except UnicodeDecodeError:
            problems.append(TestFailure(rel, "syntax", "File is not valid UTF-8"))
    return problems


def run_pytest(executor: Executor, workspace: Workspace, selectors: list[str] | None = None, extra_root: Path | None = None) -> TestReport:
    """Run pytest in the sandbox. `selectors` limits the run to specific node ids (failed-test retest)."""
    problems = syntax_check(workspace)
    if problems:
        return TestReport(command="syntax-check", exit_code=1, failures=problems, syntax_ok=False, errors=len(problems))

    args = ["-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE", f"--junitxml={{scratch}}/{JUNIT_FILE}"]
    args += selectors or []
    root = extra_root or workspace.root
    result = executor.run_python(args, root, result_file=JUNIT_FILE)
    report = _parse(result)
    report.command = "pytest " + " ".join(selectors or ["(full suite)"])
    return report


def _parse(result: ExecResult) -> TestReport:
    report = TestReport(
        command="pytest",
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        log=(result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr.strip() else ""))[-60_000:],
        executor=result.executor,
        isolated=result.isolated,
        timed_out=result.timed_out,
        no_tests=result.exit_code == PYTEST_EXIT_NO_TESTS,
    )
    if result.timed_out:
        report.failures.append(TestFailure("(run)", "error", "Test run exceeded the execution timeout"))
        report.errors = 1
        return report
    if not result.result_file:
        if result.exit_code not in (0, PYTEST_EXIT_NO_TESTS):
            report.errors = 1
            report.failures.append(TestFailure("(run)", "collection", "pytest did not produce a report", result.stdout[-4000:] + result.stderr[-4000:]))
        return report
    try:
        root = ET.fromstring(result.result_file)
    except ET.ParseError:
        report.errors = 1
        report.failures.append(TestFailure("(run)", "error", "Could not parse the JUnit report"))
        return report

    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        nodeid = _nodeid(classname, name)
        failure = case.find("failure")
        error = case.find("error")
        if failure is not None:
            report.failed += 1
            report.failures.append(TestFailure(nodeid, "failure", (failure.get("message") or "")[:2000], (failure.text or "")[-6000:]))
        elif error is not None:
            report.errors += 1
            report.failures.append(TestFailure(nodeid, "error", (error.get("message") or "")[:2000], (error.text or "")[-6000:]))
        elif case.find("skipped") is not None:
            report.skipped += 1
        else:
            report.passed += 1
    if result.exit_code not in (0, 1, PYTEST_EXIT_NO_TESTS) and not report.failures:
        report.errors += 1
        report.failures.append(TestFailure("(run)", "collection", f"pytest exited with code {result.exit_code}", result.stdout[-4000:]))
    return report


def _nodeid(classname: str, name: str) -> str:
    """Convert JUnit classname ("tests.test_api" or "tests.test_api.TestX") into a pytest node id."""
    if not classname:
        return name
    parts = classname.split(".")
    # Class names start with an uppercase letter by pytest convention.
    cls = []
    while parts and parts[-1][:1].isupper():
        cls.insert(0, parts.pop())
    path = "/".join(parts) + ".py"
    return "::".join([path, *cls, name])
