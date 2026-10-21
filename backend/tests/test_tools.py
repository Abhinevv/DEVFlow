import os

import pytest

from app.agents.schemas import Plan
from app.config import get_settings
from app.llm.base import strict_json_schema
from app.llm.mock_client import PLAN
from app.tools.analysis import security_scan
from app.tools.executor import LocalExecutor
from app.tools.testing import _nodeid, run_pytest
from app.tools.workspace import Workspace

PYTEST_INI = "[pytest]\ntestpaths = tests\npythonpath = .\n"


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws")
    w.ensure()
    w.write("pytest.ini", PYTEST_INI)
    w.write("app/__init__.py", "")
    w.write("app/calc.py", "def add(a, b):\n    return a + b\n")
    return w


@pytest.fixture
def executor():
    return LocalExecutor(get_settings())


def test_pytest_pass_and_fail_are_parsed(ws, executor):
    ws.write("tests/test_calc.py", "from app.calc import add\n\ndef test_ok():\n    assert add(1, 2) == 3\n\n"
                                   "def test_bad():\n    assert add(1, 1) == 3\n")
    report = run_pytest(executor, ws)
    assert (report.passed, report.failed) == (1, 1)
    assert not report.success
    assert report.failing_nodeids() == ["tests/test_calc.py::test_bad"]

    rerun = run_pytest(executor, ws, selectors=report.failing_nodeids())
    assert rerun.total == 1 and rerun.failed == 1


def test_syntax_errors_fail_before_execution(ws, executor):
    ws.write("app/broken.py", "def oops(:\n")
    report = run_pytest(executor, ws)
    assert not report.syntax_ok and not report.success
    assert report.failures[0].nodeid.startswith("app/broken.py")


def test_no_tests_is_reported(ws, executor):
    report = run_pytest(executor, ws)
    assert report.no_tests and report.success and report.total == 0


def test_timeout_is_enforced(ws, tmp_path):
    settings = get_settings().model_copy(update={"exec_timeout_seconds": 3})
    ws.write("tests/test_slow.py", "import time\n\ndef test_slow():\n    time.sleep(30)\n")
    report = run_pytest(LocalExecutor(settings), ws)
    assert report.timed_out and not report.success


def test_generated_code_cannot_see_secrets(ws, executor, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    ws.write("tests/test_env.py", "import os\n\ndef test_env():\n    assert 'ANTHROPIC_API_KEY' not in os.environ\n"
                                  "    assert not any(k.startswith('DEVFLOW') for k in os.environ)\n")
    assert run_pytest(executor, ws).success


def test_tests_run_on_a_copy(ws, executor):
    ws.write("tests/test_write.py", "from pathlib import Path\n\ndef test_write():\n    Path('app/calc.py').write_text('tampered')\n")
    assert run_pytest(executor, ws).success
    assert "tampered" not in ws.read("app/calc.py")


def test_nodeid_conversion():
    assert _nodeid("tests.test_api", "test_x") == "tests/test_api.py::test_x"
    assert _nodeid("tests.sub.test_api.TestThing", "test_y") == "tests/sub/test_api.py::TestThing::test_y"


def test_security_scan_detects_common_issues(ws):
    ws.write("app/bad.py", "import os, subprocess, pickle\nAPI_KEY = 'abcdefgh12345'\n"
                           "def f(x, cur):\n    eval(x)\n    os.system(x)\n    subprocess.run(x, shell=True)\n"
                           "    pickle.loads(x)\n    cur.execute(f'SELECT * FROM t WHERE id={x}')\n")
    codes = {i.code for i in security_scan(ws)}
    assert {"hardcoded-secret", "dynamic-exec", "shell-exec", "shell-true", "unsafe-deserialization", "sql-injection"} <= codes


def test_strict_schema_is_closed_and_fully_required():
    schema = strict_json_schema(Plan)
    assert "$defs" not in str(schema) and "$ref" not in str(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def test_plan_validation_rejects_bad_graphs():
    Plan.model_validate(PLAN)
    cyclic = {**PLAN, "tasks": [{**PLAN["tasks"][0], "depends_on": ["T2"]}, {**PLAN["tasks"][1], "depends_on": ["T1"]}]}
    with pytest.raises(ValueError, match="cycle"):
        Plan.model_validate(cyclic)
    unknown = {**PLAN, "tasks": [{**PLAN["tasks"][0], "depends_on": ["T9"]}]}
    with pytest.raises(ValueError, match="unknown task"):
        Plan.model_validate(unknown)


@pytest.mark.skipif(os.name != "nt", reason="Windows reserved names")
def test_windows_reserved_names_rejected(ws):
    from app.tools.workspace import PathViolation

    with pytest.raises(PathViolation):
        ws.validate("aux.py")
