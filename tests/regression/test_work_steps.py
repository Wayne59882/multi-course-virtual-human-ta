"""Durable step boundaries, competing workers, visual review resume and CPU caps."""
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
from work_steps import step_scope, completed_call
from cpu_budget import cpu_slot, cpu_workers
from pdf_vision import LocalVision, visual_clip
import fitz

with tempfile.TemporaryDirectory() as temporary:
    folder=Path(temporary)
    request={'model':'a','evidence':'source-1'}
    calls=[]
    def compute():
        calls.append(True);time.sleep(.05);return 'complete draft'
    def worker():
        with step_scope(folder/'steps'):
            assert completed_call(request,compute)=='complete draft'
    workers=[threading.Thread(target=worker) for _ in range(4)]
    for thread in workers:thread.start()
    for thread in workers:thread.join(3);assert not thread.is_alive()
    assert len(calls)==1,'competing workers repeated a completed model step'
    # A new scope simulates a restarted worker; evidence/model/revision changes
    # invalidate the key, while an exception never becomes a saved result.
    worker();assert len(calls)==1
    with step_scope(folder/'steps'):
        completed_call(dict(request,evidence='source-2'),compute)
        completed_call(dict(request,model='b'),compute)
        try:completed_call({'broken':True},lambda:(_ for _ in ()).throw(RuntimeError('interrupted')))
        except RuntimeError:pass
        assert completed_call({'broken':True},lambda:'recovered')=='recovered'
    assert len(calls)==3
    assert json.loads((folder/'steps/metrics.json').read_text())['checkpoint_hits']==4

    # The real vision path dies during review. On restart, transcription is
    # reused and only review runs. Unreviewed text is never returned as approved.
    with patch.dict(os.environ,{'VQ_PDF_VISION':'auto','VQ_PDF_VISION_HOST':'http://127.0.0.1:11435',
                                'VQ_PDF_VISION_MODEL':'fixture-local'}):
        document=fitz.open();page=document.new_page()
        page.insert_text((40,70),'Visible source')
        reader=LocalVision();requests=[];interrupt=[True]
        def respond(path,payload,timeout=180):
            if path=='/api/show':return {'capabilities':['vision']}
            prompt=payload['messages'][1]['content'];requests.append(prompt)
            if prompt.startswith('Transcribe'):
                return {'message':{'content':json.dumps({'blocks':[{'kind':'equation','text':r'$u_{tt}-\Delta u=0$','legible':True}]})}}
            if interrupt[0]:
                interrupt[0]=False
                raise SystemExit('simulate process exit during review')
            return {'message':{'content':json.dumps({'approved_indices':[0],'uncertain':False})}}
        reader.request=respond
        try:
            with step_scope(folder/'visual'):reader.read(page)
        except SystemExit:pass
        with step_scope(folder/'visual'):result=reader.read(page)
        assert result['ok'] and result['reviewed'] and r'\Delta' in result['text']
        assert len(requests)==3 and sum(p.startswith('Transcribe') for p in requests)==1
        # Figure-only crop includes images and their surrounding margin; sparse
        # pages keep the whole page so a title cannot hide a scanned body.
        image=page.get_pixmap()
        rich=document.new_page()
        rich.insert_textbox(fitz.Rect(40,40,550,150),'Readable teaching text about diffusion and membrane transport. '*7)
        rich.insert_image(fitz.Rect(100,300,350,500),pixmap=image)
        clip=visual_clip(rich)
        assert clip is not None and clip.contains(fitz.Rect(rich.get_image_info()[0]['bbox']))
        assert visual_clip(document[0]) is None
        document.close()

    # Exercise the actual backend integration, including installed-model
    # metadata changes and malformed JSON which must not poison a checkpoint.
    from types import SimpleNamespace
    from model_lock import SharedModelLock
    import reference_server as reference
    server=SimpleNamespace(model_lock=SharedModelLock(str(folder/'backend.lock')))
    state={'weights':'digest-1','calls':0,'invalid':False}
    def local_api(url,payload=None,timeout=0):
        if url.endswith('/api/show'):return {'modelfile':state['weights']}
        state['calls']+=1
        text='not-json' if state['invalid'] else ('{"approved":true}' if payload.get('format')=='json' else 'complete response')
        return {'message':{'content':text},'done':True,'eval_duration':1000000}
    with patch.object(reference,'url_json',local_api):
        for _ in range(2):
            with step_scope(folder/'backend'):
                assert reference.course_model_call(server,'gemma','lecture','source',100)=='complete response'
        assert state['calls']==1
        state['weights']='digest-2'
        with step_scope(folder/'backend'):
            reference.course_model_call(server,'gemma','lecture','source',100)
        assert state['calls']==2,'replaced weights reused an old model step'
        with step_scope(folder/'backend'):
            state['invalid']=True
            try:reference.course_model_call(server,'gemma','COURSE_GROUNDING_REVIEW','evidence',100)
            except ValueError:pass
            else:raise AssertionError('malformed JSON was accepted')
            state['invalid']=False
            assert reference.course_model_call(server,'gemma','COURSE_GROUNDING_REVIEW','evidence',100)=='{"approved":true}'
        assert state['calls']==4,'malformed JSON was cached'

    # Terminating a process releases its step lock. Only its unfinished call
    # is re-executed by a new worker; the saved earlier call remains usable.
    import multiprocessing
    if 'fork' in multiprocessing.get_all_start_methods():
        context=multiprocessing.get_context('fork')
        entered=context.Event()
        def interrupted_process():
            with step_scope(folder/'process'):
                completed_call({'step':'draft'},lambda:'saved draft')
                def pending_review():
                    entered.set();time.sleep(10);return 'never completed'
                completed_call({'step':'review'},pending_review)
        process=context.Process(target=interrupted_process)
        process.start();assert entered.wait(3)
        process.terminate();process.join(3);assert not process.is_alive()
        with step_scope(folder/'process'):
            assert completed_call({'step':'draft'},lambda:(_ for _ in ()).throw(AssertionError('draft repeated')))=='saved draft'
            assert completed_call({'step':'review'},lambda:'new review')=='new review'

    with patch.dict(os.environ,{'VQ_CPU_WORKERS':'2','VQ_CPU_SLOT_DIR':str(folder/'cpu')}):
        state={'running':0,'peak':0};guard=threading.Lock()
        def cpu_job():
            with cpu_slot():
                with cpu_slot():  # nested operations do not consume another slot
                    with guard:
                        state['running']+=1;state['peak']=max(state['peak'],state['running'])
                    time.sleep(.06)
                    with guard:state['running']-=1
        workers=[threading.Thread(target=cpu_job) for _ in range(6)]
        for thread in workers:thread.start()
        for thread in workers:thread.join(3);assert not thread.is_alive()
        assert state['peak']==cpu_workers()
print('PASS durable completed-step reuse, competing workers, changed-source invalidation, interrupted visual review resume, safe graphical crop and combined CPU cap')
