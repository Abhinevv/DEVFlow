from tests.conftest import create_project


def test_requires_authentication(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects", headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_tampered_token_is_rejected(client, auth):
    token = auth["Authorization"].split()[1]
    payload, sig = token.split(".")
    forged = f"{payload[:-2]}AA.{sig}"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_projects_are_private_to_their_owner(client, auth):
    pid = create_project(client, auth)["id"]
    other = client.post("/api/auth/register", json={"email": "intruder@example.com", "password": "password123", "display_name": "X"})
    other_auth = {"Authorization": f"Bearer {other.json()['access_token']}"}
    for path in ("", "/tasks", "/files", "/events", "/delivery"):
        assert client.get(f"/api/projects/{pid}{path}", headers=other_auth).status_code == 404
    assert client.post(f"/api/projects/{pid}/plan", headers=other_auth).status_code == 404
    assert pid not in [p["id"] for p in client.get("/api/projects", headers=other_auth).json()]


def test_login_flow(client):
    client.post("/api/auth/register", json={"email": "login@example.com", "password": "password123", "display_name": "L"})
    assert client.post("/api/auth/login", json={"email": "login@example.com", "password": "wrong-password"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "LOGIN@example.com", "password": "password123"})
    assert r.status_code == 200 and r.json()["user"]["email"] == "login@example.com"
    assert client.post("/api/auth/register", json={"email": "login@example.com", "password": "password123", "display_name": "L"}).status_code == 409


def test_input_validation(client, auth):
    bad = [
        {"name": "x", "requirements": "too short"},
        {"name": "x", "requirements": "a" * 30, "constraints": {"run_shell": True}},
        {"name": "x", "requirements": "a" * 30, "constraints": {"max_cost_usd": -1}},
        {"name": "x", "requirements": "a" * 30, "template": "../../etc"},
    ]
    for body in bad:
        assert client.post("/api/projects", json=body, headers=auth).status_code == 422, body


def test_file_reads_are_confined_to_workspace(client, auth):
    from tests.conftest import plan_and_approve, wait_for

    pid = create_project(client, auth, constraints={"mock_scenario": "clean"})["id"]
    plan_and_approve(client, auth, pid)
    wait_for(client, auth, pid, {"completed", "partial", "failed", "blocked"})
    for path in ("../../secret_key", "..%2F..%2Fsecret_key", "C:/Windows/win.ini", ".git/config"):
        r = client.get(f"/api/projects/{pid}/files/content", params={"path": path}, headers=auth)
        assert r.status_code in (400, 404), (path, r.status_code)
    assert client.get(f"/api/projects/{pid}/files/content", params={"path": "app/main.py"}, headers=auth).status_code == 200
    assert client.get(f"/api/projects/{pid}/commits/not-a-sha", headers=auth).status_code == 404


def test_system_endpoints(client, auth):
    assert client.get("/api/health").json()["status"] == "ok"
    info = client.get("/api/system", headers=auth).json()
    assert info["llm_provider"] == "mock" and info["executor"] == "local" and info["isolated_execution"] is False
    templates = client.get("/api/templates", headers=auth).json()
    assert templates[0]["id"] == "fastapi_service"
    metrics = client.get("/api/metrics", headers=auth).json()
    assert set(metrics) == {"multi_agent", "single_agent"}
