import asyncio
import base64
import json
import re
import time
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import app
import saved_records as store
import sso
from office_documents import extract_office


def office(path, text):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as z:
        z.writestr(path, text)
    return stream.getvalue()


WORD = office("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Sample manufacturing review</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Revenue</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1000</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')


def client(monkeypatch, subject="consultant-a"):
    async def verify(token):
        return subject
    monkeypatch.setattr(sso, "verify_consultant", verify)
    c = TestClient(app.app)
    assert c.post('/consultant/sso', headers={"Origin": "https://www.pimecineng.com", "X-Pimec-Dashboard": "1", "Authorization": "Bearer test-token"}).status_code == 200
    page = c.get('/consultant/method')
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    return c, token


def test_office_extract_and_validation():
    result = extract_office(WORD, 'sample.docx')
    assert result['blocks'][0]['text'] == 'Sample manufacturing review'
    assert result['blocks'][1]['text'] == 'Revenue | 1000'
    ppt = office('ppt/slides/slide1.xml', '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Slide text</a:t></p:sld>')
    assert extract_office(ppt, 'test.pptx')['blocks'][0]['text'] == 'Slide text'
    for filename, content in [('old.doc', WORD), ('fake.docx', b'not a zip'), ('wrong.xlsx', WORD)]:
        with pytest.raises(ValueError):
            extract_office(content, filename)
    entity = office('word/document.xml', '<!DOCTYPE test [<!ENTITY x "secret">]><test/>')
    with pytest.raises(ValueError):
        extract_office(entity, 'bad.docx')
    entity16 = office('word/document.xml', '<!DOCTYPE test [<!ENTITY x "secret">]><test/>'.encode('utf-16'))
    with pytest.raises(ValueError):
        extract_office(entity16, 'bad.docx')


def test_upload_save_reload_original_and_csrf(monkeypatch):
    c, token = client(monkeypatch)
    uploads = {}
    async def call(jwt, method, path, **kwargs):
        assert jwt == 'test-token'
        if method == 'POST' and path.startswith('object/consultant-analysis-records/'):
            uploads[path.removeprefix('object/')] = json.loads(kwargs['content'])
            return httpx.Response(200, json={})
        if method == 'GET':
            data = uploads.get(path.removeprefix('object/authenticated/'))
            return httpx.Response(200 if data else 404, json=data)
        return httpx.Response(200, json=[{'id':'1','name':key.split('/')[-1], 'created_at':'2026-10-03'} for key in uploads])
    monkeypatch.setattr(store, 'call', call)
    response = c.post('/consultant/documents', data={'csrf_token':token,'as_of':'2026-09-30'}, files={'document':('sample.docx',WORD)})
    assert response.status_code == 200
    assert 'Save records' in response.text and 'Revenue | 1000' in response.text
    draft = re.search(r'name="draft_id" value="([^"]+)"',response.text).group(1)
    assert c.post('/consultant/records/save',data={'csrf_token':'wrong','draft_id':draft}).status_code == 403
    saved = c.post('/consultant/records/save',data={'csrf_token':token,'draft_id':draft})
    assert saved.status_code == 200 and 'Record saved successfully' in saved.text
    assert 'sample.docx' in c.get('/consultant/records').text
    key = next(iter(uploads)).split('/')[-1]
    store.DRAFTS.clear()  # A restart does not remove the saved object.
    assert c.get('/consultant/records/'+key+'/original').content == WORD
    assert c.get('/consultant/records/'+key+'/review').json()['format'] == 'DOCX'
    assert c.get('/consultant/records/'+key).headers['cache-control'] == 'no-store'


def test_other_owner_cannot_save_draft_or_choose_folder(monkeypatch):
    c, token = client(monkeypatch, 'consultant-b')
    draft = 'foreign'
    store.DRAFTS[draft] = {'owner':'consultant-a','expires':time.time()+1000}
    assert c.post('/consultant/records/save',data={'csrf_token':token,'draft_id':draft}).status_code == 403
    with pytest.raises(HTTPException):
        store.key_path('consultant-b','../consultant-a/file.json')
    assert store.key_path('consultant-b','a'*32+'--test.json').startswith(store.BUCKET+'/consultant-b/')
    store.DRAFTS.clear()


def test_case_save_and_restore(monkeypatch):
    c, token = client(monkeypatch)
    records = {}
    async def call(jwt, method, path, **kwargs):
        if method == 'POST':
            records[path.removeprefix('object/')] = json.loads(kwargs['content'])
            return httpx.Response(200,json={})
        return httpx.Response(200,json=records[path.removeprefix('object/authenticated/')])
    monkeypatch.setattr(store, 'call', call)
    data = {'version':1,'records':{'charter':[{'client':'Test case'}]},'gates':{}}
    result = c.post('/consultant/method/save',json=data,headers={'X-CSRF-Token':token})
    assert result.status_code == 200
    key = result.json()['key']
    assert c.get('/consultant/records/'+key+'/case').json()['records']['charter'][0]['client'] == 'Test case'
    assert c.post('/consultant/method/save',json=data).status_code == 403


def test_signed_out_denied_and_missing_draft(monkeypatch):
    c = TestClient(app.app)
    assert c.get('/consultant/records').status_code == 401
    assert c.post('/consultant/documents',data={'csrf_token':'x','as_of':'2026-09-30'},files={'document':('file.docx',WORD)}).status_code == 401
    c, token = client(monkeypatch)
    assert c.post('/consultant/records/save',data={'csrf_token':token,'draft_id':'missing'}).status_code == 410


def test_storage_failure_does_not_report_success(monkeypatch):
    c, token = client(monkeypatch)
    async def call(*args, **kwargs):
        return httpx.Response(500,json={})
    monkeypatch.setattr(store, 'call', call)
    response=c.post('/consultant/method/save',json={'version':1,'records':{},'gates':{}},headers={'X-CSRF-Token':token})
    assert response.status_code == 503
    assert 'not saved' in response.json()['detail']
