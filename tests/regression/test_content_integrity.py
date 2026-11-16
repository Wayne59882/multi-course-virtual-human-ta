import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"app"))
"""Shared integrity regression across subjects, text layers and generated content."""
import ast
import json
import tempfile
from pathlib import Path
import content_integrity as ci
import course_runtime as cr
import exam_questions as eq
import reference_server as rs

samples={
 'biology':'CO₂ + H₂O → glucose; Ca²⁺; pH 7.40; V_T is a variable, not a word replacement.',
 'chemistry':'Na⁺ + Cl⁻; H₂SO₄; α + β; 25 °C; 5 µg/mL; 0.5 μmol/L; ΔG ≤ 0.',
 'physics':'V_A = 12 V; I = 2 A; R = 6 Ω; E = mc²; x-\ny; ± 0.2 m/s.',
 'economics':'Inflation ≥ 2.5%; Table 2 shows GDP = 10 million USD; $20 per unit.',
 'chinese':'本課程說明細胞膜的運輸機制。鈉離子 Na⁺ 與鈣離子 Ca²⁺ 的濃度不同，溫度為 37 °C，不能把上下標或單位刪除。',
 'computing':'PHP example: `$x=1;` and literal paths `C:\\new\\test`. Do not change identifiers.',
 'table':'Treatment | Dose | Response\nDrug A | 5 µg/mL | 20%\nDrug B | 10 µg/mL | 40%'
}
for subject,text in samples.items():
    assert not ci.text_issues(text),(subject,ci.text_issues(text))
    assert ci.clean_course_text(text)==text
    assert cr._safe_source_text(text)==text
    assert cr._lecture_evidence({},[{'id':'u1','text':text}])==text
    assert '\n'.join(cr._word_chunks(text))==text
assert cr._clean_text('\ue010')=='\ue010','a glyph code must not be guessed as a letter'
for broken in ['missing \ufffd text','private \ue010 glyph','astral \U000f0010 glyph','dose (cid:123) mg','cafÃ©','25 Â°C','bad\x08text']:
    assert ci.text_issues(broken),repr(broken)
    try:ci.clean_course_text(broken)
    except ci.ContentIntegrityError:pass
    else:raise AssertionError('corrupted text was accepted')
assert not ci.text_issues('□ Option 1 / □ Option 2'),'legitimate checkbox is ordinary text'
print('[OK] Biology, chemistry, physics, economics, Chinese, code and table text preserve their original symbols/units')

# Actual shared local-answer route must never apply the physiology sanitizer.
old=rs.course_model_call
def approved_review(user):
    payload=json.loads(user)
    return json.dumps({'approved':True,'task_in_scope':True,'paragraphs':[
        {'index':p['index'],'supported':True,'quote':payload['source']} for p in payload['paragraphs']]})
try:
    for subject,text in samples.items():
        rs.course_model_call=lambda srv,key,system,user,tokens,value=text:approved_review(user) if 'COURSE_GROUNDING_REVIEW' in system else value
        assert rs.course_local_answer(None,'gemma','Explain this concept.',text,subject)==text
    text=samples['physics']
    rs.course_model_call=lambda srv,key,system,user,tokens:approved_review(user) if 'COURSE_GROUNDING_REVIEW' in system else json.dumps({'verdict':'correct','corrected_answer':text})
    assert rs.review_course_answer(None,'gemma','Explain',text,'en-US','draft','report')[0]==text
finally:rs.course_model_call=old
print('[OK] Generic local Q&A preserves table references, variables, subscripts, unit symbols and programming identifiers')
# The local answer route follows the default mode: no sentence has to match the PDF word for word, a cut-off
# answer is asked again once, and an answer in the wrong language is rewritten once.
calls=[]
def scripted(replies):
    def call(srv,key,system,user,tokens):
        calls.append((system,user));return replies.pop(0)
    return call
try:
    rs.course_model_call=scripted(['這門課從轉錄體開始，說明基因如何被讀出並調控，之後談到基因體定序與分析方法。'])
    answer=rs.course_local_answer(None,'gemma','這整本在講什麼','Course: Genomics\nChapters in order:\n1. Transcriptome','Genomics','zh-TW')
    assert answer.startswith('這門課從轉錄體開始') and len(calls)==1,'one draft, no word-for-word source review'
    assert 'COURSE_GROUNDING_REVIEW' not in calls[0][0]
    calls.clear()
    rs.course_model_call=scripted(['The course covers genes and, therefore,','The course covers how genes are read out and regulated.'])
    assert rs.course_local_answer(None,'gemma','What is this course about?','material','Genomics','en-US')=='The course covers how genes are read out and regulated.' and len(calls)==2
    calls.clear()
    rs.course_model_call=scripted(['This course explains how genes are transcribed and regulated across the genome in several chapters.','這門課說明基因如何被轉錄與調控。'])
    assert rs.course_local_answer(None,'gemma','這整本在講什麼','material','Genomics','zh-TW')=='這門課說明基因如何被轉錄與調控。'
    calls.clear()
    rs.course_model_call=scripted(['broken \ufffd answer','這門課說明基因如何被轉錄與調控。'])
    assert rs.course_local_answer(None,'gemma','這整本在講什麼','material','Genomics','zh-TW')=='這門課說明基因如何被轉錄與調控。' and len(calls)==2,'a garbled answer is asked again once'
finally:rs.course_model_call=old
print('[OK] Uploaded-course local answers use the default flow: one draft, finished sentence, right language, no source-quote gate')


# Long Chinese paragraphs have no spaces: bounds must still hold and no text
# may disappear at the last chunk.
long=('細胞膜維持離子濃度差，並影響膜電位與物質交換。'*500)+'最後一行不可遺失。'
chunks=cr._word_chunks(long)
assert len(chunks)>1 and all(len(x)<=cr.MAX_EMBED_CHARS for x in chunks)
assert chunks[-1].endswith('最後一行不可遺失。')
assert all(any(word in chunk for chunk in chunks) for word in ['細胞膜','物質交換','最後一行'])
print('[OK] Character-bounded multilingual chunking retains final content without flattening rows')

# New and existing question-bank content share the same subject-neutral gate.
good={'stem':'How should the concentration of sodium ions be described in this chemistry experiment?',
      'choices':['Na⁺ at 5 µg/mL','Na⁺ at 10 µg/mL','Cl⁻ at 5 µg/mL','Cl⁻ at 10 µg/mL'],
      'answer':0,'explanation':'The concentration and unit are taken from the supplied experiment.'}
assert ci.question_text_ok(good)
for key in ('stem','explanation'):
    assert not ci.question_text_ok(dict(good,**{key:'invalid \ue010 character'}))
assert not ci.question_text_ok(dict(good,choices=['cafÃ©']+good['choices'][1:]))
with tempfile.TemporaryDirectory() as td:
    original=cr.COURSE_ROOT;cr.COURSE_ROOT=Path(td)
    try:
        manager=cr.CourseManager();cd=Path(td)/'chemistry';cd.mkdir()
        goodcard={'id':'good','source_statement':'chemistry','evidence_chunk_id':'c1','question':dict(good,id='q1',generation_method='exam-mcq-v5',source_review={'revision':'R15','support_quote':'chemistry concentration experiment'})}
        (cd/'chunks.jsonl').write_text(json.dumps({'id':'c1','text':'chemistry concentration experiment'})+'\n')
        badcard={'id':'bad','source_statement':'broken','question':dict(good,id='q2',stem='Encoding error: cafÃ©',generation_method='exam-mcq-v5')}
        cr._json_dump(cd/'question_banks.json',{'u1':[goodcard,badcard]})
        man={'course_id':'chemistry','question_generation':'exam-mcq-v5','units':[{'id':'u1','title':'Chemistry','script':'cafÃ©','script_status':'ready','question_count':2}],'preparation':{}}
        cr._json_dump(cd/'course_manifest.json',man)
        repaired=manager._quarantine_damaged_math(man)
        assert repaired['content_integrity_revision']=='R15'
        assert repaired['units'][0]['question_count']==1
        assert repaired['units'][0]['script_status']=='preparing'
        assert Path(repaired['math_repair']['backup']).joinpath('question_banks.json').exists()
    finally:cr.COURSE_ROOT=original
print('[OK] Damaged non-math content is backed up and excluded from active lectures/questions')

# A legacy PDF API and image-only page must behave explicitly as well.
class LegacyPage:
    def getText(self,kind):return 'Pressure = 760 mmHg. Temperature = 37 °C.'
    def getImageList(self):return []
assert ci.extract_page(LegacyPage(),7)['page']==7
class ScanPage:
    def getText(self,kind):return '12'
    def getImageList(self):return [(1,)]
try:ci.extract_page(ScanPage(),12)
except ci.ContentIntegrityError as error:assert '12' in str(error) and 'OCR' in str(error)
else:raise AssertionError('scanned source was silently skipped')
print('[OK] Legacy PDF API supported; scanned/image-only page reports page number and requires OCR/vision')

for filename in ('content_integrity.py','math_integrity.py','course_runtime.py','exam_questions.py','reference_server.py'):
    if __import__('sys').version_info>=(3,8):ast.parse(Path(filename).read_text(),feature_version=(3,6))
    else:ast.parse(Path(filename).read_text())
print('[OK] Shared modules remain Python 3.6 compatible')
