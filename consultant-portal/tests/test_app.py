from app import hash_password, verify_password
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

import app


def test_password_hash_round_trip():
    encoded = hash_password("correct horse", iterations=10_000)
    assert verify_password("correct horse", encoded)
    assert not verify_password("wrong", encoded)


def test_invalid_password_hash_is_rejected():
    assert not verify_password("anything", "invalid")


def test_legacy_route_rejects_workbook_requiring_background_processing():
    source = Path(r"D:\RIE\Projects\Cost Savings\Sales 2025-2026.xlsx")
    if not source.exists():
        return
    os.environ["CONSULTANT_EMAIL"] = "qa@example.com"
    os.environ["CONSULTANT_PASSWORD_HASH"] = hash_password("test-pass", iterations=10_000)
    client = TestClient(app.app)
    page = client.get("/consultant/login")
    token = re.search(r'name="csrf_token" value="([^"]+)', page.text).group(1)
    signed_in = client.post("/consultant/login", data={"email": "qa@example.com", "password": "test-pass", "csrf_token": token}, follow_redirects=True)
    assert signed_in.status_code == 200
    token = re.search(r'name="csrf_token" value="([^"]+)', signed_in.text).group(1)
    with source.open("rb") as handle:
        result = client.post("/consultant/analyze", data={"csrf_token": token, "as_of": "2026-09-23"}, files={"workbook": (source.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    # This workbook expands beyond the synchronous route's limit. Excel uploads
    # from the current UI use /consultant/large instead (covered separately).
    assert result.status_code == 422
    assert "processing limit" in result.text
