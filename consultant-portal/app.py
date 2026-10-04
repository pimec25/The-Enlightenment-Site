"""Consultant-only web portal for P&L and sales-detail analysis."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
import base64
from urllib.parse import quote
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from analyze_sales_periods import analyze
from method_analysis import analyze_case, validate_case
from method_schema import SCHEMA
from office_documents import extract_office, checked_zip
import saved_records
import sso

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env.local")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
RESULTS: dict[str, dict] = {}


def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


async def require_consultant(request: Request) -> None:
    if request.session.get("sso_id"):
        await sso.validate_session(request.session["sso_id"])
        return
    if not request.session.get("consultant"):
        raise HTTPException(status_code=401, detail="Consultant sign-in required")


def csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    if not token or not request.session.get("csrf") or not hmac.compare_digest(request.session.get("csrf", ""), token):
        raise HTTPException(status_code=403, detail="Invalid form token")


app = FastAPI(title="Consultant P&L Analysis Portal", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("PORTAL_SESSION_SECRET", secrets.token_urlsafe(48)),
                   https_only=os.environ.get("PORTAL_HTTPS_ONLY", "0") == "1", same_site="strict")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.exception_handler(HTTPException)
async def portal_error(request: Request, exc: HTTPException):
    path = request.url.path
    if exc.status_code == 401 and path.startswith('/consultant') and not path.startswith('/consultant/sso') and 'text/html' in request.headers.get('accept', ''):
        retry_url = path if request.method == 'GET' else '/consultant/records'
        if request.method == 'POST' and path.startswith('/consultant/large/') and path.endswith('/save'):
            retry_url = path.removesuffix('/save')
        elif request.method == 'POST' and path == '/consultant/records/save':
            form = await request.form()
            draft = str(form.get('draft_id', ''))
            if len(draft) == 32 and all(c in '0123456789abcdef' for c in draft):
                retry_url = '/consultant/drafts/' + draft
        return templates.TemplateResponse(request, 'session_recovery.html', {'retry_url': retry_url}, status_code=401)
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get('/consultant/drafts/{draft_id}', response_class=HTMLResponse)
async def reopen_draft(request: Request, draft_id: str):
    await require_consultant(request)
    import time
    _, subject = saved_records.identity(request)
    entry = saved_records.DRAFTS.get(draft_id)
    if not entry or entry['owner'] != subject:
        raise HTTPException(404, 'Draft not found for this Consultant.')
    if entry['expires'] <= time.time():
        raise HTTPException(410, 'The unsaved analysis expired. Upload the file again. Previously saved records remain available.')
    request.session['draft_id'] = draft_id
    record = entry['record']
    if record.get('report'):
        report_id = uuid.uuid4().hex
        RESULTS[report_id] = record['report']
        request.session['report_id'] = report_id
        return templates.TemplateResponse(request, 'results.html', {'report': record['report'], 'csrf': csrf(request)})
    return templates.TemplateResponse(request, 'document.html', {'document': record.get('document'), 'error': None, 'csrf': csrf(request)})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"
    return response


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/consultant", status_code=303)


@app.get("/consultant", response_class=HTMLResponse)
async def home(request: Request):
    if not request.session.get("consultant"):
        return RedirectResponse("/consultant/login", status_code=303)
    await require_consultant(request)
    from datetime import date
    return templates.TemplateResponse(request, "upload.html", {"csrf": csrf(request), "today": date.today().isoformat(), "max_upload_bytes": MAX_UPLOAD_BYTES})


def upload_too_large(request: Request):
    return templates.TemplateResponse(request, "upload_error.html", {
        "limit_mb": MAX_UPLOAD_BYTES / (1024 * 1024)
    }, status_code=413)


@app.get("/consultant/upload.js")
async def upload_script(request: Request):
    await require_consultant(request)
    return FileResponse(BASE_DIR / "static" / "upload.js", media_type="application/javascript")


@app.get("/consultant/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None, "csrf": csrf(request)})


@app.get("/consultant/method", response_class=HTMLResponse)
async def method_workspace(request: Request):
    await require_consultant(request)
    return templates.TemplateResponse(request, "method.html", {"csrf": csrf(request)})


@app.get("/consultant/method/schema")
async def method_schema(request: Request):
    await require_consultant(request)
    return SCHEMA


@app.get("/consultant/method.js")
async def method_script(request: Request):
    await require_consultant(request)
    return FileResponse(BASE_DIR / "static" / "method.js", media_type="application/javascript")


@app.get("/consultant/method.css")
async def method_style(request: Request):
    await require_consultant(request)
    return FileResponse(BASE_DIR / "static" / "method.css", media_type="text/css")


@app.post("/consultant/method/review")
async def method_review(request: Request):
    await require_consultant(request)
    token = request.headers.get("x-csrf-token", "")
    if not token or not request.session.get("csrf"):
        raise HTTPException(403, "Invalid form token")
    verify_csrf(request, token)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2 * 1024 * 1024:
            raise HTTPException(413, "Case exceeds the 2 MB limit")
    try:
        return analyze_case(json.loads(body))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/consultant/documents", response_class=HTMLResponse)
async def document_upload(request: Request, document: UploadFile = File(...), csrf_token: str = Form(...), as_of: str = Form(...)):
    await require_consultant(request)
    verify_csrf(request, csrf_token)
    filename = (document.filename or "upload").replace("\\", "/").split("/")[-1][:160]
    content = await document.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return upload_too_large(request)
    try:
        from datetime import date
        cutoff = date.fromisoformat(as_of)
        extracted = extract_office(content, filename)
    except (ValueError, TypeError) as exc:
        return templates.TemplateResponse(request, "document.html", {"error": str(exc), "csrf": csrf(request), "document": None}, status_code=422)
    report = None
    if filename.lower().endswith(".xlsx"):
        # Generic Excel files are accepted as documents; compatible sales files also receive the existing analysis.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as handle:
                handle.write(content)
                temp_path = Path(handle.name)
            report = analyze(temp_path, cutoff)
            report.update({"source": "Uploaded workbook", "uploaded_filename": filename})
            report_id = uuid.uuid4().hex
            RESULTS[report_id] = report
            request.session["report_id"] = report_id
        except (ValueError, TypeError, ZeroDivisionError):
            extracted["analysis_note"] = "Workbook accepted for review and saving. Automatic sales analysis requires the sales-detail columns and comparable periods. Review the extracted sheet values below."
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
    saved_records.stage(request, {"kind": "upload", "filename": filename, "original": base64.b64encode(content).decode(), "document": extracted, "report": report})
    if report:
        return templates.TemplateResponse(request, "results.html", {"report": report, "csrf": csrf(request)})
    return templates.TemplateResponse(request, "document.html", {"document": extracted, "error": None, "csrf": csrf(request)})


@app.post("/consultant/records/save")
async def save_uploaded_record(request: Request, csrf_token: str = Form(...), draft_id: str = Form(...)):
    await require_consultant(request)
    verify_csrf(request, csrf_token)
    key = await saved_records.save_draft(request, draft_id)
    return RedirectResponse("/consultant/records/" + key + "?saved=1", status_code=303)


@app.get("/consultant/records", response_class=HTMLResponse)
async def records_library(request: Request, offset: int = 0):
    await require_consultant(request)
    offset = max(0, min(offset, 100000))
    records = await saved_records.listing(request, offset)
    return templates.TemplateResponse(request, "records.html", {"records": records, "offset": offset})


@app.get("/consultant/records/{key}", response_class=HTMLResponse)
async def open_record(request: Request, key: str, saved: bool = False):
    await require_consultant(request)
    record = await saved_records.read(request, key)
    return templates.TemplateResponse(request, "record.html", {"record": record, "key": key, "saved": saved})


@app.get("/consultant/records/{key}/original")
async def original_record(request: Request, key: str):
    await require_consultant(request)
    record = await saved_records.read(request, key)
    if record["kind"] != "upload":
        return JSONResponse(record["case"], headers={"Content-Disposition": "attachment; filename=pimec-method-case.json"})
    if record.get('original_parts'):
        stream = await saved_records.original_parts(request, key, record)
        return StreamingResponse(stream, media_type='application/octet-stream', headers={
            'Content-Length': str(record['original_size']),
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(record['filename'], safe='')})
    try:
        content = base64.b64decode(record["original"], validate=True)
    except (ValueError, KeyError):
        raise HTTPException(422, "Saved original is invalid") from None
    return Response(content, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(record["filename"], safe="")})


@app.get("/consultant/records/{key}/review")
async def saved_review(request: Request, key: str):
    await require_consultant(request)
    record = await saved_records.read(request, key)
    return JSONResponse(record.get("report") or record.get("document") or analyze_case(record["case"]), headers={"Content-Disposition": "attachment; filename=saved-review.json"})


@app.get("/consultant/records/{key}/case")
async def saved_case(request: Request, key: str):
    await require_consultant(request)
    record = await saved_records.read(request, key)
    if record["kind"] != "case":
        raise HTTPException(422, "This record is a document, not a PIMEC case.")
    return validate_case(record["case"])


@app.post("/consultant/method/save")
async def save_method_case(request: Request):
    await require_consultant(request)
    saved_records.identity(request)
    token = request.headers.get("x-csrf-token", "")
    if not token or not request.session.get("csrf"):
        raise HTTPException(403, "Invalid form token")
    verify_csrf(request, token)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2 * 1024 * 1024:
            raise HTTPException(413, "Case exceeds the 2 MB limit")
    try:
        case = validate_case(json.loads(body))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    name = next((r["client"] for r in case["records"]["charter"] if r["client"]), "PIMEC-case")
    draft = saved_records.stage(request, {"kind": "case", "filename": name[:100], "case": case})
    key = await saved_records.save_draft(request, draft)
    return {"saved": True, "key": key}


@app.post("/consultant/sso")
async def dashboard_sign_in(request: Request):
    sso.check_origin(request)
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Dashboard sign-in required.")
    token = authorization[7:]
    subject = await sso.verify_consultant(token)
    sso.SESSIONS.pop(request.session.get("sso_id", ""), None)
    request.session.clear()
    request.session.update({"consultant": True, "sso_id": sso.create_session(token, subject),
                            "csrf": secrets.token_urlsafe(32)})
    return {"authenticated": True}


@app.post("/consultant/sso/logout")
async def dashboard_sign_out(request: Request):
    sso.check_origin(request)
    sso.SESSIONS.pop(request.session.get("sso_id", ""), None)
    request.session.clear()
    return {"authenticated": False}


@app.post("/consultant/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    expected_email = os.environ.get("CONSULTANT_EMAIL", "").casefold()
    password_hash = os.environ.get("CONSULTANT_PASSWORD_HASH", "")
    valid = expected_email and password_hash and hmac.compare_digest(email.strip().casefold(), expected_email) and verify_password(password, password_hash)
    if not valid:
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid Consultant credentials.", "csrf": csrf(request)}, status_code=401)
    request.session.clear()
    request.session["consultant"] = True
    request.session["csrf"] = secrets.token_urlsafe(32)
    return RedirectResponse("/consultant", status_code=303)


@app.post("/consultant/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    verify_csrf(request, csrf_token)
    sso.SESSIONS.pop(request.session.get("sso_id", ""), None)
    request.session.clear()
    return RedirectResponse("/consultant/login", status_code=303)


@app.post("/consultant/analyze", response_class=HTMLResponse)
async def analyze_upload(request: Request, workbook: UploadFile = File(...), csrf_token: str = Form(...), as_of: str = Form(...)):
    await require_consultant(request)
    verify_csrf(request, csrf_token)
    if not workbook.filename or Path(workbook.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Upload an .xlsx workbook")
    content = await workbook.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        return upload_too_large(request)
    try:
        with checked_zip(content):
            pass
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        from datetime import date
        cutoff = date.fromisoformat(as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid as-of date") from exc
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        report = analyze(temp_path, cutoff)
        report["source"] = "Uploaded workbook"
        report["uploaded_filename"] = Path(workbook.filename).name
        report_id = uuid.uuid4().hex
        RESULTS[report_id] = report
        request.session["report_id"] = report_id
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Workbook could not be analyzed: {exc}") from exc
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
    saved_records.stage(request, {"kind": "upload", "filename": Path(workbook.filename).name,
        "original": base64.b64encode(content).decode(), "document": None, "report": report})
    return templates.TemplateResponse(request, "results.html", {"report": report, "csrf": csrf(request)})


@app.get("/consultant/report.json")
async def download_report(request: Request):
    await require_consultant(request)
    report = RESULTS.get(request.session.get("report_id", ""))
    if not report:
        raise HTTPException(status_code=404, detail="No active report")
    return JSONResponse(report, headers={"Content-Disposition": "attachment; filename=pnl-analysis.json"})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash-password", action="store_true")
    args = parser.parse_args()
    if args.hash_password:
        import getpass
        print(hash_password(getpass.getpass("Consultant password: ")))


import large_workbooks
large_workbooks.install(app, require_consultant, verify_csrf, csrf, templates)
