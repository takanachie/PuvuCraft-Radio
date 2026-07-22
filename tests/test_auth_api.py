from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import csrf_headers


def test_setup_requires_token_and_closes_after_success(client: TestClient, settings) -> None:
    assert client.get("/api/setup/status").json() == {"required": True}
    payload = {
        "token": "invalid-token-that-is-long-enough",
        "username": "admin",
        "email": "admin@example.com",
        "password": "secure-admin-password",
    }
    assert client.post("/api/setup", json=payload).status_code == 403

    payload["token"] = settings.paths.bootstrap_token_file.read_text(encoding="utf-8").strip()
    response = client.post("/api/setup", json=payload)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "admin"
    assert not settings.paths.bootstrap_token_file.exists()
    assert client.get("/api/setup/status").json() == {"required": False}
    assert client.post("/api/setup", json=payload).status_code == 409


def test_registration_approval_and_session_revocation(
    initialized_admin: TestClient,
) -> None:
    admin = initialized_admin
    listener = TestClient(admin.app)
    with listener:
        response = listener.post(
            "/api/auth/register",
            json={
                "username": "listener",
                "email": "listener@example.com",
                "password": "listener-password",
            },
        )
        assert response.status_code == 201
        user_id = response.json()["user"]["id"]
        assert response.json()["status"] == "pending_approval"
        assert (
            listener.post(
                "/api/auth/login",
                json={"username": "listener", "password": "listener-password"},
            ).status_code
            == 403
        )

        no_csrf = admin.patch(f"/api/admin/users/{user_id}", json={"status": "approved"})
        assert no_csrf.status_code == 403
        approved = admin.patch(
            f"/api/admin/users/{user_id}",
            json={"status": "approved"},
            headers=csrf_headers(admin),
        )
        assert approved.status_code == 200

        login = listener.post(
            "/api/auth/login",
            json={"username": "listener", "password": "listener-password"},
        )
        assert login.status_code == 200
        assert listener.get("/api/channels").status_code == 200
        assert listener.get("/api/admin/users").status_code == 403

        disabled = admin.patch(
            f"/api/admin/users/{user_id}",
            json={"status": "disabled"},
            headers=csrf_headers(admin),
        )
        assert disabled.status_code == 200
        assert listener.get("/api/auth/me").status_code == 401


def test_logout_requires_csrf_and_clears_session(initialized_admin: TestClient) -> None:
    client = initialized_admin
    assert client.post("/api/auth/logout").status_code == 403
    response = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
