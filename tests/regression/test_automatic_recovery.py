"""Course preparation recovers by itself instead of asking the teacher.

* A rejected lecture draft is followed by a differently asked draft; several
  chapters are written at once when the GPU allows it.
* A chapter that failed every variant is not retried after each newly
  published chapter, only once more on the final pass.
* Figure pages: the cropped reading, then the whole page, then page halves.
  A page no variant can verify is not read again with the same model, and the
  figure step still finishes as ready (figures never hold the course back).
* Chapters without readable text are read again automatically, once.
* OCR uses 4 CPU slots while students are quiet and 2 while they ask.
"""
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
os.environ['VQ_MODEL_PRIORITY_DIR']=tempfile.mkdtemp(prefix='vq-recovery-')
try:
    import fitz
except ImportError:
    print('SKIP automatic recovery: PyMuPDF is not installed')
    raise SystemExit(0)
import course_runtime as cr
import cpu_budget
import ollama_cancel as oc
import pdf_vision

CHINESE='肺泡通氣量與血流量的比例決定氣體交換的效率。'*12
CONCURRENT={'OLLAMA_NUM_PARALLEL':'3','OLLAMA_MAX_LOADED_MODELS':'2','VQ_GPU_SHARING':'auto'}


def build_course(folder,manager,units_count=3):
    document=fitz.open()
    for i in range(units_count):
        page=document.new_page()
        page.insert_textbox(fitz.Rect(40,40,550,700),
                            ('Ventilation and perfusion are matched in healthy lungs in chapter %d. Oxygen diffuses from alveolar gas into pulmonary capillary blood. '
                             'Carbon dioxide leaves the blood and is exhaled with each breath. Regional differences in blood flow change the local gas exchange ratio. '%i)*3)
    source=folder/'source.pdf';document.save(source);document.close()
    stage=manager.inspect([('source.pdf',source)],course_name='Respiration')
    name=stage['sources'][0]['file_name']
    units=[{'id':'unit%d'%i,'title':'Respiration %d'%i,'source_title':'Respiration','source_file':name,
            'start_page':i,'end_page':i} for i in range(1,units_count+1)]
    manager._start_preparation=lambda *args,**kwargs:None
    with patch.object(cr,'_resolve_course_name',return_value=('Respiration','teacher')):
        manager._build_impl('fixture',stage,units,'course','Respiration','none','gemma',lambda *args:'')
    del manager._start_preparation
    return manager.manifest('course')


with tempfile.TemporaryDirectory() as temporary:
    folder=Path(temporary)
    cr.COURSE_ROOT=folder/'courses';cr.STAGING_ROOT=cr.COURSE_ROOT/'_staging';cr.STAGING_ROOT.mkdir(parents=True)
    manager=cr.CourseManager()
    build_course(folder,manager)

    # -- lectures: escalation and parallel chapters -------------------------
    guard=threading.Lock();seen={'active':0,'peak':0,'calls':[],'budgets':set(),'key_points':[],'key_point_mode':'chinese'}
    def model_call(key,system,user,budget):
        if system==cr.KEY_POINT_SYSTEM:   # the short call that puts a chapter's key points into Chinese
            with guard:seen['key_points'].append((user,budget))
            if seen['key_point_mode']=='chinese':return '- 肺泡通氣量與血流量的比例決定氣體交換的效率\n- 氧氣由肺泡氣體擴散進入肺微血管血液'
            if seen['key_point_mode']=='english':return '- Ventilation matches perfusion in healthy lungs\n- Oxygen diffuses into blood'
            raise RuntimeError('model busy')
        unit=next(name for name in ('Respiration 1','Respiration 2','Respiration 3') if name in user)
        with guard:
            seen['active']+=1;seen['peak']=max(seen['peak'],seen['active'])
            seen['calls'].append((unit,'5 至 7 段' in user));seen['budgets'].add(budget)
        try:
            time.sleep(.15)
            if unit=='Respiration 3':return 'English only draft. '*20      # never acceptable
            if '5 至 7 段' in user:return 'English only draft. '*20         # first variant rejected
            return CHINESE
        finally:
            with guard:seen['active']-=1
    with patch.dict(os.environ,CONCURRENT),patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        with cr.BackgroundWork():
            manager._prepare_lectures('course','gemma',model_call)
    units={u['id']:u for u in manager.manifest('course')['units']}
    assert units['unit1']['script_status']=='ready' and units['unit1']['script_variant']==1
    assert units['unit2']['script_status']=='ready' and CHINESE in units['unit2']['script']
    # A chapter the model cannot write ends with its own key points in the same pass: no waiting, no retries.
    third=units['unit3']
    assert third['script_status']=='ready' and third['script_kind']=='source_excerpts',third
    assert third['script'].startswith('本章重點：Respiration 3') and '- 肺泡通氣量與血流量的比例決定氣體交換的效率' in third['script'] and not cr._stale_lecture(third),third['script']
    assert len(seen['key_points'])==1 and 'Oxygen diffuses from alveolar gas' in seen['key_points'][0][0] and seen['key_points'][0][1]==1600,seen['key_points']   # translated from the chapter's own slide text
    assert 'page' not in third['script'].lower()
    lectures=manager.manifest('course')['preparation']['lectures']
    assert lectures['status']=='ready' and lectures['completed_units']==3 and not lectures['error'],lectures
    assert seen['budgets']=={cr.LECTURE_MAX_TOKENS} and cr.LECTURE_MAX_TOKENS==2000
    assert sum(1 for unit,_ in seen['calls'] if unit=='Respiration 3')==len(cr.LECTURE_VARIANTS)
    assert seen['peak']==2,'two chapters should be written at once (peak %s)'%seen['peak']

    def lecture_calls():
        seen['calls'].clear()
        with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
            manager._prepare_lectures('course','gemma',model_call)
        return len(seen['calls'])
    assert lecture_calls()==0,'finished chapters are not visited again'
    chunk_rows=[json.loads(x) for x in (manager._course_dir('course')/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
    def fingerprint_of(unit_id):return cr._lecture_fingerprint([row for row in chunk_rows if row['unit_id']==unit_id])
    # While chapters are still arriving, a chapter that failed waits for the final pass (no repeated calls);
    # the final pass writes its key points without asking the model again.
    manager._update_manifest('course',lambda m:(m.update(ingestion_complete=False),m['units'][2].update(script='',script_status='error',script_attempts=1,
        script_failure='content',script_kind=None,script_error='rejected',script_evidence=fingerprint_of('unit3'))))
    assert lecture_calls()==0
    assert manager.manifest('course')['units'][2]['script_status']=='error'
    manager._update_manifest('course',lambda m:m.update(ingestion_complete=True))
    assert lecture_calls()==0
    third=manager.manifest('course')['units'][2]
    assert third['script_status']=='ready' and third['script_kind']=='source_excerpts' and third['script'],third
    assert manager.manifest('course')['preparation']['lectures']['status']=='ready'
    assert lecture_calls()==0

    # -- a chapter that already failed on exactly this text is not asked again ----
    manager._update_manifest('course',lambda m:m['units'][1].update(script='',script_status='error',script_attempts=2,
        script_error='Ollama stream ended before completion',script_failure='transport',script_kind=None,script_evidence=fingerprint_of('unit2')))
    assert lecture_calls()==0,'no model call and no waiting for a chapter that already failed on this text'
    second=manager.manifest('course')['units'][1]
    assert second['script_status']=='ready' and second['script_kind']=='source_excerpts' and second['script'],second
    # New material for it: the model writes it again.
    manager._update_manifest('course',lambda m:m['units'][1].update(script='',script_status='error',script_attempts=2,script_failure='content',
        script_error='rejected',script_evidence=None,script_kind=None))
    assert lecture_calls()>0
    assert manager.manifest('course')['units'][1]['script_status']=='ready'
    # A chapter marked "too little text" before every chapter had a lecture gets one too.
    manager._update_manifest('course',lambda m:m['units'][1].update(script='',script_status='skipped'))
    assert lecture_calls()==0 and manager.manifest('course')['units'][1]['script_status']=='ready'

    # -- automatic retries continue while they make progress ----------------
    progress_restarts=[]
    real_start=manager._start_preparation
    manager._start_preparation=lambda cid,key,call,force=False,only=None:progress_restarts.append(only)
    manager._update_manifest('course',lambda m:m['preparation'].update(
        questions={'status':'partial','ready_units':13,'auto_retries':3,'auto_retry_done':12,'auto_retry_at':0}))
    assert manager._retry_stopped(model_call,now=10**9)==[('course',('questions',))],'progress since the last retry: keep retrying'
    manager._update_manifest('course',lambda m:m['preparation']['questions'].update(auto_retries=3))
    assert manager._retry_stopped(model_call,now=10**9+10**6)==[],'no progress after three retries: stop'
    # New material from figure readings: chapters without questions or a written lecture are tried again.
    progress_restarts.clear()
    manager._update_manifest('course',lambda m:m['units'][0].update(script_kind='source_excerpts'))
    manager._after_new_material('course','gemma',model_call)
    assert ('lectures',) in progress_restarts and ('questions',) in progress_restarts
    assert manager.manifest('course')['preparation']['questions']['auto_retries']==0
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_kind='written'),m['preparation']['questions'].update(status='ready')))
    del manager._start_preparation

    # -- cleaner lecture material ---------------------------------------------
    fragments='Gene\nexpression\nRNA-seq\nreads\nalignment\nFold change\np < 0.05\nHeatmap'
    sentences='RNA sequencing measures how strongly each gene is expressed in a sample. Reads are aligned to the genome before they are counted for every gene.'
    assert cr._passage_quality(fragments)<0.25<=cr._passage_quality(sentences)
    rows=[{'text':fragments}]*6+[{'text':sentences+' '+sentences}]*3
    assert 'Heatmap' not in cr._lecture_evidence({},rows,6200,0.45) and 'Heatmap' in cr._lecture_evidence({},rows,6200,0.0)
    # Slide fragments still give the chapter its own slide points (unchanged, nothing added).
    outline=cr._source_excerpt_lecture({},[{'text':fragments},{'text':fragments}])
    assert outline.startswith('Slide points from the course material') and 'Heatmap' in outline and 'Fold change' in outline
    assert outline.count('Heatmap')==1 and 'p < 0.05' not in outline,'each point once; numbers alone are dropped'
    assert cr._source_excerpt_lecture({},[{'text':'1\n2\n--\n3'}])=='','nothing readable: nothing to show'

    # -- one chapter's questions do not stop the others ----------------------
    def build(unit,chunks,sentences,existing,key,call,save,target,max_new_facts=None):
        if unit['title']=='Respiration 3':raise RuntimeError('model output was not valid JSON')
        cards=[{'question':{'generation_method':cr.QUESTION_GENERATION,'stem':'q%d'%n}} for n in range(target)]
        save(cards,target,target);return cards
    with patch.object(cr,'build_exam_questions',build):
        manager._prepare_questions('course','gemma',model_call)
    current=manager.manifest('course')
    questions=current['preparation']['questions']
    assert questions['status']=='partial' and questions['ready_units']==2 and 'Respiration 3' in questions['error'],questions
    assert current['status']!='error','the course stays usable'

    # -- what needs reading again: only pages with no text, not OCR pages ---
    ocr_page={'page':4,'status':'partial','ocr':{'ok':True}}
    blank={'page':5,'status':'needs_review','vision':{'ok':False,'exhausted':True}}
    unread={'page':6,'status':'needs_review','vision':{'ok':False,'error':'vision model unavailable'}}
    summary=cr._review_summary({'unit1':{'warnings':[ocr_page]*900,'status':'partial'}})
    assert not summary['required'] and summary['unread_pages']==0,'pages read by OCR are usable'
    summary=cr._review_summary({'unit1':{'warnings':[ocr_page,blank],'status':'partial'}})
    assert not summary['required'] and summary['unread_pages']==1 and summary['retryable_pages']==0,'a blank page is not read again'
    summary=cr._review_summary({'unit1':{'warnings':[ocr_page,blank,unread],'status':'partial'}})
    assert summary['required'] and summary['retryable_pages']==1

    # A page every reader already failed on is not read again with the same readers.
    import pdf_ingestion
    calls={'ocr':0,'vision':0}
    class NoOCR(object):
        fingerprint='ocr-1';requested='eng';error='unavailable'
        def read(self,page):calls['ocr']+=1;return {'ok':False,'error':'no text'}
    class NoVision(object):
        fingerprint='vision-1'
        def __init__(self,exhausted):self.exhausted=exhausted
        def read(self,page):calls['vision']+=1;return {'ok':False,'error':'nothing verifiable','exhausted':self.exhausted}
    blank_doc=fitz.open();picture=blank_doc.new_page()
    pixels=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,40,40),False);pixels.clear_with(180)
    picture.insert_image(fitz.Rect(100,100,400,400),pixmap=pixels)   # an image-only page
    cache=folder/'page-cache'
    for exhausted,expected in ((False,1),(False,2),(True,3),(True,3)):
        list(pdf_ingestion.iter_pages(blank_doc,1,1,cache,'blank',ocr=NoOCR(),vision=NoVision(exhausted)))
        assert calls['vision']==expected,(exhausted,calls)
    blank_doc.close()

    # -- automatic re-reading of pages without readable text ----------------
    launched=[]
    manager._launch_repair=lambda cid,key,call:launched.append(cid)
    manager._update_manifest('course',lambda m:m.update(source_review=cr._review_summary({'unit3':{'warnings':[ocr_page]*50,'status':'partial'}})))
    manager._auto_repair('course','gemma',model_call)
    assert launched==[],'OCR pages alone never trigger a re-read'
    manager._update_manifest('course',lambda m:m.update(source_review=cr._review_summary({'unit3':{'warnings':[ocr_page,unread],'status':'partial'}})))
    manager._auto_repair('course','gemma',model_call);manager._auto_repair('course','gemma',model_call)
    assert launched==['course'],'the unread pages are read again once, automatically'

    # -- Retry restarts only the stopped steps -------------------------------
    restarted=[]
    manager._start_preparation=lambda cid,key,call,force=False,only=None:restarted.append(only)
    manager._update_manifest('course',lambda m:(m.update(source_review=cr._review_summary({'unit3':{'warnings':[ocr_page]*900,'status':'partial'}})),
        m['preparation'].update(lectures={'status':'error','error':'x','auto_retries':3},questions={'status':'ready'},index={'status':'ready'},figures={'status':'ready'}),
        m['units'][2].update(script_status='error',script_attempts=2)))
    launched.clear();manager.retry_preparation('course','gemma',model_call)
    assert restarted==[('lectures',)] and launched==[],'Retry neither re-reads the PDF nor redoes finished steps'
    current=manager.manifest('course')
    assert current['preparation']['lectures']['auto_retries']==0 and current['units'][2]['script_attempts']==0
    # With pages that really have no text, those pages are read first (nothing else running).
    restarted.clear()
    manager._update_manifest('course',lambda m:m.update(source_review=cr._review_summary({'unit3':{'warnings':[ocr_page,unread],'status':'partial'}})))
    manager.retry_preparation('course','gemma',model_call)
    assert launched==['course'] and restarted==[]
    del manager._start_preparation

    # -- figure pages: reading variants -------------------------------------
    document=fitz.open();page=document.new_page()
    page.insert_textbox(fitz.Rect(40,40,550,200),'Oxygen diffuses from alveoli into pulmonary capillary blood. '*6)
    page.draw_rect(fitz.Rect(100,300,400,520),color=(0,0,0))
    vision=pdf_vision.LocalVision();vision.checked=True;vision.error=''
    assert pdf_vision.visual_clip(page) is not None
    order=[]
    def transcribe(results):
        def run(image,requests):
            order.append(len(image));outcome=results.pop(0)
            if isinstance(outcome,Exception):raise outcome
            return outcome,0,False
        return run
    with patch.object(vision,'_transcribe',transcribe([[],['Alveolar oxygen 100 mmHg']])):
        result=vision.read(page)
    assert result['ok'] and result['variant']=='page' and len(order)==2
    order.clear()
    with patch.object(vision,'_transcribe',transcribe([[],[],['top half table'],[]])):
        result=vision.read(page)
    assert result['ok'] and result['variant']=='halves' and result['text']=='top half table' and len(order)==4
    with patch.object(vision,'_transcribe',transcribe([[],[],[],[]])):
        result=vision.read(page)
    assert not result['ok'] and result['exhausted'] is True
    with patch.object(vision,'_transcribe',transcribe([RuntimeError('Ollama HTTP 500')])):
        result=vision.read(page)
    assert not result['ok'] and result['exhausted'] is False,'a model/connection failure is read again later'
    document.close()

    # -- figure step: exhausted pages are not read again; never an error ----
    reads=[]
    class StandInVision(object):
        fingerprint='stand-in';error=''
        def probe(self):return True
        def read(self,page):
            reads.append(page.number+1)
            if page.number==0:return {'ok':False,'error':'no verifiable content','exhausted':True}
            return {'ok':True,'text':'Figure: the oxygen gradient falls from alveolus to capillary blood.'}
    manager._update_manifest('course',lambda m:m.update(figure_pages=[
        {'unit_id':'unit1','source_file':m['sources'][0]['file_name'],'page':1},
        {'unit_id':'unit2','source_file':m['sources'][0]['file_name'],'page':2}]))
    manager._wait_for_lectures=lambda cid:None
    manager._index_missing_chunks=lambda cid:0
    manager._drop_indexed_chunks=lambda cid,ids=None:None
    new_material=[];manager._after_new_material=lambda cid,key,call,units=None:new_material.append(cid)
    with patch.object(cr,'LocalVision',StandInVision):
        manager._prepare_figures('course','gemma',model_call)
        figures=manager.manifest('course')['preparation']['figures']
        assert figures['status']=='ready' and figures['pages_read']==1 and figures['pages_unverified']==1,figures
        assert reads==[1,2]
        assert new_material==['course'],'figure passages make stalled chapters try again'
        reads.clear();manager._prepare_figures('course','gemma',model_call)
        assert reads==[],'pages are not read again with the same model'
    del manager._after_new_material

    # -- stopped preparation starts again by itself -------------------------
    restarts=[]
    manager._start_preparation=lambda cid,key,call,force=False,only=None:restarts.append(only)
    manager._update_manifest('course',lambda m:m['preparation'].update(
        questions={'status':'error','error':'Selected model could not draft exam questions: connection refused'},
        index={'status':'ready','auto_retries':2},lectures={'status':'ready'}))
    period=cr.PREPARATION_RETRY_SECONDS
    assert manager._retry_stopped(model_call,now=10000)==[('course',('questions',))]
    assert manager._retry_stopped(model_call,now=10000+period)==[],'waits longer after each retry'
    assert manager._retry_stopped(model_call,now=10000+2*period+1)==[('course',('questions',))]
    assert manager._retry_stopped(model_call,now=10000+7*period)==[('course',('questions',))]
    assert manager._retry_stopped(model_call,now=10000+100*period)==[],'at most three automatic retries'
    prep=manager.manifest('course')['preparation']
    assert prep['questions']['auto_retries']==cr.PREPARATION_RETRIES and prep['index']['auto_retries']==0
    del manager._start_preparation

    # -- a restarting model server pauses preparation instead of failing it --
    import urllib.error
    attempts=[]
    def flaky():
        attempts.append(1)
        if len(attempts)<3:raise urllib.error.URLError(ConnectionRefusedError(111,'Connection refused'))
        return 'drafted'
    with patch.dict(os.environ,{'VQ_TRANSIENT_FIRST_DELAY_SEC':'0.05'}):
        with oc.BackgroundWork():assert oc.retry_transient(flaky)=='drafted' and len(attempts)==3
        attempts.clear()
        try:oc.retry_transient(flaky);raise AssertionError('students are not kept waiting by retries')
        except urllib.error.URLError:pass
        with oc.BackgroundWork():
            try:oc.retry_transient(lambda:(_ for _ in ()).throw(ValueError('rejected draft')));raise AssertionError
            except ValueError:pass   # content failures are not retried here
            try:oc.retry_transient(lambda:(_ for _ in ()).throw(TimeoutError('step limit')));raise AssertionError
            except TimeoutError:pass

    # -- slide chapters: the text around them is the context, images are not forced ---
    folder_course=manager._course_dir('course')
    manager._update_manifest('course',lambda m:(
        m['units'][0].update(question_count=5,script_status='ready',script_kind='written',script_review_revision=cr.REVIEW_REVISION),
        m['units'][1].update(question_count=5,script_status='ready',script_kind='written',script_review_revision=cr.REVIEW_REVISION),
        m['units'][2].update(question_count=0,script_status='error',script='',script_error='rejected',script_failure='content',script_kind=None,script_attempts=0)))
    keywords='Transcription\nRNA polymerase\nPromoter\nmRNA\nSplicing\nIntrons and exons\nEnhancers'
    prose=('Transcription copies a gene into messenger RNA. RNA polymerase binds the promoter and builds the transcript. '
           'Introns are removed by splicing and the exons are joined into mature messenger RNA. ')*4
    rows=[json.loads(x) for x in (folder_course/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
    for row in rows:
        row['text']=keywords if row['unit_id']=='unit3' else prose+row['unit_id']
    (folder_course/'chunks.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    by_unit={}
    for row in rows:by_unit.setdefault(row['unit_id'],[]).append(row)
    units_now=manager.manifest('course')['units']
    near=cr._neighbor_chunks(units_now,by_unit,2)
    assert near and all(c['unit_id'] in ('unit1','unit2') for c in near),'the chapters before it, never its own keywords'
    assert cr._neighbor_chunks(units_now,{'unit1':[{'text':keywords}],'unit2':[{'text':keywords}]},2)==[],'keywords are not context'
    assert cr._own_sentence_text(by_unit['unit3'])==0 and cr._own_sentence_text(by_unit['unit1'])>400
    evidence=cr._context_evidence(by_unit['unit3'],near)
    assert evidence.index('Transcription\nRNA polymerase')<evidence.index('【前後章節教材】') and 'Introns are removed' in evidence

    # The lecture of such a chapter is written at once from that context (one model call), not after failed variants.
    seen_calls=[]
    def context_model(key,system,user,limit):
        seen_calls.append(user)
        return CHINESE
    manager._update_manifest('course',lambda m:m.update(ingestion_complete=True))
    with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        manager._prepare_lectures('course','gemma',context_model)
    unit=manager.manifest('course')['units'][2]
    assert unit['script_status']=='ready' and unit['script_kind']=='context' and unit['script_variant']=='context',unit
    assert len(seen_calls)==1 and '【前後章節教材】' in seen_calls[0] and 'RNA polymerase' in seen_calls[0]
    # Finished lectures count from the start: a slow first chapter never shows the row as 0 / N.
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_status='error',script='',script_failure='transport',script_error='timeout',script_kind=None,script_evidence=None),
        m['units'][2].update(script_status='ready',script=CHINESE,script_kind='written',script_review_revision=cr.REVIEW_REVISION,script_evidence='x'),
        m['preparation']['lectures'].update(status='partial',completed_units=0)))
    shown=[]
    def slow_first(key,system,user,limit):
        shown.append(manager.manifest('course')['preparation']['lectures']['completed_units']);return CHINESE
    with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        manager._prepare_lectures('course','gemma',slow_first)
    assert shown and shown[0]==2,'two of three were already written: %s'%shown
    assert manager.manifest('course')['preparation']['lectures']['completed_units']==3
    # A written chapter is not written again when images are read later.
    started=[];manager._start_preparation=lambda cid,key,call,force=False,only=None:started.append(only)
    manager._update_manifest('course',lambda m:m['preparation'].update(lectures={'status':'ready'},questions={'status':'ready'}))
    manager._after_new_material('course','gemma',model_call)
    assert started==[],'finished chapters are left alone'
    del manager._start_preparation

    # Questions use the same context when a chapter has no sentences of its own.
    manager._update_manifest('course',lambda m:m['units'][2].update(question_count=0))
    material=[]
    def build_context(unit,chunks,sentences,existing,key,call,save,target,max_new_facts=None):
        material.append((unit['id'],list(chunks)))
        cards=[{'question':{'generation_method':cr.QUESTION_GENERATION,'stem':'q%d'%n}} for n in range(target)]
        save(cards,target,target);return cards
    banks_path=folder_course/'question_banks.json';banks_path.write_text('{}',encoding='utf-8')
    with patch.object(cr,'build_exam_questions',build_context):
        manager._prepare_questions('course','gemma',model_call)
    third=[chunks for uid,chunks in material if uid=='unit3'][0]
    assert any('Introns are removed' in c['text'] for c in third) and all(c['unit_id']=='unit3' for c in third)
    first=[chunks for uid,chunks in material if uid=='unit1'][0]
    assert all(c['unit_id']=='unit1' for c in first),'chapters with their own sentences use only those'

    # A chapter that is still without practice questions after every automatic retry is counted out, without
    # waiting for figure reading; its lecture page is never skipped.
    manager._update_manifest('course',lambda m:(m['units'][2].update(script_status='ready',script=CHINESE,script_kind='written',question_count=0),
        m['preparation'].update(figures={'status':'waiting','auto_retries':0},lectures={'status':'ready','completed_units':3,'total_units':3},
                                 questions={'status':'partial','auto_retries':1,'ready_units':2})))
    assert not manager._settle('course'),'questions keep their automatic retries until they are used up'
    manager._update_manifest('course',lambda m:m['preparation']['questions'].update(auto_retries=3))
    assert manager._settle('course')
    current=manager.manifest('course')
    assert current['preparation']['questions']['status']=='ready' and current['preparation']['questions']['ready_units']==2
    assert current['units'][2]['question_skipped'] is True and current['units'][2]['script_status']=='ready'
    # New material for a settled chapter writes it again; a settled chapter is not retried without any.
    started=[];manager._start_preparation=lambda cid,key,call,force=False,only=None:started.append(only)
    manager._after_new_material('course','gemma',model_call,units={'unit3'})
    assert ('questions',) in started and ('lectures',) not in started,'a written lecture is not written again'
    assert 'question_skipped' not in manager.manifest('course')['units'][2]
    manager._update_manifest('course',lambda m:m['units'][2].update(script_kind='source_excerpts'))
    started.clear();manager._after_new_material('course','gemma',model_call,units={'unit3'})
    assert ('lectures',) in started,'key points are replaced by a written lecture when new material arrives'
    del manager._start_preparation

    # A slide chapter whose lecture the model keeps rejecting still ends with its own slide points
    # (the model is busy for the short Chinese call here: the page keeps the slides' own words).
    seen['key_point_mode']='busy'
    manager._update_manifest('course',lambda m:m['units'][2].update(script_status='error',script='',script_failure='content',script_attempts=1,script_evidence=None,script_kind=None))
    rows=[json.loads(x) for x in (folder_course/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
    for row in rows:
        if row['unit_id']=='unit3':row['text']='Transcription\nRNA polymerase\nPromoter\nmRNA\nSplicing\nIntrons and exons\nEnhancers'
    (folder_course/'chunks.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    manager._update_manifest('course',lambda m:m.update(ingestion_complete=True))
    with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        manager._prepare_lectures('course','gemma',model_call)
    unit=manager.manifest('course')['units'][2]
    assert unit['script_status']=='ready' and unit['script_kind']=='source_excerpts',unit
    assert unit['script'].startswith('Slide points from the course material') and 'RNA polymerase' in unit['script']
    lectures=manager.manifest('course')['preparation']['lectures']
    assert lectures['status']=='partial' and 'put into Chinese again' in lectures['error'],lectures   # the automatic retries put it into Chinese; no restart needed
    seen['key_point_mode']='chinese'
    with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        manager._prepare_lectures('course','gemma',model_call)
    assert manager.manifest('course')['units'][2]['script'].startswith('本章重點') and manager.manifest('course')['preparation']['lectures']['status']=='ready'
    seen['key_point_mode']='busy'
    manager._update_manifest('course',lambda m:m['units'][2].update(script='Slide points from the course material\n\n- Transcription\n- RNA polymerase\n- Promoter\n- mRNA',script_status='ready',script_kind='source_excerpts',script_review_revision=cr.REVIEW_REVISION,script_evidence=fingerprint_of('unit3')))

    # A chapter whose slides hold only a few words still gets a lecture page made of its OWN words: nothing from
    # other chapters is put under its name (a title slide once showed another lecture's cancer points).
    sparse={'id':'unit9','title':'Transcriptome'}
    page=cr._key_points_lecture(sparse,[{'text':'Seminar of Genomic Study\n2025 Spring Semester\nLiang-Chuan Lai\nEmail: llai@ntu.edu.tw\nPh: 23123456 ext. 288241'}])
    assert page.startswith('Slide points from the course material') and 'Seminar of Genomic Study' in page
    assert 'llai@ntu.edu.tw' in page and 'Ph: 23123456 ext. 288241' in page and 'chapters around' not in page,page   # the teacher's contact lines stay
    assert cr._key_points_lecture(sparse,[{'text':'1\n2'}])=='Key points from the course material: Transcriptome','nothing but the title: no remark about missing text'
    assert cr._key_points_lecture({'id':'u','title':'轉錄體'},[])=='本章重點（摘自教材原文）: 轉錄體'
    # Neighbouring pages come from the same PDF only.
    pdf_units=[{'id':'a','source_file':'transcriptome.pdf'},{'id':'b','source_file':'transcriptome.pdf'},{'id':'c','source_file':'cancer.pdf'}]
    pdf_rows={'a':[{'text':'x'*700}],'b':[{'text':'RNA polymerase binds the promoter and starts transcription of the gene. '*12}],'c':[{'text':'Human cancers increase as a function of age and genetic changes. '*12}]}
    around=cr._neighbor_chunks(pdf_units,pdf_rows,0)
    assert around and all('cancer' not in row['text'] for row in around),around
    assert cr._neighbor_chunks(pdf_units,pdf_rows,2)==[],'the only chapter of its PDF has nothing to borrow'
    # Pages saved earlier with borrowed lines, or written from other PDFs, are made again from the chapter's own words.
    borrowed='Key points from the course material: Transcriptome\n\n- Seminar of Genomic Study\n\nFrom the chapters around it\n- How Cancer Arises?'
    manager._update_manifest('course',lambda m:(m['units'][0].update(script=borrowed,script_status='ready',script_kind='source_excerpts',script_review_revision=cr.REVIEW_REVISION,
        script_evidence=fingerprint_of('unit1')),m['preparation']['lectures'].update(status='partial',completed_units=2,total_units=3)))
    assert lecture_calls()==0
    fixed=manager.manifest('course')['units'][0]
    assert 'chapters around' not in fixed['script'] and fixed['script_kind']=='source_excerpts',fixed
    # Key points are put into Chinese by one short call. A busy or careless model leaves the page in the slides' own
    # words (never a wait), and that page is made Chinese at the next start; Chinese slides need no call.
    topic={'id':'unit9','title':'Transcriptome'}
    slides=[{'text':'Transcription\nRNA polymerase\nPromoter\nmRNA\nSplicing\nIntrons and exons\nEnhancers'}]
    asked=[]
    def zh_model(key,system,user,budget):
        asked.append(user);return '- 轉錄由 RNA polymerase 啟動\n- 啟動子 (Promoter) 與增強子 (Enhancers) 調控基因表現\n\n- 剪接 (Splicing) 移除內含子 (introns)'
    def as_unit(page):return {'id':'x','script':page,'script_status':'ready','script_kind':'source_excerpts','script_kp':cr.KEY_POINTS_REVISION}
    page=cr._zh_key_points(topic,slides,'gemma',zh_model)
    assert page.startswith('本章重點：Transcriptome\n\n- 轉錄由 RNA polymerase 啟動\n- 啟動子') and page.count('\n- ')==3 and not cr._stale_lecture(as_unit(page)),page
    assert 'Promoter' in asked[0] and 'Enhancers' in asked[0],'the model is given the chapter\'s own slide text'
    def busy(*a):raise RuntimeError('model busy')
    for model in (busy,lambda *a:'- Ventilation matches perfusion\n- Oxygen diffuses into blood',lambda *a:'- 轉錄\n- Promoter and enhancers control genes',lambda *a:'好的，以下是整理：\n- '+'\ue000'*8,None):
        page=cr._zh_key_points(topic,slides,'gemma',model) if model else cr._zh_key_points(topic,slides,'gemma',None)
        assert page.startswith('Slide points from the course material') and 'RNA polymerase' in page and cr._stale_lecture(as_unit(page)),page
    asked.clear()
    chinese_slides=[{'text':'轉錄是以 DNA 為模板合成 RNA 的過程\n啟動子位於基因的上游\n剪接會移除內含子\n增強子可以提高轉錄效率'}]
    page=cr._zh_key_points(topic,chinese_slides,'gemma',zh_model)
    assert not asked and '增強子可以提高轉錄效率' in page and not cr._stale_lecture(as_unit(page)),'Chinese slides need no model call'
    assert not cr._stale_lecture({'id':'x','script':'Key points from the course material: Transcriptome','script_status':'ready','script_kind':'source_excerpts','script_kp':cr.KEY_POINTS_REVISION}),'a page with only a title is not retried at every start'
    # A saved English key-points page is made Chinese by that one call, without writing the lecture again.
    seen['key_point_mode']='chinese'
    manager._update_manifest('course',lambda m:(m['units'][1].update(script='Key points from the course material: Respiration 2\n\n- Oxygen diffuses from alveolar gas into blood',script_status='ready',
        script_kind='source_excerpts',script_review_revision=cr.REVIEW_REVISION,script_evidence=fingerprint_of('unit2')),m['preparation']['lectures'].update(status='partial',completed_units=2,total_units=3)))
    before=len(seen['key_points'])
    assert lecture_calls()==0,'no lecture is written again for it'
    chinese_page=manager.manifest('course')['units'][1]
    assert chinese_page['script'].startswith('本章重點：Respiration 2') and len(seen['key_points'])>before,chinese_page['script']   # (any other English page is put into Chinese in the same pass)
    # After a restart, a finished course whose saved pages hold another chapter's lines is queued again
    # (a "ready" step is otherwise never visited), and a clean course is left alone.
    manager._update_manifest('course',lambda m:(m['units'][0].update(script=borrowed,script_status='ready',script_kind='source_excerpts',script_review_revision=cr.REVIEW_REVISION,
        script_evidence=fingerprint_of('unit1')),m['preparation']['lectures'].update(status='ready',completed_units=3,total_units=3)))
    queued=[];real_start,real_supervise=manager._start_preparation,manager.supervise
    manager._start_preparation=lambda cid,key,call,force=False,only=None:queued.append(cid);manager.supervise=lambda call:None
    try:
        manager.resume_pending(model_call)
        assert queued==['course'] and manager.manifest('course')['preparation']['lectures']['status']=='partial',queued
        manager._update_manifest('course',lambda m:([u.update(script=CHINESE,script_kind='written',script_status='ready') for u in m['units']],m['preparation']['lectures'].update(status='ready'),m.update(status='ready')))
        queued.clear();manager.resume_pending(model_call)
        assert manager.manifest('course')['preparation']['lectures']['status']=='ready','clean lecture pages are not queued again'
    finally:manager._start_preparation,manager.supervise=real_start,real_supervise

    # The teacher renames units and arranges their order after the build. Preparation addresses units by position in
    # the stored list, so that list never moves: the arrangement is a separate list of ids that the overview, the
    # practice questions and the page follow.
    ids=[u['id'] for u in manager.manifest('course')['units']];assert len(ids)>=3,ids
    manager._update_manifest('course',lambda m:m['units'][0].update(script='Key points from the course material: Respiration 1\n\n- point',script_kind='source_excerpts',script_status='ready'))
    edited=manager.edit_units('course',{ids[0]:'  Transcriptome  '},[ids[2],ids[0],ids[1]]+ids[3:])
    assert [u['id'] for u in edited['units']]==ids,'the stored list is not reordered'
    assert edited['units'][0]['title']=='Transcriptome' and edited['units'][0]['script'].startswith('Key points from the course material: Transcriptome\n\n- point'),edited['units'][0]
    assert [u['id'] for u in cr.ordered_units(edited)]==[ids[2],ids[0],ids[1]]+ids[3:]
    chapters=manager.overview_context('course').splitlines()
    assert chapters[2].startswith('1. '+edited['units'][2]['title']) and chapters[3].startswith('2. Transcriptome'),chapters
    for bad in ({'titles':{'missing':'x'}},{'titles':{ids[0]:'   '}},{'order':ids[:-1]},{'order':ids+[ids[0]]},{'order':'nope'}):
        try:manager.edit_units('course',bad.get('titles'),bad.get('order'));raise AssertionError('accepted '+str(bad))
        except ValueError:pass
    assert manager.manifest('course')['units'][0]['title']=='Transcriptome' and manager.manifest('course')['unit_order'][0]==ids[2],'a refused change leaves the course as it was'
    try:manager.edit_units('no-such-course',None,None);raise AssertionError('unknown course')
    except ValueError:pass

    # A context lecture saved before neighbours were limited to one PDF may hold another topic: it is made again once.
    manager._update_manifest('course',lambda m:(m['units'][0].update(script=CHINESE,script_status='ready',script_kind='context',script_context=None),
        m['preparation']['lectures'].update(status='partial',completed_units=2,total_units=3)))
    lecture_calls()
    again=manager.manifest('course')['units'][0]
    assert again['script_status']=='ready' and (again.get('script_context')=='same-pdf' or again['script_kind']!='context'),again
    manager._update_manifest('course',lambda m:m['units'][0].update(script=CHINESE,script_status='ready',script_kind='written'))

    # A title slide's lines, the teacher's e-mail address and phone number included, are shown as they are on the slide.
    title_slide=[{'text':'Seminar of Genomic Study\n2025 Spring Semester\nLiang-Chuan Lai\nEmail: llai@ntu.edu.tw\nPh: 23123456 ext. 288241'}]
    assert 'llai@ntu.edu.tw' in cr._source_outline_lecture(title_slide) and '288241' in cr._source_excerpt_lecture({},title_slide)
    kept=lambda *a:'- 研討會：Seminar of Genomic Study\n- 2025 春季學期\n- 賴亮全 (Liang-Chuan Lai)\n- 電子郵件：llai@ntu.edu.tw\n- 電話：23123456 分機 288241'
    page=cr._zh_key_points(sparse,title_slide,'gemma',kept)
    assert page.startswith('本章重點：Transcriptome') and 'llai@ntu.edu.tw' in page and '288241' in page,page   # in Chinese, contact lines kept
    saved={'id':'x','script':page,'script_status':'ready','script_kind':'source_excerpts'}
    assert cr._stale_lecture(saved),'a key-points page written before contact lines were kept is written again once'
    saved['script_kp']=cr.KEY_POINTS_REVISION
    assert not cr._stale_lecture(saved)
    assert cr._source_outline_lecture([{'text':'Transcription\nRNA polymerase\nPromoter\nmRNA\nSplicing'}]).startswith('Slide points')
    # "What is this whole course about?" is answered from the chapter list, not from one searched passage.
    for question in ('可以方便跟我講一下就是這一這整本的內容大概是在講什麼','這門課主要在教什麼','What is this whole course about?','Give me an overview of this book'):
        assert cr._is_overview_question(question),question
    for question in ('甚麼是machine learning','什麼是轉錄體','What is splicing?','這個基因的功能是什麼'):
        assert not cr._is_overview_question(question),question
    overview=manager.overview_context('course')
    assert overview.startswith('Course:') and 'Chapters in order:' in overview,overview
    found=manager.search('course','這整本在講什麼')
    assert found['in_scope'] and found['ok'] and found['context'].startswith('Course:') and 'Chapters in order:' in found['context'] and found['reason']=='course-overview',found
    assert found['retrieval'].get('overview') is True
    # Passages are kept whole and the total stays inside the model's input budget; a chapter question is still searched.
    with patch.object(cr.CourseManager,'_search_passages',lambda self,cid,q:{'ok':True,'in_scope':True,'context':'\n\n'.join('[Evidence %d | a | u | PDF page 1]\n%s'%(i,'x'*1500) for i in range(1,7)),'sources':[{'chunk_id':'c'}],'retrieval':{'selected_count':6},'course':manager.manifest('course'),'top_similarity':0.5}):
        combined=manager.search('course','這本書第三章的重點是什麼')
    assert combined['context'].startswith('Course:') and '[Evidence 1 |' in combined['context'] and len(combined['context'])<=cr.CONTEXT_BUDGET
    assert combined['context'].count('[Evidence ')<6 and combined['sources']==[{'chunk_id':'c'}]
    with patch.object(cr.CourseManager,'_search_passages',lambda self,cid,q:(_ for _ in ()).throw(RuntimeError('embedding service down'))):
        assert manager.search('course','這整本在講什麼')['in_scope']

    # -- OCR: 4 at once while quiet, 2 while students ask -------------------
    with patch.object(cpu_budget,'available_cpus',lambda:8),patch.dict(os.environ,{'VQ_CPU_WORKERS':'4','VQ_CPU_WORKERS_BUSY':'2','VQ_CPU_BUSY_WINDOW_SEC':'5'}):
        quiet=Path(oc.ACTIVITY_PATH)
        if quiet.exists():quiet.unlink()
        assert cpu_budget.cpu_workers()==4
        oc.note_student_activity()
        assert cpu_budget.cpu_workers()==2
        with patch.dict(os.environ,{'VQ_CPU_BUSY_WINDOW_SEC':'0'}):assert cpu_budget.cpu_workers()==4
print('PASS automatic recovery: image-heavy chapters are written from the text around them (lecture and questions) without forced image reading, every chapter always has a lecture page (written by the model, else its own key points; never from another chapter) with no waiting or retries; one chapter never stops the others, rejected lectures are asked differently with cleaner material first, chapters prepare in parallel, a failed chapter waits for the final pass only while chapters are still arriving, figures try crop/page/halves and never block, only pages with no text are re-read (once, not blank pages), Retry restarts only stopped steps, stopped steps restart by themselves (3 times, growing pauses), a restarting model server only pauses preparation, OCR 4 quiet / 2 busy')
