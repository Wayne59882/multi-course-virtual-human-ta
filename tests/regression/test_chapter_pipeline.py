"""Publish first chapters before later extraction; restart keeps previous results."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'app'))
os.environ['VQ_PDF_OCR']='off'
os.environ['VQ_PDF_VISION']='off'
import course_runtime as cr
import fitz

with tempfile.TemporaryDirectory() as temporary:
    folder=Path(temporary)
    cr.COURSE_ROOT=folder/'courses';cr.STAGING_ROOT=cr.COURSE_ROOT/'_staging'
    cr.STAGING_ROOT.mkdir(parents=True)
    document=fitz.open()
    for i in range(3):
        page=document.new_page()
        page.insert_textbox(fitz.Rect(40,40,550,350),
                            'Membrane transport moves solutes across membranes. Diffusion follows concentration gradients. '*4)
    source=folder/'source.pdf';document.save(source);document.close()
    manager=cr.CourseManager()
    stage=manager.inspect([('source.pdf',source)],course_name='Transport')
    source_name=stage['sources'][0]['file_name']
    units=[{'id':'unit%d'%i,'title':'Transport %d'%i,'source_title':'Transport','source_file':source_name,
            'start_page':i,'end_page':i} for i in range(1,4)]
    publications=[]
    def prepare(cid,key,call,**kwargs):
        m=manager.manifest(cid)
        publications.append((len(m['units']),m['ingestion_complete']))
        # Simulate a lecture which completes while the following PDF pages are
        # being extracted. Later snapshots must never reset its script/bank.
        if len(m['units'])==1:
            manager._update_manifest(cid,lambda current:current['units'][0].update(
                {'script':'Saved reviewed lecture','script_status':'ready'}))
            cr._json_dump(manager._course_dir(cid)/'question_banks.json',{'unit1':[{'saved':True}]})
    manager._start_preparation=prepare
    with patch.object(cr,'_resolve_course_name',return_value=('Transport','teacher')):
        manager._build_impl('fixture',stage,units,'test-course','Transport','none','gemma',lambda *args:'')
    manifest=manager.manifest('test-course')
    assert publications[0]==(1,False) and publications[-1]==(3,True),publications
    assert manifest['units'][0]['script']=='Saved reviewed lecture'
    assert json.loads((manager._course_dir('test-course')/'question_banks.json').read_text())['unit1'][0]['saved']
    rows=[json.loads(x) for x in (manager._course_dir('test-course')/'chunks.jsonl').read_text().splitlines()]
    assert {r['unit_id'] for r in rows}=={'unit1','unit2','unit3'}
    assert len({r['id'] for r in rows})==len(rows)

    # Simulate restart after the first two committed chapters. Their stored
    # text is not re-extracted or reset. Only the missing chapter runs.
    manifest['units']=manifest['units'][:2];manifest['ingestion_complete']=False
    manifest['source_review']['units'].pop('unit3')
    cr._json_dump(manager._manifest_path('test-course'),manifest)
    path=manager._course_dir('test-course')/'chunks.jsonl'
    # A crash can happen after chunks were renamed but before the matching
    # manifest was committed: leave unit3 in the file to exercise recovery.
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    called=[];real=cr.iter_pages
    def track(doc,start,end,*args,**kwargs):
        called.append(start)
        yield from real(doc,start,end,*args,**kwargs)
    with patch.object(cr,'iter_pages',track):
        manager._build_impl('fixture',stage,units,'test-course','Transport','none','gemma',lambda *args:'')
    assert called==[3],called
    assert manager.manifest('test-course')['ingestion_complete'] is True
    recovered=[json.loads(x) for x in path.read_text().splitlines()]
    assert len({r['id'] for r in recovered})==len(recovered),'uncommitted crash tail was duplicated'

    # A chapter arriving while a worker is running must trigger another pass,
    # even if the first pass finished successfully against an older snapshot.
    entered=threading.Event();release=threading.Event();again=threading.Event()
    count=[]
    def old_index(*args):
        count.append(True)
        if len(count)==1:entered.set();release.wait(3)
        else:again.set()
    manager._prepare_index=old_index
    manager._start_preparation=cr.CourseManager._start_preparation.__get__(manager)
    manager._start_preparation('test-course','gemma',lambda *a:'',force=True,only=('index',))
    assert entered.wait(2)
    manager._update_manifest('test-course',lambda m:m.update(source_revision=m['source_revision']+1))
    release.set();assert again.wait(3),'new chapter was lost behind an already-running worker'
    for _ in range(100):
        if not manager.running:break
        time.sleep(.01)
    assert not manager.running
print('PASS first-chapter publication, concurrent-result preservation, restart after partial ingestion, unique chunks and preparation of newly arrived chapters')
