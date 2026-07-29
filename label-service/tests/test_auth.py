import re

from werkzeug.security import generate_password_hash

import config
from app import create_app
from app import routes


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _app(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "ADMIN_PASSWORD_HASH", generate_password_hash("lab-secret"))
    routes._failed_logins.clear()
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_application_routes_redirect_to_login(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).test_client()

    response = client.get("/history")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


def test_successful_login_returns_to_requested_page(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).test_client()
    client.get("/history")
    login_page = client.get("/admin")

    response = client.post(
        "/admin/login",
        data={"csrf_token": _csrf(login_page.get_data(as_text=True)), "password": "lab-secret"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/history")
    assert client.get("/history").status_code == 200


def test_wrong_password_does_not_authenticate(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).test_client()
    login_page = client.get("/admin")

    client.post(
        "/admin/login",
        data={"csrf_token": _csrf(login_page.get_data(as_text=True)), "password": "wrong"},
    )

    assert client.get("/").status_code == 302
