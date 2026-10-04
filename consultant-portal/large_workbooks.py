"""Bounded disk uploads and background Excel analysis. No public file paths."""
import asyncio
import hashlib
import json
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path
from zipfile import ZipFile, BadZipFile

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from analyze_sales_periods import analyze
import saved_records

LIMIT = 100 * 1024 * 1024
CHUNK = 4 * 1024 * 1024
JOBS = {}
WORKER = asyncio.Semaphore(1)
ROOT = Path(tempfile.mkdtemp(prefix="pimec-large-"))


def validate(path):
    try:
        with ZipFile(path) as archive:
            items = archive.infolist()
            if len(items) > 4000 or sum(i.file_size for i in items) > 1536 * 1024 * 1024:
                raise ValueError("Workbook exceeds the 1.5 GB expanded processing limit.")
            if "xl/workbook.xml" not in archive.namelist():
                raise ValueError("This file is not an Excel workbook.")
            for item in items:
                if item.flag_bits & 1 or "vbaproject" in item.filename.casefold():
                    raise ValueError("Use an unlocked, macro-free .xlsx workbook.")
                if item.file_size > 2_000_000 and item.file_size / max(1, item.compress_size) > 300:
                    raise ValueError("Workbook compression exceeds the processing limit.")
                if item.filename.endswith((".xml", ".rels")):
                    with archive.open(item) as source:
                        tail = b""
                        while chunk := source.read(1024 * 1024):
                            scan = tail + chunk.replace(b"\x00", b"").upper()
                            if b"<!DOCTYPE" in scan or b"<!ENTITY" in scan:
                                raise ValueError("Workbook XML declarations are not supported.")
                            tail = scan[-32:]
    except BadZipFile:
        raise ValueError("The file is not a valid .xlsx workbook.") from None


async def limited_body(request, limit):
    data = bytearray()
    async for piece in request.stream():
        data.extend(piece)
        if len(data) > limit:
            raise HTTPException(413, "Upload piece exceeds its size limit.")
    return bytes(data)


def install(app, require_consultant, verify_csrf, csrf, templates):
    async def owner(request):
        await require_consultant(request)
        return saved_records.identity(request)[1]

    async def job_for(request, job_id):
        subject = await owner(request)
        job = JOBS.get(job_id)
        if not job or job['owner'] != subject:
            raise HTTPException(404, "Upload not found. Reopen the agent and upload again.")
        if job['expires'] < time.time() and job['state'] not in ('processing', 'saving', 'queued'):
            raise HTTPException(410, "Upload expired. Please upload again.")
        return job

    @app.post('/consultant/large/start')
    async def start(request: Request):
        subject = await owner(request)
        verify_csrf(request, request.headers.get('x-csrf-token', ''))
        try:
            data = json.loads(await limited_body(request, 4096))
            size = data['size']
            filename = str(data['filename']).replace('\\', '/').split('/')[-1][:160]
            cutoff = date.fromisoformat(data['as_of'])
            if type(size) is not int or not 0 < size <= LIMIT or not filename.lower().endswith('.xlsx'):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Choose an .xlsx workbook up to 100 MB and a valid cutoff date.") from None
        for key, job in list(JOBS.items()):
            if job['expires'] < time.time() and job['state'] not in ('processing', 'queued', 'saving'):
                job['path'].unlink(missing_ok=True)
                JOBS.pop(key)
        if sum(j['state'] != 'saved' for j in JOBS.values()) >= 4:
            raise HTTPException(503, "Large-file workspace is busy. Retry after an existing upload expires.")
        key = uuid.uuid4().hex
        path = ROOT / (key + '.xlsx')
        path.touch()
        JOBS[key] = dict(owner=subject, path=path, size=size, received=0, filename=filename,
                         cutoff=cutoff, state='uploading', message='Uploading workbook',
                         expires=time.time()+3600, lock=asyncio.Lock())
        return {'id': key, 'chunk_bytes': CHUNK}

    @app.put('/consultant/large/{job_id}/chunk')
    async def chunk(request: Request, job_id: str, offset: int):
        job = await job_for(request, job_id)
        verify_csrf(request, request.headers.get('x-csrf-token', ''))
        content = await limited_body(request, CHUNK)
        async with job['lock']:
            if job['state'] != 'uploading' or offset < 0 or not content or offset + len(content) > job['size']:
                raise HTTPException(409, "Invalid upload piece. Start the upload again.")
            if offset < job['received']:
                with job['path'].open('rb') as file:
                    file.seek(offset)
                    if file.read(len(content)) != content:
                        raise HTTPException(409, "Retried upload piece does not match.")
                return {'received': job['received']}
            if offset != job['received']:
                raise HTTPException(409, "Upload pieces must arrive in order.")
            with job['path'].open('ab') as file:
                file.write(content)
            job['received'] += len(content)
            job['expires'] = time.time()+3600
        return {'received': job['received']}

    def calculate(job):
        validate(job['path'])
        def progress(done, total):
            job['message'] = f'Analyzing row {done:,} of approximately {total:,}'
        report = analyze(job['path'], job['cutoff'], progress, projected=True)
        report.update(source='Uploaded workbook', uploaded_filename=job['filename'])
        return report

    async def process(job, request):
        async with WORKER:
            job.update(state='processing', message='Validating workbook and reading sales rows')
            try:
                report = await asyncio.to_thread(calculate, job)
                draft = saved_records.stage(request, dict(kind='upload', filename=job['filename'],
                    report=report, document=None, _original_path=str(job['path'])))
                saved_records.DRAFTS[draft]['expires'] = time.time()+3600
                job.update(state='ready', message='Analysis complete. Review the results and select Save records.', report=report, draft=draft)
            except ValueError as exc:
                job.update(state='error', message=str(exc))
            except Exception:
                job.update(state='error', message='Analysis could not finish. Check the workbook format and try again. The original file on your computer is unchanged.')
            job['expires'] = time.time()+3600

    @app.post('/consultant/large/{job_id}/finish')
    async def finish(request: Request, job_id: str):
        job = await job_for(request, job_id)
        verify_csrf(request, request.headers.get('x-csrf-token', ''))
        async with job['lock']:
            if job['state'] == 'uploading':
                if job['received'] != job['size']:
                    raise HTTPException(409, "The upload is incomplete.")
                job.update(state='queued', message='Workbook uploaded. Waiting for analysis.')
                job['task'] = asyncio.create_task(process(job, request))
        return {'url': '/consultant/large/' + job_id}

    @app.get('/consultant/large/{job_id}/status')
    async def status(request: Request, job_id: str):
        job = await job_for(request, job_id)
        return {k: job.get(k) for k in ('state', 'message', 'saved_key')}

    @app.get('/consultant/large/{job_id}', response_class=HTMLResponse)
    async def page(request: Request, job_id: str):
        job = await job_for(request, job_id)
        if job['state'] == 'saved':
            return RedirectResponse('/consultant/records/' + job['saved_key'] + '?saved=1', status_code=303)
        if job['state'] == 'ready':
            request.session['draft_id'] = job['draft']
            return templates.TemplateResponse(request, 'results.html', dict(report=job['report'], csrf=csrf(request),
                save_action='/consultant/large/'+job_id+'/save', report_url='/consultant/large/'+job_id+'/report', status_message=job['message']))
        return templates.TemplateResponse(request, 'large_progress.html', dict(job_id=job_id, job=job))

    @app.get('/consultant/large/{job_id}/report')
    async def report(request: Request, job_id: str):
        job = await job_for(request, job_id)
        if 'report' not in job:
            raise HTTPException(409, 'Analysis is not ready.')
        return JSONResponse(job['report'], headers={'Content-Disposition': 'attachment; filename=calculated-report.json'})

    async def save(job, request):
        try:
            key = await saved_records.save_draft(request, job['draft'])
            job.update(state='saved', message='Record saved', saved_key=key)
            saved_records.DRAFTS.pop(job['draft'], None)
            job.pop('report', None)
            job['path'].unlink(missing_ok=True)
        except Exception:
            job.update(state='ready', message='Saving did not complete. Select Save records to retry.')

    @app.post('/consultant/large/{job_id}/save')
    async def save_start(request: Request, job_id: str):
        job = await job_for(request, job_id)
        form = await request.form()
        verify_csrf(request, str(form.get('csrf_token', '')))
        if job['state'] == 'ready':
            job.update(state='saving', message='Saving the original workbook and analysis to your private records')
            job['task'] = asyncio.create_task(save(job, request))
        return RedirectResponse('/consultant/large/'+job_id, status_code=303)
