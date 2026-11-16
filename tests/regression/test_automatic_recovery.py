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
    guard=threading.Lock();seen={'active':0,'peak':0,'calls':[],'budgets':set()}
    def model_call(key,system,user,budget):
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
    assert units['unit3']['script_status']=='error' and units['unit3']['script_attempts']==1
    lectures=manager.manifest('course')['preparation']['lectures']
    assert lectures['status']=='partial' and 'Respiration 3' in lectures['error'],'one chapter does not stop the step'
    assert seen['budgets']=={cr.LECTURE_MAX_TOKENS} and cr.LECTURE_MAX_TOKENS==2000
    assert sum(1 for unit,_ in seen['calls'] if unit=='Respiration 3')==len(cr.LECTURE_VARIANTS)
    assert seen['peak']==2,'two chapters should be written at once (peak %s)'%seen['peak']

    def lecture_calls():
        seen['calls'].clear()
        with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
            manager._prepare_lectures('course','gemma',model_call)
        return len(seen['calls'])
    # While chapters are still arriving, the failed chapter is not retried.
    manager._update_manifest('course',lambda m:m.update(ingestion_complete=False))
    assert lecture_calls()==0
    # Final pass: one more full set of variants, then it stops.
    manager._update_manifest('course',lambda m:m.update(ingestion_complete=True))
    assert lecture_calls()==len(cr.LECTURE_VARIANTS)
    third=manager.manifest('course')['units'][2]
    assert third['script_attempts']==2
    # Written twice, rejected twice: the chapter's own key sentences (no page numbers, nothing invented).
    assert third['script_status']=='ready' and third['script_kind']=='source_excerpts',third
    assert third['script'].startswith('Key points from the course material') and 'Oxygen diffuses from alveolar gas' in third['script']
    assert 'page' not in third['script'].lower()
    assert manager.manifest('course')['preparation']['lectures']['status']=='ready'
    assert lecture_calls()==0

    # -- a model/connection problem never counts as a failed attempt --------
    manager._update_manifest('course',lambda m:m['units'][1].update(script='',script_status='error',script_attempts=2,
        script_error='Ollama stream ended before completion',script_failure=None,script_kind=None))
    assert lecture_calls()>0,'a chapter stopped by the model server is written again'
    assert manager.manifest('course')['units'][1]['script_status']=='ready'
    # A chapter rejected twice before the fallback existed gets its key sentences without another model call.
    manager._update_manifest('course',lambda m:m['units'][1].update(script='',script_status='error',script_attempts=2,
        script_error='The local model did not produce a readable Traditional Chinese lecture.',script_failure='content',
        script_evidence=None))
    lecture_calls()
    second=manager.manifest('course')['units'][1]
    assert second['script_status']=='ready',second

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
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_status='error',script='',script_failure='transport',script_error='timeout',script_kind=None),
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

    # A chapter that is still without lecture or questions after every retry and all its pages
    # were read is settled: counted out, and the course is complete.
    manager._update_manifest('course',lambda m:(m['units'][2].update(script_status='error',script_failure='content',script_error='rejected',script_attempts=2,question_count=0),
        m['preparation'].update(figures={'status':'waiting','auto_retries':0},lectures={'status':'partial','auto_retries':3,'completed_units':2,'total_units':3},
                                 questions={'status':'partial','auto_retries':3,'ready_units':2})))
    assert not manager._settle('course'),'not while figure reading may still add material'
    manager._update_manifest('course',lambda m:m['preparation'].update(figures={'status':'ready'}))
    manager._update_manifest('course',lambda m:m['preparation']['questions'].update(auto_retries=1))
    assert manager._settle('course')
    current=manager.manifest('course')
    assert current['preparation']['lectures']['status']=='ready' and current['preparation']['lectures']['total_units']==2
    assert current['units'][2]['script_status']=='skipped'
    assert current['preparation']['questions']['status']=='partial','questions keep their automatic retries until they are used up'
    manager._update_manifest('course',lambda m:m['preparation']['questions'].update(auto_retries=3))
    assert manager._settle('course')
    current=manager.manifest('course')
    assert current['preparation']['questions']['status']=='ready' and current['preparation']['questions']['ready_units']==2
    assert current['units'][2]['question_skipped'] is True
    # New material for a settled chapter writes it again; a settled chapter is not retried without any.
    started=[];manager._start_preparation=lambda cid,key,call,force=False,only=None:started.append(only)
    manager._after_new_material('course','gemma',model_call,units={'unit3'})
    assert ('lectures',) in started and ('questions',) in started
    assert 'question_skipped' not in manager.manifest('course')['units'][2]
    del manager._start_preparation

    # A slide chapter whose lecture the model keeps rejecting still ends with its own slide points.
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
    assert manager.manifest('course')['preparation']['lectures']['status']=='ready'

    # A chapter the model could not write because of a model or connection problem, after every automatic
    # retry, ends with its own key sentences at once: no further model call, the course is complete.
    rows=[json.loads(x) for x in (folder_course/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
    mine=[row for row in rows if row['unit_id']=='unit1']
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_status='error',script='',script_failure='transport',
        script_error='stream ended before completion',script_kind=None,script_evidence=cr._lecture_fingerprint(mine)),
        m['preparation'].update(figures={'status':'ready'},lectures={'status':'partial','auto_retries':3,'completed_units':2,'total_units':3})))
    asked=[]
    def broken_model(key,system,user,limit):
        asked.append(user);raise RuntimeError('stream ended before completion')
    with patch.object(cr,'review_text',lambda kind,script,*a,**k:(script,{})):
        manager._prepare_lectures('course','gemma',broken_model)
    unit=manager.manifest('course')['units'][0]
    assert not asked,'settled before any new model attempt'
    assert unit['script_status']=='ready' and unit['script_kind']=='source_excerpts' and unit['script'],unit
    lectures=manager.manifest('course')['preparation']['lectures']
    assert lectures['status']=='ready' and lectures['completed_units']==3,lectures
    # Retries not used up yet: the model gets its turns first.
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_status='error',script='',script_failure='transport',script_kind=None,
        script_evidence=cr._lecture_fingerprint(mine)),m['preparation']['lectures'].update(status='partial',auto_retries=1)))
    assert manager._settle('course',early=True) is False
    assert manager.manifest('course')['units'][0]['script_status']=='error'
    # New material for the chapter: the model tries again before any key sentences.
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_evidence='older text'),m['preparation']['lectures'].update(auto_retries=3)))
    manager._settle('course',early=True)
    assert manager.manifest('course')['units'][0]['script_status']=='error'
    manager._update_manifest('course',lambda m:(m['units'][0].update(script_status='ready',script=CHINESE,script_kind='written',script_failure='',script_error=''),
        m['preparation']['lectures'].update(status='ready')))

    # A title slide gives no lecture: a teacher's e-mail address and phone number are not teaching material.
    title_slide=[{'text':'Seminar of Genomic Study\n2025 Spring Semester\nLiang-Chuan Lai\nEmail: llai@ntu.edu.tw\nPh: 23123456 ext. 288241'}]
    assert cr._source_outline_lecture(title_slide)=='' and '@' not in cr._source_excerpt_lecture({},title_slide)
    assert cr._source_outline_lecture([{'text':'Transcription\nRNA polymerase\nPromoter\nmRNA\nSplicing'}]).startswith('Slide points')
    # "What is this whole course about?" is answered from the chapter list, not from one searched passage.
    for question in ('可以方便跟我講一下就是這一這整本的內容大概是在講什麼','這門課主要在教什麼','What is this whole course about?','Give me an overview of this book'):
        assert cr._is_overview_question(question),question
    for question in ('甚麼是machine learning','什麼是轉錄體','What is splicing?','這個基因的功能是什麼'):
        assert not cr._is_overview_question(question),question
    overview=manager.overview_context('course')
    assert overview.startswith('Course:') and 'Chapters in order:' in overview,overview
    found=manager.search('course','這整本在講什麼')
    assert found['in_scope'] and found['ok'] and found['context']==manager.overview_context('course') and found['reason']=='course-overview'

    # -- OCR: 4 at once while quiet, 2 while students ask -------------------
    with patch.object(cpu_budget,'available_cpus',lambda:8),patch.dict(os.environ,{'VQ_CPU_WORKERS':'4','VQ_CPU_WORKERS_BUSY':'2','VQ_CPU_BUSY_WINDOW_SEC':'5'}):
        quiet=Path(oc.ACTIVITY_PATH)
        if quiet.exists():quiet.unlink()
        assert cpu_budget.cpu_workers()==4
        oc.note_student_activity()
        assert cpu_budget.cpu_workers()==2
        with patch.dict(os.environ,{'VQ_CPU_BUSY_WINDOW_SEC':'0'}):assert cpu_budget.cpu_workers()==4
print('PASS automatic recovery: image-heavy chapters are written from the text around them (lecture and questions) without forced image reading, finally settled (never stuck) and keep their own slide points as a lecture; one chapter never stops the others (partial), rejected lectures are asked differently with cleaner material and finally become the chapter key sentences, chapters prepare in parallel, failed chapters wait for the final pass, figures try crop/page/halves and never block, only pages with no text are re-read (once, not blank pages), Retry restarts only stopped steps, stopped steps restart by themselves (3 times, growing pauses), a restarting model server only pauses preparation, OCR 4 quiet / 2 busy')
