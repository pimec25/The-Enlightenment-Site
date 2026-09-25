# PIMEC Consultant P&L Portal

Private FastAPI service for sales margin, loss row, and 80/20 analysis. It is designed to run separately from the public GitHub Pages site while remaining in the same repository.

## Production configuration

Set these environment variables in Render:

- `CONSULTANT_EMAIL`: authorized Consultant email address
- `CONSULTANT_PASSWORD_HASH`: PBKDF2 hash generated with `python app.py --hash-password`
- `PORTAL_SESSION_SECRET`: long random secret used to sign sessions
- `PORTAL_HTTPS_ONLY`: `1` in production

Do not commit passwords, API keys, workbooks, or analysis results. Uploaded files are written to a temporary file for analysis and deleted immediately afterward. Calculated results remain only in service memory and are cleared when the service restarts.

## Local run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:CONSULTANT_EMAIL = "consultant@example.com"
$env:CONSULTANT_PASSWORD_HASH = "<generated hash>"
$env:PORTAL_SESSION_SECRET = "<random secret>"
.venv\Scripts\uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/consultant`.

## Deployment

The repository's root `render.yaml` defines the service. Connect the repository in Render, provide the required secret values, then map `consultant.pimecineng.com` to the service. The public website already links to that address.

After mapping the custom domain, add the DNS record shown by Render at the current DNS provider and verify HTTPS before using confidential workbooks.


