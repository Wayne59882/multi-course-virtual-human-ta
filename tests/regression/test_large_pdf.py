"""1001-page ingestion, resumability and private upload ownership; no model calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
try:import fitz
except ImportError:
    print('SKIP large PDF integration: PyMuPDF is not installed');raise SystemExit(0)
import course_runtime as cr
import pdf_ingestion as ingestion
import access_control as auth
os.environ['VQ_PDF_OCR']='off'  # Explicitly exercise the unavailable-engine fallback.
os.environ['VQ_PDF_VISION']='off'

with tempfile.TemporaryDirectory() as temporary:
    directory=Path(temporary);cr.COURSE_ROOT=directory/'courses';cr.STAGING_ROOT=cr.COURSE_ROOT/'_staging';cr.STAGING_ROOT.mkdir(parents=True)
    document=fitz.open()
    for number in range(1001):
        page=document.new_page()
        if number!=500:page.insert_text((40,60),'Genomics studies gene expression and molecular regulation. This page contains readable teaching evidence. Page %s.'%(number+1))
    image=document[0].get_pixmap(matrix=fitz.Matrix(.2,.2))
    document[500].insert_image(document[500].rect,pixmap=image)
    document.set_toc([[1,'Hybridization',1],[1,'Transcriptome',501],[1,'Gene expression',502],[1,'Validation',1001]])
    original=directory/'Genomics.pdf';document.save(str(original));document.close()
    manager=cr.CourseManager();started=time.monotonic()
    stage=manager.inspect([('Genomics.pdf',original)],course_name='Genomics')
    source=manager._stage_dir(stage['stage_id'])/'source'/stage['sources'][0]['file_name']
    assert source.exists() and source.read_bytes()==original.read_bytes()
    received=manager.upload_diagnostics(stage['stage_id'])['received_files'][0]
    assert received['upload_received'] and received['size_bytes']==original.stat().st_size and len(received['sha256'])==64
    manager._start_preparation=lambda *args,**kwargs:None
    jid='course-fixture';cid='genomics-fixture'
    job={'id':jid,'stage_id':stage['stage_id'],'course_id':cid,'selected':stage['units'],'status':'queued'}
    manager.jobs[jid]=job;cr._json_dump(manager._job_path(jid),job)
    manager._build(jid,stage,stage['units'],cid,'Genomics','none','gemma',lambda *args:(_ for _ in ()).throw(AssertionError('model must not run')))
    assert manager.job_status(jid)['status']=='done',manager.job_status(jid)
    manifest=manager.manifest(cid)
    assert len(manifest['units'])==4 and manifest['source_review']['required']
    blocked=[unit for unit in manifest['units'] if unit['source_blocked']]
    assert len(blocked)==1 and blocked[0]['title']=='Transcriptome'
    assert blocked[0]['source_quality']['warnings'][0]['page']==501
    rows=[json.loads(line) for line in (manager._course_dir(cid)/'chunks.jsonl').read_text().splitlines()]
    assert 1001 in {row['pdf_page'] for row in rows} and 501 not in {row['pdf_page'] for row in rows}
    assert manager.job_status(jid)['processed_pages']==1001
    assert manager.upload_diagnostics(stage['stage_id'])['source_quality']
    # Simulate restart in a different CourseManager: healthy cached pages are
    # reused, while the problem page is reconsidered on a retry.
    counts={'read':0,'cached':0}
    with fitz.open(str(original)) as doc:
        for row in ingestion.iter_pages(doc,1,1001,cr._shared_pages(manager._stage_dir(stage['stage_id']),received['sha256']),received['sha256'],lambda row,cached:counts.__setitem__('cached' if cached else 'read',counts['cached' if cached else 'read']+1) if row['status'] not in ('ocr_running','vision_running') else None):pass
    assert counts=={'read':1,'cached':1000},counts
    # Invalid files are retained with a diagnostic receipt rather than deleted.
    try:manager.inspect([('broken.pdf',b'%PDF-not-a-valid-document')],stage_id='broken-stage')
    except Exception:pass
    report=manager.upload_diagnostics('broken-stage');assert report['received_files'][0]['inspection_status']=='error'
    assert list(manager._stage_dir('broken-stage').joinpath('source').glob('*.pdf'))
    # Interrupted transfers are visible even before PDF inspection succeeds.
    manager.upload_chunk('unfinished-stage','part-one','Genomics.pdf',100,0,b'%PDF-12345')
    transfers=manager.upload_diagnostics('unfinished-stage')['transfers']
    assert transfers[0]['received_bytes']==10 and not transfers[0]['complete'] and transfers[0]['name']=='Genomics.pdf'
    # Resume after the last chunk must not read the full book into RAM.
    ud=manager._stage_dir('resume-stage')/'_uploads';ud.mkdir(parents=True)
    (ud/'resume.part').write_bytes(original.read_bytes())
    cr._json_dump(ud/'resume.json',{'name':'Genomics.pdf','size':original.stat().st_size,'offset':original.stat().st_size,'complete':False,'course_name':'Genomics'})
    read_bytes=Path.read_bytes
    def no_whole_part(path):
        if path.suffix=='.part':raise AssertionError('entire upload loaded into RAM during resume')
        return read_bytes(path)
    with patch.object(Path,'read_bytes',no_whole_part):assert manager.upload_status('resume-stage','resume')['complete']
    # Teachers cannot inspect another teacher's uploads; administrator support
    # can inspect originals, without granting students any new upload access.
    auth.DB_PATH=directory/'accounts.sqlite3';auth.init()
    auth._exec('INSERT INTO stages(stage_id,owner_id,created_at) VALUES(?,?,?)',(stage['stage_id'],1,time.time()))
    auth._exec('INSERT INTO users(id,email,name,pw,requested_role,role,status) VALUES(?,?,?,?,?,?,?)',(1,'teacher@example.test','Fixture Teacher','private-password-hash','teacher','teacher','active'))
    for user,expected in [({'id':1,'role':'teacher','is_admin':False},True),({'id':2,'role':'teacher','is_admin':False},False),({'id':2,'role':'teacher','is_admin':True},True),({'id':1,'role':'student','is_admin':False},False)]:
        denial,_=auth.authorize(user,'/api/reference/course-upload-diagnostics','stage_id='+stage['stage_id'],b'','')
        assert (denial is None)==expected,(user,denial)
    denial,_=auth.authorize({'id':2,'role':'teacher','is_admin':False},'/api/reference/course-admin-uploads','',b'','')
    assert denial and denial['status']==403
    listing=manager.admin_uploads();assert any(row['stage_id']=='unfinished-stage' for row in listing)
    assert all('source_quality' not in row for row in listing),'full reports must load on demand'
    payload={'ok':True,'uploads':listing}
    data=auth.after_response({'id':2,'role':'teacher','is_admin':True},{'seg':'course-admin-uploads'},200,json.dumps(payload).encode(),'application/json')
    admin=json.loads(data);owned=next(row for row in admin['uploads'] if row['stage_id']==stage['stage_id'])
    assert owned['owner']['name']=='Fixture Teacher' and owned['owner']['email']=='teacher@example.test'
    assert b'private-password-hash' not in data and 'pw' not in owned['owner']
    print('PASS 1001-page PDF, page isolation/cache, original retention, bounded resume, incomplete uploads, admin owner metadata and teacher isolation; %.2fs fixture run'%(time.monotonic()-started))
