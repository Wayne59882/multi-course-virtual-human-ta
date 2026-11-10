#!/usr/bin/env python3
from __future__ import print_function
from work_steps import completed_call, metric, model_signature
import gpu_policy
from math_integrity import protect_math, restore_math, loads_model_json
from content_integrity import clean_course_text, SOURCE_RULE
import argparse, json, os, re, threading, time, urllib.request, uuid
try:
    import fcntl
except ImportError:
    fcntl=None
from http.server import SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn,HTTPServer): daemon_threads=True
from pathlib import Path
from static_assets import SharedAssetsMixin
from reference_search import ReferenceSearch
from scope_guard import decision as scope_decision
from feedback.feedback_service import (save_feedback, status as feedback_status,
    feedback_record, update_correction, find_correction, correction_guidance, feedback_repair_guidance,
    pending_corrections, resume_pending_emails)
from site_metrics import heartbeat as metrics_heartbeat, snapshot as metrics_snapshot
from course_runtime import COURSES
from model_lock import SharedModelLock
from ollama_cancel import (CancellationScope, AnswerCancelled, BackgroundPreempted, current_scope,
    background_active, stream_chat_json, OLLAMA_NUM_CTX)
from course_grounding import review_text,GroundingError


ROOT=Path(__file__).resolve().parent
WEB_DIR=ROOT/'web'/'reference'
BUILD='v241.12.5-full-answer-voice'
DEFAULT_HOST='127.0.0.1'; DEFAULT_PORT=8182
OLLAMA_HOST=os.environ.get('OLLAMA_HOST','127.0.0.1:11435').strip()
OLLAMA_BASE=OLLAMA_HOST.rstrip('/') if OLLAMA_HOST.startswith(('http://','https://')) else ('http://'+OLLAMA_HOST).rstrip('/')
MODEL_KEEP_ALIVE=os.environ.get('VQ_MODEL_KEEP_ALIVE','30m')
MODEL_NUM_CTX=OLLAMA_NUM_CTX
LOCAL_MAX_WAIT=float(os.environ.get('VQ_LOCAL_MAX_WAIT_SEC','240'))
BATCH_MAX_ITEMS=max(2,min(6,int(os.environ.get('VQ_BATCH_MAX_ITEMS','4'))))
BATCH_NUM_CTX=MODEL_NUM_CTX  # same runner size as single answers and course work
BATCH_CHAR_BUDGET=max(5500,int(os.environ.get('VQ_BATCH_CHAR_BUDGET','9000')))
BATCH_EVIDENCE_CHARS=max(800,int(os.environ.get('VQ_BATCH_EVIDENCE_CHARS','1250')))
V231_DEFAULT_PROFILE=os.environ.get('VQ_V231_PROFILE','optimized').strip().lower() or 'optimized'
STT_URL=os.environ.get('VQ_STT_URL','http://127.0.0.1:11437/stt').strip()
STT_HEALTH=os.environ.get('VQ_STT_HEALTH','http://127.0.0.1:11437/health').strip()
JOB_TTL_SEC=max(600,int(os.environ.get('VQ_LOCAL_JOB_TTL_SEC','3600')))
GRANITE_API_BASE=os.environ.get('VQ_GRANITE_API_BASE',OLLAMA_BASE).rstrip('/')
GRANITE_MODEL_TAG=os.environ.get('VQ_GRANITE_MODEL_TAG','granite4.2:8b-q5_K_M').strip()
GRANITE_MODEL_LABEL=os.environ.get('VQ_GRANITE_MODEL_LABEL','Granite 4.2 8B Q5_K_M · IBM').strip()
GEMMA_API_BASE=os.environ.get('VQ_GEMMA_API_BASE',OLLAMA_BASE).rstrip('/')
GEMMA_MODEL_TAG=os.environ.get('VQ_GEMMA_MODEL_TAG','gemma4:12b').strip()
GEMMA_MODEL_LABEL=os.environ.get('VQ_GEMMA_MODEL_LABEL','Gemma 4 12B · Google').strip()
MISTRAL_API_BASE=os.environ.get('VQ_MISTRAL_API_BASE',OLLAMA_BASE).rstrip('/')
MISTRAL_MODEL_TAG=os.environ.get('VQ_MISTRAL_MODEL_TAG','ministral-3:8b-instruct-2512-q4_K_M').strip()
MISTRAL_MODEL_LABEL=os.environ.get('VQ_MISTRAL_MODEL_LABEL','Ministral 3 8B · Mistral AI').strip()
MODEL_CATALOG={
'granite':{'label':GRANITE_MODEL_LABEL,'tag':GRANITE_MODEL_TAG,'api_base':GRANITE_API_BASE},
'gemma':{'label':GEMMA_MODEL_LABEL,'tag':GEMMA_MODEL_TAG,'api_base':GEMMA_API_BASE},
'mistral':{'label':MISTRAL_MODEL_LABEL,'tag':MISTRAL_MODEL_TAG,'api_base':MISTRAL_API_BASE}}
SELECTABLE_MODEL_KEYS=('gemma','mistral')
DEFAULT_MODEL_KEY='gemma'
SEARCH=ReferenceSearch()


def jbytes(o): return json.dumps(o,ensure_ascii=False).encode('utf-8')
def url_json(url,payload=None,timeout=60):
    data=None; headers={}
    if payload is not None: data=json.dumps(payload).encode('utf-8'); headers['Content-Type']='application/json'
    req=urllib.request.Request(url,data=data,headers=headers,method='POST' if data is not None else 'GET')
    if (current_scope() is not None or background_active()) and url.rstrip('/').endswith('/api/chat'):
        return stream_chat_json(url,payload,timeout)
    with urllib.request.urlopen(req,timeout=float(timeout)) as r:return json.loads(r.read().decode('utf-8'))
def installed_models(base=None):
    base=(base or OLLAMA_BASE).rstrip('/')
    try:return set((m.get('name') or m.get('model') or '') for m in url_json(base+'/api/tags',timeout=4).get('models',[]))
    except Exception:return set()
def model_base(key): return str(MODEL_CATALOG[key].get('api_base') or OLLAMA_BASE).rstrip('/')
def public_catalog(): return {k:{'label':MODEL_CATALOG[k]['label'],'tag':MODEL_CATALOG[k]['tag']} for k in SELECTABLE_MODEL_KEYS}
def key_of(x):
    x=str(x or '').lower().strip(); return x if x in MODEL_CATALOG else DEFAULT_MODEL_KEY
def course_model_call(server,model_key,system,user,max_tokens=1200):
    """Generic uploaded-course generation/answer call using the selected local model."""
    key=key_of(model_key);meta=MODEL_CATALOG[key];tag=meta['tag'];base=model_base(key)
    payload={'model':tag,'messages':[{'role':'system','content':str(system or '')},{'role':'user','content':str(user or '')}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,
             'options':{'temperature':0.15,'top_p':0.75,'num_ctx':MODEL_NUM_CTX,'num_predict':int(max_tokens),'repeat_penalty':1.02}}
    if '大學課程情境題出題者' in str(system or '') or 'COURSE_GROUNDING_REVIEW' in str(system or ''):
        payload['format']='json'
        payload['options']['temperature']=0.0
    def compute():
        while True:
            try:
                with server.model_lock.using(tag):
                    ensure_single_active_model(server,key,tag,base)
                    started=time.monotonic()
                    try:d=url_json(base+'/api/chat',payload=payload,timeout=max(LOCAL_MAX_WAIT,360))
                    finally:metric('model_request_seconds',time.monotonic()-started)
                    server.active_model_key=key;server.active_model_tag=tag;server.active_model_base=base
                for field in ('load_duration','prompt_eval_duration','eval_duration'):
                    metric(field+'_seconds',float(d.get(field) or 0)/1e9)
                metric('model_calls')
                if d.get('done_reason')=='length' or d.get('done') is False:
                    raise RuntimeError('Model step did not finish; increase its output budget before retrying.')
                result=str(((d.get('message') or {}).get('content') or '')).strip()
                if not result:raise RuntimeError('Model returned an empty course step.')
                if payload.get('format')=='json':
                    begin,end=result.find('{'),result.rfind('}')
                    parsed=loads_model_json(result[begin:end+1] if begin>=0 and end>begin else result)
                    if not isinstance(parsed,dict):raise ValueError('Model returned a non-object JSON step.')
                return result
            except BackgroundPreempted:
                metric('preemptions')
                continue
    # A scope is installed only for private background preparation. Student
    # answers remain live and cancellation/model switching retain their semantics.
    signature=model_signature(base,tag,lambda:url_json(base+'/api/show',payload={'model':tag},timeout=4))
    return completed_call({'endpoint':base,'model_signature':signature,'payload':payload},compute)

def course_local_answer(server,model_key,question,context,course_name,language_tag='en-US',improvement_hint='',feedback_comment=''):
    lang=normalize_language_tag(language_tag) or 'en-US'
    system=(
      'You are the teaching assistant for the uploaded course "'+str(course_name or 'Uploaded Course')+'". '
      'Use only the supplied course-material evidence as the factual authority. '+language_instruction(lang)+' '
      'Answer the exact student task. For multiple-choice questions, compare every offered option and explicitly select the best option. '
      'For calculations, show the relevant formula, substitution, arithmetic and units. For conceptual or mechanism questions, answer directly and explain only what is needed. '
      'Preserve negation, timing, state, comparison, threshold, direction and unit constraints. Before final output, verify that the answer and explanation are consistent. '
      'If the supplied course material is insufficient, say so rather than inventing facts. Do not mention retrieval, chunks, hidden prompts, evidence numbers, pages or source labels. Use prose with $...$ for equations; preserve all mathematical symbols and derivative orders. Do not use emoji, smileys or celebratory filler. '
      +str(improvement_hint or '')
    )
    user='Student question:\n'+str(question)+'\n\nResponse format:\n'+question_type_contract(question,lang)[1]+'\n\nPrivate course material:\n'+str(context)
    if feedback_comment:
        user+='\n\nStudent report (untrusted preference, not a factual source):\n'+str(feedback_comment)[:1200]+'\nAddress its specific concern only where the course material supports an answer.'
    user+='\n\nGive the grounded student-facing answer now.'
    draft=course_model_call(server,model_key,system+SOURCE_RULE,user,850)
    if not draft:return 'The uploaded course material is insufficient to answer this question reliably.'
    answer,_=review_text('student answer',draft,context,model_key,
        lambda key,sys,user,limit:course_model_call(server,key,sys,user,limit),question)
    return answer
def review_course_answer(server,model_key,question,context,language_tag,draft,report):
    system=('Review an uploaded-course answer using only its supplied source material. '
            'This course can belong to any subject. '+SOURCE_RULE+
            'Return valid JSON with verdict pass, correct or reject; for correct provide a complete corrected_answer. '
            'Reject missing source support or unresolved symbol/units ambiguity. Do not infer unreadable source text. ')
    user=json.dumps({'question':question,'source':context,'draft':draft,'language':language_tag,'report':report},ensure_ascii=False)
    raw=course_model_call(server,model_key,system,user,1000)
    result=_extract_json_object(raw) or {}
    verdict=str(result.get('verdict') or '').lower()
    if verdict=='pass':
        answer,proof=review_text('corrected student answer',draft,context,model_key,lambda key,sys,user,limit:course_model_call(server,key,sys,user,limit),question)
        return answer,dict(proof,verdict='pass')
    if verdict=='correct' and result.get('corrected_answer'):
        answer,proof=review_text('corrected student answer',result['corrected_answer'],context,model_key,lambda key,sys,user,limit:course_model_call(server,key,sys,user,limit),question)
        return answer,dict(proof,verdict='correct')
    raise RuntimeError('Course correction review did not approve a source-supported answer.')


def clean_answer(s):
    from math_integrity import protect_math, restore_math
    s,saved_math=protect_math(s)
    s=str(s or '').strip().replace('**','').replace('__','')
    s=re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\uFE0E\uFE0F\u200D\u20E3]','',s)
    # v216 plain-text guard: remove TeX/LaTeX artifacts before anything reaches
    # the student UI or TTS. Preserve the medical meaning in plain notation.
    s=re.sub(r'\\dot\s*\{\s*V\s*\}\s*_\s*\{?\s*A\s*\}?','VA',s,flags=re.I)
    s=re.sub(r'\\dot\s*V\s*_\s*\{?\s*A\s*\}?','VA',s,flags=re.I)
    s=re.sub(r'\\dot\s*\{\s*Q\s*\}', 'Q', s, flags=re.I)
    s=re.sub(r'\\dot\s*Q', 'Q', s, flags=re.I)
    s=re.sub(r'\\dot\s*\{\s*V\s*\}', 'V', s, flags=re.I)
    s=re.sub(r'\\dot\s*V', 'V', s, flags=re.I)
    for _ in range(3): s=re.sub(r'\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}',r'\1/\2',s)
    s=re.sub(r'\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}',r'\1',s)
    s=s.replace('\\Delta','Δ').replace('\\times','×').replace('\\cdot','·')
    s=re.sub(r'\\(?:left|right|quad|qquad)\b','',s)
    s=re.sub(r'\\[(),;!\[\]]','',s)
    s=s.replace('$','').replace('`','')
    s=re.sub(r'_\s*\{\s*([A-Za-z0-9]+)\s*\}',r'\1',s)
    s=re.sub(r'_\s*([A-Za-z0-9]+)',r'\1',s)
    s=s.replace('{','').replace('}','')
    s=re.sub(r'\\([A-Za-z]+)',r'\1',s).replace('\\','')
    s=re.sub(r'(?m)^\s{0,3}#{1,6}\s*','',s)
    # Never expose internal RAG/source-attribution labels in student-facing answers.
    leak_patterns=[
        r'(?i)(?:根據|依据|依據|依照)\s*(?:第\s*)?(?:Evidence|Context|Chunk|Retrieved\s+passage|retrieved\s+evidence|證據|证据)\s*#?\s*\d*\s*[,，:：]?\s*',
        r'(?i)(?:根據|依据|依據|依照)\s*(?:Guyton\s*(?:&|and)\s*Hall(?:\s*教材|\s*教科書|\s*textbook)?|參考教材|参考教材|教材內容|教材内容|上述教材|來源|来源)\s*[,，:：]?\s*',
        r'(?i)According\s+to\s+(?:Evidence|Context|Chunk|Retrieved\s+passage|Guyton\s*(?:&|and)\s*Hall(?:\s+textbook)?)\s*#?\s*\d*\s*[,,:：]?\s*',
    ]
    for pat in leak_patterns: s=re.sub(pat,'',s)
    s=re.sub(r'(?i)\b(?:Evidence|Context|Chunk)\s*#?\s*\d+\b\s*[:：,-]?\s*','',s)
    source_layout_patterns=[
        r'(?i)\b(?:as\s+(?:shown|illustrated|depicted)\s+in|according\s+to|see)\s+(?:the\s+)?(?:figure|fig\.?|table)\s*[A-Za-z0-9.\-]*\s*[,;:]?\s*',
        r'(?i)\b(?:the\s+)?(?:figure|fig\.?|table)\s*[A-Za-z0-9.\-]*\s+(?:shows|illustrates|demonstrates|indicates|depicts)\s+(?:that\s+)?',
        r'(?i)\b(?:on\s+)?page\s+\d+[A-Za-z]?\b\s*[,;:]?\s*',
        r'(?i)\b(?:in\s+)?chapter\s+\d+[A-Za-z]?\b\s*[,;:]?\s*',
        r'(?i)\b(?:in|from|according\s+to)\s+(?:the\s+)?(?:textbook|reference\s+book|source|reference)\b\s*[,;:]?\s*',
        r'(?:如|正如)\s*(?:圖|图|Figure|Fig\.?|表|Table)\s*[A-Za-z0-9.\-]*\s*(?:所示|顯示|显示)[，,：:]?\s*',
        r'(?:第\s*)?\d+\s*(?:頁|页|章)\s*[，,：:]?\s*',
        r'(?:根據|依据|依據|依照)\s*(?:教材|教科書|教科书|參考書|参考书|來源|来源)[，,：:]?\s*',
        r'(?:根據|依据|依據|依照)?\s*(?:參考文本|参考文本|參考內容|参考内容|參考資料|参考资料|reference\s+text)\s*[,，:：]?\s*',
        r'(?:根據|依据|依據|依照)?\s*(?:表格|表|Table)\s*[A-Za-z0-9.\-]+(?:\s*(?:和|與|及)\s*(?:段落|內容|說明|文字))?\s*(?:指出|說明|显示|顯示)?\s*[,，:：]?\s*',
        r'(?:根據|依据|依據|依照)?\s*(?:段落|內容|說明|文字)\s*(?:指出|說明|显示|顯示)\s*[,，:：]?\s*',
    ]
    for pat in source_layout_patterns: s=re.sub(pat,'',s)
    s=re.sub(r'(?i)\bV\s*_\s*\{?A\}?\b','VA',s)
    s=re.sub(r'(?i)\bV\s*_\s*\{?E\}?\b','VE',s)
    s=re.sub(r'(?i)\bV\s*_\s*\{?T\}?\b','VT',s)
    s=re.sub(r'(?i)\bV\s*_\s*\{?D\}?\b','VD',s)
    s=re.sub(r'^(?:和|與|及)?\s*(?:段落|內容|說明|文字)?\s*(?:指出|說明|顯示|显示)\s*[,，:：]?\s*','',s)
    s=re.sub(r'(?:比\s*)?理想值\s*(?:高出|高了|高)\s*(?:約\s*)?2(?:\.|點)5\s*倍','約為理想值的 2.5 倍',s)
    s=re.sub(r'[ \t]+',' ',s); s=re.sub(r'\s+([.,;:!?，。；：！？])',r'\1',s); s=re.sub(r'\n[ \t]+','\n',s)
    return restore_math(s.strip(),saved_math).lstrip('，,:：;；')


def ollama_unload(tag,base=None):
    base=(base or OLLAMA_BASE).rstrip('/')
    try:
        url_json(base+'/api/generate',payload={'model':tag,'prompt':'','stream':False,'keep_alive':0},timeout=30)
    except Exception:
        pass

def ensure_single_active_model(server,key,tag,base):
    # With enough free GPU memory Gemma and Ministral stay loaded together, so
    # a student answer and course preparation never unload each other's model.
    if gpu_policy.keep_models_loaded():return
    previous=getattr(server,'active_model_tag',None)
    previous_base=getattr(server,'active_model_base',None)
    if previous and (previous!=tag or previous_base!=base):
        ollama_unload(previous,previous_base)
        server.active_model_key=None
        server.active_model_tag=None
        server.active_model_base=None

def stt_available():
    try:
        with urllib.request.urlopen(STT_HEALTH,timeout=.8) as r:return int(getattr(r,'status',200))==200
    except Exception:return False
def stt(raw,ctype,language_hint=''):
    headers={'Content-Type':ctype or 'audio/webm'}
    if language_hint: headers['X-VQ-Language-Hint']=str(language_hint)
    req=urllib.request.Request(STT_URL,data=raw,headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=180) as r:return json.loads(r.read().decode('utf-8'))


LANGUAGE_NAMES={
    'zh-TW':'Taiwan Traditional Chinese','zh-CN':'Simplified Chinese','en-US':'English','ja-JP':'Japanese','ko-KR':'Korean',
    'es-ES':'Spanish','fr-FR':'French','de-DE':'German','it-IT':'Italian','pt-PT':'Portuguese','ru-RU':'Russian',
    'ar':'Arabic','hi-IN':'Hindi','th-TH':'Thai','he-IL':'Hebrew','el-GR':'Greek','tr-TR':'Turkish','pl-PL':'Polish',
    'nl-NL':'Dutch','id-ID':'Indonesian','vi-VN':'Vietnamese'
}

def normalize_language_tag(tag):
    t=str(tag or '').strip().lower().replace('_','-')
    if not t:return ''
    aliases={'zh':'zh-TW','zh-tw':'zh-TW','zh-hant':'zh-TW','zh-cn':'zh-CN','zh-hans':'zh-CN','en':'en-US','ja':'ja-JP','ko':'ko-KR','es':'es-ES','fr':'fr-FR','de':'de-DE','it':'it-IT','pt':'pt-PT','ru':'ru-RU','hi':'hi-IN','th':'th-TH','he':'he-IL','el':'el-GR','tr':'tr-TR','pl':'pl-PL','nl':'nl-NL','id':'id-ID','vi':'vi-VN'}
    if t in aliases:return aliases[t]
    if t.startswith('zh-'):return 'zh-CN' if any(x in t for x in ('hans','cn','sg')) else 'zh-TW'
    parts=t.split('-');return parts[0].lower()+(''.join('-'+x.upper() for x in parts[1:]) if len(parts)>1 else '')

def detect_question_language(text,requested=''):
    r=normalize_language_tag(requested)
    if r:return r
    s=str(text or '')
    if re.search(r'[\u3040-\u30ff]',s):return 'ja-JP'
    if re.search(r'[\uac00-\ud7af]',s):return 'ko-KR'
    if re.search(r'[\u0e00-\u0e7f]',s):return 'th-TH'
    if re.search(r'[\u0590-\u05ff]',s):return 'he-IL'
    if re.search(r'[\u0600-\u06ff]',s):return 'ar'
    if re.search(r'[\u0900-\u097f]',s):return 'hi-IN'
    if re.search(r'[\u0370-\u03ff]',s):return 'el-GR'
    if re.search(r'[\u0400-\u04ff]',s):return 'ru-RU'
    if re.search(r'[\u3400-\u9fff]',s):
        simp=sum(s.count(ch) for ch in '这们来说对会从还让与并过后里发见问题应医气体压经学变动较实机开关点数总区间进则级组别类种传达选显听语声网简复统冲击临结论脏脉浓扩质导严现处产响减维调节术华台湾获识别优势频线载设备进阶')
        trad=sum(s.count(ch) for ch in '這們來說對會從還讓與並過後裡發見問題應醫氣體壓經學變動較實機開關點數總區間進則級組別類種傳達選顯聽語聲網簡復統衝擊臨結論臟脈濃擴質導嚴現處產響減維調節術華臺灣獲識別優勢頻線載設備進階')
        return 'zh-CN' if simp>trad+1 else 'zh-TW'
    return 'en-US'

def language_instruction(tag):
    tag=normalize_language_tag(tag) or 'zh-TW'; name=LANGUAGE_NAMES.get(tag,tag)
    extra=''
    if tag=='zh-TW':extra=' Use Taiwan Traditional Chinese characters only; do not use Simplified Chinese.'
    elif tag=='zh-CN':extra=' Use Simplified Chinese characters.'
    return 'Answer in the student question language. Required output language: %s (%s).%s Preserve standard medical abbreviations such as V/Q, PaO2, PaCO2, FiO2, SpO2 and PEEP.'%(tag,name,extra)

def no_evidence_message(tag):
    tag=normalize_language_tag(tag) or 'zh-TW'
    msgs={'zh-TW':'目前參考教材的語意檢索沒有取得足以可靠回答此題的內容。','zh-CN':'目前参考教材的语义检索没有取得足以可靠回答此题的内容。','en-US':'The reference-book retrieval did not find enough evidence to answer this question reliably.','ja-JP':'参考教材から、この質問に確実に答えるための十分な根拠を取得できませんでした。','ko-KR':'참고 교재에서 이 질문에 신뢰성 있게 답할 충분한 근거를 찾지 못했습니다。'}
    return msgs.get(tag,'The reference-book retrieval did not find enough evidence to answer this question reliably.')


def out_of_scope_message(tag):
    tag=normalize_language_tag(tag) or 'zh-TW'
    msgs={
      'zh-TW':'此問題超出本系統範圍。本系統回答人體生理學及直接相關的病理生理問題，請重新輸入相關問題。',
      'zh-CN':'此问题超出本系统范围。本系统回答人体生理学及直接相关的病理生理问题，请重新输入相关问题。',
      'en-US':'This question is outside the system scope. Please ask about human physiology or directly related pathophysiology.',
      'ja-JP':'この質問はシステムの対象範囲外です。人体生理学または直接関連する病態生理学について質問してください。',
      'ko-KR':'이 질문은 시스템 범위를 벗어납니다. 인체생리학 또는 직접 관련된 병태생리학에 대해 질문해 주세요.'
    }
    return msgs.get(tag,msgs['en-US'])

def detect_question_type(question):
    """Deterministic routing used only to choose the response contract.

    A question is not treated as a calculation merely because an option contains a
    number or a physiology variable name. Direct recall such as "normal VT is?"
    stays conceptual; derived quantities, ratios, explicit equations and
    "calculate/how many percent" prompts are calculation tasks.
    """
    q=str(question or '').strip()
    low=q.lower()
    option_letters=[a for a,_ in parse_mcq_options(q)]
    has_mcq=len(set(x.upper() for x in option_letters))>=2
    explicit_calc_terms=(
        'calculate','calculation','compute','derive','solve','work out','what is the ratio','what percentage',
        '計算','求出','算出','換算','比值為何','比例為何','多少%','百分比','占全肺量','佔全肺量','約為氧的多少倍'
    )
    has_explicit_calc=any(t in low for t in explicit_calc_terms)
    has_assignment=bool(re.search(r'(?i)\b(?:FRC|ERV|RV|VC|TLC|IC|VE|VA|VT|VD|PaCO2|PECO2|PACO2|FiO2)\b\s*[=:]\s*[-+]?\d',q))
    multi_given=len(re.findall(r'(?i)\b(?:FRC|ERV|RV|VC|TLC|IC|VE|VA|VT|VD|PaCO2|PECO2|PACO2|FiO2)\b\s*[=:]\s*[-+]?\d',q))>=2
    ratio_request=bool(re.search(r'(?i)(?:FRC\s*(?:/|占|佔).*TLC|VD\s*/\s*VT|多少\s*%|百分之|多少倍|ratio)',q))
    calc_like=has_explicit_calc or multi_given or ratio_request
    if has_mcq and calc_like:
        return 'multiple_choice_calculation'
    if has_mcq:
        return 'multiple_choice_conceptual'
    if (has_assignment and (has_explicit_calc or multi_given or ratio_request)) or (has_explicit_calc and bool(re.search(r'\d',q))):
        return 'numerical_calculation'
    mechanism_terms=('why','how does','how do','mechanism','cause','reason','為什麼','为何','原因','機轉','机制','如何造成','怎麼造成')
    if any(t in low for t in mechanism_terms):
        return 'mechanism_explanation'
    return 'knowledge_conceptual'

def question_type_contract(question,language_tag):
    kind=detect_question_type(question)
    lang=normalize_language_tag(language_tag) or 'zh-TW'
    zh=lang in ('zh-TW','zh-CN')
    if kind=='multiple_choice_calculation':
        fmt=('正確答案：X. [選項內容]\n計算：\n[公式、代值、結果與單位]\n解釋：\n[只補充必要的生理意義]'
             if zh else 'Correct answer: X. [option text]\nCalculation:\n[formula, substitution, result and unit]\nExplanation:\n[only the necessary physiological interpretation]')
        return kind, (
            'This is a MULTIPLE-CHOICE CALCULATION question. You MUST independently calculate before choosing. '
            'Identify the correct physiology equation, substitute the given values, check units, compute the result, then compare the result with ALL provided choices. '
            'The first line MUST explicitly state the single best option letter and its option text. Never guess an option first and then justify it. '
            'Never say none of the options is correct unless the question is genuinely malformed; if your calculation matches a listed option, you MUST choose it. '
            'Show concise verification steps only, not hidden chain-of-thought. Required output format:\n'+fmt)
    if kind=='multiple_choice_conceptual':
        fmt=('正確答案：X. [選項內容]\n解釋：\n[為何此選項最符合生理學；必要時簡要排除關鍵干擾項]'
             if zh else 'Correct answer: X. [option text]\nExplanation:\n[why this is the single best physiological answer; briefly distinguish key distractors only when useful]')
        return kind, (
            'This is a MULTIPLE-CHOICE CONCEPTUAL question. You MUST select exactly one best answer from the provided choices. '
            'Evaluate the meaning of the choices against the physiological facts before committing. Treat phase/timing words, negations, comparisons, reference conditions, signs, and directions as binding constraints; do not replace a still-occurring process with its endpoint state. The first line MUST explicitly give the option letter and option text. '
            'Do not provide explanation only. Do not invent an option, and do not answer none-of-the-above unless it is actually offered or the question is malformed. '
            'Required output format:\n'+fmt)
    if kind=='numerical_calculation':
        fmt=('答案：\n[數值與單位]\n計算：\n[公式 → 代值 → 結果]\n解釋：\n[只有需要時才補充]'
             if zh else 'Answer:\n[value and unit]\nCalculation:\n[formula -> substitution -> result]\nExplanation:\n[only if needed]')
        return kind, (
            'This is a NUMERICAL CALCULATION question without answer choices. Use the correct physiological equation, substitute values, check units and arithmetic, and state the numerical answer clearly. '
            'Show concise verification steps only. Required output format:\n'+fmt)
    if kind=='mechanism_explanation':
        fmt=('問題：\n[簡潔重述學生真正問的機轉]\n回答：\n[先直接給結論]\n機轉：\n[用精簡因果順序說明]'
             if zh else 'Question:\n[briefly restate the mechanism being asked]\nAnswer:\n[direct conclusion first]\nMechanism:\n[concise causal sequence]')
        return kind, (
            'This is a MECHANISM/WHY question. First restate the exact physiological relationship being asked, then answer it directly, then explain only the causal mechanism needed to support that answer. '
            'Do not drift to unrelated teaching material. Required output format:\n'+fmt)
    fmt=('問題：\n[簡潔重述學生真正問的問題]\n回答：\n[直接針對該問題作答]\n原因／機轉：\n[只有有助理解且與問題直接相關時才加入；不需要時省略]'
         if zh else 'Question:\n[briefly restate exactly what the student is asking]\nAnswer:\n[answer that exact question directly]\nMechanism:\n[include only when directly useful; omit if unnecessary]')
    return kind, (
        'This is a KNOWLEDGE/CONCEPTUAL question. Briefly restate the exact question, then answer that question directly. '
        'Do not convert it into multiple choice, do not generate a new quiz, and do not append unrelated textbook background. '
        'Only add a mechanism section when it directly improves understanding. Required output format:\n'+fmt)

def calculation_safeguards():
    return (
      'For respiratory-physiology calculations, the following trusted course equations may be used when relevant: '
      'FRC = ERV + RV; VC = IRV + VT + ERV; TLC = VC + RV; IC = IRV + VT; '
      'VE = VT x respiratory rate; respiratory rate = VE / VT; VA = (VT - VD) x respiratory rate; '
      'VD/VT = (PaCO2 - PECO2) / PaCO2. Do not use an equation unless it is relevant to the question. '
      'Recalculate from the values in the current question; do not rely on memory of a similar problem.'
    )

def semantic_constraint_safeguards():
    return (
      'Semantic-constraint safeguards for human-physiology questions, applied without inserting any answer facts: '
      '(1) treat every state, phase, timing, comparison, negation, reference, and boundary word in the stem as binding; '
      '(2) distinguish a process that is still occurring from its endpoint/equilibrium state; '
      '(3) identify the exact target variable and do not substitute a related variable, pressure, content, gradient, volume, flow, or compartment; '
      '(4) when signs or directions matter, verify the physical direction/gradient and require the chosen option to match that sign or direction; '
      '(5) compare every option against the same stem constraints rather than choosing a familiar nearby fact; '
      '(6) before committing, verify that the explanation logically entails the selected option and does not contradict any stem qualifier. '
      'These safeguards define how to reason; they do not provide a physiology answer key or question-specific fact.'
    )

def course_accuracy_safeguards():
    # Legacy compatibility alias. Production prompts use semantic_constraint_safeguards().
    return semantic_constraint_safeguards()

def _semantic_stem(question):
    q=str(question or '').strip()
    if not q:return q
    m=re.search(r'(?im)^\s*A\s*[\.．、\):：]\s+',q)
    if m and m.start()>5:return q[:m.start()].strip()
    ma=re.search(r'(?i)(?:^|\s)A\s*[\.．、\):：]\s*',q)
    if ma and all(re.search(r'(?i)(?:^|\s)%s\s*[\.．、\):：]\s*'%x,q[ma.end():]) for x in 'BCD'):
        return q[:ma.start()].strip()
    return q

def semantic_qualifier_risk(question):
    """Answer-agnostic trigger for stems where small semantic qualifiers can flip an MCQ answer."""
    stem=_semantic_stem(question)
    low=stem.lower()
    terms=(
      'during','while','still','not yet','before','after','at the end','end of','beginning','starts','stops','continues',
      'except','least','most','higher','lower','increase','decrease','greater','less','positive','negative','relative to','compared with',
      '進行中','进行中','尚未','仍在','還在','还在','停止前','結束前','结束前','結束時','结束时','末','之前','之後','之后',
      '何者除外','不正確','不正确','錯誤','错误','最少','最多','較高','较高','較低','较低','增加','減少','减少','相對於','相对于'
    )
    if any(t in low for t in terms):return True
    opts=parse_mcq_options(question)
    signed=0
    for _,text in opts:
        if re.search(r'(?<!\w)[+-]\s*\d',str(text)):signed+=1
    return signed>=2

def mcq_decision_risk_profile(question):
    """Answer-agnostic risk flags that determine whether a stronger comparison pass is needed."""
    q=str(question or ''); stem=_semantic_stem(q); low=stem.lower(); opts=parse_mcq_options(q)
    flags=[]
    checks=[
      ('negation',r'(?i)(?:except|incorrect|not correct|false|least likely|何者錯誤|何者错误|不正確|不正确|何者除外|非下列|不是)'),
      ('timing-phase',r'(?i)(?:during|while|still|before|after|end of|beginning|進行中|进行中|尚未|仍在|末|前\s*1/3|中\s*1/3|後\s*1/3|后\s*1/3|之前|之後|之后)'),
      ('threshold-range',r'(?i)(?:below|above|less than|greater than|低於|低于|高於|高于|\d+(?:\.\d+)?\s*[～~\-–至到]\s*\d+(?:\.\d+)?)'),
      ('numeric-unit',r'(?i)(?:\d+(?:\.\d+)?\s*(?:mm\s*hg|cm\s*h2o|cm\s*h\s*2\s*o|l/sec|l/s|ml|%|倍)|mm\s*hg|cm\s*h2o|l/sec)'),
      ('direction-comparison',r'(?i)(?:increase|decrease|higher|lower|greater|less|most|least|前|後|后|較高|较高|較低|较低|增加|減少|减少|最多|最少|由.+到|from.+to)'),
      ('relationship-topology',r'(?i)(?:related to|associated with|between|transport.+to|delay|response to|與.+有關|与.+有关|之間|之间|傳送|传送|延遲|延迟|反應|反应)'),
      ('sign',r'(?<!\w)[+-]\s*\d'),
      ('state-compartment',r'(?i)(?:residual\s+volume|functional\s+residual|dead\s+space|alveolar|arterial|venous|pleural|transpulmonary|吸氣末|吸气末|吐氣末|呼氣末|呼气末|肺餘容積|肺余容积|功能肺餘量|功能残气量|無效腔|无效腔|肺泡|動脈|动脉|靜脈|静脉|肋膜|胸膜)'),
      ('set-membership',r'(?:①|②|③|④|⑤|⑥|⑦|⑧|下列那(?:些|些)|下列哪(?:些|些)|哪些部位|何者包含|何者包括)')
    ]
    for name,pat in checks:
        if re.search(pat,stem):flags.append(name)
    # Threshold/range discrimination often lives inside the answer choices rather than
    # the stem itself. Inspect the full offered text generically; no physiology value
    # or correct answer is encoded here.
    if 'threshold-range' not in flags and re.search(r'(?i)(?:below|above|less than|greater than|低於|低于|高於|高于|\d+(?:\.\d+)?\s*[～~\-–至到]\s*\d+(?:\.\d+)?)',q):
        flags.append('threshold-range')
    numeric_options=sum(1 for _,t in opts if re.search(r'\d',t))
    if numeric_options>=2 and 'numeric-unit' not in flags:flags.append('numeric-options')
    return {'high_risk':bool(flags),'flags':flags,'option_count':len(opts)}

def parse_mcq_options(question):
    q=str(question or '')
    matches=list(re.finditer(r'(?is)(?:^|[\n\r\t ]+)[(（]?([A-EＡ-Ｅ])\s*[.．、:：)）]\s*',q))
    out=[]
    for i,m in enumerate(matches):
        a=m.group(1).upper();a=chr(ord(a)-0xFEE0) if 'Ａ'<=a<='Ｅ' else a
        b=matches[i+1].start() if i+1<len(matches) else len(q)
        text=q[m.end():b].strip(' \t\r\n;；')
        if text: out.append((a,text))
    # Deduplicate letters while preserving first occurrence.
    seen=set(); clean=[]
    for a,t in out:
        if a not in seen:
            clean.append((a,t)); seen.add(a)
    return clean

def _extract_json_object(text):
    t=str(text or '').strip()
    if t.startswith('```'):
        t=re.sub(r'^```(?:json)?\s*','',t,flags=re.I); t=re.sub(r'\s*```$','',t)
    try:return loads_model_json(t)
    except Exception:pass
    m=re.search(r'\{.*\}',t,re.S)
    if m:
        try:return loads_model_json(m.group(0))
        except Exception:pass
    return None

def mcq_solver_prompt(q,context,language_tag,kind):
    opts=parse_mcq_options(q)
    option_text='\n'.join('%s. %s'%(a,t) for a,t in opts)
    zh=(normalize_language_tag(language_tag) or 'zh-TW') in ('zh-TW','zh-CN')
    return (
      'Solve this human-physiology or directly related pathophysiology multiple-choice item as an evidence-to-decision auditor. Do not trust a familiar-sounding option and do not use an answer key. '
      'Use the private grounding material, including the symmetric option-comparison evidence when present. '
      'STEP 1: determine the target polarity (choose the true/best statement versus choose the false/incorrect statement). '
      'STEP 2: extract the exact target variable or relationship and every binding condition: state, phase/timing, anatomical endpoints, comparison, negation, threshold/range, reference condition, sign/direction and units. Never replace a dynamic in-progress state with its endpoint/equilibrium state. '
      'STEP 3: test EVERY option under those exact conditions. A keyword match is not enough; verify the subject-relation-object and the direction/threshold/value. For an EXCEPT/incorrect item, do not call a statement false merely because it is not the sole or primary mechanism; an established association is still a true statement. For a specific numerical threshold claim, require the grounding to support that threshold rather than accepting a nearby familiar number. '
      'STEP 4: identify the runner-up and state the single decisive reason it loses. For a normal-value/range item, require an explicit condition-matched value or range from the grounding; do not widen a reference range from memory. '
      'STEP 5: choose the option only after the explanation entails that option. If the explanation says early/proximal/negative/lower/etc., the selected choice must express the same relation. '
      'For a calculation/ratio item, recompute first and map the result to the listed choice. '
      'Return ONE JSON object only with keys option, explanation, calculation, target_polarity, stem_constraints, constraint_summary, decisive_relation, option_checks, runner_up, runner_up_rejection, consistency_check, constraint_check, confidence, needs_review. '
      'option_checks must be an object with EVERY available option letter. Each value must be an object {\"verdict\":\"supports|contradicts|insufficient\",\"reason\":\"...\"}; use exactly one of those three verdict words. '
      'consistency_check and constraint_check must both be pass or review and agree; constraint_summary mirrors stem_constraints for compatibility. option must be exactly one available letter. calculation is empty when not needed. '
      + language_instruction(language_tag)+' '+calculation_safeguards()+' '+semantic_constraint_safeguards()+'\n\n'
      'Question:\n'+str(q)+'\n\nOptions:\n'+option_text+'\n\nPrivate grounding material (never cite or mention it):\n'+str(context)+'\n\n'
      + ('Use Traditional Chinese in explanation/calculation and concise option-check reasons.' if zh else 'Use the required student language in explanation/calculation and concise option-check reasons.')
    )

def render_mcq_answer(option,options,explanation,calculation,language_tag,kind):
    mp=dict(options); text=mp.get(option,'')
    zh=(normalize_language_tag(language_tag) or 'zh-TW') in ('zh-TW','zh-CN')
    explanation=clean_answer(explanation)
    calculation=clean_answer(calculation)
    if zh:
        parts=['正確答案：%s. %s'%(option,text)]
        if kind=='multiple_choice_calculation' and calculation: parts += ['計算：',calculation]
        if explanation: parts += ['解釋：',explanation]
    else:
        parts=['Correct answer: %s. %s'%(option,text)]
        if kind=='multiple_choice_calculation' and calculation: parts += ['Calculation:',calculation]
        if explanation: parts += ['Explanation:',explanation]
    return '\n'.join(parts).strip()

def looks_incomplete_answer(text):
    t=str(text or '').strip()
    if not t:return True
    if re.search(r'(?:因此|所以|故|because|therefore|thus|and|or|which|that|根據|依據|FRC|TLC|PaCO2|PaO2|CO2|CO)\s*[,，:：;；-]?\s*$',t,re.I):return True
    # Normal completed Chinese/English answer should not terminate on a connector/comma.
    if t[-1] in ',，:：;；-':return True
    return False


# v227: every answer receives a millisecond-scale deterministic audit.  A second
# semantic LLM review is conditional only, so normal answers keep the single-pass
# latency profile.  Correct answers are never rewritten for style alone.
def _source_leak_remaining(text):
    t=str(text or '')
    pats=(
        r'(?i)\b(?:figure|fig\.?|table|page|chapter|evidence|context|chunk|retrieved\s+passage|textbook\s+evidence)\b',
        r'(?:根據|依据|依據|依照)\s*(?:教材|教科書|教科书|參考書|参考书|來源|来源|圖|图|表|頁|页|章)',
        r'(?:參考文本|参考文本|參考資料|参考资料|如圖|如图|表格\s*\d+)'
    )
    return any(re.search(p,t) for p in pats)

def _answer_option_letter(answer):
    t=str(answer or '')
    m=re.search(r'(?i)(?:正確答案|正确答案|correct\s+answer)\s*[:：]\s*([A-E])\b',t)
    if m:return m.group(1).upper()
    m=re.match(r'\s*([A-E])\s*[\.\)\:、]',t,re.I)
    return m.group(1).upper() if m else ''

def _question_values(question):
    vals={}
    for m in re.finditer(r'(?i)\b(FRC|ERV|RV|VC|TLC|IC|VE|VA|VT|VD|PaCO2|PECO2|PACO2|FiO2|RR)\b\s*[=:]\s*([-+]?\d+(?:\.\d+)?)',str(question or '')):
        try:vals[m.group(1).upper()]=float(m.group(2))
        except Exception:pass
    return vals

def _option_value_map(options):
    out={}
    for letter,text in options:
        nums=[]
        for x in re.findall(r'[-+]?\d+(?:\.\d+)?',str(text)):
            try:nums.append(float(x))
            except Exception:pass
        out[letter]=nums
    return out

def _closest_numeric_option(options,target,relative_tol=0.08,absolute_tol=1.0):
    best=''; best_err=None
    for letter,nums in _option_value_map(options).items():
        for n in nums:
            err=abs(n-target)
            if best_err is None or err<best_err:
                best_err=err; best=letter
    if best_err is None:return ''
    tol=max(float(absolute_tol),abs(float(target))*float(relative_tol))
    return best if best_err<=tol else ''

def deterministic_expected_option(question,options):
    """Fast checks for common respiratory calculations; blank means 'not rule-checkable'."""
    q=str(question or ''); low=q.lower(); v=_question_values(q)
    # v235 policy: deterministic verification may check arithmetic from values supplied
    # in the current item, but it must not inject memorized normal values or exam facts.
    # FRC/TLC is therefore checked only when both quantities are explicitly provided.
    if ('frc' in low and 'tlc' in low and ('%' in q or 'percent' in low or '比例' in q or '比值' in q or '占' in q or '佔' in q)) and v.get('FRC') is not None and v.get('TLC'):
        hit=_closest_numeric_option(options,100.0*v['FRC']/v['TLC'],relative_tol=0.08,absolute_tol=2.5)
        if hit:return hit
    # Bohr dead-space fraction.
    if ('bohr' in low or 'vd/vt' in low or ('dead space' in low and 'peco2' in low)) and v.get('PACO2'):
        peco=v.get('PECO2')
        if peco is not None:
            frac=(v['PACO2']-peco)/v['PACO2']
            target=100.0*frac if ('%' in q or 'percent' in low or '百分' in q) else frac
            hit=_closest_numeric_option(options,target,relative_tol=0.08,absolute_tol=2.0 if target>2 else 0.03)
            if hit:return hit
    # Alveolar ventilation from VE, VT, VD.
    if (re.search(r'(?i)\bVA\b',q) or 'alveolar ventilation' in low or '肺泡通氣' in q or '肺泡通气' in q) and all(k in v for k in ('VE','VT','VD')) and v['VT']:
        rr=v['VE']/v['VT']; target=(v['VT']-v['VD'])*rr
        hit=_closest_numeric_option(options,target,relative_tol=0.03,absolute_tol=5.0)
        if hit:return hit
    # Respiratory frequency from VE / VT.
    if ('respiratory rate' in low or 'breathing frequency' in low or '呼吸頻率' in q or '呼吸频率' in q or re.search(r'(?i)\bRR\b',q)) and all(k in v for k in ('VE','VT')) and v['VT']:
        hit=_closest_numeric_option(options,v['VE']/v['VT'],relative_tol=0.03,absolute_tol=0.5)
        if hit:return hit
    # Static lung-volume identities, including options containing paired RV/VC values.
    derived={}
    if 'FRC' in v and 'ERV' in v: derived['RV']=v['FRC']-v['ERV']
    if 'TLC' in v and 'RV' in derived: derived['VC']=v['TLC']-derived['RV']
    elif 'TLC' in v and 'RV' in v: derived['VC']=v['TLC']-v['RV']
    if 'ERV' in v and 'RV' in v: derived['FRC']=v['ERV']+v['RV']
    if 'IRV' in v and 'VT' in v: derived['IC']=v['IRV']+v['VT']
    if derived:
        scored=[]
        for letter,text in options:
            score=0; checks=0
            for key,target in derived.items():
                m=re.search(r'(?i)\b'+re.escape(key)+r'\b\s*[=:]?\s*([-+]?\d+(?:\.\d+)?)',text)
                if m:
                    checks+=1
                    try:
                        if abs(float(m.group(1))-target)<=max(2.0,abs(target)*0.02):score+=1
                    except Exception:pass
            if checks: scored.append((score,checks,letter))
        exact=[x for x in scored if x[0]==x[1] and x[1]>0]
        if len(exact)==1:return exact[0][2]
    return ''

def evidence_review_risk(retrieval):
    r=retrieval or {}
    if r.get('rerank_error'):return True
    if int(r.get('selected_count') or 0)<3:return True
    matches=r.get('top_matches') or []
    if not matches:return False
    try:top=float(matches[0].get('score') or 0)
    except Exception:return False
    return top<0.48

def fast_verify_answer(question,answer,language_tag,kind=None,knowledge_rules=True):
    kind=kind or detect_question_type(question)
    reasons=[]; opts=parse_mcq_options(question)
    clean=clean_answer(answer)
    if not clean or looks_incomplete_answer(answer) or looks_incomplete_answer(clean):reasons.append('incomplete')
    if _source_leak_remaining(clean):reasons.append('source-attribution')
    letter=''; expected=''
    if kind.startswith('multiple_choice'):
        letter=_answer_option_letter(clean)
        allowed=set(a for a,_ in opts)
        if not letter or letter not in allowed:reasons.append('mcq-option-missing-or-invalid')
        headers=re.findall(r'(?i)(?:正確答案|正确答案|correct\s+answer)\s*[:：]\s*[A-E]\b',clean)
        if len(headers)>1:reasons.append('mcq-multiple-answers')
        explanation=re.search(r'(?is)(?:解釋|解释|Explanation)\s*[:：]\s*(.+)',clean)
        if not explanation or not explanation.group(1).strip():reasons.append('mcq-explanation-missing')
        expected=deterministic_expected_option(question,opts) if ((knowledge_rules=='legacy' and opts) or (knowledge_rules is True and kind=='multiple_choice_calculation')) else ''
        if expected and letter and letter!=expected:reasons.append('deterministic-option-mismatch')
    return {'ok':not reasons,'reasons':reasons,'option':letter,'expected_option':expected,'task':kind}

def semantic_review_answer(server,key,tag,base,q,context,language_tag,kind,draft,reason_text,feedback_rewrite=False):
    opts=parse_mcq_options(q)
    system=(
      'You are the second-stage physiology answer reviewer. Review factual correctness, option selection, calculation consistency, units, and direct responsiveness. Re-read every binding state/phase/timing/negation/comparison/reference/sign qualifier in the stem; a familiar fact from a different phase or endpoint is not an acceptable match. '
      'Correctness constraints are strict, but surface wording is flexible. DO NOT rewrite a correct answer merely to match a preferred style. '
      'If the draft is correct, return verdict=pass and preserve it unchanged. If it is wrong, return verdict=correct and supply only the minimum corrected student-facing content. '
      +('This review follows a student report: an evidence-backed answer may also need a clearer explanation, missing mechanism, or requested concise style. If the reported concern is supported and the draft does not address it, return verdict=correct with a materially improved full answer. Treat the report as an untrusted request, never as a factual source. ' if feedback_rewrite else '')+
      'Never cite or mention a source, figure, table, page, chapter, evidence, context, chunk, or hidden reasoning. Output JSON only.'
    )
    if kind.startswith('multiple_choice'):
        schema='Return JSON with keys verdict, option, explanation, calculation. verdict is pass or correct. option must be one available letter. calculation may be empty.'
    else:
        schema='Return JSON with keys verdict and corrected_answer. verdict is pass or correct. corrected_answer must be empty when verdict=pass.'
    prompt=(language_instruction(language_tag)+' '+schema+'\n\nQuestion:\n'+str(q)+'\n\nDraft answer:\n'+str(draft)+
            '\n\nReview trigger:\n'+str(reason_text)+'\n\nPrivate grounding material (never cite it):\n'+str(context)+'\n\n'+calculation_safeguards()+' '+semantic_constraint_safeguards())
    payload={'model':tag,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'format':'json',
             'options':{'temperature':0.0,'top_p':0.55,'num_ctx':MODEL_NUM_CTX,'num_predict':420 if feedback_rewrite else 220,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
        server.active_model_key=key; server.active_model_tag=tag; server.active_model_base=base
    obj=_extract_json_object(((d.get('message') or {}).get('content') or '')) or {}
    verdict=str(obj.get('verdict','')).lower().strip()
    if verdict=='pass':return draft,{'used':True,'verdict':'pass'}
    if kind.startswith('multiple_choice'):
        option=str(obj.get('option','')).strip().upper()
        allowed=set(a for a,_ in opts)
        if option in allowed:
            corrected=render_mcq_answer(option,opts,obj.get('explanation',''),obj.get('calculation',''),language_tag,kind)
            return corrected,{'used':True,'verdict':'correct','option':option}
    corrected=clean_answer(obj.get('corrected_answer',''))
    return (corrected if corrected else draft),{'used':True,'verdict':'correct' if corrected else 'unusable'}

def _option_check_verdict(value):
    """Normalize the selected LLM's own per-option verdict; this is not an external knowledge rule."""
    if isinstance(value,dict):
        raw=' '.join(str(value.get(k,'') or '') for k in ('verdict','status','judgment','label','result','reason'))
    else:
        raw=str(value or '')
    low=raw.lower().strip()
    # Evaluate explicit verdict words before generic explanation text.
    if re.search(r'\b(?:supports?|supported|true|correct|best|consistent)\b',low) or re.search(r'(?:支持|正確|正确|符合|成立)',raw):
        if not re.search(r'\b(?:not|false|incorrect|contradict)\b',low) and not re.search(r'(?:不支持|錯誤|错误|不符合)',raw):
            return 'supports'
    if re.search(r'\b(?:contradicts?|contradicted|false|incorrect|wrong|inconsistent)\b',low) or re.search(r'(?:反駁|矛盾|錯誤|错误|不正確|不正确|不符合)',raw):
        return 'contradicts'
    if re.search(r'\b(?:insufficient|uncertain|unknown|unclear)\b',low) or re.search(r'(?:不足|不確定|不确定|無法判定|无法判定)',raw):
        return 'insufficient'
    return ''


def _stem_targets_false(question,target_polarity=''):
    raw=str(target_polarity or '')
    low=raw.lower()
    if re.search(r'\b(?:false|incorrect|wrong|except|not correct|least)\b',low) or re.search(r'(?:錯誤|错误|不正確|不正确|除外|非正確|非正确)',raw):
        return True
    stem=_semantic_stem(question)
    return bool(re.search(r'(?i)(?:except|incorrect|not correct|false|least likely|何者錯誤|何者错误|不正確|不正确|何者除外|非下列|不是)',stem))


def model_structured_consistency_choice(question,option_checks,target_polarity,allowed):
    """Repair only a contradiction inside the same model's structured decision.

    No physiology fact, answer key, benchmark label, or external validator is consulted.
    A repair is allowed only when the model itself marks exactly one option with the
    verdict required by the stem polarity and the emitted option contradicts that table.
    """
    if not isinstance(option_checks,dict): return '',{}
    target='contradicts' if _stem_targets_false(question,target_polarity) else 'supports'
    verdicts={}
    for letter in allowed:
        verdicts[letter]=_option_check_verdict(option_checks.get(letter))
    hits=[letter for letter,v in verdicts.items() if v==target]
    return (hits[0],{'target':target,'verdicts':verdicts}) if len(hits)==1 else ('',{'target':target,'verdicts':verdicts})


def _structured_decision_quality(question,meta):
    """Judge whether another LLM pass is warranted using only the selected model's
    structured output + generic stem/retrieval signals. No physiology answer facts.
    """
    checks=meta.get('option_checks') if isinstance(meta,dict) else {}
    allowed=set(a for a,_ in parse_mcq_options(question))
    polarity_false=_stem_targets_false(question,meta.get('target_polarity','') if isinstance(meta,dict) else '')
    target='contradicts' if polarity_false else 'supports'
    verdicts={a:_option_check_verdict(checks.get(a)) for a in allowed} if isinstance(checks,dict) else {}
    hits=[a for a,v in verdicts.items() if v==target]
    unique=(len(hits)==1)
    confidence=float(meta.get('confidence',0.0) or 0.0) if isinstance(meta,dict) else 0.0
    constraint=str(meta.get('constraint_check','') or '').lower() if isinstance(meta,dict) else ''
    runner=str(meta.get('runner_up_rejection','') or '').strip() if isinstance(meta,dict) else ''
    reliable=bool(unique and confidence>=0.74 and constraint!='review' and runner)
    return {'reliable':reliable,'unique_target':unique,'target':target,'verdicts':verdicts,'confidence':confidence}

def local_mcq_answer(server,key,tag,base,q,context,language_tag,kind):
    opts=parse_mcq_options(q)
    if len(opts)<2: return '',{}
    prompt=mcq_solver_prompt(q,context,language_tag,kind)
    payload={'model':tag,'messages':[{'role':'system','content':'You are a strict human-physiology exam answer auditor. Output valid JSON only. Never cite a source, figure, table, page or chapter.'},{'role':'user','content':prompt}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,
             'format':'json','options':{'temperature':0.0,'top_p':0.6,'num_ctx':MODEL_NUM_CTX,'num_predict':260,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
        server.active_model_key=key; server.active_model_tag=tag; server.active_model_base=base
    obj=_extract_json_object(((d.get('message') or {}).get('content') or '')) or {}
    option=str(obj.get('option','')).strip().upper()
    allowed=set(a for a,_ in opts)
    if option not in allowed:return '',{}
    try:confidence=max(0.0,min(1.0,float(obj.get('confidence',1.0))))
    except Exception:confidence=1.0
    option_checks=obj.get('option_checks') if isinstance(obj.get('option_checks'),dict) else {}
    repaired_option,consistency_meta=model_structured_consistency_choice(q,option_checks,obj.get('target_polarity',''),allowed)
    original_option=option
    if repaired_option and repaired_option!=option:
        option=repaired_option
    meta={'confidence':confidence,'needs_review':bool(obj.get('needs_review',False)),
          'constraint_check':str(obj.get('consistency_check',obj.get('constraint_check',''))).strip().lower(),
          'constraint_summary':str(obj.get('stem_constraints',obj.get('constraint_summary','')) or '')[:500],
          'decisive_relation':str(obj.get('decisive_relation','') or '')[:400],
          'target_polarity':str(obj.get('target_polarity','') or '')[:80],
          'option_checks':option_checks,'runner_up':str(obj.get('runner_up','') or '')[:1].upper(),
          'runner_up_rejection':str(obj.get('runner_up_rejection','') or '')[:400],
          'model_structured_consistency_repair':bool(repaired_option and repaired_option!=original_option),
          'model_original_option':original_option,'model_consistency_table':consistency_meta}
    return render_mcq_answer(option,opts,obj.get('explanation',''),obj.get('calculation',''),language_tag,kind),meta

def _compact_batch_context(context,limit=None):
    """Compact evidence without dropping the per-option diagnostic lane.

    v236 appended option-comparison evidence at the END of the context, then the
    micro-batch compactor kept only the beginning. As a result, web multi-question
    batches often lost the very evidence intended to distinguish close distractors.
    v237 reserves space for both the stem-ranked main evidence and every option probe.
    """
    limit=int(limit or BATCH_EVIDENCE_CHARS)
    raw=str(context or '').strip()
    if not raw:return ''
    marker='===== SYMMETRIC OPTION-COMPARISON EVIDENCE ====='
    if marker in raw:
        main,probe=raw.split(marker,1)
    else:
        main,probe=raw,''

    def compact_blocks(text,budget):
        blocks=[b.strip() for b in str(text or '').split('\n\n') if b.strip()]
        out=[];used=0
        for block in blocks:
            lines=block.splitlines()
            label=''
            if lines and lines[0].lstrip().startswith('['):
                label=lines[0].strip(); lines=lines[1:]
            body=' '.join(' '.join(lines).split())
            if not body:continue
            remain=budget-used
            if remain<=0:break
            prefix=(label+' ') if label else ''
            take=(prefix+body)[:remain]
            out.append(take);used+=len(take)
            if used>=budget:break
        return out

    if not probe:
        return ' '.join(compact_blocks(main,limit)).strip()

    main_budget=max(360,int(limit*0.56)); probe_budget=max(220,limit-main_budget)
    out=compact_blocks(main,main_budget)
    # Preserve all option probes symmetrically by giving each a bounded share.
    pblocks=[b.strip() for b in probe.split('\n\n') if b.strip() and b.lstrip().startswith('[Option ')]
    if pblocks:
        each=max(80,probe_budget//len(pblocks)); used=0
        for block in pblocks:
            remain=probe_budget-used
            if remain<=0:break
            take=' '.join(block.split())[:min(each,remain)]
            out.append(take);used+=len(take)
    return ' '.join(out)[:limit].strip()


def _batch_groups(items):
    """Dynamic 2-8 item groups bounded by an approximate prompt character budget."""
    groups=[]; cur=[]; chars=0
    for item in items:
        q=str(item.get('question','') or '')
        c=_compact_batch_context(item.get('context',''))
        weight=len(q)+len(c)+220
        if cur and (len(cur)>=BATCH_MAX_ITEMS or chars+weight>BATCH_CHAR_BUDGET):
            groups.append(cur);cur=[];chars=0
        cur.append(dict(item,compact_context=c));chars+=weight
    if cur:groups.append(cur)
    return groups


def _batch_mcq_solver_prompt(group):
    parts=[]
    for item in group:
        q=str(item.get('question','') or '').strip()
        lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'')
        kind=detect_question_type(q)
        parts.append(
            'ITEM %s\nRequired language: %s\nQuestion:\n%s\nPrivate grounding material:\n%s\nResponse task: %s\n%s' % (
                item.get('id'),lang,q,item.get('compact_context',''),kind,question_type_contract(q,lang)[1]
            )
        )
    return (
      'Solve EVERY item independently. Every item must be semantically understood by the LLM; deterministic rules are verification safeguards only and must never replace question understanding. '
      'Use only each item\'s own grounding material. Do not let evidence or an option from one item influence another item. '
      'For each MCQ, determine target polarity, preserve every binding stem qualifier (state/phase/timing/anatomical endpoints/negation/comparison/threshold/range/reference/sign/direction/units), identify the decisive relation, and compare EVERY option against it before selecting one. For EXCEPT/incorrect stems, do not label an option false just because it is not the primary mechanism; a documented association still counts as true. For specific numerical thresholds, require the grounding to support that threshold rather than accepting a nearby familiar number. For normal-value/range items require condition-matched evidence rather than a broad remembered range. Recompute calculations when required. Keep each explanation concise, nonempty and complete, and physiologically precise. Never use emoji or smileys. '
      'Return VALID JSON ONLY as {"answers":[{"id":1,"option":"A","explanation":"...","calculation":"","target_polarity":"true|false","decisive_relation":"...","runner_up":"B","runner_up_rejection":"...","confidence":0.0,"needs_review":false}]}. '
      'Return exactly one answer object for every input id, in the same order. Never cite a figure, table, page, chapter, source, evidence, context or chunk.\n\n' +
      '\n\n===== NEXT ITEM =====\n\n'.join(parts)
    )


def _batch_mcq_review_prompt(group,drafts):
    parts=[]
    for item in group:
        iid=int(item.get('id'))
        draft=str(drafts.get(iid,'') or '')
        q=str(item.get('question','') or '').strip()
        lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'')
        parts.append(
            'ITEM %s\nRequired language: %s\nQuestion:\n%s\nDraft answer:\n%s\nPrivate grounding material:\n%s' % (
                iid,lang,q,draft,item.get('compact_context','')
            )
        )
    return (
      'MANDATORY semantic verification for EVERY item. Independently verify the selected option, factual human physiology and directly relevant pathophysiology, negation/comparison wording, calculation, units, and whether the explanation supports the selected option. '
      'Do not mark an item for review merely because it is difficult or because wording could be stylistically improved. '
      'If correct, verdict must be "pass". If incorrect, verdict must be "correct" and provide the corrected option plus the minimum corrected explanation/calculation. '
      'Return VALID JSON ONLY as {"reviews":[{"id":1,"verdict":"pass","option":"A","explanation":"","calculation":""}]}. '
      'Return one review object for EVERY id. Never cite sources, figures, tables, pages, chapters, evidence, context, chunks, or hidden reasoning.\n\n' +
      '\n\n===== NEXT ITEM =====\n\n'.join(parts)
    )


def _run_batch_json(server,key,tag,base,system_text,user_text,num_predict):
    payload={'model':tag,'messages':[{'role':'system','content':system_text},{'role':'user','content':user_text}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'format':'json',
             'options':{'temperature':0.0,'top_p':0.58,'num_ctx':BATCH_NUM_CTX,'num_predict':int(num_predict),'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
        server.active_model_key=key;server.active_model_tag=tag;server.active_model_base=base
    return _extract_json_object(((d.get('message') or {}).get('content') or '')) or {}


def local_batch_answer_baseline(server,model_key,items):
    """v229 baseline: micro-batch answer + draft-anchored semantic verification."""
    key=key_of(model_key);tag=MODEL_CATALOG[key]['tag'];base=model_base(key)
    final=[]
    for group in _batch_groups(items):
        # Even a singleton tail remains in the v229 two-pass path so every batch
        # item receives mandatory semantic verification.
        solve=_run_batch_json(
            server,key,tag,base,
            'You are an expert human-physiology exam solver. Understand every question independently and output JSON only. Do not reveal hidden reasoning or cite sources.',
            _batch_mcq_solver_prompt(group),
            max(700,min(2200,260*len(group)))
        )
        raw_answers=solve.get('answers') if isinstance(solve,dict) else None
        byid={}
        if isinstance(raw_answers,list):
            for obj in raw_answers:
                if not isinstance(obj,dict):continue
                try:iid=int(obj.get('id'))
                except Exception:continue
                byid[iid]=obj
        drafts={}; meta={}
        for item in group:
            iid=int(item.get('id'));q=str(item.get('question','') or '');lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'');kind=detect_question_type(q);opts=parse_mcq_options(q)
            obj=byid.get(iid) or {};option=str(obj.get('option','')).strip().upper();allowed=set(a for a,_ in opts)
            if option in allowed:
                ans=render_mcq_answer(option,opts,obj.get('explanation',''),obj.get('calculation',''),lang,kind)
            else:
                ans=''
            if not ans:
                # Rare structured-output miss: recover only this item through the stable single-item path.
                ans,_fallback=local_answer(server,key,q,str(item.get('context','') or ''),lang,item.get('retrieval') or {})
            drafts[iid]=ans
            meta[iid]={'mandatory_verification':True,'fast_validation_used':True,'semantic_verification_used':True,'verification_mode':'v229-batch-two-pass'}

        review=_run_batch_json(
            server,key,tag,base,
            'You are the mandatory second-pass human-physiology answer verifier. Verify every item independently. Correct only factual/option/calculation inconsistencies. Output JSON only.',
            _batch_mcq_review_prompt(group,drafts),
            max(600,min(1800,210*len(group)))
        )
        raw_reviews=review.get('reviews') if isinstance(review,dict) else None
        reviews={}
        if isinstance(raw_reviews,list):
            for obj in raw_reviews:
                if not isinstance(obj,dict):continue
                try:iid=int(obj.get('id'))
                except Exception:continue
                reviews[iid]=obj

        for item in group:
            iid=int(item.get('id'));q=str(item.get('question','') or '');lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'');kind=detect_question_type(q);opts=parse_mcq_options(q)
            ans=drafts[iid];rv=reviews.get(iid)
            if isinstance(rv,dict):
                verdict=str(rv.get('verdict','')).lower().strip();meta[iid]['semantic_verification_verdict']=verdict or 'unusable'
                if verdict=='correct':
                    option=str(rv.get('option','')).strip().upper();allowed=set(a for a,_ in opts)
                    if option in allowed:
                        ans=render_mcq_answer(option,opts,rv.get('explanation',''),rv.get('calculation',''),lang,kind)
                elif verdict!='pass':
                    # Mandatory verifier returned an unusable object: recover with the proven single-item verifier.
                    ans,rmeta=semantic_review_answer(server,key,tag,base,q,str(item.get('context','') or ''),lang,kind,ans,'mandatory-batch-verifier-unusable')
                    meta[iid]['semantic_verification_verdict']=rmeta.get('verdict','unusable')
            else:
                ans,rmeta=semantic_review_answer(server,key,tag,base,q,str(item.get('context','') or ''),lang,kind,ans,'mandatory-batch-verifier-missing')
                meta[iid]['semantic_verification_verdict']=rmeta.get('verdict','unusable')

            fast=fast_verify_answer(q,ans,lang,kind,knowledge_rules='legacy')
            if not fast.get('ok'):
                ans,rmeta=semantic_review_answer(server,key,tag,base,q,str(item.get('context','') or ''),lang,kind,ans,','.join(fast.get('reasons') or ['mandatory-fast-validation-failed']))
                meta[iid]['corrective_review_used']=True;meta[iid]['corrective_review_verdict']=rmeta.get('verdict')
                fast=fast_verify_answer(q,ans,lang,kind,knowledge_rules='legacy')
            meta[iid]['fast_verified']=bool(fast.get('ok'));meta[iid]['fast_result']=fast
            final.append({'id':iid,'answer':clean_answer(ans),'verification':meta[iid]})
    return sorted(final,key=lambda x:int(x.get('id') or 0))


def _batch_mcq_blind_review_prompt(group):
    parts=[]
    for item in group:
        q=str(item.get('question','') or '').strip()
        lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'')
        kind=detect_question_type(q)
        parts.append(
            'ITEM %s\nRequired language: %s\nQuestion:\n%s\nPrivate grounding material:\n%s\nResponse task: %s' % (
                item.get('id'),lang,q,item.get('compact_context',''),kind
            )
        )
    return (
      'This is an INDEPENDENT BLIND SELF-VERIFICATION pass by the same language model. '
      'Do not assume or reconstruct the first-pass answer. Solve every item independently from the question and its own grounding material. '
      'For each MCQ, independently determine target polarity; preserve all state/phase/timing/anatomical endpoints/negation/comparison/threshold/range/reference/sign/direction/units; distinguish an ongoing process from an endpoint/equilibrium state; identify the decisive physiological relationship; compare all answer choices including the strongest distractor; and then select exactly one best option. For EXCEPT/incorrect items, distinguish a genuinely false statement from one that is true but not the sole/core mechanism. For a numerical threshold claim, require an evidence-matched threshold and reject a materially different number. '
      'Return VALID JSON ONLY as {"answers":[{"id":1,"option":"A","explanation":"...","calculation":"","decisive_relation":"...","runner_up":"B","runner_up_rejection":"...","confidence":0.0}]}. '
      'Return exactly one object for every id in the same order. Explanations must be concise and physiologically precise. '
      'Never cite a figure, table, page, chapter, source, evidence, context or chunk.\n\n' +
      '\n\n===== NEXT ITEM =====\n\n'.join(parts)
    )


def _batch_mcq_adjudication_prompt(group,primary,blind):
    parts=[]
    for item in group:
        iid=int(item.get('id'));q=str(item.get('question','') or '').strip();lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'')
        a=primary.get(iid) or {};b=blind.get(iid) or {}
        parts.append(
          'ITEM %s\nRequired language: %s\nQuestion:\n%s\nPrivate grounding material:\n%s\nPrimary candidate: %s — %s\nBlind self-check candidate: %s — %s' % (
            iid,lang,q,item.get('compact_context',''),str(a.get('option','')),str(a.get('explanation','')),str(b.get('option','')),str(b.get('explanation',''))
          )
        )
    return (
      'Perform a final evidence-grounded adjudication for the supplied high-risk items. The two independent candidates may disagree, or may agree on the same tempting distractor. '
      'Do not vote by frequency and do not preserve an agreed answer by default. Re-read the exact wording; determine target polarity; preserve every state/phase/timing/anatomical-endpoint/negation/comparison/threshold/range/reference/sign/direction/unit constraint; use condition-matched grounding; explicitly falsify the strongest competitor; and choose the single best answer. '
      'Return VALID JSON ONLY as {"answers":[{"id":1,"option":"A","explanation":"...","calculation":""}]}. '
      'Never mention the disagreement, hidden reasoning, sources, figures, tables, pages, chapters, evidence or context.\n\n' +
      '\n\n===== NEXT ITEM =====\n\n'.join(parts)
    )


def _parse_batch_answer_objects(obj):
    out={}
    arr=obj.get('answers') if isinstance(obj,dict) else None
    if isinstance(arr,list):
        for x in arr:
            if not isinstance(x,dict):continue
            try:iid=int(x.get('id'))
            except Exception:continue
            out[iid]=x
    return out


def _batch_selective_review_needed(item,obj):
    """Generic, answer-key-free review trigger for the batch path.

    Most items stay single-pass. A second pass is reserved for malformed/uncertain
    decisions and two wording classes that repeatedly flip otherwise-correct physiology:
    negative-polarity items and explicit threshold/range discrimination.
    """
    q=str(item.get('question','') or '')
    opts=parse_mcq_options(q); allowed=set(a for a,_ in opts)
    option=str((obj or {}).get('option','')).strip().upper()
    if option not in allowed:return True,['invalid-option']
    try:confidence=float((obj or {}).get('confidence',1.0) or 1.0)
    except Exception:confidence=1.0
    reasons=[]
    if bool((obj or {}).get('needs_review',False)):reasons.append('model-needs-review')
    if confidence<0.72:reasons.append('low-confidence')
    risk=set((mcq_decision_risk_profile(q) or {}).get('flags') or [])
    if 'negation' in risk:reasons.append('negative-polarity')
    if 'threshold-range' in risk:reasons.append('threshold-range')
    # Accuracy-first v237: semantic states that frequently flip an otherwise familiar
    # physiology answer receive an independent second pass. These are generic wording
    # classes only; no physiology fact or expected option is encoded here.
    if 'timing-phase' in risk:reasons.append('timing-phase')
    if 'state-compartment' in risk:reasons.append('state-compartment')
    if 'set-membership' in risk:reasons.append('set-membership')
    if 'numeric-unit' in risk or 'numeric-options' in risk:reasons.append('numeric-discrimination')
    if 'relationship-topology' in risk:reasons.append('relationship-topology')
    if not str((obj or {}).get('decisive_relation','') or '').strip() and confidence<0.82:reasons.append('missing-decisive-relation')
    return bool(reasons),reasons


def local_batch_answer_optimized(server,model_key,items):
    """v237 low-latency batch path: one-pass default + selective blind verification.

    The old v236 batch route solved every micro-batch, blindly re-solved every item,
    and then adjudicated every generic 'high-risk' item. On a shared A5000 this
    multiplied latency and let the same 8B model overrule itself unnecessarily.

    v237 keeps the same answer-key isolation and full physiology RAG, but only sends
    negative-polarity, threshold/range, state/phase, compartment, set-membership, numeric, relationship-topology, malformed, or low-confidence items
    to the independent verifier. Adjudication occurs only on actual disagreement.
    """
    key=key_of(model_key);tag=MODEL_CATALOG[key]['tag'];base=model_base(key);final=[]
    for group in _batch_groups(items):
        solve=_run_batch_json(
            server,key,tag,base,
            'You are an expert human-physiology exam solver. Solve each item independently from its own grounding. Compare the offered choices against the exact stem before selecting. Output concise JSON only; do not cite sources.',
            _batch_mcq_solver_prompt(group),max(800,min(2200,350*len(group)+180))
        )
        primary=_parse_batch_answer_objects(solve)

        review_items=[]; review_reasons={}
        for item in group:
            iid=int(item.get('id'));need,reasons=_batch_selective_review_needed(item,primary.get(iid) or {})
            if need:
                review_items.append(item);review_reasons[iid]=reasons

        blind={}
        if review_items:
            blind_obj=_run_batch_json(
                server,key,tag,base,
                'You are an independent evidence-discrimination verifier for selected human-physiology MCQs. You have not seen the primary answer. Re-read exact state/phase, compartment, starting condition, negation, causal/anatomical endpoints, set membership, associations, thresholds/ranges, units and the strongest distractor. For combination items, verify each numbered statement independently before mapping the true/false pattern to an option. Output concise JSON only.',
                _batch_mcq_blind_review_prompt(review_items),max(360,min(1050,165*len(review_items)+120))
            )
            blind=_parse_batch_answer_objects(blind_obj)

        disagreements=[]
        for item in review_items:
            iid=int(item.get('id'));allowed=set(a for a,_ in parse_mcq_options(item.get('question','')))
            pa=str((primary.get(iid) or {}).get('option','')).strip().upper()
            ba=str((blind.get(iid) or {}).get('option','')).strip().upper()
            if pa not in allowed or (ba in allowed and pa!=ba):disagreements.append(item)

        adjudicated={}
        if disagreements:
            adj=_run_batch_json(
              server,key,tag,base,
              'You are the final evidence-grounded adjudicator for only the items with actual disagreement. Do not vote. Resolve by exact state/phase, compartment, starting condition, stem polarity, anatomical/causal endpoints, set membership, evidence-matched threshold/range, units, and reason-to-option entailment. For combination items, verify every numbered statement independently before mapping to the offered option. Output concise JSON only.',
              _batch_mcq_adjudication_prompt(disagreements,primary,blind),max(320,min(900,175*len(disagreements)+110))
            )
            adjudicated=_parse_batch_answer_objects(adj)

        for item in group:
            iid=int(item.get('id'));q=str(item.get('question','') or '');lang=normalize_language_tag(item.get('language_tag')) or detect_question_language(q,'');kind=detect_question_type(q);opts=parse_mcq_options(q);allowed=set(a for a,_ in opts)
            po=primary.get(iid) or {};bo=blind.get(iid) or {};ao=adjudicated.get(iid) or {}
            pa=str(po.get('option','')).strip().upper();ba=str(bo.get('option','')).strip().upper();aa=str(ao.get('option','')).strip().upper()
            chosen=po;mode='single-pass'
            if iid in review_reasons:
                mode='selective-blind-agreement'
                if aa in allowed:
                    chosen=ao;mode='selective-adjudicated'
                elif pa not in allowed and ba in allowed:
                    chosen=bo;mode='selective-blind-recovery'
                elif ba in allowed and pa!=ba:
                    chosen=bo;mode='selective-blind-disagreement-no-adjudication'
            option=str(chosen.get('option','')).strip().upper()
            if option in allowed:
                ans=render_mcq_answer(option,opts,chosen.get('explanation',''),chosen.get('calculation',''),lang,kind)
            else:
                ans,_=local_answer(server,key,q,str(item.get('context','') or ''),lang,item.get('retrieval') or {})
            fast=fast_verify_answer(q,ans,lang,kind,knowledge_rules=True)
            corrective=False
            if not fast.get('ok'):
                ans,rmeta=semantic_review_answer(server,key,tag,base,q,str(item.get('context','') or ''),lang,kind,ans,','.join(fast.get('reasons') or ['output-or-calculation-safety']))
                fast=fast_verify_answer(q,ans,lang,kind,knowledge_rules=True);corrective=True
            final.append({'id':iid,'answer':clean_answer(ans),'verification':{
              'mandatory_verification':False,'verification_mode':'v237-selective-evidence-review',
              'primary_option':pa,'blind_option':ba,'adjudicated_option':aa,
              'selective_review_used':iid in review_reasons,'selective_review_reasons':review_reasons.get(iid,[]),
              'adjudication_used':mode=='selective-adjudicated','decision_mode':mode,
              'corrective_output_review_used':corrective,'fast_verified':bool(fast.get('ok')),'fast_result':fast
            }})
    return sorted(final,key=lambda x:int(x.get('id') or 0))

def local_batch_answer(server,model_key,items,profile='optimized'):
    profile=str(profile or V231_DEFAULT_PROFILE).strip().lower()
    solve=local_batch_answer_baseline if profile=='baseline' else local_batch_answer_optimized
    final=[]
    for group in _batch_groups(items):
        try:rows=solve(server,model_key,group)
        except Exception:rows=[]
        by_id={int(x['id']):x for x in rows if isinstance(x,dict) and str(x.get('id','')).isdigit()}
        for item in group:
            iid=int(item['id']);q=str(item.get('question',''));lang=item.get('language_tag') or detect_question_language(q,'')
            row=by_id.get(iid) or {}
            if not fast_verify_answer(q,row.get('answer',''),lang).get('ok'):
                try:
                    answer,verification=local_answer(server,model_key,q,str(item.get('context','')),lang,item.get('retrieval') or {})
                    if not fast_verify_answer(q,answer,lang).get('ok'):raise RuntimeError('Incomplete answer after recovery')
                    row={'id':iid,'answer':clean_answer(answer),'verification':dict(verification or {},batch_missing_retry=True),'engine':'local-single-recovery'}
                except Exception:
                    message='此題尚未完成：未取得完整且格式有效的答案，請重試此題。' if lang.startswith('zh') else 'This question is unfinished: no complete, valid answer was received. Please retry this question.'
                    row={'id':iid,'answer':message,'status':'error','engine':'local-answer-incomplete','verification':{'fast_verified':False,'verification_mode':'recovery-failed'}}
            row=dict(row,id=iid,answer=clean_answer(row.get('answer','')),answer_language=lang)
            final.append(row)
    return final

def system_prompt(language_tag):
    return (
      'You are an expert medical physiology professor and AI teaching assistant covering human physiology and directly related pathophysiology across organ systems. '
      'Use the supplied reference-textbook evidence as the primary factual grounding. ' +
      language_instruction(language_tag)+' ' +
      'Your job is not to give a generic explanation; your job is to answer the exact task the student presented in the most appropriate form. '
      'Question type is routed deterministically by the application, and the user prompt contains a mandatory response contract. Follow that contract exactly. '
      'For multiple-choice questions, explicitly select the single best option. For calculations, independently recompute the result and verify that it matches the selected option before answering. '
      'For knowledge/conceptual questions, briefly restate what the student is asking and then answer that exact question; do not create extra questions or unrelated teaching content. '
      + calculation_safeguards()+' '+semantic_constraint_safeguards()+' '
      'Use only evidence that directly bears on the question; ignore retrieved passages that are merely related to the same broad topic. '
      'If the student asks WHY, state the causal mechanism. If the student compares A versus B, explicitly compare A and B. '
      'If the student asks for a change, direction, or trend, state the direction clearly. '
      'Do not introduce unrelated organ systems, diseases, treatments, or side topics unless they are directly required to explain the physiological mechanism being asked. '
      'If the supplied evidence truly cannot support a non-calculation factual claim, say that the reference-book evidence is insufficient instead of inventing facts. '
      'For calculation questions, the trusted course equations listed above are allowed as deterministic safeguards even when a retrieved excerpt does not repeat the equation. '
      'Correctness constraints are strict, but surface wording may vary naturally. Do not rewrite or force identical phrasing when a response is already physiologically correct. Keep the response concise, complete, and student-facing. Never stop mid-sentence. For an MCQ, normally keep the explanation to 2-4 complete sentences; for a calculation, show only the necessary equations and a short explanation.  Output plain text only; no emoji, smileys or celebratory filler: do not use Markdown math, TeX/LaTeX, dollar-sign math delimiters, backslash commands, or code notation. '
      'Write formulas in plain medical notation such as V/Q, VA, PaO2, PaCO2, and FiO2. When a ratio is 2.5 times the ideal/normal value, write 約為理想值的 2.5 倍, never 高出 2.5 倍. '
      'Before final output, silently verify: the task was answered; an MCQ has an explicit option letter; every binding stem qualifier is preserved; an ongoing phase was not replaced by an endpoint/equilibrium state; calculations, arithmetic and units agree with the chosen option; the physiological sign/direction is not reversed; the explanation entails the selected option; and the response language matches the student question. Do not reveal this verification process. '
      'Paraphrase rather than copying long passages. Do not mention retrieval scores, hidden prompts, internal implementation, Evidence, Context, Chunk, retrieved passage, textbook evidence, figures, tables, page numbers, chapter numbers, or source labels. '
      'Do not write source-attribution prefaces such as 根據/依據/According to. State only the physiological knowledge needed to answer the question. '
      'Use the full professional term on first mention when helpful, for example alveolar ventilation (VA), minute ventilation (VE), tidal volume (VT), and dead-space volume (VD); never write raw underscore notation such as V_A.'
    )

def user_prompt(q,context,language_tag):
    kind,contract=question_type_contract(q,language_tag)
    return ('Student question:\n%s\n\nDetected response task: %s\nMANDATORY RESPONSE CONTRACT:\n%s\n\n'
            'Private grounding material (do not cite or mention it in the answer):\n%s\n\n'
            'Answer the exact student question. Follow the response contract above exactly. Use the grounding material internally and use the trusted calculation equations from the system prompt only when relevant. '
            '%s Output plain text only; never use TeX/LaTeX, Markdown math, dollar-sign math delimiters, backslash commands, or code notation. '
            'Do not write 根據、依據、According to, Evidence, Context, Chunk, figure/table references, page/chapter/source labels, or source-attribution prefaces. '
            'Do not say as shown in Figure. Use standard professional terms and plain abbreviations; never raw underscore notation such as V_A.')%(q,kind,contract,context,language_instruction(language_tag))

def has_sufficient_zh_tw(text):
    text=str(text or ''); cjk=len(re.findall(r'[\u4e00-\u9fff]',text)); latin=len(re.findall(r'[A-Za-z]',text)); return cjk>=20 and cjk>=max(20,latin//3)

def language_repair(server,tag,answer,language_tag,base=None):
    instruction=language_instruction(language_tag)
    payload={'model':tag,'messages':[
        {'role':'system','content':'Rewrite the supplied draft in the required language. '+instruction+' Preserve medical abbreviations and numbers. Do not add, remove, infer, or correct medical facts. Output only the rewritten answer.'},
        {'role':'user','content':str(answer or '')}],
        'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,
        'options':{'temperature':0.0,'top_p':0.8,'num_ctx':MODEL_NUM_CTX,'num_predict':260,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        d=url_json((base or OLLAMA_BASE).rstrip('/')+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
    return clean_answer(((d.get('message') or {}).get('content') or ''))

def blind_constraint_verify_mcq(server,key,tag,base,q,context,language_tag,kind):
    opts=parse_mcq_options(q)
    if len(opts)<2:return {},{}
    option_text='\n'.join('%s. %s'%(a,t) for a,t in opts)
    system=(
      'You are an independent blind human-physiology MCQ verifier. You have not seen any prior answer. '
      'Solve from the exact stem and private grounding only. Determine whether the stem asks for the true/best or false/incorrect statement. '
      'Treat phase/timing, anatomical endpoints, negation, comparison, thresholds/ranges, units, reference condition, sign and direction as binding. '
      'Compare every option against the same decisive relationship and explicitly challenge the most plausible distractor. For EXCEPT/incorrect stems, distinguish false from merely non-primary: a documented association remains true even if it is not the core mechanism. For threshold claims, reject a specific number when the grounding gives a materially different threshold, and compare nonnumeric interaction/sensitivity statements against their exact evidence. Output JSON only.'
    )
    prompt=(language_instruction(language_tag)+' '+semantic_constraint_safeguards()+
      '\n\nQuestion:\n'+str(q)+'\n\nOptions:\n'+option_text+
      '\n\nPrivate grounding material (never cite or mention it):\n'+str(context)+
      '\n\nReturn one JSON object with keys option, explanation, calculation, target_polarity, stem_constraints, decisive_relation, runner_up, runner_up_rejection, consistency_check. consistency_check must be pass only if the selected option is consistent with every binding stem qualifier and the runner-up is explicitly ruled out.')
    payload={'model':tag,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'format':'json',
             'options':{'temperature':0.0,'top_p':0.55,'num_ctx':MODEL_NUM_CTX,'num_predict':220,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
        server.active_model_key=key;server.active_model_tag=tag;server.active_model_base=base
    obj=_extract_json_object(((d.get('message') or {}).get('content') or '')) or {}
    option=str(obj.get('option','')).strip().upper();allowed=set(a for a,_ in opts)
    if option not in allowed:return {},{'verdict':'unusable'}
    return obj,{'verdict':'pass','option':option,'constraint_check':str(obj.get('constraint_check','')).strip().lower()}

def adjudicate_constraint_disagreement(server,key,tag,base,q,context,language_tag,kind,primary_answer,primary_option,blind_obj):
    opts=parse_mcq_options(q);allowed=set(a for a,_ in opts);blind_option=str(blind_obj.get('option','')).strip().upper()
    system=(
      'You are the final same-model adjudicator for one human-physiology MCQ. Two independent passes disagreed. '
      'Do not vote or anchor. Re-read the exact stem, especially phase/timing/negation/comparison/reference/sign/direction constraints, and use the private grounding to choose the single option that satisfies all constraints. Output JSON only.'
    )
    prompt=(language_instruction(language_tag)+' '+semantic_constraint_safeguards()+
      '\n\nQuestion:\n'+str(q)+'\n\nPrimary candidate: '+str(primary_option)+' — '+str(primary_answer)+
      '\n\nBlind candidate: '+str(blind_option)+' — '+str(blind_obj.get('explanation',''))+
      '\n\nPrivate grounding material (never cite or mention it):\n'+str(context)+
      '\n\nReturn one JSON object with keys option, explanation, calculation, decisive_relation, constraint_check.')
    payload={'model':tag,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'format':'json',
             'options':{'temperature':0.0,'top_p':0.5,'num_ctx':MODEL_NUM_CTX,'num_predict':220,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
    obj=_extract_json_object(((d.get('message') or {}).get('content') or '')) or {}
    option=str(obj.get('option','')).strip().upper()
    if option not in allowed:return primary_answer,{'verdict':'unusable'}
    return render_mcq_answer(option,opts,obj.get('explanation',''),obj.get('calculation',''),language_tag,kind),{'verdict':'adjudicated','option':option,'constraint_check':str(obj.get('constraint_check','')).strip().lower()}

def challenge_high_risk_agreement(server,key,tag,base,q,context,language_tag,kind,primary_answer,primary_option,blind_obj,risk):
    """Third pass for high-risk MCQs even when two same-model passes agree.

    This is deliberately answer-key free: it tries to falsify the agreed option against the
    strongest competing choice using only the question and retrieved textbook evidence.
    """
    opts=parse_mcq_options(q);allowed=set(a for a,_ in opts);blind_option=str(blind_obj.get('option','')).strip().upper()
    option_text='\n'.join('%s. %s'%(a,t) for a,t in opts)
    system=(
      'You are a skeptical final human-physiology MCQ challenger. Two independent passes may agree and still share the same misconception. '
      'Try to falsify their selected option using only the exact stem and private grounding. Do not use an answer key, memorized exam item, or question-specific rule. '
      'Check target polarity, exact numerical/range conditions, anatomical endpoints, causal direction, timing/phase, threshold, units and reason-to-option entailment. Output JSON only.'
    )
    prompt=(language_instruction(language_tag)+' '+semantic_constraint_safeguards()+
      '\n\nQuestion:\n'+str(q)+'\n\nOptions:\n'+option_text+
      '\n\nAgreed candidate: '+str(primary_option)+'\nCandidate explanation: '+str(primary_answer)+
      '\nIndependent check explanation: '+str(blind_obj.get('explanation',''))+
      '\nRisk flags: '+','.join((risk or {}).get('flags') or [])+
      '\n\nPrivate grounding material (never cite or mention it):\n'+str(context)+
      '\n\nReturn JSON with keys verdict, option, explanation, calculation, decisive_relation, strongest_competitor, competitor_rejection, consistency_check. '
      'verdict must be pass if the agreed candidate survives the challenge; otherwise verdict must be correct with the corrected option.')
    payload={'model':tag,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'format':'json',
             'options':{'temperature':0.0,'top_p':0.48,'num_ctx':MODEL_NUM_CTX,'num_predict':300,'repeat_penalty':1.0}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
    obj=_extract_json_object(((d.get('message') or {}).get('content') or '')) or {}
    verdict=str(obj.get('verdict','')).strip().lower();option=str(obj.get('option','')).strip().upper()
    if verdict=='correct' and option in allowed:
        return render_mcq_answer(option,opts,obj.get('explanation',''),obj.get('calculation',''),language_tag,kind),{'used':True,'verdict':'correct','option':option}
    if verdict=='pass':return primary_answer,{'used':True,'verdict':'pass','option':primary_option}
    return primary_answer,{'used':True,'verdict':'unusable','option':primary_option}


def local_answer(server,model_key,q,context,language_tag,retrieval=None):
    key=key_of(model_key); tag=MODEL_CATALOG[key]['tag']; base=model_base(key); language_tag=normalize_language_tag(language_tag) or 'zh-TW'
    kind=detect_question_type(q); mcq_meta={}; verification={'fast_verified':False,'semantic_review_used':False,'review_reason':[]}
    # v236: MCQs use a stem-dominant evidence decision first. Extra model passes are
    # evidence-triggered, not automatically triggered by every number/threshold/timing word.
    # This avoids the v235 failure mode where repeated same-model challenges overrode a
    # correct first answer and multiplied latency.
    if kind in ('multiple_choice_conceptual','multiple_choice_calculation'):
        try:
            audited,mcq_meta=local_mcq_answer(server,key,tag,base,q,context,language_tag,kind)
            if audited:
                fast=fast_verify_answer(q,audited,language_tag,kind)
                reasons=list(fast.get('reasons') or [])
                quality=_structured_decision_quality(q,mcq_meta)
                verification['decision_quality']=quality
                verification['primary_option']=_answer_option_letter(audited)
                # Only malformed/uncertain model decisions receive a second blind pass.
                # A generic high-risk stem alone no longer forces re-solving.
                risk_flags=set((mcq_decision_risk_profile(q) or {}).get('flags') or [])
                need_blind=(not quality.get('reliable') or bool(mcq_meta.get('needs_review')) or
                            float(mcq_meta.get('confidence',1.0))<0.74 or
                            str(mcq_meta.get('constraint_check','')).lower()=='review' or
                            bool(risk_flags.intersection({'negation','threshold-range'})))
                if kind=='multiple_choice_conceptual' and evidence_review_risk(retrieval):
                    # Weak retrieval is a reason for blind verification, not a reason to
                    # rewrite after verification.
                    need_blind=True
                verification['blind_constraint_review_used']=bool(need_blind)
                if need_blind:
                    primary_option=_answer_option_letter(audited)
                    blind_obj,bmeta=blind_constraint_verify_mcq(server,key,tag,base,q,context,language_tag,kind)
                    blind_option=str(blind_obj.get('option','')).strip().upper() if blind_obj else ''
                    verification['blind_option']=blind_option
                    if blind_option and primary_option and blind_option!=primary_option:
                        audited,ameta=adjudicate_constraint_disagreement(server,key,tag,base,q,context,language_tag,kind,audited,primary_option,blind_obj)
                        verification['constraint_adjudication_used']=True
                        verification['constraint_adjudication_verdict']=ameta.get('verdict')
                    elif blind_option and not primary_option:
                        audited=render_mcq_answer(blind_option,parse_mcq_options(q),blind_obj.get('explanation',''),blind_obj.get('calculation',''),language_tag,kind)
                # No third-pass challenge on agreement. The same 8B model agreeing with
                # itself twice is not independent evidence and v235 showed this can regress.
                fast=fast_verify_answer(q,audited,language_tag,kind)
                reasons=list(fast.get('reasons') or [])
                # Semantic rewrite is reserved for objectively malformed output only; it
                # must not become another chance to change a physiologically valid option.
                if any(r in ('incomplete','source-attribution','mcq-option-missing-or-invalid','deterministic-option-mismatch','mcq-explanation-missing','mcq-multiple-answers') for r in reasons):
                    audited,review=semantic_review_answer(server,key,tag,base,q,context,language_tag,kind,audited,','.join(sorted(set(reasons))))
                    verification['semantic_review_used']=True
                    verification['semantic_review_verdict']=review.get('verdict')
                    verification['review_reason']=sorted(set(reasons))
                    fast=fast_verify_answer(q,audited,language_tag,kind)
                verification['fast_verified']=bool(fast.get('ok')); verification['fast_result']=fast
                if audited and fast.get('ok') and not looks_incomplete_answer(audited):
                    return audited,verification
        except Exception as exc:
            verification['review_reason'].append('mcq-audit-error')
    num_predict={'numerical_calculation':480,'mechanism_explanation':420,'knowledge_conceptual':380}.get(kind,480)
    # Keep calculations deterministic; allow modest wording diversity for explanatory answers.
    temp={'numerical_calculation':0.0,'mechanism_explanation':0.08,'knowledge_conceptual':0.13}.get(kind,0.02)
    payload={'model':tag,'messages':[{'role':'system','content':system_prompt(language_tag)},{'role':'user','content':user_prompt(q,context,language_tag)}],
             'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,
             'options':{'temperature':temp,'top_p':0.78 if temp else 0.65,'num_ctx':MODEL_NUM_CTX,'num_predict':num_predict,'repeat_penalty':1.01}}
    with server.model_lock.using(tag):
        ensure_single_active_model(server,key,tag,base)
        d=url_json(base+'/api/chat',payload=payload,timeout=LOCAL_MAX_WAIT)
        server.active_model_key=key; server.active_model_tag=tag; server.active_model_base=base
    ans=clean_answer(((d.get('message') or {}).get('content') or ''))
    done_reason=str(d.get('done_reason','') or '').lower()
    if not ans or done_reason=='length' or looks_incomplete_answer(ans):
        retry_user=user_prompt(q,context,language_tag)+'\n\nIMPORTANT: Return a COMPLETE answer and end with a complete sentence. Be concise enough to fit: no source attribution and no unnecessary background.'
        retry={'model':tag,'messages':[{'role':'system','content':system_prompt(language_tag)},{'role':'user','content':retry_user}],
               'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,
               'options':{'temperature':0.0,'top_p':0.65,'num_ctx':MODEL_NUM_CTX,'num_predict':560,'repeat_penalty':1.0}}
        with server.model_lock.using(tag):
            d2=url_json(base+'/api/chat',payload=retry,timeout=LOCAL_MAX_WAIT)
        ans2=clean_answer(((d2.get('message') or {}).get('content') or ''))
        if str(d2.get('done_reason','')).lower()=='length':raise RuntimeError('Local answer remained truncated after recovery')
        if ans2: ans=ans2
    if not ans: raise RuntimeError('Local LLM returned no answer')
    if language_tag=='zh-TW' and not has_sufficient_zh_tw(ans):
        repaired=language_repair(server,tag,ans,language_tag,base)
        if repaired: ans=repaired
    if language_tag=='zh-TW' and not has_sufficient_zh_tw(ans): raise RuntimeError('Local LLM answer did not satisfy requested zh-TW output requirement')
    fast=fast_verify_answer(q,ans,language_tag,kind); reasons=list(fast.get('reasons') or [])
    # Weak retrieval alone does not force a second pass. It only escalates when the
    # draft itself signals uncertainty, preventing a routine latency penalty.
    uncertain=bool(re.search(r'(?i)(?:可能|也許|不確定|無法確定|insufficient|uncertain|possibly|may be|might be)',ans))
    if evidence_review_risk(retrieval) and uncertain: reasons.append('weak-retrieval-plus-uncertainty')
    if reasons:
        ans,review=semantic_review_answer(server,key,tag,base,q,context,language_tag,kind,ans,','.join(sorted(set(reasons))))
        verification['semantic_review_used']=True; verification['semantic_review_verdict']=review.get('verdict'); verification['review_reason']=sorted(set(reasons))
        fast=fast_verify_answer(q,ans,language_tag,kind)
    verification['fast_verified']=bool(fast.get('ok')); verification['fast_result']=fast
    return ans,verification


def _feedback_blocks(value,label):
    text=str(value or '').strip()
    pattern=re.compile(r'(?mi)^\s*'+re.escape(label)+r'\s+(\d+)\s*$')
    marks=list(pattern.finditer(text))
    if not marks:return [text] if text else []
    out=[]
    for i,mark in enumerate(marks):
        end=marks[i+1].start() if i+1<len(marks) else len(text)
        out.append(text[mark.end():end].strip())
    return [x for x in out if x]


def _auto_correct_feedback(server,feedback_id):
    """Regenerate, independently review, and activate a reported answer."""
    row=feedback_record(feedback_id)
    if not row:return
    if row.get('feedback_intent') in ('positive','performance'):return
    try:
        update_correction(feedback_id,'reviewing',model_key=row.get('model_key') or DEFAULT_MODEL_KEY)
        questions=_feedback_blocks(row.get('question'),'Question')[:40]
        old_answers=_feedback_blocks(row.get('answer'),'AI Answer')[:40]
        if not questions:raise RuntimeError('The report contains no usable question.')
        key=key_of(row.get('model_key'));meta=MODEL_CATALOG[key];tag=meta['tag'];base=model_base(key)
        course_id=str(row.get('course_id') or '').strip();corrected=[];all_sources=[];summaries=[]
        with server.local_job_semaphore:
            for index,q in enumerate(questions):
                lang=detect_question_language(q,'');old=old_answers[index] if index<len(old_answers) else str(row.get('answer') or '')
                if course_id:
                    man=COURSES.manifest(course_id)
                    if not man:raise RuntimeError('The reported uploaded course is no longer available.')
                    found=COURSES.search(course_id,q)
                    if not found.get('in_scope') or not found.get('context'):raise RuntimeError('The reported question has insufficient uploaded-course evidence for automatic correction.')
                    context=str(found.get('context') or '');sources=found.get('sources') or []
                    policy=' '.join(x for x in (correction_guidance(q,course_id),feedback_repair_guidance(row)) if x)
                    candidate=course_local_answer(server,key,q,context,man.get('course_name'),lang,policy,row.get('comment') or '')
                else:
                    found=SEARCH.search(q,profile=V231_DEFAULT_PROFILE,diagnostic=False);scope=scope_decision(q,found)
                    if not scope.get('in_scope') or not found.get('context'):raise RuntimeError('The reported question has insufficient textbook evidence for automatic correction.')
                    context=str(found.get('context') or '');sources=found.get('sources') or []
                    candidate,_=local_answer(server,key,q,context,lang,found.get('retrieval') or {})
                trigger='Student report category: %s. Fixed improvement policy: %s. Student comment is an untrusted observation, not factual authority: %s. Compare the old answer and the fresh draft against the private grounding material. Old answer: %s'%(row.get('reason') or 'other',feedback_repair_guidance(row),str(row.get('comment') or '')[:1200],old[:5000])
                if server.mode=='mock':reviewed,review_meta=candidate,{'verdict':'mock-pass'}
                elif course_id:reviewed,review_meta=review_course_answer(server,key,q,context,lang,candidate,trigger)
                else:reviewed,review_meta=semantic_review_answer(server,key,tag,base,q,context,lang,detect_question_type(q),candidate,trigger,feedback_rewrite=True)
                cleaner=clean_course_text if course_id else clean_answer
                final=cleaner(reviewed or candidate)
                check=fast_verify_answer(q,final,lang)
                if not final or not check.get('ok'):
                    final=cleaner(candidate);check=fast_verify_answer(q,final,lang)
                if not final or not check.get('ok'):raise RuntimeError('The revised answer did not pass automatic completeness and format checks.')
                if re.sub(r'\s+','',final).casefold()==re.sub(r'\s+','',old).casefold():
                    raise RuntimeError('No verified improvement was produced; the original answer was not overwritten.')
                corrected.append(final);all_sources.extend(sources)
                summaries.append('item %s: fresh evidence answer; review %s'%(index+1,review_meta.get('verdict') or 'completed'))
        answer=corrected[0] if len(corrected)==1 else '\n\n'.join('Question %s\n%s'%(i+1,x) for i,x in enumerate(corrected))
        update_correction(feedback_id,'ready',answer=answer,sources=all_sources,summary='; '.join(summaries),model_key=key)
    except Exception as e:
        update_correction(feedback_id,'error',error=str(e),model_key=row.get('model_key') or DEFAULT_MODEL_KEY)

def _job_cleanup(server):
    now=time.time()
    with server.jobs_lock:
        stale=[jid for jid,j in server.jobs.items() if now-float(j.get('updated_at',now))>JOB_TTL_SEC]
        for jid in stale: server.jobs.pop(jid,None)

def _job_set(server,jid,**updates):
    with server.jobs_lock:
        job=server.jobs.get(jid)
        if not job or job.get('status')=='cancelled':return
        job.update(updates);job['updated_at']=time.time()

def _job_scope(server,jid):
    with server.jobs_lock:
        return (server.jobs.get(jid) or {}).get('_cancel_scope')

def _job_cancelled(server,jid):
    with server.jobs_lock:
        return (server.jobs.get(jid) or {}).get('status')=='cancelled'

def _job_cancel(server,jid):
    with server.jobs_lock:
        job=server.jobs.get(jid)
        if not job:return False
        if job.get('status') not in ('done','error','cancelled'):
            job.update({'status':'cancelled','message':'Question superseded by another model','updated_at':time.time()})
            scope=job.get('_cancel_scope')
        else:
            scope=None
    if scope is not None:scope.cancel()
    return True

def _single_local_job(server,jid,q,model_key,language_tag,profile):
    try:
        with server.local_job_semaphore, _job_scope(server,jid):
            if _job_cancelled(server,jid):return
            _job_set(server,jid,status='retrieving',progress=10,message='Retrieving textbook evidence')
            if server.mode=='mock':
                result={'answer':'[MOCK] Grounded reference answer.','answer_language':language_tag,'engine':'mock-local','model_key':model_key,'search_method':'source-balanced semantic vector RAG','sources':[],'search_ms':0,'retrieval':{},'verification':{'fast_verified':True,'semantic_review_used':False},'serving_profile':'v238-accuracy-first-robust-batch'}
                _job_set(server,jid,status='done',progress=100,message='Done',result=result);return
            correction=find_correction(q,'')
            if correction:
                result={'answer':correction['correction_answer'],'answer_language':language_tag,'engine':'auto-corrected-answer-memory','model_key':correction.get('correction_model_key') or model_key,'sources':correction.get('sources') or [],'correction_id':correction['id'],'verification':{'fast_verified':True,'feedback_correction':True},'latency_ms':0}
                _job_set(server,jid,status='done',progress=100,message='Verified correction applied',result=result);return
            t=time.time();found=SEARCH.search(q,profile=profile,diagnostic=False);search_ms=round((time.time()-t)*1000,1)
            scope=scope_decision(q,found)
            if not scope.get('in_scope'):
                result={'answer':out_of_scope_message(language_tag),'answer_language':language_tag,'engine':'scope-rejected','search_method':'source-balanced semantic vector RAG','sources':[],'search_ms':search_ms,'retrieval':found.get('retrieval',{}),'scope':scope,'cloud_started':False}
                _job_set(server,jid,status='done',progress=100,message='Out of scope',result=result);return
            if not found.get('ok'):
                result={'answer':no_evidence_message(language_tag),'answer_language':language_tag,'engine':'reference-search-no-evidence','search_method':'source-balanced semantic vector RAG','sources':[],'search_ms':search_ms,'retrieval':found.get('retrieval',{}),'reason':found.get('reason'),'scope':scope}
                _job_set(server,jid,status='done',progress=100,message='Done',result=result);return
            if _job_cancelled(server,jid):return
            _job_set(server,jid,status='answering',progress=35,message='Local model answering')
            t0=time.time();ans,verification=local_answer(server,model_key,q,found.get('context',''),language_tag,found.get('retrieval') or {})
            result={'answer':ans,'answer_language':language_tag,'engine':'local','model_key':model_key,'search_method':'source-balanced semantic vector RAG','sources':found.get('sources') or [],'search_ms':search_ms,'retrieval':found.get('retrieval') or {},'verification':verification,'local_elapsed_sec':round(time.time()-t0,2),'latency_ms':round((time.time()-t0)*1000,1),'serving_profile':'v238-accuracy-first-robust-batch'}
            _job_set(server,jid,status='done',progress=100,message='Done',result=result)
    except Exception as e:
        _job_set(server,jid,status='error',progress=100,message='Local answer failed',error=str(e))

def _course_local_job(server,jid,cid,q,model_key,language_tag):
    """Uploaded-course Q&A uses the same cancellable worker as reference Q&A."""
    try:
        with server.local_job_semaphore, _job_scope(server,jid):
            if _job_cancelled(server,jid):return
            _job_set(server,jid,status='retrieving',progress=10,message='Searching uploaded course')
            man=COURSES.manifest(cid)
            if not man:raise ValueError('Course no longer exists')
            found=COURSES.search(cid,q)
            if _job_cancelled(server,jid):return
            if not found.get('in_scope'):
                msg='This question is outside the current uploaded course scope: '+str(man.get('course_name') or 'Uploaded Course')+'.'
                if language_tag.startswith('zh'):
                    msg='此問題超出目前上傳課程「'+str(man.get('course_name') or 'Uploaded Course')+'」的教材範圍，請改問該課程內容。'
                result={'answer':msg,'answer_language':language_tag,'engine':'course-scope-rejected','course_id':cid}
            elif not found.get('context'):
                result={'answer':'教材尚未提供足以核對此題答案的內容，請查看來源頁面或改問教材明確涵蓋的內容。' if language_tag.startswith('zh') else 'The uploaded course material does not contain enough evidence to answer reliably.','answer_language':language_tag,'engine':'course-no-evidence','course_id':cid}
            else:
                _job_set(server,jid,status='answering',progress=35,message='Answering with local model')
                t=time.time()
                answer=course_local_answer(server,model_key,q,str(found.get('context') or ''),man.get('course_name'),language_tag,correction_guidance(q,cid))
                result={'answer':answer,'answer_language':language_tag,'engine':'local-course-rag',
                    'model_key':model_key,'course_id':cid,'course_name':man.get('course_name'),
                    'sources':found.get('sources',[]),'retrieval':found.get('retrieval',{}),
                    'latency_ms':round((time.time()-t)*1000,1)}
            _job_set(server,jid,status='done',progress=100,message='Done',result=result)
    except AnswerCancelled:
        _job_cancel(server,jid)
    except GroundingError:
        _job_set(server,jid,status='done',progress=100,message='Insufficient evidence',result={
            'answer':'教材尚未提供足以核對此題答案的內容，請查看來源頁面或改問教材明確涵蓋的內容。' if language_tag.startswith('zh') else 'The current course evidence does not support a verifiable answer to this question.',
            'engine':'course-no-evidence','course_id':cid})
    except Exception as e:
        _job_set(server,jid,status='error',progress=100,message='Local course answer failed',error=str(e))


def _batch_local_job(server,jid,questions,model_key,profile):
    try:
        with server.local_job_semaphore, _job_scope(server,jid):
            if _job_cancelled(server,jid):return
            if server.mode=='mock':
                answers=[{'id':i+1,'answer':'[MOCK] Batch answer %d'%(i+1),'answer_language':detect_question_language(str(q or ''),''),'engine':'mock-local','verification':{'mandatory_verification':True,'fast_verified':True,'semantic_verification_used':True,'verification_mode':'mock'}} for i,q in enumerate(questions)]
                _job_set(server,jid,status='done',progress=100,message='Done',result={'answers':answers,'engine':'mock-local','model_key':model_key,'batch_size':len(questions),'mandatory_verification':True,'profile':profile,'serving_profile':'v238-accuracy-first-robust-batch'});return
            items=[];started=time.time();total=len(questions)
            for idx,rawq in enumerate(questions,1):
                q=str(rawq or '').strip();lang=detect_question_language(q,'')
                found=SEARCH.search(q,profile=profile,diagnostic=False) if q else {'ok':False,'reason':'empty-question','context':'','sources':[],'retrieval':{}}
                scope=scope_decision(q,found)
                items.append({'id':idx,'question':q,'language_tag':lang,'ok':bool(found.get('ok')),'in_scope':bool(scope.get('in_scope')),'scope':scope,'reason':found.get('reason'),'context':found.get('context',''),'sources':found.get('sources') or [],'retrieval':found.get('retrieval') or {}})
                _job_set(server,jid,status='retrieving',progress=min(30,int(idx/max(1,total)*30)),message='Retrieving %d/%d'%(idx,total))
            active=[x for x in items if x.get('in_scope') and x.get('ok') and x.get('context')]
            if active:
                _job_set(server,jid,status='answering',progress=35,message='Local model solving and verifying %d items'%len(active))
                answers=local_batch_answer(server,model_key,active,profile=profile)
            else:answers=[]
            amap={int(x.get('id')):x for x in answers}
            # Accuracy/completeness guard: a batch model must not silently drop an item.
            # Retry ONLY missing/empty rows individually with the same production RAG context.
            # No answer key or benchmark metadata is available to this path.
            if active:
                active_by_id={int(x.get('id')):x for x in active}
                for iid,item in active_by_id.items():
                    row=amap.get(iid) or {}
                    if not str(row.get('answer','') or '').strip():
                        q=str(item.get('question','') or '');lang=item.get('language_tag') or detect_question_language(q,'')
                        try:
                            retry_answer,retry_verification=local_answer(server,model_key,q,str(item.get('context','') or ''),lang,item.get('retrieval') or {})
                            amap[iid]={'id':iid,'answer':clean_answer(retry_answer),'verification':dict(retry_verification or {},batch_missing_retry=True),'engine':'local-single-recovery'}
                        except Exception as e:
                            amap[iid]={'id':iid,'answer':'','verification':{'mandatory_verification':True,'fast_verified':False,'verification_mode':'missing-retry-failed','error':str(e)},'engine':'local-single-recovery-error'}
            merged=[]
            for item in items:
                iid=int(item['id'])
                if not item.get('in_scope'):
                    merged.append({'id':iid,'answer':out_of_scope_message(item['language_tag']),'answer_language':item['language_tag'],'engine':'scope-rejected','scope':item.get('scope') or {},'cloud_started':False,'verification':{'mandatory_verification':False,'fast_verified':True,'semantic_verification_used':False,'verification_mode':'scope-rejected'}})
                elif not item.get('ok') or not item.get('context'):
                    merged.append({'id':iid,'answer':no_evidence_message(item['language_tag']),'answer_language':item['language_tag'],'engine':'reference-search-no-evidence','scope':item.get('scope') or {},'verification':{'mandatory_verification':True,'fast_verified':True,'semantic_verification_used':False,'verification_mode':'no-evidence'}})
                else:
                    row=amap.get(iid) or {'id':iid,'answer':no_evidence_message(item['language_tag']),'verification':{'mandatory_verification':True,'fast_verified':False,'verification_mode':'missing'}}
                    row.setdefault('answer_language',item['language_tag']);row.setdefault('engine','local-microbatch');merged.append(row)
            result={'answers':merged,'engine':'local-microbatch','model_key':model_key,'batch_size':len(questions),'batch_max_items':BATCH_MAX_ITEMS,'mandatory_verification':True,'profile':profile,'batch_search_ms':round((time.time()-started)*1000,1),'serving_profile':'v238-accuracy-first-robust-batch'}
            _job_set(server,jid,status='done',progress=100,message='Done',result=result)
    except Exception as e:
        _job_set(server,jid,status='error',progress=100,message='Local batch failed',error=str(e))


class Handler(SharedAssetsMixin, SimpleHTTPRequestHandler):
    def end_headers(self): self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0'); self.send_header('Pragma','no-cache'); super().end_headers()
    def log_message(self,fmt,*args):
        if self.path.startswith('/api/'): super().log_message(fmt,*args)
    def send_json(self,status,obj):
        b=jbytes(obj); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def read_json(self):
        n=int(self.headers.get('Content-Length','0') or 0); return json.loads(self.rfile.read(n).decode('utf-8'))
    def read_multipart(self,max_bytes=320*1024*1024):
        from email.parser import BytesParser
        from email.policy import default
        n=int(self.headers.get('Content-Length','0') or 0)
        if n<=0 or n>max_bytes: raise ValueError('Invalid upload size')
        ctype=self.headers.get('Content-Type','')
        if 'multipart/form-data' not in ctype: raise ValueError('multipart/form-data required')
        raw=self.rfile.read(n)
        msg=BytesParser(policy=default).parsebytes(('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
        fields={};files=[]
        for part in msg.iter_parts():
            name=part.get_param('name',header='content-disposition') or ''
            filename=part.get_filename();data=part.get_payload(decode=True) or b''
            if filename: files.append((filename,data))
            elif name: fields[name]=data.decode(part.get_content_charset() or 'utf-8',errors='replace')
        return fields,files
    def do_GET(self):
        if self.path=='/api/reference/ready':
            self.send_json(200,{'ok':True,'content_integrity_revision':'R15','release_revision':'R16','service':'Reference'});return
        if self.path in ('/api/health','/api/reference/health'):
            self.send_json(200,{'ok':True,'service':'MC-VHTA Reference Textbook','build':BUILD,'content_integrity_revision':'R15','release_revision':'R16','mode':self.server.mode,'reference_textbook':SEARCH.status(),
                                'answer_language':'same as detected student question','v231_default_profile':V231_DEFAULT_PROFILE,'serving_profile':'v241.12.5-full-answer-voice','cloud_mode':'Confirmed BYOK key sends answers directly to the cloud; no server-stored cloud API key',
                                'scope_policy':{'locked':False,'scope':'active uploaded course materials; built-in physiology profile retained','cloud_bypass_allowed':False},
                                'multilingual_stt':{'available':stt_available(),'engine':'faster-whisper auto language detection'},
                                'models':{k:{'label':MODEL_CATALOG[k]['label'],'tag':MODEL_CATALOG[k]['tag'],'installed':MODEL_CATALOG[k]['tag'] in installed_models(model_base(k))} for k in SELECTABLE_MODEL_KEYS}}); return
        if self.path in ('/api/models','/api/reference/models'): self.send_json(200,{'default':DEFAULT_MODEL_KEY,'models':public_catalog(),'active_model_key':self.server.active_model_key}); return
        if self.path=='/api/reference/feedback-status': self.send_json(200,feedback_status()); return
        if self.path.startswith('/api/reference/feedback-correction-status'):
            from urllib.parse import urlparse,parse_qs
            fid=str((parse_qs(urlparse(self.path).query).get('id') or [''])[0]);row=feedback_record(fid)
            if not row:self.send_json(404,{'ok':False,'error':'feedback not found'});return
            self.send_json(200,{'ok':True,'id':fid,'saved':True,'feedback_intent':row.get('feedback_intent'),
                                'feedback_focus':json.loads(row.get('feedback_focus') or '[]'),
                                'correction_status':row.get('correction_status'),
                                'correction_summary':row.get('correction_summary') or '',
                                'correction_answer':(row.get('correction_answer') or '')[:20000] if row.get('correction_status')=='ready' else '',
                                'correction_error':row.get('correction_error') or '',
                                'email_status':row.get('email_status'),
                                'email_recipient':feedback_status().get('email_recipient_hint'),
                                'corrected_at':row.get('corrected_at')});return
        if self.path=='/api/reference/site-metrics':
            try:self.send_json(200,metrics_snapshot())
            except Exception as e:self.send_json(503,{'ok':False,'error':'site metrics unavailable','detail':str(e)})
            return
        if self.path.split('?')[0] in ('/api/reference/course-upload-diagnostics','/api/reference/course-admin-uploads','/api/reference/course-upload-source'):
            from urllib.parse import urlparse,parse_qs
            parsed=urlparse(self.path);query=parse_qs(parsed.query)
            try:
                if parsed.path.endswith('course-admin-uploads'):
                    self.send_json(200,{'ok':True,'uploads':COURSES.admin_uploads()});return
                sid=str((query.get('stage_id') or [''])[0]);report=COURSES.upload_diagnostics(sid)
                if parsed.path.endswith('course-upload-diagnostics'):
                    self.send_json(200,{'ok':True,**report});return
                filename=str((query.get('file') or [''])[0])
                allowed={row['file_name'] for row in report['received_files']}
                if filename not in allowed:raise ValueError('Original upload not found')
                path=COURSES._stage_dir(sid)/'source'/filename
                if path.is_symlink() or not path.is_file():raise ValueError('Original upload not available')
                self.send_response(200);self.send_header('Content-Type','application/pdf');self.send_header('Content-Length',str(path.stat().st_size));self.send_header('Content-Disposition','attachment; filename="'+path.name+'"');self.send_header('Cache-Control','no-store');self.end_headers()
                with path.open('rb') as file:
                    while True:
                        block=file.read(1024*1024)
                        if not block:break
                        self.wfile.write(block)
            except (ValueError,OSError) as error:self.send_json(404,{'ok':False,'error':str(error)})
            return
        if self.path=='/api/reference/course-list':
            self.send_json(200,{'ok':True,'courses':COURSES.course_summaries()});return
        if self.path.startswith('/api/reference/course-quiz-stats?'):
            from urllib.parse import urlparse,parse_qs
            cid=str((parse_qs(urlparse(self.path).query).get('course_id') or [''])[0])
            try:self.send_json(200,COURSES.quiz_statistics(cid))
            except ValueError as e:self.send_json(404,{'ok':False,'error':str(e)})
            return
        if self.path.startswith('/api/reference/course-builds'):
            self.send_json(200,{'ok':True,'builds':COURSES.active_builds()});return
        if self.path.startswith('/api/reference/course-upload-status'):
            from urllib.parse import urlparse,parse_qs
            qs=parse_qs(urlparse(self.path).query)
            try:self.send_json(200,{'ok':True,**COURSES.upload_status((qs.get('stage_id') or [''])[0],(qs.get('upload_id') or [''])[0],background=True)})
            except Exception as e:self.send_json(400,{'ok':False,'error':str(e)})
            return
        if self.path.startswith('/api/reference/course-stage-status'):
            from urllib.parse import urlparse,parse_qs
            qs=parse_qs(urlparse(self.path).query);sid=str((qs.get('stage_id') or [''])[0]);sd=COURSES._stage_dir(sid)
            try:
                stage=json.loads((sd/'stage.json').read_text(encoding='utf-8')) if (sd/'stage.json').exists() else None
                job=json.loads((sd/'build_job.json').read_text(encoding='utf-8')) if (sd/'build_job.json').exists() else None
                self.send_json(200,{'ok':True,'inspection':stage,'job':COURSES.job_status(job['id']) if job else None})
            except Exception as e:self.send_json(400,{'ok':False,'error':str(e)})
            return
        if self.path.startswith('/api/reference/course-status'):
            from urllib.parse import urlparse,parse_qs
            qs=parse_qs(urlparse(self.path).query);cid=str((qs.get('course_id') or qs.get('id') or [''])[0])
            man=COURSES.manifest(cid)
            if not man:self.send_json(404,{'error':'course not found'});return
            COURSES.viewed(cid)
            self.send_json(200,{'ok':True,'manifest':man,'activity':COURSES.activity(cid)});return
        if self.path.startswith('/api/reference/course-job-status'):
            from urllib.parse import urlparse,parse_qs
            qs=parse_qs(urlparse(self.path).query);jid=str((qs.get('id') or [''])[0]);job=COURSES.job_status(jid)
            if not job:self.send_json(404,{'error':'course build job not found'});return
            self.send_json(200,{k:v for k,v in job.items() if k not in ('created_at','updated_at','_cancel_scope')});return
        if self.path.startswith('/api/reference/job-status'):
            from urllib.parse import urlparse,parse_qs
            _job_cleanup(self.server);qs=parse_qs(urlparse(self.path).query);jid=str((qs.get('id') or [''])[0])
            with self.server.jobs_lock: job=dict(self.server.jobs.get(jid) or {})
            if not job:self.send_json(404,{'error':'job not found'});return
            # Internal fields (leading underscore, e.g. the cancellation handle) are not JSON data.
            self.send_json(200,{k:v for k,v in job.items() if k not in ('created_at','updated_at') and not k.startswith('_')});return
        if self.path=='/': self.path='/index.html?v=207vectorrag'
        return super().do_GET()
    def do_POST(self):
        if self.path=='/api/reference/course-upload-chunk':
            try:
                fields,files=self.read_multipart(max_bytes=17*1024*1024)
                if len(files)!=1:raise ValueError('Exactly one PDF chunk is required.')
                result=COURSES.upload_chunk(fields.get('stage_id',''),fields.get('upload_id',''),fields.get('file_name',''),fields.get('total_size','0'),fields.get('offset','0'),files[0][1],fields.get('course_name',''),background=True)
                self.send_json(200,{'ok':True,**result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'PDF chunk upload failed','detail':str(e)})
            return
        if self.path=='/api/reference/course-inspect':
            try:
                fields,files=self.read_multipart();result=COURSES.inspect(files,fields.get('course_name',''),fields.get('visual_model','none'),fields.get('stage_id',''));self.send_json(200,{'ok':True,'inspection':result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Course material inspection failed','detail':str(e)})
            return
        if self.path=='/api/reference/course-build':
            try:
                req=self.read_json();stage_id=str(req.get('stage_id') or '');course_name=str(req.get('course_name') or '');visual_model=str(req.get('visual_model') or 'none');units=req.get('units') or [];model_key=key_of(req.get('model_key'))
                job=COURSES.start_build(stage_id,course_name,visual_model,units,model_key,lambda mk,sys,user,max_tokens:course_model_call(self.server,mk,sys,user,max_tokens),reading=req.get('reading'));self.send_json(202,{'ok':True,'job':job})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Course build could not start','detail':str(e)})
            return
        if self.path=='/api/reference/course-retry-preparation':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');model_key=key_of(req.get('model_key'))
                result=COURSES.retry_preparation(cid,model_key,lambda mk,sys,user,max_tokens:course_model_call(self.server,mk,sys,user,max_tokens))
                self.send_json(202,{'ok':True,'manifest':result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Preparation could not restart','detail':str(e)})
            return
        if self.path=='/api/reference/course-prioritize-unit':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');unit_id=str(req.get('unit_id') or '')
                kind=str(req.get('kind') or '');model_key=key_of(req.get('model_key'))
                result=COURSES.prioritize_unit(cid,unit_id,kind,model_key,
                    lambda mk,sys,user,max_tokens:course_model_call(self.server,mk,sys,user,max_tokens))
                self.send_json(202,{'ok':True,'manifest':result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Could not prioritize unit','detail':str(e)})
            return
        if self.path=='/api/reference/course-expand-questions':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');model_key=key_of(req.get('model_key'))
                result=COURSES.expand_questions(cid,req.get('unit_ids'),model_key,
                    lambda mk,sys,user,max_tokens:course_model_call(self.server,mk,sys,user,max_tokens))
                self.send_json(202,{'ok':True,'manifest':result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Could not expand questions','detail':str(e)})
            return
        if self.path=='/api/reference/course-context':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');q=str(req.get('question') or '').strip()
                if not cid or not q:self.send_json(400,{'error':'course_id and question are required'});return
                found=COURSES.search(cid,q);lang=detect_question_language(q,req.get('language_tag'));man=found.get('course') or {}
                self.send_json(200,{'ok':found.get('ok',False),'in_scope':found.get('in_scope',False),'scope':{'in_scope':found.get('in_scope',False),'course_id':cid,'course_name':man.get('course_name'),'reason':found.get('reason'),'top_similarity':found.get('top_similarity')},'reason':found.get('reason'),'question':q,'context':found.get('context',''),'sources':found.get('sources',[]),'retrieval':found.get('retrieval',{}),'language_tag':lang,'search_method':'course-adaptive semantic vector RAG','course':man,'verified_correction':None})
            except Exception as e:self.send_json(500,{'error':'Course retrieval failed','detail':str(e)})
            return
        if self.path=='/api/reference/course-answer':
            try:
                req=self.read_json();provider=str(req.get('cloud_provider') or 'off').lower().strip()
                if provider not in ('','off','local'):
                    self.send_json(400,{'error':'Cloud course answers require the Cloudflare Worker BYOK gateway','code':'cloud_gateway_required'});return
                cid=str(req.get('course_id') or '');q=str(req.get('question') or '').strip();model_key=key_of(req.get('model_key'));lang=detect_question_language(q,req.get('language_tag'));man=COURSES.manifest(cid)
                if not man or not q:self.send_json(400,{'error':'valid course_id and question are required'});return
                found=COURSES.search(cid,q)
                if not found.get('in_scope'):
                    msg='This question is outside the current uploaded course scope: '+str(man.get('course_name') or 'Uploaded Course')+'.'
                    if str(lang).startswith('zh'):msg='此問題超出目前上傳課程「'+str(man.get('course_name') or 'Uploaded Course')+'」的教材範圍，請改問該課程內容。'
                    self.send_json(200,{'answer':msg,'answer_language':lang,'engine':'course-scope-rejected','course_id':cid});return
                ans=course_local_answer(self.server,model_key,q,found.get('context',''),man.get('course_name'),lang,correction_guidance(q,cid))
                self.send_json(200,{'answer':ans,'answer_language':lang,'engine':'local-course-rag','model_key':model_key,'course_id':cid,'course_name':man.get('course_name'),'sources':found.get('sources',[]),'retrieval':found.get('retrieval',{})});return
            except GroundingError:
                self.send_json(200,{'answer':'教材尚未提供足以核對此題答案的內容，請查看來源頁面或改問教材明確涵蓋的內容。' if lang.startswith('zh') else 'The current course evidence does not support a verifiable answer to this question.','engine':'course-no-evidence','course_id':cid})
            except Exception as e:self.send_json(503,{'error':'Course answer unavailable','detail':str(e)})
            return
        if self.path=='/api/reference/course-answer-local':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');q=str(req.get('question') or '').strip();model_key=key_of(req.get('model_key'));lang=detect_question_language(q,req.get('language_tag'));context=str(req.get('context') or '')
                man=COURSES.manifest(cid)
                if not man or not q:self.send_json(400,{'error':'valid course_id and question are required'});return
                # Never treat browser-supplied context as course authority.
                found=COURSES.search(cid,q);context=str(found.get('context') or '')
                if not found.get('in_scope'):
                    msg='This question is outside the current uploaded course scope: '+str(man.get('course_name') or 'Uploaded Course')+'.'
                    if str(lang).startswith('zh'):msg='此問題超出目前上傳課程「'+str(man.get('course_name') or 'Uploaded Course')+'」的教材範圍，請改問該課程內容。'
                    self.send_json(200,{'answer':msg,'answer_language':lang,'engine':'course-scope-rejected','course_id':cid,'scope':{'in_scope':False}});return
                if not context:
                    self.send_json(200,{'answer':'The uploaded course material did not provide enough evidence to answer this question reliably.','answer_language':lang,'engine':'course-no-evidence','course_id':cid});return
                t=time.time();ans=course_local_answer(self.server,model_key,q,context,man.get('course_name'),lang,correction_guidance(q,cid))
                self.send_json(200,{'answer':ans,'answer_language':lang,'engine':'local-course-rag','model_key':model_key,'course_id':cid,'course_name':man.get('course_name'),'sources':(found or {}).get('sources',[]),'retrieval':(found or {}).get('retrieval',{}),'latency_ms':round((time.time()-t)*1000,1)})
            except GroundingError:
                self.send_json(200,{'answer':'教材尚未提供足以核對此題答案的內容，請查看來源頁面或改問教材明確涵蓋的內容。' if lang.startswith('zh') else 'The current course evidence does not support a verifiable answer to this question.','engine':'course-no-evidence','course_id':cid})
            except Exception as e:self.send_json(503,{'error':'Local course answer unavailable','detail':str(e),'engine':'local-course-error'})
            return
        if self.path=='/api/reference/course-quiz':
            try:
                req=self.read_json();cid=str(req.get('course_id') or '');per=int(req.get('per_unit') or 2);key=key_of(req.get('model_key'))
                if not COURSES.manifest(cid):raise ValueError('course not found')
                result=COURSES.quiz(cid,per,key,unit_ids=req.get('unit_ids'))
                self.send_json(200,{'ok':True,**result})
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Course quiz unavailable','detail':str(e)})
            return
        if self.path=='/api/reference/course-quiz-result':
            try:
                req=self.read_json();self.send_json(200,COURSES.record_quiz(str(req.get('course_id') or ''),str(req.get('quiz_id') or ''),req.get('answers')))
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(500,{'ok':False,'error':'Quiz result could not be saved','detail':str(e)})
            return
        if self.path=='/api/reference/course-delete':
            try:
                req=self.read_json()
                self.send_json(200,COURSES.delete_course(str(req.get('course_id') or '')))
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except RuntimeError as e:self.send_json(409,{'ok':False,'error':str(e)})
            return
        if self.path=='/api/reference/course-build-cancel':
            try:
                req=self.read_json()
                self.send_json(200,COURSES.cancel_build(str(req.get('id') or req.get('job_id') or '')))
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            return
        if self.path=='/api/reference/site-metrics':
            try:
                req=self.read_json(); self.send_json(200,metrics_heartbeat(req))
            except ValueError as e:self.send_json(400,{'ok':False,'error':str(e)})
            except Exception as e:self.send_json(503,{'ok':False,'error':'site metrics unavailable','detail':str(e)})
            return
        if self.path=='/api/reference/feedback':
            try:
                req=self.read_json(); result=save_feedback(req)
                if result['auto_correction_queued']:
                    threading.Thread(target=_auto_correct_feedback,args=(self.server,result['id']),daemon=True).start()
                self.send_json(202,result)
            except ValueError as e:self.send_json(400,{'error':str(e),'code':'invalid_feedback'})
            except Exception as e:self.send_json(500,{'error':'Feedback could not be saved','detail':str(e),'code':'feedback_save_failed'})
            return
        if self.path=='/api/stt':
            try:
                n=int(self.headers.get('Content-Length','0') or 0)
                if n<=0 or n>25*1024*1024:self.send_json(400,{'error':'Invalid audio size'});return
                self.send_json(200,stt(self.rfile.read(n),self.headers.get('Content-Type','audio/webm'),self.headers.get('X-VQ-Language-Hint','')))
            except Exception as e:self.send_json(503,{'error':str(e),'stt_available':stt_available()})
            return
        if self.path in ('/api/warm-model','/api/reference/warm-model'):
            try:req=self.read_json(); key=key_of(req.get('model_key')); tag=MODEL_CATALOG[key]['tag']; base=model_base(key)
            except Exception:self.send_json(400,{'error':'Invalid JSON'});return
            try:
                p={'model':tag,'messages':[{'role':'user','content':'Reply OK only.'}],'think':False,'stream':False,'keep_alive':MODEL_KEEP_ALIVE,'options':{'num_ctx':MODEL_NUM_CTX,'num_predict':1,'temperature':0}}
                with self.server.model_lock.using(tag):
                    ensure_single_active_model(self.server,key,tag,base)
                    url_json(base+'/api/chat',payload=p,timeout=180)
                    self.server.active_model_key=key;self.server.active_model_tag=tag;self.server.active_model_base=base
                self.send_json(200,{'ok':True,'model_key':key,'model':tag})
            except Exception as e:self.send_json(503,{'ok':False,'error':str(e),'model_key':key,'model':tag})
            return
        if self.path=='/api/reference/job-cancel':
            try:
                req=self.read_json();jid=str(req.get('job_id') or '')
                if not re.fullmatch(r'[0-9a-f]{32}',jid):
                    self.send_json(400,{'ok':False,'error':'Invalid job ID'});return
                ok=_job_cancel(self.server,jid)
                self.send_json(200 if ok else 404,{'ok':ok,'job_id':jid,'status':'cancelled' if ok else 'not_found'})
            except Exception as e:self.send_json(400,{'ok':False,'error':str(e)[:120]})
            return
        if self.path in ('/api/reference/answer-job-start','/api/reference/batch-job-start','/api/reference/course-answer-job-start'):
            try:req=self.read_json()
            except Exception:self.send_json(400,{'error':'Invalid JSON'});return
            _job_cleanup(self.server);jid=uuid.uuid4().hex
            model_key=key_of(req.get('model_key'));profile=str(req.get('profile') or V231_DEFAULT_PROFILE).strip().lower()
            if self.path=='/api/reference/course-answer-job-start':
                cid=str(req.get('course_id') or '');q=str(req.get('question') or '').strip()
                man=COURSES.manifest(cid)
                if not man or not q:self.send_json(400,{'error':'Valid course_id and question are required'});return
                lang=detect_question_language(q,req.get('language_tag'))
                with self.server.jobs_lock:
                    self.server.jobs[jid]={'id':jid,'status':'queued','progress':0,'message':'Queued',
                        'created_at':time.time(),'updated_at':time.time(),'_cancel_scope':CancellationScope()}
                threading.Thread(target=_course_local_job,args=(self.server,jid,cid,q,model_key,lang),daemon=True).start()
            elif self.path=='/api/reference/answer-job-start':
                q=str(req.get('question','') or '').strip();lang=detect_question_language(q,req.get('language_tag'))
                if not q:self.send_json(400,{'error':'question is required'});return
                with self.server.jobs_lock:self.server.jobs[jid]={'id':jid,'status':'queued','progress':0,'message':'Queued','created_at':time.time(),'updated_at':time.time(),'_cancel_scope':CancellationScope()}
                threading.Thread(target=_single_local_job,args=(self.server,jid,q,model_key,lang,profile),daemon=True).start()
            else:
                questions=req.get('questions') or []
                if not isinstance(questions,list) or len(questions)<2 or len(questions)>40:self.send_json(400,{'error':'questions must contain 2-40 items'});return
                with self.server.jobs_lock:self.server.jobs[jid]={'id':jid,'status':'queued','progress':0,'message':'Queued','created_at':time.time(),'updated_at':time.time(),'_cancel_scope':CancellationScope()}
                threading.Thread(target=_batch_local_job,args=(self.server,jid,questions,model_key,profile),daemon=True).start()
            self.send_json(202,{'ok':True,'job_id':jid,'status':'queued','serving_profile':'v238-accuracy-first-robust-batch'});return
        if self.path not in ('/api/reference/context','/api/reference/answer-local','/api/reference/answer','/api/reference/context-batch','/api/reference/answer-batch-local'):
            self.send_json(404,{'error':'Not found'});return
        try:req=self.read_json()
        except Exception:self.send_json(400,{'error':'Invalid JSON'});return
        if self.path=='/api/reference/context-batch':
            questions=req.get('questions') or []
            profile=str(req.get('profile') or V231_DEFAULT_PROFILE).strip().lower(); diagnostic=bool(req.get('diagnostic',False))
            if not isinstance(questions,list) or len(questions)<2 or len(questions)>40:
                self.send_json(400,{'error':'questions must contain 2-40 items'});return
            out=[];started=time.time()
            for idx,rawq in enumerate(questions,1):
                q=str(rawq or '').strip();lang=detect_question_language(q,'')
                found=SEARCH.search(q,profile=profile,diagnostic=diagnostic) if q else {'ok':False,'reason':'empty-question','context':'','sources':[],'evidence_count':0,'retrieval':{}}
                scope=scope_decision(q,found)
                correction=find_correction(q,'') if scope.get('in_scope') and found.get('context') else None
                out.append({'id':idx,'question':q,'language_tag':lang,'ok':bool(found.get('ok')),'in_scope':bool(scope.get('in_scope')),'scope':scope,'reason':found.get('reason'),'context':found.get('context','') if scope.get('in_scope') else '','sources':(found.get('sources') or []) if scope.get('in_scope') else [],'retrieval':found.get('retrieval') or {},
                            'verified_correction':{'id':correction['id'],'answer':correction['correction_answer']} if correction else None})
            self.send_json(200,{'ok':True,'items':out,'batch_search_ms':round((time.time()-started)*1000,1),'search_method':'source-balanced semantic vector RAG','profile':profile});return
        if self.path=='/api/reference/answer-batch-local':
            items=req.get('items') or [];model_key=key_of(req.get('model_key'));profile=str(req.get('profile') or V231_DEFAULT_PROFILE).strip().lower()
            if not isinstance(items,list) or len(items)<2 or len(items)>40:
                self.send_json(400,{'error':'items must contain 2-40 items'});return
            normalized=[]
            for idx,item in enumerate(items,1):
                if not isinstance(item,dict):continue
                q=str(item.get('question','') or '').strip()
                if not q:continue
                lang=detect_question_language(q,item.get('language_tag'))
                supplied={'ok':bool(item.get('context')),'context':str(item.get('context','') or ''),'sources':item.get('sources') or [],'retrieval':item.get('retrieval') or {}}
                scope=item.get('scope') if isinstance(item.get('scope'),dict) else scope_decision(q,supplied)
                if not scope.get('in_scope'): continue
                normalized.append({'id':int(item.get('id') or idx),'question':q,'context':str(item.get('context','') or ''),'sources':item.get('sources') or [],'retrieval':item.get('retrieval') or {},'language_tag':lang,'scope':scope})
            if len(normalized)<2:self.send_json(400,{'error':'at least two valid items are required'});return
            t0=time.time()
            if self.server.mode=='mock':
                answers=[{'id':x['id'],'answer':'[MOCK] Batch answer %s'%x['id'],'verification':{'mandatory_verification':True,'fast_verified':True,'semantic_verification_used':True,'semantic_verification_verdict':'pass','verification_mode':'mock'}} for x in normalized]
            else:
                try:answers=local_batch_answer(self.server,model_key,normalized,profile=profile)
                except Exception as e:self.send_json(503,{'error':'Local batch LLM unavailable','detail':str(e),'engine':'local-batch-error'});return
            self.send_json(200,{'answers':answers,'engine':'local-microbatch','model_key':model_key,'batch_size':len(normalized),'batch_max_items':BATCH_MAX_ITEMS,'mandatory_verification':True,'profile':profile,'latency_ms':round((time.time()-t0)*1000,1)});return
        q=str(req.get('question','') or '').strip(); model_key=key_of(req.get('model_key')); language_tag=detect_question_language(q,req.get('language_tag')); profile=str(req.get('profile') or V231_DEFAULT_PROFILE).strip().lower(); diagnostic=bool(req.get('diagnostic',False))
        if not q:self.send_json(400,{'error':'question is required'});return
        if self.path=='/api/reference/context':
            t=time.time(); found=SEARCH.search(q,profile=profile,diagnostic=diagnostic); scope=scope_decision(q,found)
            correction=find_correction(q,'') if scope.get('in_scope') and found.get('context') else None
            self.send_json(200,{'ok':found['ok'],'in_scope':bool(scope.get('in_scope')),'scope':scope,'reason':found['reason'],'question':q,'context':found['context'] if scope.get('in_scope') else '','sources':found['sources'] if scope.get('in_scope') else [],
                                'evidence_count':found['evidence_count'] if scope.get('in_scope') else 0,'language_tag':language_tag,'search_ms':round((time.time()-t)*1000,1),'search_method':'source-balanced semantic vector RAG','retrieval':found.get('retrieval',{}),
                                'verified_correction':{'id':correction['id'],'answer':correction['correction_answer']} if correction else None});return
        context=str(req.get('context','') or ''); sources=req.get('sources') or []; search_ms=None; retrieval=req.get('retrieval') or {}; scope=req.get('scope') if isinstance(req.get('scope'),dict) else None
        if not context:
            t=time.time(); found=SEARCH.search(q,profile=profile,diagnostic=diagnostic); search_ms=round((time.time()-t)*1000,1); context=found['context']; sources=found['sources']; retrieval=found.get('retrieval',{}); scope=scope_decision(q,found)
            if not scope.get('in_scope'):
                self.send_json(200,{'answer':out_of_scope_message(language_tag),'answer_language':language_tag,'engine':'scope-rejected','search_method':'source-balanced semantic vector RAG','sources':[],'search_ms':search_ms,'retrieval':retrieval,'scope':scope,'cloud_started':False});return
            if not found['ok']:
                self.send_json(200,{'answer':no_evidence_message(language_tag),'answer_language':language_tag,'engine':'reference-search-no-evidence','search_method':'source-balanced semantic vector RAG','sources':[],
                                    'search_ms':search_ms,'retrieval':retrieval,'reason':found.get('reason'),'scope':scope});return
        if scope is None:
            scope=scope_decision(q,{'ok':bool(context),'context':context,'sources':sources,'retrieval':retrieval})
        if not scope.get('in_scope'):
            self.send_json(200,{'answer':out_of_scope_message(language_tag),'answer_language':language_tag,'engine':'scope-rejected','search_method':'source-balanced semantic vector RAG','sources':[],'search_ms':search_ms,'retrieval':retrieval,'scope':scope,'cloud_started':False});return
        correction=find_correction(q,'')
        if correction:
            self.send_json(200,{'answer':correction['correction_answer'],'answer_language':language_tag,'engine':'auto-corrected-answer-memory','model_key':correction.get('correction_model_key') or model_key,'sources':correction.get('sources') or [],'correction_id':correction['id'],'verification':{'fast_verified':True,'feedback_correction':True},'latency_ms':0});return
        t0=time.time()
        if self.server.mode=='mock': ans='[MOCK] Grounded reference answer.'; engine='mock-local'; verification={'fast_verified':True,'semantic_review_used':False,'review_reason':[]}
        else:
            try: ans,verification=local_answer(self.server,model_key,q,context,language_tag,retrieval); engine='local'
            except Exception as e:
                self.send_json(503,{'error':'Local LLM unavailable','detail':str(e),'engine':'local-error','sources':sources});return
        self.send_json(200,{'answer':ans,'answer_language':language_tag,'engine':engine,'model_key':model_key,'search_method':'source-balanced semantic vector RAG','sources':sources,'search_ms':search_ms,
                            'retrieval':retrieval,'scope':scope,'verification':verification,'local_elapsed_sec':round(time.time()-t0,2),'latency_ms':round((time.time()-t0)*1000,1)})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--host',default=DEFAULT_HOST);ap.add_argument('--port',type=int,default=DEFAULT_PORT);ap.add_argument('--mode',choices=['ollama','mock'],default='ollama');ap.add_argument('--open',choices=['auto','yes','none'],default='none');args=ap.parse_args()
    os.chdir(str(WEB_DIR));srv=ThreadingHTTPServer((args.host,args.port),Handler);srv.mode=args.mode;srv.model_lock=SharedModelLock();srv.local_job_semaphore=threading.Semaphore(1);srv.jobs={};srv.jobs_lock=threading.Lock();srv.active_model_key=None;srv.active_model_tag=None;srv.active_model_base=None;srv.installed_models=installed_models()
    threading.Thread(target=lambda:COURSES.resume_pending(lambda mk,sys,user,max_tokens:course_model_call(srv,mk,sys,user,max_tokens)),daemon=True).start()
    for feedback_id in pending_corrections():
        threading.Thread(target=_auto_correct_feedback,args=(srv,feedback_id),daemon=True).start()
    resume_pending_emails()
    print('[OK] '+BUILD+' Reference Textbook backend http://%s:%s'%(args.host,args.port));print('[REFERENCE]',SEARCH.status());srv.serve_forever()
if __name__=='__main__':main()
