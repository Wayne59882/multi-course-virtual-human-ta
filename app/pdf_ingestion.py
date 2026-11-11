"""Page-local PDF ingestion with durable checkpoints and explicit source gaps.

Original uploads stay private on the teaching server. Never guess font codes.
The cache stores one page per file so retrying a large book does not restart it.
"""
from __future__ import print_function
import hashlib
import json
import os
from pathlib import Path
import re
import time
from content_integrity import extract_page, text_issues
from work_steps import atomic_json
from cpu_budget import cpu_workers, cpu_workers_idle, cpu_bound
from pdf_ocr import LocalOCR
from pdf_vision import LocalVision,teaching_images

REVISION='R15'
# How pages with images are read during a course build:
#   fast (default since R18c): text first. A page's own text layer, or local OCR when
#     it has none, is used at once and the course opens; the local vision model reads
#     the page's images afterwards in the background and adds them to course Q&A.
#     Only pages with no usable text at all are read by the vision model during the build.
#   inline (the R15 method): every page with teaching images is read by the vision
#     model (transcription + independent review) before the course opens.
#   background: like inline, but text-rich pages defer their figures.  off: never read figures.
FIGURES_MODE=os.environ.get('VQ_PDF_VISION_FIGURES','fast').strip().lower()
READING_MODES=('fast','inline','background','off')
USABLE_TEXT=40  # letters/digits OCR must recover before a page can be taught from without the vision model
SLIDE_TEXT=10   # a slide's own text (e.g. its title) is enough to open the course; its images follow in the background

def reading_mode(value=None):
    value=str(value or FIGURES_MODE).strip().lower()
    return value if value in READING_MODES else 'fast'

def digest_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def inspect_page(page,number,ocr=None,activity=None,vision=None,mode=None):
    mode=reading_mode(mode)
    ocr=ocr if ocr is not None else LocalOCR()
    vision=vision if vision is not None else LocalVision()
    native=False
    try:
        row=extract_page(page,number)
        row.update({'status':'readable' if row['text'] else 'blank','issues':[]})
        # Some slide PDFs have a searchable title over a scanned body.
        outlined=False
        drawings=getattr(page,'get_drawings',None) or getattr(page,'getDrawings',None)
        if drawings:
            try:
                paths=drawings()
                area=max(1,float(page.rect.width)*float(page.rect.height))
                # A vector chart can contain labels in the text layer but no
                # embedded raster images. Borders/underlines alone are ignored.
                outlined=bool(paths) if not row['text'] else len(paths)>=3 and any(float(path['rect'].width)*float(path['rect'].height)/area>=.12 for path in paths if path.get('rect'))
            except Exception:pass
        rich=sum(c.isalnum() for c in row['text'])>=160
        if not outlined and not teaching_images(page,row) and (not row.get('has_images') or rich):return row
        if rich and mode!='inline':
            # Enough native text to teach from now; do not hold the whole
            # course build for a slow two-pass vision reading of this figure.
            if mode!='off':row['figure_pending']=True
            return row
        if mode=='fast' and sum(c.isalnum() for c in row['text'])>=SLIDE_TEXT:
            # Slide titles and bullets are enough to open the course; images follow.
            row['figure_pending']=True
            return row
        native=bool(row['text'])
        row['status']='partial' if row['text'] else 'needs_review';row['issues']=['Sparse searchable text over image or outlined content; visual text requires OCR.']
    except Exception as error:
        # One symbol in a caption must not destroy all independent paragraphs.
        # Keep whole clean PDF blocks and explicitly record incomplete coverage.
        getter=getattr(page,'get_text',None) or getattr(page,'getText',None)
        good=[]
        try:
            for block in getter('blocks'):
                if len(block)>6 and block[6]!=0:continue
                value=str(block[4] or '').strip()
                if len(value)>=30 and not text_issues(value,check_math=False):good.append(value)
        except Exception:pass
        text='\n\n'.join(good)
        if sum(c.isalpha() for c in text)<40:text=''
        row={'page':int(number),'text':text,'status':'partial' if text else 'needs_review',
                'issues':[str(error)[:600]],'image_content_read':False}
    if mode=='fast':
        if sum(c.isalnum() for c in row['text'])>=SLIDE_TEXT:
            row.update({'figure_pending':True,'ocr_config':ocr.fingerprint,'vision_config':vision.fingerprint})
            return row
        # No usable text layer: local OCR now (seconds), the vision model later.
        if activity:activity({'page':int(number),'status':'ocr_running'},False)
        recovered=ocr.read(page)
        text=recovered.get('text','') if recovered.get('ok') else ''
        if sum(c.isalnum() for c in text)>=USABLE_TEXT:
            row.update({'text':text,'status':'partial','extraction_method':'local_ocr','image_content_read':False,'figure_pending':True,
                        'ocr_config':ocr.fingerprint,'vision_config':vision.fingerprint,'ocr':dict({key:value for key,value in recovered.items() if key!='text'},languages=ocr.requested),
                        'issues':['Text recovered with local OCR so the course can open; the local vision model reads this page in the background and adds what it reads to course Q&A.']})
            return row
        # Nothing to teach from without the vision model: read it now.
    if activity:activity({'page':int(number),'status':'vision_running'},False)
    visual=vision.read(page);row['vision_config']=vision.fingerprint
    row['vision']={key:value for key,value in visual.items() if key!='text'}
    if visual.get('ok'):
        row.update({'text':(row['text']+'\n\n' if native else '')+visual['text'],'status':'partial','extraction_method':'local_vision','image_content_read':True,'ocr_config':ocr.fingerprint})
        row['issues'].append('Local visual transcription reviewed against page pixels; retain original page for verification.')
        return row
    if native and sum(c.isalnum() for c in row['text'])>=160:
        row['extraction_method']='native_text';row['ocr_config']=ocr.fingerprint
        row['issues'].append('Native text preserved; image relationships remain unread until local vision is available.')
        return row
    if activity:activity({'page':int(number),'status':'ocr_running'},False)
    recovered=ocr.read(page);row['ocr_config']=ocr.fingerprint
    row['ocr']={key:value for key,value in recovered.items() if key!='text'}
    row['ocr']['languages']=ocr.requested
    if recovered.get('ok'):
        row.update({'text':recovered['text'],'status':'partial','extraction_method':'local_ocr','image_content_read':False})
        row['issues'].append('Text recovered with local OCR; verify symbols, values, table layout and diagrams against the original PDF.')
    else:row['issues'].append(recovered.get('error','Local OCR unavailable.'))
    return row

def ocr_workers():
    """OCR threads for scanned pages (one CPU thread each). The shared CPU budget
    (cpu_budget) lets 4 of them work while students are quiet and 2 while they ask."""
    try:return min(cpu_workers_idle(),max(1,int(os.environ.get('VQ_OCR_WORKERS',str(cpu_workers_idle())))))
    except ValueError:return 1


@cpu_bound
def _recognize(ocr,png):
    return ocr.recognize(png)


class PrefetchedOCR(object):
    """Recognize upcoming scanned pages in parallel; read() returns the page's result when it was
    prefetched and otherwise reads the page itself.  Pages are rendered in this (the main) thread."""
    def __init__(self,ocr,workers):
        from concurrent.futures import ThreadPoolExecutor
        self.ocr=ocr;self.pool=ThreadPoolExecutor(max_workers=workers);self.futures={};self.window=workers*2

    def __getattr__(self,name):
        return getattr(self.ocr,name)

    def ahead(self,doc,number,last,skip):
        for ahead in range(number,min(last,number+self.window-1)+1):
            if ahead in self.futures or skip(ahead):continue
            page=doc[ahead-1]
            if not _scanned_page(page):continue
            try:self.futures[ahead]=self.pool.submit(_recognize,self.ocr,self.ocr.render(page))
            except Exception:continue
        for old in [key for key in self.futures if key<number]:self.futures.pop(old).cancel()

    def read(self,page):
        future=self.futures.pop(int(page.number)+1,None)
        return future.result() if future is not None else self.ocr.read(page)

    def close(self):
        for future in self.futures.values():future.cancel()
        self.pool.shutdown(wait=False)


def _scanned_page(page):
    """No usable text layer but something drawn on it: text-first reading will OCR this page."""
    try:
        getter=getattr(page,'get_text',None) or getattr(page,'getText')
        if sum(c.isalnum() for c in getter('text'))>=SLIDE_TEXT:return False
        images=getattr(page,'get_images',None) or getattr(page,'getImageList',None)
        return bool(images and images())
    except Exception:return False


def iter_pages(doc,start,end,cache_directory,source_identity,progress=None,ocr=None,vision=None,mode=None):
    mode=reading_mode(mode)
    ocr=ocr if ocr is not None else LocalOCR()
    vision=vision if vision is not None else LocalVision()
    directory=Path(cache_directory)/hashlib.sha256((REVISION+'|'+source_identity).encode()).hexdigest()[:24]
    directory.mkdir(parents=True,exist_ok=True)
    first,last=max(1,int(start)),min(int(end),doc.page_count)
    # Scanned slides: several Tesseract processes at once instead of one page at a time.
    prefetch=None
    parallel=callable(getattr(ocr,'render',None)) and callable(getattr(ocr,'recognize',None)) and not getattr(ocr,'error','unknown')
    if mode=='fast' and parallel and ocr_workers()>1:
        prefetch=PrefetchedOCR(ocr,ocr_workers());ocr=prefetch
    try:
        for row in _pages(doc,first,last,directory,progress,ocr,vision,mode,prefetch):yield row
    finally:
        if prefetch is not None:prefetch.close()


def _pages(doc,first,last,directory,progress,ocr,vision,mode,prefetch):
    for number in range(first,last+1):
        if prefetch is not None:prefetch.ahead(doc,number,last,lambda n:(directory/('%06d.json'%n)).exists())
        path=directory/('%06d.json'%number);cached=False;row=None
        if path.exists():
            try:
                row=json.loads(path.read_text(encoding='utf-8'))
                cached=row.get('page')==number and row.get('status') in ('readable','blank','partial')
                # Every reader already tried this page and none could verify any text
                # (a blank or decorative page): do not read it again with the same readers.
                if row.get('page')==number and row.get('status')=='needs_review' and (row.get('vision') or {}).get('exhausted'):
                    cached=row.get('vision_config')==vision.fingerprint and row.get('ocr_config')==ocr.fingerprint
                if row.get('status')=='partial':
                    # A reviewed vision reading stays valid whatever OCR is installed now.
                    seen=row.get('extraction_method')=='local_vision' and row.get('image_content_read')
                    cached=cached and (seen or row.get('ocr_config')==ocr.fingerprint) and row.get('vision_config')==vision.fingerprint
                # A page read text-first is not a complete reading for a full (inline) build.
                if mode=='inline' and row.get('figure_pending'):cached=False
            except Exception:pass
        if not cached:
            row=inspect_page(doc[number-1],number,ocr,progress,vision,mode)
            atomic_json(path,row)
        if progress:progress(row,cached)
        yield row

def unit_quality(rows):
    warnings=[{'page':r['page'],'status':r['status'],'issues':r.get('issues',[]),'ocr':r.get('ocr'),'vision':r.get('vision')} for r in rows if r['status'] in ('partial','needs_review')]
    usable=sum(bool(r.get('text')) for r in rows)
    return {'revision':REVISION,'pages':len(rows),'usable_pages':usable,'warnings':warnings,
            'complete':not warnings,'status':'partial' if warnings and usable else 'needs_review' if not usable else 'readable'}

def write_receipt(stage_directory,file_path,display_name):
    path=Path(file_path)
    receipt={'file_name':path.name,'display_name':Path(display_name).name,'size_bytes':path.stat().st_size,
             'sha256':digest_file(path),'received_at':time.time(),'upload_received':True,'inspection_status':'received'}
    atomic_json(Path(stage_directory)/'receipts'/(path.name+'.json'),receipt)
    return receipt

def upload_diagnostics(stage_directory,ocr_report=None,vision_report=None):
    directory=Path(stage_directory)
    receipts=[]
    for path in sorted((directory/'receipts').glob('*.json')):
        try:receipts.append(json.loads(path.read_text(encoding='utf-8')))
        except Exception:pass
    known={row['file_name'] for row in receipts}
    for path in sorted((directory/'source').glob('*.pdf')):
        if path.name not in known:
            receipts.append({'file_name':path.name,'display_name':path.name,'size_bytes':path.stat().st_size,
                             'received_at':path.stat().st_mtime,'upload_received':True,'inspection_status':'legacy-staged'})
    transfers=[]
    for path in sorted((directory/'_uploads').glob('*.json')):
        try:
            row=json.loads(path.read_text(encoding='utf-8'))
            transfers.append({'upload_id':path.stem,'name':row.get('name',''),'size_bytes':row.get('size',0),
                'received_bytes':row.get('offset',0),'complete':bool(row.get('complete')),
                'created_at':row.get('created_at') or path.stat().st_ctime,'updated_at':row.get('updated_at') or path.stat().st_mtime})
        except (ValueError,OSError):pass
    job={}
    if (directory/'build_job.json').exists():
        try:job=json.loads((directory/'build_job.json').read_text(encoding='utf-8'))
        except Exception:pass
    return {'stage_id':directory.name,'received_files':receipts,'transfers':transfers,'build_job':job,'local_ocr':ocr_report if ocr_report is not None else LocalOCR().report(),'local_vision':vision_report if vision_report is not None else LocalVision().report()}
