import pytest

from app.templates import unapproved_requirements
from app.tools.workspace import PathViolation, Workspace


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path / "ws", max_file_bytes=1000, max_files=5)
    w.ensure()
    return w


@pytest.mark.parametrize("path", [
    "../escape.py", "app/../../escape.py", "/etc/passwd", "C:/Windows/win.ini", "c:evil.py", "~/x.py",
    "app\\main.py", ".git/config", ".github/workflows/x.yml", "app/.env", "app/con.py", "nul.txt",
    "app/run.sh", "app/tool.exe", "", "a\x00b.py", "app/./main.py", "app//main.py", "app/", "a b.py",
])
def test_rejects_unsafe_paths(ws, path):
    with pytest.raises(PathViolation):
        ws.validate(path)


@pytest.mark.parametrize("path", ["app/main.py", "tests/test_api.py", "README.md", ".gitignore", "requirements.txt", "pytest.ini"])
def test_accepts_project_paths(ws, path):
    assert ws.validate(path) == path


def test_write_read_delete_roundtrip(ws):
    change = ws.write("app/main.py", "x = 1\n")
    assert change.action == "create"
    assert ws.write("app/main.py", "x = 2\n").action == "modify"
    assert ws.read("app/main.py") == "x = 2\n"
    ws.delete("app/main.py")
    assert not ws.exists("app/main.py")


def test_enforces_size_and_count_limits(ws):
    with pytest.raises(PathViolation):
        ws.write("big.txt", "x" * 1001)
    for i in range(5):
        ws.write(f"f{i}.txt", "x")
    with pytest.raises(PathViolation):
        ws.write("f5.txt", "x")


def test_writes_never_leave_root(ws, tmp_path):
    with pytest.raises(PathViolation):
        ws.write("../outside.py", "print(1)")
    assert not (tmp_path / "outside.py").exists()


def test_dependency_policy():
    approved = ["fastapi", "pydantic", "sqlalchemy", "pytest"]
    assert unapproved_requirements("fastapi>=0.110\nSQLAlchemy==2.0\n# comment\npydantic[email]\n", approved) == []
    bad = unapproved_requirements("requests\n-e git+https://x/y\n--index-url https://evil\n./local\nfastapi\n", approved)
    assert bad == ["requests", "-e git+https://x/y", "--index-url https://evil", "./local"]
