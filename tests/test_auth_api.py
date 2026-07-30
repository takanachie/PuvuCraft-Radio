from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import AuditEvent, Channel, LoginSession, User

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
    with closing(listener):
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
        active_at = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)
        with admin.app.state.database.session_factory.begin() as db:
            user = db.get(User, user_id)
            login_session = db.scalar(
                select(LoginSession).where(LoginSession.user_id == user_id)
            )
            assert user is not None and login_session is not None
            user.last_login_at = active_at - timedelta(hours=1)
            login_session.last_seen_at = active_at
        listed_user = next(
            user
            for user in admin.get("/api/admin/users").json()
            if user["id"] == user_id
        )
        assert listed_user["last_active_at"] == "2026-07-30T01:02:03Z"
        assert listed_user["last_active_at"] != listed_user["last_login_at"]
        assert listener.get("/api/channels").status_code == 200
        assert listener.get("/api/admin/users").status_code == 403
        assert listener.get("/api/admin/listeners").status_code == 403
        with admin.app.state.database.session_factory.begin() as db:
            channel = db.get(Channel, 1)
            assert channel is not None and channel.playback_state is not None
            channel.playback_state.status = "live"
        assert listener.get(
            "/api/internal/stream-auth",
            headers={"X-Original-URI": "/hls/default/index.m3u8"},
        ).status_code == 204
        assert admin.app.state.listeners.count(1) == 1

        disabled = admin.patch(
            f"/api/admin/users/{user_id}",
            json={"status": "disabled"},
            headers=csrf_headers(admin),
        )
        assert disabled.status_code == 200
        assert admin.app.state.listeners.count(1) == 0
        assert admin.get("/api/admin/listeners").json() == []
        assert listener.get("/api/auth/me").status_code == 401


def test_admin_can_promote_approved_listener_and_revoke_session(
    initialized_admin: TestClient,
) -> None:
    admin = initialized_admin
    listener = TestClient(admin.app)
    with closing(listener):
        response = listener.post(
            "/api/auth/register",
            json={
                "username": "operator",
                "email": "operator@example.com",
                "password": "operator-password",
            },
        )
        assert response.status_code == 201
        user_id = response.json()["user"]["id"]

        pending_promotion = admin.patch(
            f"/api/admin/users/{user_id}/role",
            json={"role": "admin"},
            headers=csrf_headers(admin),
        )
        assert pending_promotion.status_code == 409
        assert pending_promotion.json()["code"] == "user_not_approved"

        approved = admin.patch(
            f"/api/admin/users/{user_id}",
            json={"status": "approved"},
            headers=csrf_headers(admin),
        )
        assert approved.status_code == 200
        assert approved.json()["user"]["role"] == "listener"

        login = listener.post(
            "/api/auth/login",
            json={"username": "operator", "password": "operator-password"},
        )
        assert login.status_code == 200
        assert listener.get("/api/admin/users").status_code == 403

        no_csrf = admin.patch(
            f"/api/admin/users/{user_id}/role",
            json={"role": "admin"},
        )
        assert no_csrf.status_code == 403

        promoted = admin.patch(
            f"/api/admin/users/{user_id}/role",
            json={"role": "admin"},
            headers=csrf_headers(admin),
        )
        assert promoted.status_code == 200
        assert promoted.json()["user"]["role"] == "admin"
        assert listener.get("/api/auth/me").status_code == 401

        relogin = listener.post(
            "/api/auth/login",
            json={"username": "operator", "password": "operator-password"},
        )
        assert relogin.status_code == 200
        assert relogin.json()["user"]["role"] == "admin"
        assert listener.get("/api/admin/users").status_code == 200

        with admin.app.state.database.session_factory() as db:
            event = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "user.role_changed",
                    AuditEvent.target_id == str(user_id),
                )
                .order_by(AuditEvent.id.desc())
            )
            assert event is not None
            assert event.details == {"from": "listener", "to": "admin"}


def test_admin_can_permanently_delete_user_but_not_self(
    initialized_admin: TestClient,
) -> None:
    admin = initialized_admin
    current_user = admin.get("/api/auth/me").json()["user"]
    self_delete = admin.delete(
        f"/api/admin/users/{current_user['id']}",
        headers=csrf_headers(admin),
    )
    assert self_delete.status_code == 409
    assert self_delete.json()["code"] == "cannot_delete_self"

    listener = TestClient(admin.app)
    with closing(listener):
        registration = listener.post(
            "/api/auth/register",
            json={
                "username": "removable",
                "email": "removable@example.com",
                "password": "removable-password",
            },
        )
        assert registration.status_code == 201
        user_id = registration.json()["user"]["id"]

        approved = admin.patch(
            f"/api/admin/users/{user_id}",
            json={"status": "approved"},
            headers=csrf_headers(admin),
        )
        assert approved.status_code == 200
        assert listener.post(
            "/api/auth/login",
            json={"username": "removable", "password": "removable-password"},
        ).status_code == 200

        no_csrf = admin.delete(f"/api/admin/users/{user_id}")
        assert no_csrf.status_code == 403

        deleted = admin.delete(
            f"/api/admin/users/{user_id}",
            headers=csrf_headers(admin),
        )
        assert deleted.status_code == 204
        assert listener.get("/api/auth/me").status_code == 401
        assert all(
            user["id"] != user_id
            for user in admin.get("/api/admin/users").json()
        )

        with admin.app.state.database.session_factory() as db:
            assert db.get(User, user_id) is None
            event = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "user.deleted",
                    AuditEvent.target_id == str(user_id),
                )
                .order_by(AuditEvent.id.desc())
            )
            assert event is not None
            assert event.details == {
                "username": "removable",
                "role": "listener",
                "status": "approved",
            }

        repeated_registration = listener.post(
            "/api/auth/register",
            json={
                "username": "removable",
                "email": "removable@example.com",
                "password": "removable-password",
            },
        )
        assert repeated_registration.status_code == 201
        assert repeated_registration.json()["status"] == "pending_approval"


def test_logout_requires_csrf_and_clears_session(initialized_admin: TestClient) -> None:
    client = initialized_admin
    assert client.post("/api/auth/logout").status_code == 403
    response = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
