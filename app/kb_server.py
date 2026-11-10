#!/usr/bin/env python3
from __future__ import print_function
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
try:
    import fcntl
except ImportError:
    fcntl=None
import time
import tempfile
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from static_assets import SharedAssetsMixin
from model_lock import SharedModelLock
import gpu_policy
from feedback.feedback_service import find_correction
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python 3.6 compatibility
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web" / "kb"
BUILD = "v227-kb-adaptive-verification-speed-diversity"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8181
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "response_audit.csv"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11435").strip()
if OLLAMA_HOST.startswith("http://") or OLLAMA_HOST.startswith("https://"):
    OLLAMA_BASE = OLLAMA_HOST.rstrip("/")
else:
    OLLAMA_BASE = ("http://" + OLLAMA_HOST).rstrip("/")
from ollama_cancel import OLLAMA_NUM_CTX
MODEL_NUM_CTX = OLLAMA_NUM_CTX  # shared with Reference/course calls: no runner reload
# v204.2: restore the v199 response-generation profile. Language routing is
# handled without a separate LLM call: Whisper metadata for voice, a lightweight
# natural-language router for typed text, and the semantic-recognition LLM's
# language_tag only as an ambiguity fallback in the SAME semantic inference.
SEMANTIC_TIMEOUT_SEC = float(os.environ.get("VQ_SEMANTIC_TIMEOUT_SEC", "180"))
FINAL_TIMEOUT_SEC = float(os.environ.get("VQ_FINAL_TIMEOUT_SEC", "240"))
MODEL_KEEP_ALIVE = os.environ.get("VQ_MODEL_KEEP_ALIVE", "2h")

# v185 server-side Taiwan TTS. The main app remains Python 3.6 compatible;
# a separate newer Python environment provides the edge-tts executable.
TTS_EDGE_BIN = os.environ.get(
    "VQ_EDGE_TTS_BIN",
    "/path/to/workdir/miniforge-tts/bin/edge-tts"
).strip()
TTS_VOICES = {
    "yating": {"label": "Yating / female", "zh": "zh-TW-HsiaoChenNeural", "en": "en-US-JennyNeural", "gender": "female"},
    "zhiwei": {"label": "Zhiwei / authorized teacher reference voice", "zh": "", "en": "", "gender": "male"},
}
TEACHER_TTS_URL = os.environ.get("VQ_TEACHER_TTS_URL", "http://127.0.0.1:11436/tts").strip()
TEACHER_TTS_HEALTH = os.environ.get("VQ_TEACHER_TTS_HEALTH", "http://127.0.0.1:11436/health").strip()
STT_URL = os.environ.get("VQ_STT_URL", "http://127.0.0.1:11437/stt").strip()
STT_HEALTH = os.environ.get("VQ_STT_HEALTH", "http://127.0.0.1:11437/health").strip()
TTS_MAX_CHARS = int(os.environ.get("VQ_TTS_MAX_CHARS", "8000"))
TTS_TIMEOUT = int(os.environ.get("VQ_TTS_TIMEOUT", "120"))

def tts_available():
    return bool(TTS_EDGE_BIN and os.path.isfile(TTS_EDGE_BIN) and os.access(TTS_EDGE_BIN, os.X_OK))

def _tts_rate_arg(rate):
    try:
        rate = float(rate)
    except Exception:
        rate = 1.0
    rate = max(0.8, min(1.2, rate))
    pct = int(round((rate - 1.0) * 100.0))
    return "%+d%%" % pct

def _tts_pitch_arg(pitch):
    try:
        pitch = float(pitch)
    except Exception:
        pitch = 1.0
    pitch = max(0.8, min(1.2, pitch))
    hz = int(round((pitch - 1.0) * 50.0))
    return "%+dHz" % hz

def _language_key(language, text=""):
    language = str(language or "").lower().replace("_", "-")
    if language.startswith("zh"):
        return "zh"
    if language.startswith("en"):
        return "en"
    sample = str(text or "")
    return "zh" if re.search(r"[\u3400-\u9fff]", sample) else "en"

def teacher_tts_available():
    if not TEACHER_TTS_HEALTH:
        return False
    try:
        with urllib.request.urlopen(TEACHER_TTS_HEALTH, timeout=0.8) as resp:
            return int(getattr(resp, "status", 200)) == 200
    except Exception:
        return False

def stt_available():
    if not STT_HEALTH:
        return False
    try:
        with urllib.request.urlopen(STT_HEALTH, timeout=0.8) as resp:
            return int(getattr(resp, "status", 200)) == 200
    except Exception:
        return False

def _stt_transcribe(raw_audio, content_type, language_hint=""):
    headers={"Content-Type": content_type or "audio/webm"}
    if language_hint: headers["X-VQ-Language-Hint"] = str(language_hint)
    req = urllib.request.Request(STT_URL, data=raw_audio, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_decimal_speech(text, language):
    """Make decimal points audible without changing displayed text."""
    raw=str(text or "")
    lang=str(language or "").lower()
    is_zh=lang.startswith(("zh","cmn")) or bool(re.search(r"[\u3400-\u9fff]", raw))
    word=" 點 " if is_zh else " point "
    def repl(m):
        return m.group(1)+word+" ".join(m.group(2))
    return re.sub(r"(?<![\w.])(\d+)\.(\d+)(?![\w.])", repl, raw)

def _teacher_tts_audio(text, language, rate):
    payload = json.dumps({"text": text, "language": language, "rate": rate}).encode("utf-8")
    req = urllib.request.Request(TEACHER_TTS_URL, data=payload, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TTS_TIMEOUT) as resp:
        return resp.read()

def generate_tts_audio(text, role, rate=1.0, pitch=1.0, language="auto"):
    text = str(text or "").strip()
    role = str(role or "zhiwei").strip().lower()
    if role not in TTS_VOICES:
        role = "zhiwei"
    if not text:
        raise ValueError("TTS text is empty")
    if len(text) > TTS_MAX_CHARS:
        raise ValueError("TTS text exceeds %s characters" % TTS_MAX_CHARS)
    lang_key = _language_key(language, text)
    spoken_text = _normalize_decimal_speech(text, language)

    # v201: Zhiwei is NEVER substituted with a generic male voice.
    # If the authorized teacher-reference XTTS service is not available, fail visibly
    # instead of silently playing a different person's voice.
    if role == "zhiwei":
        if not teacher_tts_available():
            raise RuntimeError("Teacher reference voice service is unavailable; no substitute male voice will be used.")
        try:
            audio = _teacher_tts_audio(spoken_text, language, rate)
            if audio:
                return audio, {"voice":"TeacherReference-XTTSv2", "gender":"male", "content_type":"audio/wav", "cloned":True}
            raise RuntimeError("Teacher reference voice returned empty audio.")
        except Exception as exc:
            raise RuntimeError("Teacher reference voice synthesis failed: %s" % exc)

    if not tts_available():
        raise RuntimeError("Server TTS is not installed. Run bash scripts/setup/prepare_tts.sh from the repository root first.")
    fd, out_path = tempfile.mkstemp(prefix="vq_tts_", suffix=".mp3")
    os.close(fd)
    try:
        voice_name = TTS_VOICES[role][lang_key]
        cmd = [
            TTS_EDGE_BIN,
            "--voice", voice_name,
            "--rate=" + _tts_rate_arg(rate),
            "--pitch=" + _tts_pitch_arg(pitch),
            "--text", spoken_text,
            "--write-media", out_path,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=TTS_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill(); stdout, stderr = proc.communicate(); raise RuntimeError("Server TTS timed out")
        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(err or "edge-tts failed")
        with open(out_path, "rb") as fh:
            audio = fh.read()
        if not audio:
            raise RuntimeError("Server TTS returned empty audio")
        return audio, {"voice":voice_name,"gender":TTS_VOICES[role]["gender"],"content_type":"audio/mpeg","cloned":False}
    finally:
        try: os.remove(out_path)
        except Exception: pass


# Shared RTX A5000 profile: one selected answer model is active at a time.
# These 6.0-7.6 GB Q4-class models are intentionally larger than the edge 3-4B set,
# but still small enough for partial GPU placement + CPU/RAM offload when the shared 24 GB GPU is busy.
GRANITE_API_BASE = os.environ.get("VQ_GRANITE_API_BASE", OLLAMA_BASE).rstrip("/")
GRANITE_MODEL_TAG = os.environ.get("VQ_GRANITE_MODEL_TAG", "granite4.2:8b-q5_K_M").strip()
GRANITE_MODEL_LABEL = os.environ.get("VQ_GRANITE_MODEL_LABEL", "Granite 4.2 8B Q5_K_M · IBM").strip()
GEMMA_API_BASE = os.environ.get("VQ_GEMMA_API_BASE", OLLAMA_BASE).rstrip("/")
GEMMA_MODEL_TAG = os.environ.get("VQ_GEMMA_MODEL_TAG", "gemma4:12b").strip()
GEMMA_MODEL_LABEL = os.environ.get("VQ_GEMMA_MODEL_LABEL", "Gemma 4 12B · Google").strip()
MISTRAL_API_BASE = os.environ.get("VQ_MISTRAL_API_BASE", OLLAMA_BASE).rstrip("/")
MISTRAL_MODEL_TAG = os.environ.get("VQ_MISTRAL_MODEL_TAG", "ministral-3:8b-instruct-2512-q4_K_M").strip()
MISTRAL_MODEL_LABEL = os.environ.get("VQ_MISTRAL_MODEL_LABEL", "Ministral 3 8B · Mistral AI").strip()
MODEL_CATALOG = {
    "granite": {"label": GRANITE_MODEL_LABEL,"tag": GRANITE_MODEL_TAG,"api_base": GRANITE_API_BASE,"company": "IBM / Granite","role": "question-type prompt engineering; QLoRA adapter used when trained and enabled"},
    "gemma": {"label": GEMMA_MODEL_LABEL,"tag": GEMMA_MODEL_TAG,"api_base": GEMMA_API_BASE,"company": "Google / Gemma","role": "question-type prompt engineering; QLoRA adapter used when trained and enabled"},
    "mistral": {"label": MISTRAL_MODEL_LABEL,"tag": MISTRAL_MODEL_TAG,"api_base": MISTRAL_API_BASE,"company": "Mistral AI","role": "question-type prompt engineering; QLoRA adapter used when trained and enabled"},
}
SELECTABLE_MODEL_KEYS = ("gemma", "mistral")
DEFAULT_MODEL_KEY = "gemma"

def clean_student_plain_text(value):
    s=str(value or '').strip().replace('**','').replace('__','')
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
    s=re.sub(r'[ \t]+',' ',s); s=re.sub(r'\s+([.,;:!?，。；：！？])',r'\1',s); s=re.sub(r'\n[ \t]+','\n',s)
    return s.strip()

def detect_response_task(question):
    q=str(question or '').strip(); low=q.lower()
    letters=re.findall(r'(?i)(?:^|[\n\r\t ]+)([A-E])\s*[\.\)\:、]\s*',q)
    has_mcq=len(set(x.upper() for x in letters))>=2
    explicit=('calculate','calculation','compute','derive','solve','work out','what is the ratio','what percentage','計算','求出','算出','換算','比值為何','比例為何','多少%','百分比','占全肺量','佔全肺量','約為氧的多少倍')
    has_explicit=any(x in low for x in explicit)
    assigns=re.findall(r'(?i)\b(?:FRC|ERV|RV|VC|TLC|IC|VE|VA|VT|VD|PaCO2|PECO2|PACO2|FiO2)\b\s*[=:]\s*[-+]?\d',q)
    ratio=bool(re.search(r'(?i)(?:FRC\s*(?:/|占|佔).*TLC|VD\s*/\s*VT|多少\s*%|百分之|多少倍|ratio)',q))
    calc_like=has_explicit or len(assigns)>=2 or ratio
    if has_mcq and calc_like:return 'multiple_choice_calculation'
    if has_mcq:return 'multiple_choice_conceptual'
    if (has_explicit and re.search(r'\d',q)) or len(assigns)>=2 or ratio:return 'numerical_calculation'
    if any(x in low for x in ('why','how does','mechanism','cause','reason','為什麼','原因','機轉','如何造成')):return 'mechanism_explanation'
    return 'knowledge_conceptual'

def response_contract(question,language):
    task=detect_response_task(question); zh=str(language or '').lower() in ('zh-tw','zh-hant','zh-cn','zh-hans','zh')
    if task=='multiple_choice_calculation':
        f='正確答案：X. [選項]\n計算：[公式 → 代值 → 結果與單位]\n解釋：[必要的生理意義]' if zh else 'Correct answer: X. [option]\nCalculation: [formula -> substitution -> result and unit]\nExplanation: [brief physiological meaning]'
        return task,'Calculate independently first, check units, compare the result with every provided option, then explicitly select the single matching option. Never guess the option first. Required format: '+f
    if task=='multiple_choice_conceptual':
        f='正確答案：X. [選項]\n解釋：[直接說明為何此選項最正確]' if zh else 'Correct answer: X. [option]\nExplanation: [why this is the single best answer]'
        return task,'Evaluate the choices and explicitly select exactly one best option. Never give explanation only. Required format: '+f
    if task=='numerical_calculation':
        f='答案：[數值與單位]\n計算：[公式 → 代值 → 結果]' if zh else 'Answer: [value and unit]\nCalculation: [formula -> substitution -> result]'
        return task,'Calculate independently, check arithmetic and units, and state the result clearly. Required format: '+f
    if task=='mechanism_explanation':
        f='問題：[重述核心問題]\n回答：[直接結論]\n機轉：[精簡因果關係]' if zh else 'Question: [brief restatement]\nAnswer: [direct conclusion]\nMechanism: [concise causal sequence]'
        return task,'Restate the exact mechanism being asked, answer directly, then explain only the causal pathway needed. Required format: '+f
    f='問題：[簡潔重述學生真正問的問題]\n回答：[直接針對問題作答]\n原因／機轉：[只有需要時才加入]' if zh else 'Question: [briefly restate the exact question]\nAnswer: [answer it directly]\nMechanism: [only if directly useful]'
    return task,'For a knowledge/conceptual question, briefly restate what the student asks and answer that exact question. Do not create extra questions or unrelated background. Required format: '+f

def calculation_safeguard_text():
    return ('Trusted course equations when relevant: FRC = ERV + RV; VC = IRV + VT + ERV; TLC = VC + RV; IC = IRV + VT; '
            'VE = VT x respiratory rate; respiratory rate = VE / VT; VA = (VT - VD) x respiratory rate; VD/VT = (PaCO2 - PECO2) / PaCO2. '
            'Recalculate from the current question values; do not rely on memory of a similar item.')

def course_accuracy_safeguard_text():
    return ('High-confidence safeguards when relevant: normal quiet adult VT is about 500 mL; FRC/TLC using about 2300/5800 is about 40%; '
            'peripheral arterial chemoreceptors respond primarily to low PaO2 and also to elevated PCO2/H+; CO is carbon monoxide and is not CO2, and PaO2 is not total oxygen content; '
            'normal quiet inspiration is primarily diaphragmatic; dynamic compliance can decrease with airflow obstruction/faster breathing; '
            'a solubility coefficient at fixed temperature is not halved simply because ambient pressure is halved. Distinguish the coefficient from dissolved amount.')


def parse_mcq_options(question):
    q=str(question or '')
    matches=list(re.finditer(r'(?is)(?:^|[\n\r\t ]+)([A-E])\s*[\.\)\:、]\s*',q))
    out=[]; seen=set()
    for i,m in enumerate(matches):
        letter=m.group(1).upper(); end=matches[i+1].start() if i+1<len(matches) else len(q)
        text=q[m.end():end].strip(' \t\r\n;；')
        if text and letter not in seen:
            out.append((letter,text)); seen.add(letter)
    return out

def _answer_option_letter(answer):
    t=str(answer or '')
    m=re.search(r'(?i)(?:正確答案|正确答案|correct\s+answer)\s*[:：]\s*([A-E])\b',t)
    if m:return m.group(1).upper()
    m=re.match(r'\s*([A-E])\s*[\.\)\:、]',t,re.I)
    return m.group(1).upper() if m else ''

def _looks_incomplete(text):
    t=str(text or '').strip()
    if not t:return True
    if t[-1] in ',，:：;；-':return True
    return bool(re.search(r'(?i)(?:因此|所以|because|therefore|thus|and|or|which|that|FRC|TLC|PaCO2|PaO2|CO2|CO)\s*[,，:：;；-]?\s*$',t))

def _source_leak_remaining(text):
    t=str(text or '')
    return bool(re.search(r'(?i)\b(?:figure|fig\.?|table|page|chapter|evidence|context|chunk|retrieved\s+passage|textbook\s+evidence)\b|(?:參考文本|参考文本|如圖|如图|第\s*\d+\s*(?:頁|页|章))',t))

def _question_values(question):
    vals={}
    for m in re.finditer(r'(?i)\b(FRC|ERV|RV|VC|TLC|IC|VE|VA|VT|VD|PaCO2|PECO2|PACO2|FiO2|RR)\b\s*[=:]\s*([-+]?\d+(?:\.\d+)?)',str(question or '')):
        try:vals[m.group(1).upper()]=float(m.group(2))
        except Exception:pass
    return vals

def _closest_numeric_option(options,target,relative_tol=0.08,absolute_tol=1.0):
    best=''; best_err=None
    for letter,text in options:
        for x in re.findall(r'[-+]?\d+(?:\.\d+)?',str(text)):
            try:n=float(x)
            except Exception:continue
            err=abs(n-target)
            if best_err is None or err<best_err:best_err=err;best=letter
    if best_err is None:return ''
    return best if best_err<=max(float(absolute_tol),abs(float(target))*float(relative_tol)) else ''

def deterministic_expected_option(question,options):
    q=str(question or ''); low=q.lower(); v=_question_values(q)
    if ('tidal volume' in low or '潮氣' in q or re.search(r'(?i)\bVT\b',q)) and ('normal' in low or '正常' in q or '安靜' in q or 'quiet' in low):
        hit=_closest_numeric_option(options,500.0,0.03,8.0)
        if hit:return hit
    if ('frc' in low and 'tlc' in low and ('%' in q or 'percent' in low or '比例' in q or '比值' in q or '占' in q or '佔' in q)):
        frc=v.get('FRC',2300.0 if ('正常' in q or 'normal' in low) else None); tlc=v.get('TLC',5800.0 if ('正常' in q or 'normal' in low) else None)
        if frc is not None and tlc:
            hit=_closest_numeric_option(options,100.0*frc/tlc,0.08,2.5)
            if hit:return hit
    if ('bohr' in low or 'vd/vt' in low or ('dead space' in low and 'peco2' in low)) and v.get('PACO2') and v.get('PECO2') is not None:
        frac=(v['PACO2']-v['PECO2'])/v['PACO2']; target=100.0*frac if ('%' in q or 'percent' in low or '百分' in q) else frac
        hit=_closest_numeric_option(options,target,0.08,2.0 if target>2 else 0.03)
        if hit:return hit
    if (re.search(r'(?i)\bVA\b',q) or 'alveolar ventilation' in low or '肺泡通氣' in q or '肺泡通气' in q) and all(k in v for k in ('VE','VT','VD')) and v['VT']:
        hit=_closest_numeric_option(options,(v['VT']-v['VD'])*(v['VE']/v['VT']),0.03,5.0)
        if hit:return hit
    if ('respiratory rate' in low or '呼吸頻率' in q or '呼吸频率' in q or re.search(r'(?i)\bRR\b',q)) and all(k in v for k in ('VE','VT')) and v['VT']:
        hit=_closest_numeric_option(options,v['VE']/v['VT'],0.03,0.5)
        if hit:return hit
    return ''

def fast_verify_kb_answer(question,answer,language):
    task=detect_response_task(question); reasons=[]; clean=clean_student_plain_text(answer); opts=parse_mcq_options(question)
    if not clean or _looks_incomplete(clean):reasons.append('incomplete')
    if _source_leak_remaining(clean):reasons.append('source-attribution')
    letter=''; expected=''
    if task.startswith('multiple_choice'):
        letter=_answer_option_letter(clean); allowed=set(a for a,_ in opts)
        if not letter or letter not in allowed:reasons.append('mcq-option-missing-or-invalid')
        expected=deterministic_expected_option(question,opts)
        if expected and letter and letter!=expected:reasons.append('deterministic-option-mismatch')
    return {'ok':not reasons,'reasons':reasons,'task':task,'option':letter,'expected_option':expected}

def review_kb_answer(server,model_key,question,draft,language,grounded_context,canonical,reasons):
    task=detect_response_task(question)
    sys_prompt=(
        'You are a second-stage respiratory-physiology answer reviewer. Correctness constraints are strict, but surface wording is flexible. '
        'Do not rewrite a correct draft merely for style. Check factual correctness, option selection, calculations, units, and whether the draft directly answers the question. '
        'Use only the supplied KB evidence and canonical fallback. If correct, return JSON {"verdict":"pass","corrected_answer":""}. '
        'If incorrect, return JSON {"verdict":"correct","corrected_answer":"..."} with the minimum necessary correction. '
        'Do not cite sources, evidence, ranks, figures, tables, pages, chapters, or hidden reasoning.'
    )
    user=('Question:\n'+question+'\n\nDraft:\n'+draft+'\n\nReview trigger:\n'+','.join(reasons)+'\n\nKB evidence:\n'+grounded_context+
          '\n\nCanonical fallback:\n'+canonical+'\n\nOutput language: '+language+'\n\n'+response_contract(question,language)[1]+'\n'+calculation_safeguard_text())
    key,resolved,data=run_selected_chat(server,model_key,[{'role':'system','content':sys_prompt},{'role':'user','content':user}],
        {'temperature':0.0,'top_p':0.55,'num_ctx':MODEL_NUM_CTX,'num_predict':220,'repeat_penalty':1.0},timeout=FINAL_TIMEOUT_SEC,json_format=True)
    try:obj=json.loads(((data.get('message') or {}).get('content') or '').strip() or '{}')
    except Exception:obj={}
    verdict=str(obj.get('verdict','')).strip().lower(); corrected=clean_student_plain_text(obj.get('corrected_answer',''))
    if verdict=='pass':return draft,{'used':True,'verdict':'pass'}
    if corrected:return corrected,{'used':True,'verdict':'correct'}
    return draft,{'used':True,'verdict':'unusable'}

SYSTEM_PROMPT = (
    "You are the grounded response-synthesis layer for a respiratory physiology teaching assistant. "
    "Answer the exact student task in the response form appropriate to that question type; do not drift to adjacent concepts. "
    "For MCQs, explicitly select the single best option. For calculations, recompute before selecting. For knowledge questions, briefly restate the question and answer it directly. " + course_accuracy_safeguard_text() + " "
    "Before final output, silently verify that option letter, calculation, units, physiological direction, and output language are mutually consistent. Do not expose hidden reasoning. Correctness constraints are strict, but surface wording may vary naturally; do not force identical phrasing when the answer is already correct. "
    "The retrieved KB records supplied by the application are the ONLY factual sources you may use. "
    "The application has already completed deterministic Active-7 Knowledge Base scoring and KB Gate = 68. "
    "There is no formal Domain Gate in this build. Do not redo retrieval, do not override ranking, and do not use outside medical knowledge. "
    "Prioritize Rank 1 and use lower-ranked records only when they add relevant, non-duplicate support. "
    "For comparison questions, state the central distinction first, then explain each named concept. "
    "Preserve numerical values, formulas, medical terminology, directions of change, and causal relationships from supplied evidence. "
    "When a ratio is described as 2.5 times the ideal/normal value, write it as approximately 2.5 times the ideal value (約為理想值的 2.5 倍), never as 高出 2.5 倍. "
    "Do not add mechanisms, examples, diagnoses, consequences, or background facts unless they appear in supplied evidence. "
    "Never mention ranks, scores, retrieval, gates, prompts, models, or knowledge-base internals. "
    "When the requested language is zh-TW or zh-Hant, output Traditional Chinese as used in Taiwan only; never output Simplified Chinese characters. "
    "For a simple definition question, define only the requested term and its essential physiological meaning; do not append edge cases or adjacent examples unless explicitly asked. "
    "Return natural plain text only, with 2 to 5 concise sentences in the requested language. Never use TeX/LaTeX, Markdown math, dollar-sign math delimiters, backslash commands, or code notation; write formulas in plain medical notation such as V/Q, VA, PaO2, PaCO2, and FiO2. Never mention figures, tables, page/chapter numbers, textbook/source labels, or phrases such as as shown in Figure. State only the physiological knowledge needed to answer. In prose, use the full professional term on first mention when helpful, for example alveolar ventilation (VA), minute ventilation (VE), tidal volume (VT), and dead-space volume (VD); never raw underscore notation such as V_A. "
    "If the supplied evidence cannot safely support a direct answer, return the canonical fallback answer unchanged."
)


# Common Simplified-only characters used as a fast detector. If any are present in a
# zh-TW response, the same selected LLM performs a zero-temperature orthographic
# normalization pass. This avoids forcing a second inference for already-correct text.
SIMPLIFIED_HINT_CHARS = set(
    "这为个们来时说对会从还没让与并过后里发见问题应医气体压经学变动较实"
    "机开关点数总区间进则级组别类种传达选显听语声头脑网简复统冲击临结论"
    "脏脉浓扩质导严现处产响减维调节术华台湾获识别优势频线载设备进阶"
)

def needs_tw_normalization(text):
    return any(ch in SIMPLIFIED_HINT_CHARS for ch in (text or ""))


def normalize_tw_with_selected_model(server, model_key, text):
    """Orthographic/wording normalization only; never add factual content."""
    if not text or not needs_tw_normalization(text):
        return text
    system = (
        "Convert the supplied text to Traditional Chinese as used in Taiwan (zh-TW). "
        "Preserve every factual claim, number, formula, abbreviation, and medical term exactly in meaning. "
        "Do not add, remove, explain, summarize, or correct facts. Output plain text only. "
        "Use Traditional Chinese characters only; do not output Simplified Chinese."
    )
    try:
        _key, _resolved, data = run_selected_chat(
            server, model_key,
            [{"role": "system", "content": system},
             {"role": "user", "content": text}],
            {
                "temperature": 0.0,
                "top_p": 0.5,
                "num_ctx": MODEL_NUM_CTX,
                "num_predict": 260,
                "repeat_penalty": 1.0,
            }, timeout=180, json_format=False
        )
        converted = ((data.get("message") or {}).get("content") or "").strip()
        if converted:
            converted = converted.replace("**", "").replace("__", "")
            converted = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", converted)
            return converted
    except Exception:
        pass
    return text


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def url_json(url, payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ollama_ready(base=None):
    base=(base or OLLAMA_BASE).rstrip("/")
    try:
        url_json(base + "/api/version", timeout=2)
        return True
    except Exception:
        return False


def start_ollama_if_needed():
    if ollama_ready():return True,""
    exe = shutil.which("ollama")
    if not exe:
        return False, "Ollama executable not found"
    if ollama_ready():
        return True, ""
    try:
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen([exe, "serve"], creationflags=flags)
    except Exception as exc:
        return False, str(exc)
    for _ in range(40):
        if ollama_ready():
            return True, ""
        time.sleep(0.5)
    return False, "Ollama did not become ready"


def installed_ollama_models():
    try:
        data=url_json(OLLAMA_BASE+'/api/tags',timeout=4)
        return set(row.get('name') or row.get('model') for row in data.get('models',[]) if row.get('name') or row.get('model'))
    except Exception:pass
    exe = shutil.which("ollama")
    if not exe:
        return set()
    try:
        proc = subprocess.Popen([exe, "list"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        out, _ = proc.communicate(timeout=15)
    except Exception:
        return set()
    names = set()
    for line in (out or "").splitlines()[1:]:
        parts = line.split()
        if parts:
            names.add(parts[0].strip())
    return names


def installed_models_for_base(base):
    base=(base or OLLAMA_BASE).rstrip("/")
    if base == OLLAMA_BASE:
        return installed_ollama_models()
    try:
        data=url_json(base + "/api/tags", timeout=4)
        return set((m.get("name") or m.get("model") or "") for m in data.get("models", []))
    except Exception:
        return set()


def model_api_base(key):
    return str(MODEL_CATALOG[resolve_model_key(key)].get("api_base") or OLLAMA_BASE).rstrip("/")


def public_model_catalog():
    out={}
    for key in SELECTABLE_MODEL_KEYS:
        value=MODEL_CATALOG[key]
        out[key]={k:v for k,v in value.items() if k != "api_base"}
    return out


def resolve_model_key(value):
    key = str(value or "").strip().lower()
    return key if key in MODEL_CATALOG else DEFAULT_MODEL_KEY


def model_meta(key):
    return MODEL_CATALOG[resolve_model_key(key)]


def ollama_unload(tag, base=None):
    base=(base or OLLAMA_BASE).rstrip("/")
    try:
        url_json(base + "/api/generate", payload={
            "model": tag,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        }, timeout=30)
    except Exception:
        pass


def run_selected_chat(server, model_key, messages, options, timeout, json_format=False):
    key = resolve_model_key(model_key)
    meta = model_meta(key)
    tag = meta["tag"]
    base = model_api_base(key)
    with server.model_lock.using(tag):
        if server.mode != "ollama":
            raise RuntimeError("Local model mode is not enabled")
        if not ollama_ready(base):
            raise RuntimeError("Local model service is not available: %s" % base)
        available = installed_models_for_base(base)
        if tag not in available:
            raise RuntimeError("Model is not available on its configured local service: %s" % tag)

        previous = server.active_model_tag
        previous_base = getattr(server, "active_model_base", None)
        # Keep both models loaded when the GPU has room (see gpu_policy).
        if previous and (previous != tag or previous_base != base) and not gpu_policy.keep_models_loaded():
            ollama_unload(previous, previous_base)
            server.active_model_tag = None
            server.active_model_key = None
            server.active_model_base = None

        payload = {
            "model": tag,
            "messages": messages,
            "think": False,
            "stream": False,
            "keep_alive": MODEL_KEEP_ALIVE,
            "options": options,
        }
        if json_format:
            payload["format"] = "json"
        data = url_json(base + "/api/chat", payload=payload, timeout=float(timeout))
        server.active_model_tag = tag
        server.active_model_key = key
        server.active_model_base = base
        return key, meta, data

def performance_metrics(data):
    eval_count = int(data.get("eval_count") or 0)
    eval_duration = int(data.get("eval_duration") or 0)
    tokens_per_second = 0.0
    if eval_count > 0 and eval_duration > 0:
        tokens_per_second = eval_count / (eval_duration / 1000000000.0)
    return {
        "total_duration_ns": data.get("total_duration"),
        "load_duration_ns": data.get("load_duration"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "prompt_eval_duration_ns": data.get("prompt_eval_duration"),
        "eval_count": eval_count,
        "eval_duration_ns": eval_duration,
        "tokens_per_second": round(tokens_per_second, 2),
    }


def append_audit(question, canonical, answer, mode, model_key, model_tag,
                 latency_ms, used_model, metrics=None):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        exists = LOG_FILE.exists()
        metrics = metrics or {}
        with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            if not exists:
                writer.writerow([
                    "timestamp", "question", "canonical_answer", "final_answer",
                    "mode", "model_key", "model_tag", "latency_ms", "used_model",
                    "prompt_eval_count", "eval_count", "tokens_per_second"
                ])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"), question, canonical, answer,
                mode, model_key or "", model_tag or "", round(latency_ms, 1), bool(used_model),
                metrics.get("prompt_eval_count", ""), metrics.get("eval_count", ""),
                metrics.get("tokens_per_second", "")
            ])
    except Exception:
        pass


class Handler(SharedAssetsMixin, SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        SimpleHTTPRequestHandler.end_headers(self)

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") or (len(args) > 1 and str(args[1]).startswith(("4", "5"))):
            SimpleHTTPRequestHandler.log_message(self, fmt, *args)

    def send_json(self, status, obj):
        body = json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, status, body, content_type, extra_headers=None):
        body = body or b""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        if self.path=="/api/ready":
            self.send_json(200,{'ok':True,'content_integrity_revision':'R15','release_revision':'R16','service':'KB','warmup':getattr(self.server,'warmup_status','not_requested')});return
        if self.path == "/api/health":
            self.send_json(200, {
                "ok": True,
                "service": "VQ TA local model server",
                "build": BUILD,
                "mode": self.server.mode,
                "domain_gate_enabled": False,
                "kb_gate": 68,
                "qa_stage_timeouts_sec": {"semantic": SEMANTIC_TIMEOUT_SEC, "final": FINAL_TIMEOUT_SEC},
                "language_detection": {
                    "extra_llm_call": False,
                    "voice": "faster-whisper detected language",
                    "typed": "medical-term-neutral lightweight router + semantic-pass ambiguity fallback"
                },
                "active_model_key": self.server.active_model_key,
                "active_model": self.server.active_model_tag,
                "multilingual_stt": {"available": stt_available(), "engine": "faster-whisper auto language detection"},
                "server_tts": {
                    "available": tts_available() or teacher_tts_available(),
                    "engine": "authorized teacher-reference XTTS-v2 for Zhiwei; edge-tts for Yating",
                    "teacher_voice_available": teacher_tts_available(),
                    "female_voice_zh": TTS_VOICES["yating"]["zh"],
                    "female_voice_en": TTS_VOICES["yating"]["en"],
                },
                "models": dict((key, {
                    "label": value["label"],
                    "tag": value["tag"],
                    "installed": value["tag"] in installed_models_for_base(model_api_base(key)),
                }) for key, value in MODEL_CATALOG.items())
            })
            return
        if self.path == "/api/models":
            self.send_json(200, {
                "default": DEFAULT_MODEL_KEY,
                "models": public_model_catalog(),
                "active_model_key": self.server.active_model_key,
            })
            return
        if self.path == "/":
            self.path = "/index.html?v=2042"
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        if self.path == "/api/stt":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 25 * 1024 * 1024:
                    self.send_json(400, {"error": "Invalid audio size"})
                    return
                raw = self.rfile.read(length)
                result = _stt_transcribe(raw, self.headers.get("Content-Type", "audio/webm"), self.headers.get("X-VQ-Language-Hint", ""))
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(503, {"error": str(exc), "stt_available": stt_available()})
            return

        if self.path == "/api/tts":
            try:
                req = self.read_json()
            except Exception:
                self.send_json(400, {"error": "Invalid JSON"})
                return
            text = str(req.get("text", "") or "").strip()
            role = str(req.get("role", "zhiwei") or "zhiwei").strip().lower()
            rate = req.get("rate", 1.0)
            pitch = req.get("pitch", 1.0)
            language = str(req.get("language", "auto") or "auto")
            try:
                audio, meta = generate_tts_audio(text, role, rate, pitch, language)
                self.send_bytes(200, audio, meta.get("content_type", "audio/mpeg"), {
                    "X-VQ-TTS-Voice": meta["voice"],
                    "X-VQ-TTS-Gender": meta["gender"],
                    "X-VQ-TTS-Cloned": "1" if meta.get("cloned") else "0",
                })
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(503, {"error": str(exc), "tts_available": tts_available()})
            return

        if self.path == "/api/warm-model":
            try:
                req = self.read_json()
            except Exception:
                self.send_json(400, {"error": "Invalid JSON"})
                return
            model_key = resolve_model_key(req.get("model_key"))
            meta = model_meta(model_key)
            if self.server.mode != "ollama":
                self.send_json(503, {"ok": False, "error": "Ollama mode is not enabled", "model_key": model_key})
                return
            try:
                started = time.perf_counter()
                key, resolved, _data = run_selected_chat(
                    self.server, model_key,
                    [{"role":"system","content":"Reply with OK only."},
                     {"role":"user","content":"OK"}],
                    {"temperature":0.0,"num_ctx":MODEL_NUM_CTX,"num_predict":1},
                    timeout=30, json_format=False
                )
                self.send_json(200, {
                    "ok": True, "model_key": key, "model": resolved["tag"],
                    "warm_ms": round((time.perf_counter()-started)*1000.0, 1)
                })
            except Exception as exc:
                self.send_json(503, {"ok": False, "model_key": model_key, "model": meta["tag"], "error": str(exc)})
            return

        if self.path == "/api/normalize":
            try:
                req = self.read_json()
            except Exception:
                self.send_json(400, {"error": "Invalid JSON"})
                return

            question = str(req.get("question", "")).strip()
            model_key = resolve_model_key(req.get("model_key"))
            meta = model_meta(model_key)
            allowed_terms = req.get("allowed_terms") or []
            if not isinstance(allowed_terms, list):
                allowed_terms = []
            allowed_terms = [str(item).strip() for item in allowed_terms if str(item).strip()][:500]
            if not question or not allowed_terms:
                self.send_json(200, {
                    "in_scope": False, "terms": [], "confidence": 0,
                    "normalized_question": "", "language_tag": "", "language_confidence": 0, "model_key": model_key,
                    "model": meta["tag"], "model_label": meta["label"]
                })
                return
            if self.server.mode != "ollama":
                self.send_json(200, {
                    "in_scope": False, "terms": [], "confidence": 0,
                    "normalized_question": "", "language_tag": "", "language_confidence": 0, "model_key": model_key,
                    "model": meta["tag"], "model_label": meta["label"],
                    "error": "mock mode"
                })
                return

            allowed_lookup = dict((item.casefold(), item) for item in allowed_terms)
            allowed_text = "\n".join("- " + item for item in allowed_terms)
            semantic_prompt = (
                "You are the semantic recognition layer for a respiratory physiology teaching assistant. "
                "Do not answer the medical question. Map natural language to exact canonical terms from the allowlist only. "
                "Return JSON only with keys: in_scope (boolean), terms (array of 1-8 exact allowlist strings), "
                "confidence (0 to 1), normalized_question (short canonical restatement), "
                "language_tag (dominant natural-language BCP-47 tag), language_confidence (0 to 1). "
                "For language_tag, judge the language from ordinary words, grammar, particles, and sentence structure. "
                "Respiratory/medical English terms and abbreviations such as V/Q, PaO2, PaCO2, FiO2, SpO2, PEEP, shunt, dead space, "
                "ventilation, perfusion, hypoxemia, hypercapnia, and compliance are content tokens and MUST NOT override the surrounding natural language. "
                "For mixed text, choose the language of the surrounding general-language sentence. "
                "Use zh-TW for Traditional Chinese and zh-CN for Simplified Chinese when distinguishable. "
                "Mark in_scope=false when the medical mapping is unrelated or too uncertain; language_tag is independent of in_scope. "
                "Handle multilingual input, mixed language, abbreviations, typos, follow-up phrasing, and conversational wording."
            )
            user_content = (
                "Student question:\n" + question + "\n\n"
                "Allowed canonical terms:\n" + allowed_text + "\n\n"
                "Return JSON only."
            )
            try:
                key, resolved, data = run_selected_chat(
                    self.server, model_key,
                    [{"role": "system", "content": semantic_prompt},
                     {"role": "user", "content": user_content}],
                    {
                        "temperature": 0.02,
                        "top_p": 0.70,
                        "num_ctx": MODEL_NUM_CTX,
                        "num_predict": 200,
                        "repeat_penalty": 1.02,
                    },
                    timeout=SEMANTIC_TIMEOUT_SEC, json_format=True
                )
                content = ((data.get("message") or {}).get("content") or "").strip()
                parsed = json.loads(content) if content else {}
            except Exception as exc:
                self.send_json(200, {
                    "in_scope": False, "terms": [], "confidence": 0,
                    "normalized_question": "", "language_tag": "", "language_confidence": 0, "model_key": model_key,
                    "model": meta["tag"], "model_label": meta["label"],
                    "error": str(exc)
                })
                return

            raw_terms = parsed.get("terms") or []
            if not isinstance(raw_terms, list):
                raw_terms = []
            safe_terms = []
            seen = set()
            for term in raw_terms:
                lowered = str(term).strip().casefold()
                if lowered in allowed_lookup and lowered not in seen:
                    seen.add(lowered)
                    safe_terms.append(allowed_lookup[lowered])
                if len(safe_terms) >= 8:
                    break
            try:
                confidence = float(parsed.get("confidence", 0) or 0)
            except Exception:
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            try:
                language_confidence = float(parsed.get("language_confidence", 0) or 0)
            except Exception:
                language_confidence = 0.0
            language_confidence = max(0.0, min(1.0, language_confidence))
            in_scope = bool(parsed.get("in_scope")) and bool(safe_terms) and confidence >= 0.45
            result = {
                "in_scope": in_scope,
                "terms": safe_terms if in_scope else [],
                "confidence": confidence,
                "normalized_question": str(parsed.get("normalized_question", "") or "")[:500],
                "language_tag": str(parsed.get("language_tag", "") or "")[:32],
                "language_confidence": language_confidence,
                "model_key": key,
                "model": resolved["tag"],
                "model_label": resolved["label"],
            }
            result.update(performance_metrics(data))
            self.send_json(200, result)
            return

        if self.path != "/api/diversify":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            req = self.read_json()
        except Exception:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        canonical = str(req.get("canonical_answer", "")).strip()
        question = str(req.get("question", "")).strip()
        language = str(req.get("language", "zh-TW")).strip() or "zh-TW"
        model_key = resolve_model_key(req.get("model_key"))
        meta = model_meta(model_key)
        kb_candidates = req.get("kb_candidates") or []
        if not isinstance(kb_candidates, list):
            kb_candidates = []
        kb_candidates = kb_candidates[:6]
        response_support = req.get("response_support") or []
        if not isinstance(response_support, list):
            response_support = []
        response_support = response_support[:4]
        response_target_groups = req.get("response_target_groups") or []
        if not isinstance(response_target_groups, list):
            response_target_groups = []
        response_target_groups = response_target_groups[:4]
        response_intent = str(req.get("response_intent", "general") or "general").strip().lower()
        if not canonical:
            self.send_json(400, {"error": "canonical_answer is required"})
            return

        request_start = time.perf_counter()
        correction = find_correction(question, '') if question else None
        if correction:
            latency_ms = (time.perf_counter() - request_start) * 1000.0
            self.send_json(200, {
                "answer": correction["correction_answer"], "used_model": False,
                "mode": "auto-corrected-answer-memory", "model_key": correction.get("correction_model_key") or model_key,
                "model": meta["tag"], "model_label": meta["label"], "latency_ms": round(latency_ms, 1),
                "feedback_correction": True, "correction_id": correction["id"]
            })
            return
        if self.server.mode == "mock":
            latency_ms = (time.perf_counter() - request_start) * 1000.0
            append_audit(question, canonical, canonical, "mock", model_key, meta["tag"], latency_ms, False)
            self.send_json(200, {
                "answer": canonical, "used_model": False, "mode": "mock",
                "model_key": model_key, "model": meta["tag"], "model_label": meta["label"],
                "latency_ms": round(latency_ms, 1)
            })
            return

        evidence_lines = []
        for item in kb_candidates:
            if not isinstance(item, dict):
                continue
            rank = item.get("rank", len(evidence_lines) + 1)
            score = item.get("score", "")
            evidence_lines.append("[Rank %s; score %s]" % (rank, score))
            for key_name, label in [
                ("courseTitle", "Course"), ("concept", "Concept"),
                ("definition", "Definition"), ("equation", "Equation"),
                ("mechanism", "Mechanism"), ("example", "Example"),
                ("model", "Model"), ("clinical", "Clinical"),
                ("misconception", "Misconception")]:
                value = str(item.get(key_name, "") or "").strip()
                if value:
                    evidence_lines.append(label + ": " + value)
            evidence_lines.append("")
        grounded_context = "\n".join(evidence_lines).strip()
        has_top6 = bool(grounded_context)

        support_lines = []
        for item in response_support:
            if not isinstance(item, dict):
                continue
            concept = str(item.get("concept", "") or "").strip()
            if concept:
                support_lines.append("[Explicit target: " + concept + "]")
            for key_name, label in [
                ("definition", "Definition"), ("equation", "Equation"),
                ("mechanism", "Mechanism"), ("example", "Example"),
                ("clinical", "Clinical"), ("misconception", "Misconception")]:
                value = str(item.get(key_name, "") or "").strip()
                if value:
                    support_lines.append(label + ": " + value)
            support_lines.append("")
        response_support_context = "\n".join(support_lines).strip()

        target_group_lines = []
        for group in response_target_groups:
            if not isinstance(group, dict):
                continue
            target = str(group.get("target", "") or "").strip()
            records = group.get("records") or []
            if not isinstance(records, list):
                records = []
            if target:
                target_group_lines.append("=== TARGET: " + target + " ===")
            for item in records[:6]:
                if not isinstance(item, dict):
                    continue
                concept = str(item.get("concept", "") or "").strip()
                if concept:
                    target_group_lines.append("Concept: " + concept)
                for key_name, label in [
                    ("definition", "Definition"), ("equation", "Equation"),
                    ("mechanism", "Mechanism"), ("example", "Example"),
                    ("clinical", "Clinical"), ("misconception", "Misconception")]:
                    value = str(item.get(key_name, "") or "").strip()
                    if value:
                        target_group_lines.append(label + ": " + value)
                target_group_lines.append("")
            target_group_lines.append("")
        target_group_context = "\n".join(target_group_lines).strip()

        if has_top6:
            support_section = ("\n\nExplicit target evidence for answer quality only (NOT part of retrieval ranking):\n" + response_support_context) if response_support_context else ""
            target_group_section = ("\n\nTarget-specific evidence bundles (answer layer only; NOT part of scoring):\n" + target_group_context) if target_group_context else ""
            compare_instruction = ""
            if response_intent == "compare":
                compare_instruction = (
                    "\nThis is a comparison question. If target-specific evidence bundles are supplied, "
                    "treat them as primary comparison evidence. State what each named target represents physiologically "
                    "and the key difference between them. Do not add unsupported determinants."
                )
            user_content = (
                "Student question:\n" + question + "\n\n"
                "Retrieved KB candidates (highest rank first):\n" + grounded_context +
                support_section + target_group_section + "\n\n"
                "Canonical fallback answer:\n" + canonical + "\n\n"
                "Output language: " + language + "\n" + compare_instruction + "\n"
                "Response task: " + response_contract(question, language)[0] + "\n"
                "Mandatory response contract: " + response_contract(question, language)[1] + "\n"
                + calculation_safeguard_text() + "\n"
                "If output language is zh-TW or zh-Hant, use Taiwan Traditional Chinese characters only and no Simplified Chinese.\n"
                "Answer in natural plain text using only supplied evidence."
            )
            active_prompt = SYSTEM_PROMPT
            response_mode = "top6-grounded"
        else:
            user_content = (
                "Student question:\n" + question + "\n\n"
                "Canonical fallback answer:\n" + canonical + "\n\n"
                "Output language: " + language + "\n"
                "Response task: " + response_contract(question, language)[0] + "\n"
                "Mandatory response contract: " + response_contract(question, language)[1] + "\n"
                + calculation_safeguard_text() + "\n"
                "If output language is zh-TW or zh-Hant, use Taiwan Traditional Chinese characters only and no Simplified Chinese.\n"
                "Follow the response contract while using the canonical answer as the factual source; do not add unsupported facts."
            )
            active_prompt = (
                "You are a controlled paraphrasing layer. Use only the canonical fallback answer as factual source. "
                "Do not add facts. When the requested language is zh-TW or zh-Hant, output Taiwan Traditional Chinese only and never Simplified Chinese. "
                "Follow the mandatory question-type response contract in the user message. Return natural plain text only."
            )
            response_mode = "canonical-paraphrase"

        try:
            key, resolved, data = run_selected_chat(
                self.server, model_key,
                [{"role": "system", "content": active_prompt},
                 {"role": "user", "content": user_content}],
                {
                    "temperature": 0.16,
                    "top_p": 0.82,
                    "num_ctx": MODEL_NUM_CTX,
                    "num_predict": 420 if has_top6 else 320,
                    "repeat_penalty": 1.0,
                }, timeout=FINAL_TIMEOUT_SEC, json_format=False
            )
            answer = ((data.get("message") or {}).get("content") or "").strip()
            if answer:
                answer = answer.replace("**", "").replace("__", "")
                answer = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", answer)
                answer = re.sub(r"(?m)^\s*[-*]\s+", "", answer)
                answer = re.sub(r"(?:比\s*)?理想值\s*(?:高出|高了|高)\s*(?:約\s*)?2(?:\.|點)5\s*倍", "約為理想值的 2.5 倍", answer)
                answer = clean_student_plain_text(answer)
            if not answer:
                answer = clean_student_plain_text(canonical)
            if answer and language.lower() in ("zh-tw", "zh-hant") and needs_tw_normalization(answer):
                answer = clean_student_plain_text(normalize_tw_with_selected_model(self.server, key, answer))
            # v227 two-stage verification: deterministic audit for every answer,
            # conditional short semantic review only when the audit finds a risk.
            verification = {"fast_verified": False, "semantic_review_used": False, "review_reason": []}
            fast = fast_verify_kb_answer(question, answer, language)
            reasons = list(fast.get("reasons") or [])
            # Near-gate MCQ answers are semantically rechecked; ordinary answers stay single-pass.
            try:
                top_score = float((kb_candidates[0] or {}).get("score") or 100) if kb_candidates else 100.0
            except Exception:
                top_score = 100.0
            if detect_response_task(question).startswith("multiple_choice") and top_score < 72.0:
                reasons.append("near-kb-gate")
            if reasons:
                try:
                    answer, review_meta = review_kb_answer(self.server, key, question, answer, language, grounded_context, canonical, sorted(set(reasons)))
                    verification["semantic_review_used"] = True
                    verification["semantic_review_verdict"] = review_meta.get("verdict")
                    verification["review_reason"] = sorted(set(reasons))
                    fast = fast_verify_kb_answer(question, answer, language)
                except Exception:
                    verification["review_reason"] = sorted(set(reasons))
            verification["fast_verified"] = bool(fast.get("ok"))
            verification["fast_result"] = fast
            # v204.2 response path: semantic recognition + final synthesis.
            # Language routing does NOT consume a separate LLM inference.
            # Preserve v199's optional zh-TW orthographic repair only when needed.
            latency_ms = (time.perf_counter() - request_start) * 1000.0
            metrics = performance_metrics(data)
            append_audit(question, canonical, answer, response_mode, key, resolved["tag"], latency_ms, answer != canonical, metrics)
            response = {
                "answer": answer,
                "used_model": answer != canonical,
                "mode": response_mode,
                "model_key": key,
                "model": resolved["tag"],
                "model_label": resolved["label"],
                "top6_count": len(kb_candidates),
                "response_support_count": len(response_support),
                "response_intent": response_intent,
                "latency_ms": round(latency_ms, 1),
                "verification": verification,
            }
            response.update(metrics)
            self.send_json(200, response)
        except Exception as exc:
            latency_ms = (time.perf_counter() - request_start) * 1000.0
            append_audit(question, canonical, canonical, "fallback", model_key, meta["tag"], latency_ms, False)
            self.send_json(200, {
                "answer": canonical,
                "used_model": False,
                "mode": "fallback",
                "model_key": model_key,
                "model": meta["tag"],
                "model_label": meta["label"],
                "top6_count": len(kb_candidates),
                "latency_ms": round(latency_ms, 1),
                "verification": {"fast_verified": False, "semantic_review_used": False, "review_reason": ["generation-error"]},
                "error": str(exc)
            })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["mock", "ollama"], default="mock")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", choices=["main", "test", "none"], default="main")
    args = parser.parse_args()

    print("\nVQ TA %s - 3-Model One-Pipeline" % BUILD)
    print("--------------------------------")
    print("Formal Domain Gate: disabled")
    print("Active-7 KB Gate: 68")
    print("Host: %s:%s" % (args.host, args.port))
    print("Mode: %s" % args.mode)
    print("Server-side TTS: %s" % ("ready" if tts_available() else "NOT INSTALLED"))
    print("  female: %s" % TTS_VOICES["yating"]["zh"])
    print("  male:   %s" % TTS_VOICES["zhiwei"]["zh"])
    for key in ("granite", "gemma", "mistral"):
        item = MODEL_CATALOG[key]
        print("  %-9s %s  [%s]" % (key, item["label"], item["tag"]))

    if args.mode == "ollama":
        ok, error = start_ollama_if_needed()
        if not ok:
            print("\n[ERROR] Ollama is unavailable: %s" % error)
            print("Install/start Ollama, then run scripts/setup/prepare_models.sh from the repository root.")
            sys.exit(1)
        installed = installed_ollama_models()
        missing = [item["tag"] for key,item in MODEL_CATALOG.items() if model_api_base(key)==OLLAMA_BASE and item["tag"] not in installed]
        if missing:
            print("\n[WARN] Missing local models:")
            for tag in missing:
                print("  - " + tag)
            print("Run scripts/setup/prepare_models.sh from the repository root before testing those choices.\n")
    else:
        installed = set()

    # Python 3.6 SimpleHTTPRequestHandler has no `directory=` argument.
    os.chdir(str(WEB_DIR))
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print("\n[ERROR] Could not start port %s: %s" % (args.port, exc))
        sys.exit(1)
    httpd.mode = args.mode
    httpd.installed_models = installed
    httpd.active_model_tag = None
    httpd.active_model_key = None
    httpd.active_model_base = None
    httpd.model_lock = SharedModelLock()

    # Warm the default model in the background so loading cannot block HTTP readiness.
    def warm_default():
        if args.mode == "ollama" and model_meta(DEFAULT_MODEL_KEY)["tag"] in installed_models_for_base(model_api_base(DEFAULT_MODEL_KEY)):
            try:
                httpd.warmup_status='preparing'
                print("[v204.2] Pre-warming default model: %s" % model_meta(DEFAULT_MODEL_KEY)["tag"])
                run_selected_chat(
                    httpd, DEFAULT_MODEL_KEY,
                    [{"role":"system","content":"Reply with OK only."},
                     {"role":"user","content":"OK"}],
                    {"temperature":0.0,"num_ctx":MODEL_NUM_CTX,"num_predict":1},
                    timeout=30, json_format=False
                )
                print("[v204.2] Default model is warm and will stay resident for %s." % MODEL_KEEP_ALIVE)
                httpd.warmup_status='ready'
            except Exception as exc:
                httpd.warmup_status='error'
                print("[WARN] Model pre-warm failed: %s" % exc)
    httpd.warmup_status='not_requested'
    threading.Thread(target=warm_default,daemon=True).start()

    if args.open != "none":
        def open_browser():
            time.sleep(0.8)
            page = "index.html?v=2042" if args.open == "main" else "server_test.html?v=2042"
            webbrowser.open("http://127.0.0.1:%s/%s" % (args.port, page))
        threading.Thread(target=open_browser, daemon=True).start()

    print("\n[OK] Server started. Press Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if httpd.active_model_tag:
            ollama_unload(httpd.active_model_tag, getattr(httpd, "active_model_base", None))
        httpd.server_close()
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
