#!/usr/bin/env python3
from __future__ import print_function
import hashlib, json, math, os, random, re, shutil, sqlite3, struct, threading, time, unicodedata, urllib.error, urllib.request, uuid
from array import array
from collections import Counter
from contextlib import closing, contextmanager
from cpu_budget import cpu_slot
from work_steps import step_scope, reject_steps, steps_mark, completed_call, model_signature, atomic_json as step_json
import gpu_policy
from pathlib import Path
from exam_questions import build as build_exam_questions,reviewed_card
from math_integrity import math_issues, question_math_ok
from content_integrity import text_issues, clean_course_text, question_text_ok, extract_page, ContentIntegrityError, SOURCE_RULE
from pdf_ingestion import iter_pages, unit_quality, write_receipt, atomic_json, upload_diagnostics, reading_mode, REVISION as INGESTION_REVISION
from course_grounding import review_text,GroundingError,REVISION as REVIEW_REVISION
from ollama_cancel import BackgroundWork, PAUSED, retry_transient
from pdf_vision import LocalVision


def _duration(seconds):
    seconds=max(0,int(seconds or 0))
    if seconds<90:return '1 min'
    if seconds<5400:return '%d min'%round(seconds/60.0)
    return '%.1f h'%(seconds/3600.0)


def _shared_pages(stage_directory,identity):
    """Page readings are kept by PDF content, so uploading the same PDF again (any teacher, any
    stage) reuses every page already read. Readings an earlier upload left in its own stage
    folder are adopted the first time."""
    root=COURSE_ROOT/'_page_cache'
    name=hashlib.sha256((INGESTION_REVISION+'|'+identity).encode()).hexdigest()[:24]
    target=root/name
    if not target.exists():
        found=[path for path in [Path(stage_directory)/'page_cache'/name]+list(STAGING_ROOT.glob('*/page_cache/'+name)) if path.is_dir()]
        if found:
            best=max(found,key=lambda path:sum(1 for _ in path.glob('*.json')))
            root.mkdir(parents=True,exist_ok=True)
            temporary=root/(name+'.adopt-'+uuid.uuid4().hex[:8])
            try:
                shutil.copytree(str(best),str(temporary))
                if not target.exists():os.rename(str(temporary),str(target))
            except OSError:pass
            finally:shutil.rmtree(str(temporary),ignore_errors=True)
    return root


class CourseDeleted(Exception):
    """The teacher deleted the course or cancelled its build; stop its background work."""

try:
    import fitz
    # PyMuPDF API compatibility: the production server currently uses Python 3.6,
    # whose available binary wheel is PyMuPDF 1.16.x (legacy camelCase API).
    # Normalize the few APIs used by v241 so the same code also works with newer
    # PyMuPDF releases.
    if hasattr(fitz, 'Document'):
        if not hasattr(fitz.Document, 'get_toc') and hasattr(fitz.Document, 'getToC'):
            fitz.Document.get_toc = fitz.Document.getToC
        if not hasattr(fitz.Document, 'page_count') and hasattr(fitz.Document, 'pageCount'):
            fitz.Document.page_count = property(lambda self: self.pageCount)
    if hasattr(fitz, 'Page'):
        if not hasattr(fitz.Page, 'get_text') and hasattr(fitz.Page, 'getText'):
            fitz.Page.get_text = fitz.Page.getText
except Exception as e:
    fitz=None
    PDF_IMPORT_ERROR=str(e)
try:
    import numpy as np
except Exception:
    np=None

ROOT=Path(__file__).resolve().parent
COURSE_ROOT=ROOT/'course_workspaces'
STAGING_ROOT=COURSE_ROOT/'_staging'
COURSE_ROOT.mkdir(exist_ok=True)
STAGING_ROOT.mkdir(exist_ok=True)

EMBED_HOST=os.environ.get('VQ_REFERENCE_EMBED_HOST','127.0.0.1:11438').strip()
EMBED_BASE=EMBED_HOST.rstrip('/') if EMBED_HOST.startswith(('http://','https://')) else ('http://'+EMBED_HOST).rstrip('/')
EMBED_MODEL=os.environ.get('VQ_REFERENCE_EMBED_MODEL','bge-m3:latest').strip()
TOP_CANDIDATES=max(8,int(os.environ.get('VQ_COURSE_TOP_CANDIDATES','16')))
FINAL_TOP_K=max(4,int(os.environ.get('VQ_COURSE_FINAL_TOP_K','6')))
SOURCE_CANDIDATES=max(2,min(8,int(os.environ.get('VQ_COURSE_SOURCE_CANDIDATES','5'))))
SOURCE_MAX_FINAL=max(2,min(5,int(os.environ.get('VQ_COURSE_SOURCE_MAX_FINAL','3'))))
CONTEXT_BUDGET=max(4500,int(os.environ.get('VQ_COURSE_CONTEXT_CHARS','8500')))
QUESTION_TARGET=100
FIGURE_BATCH=10  # figure pages read before their passages are added to course Q&A
CHUNK_LIMIT=16*1024*1024  # one upload request; the page sends 8 MB
VIEW_SECONDS=600  # a course whose page polled its status this recently counts as open (background priority)
DORMANT_SECONDS=3*86400  # background work pauses on a course nobody has opened for this long; opening it resumes
QUESTION_GENERATION='exam-mcq-v5'
RERANK_BLEND=float(os.environ.get('VQ_COURSE_RERANK_BLEND','0.92'))
MIN_SCOPE_SCORE=float(os.environ.get('VQ_COURSE_MIN_SCOPE_SCORE','0.26'))
MAX_EMBED_CHARS=2400

# A visual lung simulator is appropriate only when the uploaded material
# actually teaches respiratory mechanics, gas exchange or V/Q physiology.
RESPIRATORY_TOPICS=(
    r'\bv\s*[/\\]\s*q\b', r'ventilation.perfusion', r'pulmonary physiology',
    r'respiratory physiology', r'respiratory mechanics', r'pulmonary ventilation',
    r'alveolar ventilation', r'alveolar gas',
    r'gas exchange', r'dead space', r'lung compliance', r'pulmonary shunt',
    r'pulmonary perfusion', r'pulmonary gas', r'肺泡通氣', r'通氣.灌流',
    r'呼吸生理', r'呼吸力學', r'肺通氣', r'氣體交換', r'肺順應性', r'肺泡氣體', r'肺血流', r'死腔', r'肺內分流'
)
RESPIRATORY_CONTEXT=(r'\balveol',r'\bpaco2\b',r'\bpao2\b',r'\bfio2\b',
                     r'\btidal volume\b',r'\brespiratory rate\b',r'肺泡',r'通氣量',r'氧分壓')

def _is_respiratory_physiology(text):
    sample=str(text or '').lower()
    hits=sum(bool(re.search(pattern,sample,re.I)) for pattern in RESPIRATORY_TOPICS)
    return hits>=2 or (hits>=1 and sum(bool(re.search(p,sample,re.I)) for p in RESPIRATORY_CONTEXT)>=2)


def _json_dump(path,obj):
    step_json(path,obj)

def _safe_id(s):
    s=re.sub(r'[^A-Za-z0-9._-]+','-',str(s or '').strip()).strip('-_.').lower()
    return s[:72] or 'course'

_PDF_PRIVATE=re.compile(r'[\ue000-\uf8ff\ufffd]')

def _clean_text(t):
    t=str(t or '').replace('\u200b','').replace('\xad','').replace('\ufeff','')
    # Never guess letters from a font's private-use glyph codes.
    # Preserve source hyphens, Unicode superscripts and unit symbols.
    t=re.sub(r'[ \t]+',' ',t)
    t=re.sub(r'\n{3,}','\n\n',t)
    return t.strip()

def _safe_source_text(t):
    return clean_course_text(_clean_text(t),'PDF source text',check_math=False)


def _readable_source(text):
    text=str(text or '')
    glyphs=len(_PDF_PRIVATE.findall(text))
    letters=len(re.findall(r'[A-Za-z\u3400-\u9fff]',text))
    return letters>=90 and glyphs<=max(2,int(letters*.005))

# A PDF outline is a navigation aid, not a course syllabus. Publishers often
# put Cover, IFC and legal pages at the same outline level as real chapters.
FRONT_MATTER=re.compile(r'^(?:cover|ifc|inside\s+(?:front|back)\s+cover|title(?:\s+page)?|half\s+title|copyright|dedication|contributors?|preface|foreword|acknowledg(?:e)?ments?|contents|table\s+of\s+contents|toc|about\s+(?:the\s+)?authors?|index|glossary|bibliography|references|abbreviations|list\s+of\s+(?:figures|tables)|封面|書名頁|版權頁|目錄|前言|序言|致謝|索引|參考文獻)(?:\s*[:.\-–].*)?$',re.I)
CHAPTER_HEADING=re.compile(r'^(?:chapter|chap\.?|ch\.?|unit|module)\s*\d+[a-z]?\b|^第\s*[一二三四五六七八九十百0-9]+\s*(?:章|單元)|^\d{1,3}\s*[.、:：\-]\s*\S',re.I)
PART_HEADING=re.compile(r'^(?:(?:part|section|volume)\s+(?:[ivxlcdm]+|\d+)|(?:第\s*[一二三四五六七八九十百0-9]+\s*篇))(?:\s*[:.\-–].*)?$',re.I)

def _course_title(metadata_title,filename):
    title=_clean_text(metadata_title or '')
    # Office "print to PDF" titles look like "Microsoft PowerPoint - deck.pptm".
    title=re.sub(r'^Microsoft\s+(?:Office\s+)?(?:PowerPoint|Word|Excel)\s*[-–:]\s*','',title,flags=re.I)
    title=re.sub(r'\.(?:pptx?|pptm|ppsx?|potx?|docx?|docm|xlsx?|key|odp|odt)\s*$','',title,flags=re.I).strip()
    if not title or len(title)>110 or re.search(r'[{}]|libgen|doi:|https?://|[/\\]',title,re.I):
        title=Path(filename).stem.split('{',1)[0].replace('_',' ')
        title=re.sub(r'\s*(?:libgen(?:\.li)?|\([^)]*\d{4}[^)]*\))\s*$','',title,flags=re.I)
    return title[:110].strip() or 'Uploaded Course'


# R10 course-subject detection: name the course from its content when the
# user leaves Course Name blank (a typed name always wins).
_SUBJECT_LABEL = re.compile(r'^\s*(?:the\s+)?(?:academic\s+)?(?:subject|course(?:\s+(?:subject|name|title))?|discipline|answer|學科|科目|課程(?:名稱)?)\s*(?:is)?\s*[:：]\s*', re.I)

def _clean_subject(raw):
    lines = [l for l in str(raw or '').splitlines() if l.strip()]
    if not lines:
        return ''
    s = _SUBJECT_LABEL.sub('', lines[0])
    s = re.sub(r'[*_`#"“”‘’「」『』\[\]]', '', s).strip()
    s = re.sub(r'[\s.。．、,，;；:：!！?？]+$', '', s)
    s = re.sub(r'\s+(?:course|textbook|class|課程|教科書)$', '', s, flags=re.I)
    s = _clean_text(s)
    if not s or len(s) > 60 or len(s.split()) > 6 or re.search(r'https?://|[{}<>|=]', s):
        return ''
    s = _subject_from_title(s) or ''
    if not s or re.search(r'\b(?:the|of|to|and|for|a|an|in|on|with)$', s, flags=re.I):
        return ''
    if re.fullmatch(r'(?:unknown|n/?a|none|uploaded course|course|textbook|introduction|無法判斷|未知)', s, flags=re.I):
        return ''
    return s[:1].upper() + s[1:] if re.match(r'[a-z]', s) else s

def _subject_from_title(title):
    t = _clean_text(title)
    if not t:
        return ''
    t = re.split(r'\s*[:：|]\s*', t)[0]
    t = re.sub(r'\s*[,(\[]?\s*(?:\d+\s*(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+(?:ed\.?|edition)\b.*$', '', t, flags=re.I)
    t = re.sub(r'^(?:(?:an?|the)\s+)?(?:fundamentals|principles|essentials|basics|elements|foundations|introduction|concepts|handbook|textbook|manual)\s+(?:of|to|in)\s+', '', t, flags=re.I)
    t = re.sub(r'\s+for\s+.+$', '', t, flags=re.I)
    t = t.strip(' -–,')
    return t if 0 < len(t) <= 60 and len(t.split()) <= 6 else ''

def _detect_course_subject(book_titles, unit_titles, unit_texts, model_key, model_call):
    heads = [_clean_text(t)[:120] for t in unit_titles if _clean_text(t)][:40]
    texts = [x for x in unit_texts if _clean_text(x)]
    picks = [texts[int(i * (len(texts) - 1) / 3)] for i in range(4)] if len(texts) > 4 else texts
    excerpts = [_clean_text(x)[:700] for x in picks]
    system = ('You identify the academic subject of uploaded course material. '
              'Reply with the subject name only, nothing else.')
    user = ('Book or file titles: ' + ('; '.join(t for t in book_titles if t)[:400] or '(none)') +
            '\nChapter titles:\n' + '\n'.join('- ' + h for h in heads) +
            '\n\nExcerpts:\n' + '\n---\n'.join(excerpts) +
            '\n\nWhat academic subject does this course material teach? Reply with only the subject name, '
            '1 to 4 words, in the main language of the material, for example: Statistics, Pharmacology, '
            'Biochemistry, Respiratory Physiology, 統計學, 藥理學. Do not include the book title, edition, '
            'author, publisher, intended audience, or words such as course, textbook or introduction.')
    return _clean_subject(model_call(model_key, system, user, 24))

def _resolve_course_name(course_name, stage, units, unit_texts, model_key, model_call, notify=None):
    typed = _clean_text(course_name)
    if typed:
        return typed[:110], 'user'
    titles = [_clean_text(s.get('title') or '') for s in (stage.get('sources') or [])]
    if notify:
        try:
            notify('Identifying the course subject from the PDF content')
        except Exception:
            pass
    try:
        subject = _detect_course_subject(titles, [u.get('title') or '' for u in units],
                                         [unit_texts.get(u['id'], '') for u in units], model_key, model_call)
    except Exception:
        subject = ''
    if subject:
        return subject, 'content'
    fallback = _subject_from_title(titles[0] if titles else '') or _clean_text(stage.get('course_name') or '')
    return (fallback or 'Uploaded Course'), 'title'


# Generic outline names that say nothing about the content, e.g. PowerPoint's
# default section names exported as PDF bookmarks.
GENERIC_UNIT_TITLE=re.compile(r'^(?:未命名(?:的)?(?:章節|區段|節|段落|部分)?|預設(?:章節|區段)|默认节|未命名节|无标题节|無標題(?:章節)?|untitled(?:\s+section)?|default\s+section|new\s+section|section\s*\d*|slide\s*\d+|投影片\s*\d+|幻灯片\s*\d+|page\s*\d+|第\s*\d+\s*頁)$',re.I)
# Short slides that end or organize a talk are not topics.
NON_TOPIC_SLIDE=re.compile(r'^(?:thank\s*you!?|thanks!?|questions?\??|any\s+questions\??|q\s*&\s*a|the\s+end|end|references?|bibliography|acknowledg(?:e)?ments?|outline|agenda|contents?|overview|summary|謝謝(?:聆聽|大家|觀看)?|感謝(?:聆聽|大家)?|問題(?:與|和)?討論|提問|參考(?:文獻|資料)|大綱|目錄|總結|摘要)[!！.。]?$',re.I)
UNIT_SPLIT_PAGES=30      # slide-deck units at least this long are split at section-title slides
UNIT_MIN_PAGES=3         # a split never leaves a part shorter than this
HEADING_SCAN_SECONDS=25  # page-heading scan budget during upload inspection

def _word_count(text):
    text=str(text or '')
    cjk=len(re.findall(r'[㐀-鿿豈-﫿]',text))
    latin=len(re.findall(r'[A-Za-z0-9]+(?:[\'’.-][A-Za-z0-9]+)*',re.sub(r'[㐀-鿿豈-﫿]',' ',text)))
    return latin+(cjk+1)//2

def _page_heading(page):
    """The largest-font line(s) of a page: its slide title or heading."""
    flags=getattr(fitz,'TEXT_PRESERVE_LIGATURES',1)|getattr(fitz,'TEXT_PRESERVE_WHITESPACE',2)
    try:d=page.get_text('dict',flags=flags)
    except TypeError:d=page.get_text('dict')
    except Exception:return None
    lines=[];words=0
    for bi,block in enumerate(d.get('blocks') or []):
        if block.get('type',0)!=0:continue
        for line in block.get('lines') or []:
            spans=[x for x in line.get('spans') or [] if str(x.get('text') or '').strip()]
            if not spans:continue
            text=_clean_text(''.join(str(x.get('text') or '') for x in spans))
            if not text or re.fullmatch(r'[\d\W_]+',text):continue  # page numbers, bullets
            words+=_word_count(text)
            box=line.get('bbox') or spans[0].get('bbox') or (0,0,0,0)
            lines.append({'text':text,'size':max(float(x.get('size') or 0) for x in spans),'block':bi,'y0':float(box[1]),'y1':float(box[3])})
    if not lines:return None
    top=max(x['size'] for x in lines)
    first=next(x for x in lines if x['size']>=top-0.01)
    title=[x for x in lines if x['block']==first['block'] and x['size']>=top*0.9]
    title.sort(key=lambda x:x['y0'])
    text=_clean_text(' '.join(x['text'] for x in title))
    text=re.sub(r'\s+',' ',text).strip(' -–—:|•·')[:90].strip()
    height=float(page.rect.height) or 1.0
    centre=(min(x['y0'] for x in title)+max(x['y1'] for x in title))/2.0/height
    return {'title':text,'size':top,'centre':centre,'words':words,'height':height}

def _usable_title(text):
    t=_clean_text(text)
    return bool(t) and 2<=len(t)<=90 and not GENERIC_UNIT_TITLE.match(t) and not NON_TOPIC_SLIDE.match(t) and not re.fullmatch(r'[\d\W_]+',t)

def _is_section_slide(info):
    """A section-title slide: a large, vertically centred title and little else."""
    if not info or not _usable_title(info['title']):return False
    return info['words']<=20 and 0.22<=info['centre']<=0.78 and info['size']>=20*info['height']/540.0

def _is_slide_deck(doc):
    n=doc.page_count;step=max(1,n//24);wide=total=0
    for i in range(0,n,step):
        r=doc[i].rect;total+=1
        if r.width>r.height*1.05:wide+=1
    return total>0 and wide>=0.7*total

def _course_chapters(doc):
    """Teaching units as (title, start_page, end_page), in page order.

    The PDF outline decides the units. Its entries are put in page order (an
    outline listed out of page order gave wrong page ranges), and generic or
    repeated names are replaced or told apart. Slide decks also use their
    section-title slides: they name the slides before the first bookmark and
    split very long units, or form the units when there is no outline."""
    total=doc.page_count
    rows=[]
    for row in doc.get_toc(simple=True) or []:
        try:level,title,page=int(row[0]),_clean_text(row[1]),int(row[2])
        except (TypeError,ValueError,IndexError):continue
        if title and 1<=page<=total and not FRONT_MATTER.match(title) and not PART_HEADING.match(title):
            rows.append((level,title,page))
    explicit=[r for r in rows if CHAPTER_HEADING.match(r[1])]
    if explicit:
        level=min(r[0] for r in explicit)
        chosen=[r for r in explicit if r[0]==level]
    else:
        # Keep the broadest usable outline level; a lone book-level bookmark
        # above several real sections should not swallow the entire textbook.
        levels=sorted(set(r[0] for r in rows))
        level=next((l for l in levels if len([r for r in rows if r[0]==l])>=2),levels[0] if levels else None)
        chosen=[r for r in rows if r[0]==level]
    starts=[];seen=set()
    for _,title,page in sorted(chosen,key=lambda r:r[2]):  # stable: outline order within a page
        if page in seen:continue
        seen.add(page);starts.append([title,page])
    headings={};slides=_is_slide_deck(doc);sections=[]
    def heading(page):
        if page not in headings:headings[page]=_page_heading(doc[page-1])
        return headings[page]
    if slides:
        began=time.time()
        for page in range(1,total+1):
            if time.time()-began>HEADING_SCAN_SECONDS:sections=[];break
            info=heading(page)
            if _is_section_slide(info):sections.append([info['title'],page])
    review=False
    if not starts:
        if len(sections)>=2:starts=[list(x) for x in sections]
        else:return [('Entire PDF — review or upload separate chapter PDFs',1,total)]
    if slides and starts[0][1]-1>=UNIT_MIN_PAGES:
        # Slides before the first bookmark are part of the talk, not book front matter.
        first=heading(1)
        starts.insert(0,[first['title'] if first and _usable_title(first['title']) else '',1])
    units=[]
    for i,(title,start) in enumerate(starts):
        end=starts[i+1][1]-1 if i+1<len(starts) else total
        units.append([title,start,max(start,end)])
    if slides and sections:
        split=[]
        for title,start,end in units:
            cuts=[(t,p) for t,p in sections if start<p<=end] if end-start+1>=UNIT_SPLIT_PAGES else []
            parts=[[title,start]]
            for t,p in cuts:
                if p-parts[-1][1]>=UNIT_MIN_PAGES and end-p+1>=UNIT_MIN_PAGES:parts.append([t,p])
            for j,(t,p) in enumerate(parts):
                split.append([t,p,parts[j+1][1]-1 if j+1<len(parts) else end])
        units=split
    for unit in units:
        if not _usable_title(unit[0]):
            info=heading(unit[1])
            unit[0]=info['title'] if info and _usable_title(info['title']) else 'Pages %d–%d'%(unit[1],unit[2])
    merged=[]
    for unit in units:
        if merged and merged[-1][0].casefold()==unit[0].casefold():merged[-1][2]=unit[2]   # one topic split by a repeated bookmark
        else:merged.append(unit)
    counts=Counter(u[0].casefold() for u in merged);used=Counter()
    for unit in merged:
        key=unit[0].casefold()
        if counts[key]>1:
            used[key]+=1
            if used[key]>1:unit[0]='%s (%d)'%(unit[0],used[key])
    return [(t,a,b) for t,a,b in merged]

def _word_chunks(text,max_words=420,overlap=70):
    from math_integrity import MATH_PATTERN
    text=_clean_text(text)
    if not text:return []
    out=[];start=0;cap=MAX_EMBED_CHARS
    math_ranges=[(m.start(),m.end()) for m in MATH_PATTERN.finditer(text)]
    while start<len(text):
        stop=min(len(text),start+cap)
        words=list(re.finditer(r'\S+',text[start:stop]))
        if len(words)>max_words:stop=start+words[max_words].start()
        if stop<len(text):
            bounds=[m.end() for m in re.finditer(r'\n\n|[。！？.!?]\s|\n|\s',text[start:stop]) if m.end()>max(100,(stop-start)//2)]
            if bounds:stop=start+bounds[-1]
            for left,right in math_ranges:
                if left<stop<right:stop=left if left>start else right
        value=text[start:stop].strip()
        if value:out.append(value)
        if stop>=len(text):break
        # Overlap a complete trailing line/paragraph when it fits; never drop
        # the end of a short page or insert artificial word spaces into CJK.
        shared=min(350,max(0,int(overlap))*4,(stop-start)//4)
        next_start=max(start+1,stop-shared)
        newline=text.find('\n',next_start,stop)
        if newline>=0:next_start=newline+1
        for left,right in math_ranges:
            if left<next_start<right:next_start=right
        start=min(next_start,stop)
    return out


def _sentence_windows(text,target_chars=900,max_chars=1350):
    raw=' '.join(str(text or '').split())
    if not raw:return []
    parts=[x.strip() for x in re.split(r'(?<=[.!?。！？])\s+(?=[A-Z0-9\u3400-\u9fff(])',raw) if x.strip()]
    if len(parts)<=2:return [raw[:max_chars]]
    windows=[];cur=[];n=0
    for sent in parts:
        if cur and n+len(sent)+1>target_chars:
            w=' '.join(cur).strip()
            if w:windows.append(w[:max_chars])
            cur=cur[-1:];n=sum(len(x)+1 for x in cur)
        cur.append(sent);n+=len(sent)+1
    if cur:
        w=' '.join(cur).strip()
        if w:windows.append(w[:max_chars])
    seen=set();out=[]
    for w in windows:
        k=' '.join(w.lower().split())
        if k not in seen:seen.add(k);out.append(w)
    return out

class EmbeddingHTTPError(RuntimeError):
    def __init__(self,status,detail):
        self.status=status
        self.detail=detail
        super(EmbeddingHTTPError,self).__init__('BGE-M3 embedding HTTP %s: %s'%(status,detail))


def _url_json(url,payload=None,timeout=240):
    data=None;headers={}
    if payload is not None:
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8');headers['Content-Type']='application/json'
    req=urllib.request.Request(url,data=data,headers=headers,method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req,timeout=float(timeout)) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raw=e.read(2048).decode('utf-8',errors='replace')
        try:detail=str(json.loads(raw).get('error') or raw)
        except (ValueError,AttributeError):detail=raw
        raise EmbeddingHTTPError(e.code,detail[:350] or 'No error detail returned by Ollama')

def _normalize(v):
    vals=[float(x) for x in v]
    n=math.sqrt(sum(x*x for x in vals)) or 1.0
    return [x/n for x in vals]

def _embed_input(value,max_chars=MAX_EMBED_CHARS):
    s=unicodedata.normalize('NFKC',str(value or ''))
    s=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffd]',' ',s)
    return ' '.join(s.split())[:max_chars].strip()


def _embed(texts,timeout=240,minimal=False):
    single=isinstance(texts,str);items=[texts] if single else list(texts)
    if not items:return []
    items=[_embed_input(x) for x in items]
    if any(not x for x in items):raise ValueError('A selected chapter contains no embeddable text.')
    payload={'model':EMBED_MODEL,'input':items[0] if single else items}
    if not minimal:payload.update({'truncate':True,'keep_alive':'12h'})
    payload['options']={'num_thread':1}
    def compute():
        def request():
            with cpu_slot():return _url_json(EMBED_BASE+'/api/embed',payload,timeout=timeout)
        d=retry_transient(request)  # the CPU slot is not held while waiting
        emb=d.get('embeddings') or []
        if single and emb and isinstance(emb[0],(int,float)):emb=[emb]
        if len(emb)!=len(items):raise RuntimeError('Embedding backend returned %s vectors for %s inputs'%(len(emb),len(items)))
        return [_normalize(v) for v in emb]
    signature=model_signature(EMBED_BASE,EMBED_MODEL,lambda:_url_json(EMBED_BASE+'/api/show',{'model':EMBED_MODEL},timeout=4))
    return completed_call({'embedding_endpoint':EMBED_BASE,'model_signature':signature,'payload':payload},compute)


def _embed_resilient(items,timeout=300):
    items=list(items)
    if not items:return []
    try:return _embed(items,timeout=timeout)
    except EmbeddingHTTPError as e:
        if e.status not in (400,413):raise
        if len(items)==1:
            last=e
            for limit in (1200,512,160):
                try:return _embed([_embed_input(items[0],limit)],timeout=timeout,minimal=True)
                except EmbeddingHTTPError as retry:
                    if retry.status not in (400,413):raise
                    last=retry
            raise RuntimeError('Embedding service rejected one cleaned PDF passage (model %s, host %s): %s'%(EMBED_MODEL,EMBED_BASE,last.detail))
        m=len(items)//2
        return _embed_resilient(items[:m],timeout)+_embed_resilient(items[m:],timeout)

def _pack(v):return struct.pack('<%df'%len(v),*v)
def _unpack(blob):
    a=array('f');a.frombytes(blob)
    if os.sys.byteorder!='little':a.byteswap()
    return a

def _dot(a,b):
    if np is not None:
        return float(np.dot(np.asarray(a,dtype=np.float32),np.asarray(b,dtype=np.float32)))
    return float(sum(x*y for x,y in zip(a,b)))

def _same_language_hint(text):
    s=str(text or '')
    zh=len(re.findall(r'[\u3400-\u9fff]',s)); en=len(re.findall(r'[A-Za-z]',s))
    return 'Traditional Chinese' if zh>max(50,en//3) else 'English'

# Source-backed choices are assembled from distinct statements in the selected unit.
# The bank is complete before a quiz starts, so no answer model can stall a draw.
_QUESTION_STOP=set(('a an and are as at be been being because but by can could did do does doing done for from had has have having he her hers him his how i if in into is it its may might more most must no nor not of on one or our ours she should so than that the their theirs them then there these they this those through to too under up very was we were what when where which while who why will with would you your yours according also between both during each either every first following further however including itself many other same several some such these those using use used'.split()))
_QUESTION_STOP.update(('chapter figure table copyright edition elsevier respiratory course material unit principles introduction summary review learning objectives key points conversely therefore moreover nevertheless otherwise traditionally generally usually commonly respectively approximately'.split()))
_QUESTION_BAD_END=set(('call calls called calling mean means meant meaning change changes changed changing cause causes caused causing explain explains explained explaining preserve preserves preserved preserving regulate regulates regulated regulating increase increases increased increasing decrease decreases decreased decreasing reduce reduces reduced reducing move moves moved moving make makes made making perform performs performed performing result results resulted resulting lead leads led leading use uses used using include includes included including provide provides provided providing require requires required requiring become becomes became becoming remain remains remained remaining'.split()))

def _stable_number(value):
    return int(hashlib.sha1(str(value).encode('utf-8')).hexdigest()[:12],16)

def _question_sentences(chunks):
    rows=[];seen=set()
    for chunk in chunks:
        raw=_clean_text(chunk.get('text') or '').replace('\r','\n')
        parts=re.split(r'(?<=[.!?。！？])\s+|\n+',raw)
        for part in parts:
            sent=' '.join(_clean_text(part).split()).strip(' \t-–—•')
            latin=len(re.findall(r'[A-Za-z]',sent));han=len(re.findall(r'[\u3400-\u9fff]',sent))
            if len(sent)<32 or len(sent)>360 or latin+han<22:continue
            if _PDF_PRIVATE.search(sent):continue
            # The PDF text layer can glue review keys, printer marks and page
            # furniture to a valid sentence. None belongs in a student quiz.
            if re.search(r'appendix\s+[a-z]|study\s+and\s+review|\.indd\b|\b(?:isbn|copyright|all rights reserved|answer found in)\b|\b\w{3,}\d{3,}_ch\d+|\bchapter\s+\d+\b',sent,re.I):continue
            if re.match(r'^\s*(?:\([a-d]\)|[a-d][.)]|\d{1,3}[.)])\s',sent,re.I):continue
            if latin>20 and len(re.findall(r'[A-Z]',sent))/float(latin)>.65:continue
            if not re.search(r'[.!?。！？][\]”"\']?$',sent):continue
            if re.search(r'\b(?:figure|fig\.?|table|illustration|key\s*point|references)\s*\d',sent,re.I):continue
            if re.match(r'^\d{1,3}\.\d{1,3}\s',sent):continue
            if re.search(r'\b(?:KEY\s*POINT|DOUGLAS\s+S\.|ISBN|Copyright)\b',sent,re.I):continue
            if latin and len(re.findall(r"[A-Za-z][A-Za-z0-9'’/-]*",sent))<6:continue
            key=re.sub(r'\W+','',sent.lower())[:260]
            if not key or key in seen:continue
            seen.add(key);rows.append({'text':sent,'evidence_chunk_id':chunk['id']})
    return rows

def _candidate_terms(sentence,frequencies,max_terms=5,prefer_phrases=False):
    found=[];occupied=set()
    def add(start,end,text,kind,bonus=0):
        value=' '.join(str(text).split()).strip(' ,.;:()[]{}')
        low=value.lower()
        if len(value)<2 or len(value)>64 or low in _QUESTION_STOP:return
        marker=(start,end,low)
        if marker in occupied:return
        occupied.add(marker)
        rarity=10.0/max(1,frequencies.get(low,1))
        found.append({'start':start,'end':end,'value':value,'kind':kind,'score':bonus+min(18,len(value))+rarity})
    for m in re.finditer(r'(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:%|mg|mcg|µg|g|kg|mL|ml|L|mmHg|cmH2O|s|sec(?:onds?)?|min(?:utes?)?|hours?)?',sentence,re.I):
        add(m.start(),m.end(),m.group(0),'number',18)
    words=list(re.finditer(r"\b[A-Za-z][A-Za-z0-9α-ωΑ-Ω'’/-]{1,}\b",sentence))
    content=[]
    for m in words:
        value=m.group(0);low=value.lower()
        if low in _QUESTION_STOP or (len(value)<4 and not value.isupper()):continue
        if low in _QUESTION_BAD_END or low.endswith('ly'):continue
        if value.isupper() and 2<=len(value)<=10:kind='acronym'
        elif low.endswith(('ing','ed')):kind='verb'
        elif low.endswith(('tion','sion','ment','ness','ity','ance','ence','ism','osis','emia','ase','ide','ium','ology','ship','age','ure')):kind='noun'
        elif low.endswith(('al','ic','ive','ous','able','ible','ary','ory','ant','ent','less','ful')):kind='adjective'
        else:kind='term'
        add(m.start(),m.end(),value,kind,10 if kind=='acronym' else 0);content.append(m)
    # Keep at most one conservative multiword expression per sentence.  The
    # source span must be plain adjacent words (no skipped punctuation), and a
    # verb-like ending is rejected.  This avoids awkward blanks such as
    # "vessels and reduced" while retaining terms such as "alveolar pressure".
    for size in (3,2):
        for i in range(0,max(0,len(words)-size+1)):
            group=words[i:i+size];tokens=[x.group(0).lower() for x in group]
            if tokens[0] in _QUESTION_STOP or tokens[-1] in _QUESTION_STOP or any(x in _QUESTION_BAD_END for x in tokens):continue
            if any(x in _QUESTION_STOP and x not in ('of','and','to') for x in tokens[1:-1]):continue
            if tokens[-1] in _QUESTION_BAD_END or tokens[-1].endswith(('ing','ed','ly')):continue
            if group[-1].end()-group[0].start()>58:continue
            phrase=sentence[group[0].start():group[-1].end()]
            if not re.fullmatch(r"[A-Za-z0-9α-ωΑ-Ω'’/-]+(?:\s+[A-Za-z0-9α-ωΑ-Ω'’/-]+){%d}"%(size-1),phrase):continue
            add(group[0].start(),group[-1].end(),phrase,'phrase',13+size)
    # Chinese PDFs do not expose word boundaries. Short source substrings still
    # make exact, auditable cloze answers.
    for run in re.finditer(r'[\u3400-\u9fff]{4,}',sentence):
        text=run.group(0)
        for size in (4,3,2):
            for pos in range(0,max(0,len(text)-size+1),size):
                add(run.start()+pos,run.start()+pos+size,text[pos:pos+size],'han',8+size)
    found.sort(key=lambda x:(-x['score'],x['start'],x['value'].lower()))
    selected=[]
    # Single terms and measurements make the most readable deterministic
    # cloze items.  Reserve one slot for a carefully filtered compound term.
    ordered=([x for x in found if x['kind']=='phrase']+[x for x in found if x['kind']!='phrase']) if prefer_phrases else [x for x in found if x['kind']!='phrase']+[x for x in found if x['kind']=='phrase']
    phrase_count=0
    for item in ordered:
        if item['kind']=='phrase' and phrase_count>=(2 if prefer_phrases else 1):continue
        if any(not (item['end']<=x['start'] or item['start']>=x['end']) for x in selected):continue
        selected.append(item)
        if item['kind']=='phrase':phrase_count+=1
        if len(selected)>=max_terms:break
    return selected

def _fast_question_bank(unit,chunks,target=QUESTION_TARGET,max_terms=5):
    sentences=_question_sentences(chunks)
    if not sentences:return []
    tokens=[]
    for row in sentences:
        tokens.extend(x.lower() for x in re.findall(r"\b[A-Za-z][A-Za-z0-9α-ωΑ-Ω'’/-]{2,}\b",row['text']))
    frequencies=Counter(tokens)
    rounds={}
    for si,row in enumerate(sentences):
        terms=_candidate_terms(row['text'],frequencies,max_terms=max_terms)
        for rank,term in enumerate(terms):
            rounds.setdefault(rank,[]).append({'source_concept':term['value'],'source_statement':row['text'],'evidence_chunk_id':row['evidence_chunk_id']})
    # One concept from each source sentence before returning to the same
    # sentence. This is linear in the extracted text and never duplicates a
    # fact merely to inflate the advertised count.
    generated=[];seen=set()
    for rank in sorted(rounds):
        pool=rounds[rank]
        pool.sort(key=lambda x:_stable_number(unit['id']+'|'+x['source_statement']+'|'+x['source_concept']))
        for item in pool:
            key=(item['source_statement'].casefold(),item['source_concept'].casefold())
            if key in seen:continue
            seen.add(key);item['id']='%s_q%03d'%(unit['id'],len(generated)+1)
            item['generation_method']='scenario-seed-v1'
            generated.append(item)
            if len(generated)>=target:return generated
    return generated

def _ready_question_bank(unit,chunks,target=QUESTION_TARGET):
    """Use an intact textbook sentence, never a guessed subject/object relation.

    Choices are exact, bounded terms from the same unit and grammatical class.
    The task asks for the source wording: another term may be true elsewhere,
    but only one completes this specific, quoted sentence.
    """
    rows=[];frequencies=Counter()
    for row in _question_sentences(chunks):
        fact=row['text']
        # An isolated clause after a page break or a context-only transition
        # cannot be made into a stand-alone question by adding a generic stem.
        if re.match(r'^(?:there\b|as a result\b|however\b|therefore\b|which\b|this\b|these\b|those\b)',fact,re.I):continue
        if len(re.findall(r'[A-Za-z]',fact))<35 or re.search(r'[\u3400-\u9fff]',fact):continue
        if not re.match(r'^[A-Za-z]',fact):continue
        rows.append(row)
        frequencies.update(x.lower() for x in re.findall(r"\b[A-Za-z][A-Za-z0-9α-ωΑ-Ω'’/-]{2,}\b",fact))
    candidates=[];pool={}
    for row in rows:
        fact=row['text']
        for term in _candidate_terms(fact,frequencies,max_terms=12,prefer_phrases=True):
            kind=term['kind'];answer=term['value']
            if kind in ('verb','adjective','han') or re.search(r'[\u3400-\u9fff]',answer):continue
            if kind=='number' and len(re.findall(r'\d+(?:\.\d+)?',fact))>1:continue
            if answer.casefold() in ('the','there','which','that','this','little','information','evidence','because'):continue
            if re.search(r'\b(?:and|or|the|a|an|of|to|for)\b',answer,re.I) and kind!='phrase':continue
            before=fact[:term['start']];after=fact[term['end']:]
            if len(after.strip())<8:continue
            if (before and not before[-1].isspace()) or (after[0] not in ' .,;:!?)]' and not after[0].isspace()):continue
            if len(answer)>48 or not re.search(r'[A-Za-z]',answer):continue
            stem='Complete this statement from the course material:\n'+before+'____'+after
            # The source wording must contain exactly one target span.
            if fact.casefold().count(answer.casefold())!=1:continue
            candidate={'kind':kind,'answer':answer,'stem':stem,'fact':fact,'chunk':row['evidence_chunk_id']}
            candidates.append(candidate);pool.setdefault(kind,set()).add(answer)
    result=[];seen=set()
    candidates.sort(key=lambda c:(_stable_number(unit['id']+'|'+c['fact']),_stable_number(c['answer'])))
    for card in candidates:
        fact=card['fact'];answer=card['answer'];kind=card['kind']
        key=(fact.casefold(),answer.casefold())
        if key in seen:continue
        seen.add(key)
        options=[s for s in pool[kind] if s.casefold()!=answer.casefold() and s.casefold() not in fact.casefold() and answer.casefold() not in s.casefold() and s.casefold() not in answer.casefold()]
        options.sort(key=lambda s:(abs(len(s)-len(answer)),_stable_number(card['stem']+'|'+s)))
        if len(options)<3:continue
        slot=_stable_number('answer|'+card['stem'])%4
        choices=options[:3];choices.insert(slot,answer)
        if len(set(s.casefold() for s in choices))!=4:continue
        qid='%s_cloze%03d'%(unit['id'],len(result)+1);seed_id=qid+'_seed'
        explanation='The course material states: '+fact
        question={'id':qid,'source_seed_id':seed_id,'stem':card['stem'],'choices':choices,'answer':slot,'explanation':explanation,'evidence_statement':fact,'evidence_chunk_id':card['chunk'],'generation_method':QUESTION_GENERATION}
        if _PDF_PRIVATE.search(' '.join([question['stem'],explanation]+choices)):continue
        result.append({'id':seed_id,'source_concept':answer,'source_statement':fact,'evidence_chunk_id':card['chunk'],'generation_method':QUESTION_GENERATION,'question':question})
        if len(result)>=target:break
    if len(result)<target:
        raise RuntimeError('Unit "%s" has only %s/%s complete English source questions. Upload a longer OCR-readable English chapter; incomplete sentences and unrelated choice fragments are excluded.'%(unit['title'],len(result),target))
    return result

_RELATION=re.compile(r'^(?P<subject>[A-Za-z\u3400-\u9fff][^.!?。！？]{3,100}?)\s+(?:(?:can|may|will)\s+)?(?P<verb>is\s+(?:partially\s+)?determined\s+by|are\s+(?:partially\s+)?determined\s+by|consists\s+of|results\s+in|leads\s+to|depends\s+on|is\s+associated\s+with|contains?|includes?|has|have|increases?|decreases?|reduces?|inhibits?|stimulates?|regulates?|changes?|causes?|affects?|produces?|describes?|determines?|allows?|involves?|requires?|contributes?|delivers?|binds?|blocks?|prevents?|maintains?|is|are)\s+(?P<object>[^.!?。！？]{4,230})[.!?。！？]$',re.I)
_RELATION_ZH=re.compile(r'^(?P<subject>[\u3400-\u9fff]{2,35}?)(?P<verb>包含|含有|增加|降低|減少|抑制|促進|調節|影響|導致|需要|位於|是)(?P<object>[^。！？]{4,85})[。！？]$')
_QUESTION_FURNITURE=re.compile(r'\b(?:appendix|figure|table|chapter|study and review|answer found in|copyright|isbn)\b|\.indd\b|\b\w{3,}\d{3,}_ch\d+',re.I)

def _case_phrase(value):
    value=' '.join(_clean_text(value).split()).strip(' ,.;:()[]{}-–—')
    value=re.sub(r'^(?:for example|for instance|in contrast|however|therefore|the|a|an|many different|various)\s+','',value,flags=re.I)
    return value.strip(' ,.;:()[]{}-–—')

def _case_part(value,limit=95):
    value=_case_phrase(value)
    if not value or len(value)>limit or _QUESTION_FURNITURE.search(value) or _PDF_PRIVATE.search(value):return ''
    if re.search(r'\b(?:and|or|of|to|by|the|a|an|which|that|because)\s*$',value,re.I):return ''
    return value

def _relationship_cards(statement,unit_title):
    """Turn an explicit textbook relation into a short case and short answers."""
    if _QUESTION_FURNITURE.search(statement):return []
    clean=re.sub(r'^(?:for example|for instance|in contrast|however|therefore),?\s+','',statement.strip(),flags=re.I)
    m=_RELATION.match(clean) or _RELATION_ZH.match(clean)
    if not m:return []
    subject=_case_part(m.group('subject'),70)
    obj=m.group('object');cause=''
    if re.search(r'\b(?:does not|do not|cannot|is not|are not)\s*$',subject,re.I) or subject.endswith(('不','未')):return []
    for link in (' because ',' by ',' that ',' which ','因為','透過','藉由'):
        pos=obj.lower().find(link)
        if pos>=0:
            cause=_case_part(obj[pos+len(link):],90);obj=obj[:pos]
            break
    outcome=_case_part(obj,85)
    if not subject or not outcome or subject.casefold()==outcome.casefold():return []
    if len(subject.split())>12 or len(outcome.split())>14:return []
    verb=m.group('verb').lower()
    clinical=bool(re.search(r'patient|drug|lung|pulmonary|respirat|blood|disease|pharmac|medic|alveol|病人|藥物|肺|血液',statement+' '+unit_title,re.I))
    setting='臨床實習生在照護討論中' if clinical else '學生在實驗與實務案例討論中'
    cards=[]
    if verb in ('is','are','是','describes','describe'):
        cards.append(('outcome','%s需要向同學解釋 %s。依本單元教材，哪一項描述最恰當？'%(setting,subject),outcome))
        cards.append(('subject','%s讀到 %s 的描述。這個描述對應教材中的哪一項？'%(setting,outcome),subject))
    elif verb.startswith(('contain','include')) or verb in ('has','have','consists of','包含','含有'):
        cards.append(('outcome','%s分析 %s 的組成，準備解釋觀察結果。依教材，應找到哪一項成分？'%(setting,subject),outcome))
        if cause:
            focus=_case_part(cause.rsplit('such as',1)[-1],50) if 'such as' in cause.lower() else ''
            if focus:
                cards.append(('outcome','%s觀察 %s 的變化，想找出 %s 中參與調控的成分。根據教材，應選哪一項？'%(setting,focus,subject),outcome))
    else:
        direction='受抑制' if verb.startswith(('inhib','block','prevent')) or verb in ('抑制',) else '降低' if verb.startswith(('decrease','reduc')) or verb in ('降低','減少') else '增加' if verb.startswith(('increase','stimulat')) or verb in ('增加','促進') else '受到影響'
        cards.append(('outcome','%s評估 %s 的作用。依教材，哪一項會%s？'%(setting,subject,direction),outcome))
        cards.append(('subject','%s發現 %s %s。教材提到哪個因素可能直接造成這個變化？'%(setting,outcome,direction),subject))
        if cause:
            cards.append(('mechanism','%s要解釋 %s 對 %s 的作用。教材指出的機轉是哪一項？'%(setting,subject,outcome),cause))
            cards.append(('outcome','%s研究 %s 透過 %s 發揮作用。預期哪一項會%s？'%(setting,subject,cause,direction),outcome))
    return [(kind,stem,answer) for kind,stem,answer in cards if len(stem)<=210 and answer.casefold() not in stem.casefold() and not _PDF_PRIVATE.search(stem)]

def _lecture_evidence(unit,chunks,max_chars=6200):
    rows=[str(c.get('text') or '').strip() for c in chunks if str(c.get('text') or '').strip() and not text_issues(c.get('text'))]
    if sum(len(x)+2 for x in rows)<=max_chars:return '\n\n'.join(rows)
    # Balanced chapter coverage using complete bounded passages.
    picked=[];used=0
    order=list(range(len(rows)))
    if len(order)>8:order=sorted(set(int(round(i*(len(rows)-1)/7.0)) for i in range(8)))
    for i in order:
        value=rows[i]
        if used+len(value)+2<=max_chars:picked.append(value);used+=len(value)+2
    return '\n\n'.join(picked)


class RejectedLecture(RuntimeError):pass
class MissingLectureSource(RejectedLecture):pass


def _unread_pages(quality,retryable=False):
    """Pages with no readable text. retryable: not yet tried with every reader
    (a page the vision model could not verify in any way, such as a blank or a
    purely decorative page, is not read again with the same model)."""
    rows=[w for w in (quality or {}).get('warnings') or [] if w.get('status')=='needs_review']
    if retryable:rows=[w for w in rows if not (w.get('vision') or {}).get('exhausted')]
    return rows


def _review_summary(qualities):
    """Pages read by OCR are usable; only pages with no readable text count."""
    unread=sum(len(_unread_pages(q)) for q in qualities.values())
    retryable=sum(len(_unread_pages(q,True)) for q in qualities.values())
    return {'required':bool(retryable),'units':qualities,'unread_pages':unread,'retryable_pages':retryable}


def _needs_reread(unit,quality):
    return bool(_unread_pages(quality,True))

LECTURE_MAX_TOKENS=int(os.environ.get('VQ_LECTURE_MAX_TOKENS','2000'))
PREPARATION_RETRY_SECONDS=max(30,int(os.environ.get('VQ_PREPARATION_RETRY_SEC','600')))
PREPARATION_RETRIES=int(os.environ.get('VQ_PREPARATION_RETRIES','3'))
# When a draft is rejected, the next attempt changes how it is asked rather
# than repeating the same request: a shorter evenly spaced excerpt and a
# tighter five-paragraph structure (still within the five-to-seven paragraphs).
LECTURE_VARIANTS=(
    (6200,'請寫 5 至 7 段繁體中文講稿，先定義核心概念，再用教材中的例子解釋機轉，最後歸納。'),
    (4600,'請寫 5 段繁體中文講稿，每段 3 至 5 句：先定義核心概念，再用教材中的例子解釋機轉，最後歸納。只寫教材明確支持的內容，全部使用繁體中文。'),
    (3400,'請寫 5 段精簡的繁體中文講稿，每段 2 至 4 句，只解釋下面摘錄中明確寫出的概念與機轉，最後一段歸納。不確定的內容一律省略。'),
)

def _lecture_retryable(error):
    return isinstance(error,(RejectedLecture,GroundingError)) or 'did not finish' in str(error)

def _chinese_lecture(unit,chunks,model_key,model_call,course_name='',variant=0):
    max_chars,instruction=LECTURE_VARIANTS[max(0,min(int(variant),len(LECTURE_VARIANTS)-1))]
    evidence=_lecture_evidence(unit,chunks,max_chars)
    if len(evidence)<180:raise MissingLectureSource('This chapter has too little readable text to make a Chinese lecture yet; its pages are read again automatically.')
    system=('你是'+(_clean_text(course_name) or '目前上傳課程')+'課程的教學助理。以繁體中文撰寫可直接朗讀的課堂講稿。'
            '所有事實只能來自提供的教材，保留藥名、數值、公式與重要英文專有名詞。'
            '不要引用圖號、頁碼、書名、章節號或作者；不要寫題目；不要輸出英文段落或來源標記。'
            '若教材沒有支持某一因果關係，就不要自行補充。'
            '數學式使用 $...$ 或 $$...$$，逐字保留上下標、微分階數、括號、正負號及向量符號。'
            '若文字擷取沒有保留完整公式，省略該式，僅解釋可確認的概念，不得憑方程式名稱補寫公式。')
    user=('單元：'+str(unit['title'])+'\n'+instruction+
          '僅使用下面的乾淨教材摘錄；其餘頁面仍由學生問答檢索。\n\n'+evidence)
    raw_script=model_call(model_key,system+SOURCE_RULE,user,LECTURE_MAX_TOKENS)
    if text_issues(raw_script):raise RejectedLecture('Lecture contains unreadable text or symbols. Retry preparation from the source PDF.')
    script=_clean_text(raw_script)
    han=len(re.findall(r'[\u3400-\u9fff]',script));latin=len(re.findall(r'[A-Za-z]',script))
    if _PDF_PRIVATE.search(script) or han<150 or han<latin//2:
        raise RejectedLecture('The local model did not produce a readable Traditional Chinese lecture. Retry with another available model.')
    script,_=review_text('teacher script',script,evidence,model_key,model_call)
    return '單元：%s。\n\n%s'%(unit['title'],script)

_SEARCH_STOP={'the','and','what','how','does','is','are','of','in','to','for','a','an','on','with','why','can','from','this','that','about','which','when','where','explain','describe','影響','什麼','如何','請問','課程'}
def _search_terms(value):
    raw=unicodedata.normalize('NFKC',str(value or '')).lower().replace('v/q','vq')
    words=[x for x in re.findall(r'[a-z0-9]{2,}',raw) if x not in _SEARCH_STOP]
    han=re.findall(r'[\u3400-\u9fff]+',raw)
    return set(words+[s[i:i+2] for s in han for i in range(max(0,len(s)-1))])

def _lex_score(terms,value):
    if not terms:return 0.0
    found=terms & _search_terms(value)
    return float(len(found))/float(len(terms))

class CourseManager(object):
    def __init__(self):
        self.build_slots=threading.Semaphore(1);self.jobs={};self.jobs_lock=threading.RLock();self.cache={};self.cache_lock=threading.Lock()
        self.upload_lock=threading.RLock();self.manifest_lock=threading.RLock();self.scenario_lock=threading.RLock();self.quiz_result_lock=threading.RLock();self.running=set();self.deleted=set();self.running_lock=threading.Lock();self.views=self._load_views();self.views_saved=0;self.created={};self.inspecting=set();self.inspect_lock=threading.Lock()

    def _stage_dir(self,stage_id):return STAGING_ROOT/_safe_id(stage_id)
    def _course_dir(self,course_id):return COURSE_ROOT/_safe_id(course_id)
    def _manifest_path(self,course_id):return self._course_dir(course_id)/'course_manifest.json'

    def upload_status(self,stage_id,upload_id,background=False):
        """Upload progress.  background=True (the web server): a received PDF is inspected in a
        background thread and reported as inspecting, so no request waits for a long PDF."""
        sd=self._stage_dir(stage_id);uid=_safe_id(upload_id)
        meta=sd/'_uploads'/(uid+'.json')
        if not meta.exists():return {'offset':0,'complete':False,'stage_id':_safe_id(stage_id)}
        if background:
            with self.upload_lock:state=json.loads(meta.read_text(encoding='utf-8'))
            if not state.get('complete') and state['offset']==state['size']:
                if state.get('inspect_error'):raise ValueError(state['inspect_error'])
                self._inspect_later(stage_id,uid)
                return {'offset':state['offset'],'complete':False,'inspecting':True,'stage_id':_safe_id(stage_id)}
        with self.upload_lock:
            state=json.loads(meta.read_text(encoding='utf-8'))
            part=sd/'_uploads'/(uid+'.part')
            if not state.get('complete') and state['offset']==state['size'] and part.exists():
                self._finish_inspection(sd,stage_id,meta,state,part)
        result={'offset':state['offset'],'complete':bool(state.get('complete')),'stage_id':_safe_id(stage_id)}
        if result['complete'] and (sd/'stage.json').exists():
            result['inspection']=json.loads((sd/'stage.json').read_text(encoding='utf-8'))
        return result

    def _finish_inspection(self,sd,stage_id,meta,state,part):
        stage_path=sd/'stage.json'
        count=len(json.loads(stage_path.read_text(encoding='utf-8')).get('sources',[])) if stage_path.exists() else 0
        if count<=state.get('source_count',0):
            self.inspect([(state['name'],part)],course_name=state.get('course_name',''),stage_id=stage_id)
        state['complete']=True;state.pop('inspect_error',None);_json_dump(meta,state);part.unlink()

    def _inspect_later(self,stage_id,uid):
        """Inspect a fully received PDF once, in the background (one inspection at a time)."""
        key=(_safe_id(stage_id),uid)
        with self.running_lock:
            if key in self.inspecting:return
            self.inspecting.add(key)
        def work():
            sd=self._stage_dir(stage_id);meta=sd/'_uploads'/(uid+'.json');part=sd/'_uploads'/(uid+'.part')
            try:
                with self.inspect_lock:
                    state=json.loads(meta.read_text(encoding='utf-8'))
                    if not state.get('complete') and state['offset']==state['size'] and part.exists():
                        self._finish_inspection(sd,stage_id,meta,state,part)
            except Exception as error:
                try:
                    with self.upload_lock:
                        state=json.loads(meta.read_text(encoding='utf-8'));state['inspect_error']=str(error)[:400];_json_dump(meta,state)
                except Exception:pass
            finally:
                with self.running_lock:self.inspecting.discard(key)
        threading.Thread(target=work,daemon=True).start()

    def active_builds(self):
        """Course builds that are queued or running, and builds that failed in the last day."""
        out={}
        for path in (COURSE_ROOT/'_jobs').glob('*.json'):
            try:job=json.loads(path.read_text(encoding='utf-8'))
            except Exception:continue
            if job.get('id'):out[job['id']]=job
        with self.jobs_lock:out.update({jid:dict(job) for jid,job in self.jobs.items()})
        now=time.time();rows=[]
        for job in out.values():
            status=job.get('status')
            if status in ('queued','running') or (status=='error' and now-float(job.get('updated_at') or 0)<86400):
                files=[]
                try:files=[src.get('display_name') or src.get('file_name') for src in json.loads((self._stage_dir(job.get('stage_id') or '')/'stage.json').read_text(encoding='utf-8')).get('sources') or []]
                except Exception:pass
                rows.append({'id':job.get('id'),'course_id':job.get('course_id') or '','course_name':job.get('course_name') or '','files':files[:5],'status':status,
                             'page_methods':job.get('page_methods') or {},'processed_pages':job.get('processed_pages') or 0,'total_pages':job.get('total_pages') or 0,
                             'progress':job.get('progress') or 0,'message':str(job.get('message') or '')[:300],'error':str(job.get('error') or '')[:300],
                             'created_at':job.get('created_at') or 0,'updated_at':job.get('updated_at') or 0})
        return sorted(rows,key=lambda row:-float(row['created_at'] or 0))

    def upload_chunk(self,stage_id,upload_id,name,total_size,offset,data,course_name='',background=False):
        if not stage_id or not upload_id or not str(name).lower().endswith('.pdf'):
            raise ValueError('A valid stage, upload ID and PDF filename are required.')
        if len(data)<1 or len(data)>CHUNK_LIMIT:raise ValueError('Upload chunks must be 16 MB or smaller.')
        total_size=int(total_size);offset=int(offset)
        if total_size<=0 or total_size>320*1024*1024 or offset<0:raise ValueError('Invalid PDF size or upload offset.')
        sd=self._stage_dir(stage_id);ud=sd/'_uploads';ud.mkdir(parents=True,exist_ok=True)
        uid=_safe_id(upload_id);mp=ud/(uid+'.json');part=ud/(uid+'.part')
        with self.upload_lock:
            state=json.loads(mp.read_text(encoding='utf-8')) if mp.exists() else {'name':str(name),'size':total_size,'offset':0,'complete':False,'course_name':course_name,'created_at':time.time()}
            if state['name']!=name or state['size']!=total_size:raise ValueError('The resumed PDF does not match the original file.')
            if state.get('complete'):return self.upload_status(stage_id,upload_id)
            if offset!=state['offset']:
                raise ValueError('Upload offset mismatch; check upload status and resume at the server offset.')
            if offset+len(data)>total_size:raise ValueError('Chunk exceeds declared PDF size.')
            with part.open('r+b' if part.exists() else 'wb') as f:
                f.seek(offset);f.write(data);f.flush();os.fsync(f.fileno())
            state['offset']=offset+len(data)
            state['updated_at']=time.time()
            if state['offset']==total_size:
                stage_path=sd/'stage.json'
                state['source_count']=len(json.loads(stage_path.read_text(encoding='utf-8')).get('sources',[])) if stage_path.exists() else 0
            _json_dump(mp,state)
            if state['offset']==total_size and background:
                # A long PDF is inspected in the background; the page polls upload status.
                self._inspect_later(stage_id,uid)
                return {'offset':total_size,'complete':False,'inspecting':True,'stage_id':_safe_id(stage_id)}
            if state['offset']==total_size:
                # The completed file is durably staged before inspection; a lost
                # response can be recovered through upload_status on refresh.
                inspection=self.inspect([(name,part)],course_name=course_name,stage_id=stage_id)
                state['complete']=True;_json_dump(mp,state);part.unlink()
                return {'offset':total_size,'complete':True,'stage_id':_safe_id(stage_id),'inspection':inspection}
            return {'offset':state['offset'],'complete':False,'stage_id':_safe_id(stage_id)}

    def inspect(self,files,course_name='',visual_model='none',stage_id=''):
        if fitz is None:raise RuntimeError('PDF parser unavailable in the serving Python. Install PyMuPDF in the serving Python; import error: '+PDF_IMPORT_ERROR)
        if not files:raise ValueError('At least one PDF is required.')
        stage_id=_safe_id(stage_id) if stage_id else ('stage-'+uuid.uuid4().hex[:12])
        sd=self._stage_dir(stage_id);(sd/'source').mkdir(parents=True,exist_ok=True)
        stage_path=sd/'stage.json'
        if stage_path.exists():
            try:meta=json.loads(stage_path.read_text(encoding='utf-8'))
            except Exception:raise ValueError('Upload staging data is invalid.')
            sources=list(meta.get('sources') or []);units=list(meta.get('units') or []);auto_name=_clean_text(meta.get('course_name') or '')
        else:
            sources=[];units=[];auto_name=''
        source_index=len(sources)
        for offset,(name,data) in enumerate(files,1):
            if not str(name).lower().endswith('.pdf'):continue
            fi=source_index+offset
            safe='%02d_%s'%(fi,re.sub(r'[^A-Za-z0-9._-]+','_',Path(name).name))
            p=sd/'source'/safe
            if isinstance(data,Path):
                if data.resolve()!=p.resolve():shutil.copy2(str(data),str(p))
            else:p.write_bytes(data)
            receipt=write_receipt(sd,p,name)
            try:
                with closing(fitz.open(str(p))) as doc:
                    if doc.page_count<1:raise ValueError('Not a readable PDF: '+str(name))
                    pdfmeta=doc.metadata or {}
                    title=_course_title(pdfmeta.get('title'),name)
                    chapter_rows=_course_chapters(doc)
                    samples=[]; readable=0
                    # Sample throughout the book; a cover alone cannot enable the simulator.
                    stride=max(1,doc.page_count//16)
                    for pi in range(0,doc.page_count,stride):
                        s=_clean_text(doc[pi].get_text('text') or '')
                        if s:readable+=len(s);samples.append(s[:3500])
                    # Scanned uploads are valid inputs. Defer page OCR to the
                    # background worker instead of rejecting the original here.
                    requires_ocr=readable<40
                    if not auto_name:auto_name=title
                    for ctitle,start,end in chapter_rows:
                        units.append({'id':'u%03d'%len(units),'title':title if ctitle.startswith('Entire PDF —') else ctitle,'source_file':safe,'source_title':title,'start_page':start,'end_page':end,'enabled':True,'needs_review':ctitle.startswith('Entire PDF —')})
                    respiratory=_is_respiratory_physiology(' '.join([x[0] for x in chapter_rows]+samples))
                    receipt.update({'inspection_status':'ready','pages':doc.page_count,'ocr_recommended':requires_ocr});atomic_json(sd/'receipts'/(safe+'.json'),receipt)
                    sources.append({'sha256':receipt['sha256'],'size_bytes':receipt['size_bytes'],'file_name':safe,'display_name':Path(name).name,'title':title,'pages':doc.page_count,'chapter_count':len(chapter_rows),'ocr_recommended':requires_ocr,'respiratory_physiology':respiratory})
            except Exception as error:
                receipt.update({'inspection_status':'error','error':str(error)[:1000]});atomic_json(sd/'receipts'/(safe+'.json'),receipt)
                raise
        if not sources:raise ValueError('No valid PDF was uploaded.')
        meta={'stage_id':stage_id,'course_name':_clean_text(course_name) or auto_name or 'Uploaded Course','visual_model':'vq' if any(s.get('respiratory_physiology') for s in sources) else 'none','sources':sources,'units':units,'created_at':meta.get('created_at',time.time()) if stage_path.exists() else time.time(),'updated_at':time.time()}
        _json_dump(sd/'stage.json',meta)
        return meta

    def start_build(self,stage_id,course_name,visual_model,units,model_key,model_call,reading=None):
        sd=self._stage_dir(stage_id);stage_path=sd/'stage.json'
        if not stage_path.exists():raise ValueError('Upload staging data was not found.')
        stage=json.loads(stage_path.read_text(encoding='utf-8'))
        allowed={u['id']:u for u in stage.get('units',[])};selected=[]
        pages={s.get('file_name'):int(s.get('pages') or 0) for s in stage.get('sources',[])}
        for x in units or []:
            uid=str((x or {}).get('id') or '')
            if uid not in allowed:continue
            u=dict(allowed[uid]);title=_clean_text((x or {}).get('title') or u['title'])
            if title:u['title']=title[:160]
            # The teacher may correct a unit's page range within its own PDF.
            if (x or {}).get('start_page') is not None or (x or {}).get('end_page') is not None:
                try:start=int(x.get('start_page',u['start_page']));end=int(x.get('end_page',u['end_page']))
                except (TypeError,ValueError):raise ValueError('Unit pages must be whole numbers: '+u['title'])
                last=pages.get(u.get('source_file')) or int(u['end_page'])
                if not 1<=start<=end<=last:raise ValueError('Pages of "%s" must be between 1 and %d, first page not after last page.'%(u['title'],last))
                if (start,end)!=(int(u['start_page']),int(u['end_page'])):u.update({'start_page':start,'end_page':end,'pages_corrected':True})
            selected.append(u)
        if not selected:raise ValueError('Select at least one chapter/unit.')
        if len({u['id'] for u in selected})!=len(selected):raise ValueError('Each chapter/unit can only be selected once.')
        # One staged upload owns one build. Retrying after a lost response or
        # refreshing the browser must attach to the existing job.
        prior=sd/'build_job.json'
        with self.jobs_lock:
            if prior.exists():
                existing=json.loads(prior.read_text(encoding='utf-8'))
                if self.job_status(existing['id']).get('status')=='cancelled':
                    prior.unlink();existing=None
            if prior.exists() and existing:
                if existing.get('selected')!=selected:raise ValueError('This upload is already building. Continue the existing job or start a new upload for different units.')
                current=self.job_status(existing['id'])
                if current.get('status')=='error':
                    self._set_job(existing['id'],status='queued',progress=0,message='Retrying course activation',error='')
                    threading.Thread(target=self._build,args=(existing['id'],stage,selected,existing['course_id'],course_name,stage.get('visual_model','none'),model_key,model_call),daemon=True).start()
                return self.job_status(existing['id'])
        base=_safe_id(course_name or stage.get('course_name') or 'uploaded-course');course_id=base+'-'+uuid.uuid4().hex[:8]
        job_id='course-'+uuid.uuid4().hex[:12]
        job={'id':job_id,'status':'queued','progress':0,'message':'Queued','course_id':course_id,'stage_id':stage_id,'selected':selected,'course_name':course_name,'model_key':model_key,
             'reading':reading_mode(reading),'created_at':time.time(),'updated_at':time.time()}
        with self.jobs_lock:
            self.jobs[job_id]=job;_json_dump(sd/'build_job.json',job);_json_dump(self._job_path(job_id),job)
        th=threading.Thread(target=self._build,args=(job_id,stage,selected,course_id,course_name,stage.get('visual_model','none'),model_key,model_call),daemon=True);th.start()
        return dict(job)

    def _job_path(self,jid):return COURSE_ROOT/'_jobs'/(_safe_id(jid)+'.json')

    def _set_job(self,jid,**kw):
        with self.jobs_lock:
            if jid not in self.jobs:
                jp=self._job_path(jid)
                if not jp.exists():return
                self.jobs[jid]=json.loads(jp.read_text(encoding='utf-8'))
            self.jobs[jid].update(kw);self.jobs[jid]['updated_at']=time.time()
            _json_dump(self._job_path(jid),self.jobs[jid])

    def job_status(self,jid):
        with self.jobs_lock:
            if jid in self.jobs:return dict(self.jobs[jid])
            p=self._job_path(jid)
            return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

    def resume_pending(self,model_call):
        """Reconnect jobs and background work after a service restart."""
        jobs_dir=COURSE_ROOT/'_jobs'
        if jobs_dir.exists():
            for p in jobs_dir.glob('*.json'):
                try:
                    job=json.loads(p.read_text(encoding='utf-8'))
                    if job.get('status') not in ('queued','running'):
                        recoverable=job.get('status')=='error' and job.get('phase')=='extracting chapter text' and job.get('ingestion_revision')!='R15'
                        if not recoverable:continue
                    stage=json.loads((self._stage_dir(job['stage_id'])/'stage.json').read_text(encoding='utf-8'))
                    cid=job['course_id'];jid=job['id']
                    if self.manifest(cid) and self.manifest(cid).get('ingestion_complete',True):
                        self._set_job(jid,status='done',progress=100,message='Course active; background preparation continues',result={'course_id':cid})
                    else:
                        threading.Thread(target=self._build,args=(jid,stage,job['selected'],cid,job['course_name'],stage.get('visual_model','none'),job['model_key'],model_call),daemon=True).start()
                except Exception as e:self._set_job(job.get('id',''),status='error',error='Could not resume build: '+str(e))
        for man in self.list_courses():
            if man.get('content_version')!='v241.9':
                try:man=self._upgrade_course_text(man)
                except Exception as e:
                    self._update_manifest(man['course_id'],lambda current:current.update({'status':'error','migration_error':str(e)}))
                    continue
            if man.get('question_generation')!=QUESTION_GENERATION:
                def prepare_bank(current):
                    current['status']='preparing'
                    current.setdefault('preparation',{})['questions']={'status':'preparing','completed_units':0,'total_units':len(current.get('units') or []),'message':'Preparing reviewed exam-style questions from available evidence'}
                    for u in current.get('units') or []:u['question_count']=0
                man=self._update_manifest(man['course_id'],prepare_bank) or man
            man=self._quarantine_damaged_math(man)
            def refresh(current):
                # Pages read by OCR are not "unread" (older courses counted them).
                if current.get('source_review'):
                    current['source_review']=_review_summary((current['source_review'].get('units') or {}))
            man=self._update_manifest(man['course_id'],refresh) or man
            prep=man.get('preparation') or {}
            if (prep.get('sources') or {}).get('status')=='preparing':
                # A source re-read stopped by a restart would otherwise show its last page forever.
                self._launch_repair(man['course_id'],man.get('model_key') or 'gemma',model_call)
                continue
            if man.get('status') in ('preparing','error') or any((prep.get(k) or {}).get('status') in ('preparing','error','waiting') for k in ('questions','index','lectures','figures')):
                self._start_preparation(man['course_id'],man.get('model_key') or 'gemma',model_call)
        self.supervise(model_call)

    def supervise(self,model_call):
        """Preparation that stopped (a model or connection failure, a full GPU) starts
        again by itself: every VQ_PREPARATION_RETRY_SEC (600 s), with growing
        pauses, at most PREPARATION_RETRIES times per step until it is ready again.
        Teachers do not need to press retry."""
        with self.running_lock:
            if getattr(self,'_supervising',False):return
            self._supervising=True
        def loop():
            while True:
                time.sleep(PREPARATION_RETRY_SECONDS)
                try:self._retry_stopped(model_call)
                except Exception as error:print('Automatic preparation retry skipped: %s'%error,flush=True)
        threading.Thread(target=loop,daemon=True).start()

    def _retry_stopped(self,model_call,now=None):
        now=time.time() if now is None else now;started=[]
        for man in self.list_courses():
            cid=man.get('course_id')
            if not cid or cid in self.deleted or man.get('ingestion_complete') is False:continue
            with self.running_lock:
                if any(item[0]==cid for item in self.running):continue
            prep=man.get('preparation') or {};kinds=[];reset=[]
            for kind in ('questions','lectures','index','figures'):
                info=prep.get(kind) or {}
                retries=int(info.get('auto_retries') or 0)
                if info.get('status')=='ready':
                    if retries:reset.append(kind)
                    continue
                if info.get('status') not in ('error','waiting') or retries>=PREPARATION_RETRIES:continue
                if now-float(info.get('auto_retry_at') or 0)<PREPARATION_RETRY_SECONDS*(2**retries):continue
                kinds.append(kind)
            if not kinds and not reset:continue
            def mark(current,kinds=tuple(kinds),reset=tuple(reset)):
                for kind in kinds:
                    row=current.setdefault('preparation',{}).setdefault(kind,{})
                    row.update({'auto_retries':int(row.get('auto_retries') or 0)+1,'auto_retry_at':now})
                for kind in reset:current['preparation'][kind]['auto_retries']=0
            self._update_manifest(cid,mark)
            if kinds:
                started.append((cid,tuple(kinds)))
                self._start_preparation(cid,man.get('model_key') or 'gemma',model_call,only=tuple(kinds))
        return started

    def _quarantine_damaged_math(self,man):
        cid=man['course_id'];cd=self._course_dir(cid);qp=cd/'question_banks.json'
        banks=json.loads(qp.read_text(encoding='utf-8')) if qp.exists() else {}
        cp=cd/'chunks.jsonl'
        current={str(row['id']):row.get('text','') for row in (json.loads(line) for line in cp.read_text(encoding='utf-8').splitlines() if line)} if cp.exists() else {}
        def valid(row):return reviewed_card(row,current.get(str(row.get('evidence_chunk_id')),''))
        damaged_units=[];removed=0
        for unit in man.get('units') or []:
            uid=unit['id'];bank=banks.get(uid) or []
            good=[row for row in bank if valid(row)]
            if len(good)!=len(bank):removed+=len(bank)-len(good);damaged_units.append(uid)
        bad_scripts=[u['id'] for u in man.get('units') or [] if u.get('script') and (text_issues(u.get('script','')) or u.get('script_review_revision')!=REVIEW_REVISION)]
        if not damaged_units and not bad_scripts:return man
        backup=cd/'backups'/('content_R15_'+uuid.uuid4().hex)
        backup.mkdir(parents=True,exist_ok=True)
        for name in ('course_manifest.json','question_banks.json'):
            if (cd/name).exists():shutil.copy2(str(cd/name),str(backup/name))
        for unit in man.get('units') or []:
            uid=unit['id']
            if uid in damaged_units:
                banks[uid]=[row for row in banks[uid] if valid(row)]
                unit['question_count']=len(banks[uid])
            if uid in bad_scripts:unit.update({'script':'','script_status':'preparing'})
        prep=man.setdefault('preparation',{})
        for kind,affected in (('questions',damaged_units),('lectures',bad_scripts)):
            if affected:prep.setdefault(kind,{}).update({'status':'preparing','message':'Reviewing saved content against current course sources'})
        man.update({'status':'preparing','math_integrity_revision':'R12','content_integrity_revision':REVIEW_REVISION})
        man['math_repair']={'removed_questions':removed,'lecture_units':bad_scripts,'backup':str(backup)}
        _json_dump(qp,banks);_json_dump(cd/'course_manifest.json',man)
        return man

    def _upgrade_course_text(self,man):
        """Replace v241.8 glyph-contaminated scripts, questions and indexes."""
        cid=man['course_id'];cd=self._course_dir(cid);old=[]
        cp=cd/'chunks.jsonl'
        if cp.exists():old=[json.loads(x) for x in cp.read_text(encoding='utf-8').splitlines() if x]
        first={}
        for chunk in old:first.setdefault(chunk.get('unit_id'),chunk)
        chunks=[]
        for unit in man.get('units',[]):
            uid=unit['id'];path=cd/'units'/(uid+'.txt')
            if not path.exists():raise RuntimeError('Saved chapter text is missing for '+str(unit.get('title') or uid))
            content=_safe_source_text(path.read_text(encoding='utf-8'))
            if not _readable_source(content):raise RuntimeError('Chapter '+str(unit.get('title') or uid)+' contains unreadable PDF glyphs; upload an OCR-readable PDF.')
            path.write_text(content,encoding='utf-8')
            previous=first.get(uid) or {}
            for n,value in enumerate(_word_chunks(content),1):
                chunks.append({'id':'%s_c%03d'%(uid,n),'unit_id':uid,'unit_title':unit['title'],'source':unit.get('source_title') or previous.get('source') or 'Uploaded PDF','source_file':previous.get('source_file') or '', 'pdf_page':unit.get('start_page') or previous.get('pdf_page') or 1,'text':value})
            unit['script']='';unit['script_status']='preparing';unit['question_count']=0
        if not chunks:raise RuntimeError('No readable teaching passages remain after PDF text repair.')
        with cp.open('w',encoding='utf-8') as f:
            for chunk in chunks:f.write(json.dumps(chunk,ensure_ascii=False)+'\n')
        _json_dump(cd/'question_banks.json',{});_json_dump(cd/'scenario_cache.json',{})
        for filename in ('vector_index.sqlite3','vector_index.building.sqlite3','window_index.sqlite3'):
            p=cd/filename
            if p.exists():p.unlink()
        prep=man.setdefault('preparation',{})
        prep['lectures']={'status':'preparing','completed_units':0,'total_units':len(man['units']),'message':'Preparing readable Chinese teacher scripts'}
        prep['questions']={'status':'preparing','completed_units':0,'total_units':len(man['units']),'message':'Preparing reviewed exam-style questions from available evidence'}
        prep['index']={'status':'preparing','message':'Rebuilding search from corrected PDF text'}
        man.update({'content_version':'v241.9','question_generation':'scenario-seed-v1','status':'preparing'})
        _json_dump(cd/'course_manifest.json',man)
        with self.cache_lock:self.cache.pop(cid,None)
        return man

    def _unit_pages(self,doc,start_page,end_page):
        return [extract_page(doc[p-1],p) for p in range(max(1,int(start_page)),min(doc.page_count,int(end_page))+1)]

    def _unit_text(self,source_path,start_page,end_page,doc=None):
        owned=doc is None
        if owned:doc=fitz.open(str(source_path))
        try:return '\n\n'.join(row['text'] for row in self._unit_pages(doc,start_page,end_page) if row['text'])
        finally:
            if owned:doc.close()

    def _alive(self,cid):
        if cid in self.deleted:raise CourseDeleted('This course was deleted or its build was cancelled.')

    @contextmanager
    def _background(self,cid,kind='text',idle_only=False):
        """Course work yields the model to student questions and stops when the course is deleted.

        Courses take turns on the shared model by rank instead of all at once: a build (a
        teacher is waiting for the course to open) first, then text preparation (lecture
        scripts, questions, search), then image reading; within each, a course someone has
        open goes first, then the newest course.
        """
        with BackgroundWork(lambda:self._alive(cid),rank=lambda:self._rank(cid,kind),idle_only=idle_only):
            with step_scope(self._course_dir(cid)/'_steps'/kind,lambda:self._alive(cid)):
                yield

    def viewed(self,cid):
        """A teacher or student has this course open (its status is being read)."""
        if not cid:return
        now=time.time();self.views[cid]=now
        if now-self.views_saved>60:  # remembered across restarts, written at most once a minute
            self.views_saved=now
            try:_json_dump(COURSE_ROOT/'_views.json',self.views)
            except Exception:pass

    def _load_views(self):
        try:return {str(k):float(v) for k,v in json.loads((COURSE_ROOT/'_views.json').read_text(encoding='utf-8')).items()}
        except Exception:return {}

    def dormant(self,cid):
        """Nobody has opened this course for DORMANT_SECONDS and it is not new: its background work waits."""
        created=self.created.get(cid)
        if created is None:
            created=float((self.manifest(cid) or {}).get('created_at') or time.time());self.created[cid]=created
        if time.time()-max(self.views.get(cid,0),created)<=DORMANT_SECONDS:return False
        manifest=self.manifest(cid) or {}
        if manifest.get('ingestion_complete') is False or any(
            row.get('status') in ('preparing','waiting','queued') or row.get('expansion_requests')
            for row in (manifest.get('preparation') or {}).values() if isinstance(row,dict)):return False
        return True

    def _rank(self,cid,kind):
        tier={'build':0,'text':1,'image':2}.get(kind,1)
        if kind!='build' and self.dormant(cid):return PAUSED
        open_now=time.time()-self.views.get(cid,0)<VIEW_SECONDS
        return (tier,0 if open_now else 1,-self.created.get(cid,time.time()))

    def _discard_deleted(self,cid):
        """Remove files a stopped worker may have written after deletion."""
        if cid not in self.deleted:return
        cd=self._course_dir(cid)
        if cd.is_dir() and not cd.is_symlink():shutil.rmtree(str(cd),ignore_errors=True)
        with self.cache_lock:self.cache.pop(cid,None)

    def _build(self,jid,stage,units,course_id,course_name,visual_model,model_key,model_call):
        self._set_job(jid,status='queued',message='PDF received; waiting for the extraction worker',ingestion_revision='R15')
        try:
            with self.build_slots:
                if course_id in self.deleted:raise CourseDeleted('Build cancelled')
                with self._background(course_id,'build'):
                    return self._build_impl(jid,stage,units,course_id,course_name,visual_model,model_key,model_call)
        finally:self._discard_deleted(course_id)

    def _build_impl(self,jid,stage,units,course_id,course_name,visual_model,model_key,model_call):
        phase='initializing the course workspace'
        try:
            if self.manifest(course_id) and self.manifest(course_id).get('ingestion_complete',True):
                self._set_job(jid,status='done',progress=100,message='Course active; background preparation continues',result={'course_id':course_id})
                self._start_preparation(course_id,model_key,model_call);return
            cd=self._course_dir(course_id);(cd/'source').mkdir(parents=True,exist_ok=True);(cd/'units').mkdir(exist_ok=True)
            sd=self._stage_dir(stage['stage_id'])
            for s in stage.get('sources',[]):
                src=sd/'source'/s['file_name'];dst=cd/'source'/s['file_name']
                if src.exists() and not dst.exists():
                    try:os.link(str(src),str(dst))
                    except OSError:shutil.copyfile(str(src),str(dst))
            phase='extracting chapter text'
            self._set_job(jid,status='running',progress=4,message='Extracting chapter text')
            prior=self.manifest(course_id) or {}
            chunks=[json.loads(x) for x in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x] if prior else []
            committed={u['id'] for u in prior.get('units',[])}
            chunks=[row for row in chunks if row.get('unit_id') in committed]  # discard an uncommitted crash tail
            unit_texts={u['id']:(cd/'units'/(u['id']+'.txt')).read_text(encoding='utf-8') for u in prior.get('units',[])}
            source_quality=dict((prior.get('source_review') or {}).get('units') or {})
            extracted=[u for u in units if u['id'] in source_quality]
            doc=None;last_file=None;figure_pages=list(prior.get('figure_pages') or [])
            # Builds started before reading modes existed (or after a restart) read text first.
            reading=reading_mode((self.job_status(jid) or {}).get('reading'))
            total_pages=sum(int(u['end_page'])-int(u['start_page'])+1 for u in units);page_progress=[0]
            # How each page was read and how fast, shown on the build card (and in the status report).
            methods={'text':0,'OCR':0,'image model':0,'saved':0,'blank':0};timing={'started':time.time(),'read':0}
            def reading_summary(current=None):
                left=total_pages-page_progress[0]
                rate=(time.time()-timing['started'])/timing['read'] if timing['read'] else None
                eta=' · about %s left'%_duration(rate*left) if rate and left else ''
                counts=' · '.join('%s %s'%(name,count) for name,count in methods.items() if count)
                return '%s%s / %s pages%s%s'%(current+' · ' if current else '',page_progress[0],total_pages,eta,(' · '+counts) if counts else '')
            try:
                for ui,u in enumerate(units,1):
                    if u['id'] in source_quality:
                        page_progress[0]+=int(u['end_page'])-int(u['start_page'])+1
                        continue
                    if u['source_file']!=last_file:
                        if doc:doc.close()
                        doc=fitz.open(str(cd/'source'/u['source_file']));last_file=u['source_file']
                    source=next((src for src in stage.get('sources',[]) if src['file_name']==u['source_file']),{})
                    identity=source.get('sha256') or (u['source_file']+'|'+str((cd/'source'/u['source_file']).stat().st_size))
                    def page_done(row,cached):
                        self._alive(course_id)
                        if row['status'] in ('ocr_running','vision_running'):
                            self._set_job(jid,status='running',message=reading_summary('%s page %s'%('Image model reading' if row['status']=='vision_running' else 'OCR',row['page'])),page_methods=dict(methods),current_page=row['page'],current_source=source.get('display_name') or u['source_file'])
                            return
                        page_progress[0]+=1
                        kind='saved' if cached else 'image model' if row.get('extraction_method')=='local_vision' else 'OCR' if row.get('extraction_method')=='local_ocr' else 'blank' if not row.get('text') else 'text'
                        methods[kind]+=1
                        if not cached:timing['read']+=1  # the time per page counts pages actually read
                        if page_progress[0]%5==0 or row['status']=='needs_review' or page_progress[0]==total_pages:
                            self._set_job(jid,status='running',progress=4+int(10*page_progress[0]/max(1,total_pages)),message=reading_summary('Reading page %s'%row['page']),page_methods=dict(methods),processed_pages=page_progress[0],total_pages=total_pages,current_page=row['page'],current_source=source.get('display_name') or u['source_file'])
                    pages=list(iter_pages(doc,u['start_page'],u['end_page'],_shared_pages(sd,identity),identity,page_done,mode=reading))
                    figure_pages.extend({'unit_id':u['id'],'source_file':u['source_file'],'page':row['page']} for row in pages if row.get('figure_pending'))
                    source_quality[u['id']]=unit_quality(pages)
                    atomic_json(cd/'source_quality.json',source_quality)
                    txt=_safe_source_text('\n\n'.join(row['text'] for row in pages if row['text']))
                    if sum(c.isalpha() for c in txt)<40:
                        source_quality[u['id']]['status']='needs_review';source_quality[u['id']]['reason']='insufficient readable teaching text';txt=''
                    atomic_json(cd/'source_quality.json',source_quality)
                    unit_texts[u['id']]=txt
                    (cd/'units'/(u['id']+'.txt')).write_text(txt,encoding='utf-8')
                    ci=0
                    for page in (pages if txt else []):
                        for c in _word_chunks(page['text']):
                            ci+=1
                            chunks.append({'id':'%s_c%03d'%(u['id'],ci),'unit_id':u['id'],'unit_title':u['title'],'source':u['source_title'],'source_file':u['source_file'],'pdf_page':page['page'],'extraction_method':page.get('extraction_method','native_text'),'text':c})
                    self._set_job(jid,progress=4+int(10*ui/max(1,len(units))),message='Extracted %s / %s chapters'%(ui,len(units)))
                    extracted.append(u)
                    # Publish a consistent chapter snapshot, then let CPU extraction
                    # overlap GPU preparation of the chapters already available.
                    self._publish_extracted(jid,stage,extracted,course_id,course_name,visual_model,model_key,model_call,
                                            chunks,unit_texts,source_quality,figure_pages,False)

            finally:
                if doc:doc.close()
            self._publish_extracted(jid,stage,units,course_id,course_name,visual_model,model_key,model_call,
                                    chunks,unit_texts,source_quality,figure_pages,True)
            self._set_job(jid,status='done',progress=100,message='Course Q&A active; background preparation continues' if chunks else 'PDF received; no usable text yet. Open upload diagnostics.',result={'course_id':course_id})
        except CourseDeleted:
            self._set_job(jid,status='cancelled',progress=100,message='Course build cancelled by the teacher')
        except Exception as e:
            if course_id in self.deleted:
                self._set_job(jid,status='cancelled',progress=100,message='Course build cancelled by the teacher');return
            self._set_job(jid,status='error',message='Course build failed during '+phase,error=str(e),phase=phase)

    def _publish_extracted(self,jid,stage,units,cid,course_name,visual_model,model_key,model_call,
                           chunks,unit_texts,source_quality,figure_pages,complete):
        """Commit a chapter snapshot without resetting results from concurrent workers."""
        self._alive(cid)
        cd=self._course_dir(cid)
        previous=self.manifest(cid) or {}
        if previous:
            title=previous['course_name'];name_source=previous.get('course_name_source','pdf_metadata')
        elif chunks:
            title,name_source=_resolve_course_name(course_name,stage,units,unit_texts,model_key,model_call,
                lambda message:self._set_job(jid,status='running',message=message))
        else:title,name_source=(_clean_text(course_name) or stage.get('course_name') or 'Uploaded Course'),'pdf_metadata'
        with self.manifest_lock:
            self._alive(cid)
            manifest=self.manifest(cid) or {}
            old={u['id']:u for u in manifest.get('units',[])}
            lecture_units=[]
            for unit in units:
                u=dict(old.get(unit['id']) or {})
                quality=source_quality[unit['id']];blocked=quality['status']=='needs_review'
                u.update({'id':unit['id'],'title':unit['title'],'source_title':unit['source_title'],
                          'start_page':unit['start_page'],'end_page':unit['end_page'],
                          'source_quality':quality,'source_blocked':blocked})
                for key,value in {'script':'','script_status':'source_review' if blocked else 'preparing',
                                  'question_count':0,'question_source_count':0,'question_processed_count':0}.items():
                    u.setdefault(key,value)
                lecture_units.append(u)
            manifest.update({'stage_id':stage['stage_id'],'ingestion_revision':'R15',
                'source_review':_review_summary(source_quality),
                'course_id':cid,'course_name':title,'course_name_source':name_source,
                'visual_model':'vq' if visual_model=='vq' else 'none',
                'visual_model_label':'V/Q Visual Model' if visual_model=='vq' else 'No Specialized Visual Model',
                'sources':stage.get('sources',[]),'units':lecture_units,'content_version':'v241.9',
                'question_target_per_unit':QUESTION_TARGET,'quiz_questions_per_unit':2,'question_generation':QUESTION_GENERATION,
                'rag':{'embedding_model':EMBED_MODEL,'dimension':1024,'top_candidates':TOP_CANDIDATES,'final_top_k':FINAL_TOP_K,
                       'search_method':'course text ready immediately; semantic passage index follows'},
                'model_key':model_key,'created_at':manifest.get('created_at') or time.time(),
                'status':'preparing' if chunks else 'source_review','ingestion_complete':complete,
                'source_revision':int(manifest.get('source_revision',0))+1,'figure_pages':figure_pages})
            prep=manifest.setdefault('preparation',{})
            for kind in ('lectures','questions','index'):
                prep.setdefault(kind,{})['status']='preparing' if chunks else 'source_review'
            if figure_pages:
                prep.setdefault('figures',{}).update({'status':'waiting','total':len(figure_pages),
                    'message':'Visual source preparation continues in the background'})
            # Chunks and manifest are read together under this same lock by workers.
            path=cd/'chunks.jsonl'
            temporary=path.with_name(path.name+'.publishing')
            temporary.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in chunks),encoding='utf-8')
            os.replace(str(temporary),str(path))
            for name in ('question_banks.json','scenario_cache.json'):
                if not (cd/name).exists():_json_dump(cd/name,{})
            _json_dump(cd/'course_manifest.json',manifest)
            with self.cache_lock:self.cache.pop(cid,None)
        if chunks:
            self._start_preparation(cid,model_key,model_call,
                                    only=None if complete else ('lectures','questions','index'))

    def _build_vectors(self,cd,chunks,jid):
        vp=cd/'vector_index.sqlite3';tmp=cd/'vector_index.building.sqlite3'
        if tmp.exists():tmp.unlink()
        conn=sqlite3.connect(str(tmp))
        try:
            conn.execute('CREATE TABLE vectors (idx INTEGER PRIMARY KEY, chunk_id TEXT, unit_id TEXT, source TEXT, page INTEGER, text TEXT, dim INTEGER, vec BLOB)')
            batch=8;dim=0
            for i in range(0,len(chunks),batch):
                rows=chunks[i:i+batch]
                try:emb=_embed_resilient([r['text'] for r in rows])
                except Exception as e:raise RuntimeError('Passage embedding %s–%s of %s failed: %s'%(i+1,i+len(rows),len(chunks),e))
                if emb:dim=len(emb[0])
                conn.executemany('INSERT INTO vectors VALUES (?,?,?,?,?,?,?,?)',[(i+j+1,r['id'],r['unit_id'],r['source'],int(r.get('pdf_page') or 0),r['text'],len(v),_pack(v)) for j,(r,v) in enumerate(zip(rows,emb))]);conn.commit()
                self._update_manifest(cd.name,lambda m:m['preparation']['index'].update({'status':'preparing','completed':i+len(rows),'total':len(chunks),'message':'Indexed %s / %s passages'%(i+len(rows),len(chunks))}))
            conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)');conn.executemany('INSERT INTO meta VALUES (?,?)',[('model',EMBED_MODEL),('count',str(len(chunks))),('dimension',str(dim))]);conn.commit()
        finally:conn.close()
        os.replace(str(tmp),str(vp))
        with self.cache_lock:self.cache.pop(cd.name,None)

    def _update_manifest(self,cid,change):
        with self.manifest_lock:
            m=self.manifest(cid)
            if not m:return None
            change(m);m['updated_at']=time.time();_json_dump(self._manifest_path(cid),m)
            return m

    def _start_preparation(self,cid,model_key,model_call,force=False,only=None):
        m=self.manifest(cid)
        if not m:return
        with self.running_lock:
            if (cid,'sources') in self.running:return
        if m.get('units') and all(u.get('source_blocked') for u in m['units']):
            self._auto_repair(cid,model_key,model_call);return
        prep=m.get('preparation') or {};launched=[]
        tasks=(('questions',self._prepare_questions),('lectures',self._prepare_lectures),('index',self._prepare_index),('figures',self._prepare_figures))
        for kind,fn in tasks:
            if kind=='figures' and (not m.get('figure_pages') or m.get('ingestion_complete') is False):continue
            if only is not None and kind not in only:continue
            if not force and (prep.get(kind) or {}).get('status')=='ready':continue
            key=(cid,kind)
            with self.running_lock:
                if cid in self.deleted:continue
                if (cid,'sources') in self.running:return
                if key in self.running:continue
                self.running.add(key)
            def work(task=key,task_fn=fn,revision=m.get('source_revision')):
                try:
                    # Figures are a supplement: they start only while no student is asking.
                    with self._background(cid,'image' if task[1]=='figures' else 'text',idle_only=task[1]=='figures'):
                        with step_scope(self._course_dir(cid)/'_steps'/task[1],lambda:self._alive(cid)):
                            task_fn(cid,model_key,model_call)
                except CourseDeleted:pass
                finally:
                    with self.running_lock:self.running.discard(task)
                    if cid in self.deleted:
                        with self.running_lock:busy=any(item[0]==cid for item in self.running)
                        if not busy:self._discard_deleted(cid)
                        return
                    latest=self.manifest(cid) or {}
                    if latest.get('source_revision')!=revision:
                        self._start_preparation(cid,model_key,model_call,force=True,only=(task[1],))
                        return
                    if task[1]=='questions':
                        latest=self.manifest(cid) or {}
                        questions=(latest.get('preparation') or {}).get('questions') or {}
                        if questions.get('expansion_requests') and questions.get('status')!='error':
                            self._start_preparation(cid,model_key,model_call,force=True,only=('questions',))
                    self._auto_repair(cid,model_key,model_call)
            launched.append(kind)
            threading.Thread(target=work,daemon=True).start()
        if not launched:self._auto_repair(cid,model_key,model_call)

    def _auto_repair(self,cid,model_key,model_call):
        """Pages with no readable text are read again automatically, once, after
        the other preparation for the course has finished (OCR, then the local
        vision model reading the whole page, then page halves). Only the
        chapters containing such pages are touched."""
        with self.running_lock:
            if cid in self.deleted or any(item[0]==cid for item in self.running):return
        m=self.manifest(cid) or {}
        if m.get('ingestion_complete') is False or int(m.get('auto_source_repairs') or 0)>=1:return
        known=(m.get('source_review') or {}).get('units') or {}
        if not any(_needs_reread(u,known.get(u['id']) or u.get('source_quality')) for u in m.get('units') or []):return
        self._update_manifest(cid,lambda current:current.update({'auto_source_repairs':int(current.get('auto_source_repairs') or 0)+1}))
        self._launch_repair(cid,model_key,model_call)

    def retry_preparation(self,cid,model_key,model_call):
        """Restart only the steps that are not ready; never re-read the whole PDF.

        Pages with no readable text are read again afterwards, by themselves
        (_auto_repair), and chapters that failed every lecture variant get
        another full try.
        """
        manifest=self.manifest(cid)
        if not manifest:raise ValueError('course not found')
        with self.running_lock:
            if (cid,'sources') in self.running:return manifest   # unreadable pages are being read again
        prep=manifest.get('preparation') or {}
        kinds=tuple(kind for kind in ('questions','lectures','index','figures') if (prep.get(kind) or {}).get('status')!='ready')
        def reset(current):
            current['auto_source_repairs']=0
            for kind in kinds:current.setdefault('preparation',{}).setdefault(kind,{})['auto_retries']=0
            for unit in current.get('units') or []:
                if unit.get('script_status')=='error':unit['script_attempts']=0
        self._update_manifest(cid,reset)
        with self.running_lock:busy=any(item[0]==cid for item in self.running)
        known=(manifest.get('source_review') or {}).get('units') or {}
        if not busy and any(_needs_reread(u,known.get(u['id']) or u.get('source_quality')) for u in manifest.get('units') or []):
            # Nothing else is running: read the unread pages now (only their chapters);
            # the stopped steps continue as soon as that finishes.
            self._update_manifest(cid,lambda current:current.update({'auto_source_repairs':1}))
            self._launch_repair(cid,model_key,model_call)
        elif kinds:self._start_preparation(cid,model_key,model_call,only=kinds)
        else:self._auto_repair(cid,model_key,model_call)
        return self.manifest(cid)

    def _launch_repair(self,cid,model_key,model_call):
        """Re-read problem pages from the saved original in the background, then continue preparation."""
        key=(cid,'sources')
        with self.running_lock:
            if key in self.running:return
            self.running.add(key)
        self._update_manifest(cid,lambda m:m.setdefault('preparation',{}).update({'sources':{'status':'preparing','message':'Reading pages with no readable text again','started_at':time.time()}}))
        def repair():
            try:
                # Not a build slot: a long re-read must not hold up other teachers' new courses.
                with self._background(cid,'image'):self._repair_sources(cid)
            except CourseDeleted:return
            except Exception as error:
                self._update_manifest(cid,lambda m:m['preparation']['sources'].update({'status':'error','error':str(error)[:400]}))
            finally:
                with self.running_lock:self.running.discard(key)
            self._start_preparation(cid,model_key,model_call)
        threading.Thread(target=repair,daemon=True).start()

    def _repair_sources(self,cid):
        """Read again only the chapters with pages that have no readable text.

        Other chapters keep their text, chunks and question banks; pages read
        before are taken from the page cache, so only unread pages are re-read.
        """
        m=self.manifest(cid);cd=self._course_dir(cid);sd=self._stage_dir(m['stage_id'])
        stage=json.loads((sd/'stage.json').read_text(encoding='utf-8'))
        selected={unit['id']:unit for unit in stage['units']}
        known=(m.get('source_review') or {}).get('units') or {}
        targets={u['id'] for u in m['units'] if _needs_reread(u,known.get(u['id']) or u.get('source_quality'))}
        try:old_chunks=[json.loads(x) for x in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
        except OSError:old_chunks=[]
        chunks=[];texts={};qualities={};changed=set()
        total=sum(int(selected[u['id']]['end_page'])-int(selected[u['id']]['start_page'])+1 for u in m['units'] if u['id'] in targets)
        seen=set();pending=[];shown=[0.0]
        for unit in m['units']:
            spec=selected[unit['id']];uid=unit['id']
            if uid not in targets:
                chunks.extend(row for row in old_chunks if row.get('unit_id')==uid)
                qualities[uid]=known.get(uid) or unit.get('source_quality') or {}
                continue
            source=next(row for row in stage['sources'] if row['file_name']==spec['source_file'])
            def progress(row,cached,uid=uid):
                if row['status'] not in ('ocr_running','vision_running'):seen.add((uid,row['page']))
                if time.time()-shown[0]<1 and len(seen)<total:return   # at most one progress write a second
                shown[0]=time.time()
                label='Local OCR' if row['status']=='ocr_running' else 'Local visual reading' if row['status']=='vision_running' else 'Reading saved PDF'
                self._update_manifest(cid,lambda current:current['preparation']['sources'].update({'current_page':row['page'],'completed':len(seen),'total':total,'updated_at':time.time(),
                    'message':'%s: page %s (%s / %s pages)'%(label,row['page'],len(seen),total)}))
            with closing(fitz.open(str(cd/'source'/spec['source_file']))) as doc:
                pages=list(iter_pages(doc,spec['start_page'],spec['end_page'],_shared_pages(sd,source.get('sha256') or spec['source_file']),source.get('sha256') or spec['source_file'],progress))
            quality=unit_quality(pages);text=_safe_source_text('\n\n'.join(row['text'] for row in pages if row['text']))
            pending.extend({'unit_id':uid,'source_file':spec['source_file'],'page':row['page']} for row in pages if row.get('figure_pending'))
            if sum(c.isalpha() for c in text)<40:text='';quality['status']='needs_review'
            previous=cd/'units'/(uid+'.txt')
            if not previous.exists() or previous.read_text(encoding='utf-8')!=text:changed.add(uid)
            texts[uid]=text;qualities[uid]=quality
            unit.update({'source_quality':quality,'source_blocked':quality['status']=='needs_review'})
            if uid in changed:unit.update({'script':'','script_status':'source_review' if unit['source_blocked'] else 'preparing','question_count':0,'question_source_count':0,'question_processed_count':0})
            ci=0
            for page in (pages if text else []):
                for value in _word_chunks(page['text']):
                    ci+=1
                    chunks.append({'id':'%s_c%03d'%(uid,ci),'unit_id':uid,'unit_title':unit['title'],'source':spec['source_title'],'source_file':spec['source_file'],'pdf_page':page['page'],'extraction_method':page.get('extraction_method','native_text'),'text':value})
        if changed:
            backup=cd/'backups'/('source_ocr_'+uuid.uuid4().hex);backup.mkdir(parents=True)
            for name in ('course_manifest.json','chunks.jsonl','question_banks.json','scenario_cache.json'):
                if (cd/name).exists():shutil.copy2(str(cd/name),str(backup/name))
            for uid,text in texts.items():(cd/'units'/(uid+'.txt')).write_text(text,encoding='utf-8')  # re-read chapters only
            banks=json.loads((cd/'question_banks.json').read_text(encoding='utf-8'))
            _json_dump(cd/'question_banks.json',{uid:cards for uid,cards in banks.items() if uid not in changed})
            _json_dump(cd/'scenario_cache.json',{})
            for name in ('vector_index.sqlite3','window_index.sqlite3'):
                if (cd/name).exists():(cd/name).unlink()
            temporary=cd/'chunks.repair.jsonl'
            temporary.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in chunks),encoding='utf-8')
            os.replace(str(temporary),str(cd/'chunks.jsonl'))
            for kind in ('lectures','questions','index'):m['preparation'].setdefault(kind,{}).update({'status':'preparing','error':''})
        atomic_json(cd/'source_quality.json',qualities)
        # Pages re-read text-first still get their images read (and reviewed) in the background.
        known={(row['source_file'],int(row['page'])) for row in m.get('figure_pages') or []}
        added=[row for row in pending if (row['source_file'],int(row['page'])) not in known]
        if added:
            m['figure_pages']=list(m.get('figure_pages') or [])+added
            m['preparation']['figures']={'status':'waiting','completed':0,'total':len(m['figure_pages']),'message':'Figures are read after lectures are ready'}
        m['source_review']=_review_summary(qualities)
        if changed:m['status']='preparing' if chunks else 'source_review'
        m['preparation']['sources']={'status':'ready','message':'Pages with no readable text were read again (%s pages in %s chapters)'%(total,len(targets))}
        for unit in m['units']:
            if unit['id'] in targets:unit['script_needs_source']=False
        # Retain concurrently updated source-progress metadata only until this
        # completed snapshot is installed; other preparation is locked out.
        _json_dump(cd/'course_manifest.json',m)
        with self.cache_lock:self.cache.pop(cid,None)

    def prioritize_unit(self,cid,unit_id,kind,model_key,model_call):
        man=self.manifest(cid)
        if not man:raise ValueError('course not found')
        if kind not in ('questions','lectures'):raise ValueError('Invalid preparation type.')
        if unit_id not in {u['id'] for u in man.get('units',[])}:raise ValueError('Selected unit not found.')
        self._update_manifest(cid,lambda current:current['preparation'][kind].update({'priority_unit_id':unit_id}))
        self._start_preparation(cid,model_key,model_call)
        return self.manifest(cid)

    def expand_questions(self,cid,unit_ids,model_key,model_call):
        man=self.manifest(cid)
        if not man:raise ValueError('course not found')
        valid={u['id'] for u in man.get('units',[])}
        if not isinstance(unit_ids,list) or not unit_ids or len(unit_ids)>len(valid):
            raise ValueError('Select at least one unit to expand.')
        requested=[str(uid) for uid in unit_ids]
        if len(set(requested))!=len(requested) or any(uid not in valid for uid in requested):
            raise ValueError('The selected unit is invalid.')
        def queue(current):
            q=current['preparation']['questions']
            waiting=list(q.get('expansion_requests') or [])
            q['expansion_requests']=waiting+[uid for uid in requested if uid not in waiting]
            q.update({'status':'preparing','message':'Expanding the selected unit question bank'})
        self._update_manifest(cid,queue)
        self._start_preparation(cid,model_key,model_call,force=True,only=('questions',))
        return self.manifest(cid)

    def _prepare_index(self,cid,model_key,model_call):
        cd=self._course_dir(cid)
        try:
            self._update_manifest(cid,lambda m:m['preparation']['index'].update({'status':'preparing','message':'Preparing semantic passage index','error':''}))
            chunks=[json.loads(line) for line in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if line]
            self._build_vectors(cd,chunks,None)
            self._update_manifest(cid,lambda m:m['preparation']['index'].update({'status':'ready','message':'Semantic search ready'}))
        except Exception as e:self._update_manifest(cid,lambda m:m['preparation']['index'].update({'status':'error','error':str(e),'message':'Semantic search unavailable; course text search remains active'}))

    def _prepare_lectures(self,cid,model_key,model_call):
        """Write lectures for several chapters at once (gpu_policy.background_slots).

        A rejected draft is followed by a differently asked draft
        (LECTURE_VARIANTS). While chapters are still being extracted, a chapter
        that failed every variant waits for the final pass instead of being
        retried after each newly published chapter.
        """
        cd=self._course_dir(cid)
        try:
            with self.manifest_lock:
                rows=[json.loads(x) for x in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
                manifest=self.manifest(cid);units=manifest['units']
                final=manifest.get('ingestion_complete',True) is not False
            grouped={}
            for row in rows:grouped.setdefault(row['unit_id'],[]).append(row)
            course_name=(manifest or {}).get('course_name','')
            guard=threading.Lock();state={'completed':0,'errors':[],'pending':list(range(len(units)))}

            def fingerprint(unit):
                text='\n'.join(str(row.get('text') or '') for row in grouped.get(unit['id'],[]))
                return hashlib.sha256(('%s|%s'%(REVIEW_REVISION,text)).encode('utf-8')).hexdigest()[:20]

            def wanted(unit):
                if unit.get('source_blocked'):return False
                if unit.get('script_status')=='ready' and unit.get('script') and unit.get('script_review_revision')==REVIEW_REVISION:
                    with guard:state['completed']+=1
                    return False
                if unit.get('script_status')=='error' and unit.get('script_evidence')==fingerprint(unit):
                    # Same evidence already failed every variant: try once more on the final pass.
                    if not final or int(unit.get('script_attempts') or 0)>=2:
                        with guard:state['errors'].append('%s: %s'%(unit['title'],unit.get('script_error') or 'not ready'))
                        return False
                return True

            def take():
                with guard:
                    if not state['pending']:return None
                    current=self.manifest(cid) or {}
                    priority=(current.get('preparation',{}).get('lectures') or {}).get('priority_unit_id')
                    i=next((index for index in state['pending'] if units[index]['id']==priority),state['pending'][0])
                    state['pending'].remove(i);return i

            def write(i):
                unit=units[i];evidence=fingerprint(unit);last=None
                self._update_manifest(cid,lambda m,n=i:m['preparation']['lectures'].update({'status':'preparing','message':'Writing Chinese lecture %s / %s'%(n+1,len(units)),'error':''}))
                for variant in range(len(LECTURE_VARIANTS)):
                    self._alive(cid);steps_mark()
                    try:
                        script=_chinese_lecture(unit,grouped.get(unit['id'],[]),model_key,model_call,course_name,variant)
                    except CourseDeleted:raise
                    except Exception as error:
                        last=error
                        if isinstance(error,MissingLectureSource):break   # needs its pages read again
                        if _lecture_retryable(error):
                            reject_steps();continue                        # ask differently
                        break                                              # model unavailable: later pass
                    else:
                        with guard:state['completed']+=1
                        self._update_manifest(cid,lambda m,n=i,value=script,v=variant:m['units'][n].update({
                            'script':value,'script_status':'ready','script_error':'','script_review_revision':REVIEW_REVISION,
                            'script_variant':v,'script_evidence':evidence}))
                        return
                with guard:state['errors'].append('%s: %s'%(unit['title'],last))
                self._update_manifest(cid,lambda m,n=i,err=str(last),missing=isinstance(last,MissingLectureSource):m['units'][n].update({
                    'script':'','script_status':'error','script_error':err,'script_needs_source':missing,'script_evidence':evidence,
                    'script_attempts':int(m['units'][n].get('script_attempts') or 0)+1}))

            def worker():
                while True:
                    i=take()
                    if i is None:return
                    if wanted(units[i]):write(i)
                    with guard:done=state['completed']
                    self._update_manifest(cid,lambda m,done=done:m['preparation']['lectures'].update({'completed_units':done,'total_units':len(units),'message':'Chinese lectures ready: %s / %s'%(done,len(units))}))

            self._parallel(cid,'text','lectures',worker,gpu_policy.background_slots())
            state_name='error' if state['errors'] else 'ready'
            self._update_manifest(cid,lambda m:m['preparation']['lectures'].update({'status':state_name,'error':'; '.join(state['errors'])[:1000]}))
        except CourseDeleted:raise
        except Exception as e:
            self._update_manifest(cid,lambda m:m['preparation']['lectures'].update({'status':'error','error':str(e),'message':'Chinese lecture preparation stopped'}))

    def _parallel(self,cid,kind,steps,target,count):
        """Run target in count threads, each with this course's background context."""
        if count<=1:
            target();return
        errors=[]
        def run():
            try:
                with self._background(cid,kind):
                    with step_scope(self._course_dir(cid)/'_steps'/steps,lambda:self._alive(cid)):target()
            except BaseException as error:errors.append(error)
        threads=[threading.Thread(target=run,daemon=True) for _ in range(count)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        deleted=[error for error in errors if isinstance(error,CourseDeleted)]
        if deleted:raise deleted[0]
        if errors:raise errors[0]

    def _prepare_figures(self,cid,model_key,model_call):
        """Read figures (and text-first pages) after the lectures; add them to course Q&A as they are read."""
        cd=self._course_dir(cid)
        def status(**values):
            self._update_manifest(cid,lambda m:m.setdefault('preparation',{}).setdefault('figures',{}).update(values))
        try:
            self._wait_for_lectures(cid);self._alive(cid)
            m=self.manifest(cid) or {};pages=m.get('figure_pages') or []
            if not pages:return
            vision=LocalVision()
            if not vision.probe():
                status(status='unavailable',message='Figures on text pages were not read: '+vision.error);return
            cache_path=cd/'figure_cache.json'
            done=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
            units={u['id']:u for u in m.get('units') or []};docs={};totals={'added':0,'indexed':0,'replaced':0};published=set()
            def publish(final):
                """Add every read page's passages to course Q&A (a batch at a time on long decks)."""
                added=[]
                for row in pages:
                    key='%s|%s'%(row['source_file'],row['page'])
                    if key in published or key not in done:continue
                    published.add(key);entry=done[key]
                    text=_safe_source_text(entry.get('text') or '') if entry.get('ok') else ''
                    unit=units.get(row['unit_id'])
                    if not text or not unit or text_issues(text):continue
                    for k,value in enumerate(_word_chunks(text),1):
                        added.append({'id':'%s_p%04d_fig%02d'%(unit['id'],int(row['page']),k),'unit_id':unit['id'],'unit_title':unit['title'],
                            'source':unit.get('source_title') or 'Uploaded PDF','source_file':row['source_file'],'pdf_page':int(row['page']),
                            'extraction_method':'local_vision_figure','text':value})
                self._alive(cid)
                read={(c['unit_id'],c['pdf_page']) for c in added}
                with self.manifest_lock:
                    cp=cd/'chunks.jsonl';rows=[json.loads(x) for x in cp.read_text(encoding='utf-8').splitlines() if x]
                    have={c['id'] for c in rows}
                    fresh=[c for c in added if c['id'] not in have]
                    # A page the vision model has read (and reviewed) no longer keeps its
                    # interim OCR text: the reviewed reading replaces it in course Q&A.
                    stale=[c for c in rows if c.get('extraction_method')=='local_ocr' and (c['unit_id'],c.get('pdf_page')) in read]
                    if stale:
                        drop={c['id'] for c in stale}
                        rows=[c for c in rows if c['id'] not in drop]+fresh
                        temporary=cp.with_name(cp.name+'.tmp')
                        with temporary.open('w',encoding='utf-8') as f:
                            for c in rows:f.write(json.dumps(c,ensure_ascii=False)+'\n')
                        os.replace(str(temporary),str(cp))
                    elif fresh:
                        with cp.open('a',encoding='utf-8') as f:
                            for c in fresh:f.write(json.dumps(c,ensure_ascii=False)+'\n')
                if stale:self._drop_indexed_chunks(cid,[c['id'] for c in stale])
                totals['added']+=len(fresh);totals['replaced']+=len({(c['unit_id'],c.get('pdf_page')) for c in stale})
                with self.running_lock:indexing=(cid,'index') in self.running
                if final:
                    while indexing:  # the full index may still be embedding the original passages
                        self._alive(cid);time.sleep(5)
                        with self.running_lock:indexing=(cid,'index') in self.running
                if not indexing:
                    totals['indexed']+=self._index_missing_chunks(cid)
                    if final:self._drop_indexed_chunks(cid)  # an index built meanwhile may still hold replaced OCR passages
                else:
                    with self.cache_lock:self.cache.pop(cid,None)  # text search sees new passages at once
            shas={src.get('file_name'):src.get('sha256') for src in m.get('sources') or [] if src.get('sha256')}
            def shared(row):
                sha=shas.get(row['source_file'])
                return COURSE_ROOT/'_figure_cache'/sha/('%06d-%s.json'%(int(row['page']),vision.fingerprint)) if sha else None
            def unfinished():
                return [r for r in pages if not (done.get('%s|%s'%(r['source_file'],r['page'])) or {}).get('ok') and not (done.get('%s|%s'%(r['source_file'],r['page'])) or {}).get('exhausted')]
            try:
              for attempt in range(2):
                if attempt:
                    # Pages interrupted by a model or connection failure are read once more.
                    if not unfinished():break
                    for _ in range(30):self._alive(cid);time.sleep(1)
                for n,row in enumerate(pages,1):
                    self._alive(cid)
                    key='%s|%s'%(row['source_file'],row['page'])
                    # A page no reading variant could verify with this model is not read
                    # again on every pass; a transport failure or a new model is.
                    if key in done and not done[key].get('ok') and not (done[key].get('exhausted') and done[key].get('vision')==vision.fingerprint):
                        done.pop(key)
                    if key not in done and shared(row) is not None and shared(row).exists():
                        try:done[key]=json.loads(shared(row).read_text(encoding='utf-8'));_json_dump(cache_path,done)  # read before (same PDF, same model)
                        except Exception:pass
                    if key not in done:
                        status(status='preparing',completed=n-1,total=len(pages),message='Reading figures: page %s (%s / %s)'%(row['page'],n,len(pages)))
                        if row['source_file'] not in docs:docs[row['source_file']]=fitz.open(str(cd/'source'/row['source_file']))
                        result=vision.read(docs[row['source_file']][int(row['page'])-1])
                        done[key]={'ok':bool(result.get('ok')),'text':result.get('text','') if result.get('ok') else '','error':result.get('error','')[:240],
                                   'exhausted':bool(result.get('exhausted')),'vision':vision.fingerprint}
                        self._alive(cid);_json_dump(cache_path,done)
                        if done[key]['ok'] and shared(row) is not None:
                            shared(row).parent.mkdir(parents=True,exist_ok=True);_json_dump(shared(row),done[key])
                        if n%FIGURE_BATCH==0:publish(False)
            finally:
                for doc in docs.values():doc.close()
            publish(True)
            read=sum(bool((done.get('%s|%s'%(r['source_file'],r['page'])) or {}).get('ok')) for r in pages)
            # Figures supplement the text: pages whose figures could not be verified keep
            # their text in course Q&A and never hold the course back.
            unverified=sum(bool((done.get('%s|%s'%(r['source_file'],r['page'])) or {}).get('exhausted')) for r in pages)
            pending=len(pages)-read-unverified
            status(status='ready' if not pending else 'waiting',completed=len(pages),total=len(pages),pages_read=read,pages_unverified=unverified,
                   pages_pending=pending,passages_added=totals['added'],passages_indexed=totals['indexed'],
                   ocr_pages_replaced=totals['replaced'],
                   message='Figures read on %s / %s pages; %s figure passages added to course Q&A'%(read,len(pages),totals['added'])+
                   ('; reviewed readings replaced interim OCR text on %s pages'%totals['replaced'] if totals['replaced'] else ''))
        except CourseDeleted:raise
        except Exception as e:status(status='error',error=str(e)[:400],message='Figure reading stopped; text-based course content remains available')

    def _drop_indexed_chunks(self,cid,ids=None):
        """Remove replaced passages from the semantic index (text search reads chunks.jsonl).
        Without ids, every indexed passage that is no longer in chunks.jsonl is removed."""
        cd=self._course_dir(cid);vp=cd/'vector_index.sqlite3'
        if vp.exists() and ids!=[]:
            conn=sqlite3.connect(str(vp))
            try:
                if ids is None:
                    current={json.loads(x)['id'] for x in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x}
                    ids=[r[0] for r in conn.execute('SELECT chunk_id FROM vectors') if r[0] not in current]
                conn.executemany('DELETE FROM vectors WHERE chunk_id=?',[(i,) for i in ids])
                conn.execute("UPDATE meta SET value=? WHERE key='count'",(str(conn.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]),))
                conn.commit()
            finally:conn.close()
        with self.cache_lock:self.cache.pop(cid,None)

    def _index_missing_chunks(self,cid):
        """Embed passages appended after the semantic index was built."""
        cd=self._course_dir(cid);vp=cd/'vector_index.sqlite3'
        if not vp.exists():return 0  # the full index (or text search) reads chunks.jsonl directly
        chunks=[json.loads(x) for x in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
        conn=sqlite3.connect(str(vp))
        try:
            have={r[0] for r in conn.execute('SELECT chunk_id FROM vectors')}
            missing=[c for c in chunks if c['id'] not in have]
            start=conn.execute('SELECT MAX(idx) FROM vectors').fetchone()[0] or 0
            for i in range(0,len(missing),8):
                self._alive(cid)
                rows=missing[i:i+8];emb=_embed_resilient([r['text'] for r in rows])
                conn.executemany('INSERT INTO vectors VALUES (?,?,?,?,?,?,?,?)',[(start+i+j+1,r['id'],r['unit_id'],r['source'],int(r.get('pdf_page') or 0),r['text'],len(v),_pack(v)) for j,(r,v) in enumerate(zip(rows,emb))])
                conn.commit()
            if missing:
                conn.execute("UPDATE meta SET value=? WHERE key='count'",(str(len(have)+len(missing)),));conn.commit()
        finally:conn.close()
        with self.cache_lock:self.cache.pop(cid,None)
        return len(missing)

    def _wait_for_lectures(self,cid,limit=3*3600):
        """R10: larger question banks start after every unit has its lecture."""
        started=time.time()
        while time.time()-started<limit:
            with self.running_lock:
                lecturing=(cid,'lectures') in self.running and cid not in self.deleted
            state=(((self.manifest(cid) or {}).get('preparation') or {}).get('lectures') or {}).get('status')
            if not lecturing or state!='preparing':return
            time.sleep(5)

    def _prepare_questions(self,cid,model_key,model_call):
        """Build reviewed questions in the background; persist each batch for resume."""
        cd=self._course_dir(cid)
        try:
            self._update_manifest(cid,lambda m:m['preparation']['questions'].update({'status':'preparing','message':'Drafting and reviewing exam-style questions','error':''}))
            with self.manifest_lock:
                m=self.manifest(cid);banks=json.loads((cd/'question_banks.json').read_text(encoding='utf-8'))
                chunks=[json.loads(line) for line in (cd/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if line]
            by_unit={u['id']:[] for u in m['units']}
            for c in chunks:
                if c['unit_id'] in by_unit:by_unit[c['unit_id']].append(c)
            banks={uid:[row for row in banks.get(uid,[]) if isinstance(row,dict) and
                (row.get('question') or {}).get('generation_method')==QUESTION_GENERATION]
                for uid in by_unit}
            timing={'calls':0,'seconds':0.0}
            def timed_model_call(*args):
                start=time.monotonic()
                try:return model_call(*args)
                finally:
                    timing['calls']+=1
                    timing['seconds']+=time.monotonic()-start
            # First make each unit playable, then give each a useful small
            # bank. Longer builds run only when a unit is explicitly selected.
            for phase,target in (('first questions',2),('small banks',10)):
                if phase=='small banks':self._wait_for_lectures(cid)
                pending=list(enumerate(m['units']))
                while pending:
                    self._alive(cid)
                    priority=((self.manifest(cid) or {}).get('preparation',{}).get('questions') or {}).get('priority_unit_id')
                    position=next((n for n,(_,unit) in enumerate(pending) if unit['id']==priority),0)
                    idx,u=pending.pop(position)
                    unit_id=u['id']
                    if u.get('source_blocked'):continue
                    if len(banks.get(unit_id) or [])>=target:continue
                    def save_progress(cards,available,processed,unit_index=idx,unit_id=unit_id,unit_title=u['title'],phase=phase):
                        self._alive(cid);banks[unit_id]=cards;_json_dump(cd/'question_banks.json',banks)
                        def update(current):
                            current['units'][unit_index].update({'question_count':len(cards),'question_source_count':available,
                                'question_processed_count':processed})
                            ready=sum(x.get('question_count',0)>=2 for x in current['units'])
                            finished=sum(bool(x.get('question_source_count')) and
                                x.get('question_processed_count',0)>=min(QUESTION_TARGET,x['question_source_count'])
                                for x in current['units'])
                            if any(x.get('question_count',0)>0 for x in current['units']):current['question_generation']=QUESTION_GENERATION
                            current['preparation']['questions'].update({'completed_units':finished,'total_units':len(current['units']),
                                'ready_units':ready,'model_calls':timing['calls'],
                                'model_elapsed_seconds':round(timing['seconds'],1),
                                'message':'%s / %s units playable; %s: %s reviewed questions, %s / %s source facts evaluated (%s)'%(
                                    ready,len(current['units']),unit_title,len(cards),processed,available,phase)})
                        self._update_manifest(cid,update)
                    questions=build_exam_questions(u,by_unit[unit_id],_question_sentences,banks.get(unit_id) or [],
                        model_key,timed_model_call,save_progress,target,max_new_facts=12 if target==2 else None)
                    banks[unit_id]=questions
            while True:
                latest=self.manifest(cid) or {}
                requests=((latest.get('preparation') or {}).get('questions') or {}).get('expansion_requests') or []
                if not requests:break
                unit_id=requests[0];index=next((i for i,u in enumerate(m['units']) if u['id']==unit_id),None)
                if index is None:break  # a newly published unit is handled by the next source-revision pass
                if len(banks.get(unit_id) or [])<QUESTION_TARGET:
                    unit=m['units'][index]
                    def save_expansion(cards,available,processed,unit_index=index,unit_id=unit_id,unit_title=unit['title']):
                        self._alive(cid);banks[unit_id]=cards;_json_dump(cd/'question_banks.json',banks)
                        def update(current):
                            current['units'][unit_index].update({'question_count':len(cards),'question_source_count':available,
                                'question_processed_count':processed})
                            current['preparation']['questions'].update({'status':'preparing','model_calls':timing['calls'],
                                'model_elapsed_seconds':round(timing['seconds'],1),
                                'message':'Expanding %s: %s approved questions; %s / %s source facts evaluated'%(
                                    unit_title,len(cards),processed,available)})
                        self._update_manifest(cid,update)
                    banks[unit_id]=build_exam_questions(unit,by_unit[unit_id],_question_sentences,banks.get(unit_id) or [],
                        model_key,timed_model_call,save_expansion,QUESTION_TARGET)
                self._update_manifest(cid,lambda current,uid=unit_id:current['preparation']['questions'].update({
                    'expansion_requests':[x for x in current['preparation']['questions'].get('expansion_requests',[]) if x!=uid]}))
            missing=[u['title'] for u in m['units'] if not u.get('source_blocked') and len(banks.get(u['id']) or [])<2]
            if missing:
                raise RuntimeError('Fewer than two reviewed questions in: '+', '.join(missing[:4])+'. Ready units can still be practised; review the PDF text or retry with another model.')
            usable=sum(not u.get('source_blocked') for u in m['units'])
            self._update_manifest(cid,lambda current:(current.update({'status':'ready' if current.get('ingestion_complete',True) else 'preparing','question_generation':QUESTION_GENERATION}),
                current['preparation']['questions'].update({'status':'ready','completed_units':usable,'total_units':usable,
                    'ready_units':usable,'source_blocked_units':len(m['units'])-usable,'message':'Quick question banks ready; source-blocked units remain listed for review'})))
        except Exception as e:self._update_manifest(cid,lambda m:(m.update({'status':'error'}),m['preparation']['questions'].update({'status':'error','error':str(e),'message':'Question review stopped; saved approved questions remain available'})))

    def upload_diagnostics(self,stage_id):
        sid=str(stage_id or '')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',sid):raise ValueError('Invalid upload identifier')
        directory=self._stage_dir(sid)
        if not directory.is_dir():raise ValueError('Upload not found')
        report=upload_diagnostics(directory)
        report['jobs']=[]
        for path in (COURSE_ROOT/'_jobs').glob('*.json'):
            try:
                job=json.loads(path.read_text(encoding='utf-8'))
                if job.get('stage_id')==sid:report['jobs'].append(job)
            except (ValueError,OSError):pass
        for job in report['jobs']:
            cid=job.get('course_id');quality=self._course_dir(cid)/'source_quality.json' if cid else None
            if quality and quality.exists():report['source_quality']=json.loads(quality.read_text(encoding='utf-8'))
        return report

    def admin_uploads(self):
        # Keep the all-user listing bounded to receipts and job summaries.
        # Load a book's thousand-page quality report only when requested.
        from pdf_ocr import LocalOCR
        from pdf_vision import LocalVision
        ocr=LocalOCR().report();vision=LocalVision().report();jobs={};reports=[]
        for path in (COURSE_ROOT/'_jobs').glob('*.json'):
            try:
                job=json.loads(path.read_text(encoding='utf-8'))
                jobs.setdefault(job.get('stage_id'),[]).append({key:job.get(key) for key in ('id','course_id','course_name','status','progress','phase','message','error','updated_at')})
            except (ValueError,OSError):pass
        for directory in sorted(STAGING_ROOT.iterdir()) if STAGING_ROOT.exists() else []:
            if not directory.is_dir():continue
            report=upload_diagnostics(directory,ocr_report=ocr,vision_report=vision);report['jobs']=jobs.get(directory.name,[])
            stage=directory/'stage.json'
            try:report['course_name']=json.loads(stage.read_text(encoding='utf-8')).get('course_name','')
            except (ValueError,OSError):report['course_name']=''
            reports.append(report)
        return sorted(reports,key=lambda row:max([x.get('received_at',0) for x in row['received_files']]+[x.get('updated_at',0) for x in row['transfers']]+[0]),reverse=True)

    def list_courses(self):
        out=[]
        for p in sorted(COURSE_ROOT.iterdir()):
            if not p.is_dir() or p.name.startswith('_'):continue
            mp=p/'course_manifest.json'
            if mp.exists():
                try:out.append(json.loads(mp.read_text(encoding='utf-8')))
                except Exception:pass
        return out

    def activity(self,cid=''):
        """How much other courses are preparing on the shared model (counts only, no names)."""
        with self.running_lock:running=set(self.running)
        with self.jobs_lock:building=sum(1 for job in self.jobs.values() if job.get('status') in ('queued','running') and job.get('course_id')!=cid)
        others=[item for item in running if item[0]!=cid]
        return {'other_courses':len({item[0] for item in others}),'other_images':len({item[0] for item in others if item[1] in ('figures','sources')}),'other_builds':building}

    def course_summaries(self):
        return [{'course_id':m['course_id'],'course_name':m.get('course_name') or 'Uploaded Course','unit_count':len(m.get('units') or []),'created_at':m.get('created_at',0)} for m in self.list_courses()]

    def manifest(self,course_id):
        p=self._manifest_path(course_id)
        if not p.exists():return None
        return json.loads(p.read_text(encoding='utf-8'))

    def delete_course(self,course_id):
        """Delete at any time; running preparation stops at its next model step."""
        cid=str(course_id or '').strip()
        if not cid or cid!=_safe_id(cid) or cid.startswith('_'):
            raise ValueError('Select a valid uploaded course')
        with self.manifest_lock:
            cd=self._course_dir(cid)
            if cd.is_symlink() or not cd.is_dir() or not self.manifest(cid):raise ValueError('Uploaded course not found')
            with self.running_lock:self.deleted.add(cid)
            try:
                trash=COURSE_ROOT/'_trash';trash.mkdir(parents=True,exist_ok=True)
                target=trash/('%s-%s'%(cid,uuid.uuid4().hex[:8]))
                os.rename(str(cd),str(target))  # disappears from the course list at once
                deleted_path=COURSE_ROOT/'_deleted_courses.json'
                previously=json.loads(deleted_path.read_text(encoding='utf-8')) if deleted_path.exists() else []
                _json_dump(deleted_path,sorted(set(previously+[cid])))
            except Exception:
                with self.running_lock:self.deleted.discard(cid)
                raise
        with self.cache_lock:self.cache.pop(cid,None)
        threading.Thread(target=shutil.rmtree,args=(str(target),),kwargs={'ignore_errors':True},daemon=True).start()
        with self.running_lock:stopping=sorted(kind for item_cid,kind in self.running if item_cid==cid)
        return {'ok':True,'course_id':cid,'deleted':True,'stopped_background':stopping}

    def cancel_build(self,job_id):
        """Stop a course that is still being extracted; finished builds are deleted instead."""
        job=self.job_status(job_id)
        if not job:raise ValueError('Course build not found')
        cid=str(job.get('course_id') or '')
        if not cid or cid!=_safe_id(cid):raise ValueError('Course build not found')
        if self.manifest(cid):
            result=self.delete_course(cid);self._set_job(job_id,status='cancelled',message='Course deleted by the teacher')
            return dict(result,job_id=job_id,status='cancelled')
        if job.get('status') in ('cancelled',):return {'ok':True,'job_id':job_id,'course_id':cid,'status':'cancelled'}
        with self.running_lock:self.deleted.add(cid)
        self._set_job(job_id,status='cancelled',progress=100,message='Course build cancelled by the teacher')
        stage_job=self._stage_dir(job.get('stage_id') or '')/'build_job.json'
        try:
            if stage_job.exists():stage_job.unlink()  # the same upload can be built again
        except OSError:pass
        self._discard_deleted(cid)
        return {'ok':True,'job_id':job_id,'course_id':cid,'status':'cancelled'}

    def _load_index(self,course_id):
        cd=self._course_dir(course_id);vp=cd/'vector_index.sqlite3';wp=cd/'window_index.sqlite3'
        cp=cd/'chunks.jsonl';stamp=(vp.stat().st_mtime_ns if vp.exists() else 0,wp.stat().st_mtime_ns if wp.exists() else 0,cp.stat().st_mtime_ns if cp.exists() else 0)
        with self.cache_lock:
            cached=self.cache.get(course_id)
            if cached and cached.get('stamp')==stamp:return cached
        if not cp.exists() and not vp.exists():raise RuntimeError('Course text is not ready.')
        if vp.exists():
            conn=sqlite3.connect(str(vp));rows=conn.execute('SELECT chunk_id,unit_id,source,page,text,vec FROM vectors ORDER BY idx').fetchall();conn.close()
            passages=[{'chunk_id':r[0],'unit_id':r[1],'source':r[2],'page':r[3],'text':r[4],'vec':_unpack(r[5])} for r in rows]
        else:
            passages=[{'chunk_id':r['id'],'unit_id':r['unit_id'],'source':r['source'],'page':r.get('pdf_page'),'text':r['text']} for r in (json.loads(line) for line in cp.read_text(encoding='utf-8').splitlines() if line)]
        wins=[]
        if wp.exists():
            conn=sqlite3.connect(str(wp));raw=conn.execute('SELECT chunk_id,unit_id,source,window_idx,text,vec FROM windows ORDER BY idx').fetchall();conn.close()
            wins=[{'chunk_id':r[0],'unit_id':r[1],'source':r[2],'window_idx':r[3],'text':r[4],'vec':_unpack(r[5])} for r in raw]
        rejected=sum(bool(text_issues(r['text'])) for r in passages)
        passages=[r for r in passages if not text_issues(r['text'])]
        wins=[r for r in wins if not text_issues(r['text'])]
        data={'rejected_passages':rejected,'stamp':stamp,'rows':passages,'windows':wins,'semantic':vp.exists()}
        with self.cache_lock:self.cache[course_id]=data
        return data

    def search(self,course_id,question):
        man=self.manifest(course_id)
        if not man:return {'ok':False,'reason':'course-not-found','context':'','sources':[],'retrieval':{},'in_scope':False}
        q=str(question or '').strip()
        if not q:return {'ok':False,'reason':'empty-question','context':'','sources':[],'retrieval':{},'in_scope':False,'course':man}
        data=self._load_index(course_id);terms=_search_terms(q)
        try:qv=_embed_resilient([q])[0] if data['semantic'] else None
        except Exception:qv=None
        ranked=[]
        for r in data['rows']:
            s=_dot(qv,r['vec']) if qv is not None else _lex_score(terms,r['text'])
            ranked.append((s,r))
        ranked.sort(key=lambda x:x[0],reverse=True)
        srcs={r['source'] for _,r in ranked};cands=[];counts={}
        if len(srcs)>1:
            for s,r in ranked:
                src=r['source'];n=counts.get(src,0)
                if n<SOURCE_CANDIDATES:cands.append((s,r));counts[src]=n+1
                if len(cands)>=TOP_CANDIDATES:break
            if len(cands)<TOP_CANDIDATES:
                seen={r['chunk_id'] for _,r in cands}
                for s,r in ranked:
                    if r['chunk_id'] not in seen:cands.append((s,r));seen.add(r['chunk_id'])
                    if len(cands)>=TOP_CANDIDATES:break
        else:cands=ranked[:TOP_CANDIDATES]
        cids={r['chunk_id'] for _,r in cands};bestwin={}
        if qv is not None:
            for w in data['windows']:
                if w['chunk_id'] not in cids:continue
                s=_dot(qv,w['vec'])
                if w['chunk_id'] not in bestwin or s>bestwin[w['chunk_id']][0]:bestwin[w['chunk_id']]=(s,w)
        rer=[]
        for cs,r in cands:
            if r['chunk_id'] in bestwin:
                ws,w=bestwin[r['chunk_id']];score=RERANK_BLEND*ws+(1.0-RERANK_BLEND)*cs
            else:
                windows=_sentence_windows(r['text']);best=max(windows,key=lambda t:_lex_score(terms,t)) if windows else r['text']
                ws=_lex_score(terms,best);w={'text':best};score=(cs+0.06*ws) if qv is not None else cs
            rr=dict(r);rr['score']=float(score);rr['chunk_score']=float(cs);rr['window_score']=float(ws);rr['window_text']=w.get('text') or r['text'];rer.append(rr)
        rer.sort(key=lambda r:r['score'],reverse=True)
        final=[];counts={}
        for r in rer:
            src=r['source'];cap=SOURCE_MAX_FINAL if len(srcs)>1 else FINAL_TOP_K
            if counts.get(src,0)>=cap:continue
            final.append(r);counts[src]=counts.get(src,0)+1
            if len(final)>=FINAL_TOP_K:break
        if len(final)<FINAL_TOP_K:
            seen={r['chunk_id'] for r in final}
            for r in rer:
                if r['chunk_id'] not in seen:final.append(r);seen.add(r['chunk_id'])
                if len(final)>=FINAL_TOP_K:break
        top_score=float(final[0]['score']) if final else 0.0
        in_scope=bool(final and (top_score>=MIN_SCOPE_SCORE if qv is not None else top_score>0))
        context_parts=[];used=0;sources=[]
        if in_scope:
            for i,r in enumerate(final,1):
                block='[Evidence %d | %s | %s | PDF page %s]\n%s'%(i,r['source'],r['unit_id'],r.get('page') or '?',r['window_text'])
                if used+len(block)>CONTEXT_BUDGET:break
                context_parts.append(block);used+=len(block)+2
                sources.append({'chunk_id':r['chunk_id'],'source':r['source'],'unit_id':r['unit_id'],'pdf_page':r.get('page'),'score':round(r['score'],5)})
        retrieval={'embedding_model':EMBED_MODEL if qv is not None else None,'stage1_candidate_count':len(cands),'selected_count':len(sources),'top_matches':[{'chunk_id':r['chunk_id'],'source':r['source'],'unit_id':r['unit_id'],'score':round(r['score'],5)} for r in rer[:8]],'source_balanced':len(srcs)>1,'source_candidate_quota':SOURCE_CANDIDATES,'source_max_final':SOURCE_MAX_FINAL if len(srcs)>1 else None,'rerank_mode':'precomputed semantic windows' if qv is not None and data['windows'] else 'passage vector and lexical windows' if qv is not None else 'course text lexical fallback','final_top_k':FINAL_TOP_K}
        return {'ok':bool(context_parts),'in_scope':in_scope,'reason':'ok' if in_scope else 'outside-uploaded-course-scope','context':'\n\n'.join(context_parts),'sources':sources,'retrieval':retrieval,'course':man,'top_similarity':round(top_score,5)}

    def quiz(self,course_id,per_unit=2,model_key='gemma',model_call=None,progress=None,unit_ids=None):
        man=self.manifest(course_id)
        if not man:raise ValueError('course not found')
        if man.get('question_generation')!=QUESTION_GENERATION:raise ValueError('The exam-style question bank is being prepared. Refresh course status shortly.')
        qp=self._course_dir(course_id)/'question_banks.json'
        if not qp.exists():raise ValueError('question bank not ready')
        per=int(per_unit)
        if per<1 or per>10:raise ValueError('Select 1 to 10 questions per unit.')
        banks=json.loads(qp.read_text(encoding='utf-8'));items=[];summary=[]
        units=man.get('units',[]);started=time.monotonic()
        if unit_ids is not None:
            if not isinstance(unit_ids,list) or not unit_ids or len(unit_ids)>len(units):
                raise ValueError('Select at least one course unit to practise.')
            requested=[str(value) for value in unit_ids]
            valid={u['id'] for u in units}
            if len(set(requested))!=len(requested) or any(uid not in valid for uid in requested):
                raise ValueError('The selected course unit is invalid.')
            units=[u for u in units if u['id'] in requested]
        for index,u in enumerate(units):
            bank=[row for row in (banks.get(u['id']) or []) if reviewed_card(row)]
            if len(bank)<per:
                raise ValueError('Unit "%s" has %s reviewed questions; select %s or fewer per unit.'%(u['title'],len(bank),len(bank)))
            if any(not isinstance(row.get('question'),dict) for row in bank):
                raise ValueError('The complete question bank is being upgraded. Refresh the course status shortly.')
            shuffled=random.sample(bank,len(bank));pick=[];used_statements=set()
            for seed in shuffled:
                statement=seed['source_statement']
                if statement in used_statements:continue
                pick.append(seed);used_statements.add(statement)
                if len(pick)==per:break
            if len(pick)<per:
                pick.extend(x for x in shuffled if x not in pick)  # Smaller chapters may reuse a source statement.
                pick=pick[:per]
            summary.append({'unit_id':u['id'],'title':u['title'],'available':len(bank),'selected':len(pick)})
            for seed in pick:
                q=seed['question']
                if q.get('generation_method')!=QUESTION_GENERATION:raise RuntimeError('A reviewed exam-style question was not available.')
                x=dict(q);x['unit_id']=u['id'];x['unit_title']=u['title'];items.append(x)
            if progress:progress(len(items),per*len(units),'Selected %s / %s ready questions'%(len(items),per*len(units)))
        quiz_id=uuid.uuid4().hex
        with self.quiz_result_lock:
            conn=self._quiz_db(course_id)
            try:
                conn.execute('INSERT INTO sessions (id,created_at,question_ids) VALUES (?,?,?)',(quiz_id,time.time(),json.dumps([x['id'] for x in items])))
                conn.commit()
            finally:conn.close()
        return {'course_id':course_id,'course_name':man.get('course_name'),'quiz_id':quiz_id,'per_unit':per,'total':len(items),'units':summary,'questions':items,'generation':{'prebuilt_questions':len(items),'cached_questions':len(items),'model_calls':0,'elapsed_ms':round((time.monotonic()-started)*1000)}}

    def _quiz_db(self,course_id):
        conn=sqlite3.connect(str(self._course_dir(course_id)/'quiz_results.sqlite3'),timeout=10)
        conn.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,created_at REAL NOT NULL,question_ids TEXT NOT NULL,submitted_at REAL)')
        conn.execute('CREATE TABLE IF NOT EXISTS attempts (quiz_id TEXT PRIMARY KEY,submitted_at REAL NOT NULL,summary TEXT NOT NULL)')
        return conn

    def record_quiz(self,course_id,quiz_id,answers):
        if not self.manifest(course_id):raise ValueError('course not found')
        if not re.fullmatch(r'[a-f0-9]{32}',str(quiz_id or '')):raise ValueError('Invalid quiz session')
        if not isinstance(answers,list):raise ValueError('Answers must be a list')
        with self.quiz_result_lock:
            conn=self._quiz_db(course_id)
            try:
                existing=conn.execute('SELECT summary FROM attempts WHERE quiz_id=?',(quiz_id,)).fetchone()
                if existing:return json.loads(existing[0])
                row=conn.execute('SELECT question_ids FROM sessions WHERE id=?',(quiz_id,)).fetchone()
                if not row:raise ValueError('Quiz session not found; start a new quiz')
                ids=json.loads(row[0])
                if len(answers)!=len(ids) or [x.get('id') for x in answers if isinstance(x,dict)]!=ids:
                    raise ValueError('The submitted questions differ from this quiz session')
                banks=json.loads((self._course_dir(course_id)/'question_banks.json').read_text(encoding='utf-8'))
                legacy_cache_path=self._course_dir(course_id)/'scenario_cache.json'
                legacy_cache=json.loads(legacy_cache_path.read_text(encoding='utf-8')) if legacy_cache_path.exists() else {}
                unit_by_id={key:(uid,seed['source_statement'],seed.get('question') if key!=seed['id'] else legacy_cache.get(seed['id'])) for uid,bank in banks.items() for seed in bank for key in (seed['id'],(seed.get('question') or {}).get('id')) if key}
                rows=[];correct=0;answered=0;per_unit={}
                for item,answer_id in zip(answers,ids):
                    source=unit_by_id.get(answer_id)
                    if not source and answer_id in legacy_cache:
                        prior=legacy_cache[answer_id]
                        source=(str(prior.get('unit_id') or answer_id.split('_q')[0]),str(prior.get('evidence_statement') or ''),prior)
                    if not source:raise ValueError('Quiz question is unavailable')
                    q=source[2]
                    if not q:raise ValueError('Quiz question is unavailable')
                    choice=item.get('choice')
                    if choice is not None and (isinstance(choice,bool) or not isinstance(choice,int) or choice not in range(4)):
                        raise ValueError('Answer choice must be A–D or unanswered')
                    is_correct=choice is not None and choice==q['answer'];correct+=int(is_correct);answered+=int(choice is not None)
                    uid=source[0];u=per_unit.setdefault(uid,{'unit_id':uid,'total':0,'answered':0,'correct':0})
                    u['total']+=1;u['answered']+=int(choice is not None);u['correct']+=int(is_correct)
                    rows.append({'id':answer_id,'unit_id':uid,'choice':choice,'correct_choice':q['answer'],'correct':is_correct})
                result={'ok':True,'quiz_id':quiz_id,'course_id':course_id,'total':len(ids),'answered':answered,'correct':correct,'accuracy':round(100*correct/float(max(1,len(ids))),1),'units':list(per_unit.values()),'items':rows}
                conn.execute('INSERT INTO attempts (quiz_id,submitted_at,summary) VALUES (?,?,?)',(quiz_id,time.time(),json.dumps(result,ensure_ascii=False)))
                conn.execute('UPDATE sessions SET submitted_at=? WHERE id=?',(time.time(),quiz_id));conn.commit()
                return result
            finally:conn.close()

    def quiz_statistics(self,course_id):
        if not self.manifest(course_id):raise ValueError('course not found')
        with self.quiz_result_lock:
            conn=self._quiz_db(course_id)
            try:rows=[json.loads(x[0]) for x in conn.execute('SELECT summary FROM attempts')]
            finally:conn.close()
        units={}
        for attempt in rows:
            for row in attempt['units']:
                u=units.setdefault(row['unit_id'],{'unit_id':row['unit_id'],'total':0,'answered':0,'correct':0})
                for field in ('total','answered','correct'):u[field]+=row[field]
        questions=sum(x['total'] for x in rows);correct=sum(x['correct'] for x in rows)
        return {'ok':True,'course_id':course_id,'attempts':len(rows),'questions':questions,'correct':correct,'accuracy':round(100*correct/float(max(1,questions)),1),'units':list(units.values())}

COURSES=CourseManager()
