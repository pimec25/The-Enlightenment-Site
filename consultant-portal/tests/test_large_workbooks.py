import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from types import SimpleNamespace
import app
import large_workbooks as large
import saved_records as store
import sso
from analyze_sales_periods import analyze
from datetime import date


def workbook():
    book = Workbook()
    book.active.append(['Request Date','Extended Price','Extended Cost','Parent Number','2nd Item Number','Value Stream Product Family','Order Number','Line Number'])
    book.active.append([datetime(2026,1,1),100,60,'c','p','stream','order',1])
    out=BytesIO(); book.save(out)
    return out.getvalue()


def test_stream_validation(tmp_path):
    path=tmp_path/'test.xlsx'; path.write_bytes(workbook())
    large.validate(path)
    with ZipFile(path,'a') as archive:
        archive.writestr('xl/bad.xml',b' '*(1024*1024-4)+b'<!DOCTYPE test>')
    with pytest.raises(ValueError,match='declarations'):
        large.validate(path)


@pytest.mark.parametrize('epoch1904', [False, True])
def test_projected_reader_matches_standard_with_gaps_and_dates(tmp_path, epoch1904):
    from openpyxl.utils.datetime import MAC_EPOCH
    book=Workbook()
    if epoch1904: book.epoch=MAC_EPOCH
    sheet=book.active
    sheet.append(['Request Date','Extended Price','Extended Cost','Parent Number','2nd Item Number','Value Stream Product Family','Order Number','Line Number','Customer Name','Product Name','Ignored'])
    sheet.append([datetime(2026,1,1),150.5,120,123,'P1','V1',100,1,'Name & more','Item','Ignored content'])
    sheet.append([None]*11)
    sheet.append([datetime(2025,1,1),100,50,123,'P1','V1',101,1])
    sheet.append([datetime(2026,2,1),0,50,124,'P2','V1',102,2])
    path=tmp_path/'sparse.xlsx'; book.save(path)
    assert analyze(path,date(2026,9,30),projected=True)==analyze(path,date(2026,9,30))


def test_chunk_upload_analysis_save_download_retry_and_ownership(monkeypatch):
    async def scenario():
        large.JOBS.clear(); store.DRAFTS.clear()
        async def verify(token): return token
        monkeypatch.setattr(sso,'verify_consultant',verify)
        objects={}
        async def storage(token,method,path,**kwargs):
            if method=='POST':
                key=path.removeprefix('object/')
                if key in objects: return httpx.Response(409,text='Duplicate')
                objects[key]=json.loads(kwargs['content'])
                return httpx.Response(200,json={})
            key=path.removeprefix('object/authenticated/')
            return httpx.Response(200,json=objects[key])
        monkeypatch.setattr(store,'call',storage)
        transport=httpx.ASGITransport(app=app.app)
        async with httpx.AsyncClient(transport=transport,base_url='http://test') as c:
            await c.post('/consultant/sso',headers={'Origin':'https://www.pimecineng.com','X-Pimec-Dashboard':'1','Authorization':'Bearer owner-a'})
            page=await c.get('/consultant')
            csrf=re.search(r'name="csrf_token" value="([^"]+)"',page.text).group(1)
            headers={'X-CSRF-Token':csrf}
            content=workbook()
            async def start(size):
                return await c.post('/consultant/large/start',headers=headers,json={'filename':'sample.xlsx','size':size,'as_of':'2026-09-30'})
            assert (await start(large.LIMIT+1)).status_code==422
            boundary=await start(large.LIMIT)
            assert boundary.status_code==200
            job=(await start(len(content))).json()['id']
            url='/consultant/large/'+job
            assert (await c.post(url+'/finish',headers=headers)).status_code==409
            assert (await c.put(url+'/chunk?offset=1',headers=headers,content=content)).status_code==409
            assert (await c.put(url+'/chunk?offset=0',content=content)).status_code==403
            assert (await c.put(url+'/chunk?offset=0',headers=headers,content=content)).status_code==200
            assert (await c.put(url+'/chunk?offset=0',headers=headers,content=content)).status_code==200
            assert (await c.put(url+'/chunk?offset=0',headers=headers,content=b'wrong')).status_code==409
            assert (await c.post(url+'/finish',headers=headers)).status_code==200
            await large.JOBS[job]['task']
            assert (await c.get(url+'/status')).json()['state']=='ready'
            report=(await c.get(url+'/report')).json()
            assert report['current_period']['sales']==100
            assert report['detailed_80_20']['dimensions']['customer']['sales']['total_groups']==1
            assert 'Management results' in (await c.get(url)).text
            # Different Consultant cannot inspect the upload even with its identifier.
            async with httpx.AsyncClient(transport=transport,base_url='http://test') as other:
                await other.post('/consultant/sso',headers={'Origin':'https://www.pimecineng.com','X-Pimec-Dashboard':'1','Authorization':'Bearer owner-b'})
                assert (await other.get(url+'/status')).status_code==404
            # Save twice before completing the job, exercising immutable-piece retry.
            request=SimpleNamespace(session={'sso_id':next(k for k,v in sso.SESSIONS.items() if v[1]=='owner-a')})
            draft=large.JOBS[job]['draft']
            key=await store.save_draft(request,draft)
            assert await store.save_draft(request,draft)==key
            response=await c.get('/consultant/records/'+key+'/original')
            assert response.content==content
            assert hashlib.sha256(response.content).digest()==hashlib.sha256(content).digest()
            manifest=objects[store.BUCKET+'/owner-a/'+key]
            assert '_original_path' not in manifest and 'original' not in manifest
            assert manifest['original_size']==len(content)
            assert (await c.post(url+'/save',data={'csrf_token':csrf})).status_code==303
            await large.JOBS[job]['task']
            assert large.JOBS[job]['state']=='saved'
            assert not large.JOBS[job]['path'].exists()
        for job in large.JOBS.values(): job['path'].unlink(missing_ok=True)
        large.JOBS.clear()
    asyncio.run(scenario())


def test_original_manifest_rejects_foreign_piece(monkeypatch):
    monkeypatch.setattr(store,'identity',lambda request:('token','owner'))
    record={'original_parts':[{'name':'foreign.json','size':1,'sha256':'x'}],'original_size':1}
    with pytest.raises(HTTPException):
        asyncio.run(store.original_parts(None,'record.json',record))


def test_multiple_saved_pieces_reconstruct_original(tmp_path, monkeypatch):
    content=b'a'*(4*1024*1024)+b'b'*(4*1024*1024)+b'final piece'
    path=tmp_path/'original.xlsx'; path.write_bytes(content)
    request=SimpleNamespace(session={'sso_id':'multipart-test'})
    sso.SESSIONS['multipart-test']=('token','owner',time.time()+1000)
    objects={}
    async def storage(token,method,name,**kwargs):
        if method=='POST':
            objects[name.removeprefix('object/')]=json.loads(kwargs['content'])
            return httpx.Response(200,json={})
        return httpx.Response(200,json=objects[name.removeprefix('object/authenticated/')])
    monkeypatch.setattr(store,'call',storage)
    async def scenario():
        draft=store.stage(request,{'kind':'upload','filename':'original.xlsx','_original_path':str(path),'report':{}})
        key=await store.save_draft(request,draft)
        record=await store.read(request,key)
        assert len(record['original_parts'])==3
        stream=await store.original_parts(request,key,record)
        restored=b''.join([part async for part in stream])
        assert restored==content
        store.DRAFTS.pop(draft)
    asyncio.run(scenario())
