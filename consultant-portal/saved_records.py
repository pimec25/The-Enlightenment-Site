"""Private, consultant-owned record bundles using the caller's Supabase JWT."""
import json
import re
import time
import uuid
import base64
import hashlib
from pathlib import Path
from datetime import datetime, timezone
import httpx
from fastapi import HTTPException
import sso

BUCKET = "consultant-analysis-records"
DRAFTS = {}
MAX_DRAFT_BYTES = 100 * 1024 * 1024


def identity(request):
    stored = sso.SESSIONS.get(request.session.get("sso_id", ""))
    if not stored or stored[2] <= time.time():
        raise HTTPException(403, "To save or reopen private records, sign in through the Consultant dashboard and select Open Analysis agent.")
    return stored[0], stored[1]


def stage(request, record):
    now = time.time()
    for key in list(DRAFTS):
        if DRAFTS[key]["expires"] <= now:
            DRAFTS.pop(key, None)
    record.update({"version": 1, "created_at": datetime.now(timezone.utc).isoformat()})
    size = len(json.dumps(record).encode())
    if size + sum(v["size"] for v in DRAFTS.values()) > MAX_DRAFT_BYTES:
        raise HTTPException(503, "Temporary upload space is busy. Please retry shortly.")
    draft = uuid.uuid4().hex
    owner = sso.SESSIONS.get(request.session.get("sso_id", ""), (None, None))[1]
    slug = re.sub(r"[^a-zA-Z0-9._-]", "_", record.get("filename", "PIMEC-case"))[:100]
    DRAFTS[draft] = {"record": record, "owner": owner, "expires": now + 1800, "size": size, "key": uuid.uuid4().hex + "--" + slug + ".json"}
    request.session["draft_id"] = draft
    return draft


async def call(token, method, path, **kwargs):
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            response = await client.request(method, sso.CONFIG["url"] + "/storage/v1/" + path,
                headers={"apikey": sso.CONFIG["public_key"], "Authorization": "Bearer " + token, "Content-Type": "application/json"}, **kwargs)
    except httpx.HTTPError:
        raise HTTPException(503, "Record storage is temporarily unavailable. Your unsaved record remains on this page; retry Save records.") from None
    return response


def key_path(subject, key):
    if not re.fullmatch(r"[a-f0-9]{32}--[a-zA-Z0-9._-]{1,100}\.json", key):
        raise HTTPException(404, "Record not found")
    return f"{BUCKET}/{subject}/{key}"


async def save_draft(request, draft_id=None):
    token, subject = identity(request)
    entry = DRAFTS.get(draft_id or request.session.get("draft_id", ""))
    if not entry or entry["expires"] <= time.time():
        raise HTTPException(410, "The temporary upload expired. Upload the file again, then save it.")
    if entry["owner"] != subject:
        raise HTTPException(403, "This upload belongs to a different sign-in. Upload it again from your dashboard session.")
    record = dict(entry['record'])
    original_path = record.pop('_original_path', None)
    if original_path:
        parts = []
        size = 0
        with Path(original_path).open('rb') as source:
            while data := source.read(4 * 1024 * 1024):
                digest = hashlib.sha256(data).hexdigest()
                name = f"{entry['key']}.{len(parts):04d}.json"
                path = f"{BUCKET}/{subject}/parts/{name}"
                payload = {'data': base64.b64encode(data).decode(), 'sha256': digest}
                response = await call(token, 'POST', 'object/' + path, content=json.dumps(payload).encode())
                if response.status_code not in (200, 201):
                    if response.status_code in (400, 409) and 'Duplicate' in response.text:
                        existing = await call(token, 'GET', 'object/authenticated/' + path)
                        if existing.status_code != 200 or existing.json() != payload:
                            raise HTTPException(503, 'Saved file piece did not verify. Retry Save records.')
                    else:
                        raise HTTPException(503, 'File pieces were not fully saved. Retry Save records.')
                parts.append({'name': name, 'sha256': digest, 'size': len(data)})
                size += len(data)
        record.update(original_parts=parts, original_size=size)
    encoded = json.dumps(record).encode()
    if len(encoded) > 45 * 1024 * 1024:
        raise HTTPException(413, 'Calculated report is too large to save. The original workbook remains on your computer.')
    response = await call(token, "POST", "object/" + key_path(subject, entry["key"]), content=encoded)
    if response.status_code not in (200, 201):
        # A completed upload may have lost its response; an identical immutable key is safe to retry.
        if response.status_code in (400, 409) and "Duplicate" in response.text:
            existing = await read(request, entry["key"])
            if existing != record:
                raise HTTPException(409, "A different saved record uses this ID. Upload again to create a new record.")
        else:
            raise HTTPException(503, "The record was not saved. Keep this page open and retry, or reopen the agent from the dashboard.")
    return entry["key"]


async def original_parts(request, key, record):
    token, subject = identity(request)
    parts = record.get('original_parts')
    if not isinstance(parts, list) or not 1 <= len(parts) <= 25:
        raise HTTPException(422, 'Saved original manifest is invalid.')
    total = 0
    for index, part in enumerate(parts):
        if part.get('name') != f'{key}.{index:04d}.json' or not 0 < part.get('size', 0) <= 4 * 1024 * 1024:
            raise HTTPException(422, 'Saved original manifest is invalid.')
        total += part['size']
    if total != record.get('original_size'):
        raise HTTPException(422, 'Saved original size is invalid.')
    async def stream():
        for part in parts:
            response = await call(token, 'GET', f"object/authenticated/{BUCKET}/{subject}/parts/{part['name']}")
            if response.status_code != 200:
                raise RuntimeError('Saved file piece unavailable. Retry the download.')
            data = base64.b64decode(response.json()['data'], validate=True)
            if len(data) != part['size'] or hashlib.sha256(data).hexdigest() != part['sha256']:
                raise RuntimeError('Saved file piece failed verification.')
            yield data
    return stream()


async def listing(request, offset=0):
    token, subject = identity(request)
    response = await call(token, "POST", f"object/list/{BUCKET}", json={"prefix": subject + "/", "limit": 50, "offset": offset, "sortBy": {"column": "created_at", "order": "desc"}})
    if response.status_code != 200:
        raise HTTPException(503, "Saved records could not be loaded. Please retry.")
    return [{"key": r["name"], "name": r["name"].split("--", 1)[-1][:-5], "created_at": r.get("created_at", "")}
            for r in response.json() if r.get("id") and re.fullmatch(r"[a-f0-9]{32}--[a-zA-Z0-9._-]{1,100}\.json", r.get("name", ""))]


async def read(request, key):
    token, subject = identity(request)
    response = await call(token, "GET", "object/authenticated/" + key_path(subject, key))
    if response.status_code in (400, 403, 404):
        raise HTTPException(404, "Record not found or access denied")
    if response.status_code != 200:
        raise HTTPException(503, "Saved record could not be opened. Please retry.")
    try:
        record = response.json()
        if not isinstance(record, dict) or record.get("version") != 1 or record.get("kind") not in ("upload", "case"):
            raise ValueError()
        return record
    except ValueError:
        raise HTTPException(422, "Saved record format is invalid.") from None
