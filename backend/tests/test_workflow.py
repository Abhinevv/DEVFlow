"""End-to-end workflow tests through the HTTP API, using the deterministic mock provider."""

import io
import os
import time
import zipfile
from pathlib import Path

from tests.conftest import create_project, plan_and_approve, wait_for

DONE = {"completed", "partial", "failed", "cancelled", "blocked"}


def test_full_workflow_detects_failure_repairs_and_delivers(client, auth):
    project = create_project(client, auth)
    pid = project["id"]
    plan_and_approve(client, auth, pid)
    project = wait_for(client, auth, pid, DONE)
    assert project["status"] == "completed", project["status_reason"]

    tasks = client.get(f"/api/projects/{pid}/tasks", headers=auth).json()
    assert [t["status"] for t in tasks] == ["completed"] * 3
    assert {t["key"]: t["debug_attempts"] for t in tasks} == {"T1": 0, "T2": 1, "T3": 0}

    runs = client.get(f"/api/projects/{pid}/tests", headers=auth).json()
    kinds = [(r["kind"], r["success"]) for r in runs]
    # T2 fails, debugger fixes it, failing test re-run first, then full regression suite.
    assert ("task", False) in kinds
    assert kinds.index(("retest", True)) < kinds.index(("regression", True))
    assert kinds[-1] == ("final", True)
    assert runs[-1]["passed"] >= 12

    findings = client.get(f"/api/projects/{pid}/findings", headers=auth).json()
    validation = [f for f in findings if "title" in f["description"]]
    assert validation and validation[0]["status"] == "resolved"

    roles = [r["role"] for r in client.get(f"/api/projects/{pid}/agent-runs", headers=auth).json()]
    assert roles == ["planner", "developer", "developer", "debugger", "developer", "reviewer", "developer", "reviewer"]

    metrics = project["metrics"]
    assert metrics["retry_count"] == 1 and metrics["recovery_rate"] == 1.0 and metrics["test_pass_rate"] == 1.0
    assert metrics["safety_violations"] == 0 and metrics["human_interventions"] == 0

    files = {f["path"] for f in client.get(f"/api/projects/{pid}/files", headers=auth).json()}
    assert {"app/main.py", "tests/test_validation.py", "README.md"} <= files
    commits = client.get(f"/api/projects/{pid}/commits", headers=auth).json()
    assert len(commits) >= 6
    fix_commit = next(c for c in commits if c["message"].startswith("T2 fix:"))
    diff = client.get(f"/api/projects/{pid}/commits/{fix_commit['sha']}", headers=auth).text
    assert "+    note = _get_or_404(db, note_id)" in diff

    delivery = client.get(f"/api/projects/{pid}/delivery", headers=auth).json()
    assert delivery["package"] and "Final test results" in delivery["markdown"]
    package = client.get(f"/api/projects/{pid}/package", headers=auth)
    names = zipfile.ZipFile(io.BytesIO(package.content)).namelist()
    assert {"DEVFLOW_REPORT.md", "devflow_manifest.json", "source/app/main.py"} <= set(names)

    events = {e["type"] for e in client.get(f"/api/projects/{pid}/events", headers=auth).json()}
    assert {"plan_ready", "plan_approved", "tests_failed", "debug_attempt", "task_completed", "review_completed",
            "revision_started", "final_validation", "delivered"} <= events


def test_retry_exhaustion_blocks_then_cancel(client, auth):
    pid = create_project(client, auth, constraints={"mock_scenario": "unfixable", "max_debug_attempts": 2})["id"]
    plan_and_approve(client, auth, pid)
    project = wait_for(client, auth, pid, DONE)
    assert project["status"] == "blocked"
    assert "T2 still failing after 2 debug attempt(s)" in project["status_reason"]
    tasks = {t["key"]: t["status"] for t in client.get(f"/api/projects/{pid}/tasks", headers=auth).json()}
    assert tasks == {"T1": "completed", "T2": "failed", "T3": "pending"}

    r = client.post(f"/api/projects/{pid}/cancel", json={"comment": "giving up"}, headers=auth)
    assert r.json()["status"] == "cancelled"


def test_path_traversal_is_rejected_and_recorded(client, auth):
    pid = create_project(client, auth, constraints={"mock_scenario": "unsafe"})["id"]
    plan_and_approve(client, auth, pid)
    project = wait_for(client, auth, pid, DONE)
    assert project["metrics"]["safety_violations"] == 1
    artifacts = client.get(f"/api/projects/{pid}/artifacts", headers=auth).json()
    assert any(a["action"] == "rejected" and a["path"] == "../outside_workspace.py" for a in artifacts)
    workspace_parent = Path(os.environ["DEVFLOW_DATA_DIR"]) / "workspaces"
    assert not (workspace_parent / "outside_workspace.py").exists()


def test_single_agent_baseline_has_no_recovery(client, auth):
    pid = create_project(client, auth, mode="single_agent")["id"]
    assert client.post(f"/api/projects/{pid}/plan", headers=auth).status_code == 409
    assert client.post(f"/api/projects/{pid}/start", headers=auth).status_code == 202
    project = wait_for(client, auth, pid, DONE)
    assert project["status"] == "failed"
    roles = [r["role"] for r in client.get(f"/api/projects/{pid}/agent-runs", headers=auth).json()]
    assert roles == ["single_agent"]


def test_pause_and_resume_skip_completed_tasks(client, auth, monkeypatch):
    monkeypatch.setenv("DEVFLOW_MOCK_DELAY", "0.5")
    pid = create_project(client, auth, constraints={"mock_scenario": "clean"})["id"]
    plan_and_approve(client, auth, pid)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        tasks = client.get(f"/api/projects/{pid}/tasks", headers=auth).json()
        if any(t["status"] == "completed" for t in tasks):
            break
        time.sleep(0.05)
    assert client.post(f"/api/projects/{pid}/pause", headers=auth).status_code == 200
    project = wait_for(client, auth, pid, DONE | {"paused"})
    assert project["status"] == "paused"
    completed_before = {t["key"] for t in client.get(f"/api/projects/{pid}/tasks", headers=auth).json() if t["status"] == "completed"}
    assert completed_before

    monkeypatch.setenv("DEVFLOW_MOCK_DELAY", "0")
    assert client.post(f"/api/projects/{pid}/resume", json={}, headers=auth).status_code == 202
    project = wait_for(client, auth, pid, DONE)
    assert project["status"] == "completed", project["status_reason"]
    runs = client.get(f"/api/projects/{pid}/agent-runs", headers=auth).json()
    tasks = {t["id"]: t["key"] for t in client.get(f"/api/projects/{pid}/tasks", headers=auth).json()}
    developed = [tasks.get(r["task_id"]) for r in runs if r["role"] == "developer"]
    for key in completed_before:
        assert developed.count(key) == 1, f"{key} was re-implemented after resume"
    assert project["metrics"]["human_interventions"] == 2


def test_plan_revision_and_rejection(client, auth):
    pid = create_project(client, auth)["id"]
    client.post(f"/api/projects/{pid}/plan", headers=auth)
    wait_for(client, auth, pid, {"awaiting_approval"})
    assert client.post(f"/api/projects/{pid}/plan/revise", json={"feedback": "Add pagination"}, headers=auth).status_code == 202
    project = wait_for(client, auth, pid, {"awaiting_approval"})
    assert project["plan_version"] == 2
    assert any("pagination" in a for a in project["plan"]["assumptions"])
    r = client.post(f"/api/projects/{pid}/plan/reject", json={"comment": "not now"}, headers=auth)
    assert r.json()["status"] == "plan_rejected"
    assert client.post(f"/api/projects/{pid}/plan/approve", json={}, headers=auth).status_code == 409
