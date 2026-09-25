import os
import re

from fastapi.testclient import TestClient

import app


def test_health_and_private_redirect():
    client = TestClient(app.app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/consultant", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/consultant/login"


def test_login_and_security_headers():
    os.environ["CONSULTANT_EMAIL"] = "consultant@example.com"
    os.environ["CONSULTANT_PASSWORD_HASH"] = app.hash_password("test-pass", iterations=10_000)
    client = TestClient(app.app)
    page = client.get("/consultant/login")
    token = re.search(r'name="csrf_token" value="([^"]+)', page.text).group(1)
    response = client.post(
        "/consultant/login",
        data={"email": "consultant@example.com", "password": "test-pass", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Analyze sales and margin performance" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


