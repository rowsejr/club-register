from fastapi.testclient import TestClient

from app.main import app


def _member_headers(client: TestClient, admin: dict, email: str, name: str = "Member") -> tuple[dict, int]:
    created = client.post("/members", headers=admin, json={"name": name, "email": email})
    assert created.status_code == 200
    magic = client.post("/auth/magic-link", json={"email": email})
    token = magic.json()["token"]
    exchange = TestClient(app).post("/auth/session", json={"token": token})
    return {"x-session-token": exchange.json()["session_token"]}, created.json()["id"]


def test_operator_pages_require_login() -> None:
    anon = TestClient(app, follow_redirects=False)
    for path in ("/app", "/app/members", "/app/contacts", "/app/events", "/app/finance", "/app/admin/users"):
        resp = anon.get(path)
        assert resp.status_code == 303 and resp.headers["location"].startswith("/login")
    assert anon.get("/").status_code == 200
    assert "Members:" not in anon.get("/").text
















