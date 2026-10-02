"""Validate dashboard sessions against the same Supabase project as the portal."""
import json
import secrets
import time
from pathlib import Path

import httpx
from fastapi import HTTPException

CONFIG = json.loads(Path(__file__).with_name("sso-public.json").read_text())
ORIGINS = {"https://www.pimecineng.com", "https://pimecineng.com"}
SESSIONS = {}
TTL = 3600


def check_origin(request):
    if request.headers.get("origin") not in ORIGINS or request.headers.get("x-pimec-dashboard") != "1":
        raise HTTPException(403, "Open the agent from your PIMEC dashboard.")


async def verify_consultant(token):
    if not token or len(token) > 12000:
        raise HTTPException(401, "Sign in to the dashboard again.")
    headers = {"apikey": CONFIG["public_key"], "Authorization": "Bearer " + token}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            result = await client.get(CONFIG["url"] + "/auth/v1/user", headers=headers)
            if result.status_code != 200:
                raise HTTPException(401, "Sign in to the dashboard again.")
            user = result.json()
            if not user.get("id") or not user.get("email_confirmed_at"):
                raise HTTPException(403, "A confirmed Consultant account is required.")
            result = await client.get(CONFIG["url"] + "/rest/v1/profiles", headers=headers,
                params={"id": "eq." + user["id"], "select": "id,role", "limit": "1"})
            if result.status_code != 200:
                raise HTTPException(503, "Consultant access could not be verified. Please retry.")
            rows = result.json()
            if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("id") != user["id"] or rows[0].get("role") != "consultant":
                raise HTTPException(403, "Consultant access is required.")
            return user["id"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Consultant access could not be verified. Please retry.") from None


def create_session(token, subject):
    now = time.time()
    for key in list(SESSIONS):
        if SESSIONS[key][2] <= now:
            SESSIONS.pop(key, None)
    key = secrets.token_urlsafe(32)
    SESSIONS[key] = (token, subject, now + TTL)
    return key


async def validate_session(key):
    stored = SESSIONS.get(key)
    if not stored or stored[2] <= time.time():
        SESSIONS.pop(key, None)
        raise HTTPException(401, "Return to the dashboard and open Analysis agent again.")
    try:
        subject = await verify_consultant(stored[0])
        if subject != stored[1]:
            raise HTTPException(401, "Your dashboard session has changed.")
    except HTTPException:
        SESSIONS.pop(key, None)
        raise

