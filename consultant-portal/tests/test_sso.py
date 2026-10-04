import asyncio
import base64
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app
import sso

HEADERS = {"Origin": "https://www.pimecineng.com", "X-Pimec-Dashboard": "1", "Authorization": "Bearer test-token"}


def fake_supabase(monkeypatch, role="consultant", auth_status=200):
    def respond(request):
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path == "/auth/v1/user":
            return httpx.Response(auth_status, json={"id": "user-1", "email_confirmed_at": "2026-01-01", "user_metadata": {"role": "consultant"}})
        assert request.url.params["id"] == "eq.user-1"
        return httpx.Response(200, json=[{"id": "user-1", "role": role}])
    client = httpx.AsyncClient
    monkeypatch.setattr(sso.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))


def test_sso_verified_consultant_no_token_in_cookie_and_logout(monkeypatch):
    fake_supabase(monkeypatch)
    client = TestClient(app.app)
    result = client.post("/consultant/sso", headers=HEADERS)
    assert result.status_code == 200
    session = json.loads(base64.b64decode(client.cookies["session"].split(".")[0]))
    assert "test-token" not in json.dumps(session)
    assert client.get("/consultant").status_code == 200
    key = session["sso_id"]
    assert client.post("/consultant/sso/logout", headers=HEADERS).json() == {"authenticated": False}
    assert key not in sso.SESSIONS
    assert client.get("/consultant/report.json").status_code == 401


@pytest.mark.parametrize("role", ["client", "admin", "", None])
def test_sso_rejects_non_consultants_even_with_metadata_claim(monkeypatch, role):
    fake_supabase(monkeypatch, role)
    assert TestClient(app.app).post("/consultant/sso", headers=HEADERS).status_code == 403


def test_sso_rejects_invalid_and_expired_tokens(monkeypatch):
    fake_supabase(monkeypatch, auth_status=401)
    assert TestClient(app.app).post("/consultant/sso", headers=HEADERS).status_code == 401


def test_sso_rejects_cross_site_and_missing_credentials():
    client = TestClient(app.app)
    for path in ["/consultant/sso", "/consultant/sso/logout"]:
        assert client.post(path, headers={**HEADERS, "Origin": "https://attacker.example"}).status_code == 403
        assert client.post(path).status_code == 403
    assert client.post("/consultant/sso", headers={k: v for k, v in HEADERS.items() if k != "Authorization"}).status_code == 401


def test_sso_rechecks_role_before_report_access(monkeypatch):
    async def permit(token): return "user-1"
    monkeypatch.setattr(sso, "verify_consultant", permit)
    client = TestClient(app.app)
    assert client.post("/consultant/sso", headers=HEADERS).status_code == 200
    async def revoked(token): raise HTTPException(403, "Consultant access is required.")
    monkeypatch.setattr(sso, "verify_consultant", revoked)
    assert client.get("/consultant/report.json").status_code == 403
    assert client.get("/consultant").status_code == 401


def test_sso_expired_or_lost_server_session_fails_closed():
    key = sso.create_session("test-token", "user-1")
    sso.SESSIONS[key] = ("test-token", "user-1", time.time() - 1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(sso.validate_session(key))
    assert exc.value.status_code == 401


def test_sso_network_failure_fails_closed(monkeypatch):
    def fail(request): raise httpx.ConnectError("offline")
    client = httpx.AsyncClient
    monkeypatch.setattr(sso.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(fail), **kwargs))
    assert TestClient(app.app).post("/consultant/sso", headers=HEADERS).status_code == 503


def test_outage_keeps_session_but_denies_request(monkeypatch):
    key = sso.create_session('test-token', 'user-1')
    async def unavailable(token):
        raise HTTPException(503, 'Try again')
    monkeypatch.setattr(sso, 'verify_consultant', unavailable)
    with pytest.raises(HTTPException) as error:
        asyncio.run(sso.validate_session(key))
    assert error.value.status_code == 503
    assert key in sso.SESSIONS
    async def permitted(token): return 'user-1'
    monkeypatch.setattr(sso, 'verify_consultant', permitted)
    asyncio.run(sso.validate_session(key))


def test_expired_browser_session_recovers_library_and_save(monkeypatch):
    fake_supabase(monkeypatch)
    client = TestClient(app.app)
    client.post('/consultant/sso', headers=HEADERS)
    session = json.loads(base64.b64decode(client.cookies['session'].split('.')[0]))
    sso.SESSIONS.pop(session['sso_id'])
    html = {'Accept': 'text/html'}
    library = client.get('/consultant/records', headers=html)
    assert library.status_code == 401
    assert 'Reconnect to continue' in library.text
    assert 'href="/consultant/records">Continue here' in library.text
    assert 'target="_blank"' in library.text
    # API clients must still receive JSON, never a login page masquerading as data.
    assert client.get('/consultant/records').json()['detail'].startswith('Return to the dashboard')
    draft = 'a' * 32
    response = client.post('/consultant/records/save', headers=html,
        data={'csrf_token': session['csrf'], 'draft_id': draft})
    assert response.status_code == 401
    assert f'href="/consultant/drafts/{draft}">Continue here' in response.text
    response = client.post('/consultant/large/test-job/save', headers=html)
    assert response.status_code == 401
    assert 'href="/consultant/large/test-job">Continue here' in response.text


def test_draft_reopens_after_reauthentication_only_for_owner(monkeypatch):
    import saved_records
    fake_supabase(monkeypatch)
    client = TestClient(app.app)
    client.post('/consultant/sso', headers=HEADERS)
    draft = 'b' * 32
    saved_records.DRAFTS[draft] = {'owner': 'user-1', 'expires': time.time()+60,
        'record': {'kind': 'upload', 'document': None}}
    client.post('/consultant/sso', headers=HEADERS)
    response = client.get('/consultant/drafts/' + draft)
    assert response.status_code == 200
    saved_records.DRAFTS[draft]['owner'] = 'someone-else'
    assert client.get('/consultant/drafts/' + draft).status_code == 404
    saved_records.DRAFTS.pop(draft)
