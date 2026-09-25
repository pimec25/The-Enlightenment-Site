"""Consultant-only web portal for P&L and sales-detail analysis."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from analyze_sales_periods import analyze

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


def require_consultant(request: Request) -> None:
    if not request.session.get("consultant"):
        raise HTTPException(status_code=401, detail="Consultant sign-in required")


def csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    if not hmac.compare_digest(request.session.get("csrf", ""), token or ""):
        raise HTTPException(status_code=403, detail="Invalid form token")


app = FastAPI(title="Consultant P&L Analysis Portal", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("PORTAL_SESSION_SECRET", secrets.token_urlsafe(48)),
                   https_only=os.environ.get("PORTAL_HTTPS_ONLY", "0") == "1", same_site="strict")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
def home(request: Request):
    if not request.session.get("consultant"):
        return RedirectResponse("/consultant/login", status_code=303)
    from datetime import date
    return templates.TemplateResponse(request, "upload.html", {"csrf": csrf(request), "today": date.today().isoformat()})


@app.get("/consultant/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None, "csrf": csrf(request)})


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
def logout(request: Request, csrf_token: str = Form(...)):
    require_consultant(request)
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/consultant/login", status_code=303)


@app.post("/consultant/analyze", response_class=HTMLResponse)
async def analyze_upload(request: Request, workbook: UploadFile = File(...), csrf_token: str = Form(...), as_of: str = Form(...)):
    require_consultant(request)
    verify_csrf(request, csrf_token)
    if not workbook.filename or Path(workbook.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="Upload an .xlsx workbook")
    content = await workbook.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Workbook exceeds the 30 MB upload limit")
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
    return templates.TemplateResponse(request, "results.html", {"report": report, "csrf": csrf(request)})


@app.get("/consultant/report.json")
def download_report(request: Request):
    require_consultant(request)
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

