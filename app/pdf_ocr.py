"""Bounded local OCR for scanned pages and broken PDF text layers (Python 3.6)."""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from content_integrity import text_issues


# Tesseract installed without administrator rights (scripts/setup/install_ocr_user.sh)
# is recorded here: {"executable": ".../bin/tesseract", "tessdata": ".../share/tessdata"}.
USER_OCR=Path(__file__).resolve().parent/'logs'/'tesseract.json'


def _tesseract():
    """(executable, tessdata directory or '') from VQ_TESSERACT, the PATH, or a user install."""
    configured=os.environ.get('VQ_TESSERACT','').strip()
    if configured:return configured,os.environ.get('TESSDATA_PREFIX','')
    found=shutil.which('tesseract')
    if found:return found,''
    try:
        record=json.loads(USER_OCR.read_text(encoding='utf-8'))
        executable=str(record.get('executable') or '')
        if executable and os.access(executable,os.X_OK):return executable,str(record.get('tessdata') or '')
    except (OSError,ValueError,AttributeError):pass
    return None,''


class LocalOCR:
    def __init__(self,languages=None):
        self.executable,self.tessdata=_tesseract()
        self.requested=languages or os.environ.get('VQ_PDF_OCR_LANGS','eng+chi_tra')
        self.languages=[];self.error=''
        if os.environ.get('VQ_PDF_OCR','auto').lower() in ('off','0','false'):
            self.error='Local OCR is disabled (VQ_PDF_OCR).'
        elif not self.executable:self.error='Tesseract is not installed on this server.'
        elif not re.fullmatch(r'[a-zA-Z0-9_+/-]+',self.requested):self.error='Invalid OCR language setting.'
        else:
            try:
                result=subprocess.run([self.executable,'--list-langs'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=self._env(),timeout=10)
                available=result.stdout.decode('utf-8',errors='replace').splitlines()[1:]
                self.languages=[x.strip() for x in available if x.strip()]
                missing=[x for x in self.requested.split('+') if x not in self.languages]
                if result.returncode or missing:self.error='Missing OCR language data: '+', '.join(missing or [self.requested])
            except (OSError,subprocess.TimeoutExpired) as error:self.error='Cannot start local OCR: '+str(error)[:200]
        self.fingerprint=hashlib.sha256(json.dumps([self.requested,self.languages,self.error,'ocr1']).encode()).hexdigest()[:16]

    def _env(self):
        env=dict(os.environ)
        if self.tessdata:env['TESSDATA_PREFIX']=self.tessdata
        return env

    def report(self):
        return {'engine':'tesseract','available':not bool(self.error),'languages':self.requested,
                'installed_languages':self.languages,'error':self.error,
                'setup':'Install Tesseract with eng and chi_tra data; choose other installed languages using VQ_PDF_OCR_LANGS.'}

    def read(self,page):
        if self.error:return {'ok':False,'error':self.error}
        try:
            import fitz
            # At most eight million RGB pixels; only one extraction worker and
            # one Tesseract thread. Never rasterize the whole book into memory.
            rect=page.rect;area=max(1,float(rect.width)*float(rect.height))
            scale=min(220.0/72,(8000000.0/area)**.5)
            getter=getattr(page,'get_pixmap',None) or getattr(page,'getPixmap')
            pixmap=getter(matrix=fitz.Matrix(scale,scale),alpha=False,colorspace=fitz.csRGB)
            with tempfile.TemporaryDirectory(prefix='course-ocr-') as temporary:
                image=Path(temporary)/'page.png';pixmap.save(str(image));del pixmap
                env=self._env();env['OMP_THREAD_LIMIT']='1'
                result=subprocess.run([self.executable,str(image),'stdout','-l',self.requested,'--psm','3','tsv'],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=45)
                if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[:300])
            return parse_tsv(result.stdout.decode('utf-8',errors='replace'))
        except subprocess.TimeoutExpired:return {'ok':False,'error':'Local OCR exceeded the 45-second page limit; other pages continue.'}
        except Exception as error:return {'ok':False,'error':'Local OCR failed: '+str(error)[:400]}


def parse_tsv(value):
    """Keep complete confident paragraphs; never delete a doubtful word and
    silently join the words around it. Confidence is a gate, not a guarantee.
    """
    paragraphs={};all_scores=[]
    try:
        for row in csv.DictReader(io.StringIO(value),delimiter='\t',quoting=csv.QUOTE_NONE):
            text=(row.get('text') or '').strip()
            if row.get('level')!='5' or not text:continue
            score=float(row['conf']);all_scores.append(score)
            key=(row['page_num'],row['block_num'],row['par_num'])
            paragraphs.setdefault(key,[]).append((row['line_num'],text,score))
    except (ValueError,KeyError,TypeError):return {'ok':False,'error':'Invalid OCR confidence output.'}
    accepted=[];excluded=0
    for words in paragraphs.values():
        if min(x[2] for x in words)<60 or sum(x[2] for x in words)/len(words)<80:
            excluded+=1;continue
        lines={}
        for line,text,score in words:lines.setdefault(line,[]).append(text)
        text='\n'.join(' '.join(words) for words in lines.values())
        if text_issues(text,check_math=False):excluded+=1;continue
        accepted.append(text)
    text='\n\n'.join(accepted)
    stats={'mean_confidence':round(sum(all_scores)/len(all_scores),1) if all_scores else 0,
           'accepted_paragraphs':len(accepted),'excluded_paragraphs':excluded,'words':len(all_scores)}
    if sum(c.isalpha() for c in text)<40:
        return dict(stats,ok=False,error='OCR found insufficient confident text. The source page needs manual review.')
    return dict(stats,ok=True,text=text)
