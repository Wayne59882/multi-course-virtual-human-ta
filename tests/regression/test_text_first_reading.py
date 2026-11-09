"""Text-first course reading (R18c default): the course opens after text is read,
the vision model reads slide images afterwards, and the R15 full reading stays available.

Offline: generated slide PDFs, stand-in OCR and vision readers.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get('VHTA_TEST_APP') or ROOT / 'app')
sys.path.insert(0, str(APP))
os.environ.pop('VQ_PDF_VISION_FIGURES', None)
import fitz  # noqa: E402
import pdf_ingestion as ingestion  # noqa: E402

assert ingestion.FIGURES_MODE == 'fast' and ingestion.reading_mode(None) == 'fast' and ingestion.reading_mode('bogus') == 'fast'
assert ingestion.reading_mode('inline') == 'inline'


def slide(doc, title=None, image=True, body=None):
    page = doc.new_page(width=960, height=540)
    if title:
        page.insert_text((40, 60), title, fontsize=36)
    if body:
        page.insert_text((60, 140), body, fontsize=20)
    if image:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 140), 0)
        pix.set_rect(pix.irect, (40, 120, 200))
        page.insert_image(fitz.Rect(500, 160, 900, 440), pixmap=pix)
    return page


class Vision:
    fingerprint = 'vision-fixture'
    def __init__(self):
        self.calls = []
    def read(self, page):
        self.calls.append(page.number + 1)
        return {'ok': True, 'text': 'Vision reading of slide %d: hybridization probe binds the target strand.' % (page.number + 1)}
    def probe(self):
        return True


class OCR:
    fingerprint = 'ocr-fixture'; requested = 'eng'
    def __init__(self, text):
        self.text, self.calls = text, []
    def read(self, page):
        self.calls.append(page.number + 1)
        return {'ok': bool(self.text), 'text': self.text, 'error': '' if self.text else 'no text found'}


doc = fitz.open()
slide(doc, 'Southern blot', body='DNA fragments are separated and transferred to a membrane.')   # text + image
slide(doc, image=True)                                                                            # image only
slide(doc, image=True)                                                                            # image only, OCR finds nothing

# 1. Text-first: slide text is used at once and the image is queued, no vision call.
vision, ocr = Vision(), OCR('Northern blot detects RNA molecules after gel electrophoresis and transfer.')
row = ingestion.inspect_page(doc[0], 1, ocr=ocr, vision=vision)
assert row['status'] == 'readable' and row.get('figure_pending') and 'Southern blot' in row['text'] and not vision.calls and not ocr.calls, row
# 1b. A slide whose only text is its title also opens at once; its image follows.
titled = fitz.open(); slide(titled, 'Southern blot')
row = ingestion.inspect_page(titled[0], 1, ocr=OCR(''), vision=vision)
assert row.get('figure_pending') and 'Southern blot' in row['text'] and not vision.calls, row
# 2. Image-only slide: OCR now, vision later.
row = ingestion.inspect_page(doc[1], 2, ocr=ocr, vision=vision)
assert row['extraction_method'] == 'local_ocr' and row.get('figure_pending') and 'Northern blot' in row['text'] and not vision.calls, row
# 3. Nothing readable without vision: read by the vision model during the build, as before.
row = ingestion.inspect_page(doc[2], 3, ocr=OCR(''), vision=vision)
assert vision.calls == [3] and not row.get('figure_pending'), (row, vision.calls)
# 4. The R15 full reading still reads every slide image during the build.
full = Vision()
ingestion.inspect_page(doc[0], 1, ocr=ocr, vision=full, mode='inline')
assert full.calls == [1], 'inline mode keeps the R15 method'

# 5. Cached text-first pages are reused by text-first builds but not by a full (inline) build.
with tempfile.TemporaryDirectory() as temporary:
    cache = Path(temporary)
    ocr = OCR('Northern blot detects RNA molecules after gel electrophoresis and transfer.')
    first = list(ingestion.iter_pages(doc, 1, 2, cache, 'deck', ocr=ocr, vision=Vision()))
    assert all(r.get('figure_pending') for r in first) and ocr.calls == [2]
    again = Vision(); list(ingestion.iter_pages(doc, 1, 2, cache, 'deck', ocr=ocr, vision=again))
    assert ocr.calls == [2] and not again.calls, 'text-first pages come from the cache'
    complete = Vision(); list(ingestion.iter_pages(doc, 1, 2, cache, 'deck', ocr=ocr, vision=complete, mode='inline'))
    assert complete.calls, 'a full build reads the images instead of reusing text-first pages'

# 6. Course build: opens text-first, then vision passages reach course Q&A in batches.
import course_runtime as cr  # noqa: E402
with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    cr.COURSE_ROOT = directory / 'courses'; cr.STAGING_ROOT = cr.COURSE_ROOT / '_staging'; cr.STAGING_ROOT.mkdir(parents=True)
    cr.EMBED_BASE = 'http://127.0.0.1:9'
    deck = fitz.open()
    for n in range(1, 13):
        slide(deck, 'Hybridization slide %d' % n, body='Probe and target strands pair by complementary bases in step %d.' % n)
    pdf = directory / 'deck.pdf'; deck.save(str(pdf))
    manager = cr.CourseManager()
    stage = manager.inspect([('deck.pdf', pdf)], course_name='Genomics')
    readers = []
    class CourseVision(Vision):
        def __init__(self):
            super().__init__(); readers.append(self)
    cr.LocalVision = CourseVision
    import pdf_ingestion
    pdf_ingestion.LocalVision = CourseVision
    manager._start_preparation = lambda *args, **kwargs: None   # run the figure task by hand below
    job = manager.start_build(stage['stage_id'], 'Genomics', 'none', stage['units'], 'gemma', lambda *a: '')
    assert job['reading'] == 'fast'
    import time
    for _ in range(200):
        if manager.job_status(job['id']).get('status') in ('done', 'error'): break
        time.sleep(0.05)
    assert manager.job_status(job['id'])['status'] == 'done', manager.job_status(job['id'])
    cid = job['course_id']; manifest = manager.manifest(cid)
    assert len(manifest['figure_pages']) == 12 and not any(r.calls for r in readers), 'no slide image was read before the course opened'
    chunks = lambda: [json.loads(x) for x in (manager._course_dir(cid) / 'chunks.jsonl').read_text(encoding='utf-8').splitlines() if x]
    assert chunks() and not [c for c in chunks() if c['extraction_method'] == 'local_vision_figure']
    seen = []
    real_index = manager._index_missing_chunks
    def counting_index(course):
        seen.append(len([c for c in chunks() if c['extraction_method'] == 'local_vision_figure'])); return real_index(course)
    manager._index_missing_chunks = counting_index
    cr.FIGURE_BATCH = 5
    manager._prepare_figures(cid, 'gemma', lambda *a: '')
    figures = [c for c in chunks() if c['extraction_method'] == 'local_vision_figure']
    assert len({c['pdf_page'] for c in figures}) == 12, figures
    assert seen[:2] == [5, 10] and seen[-1] == 12, 'passages are added every few pages, not only at the end: %s' % seen
    assert manager.manifest(cid)['preparation']['figures']['status'] == 'ready'
    # 7. Image-only slides opened with OCR: once the vision model has read a page,
    #    its reviewed reading replaces the interim OCR text (also in the semantic index).
    scans = fitz.open()
    for n in range(1, 4):
        slide(scans, image=True)
    scan_pdf = directory / 'scans.pdf'; scans.save(str(scan_pdf))
    class CourseOCR(OCR):
        def __init__(self):
            super().__init__('OCR guess for this slide: Nothern blot detets RNA molecules after gel electrophoresis.')
    pdf_ingestion.LocalOCR = CourseOCR
    stage = manager.inspect([('scans.pdf', scan_pdf)], course_name='Scans')
    job = manager.start_build(stage['stage_id'], 'Scans', 'none', stage['units'], 'gemma', lambda *a: '')
    for _ in range(200):
        if manager.job_status(job['id']).get('status') in ('done', 'error'): break
        time.sleep(0.05)
    cid = job['course_id']
    before = chunks()
    ocr_ids = [c['id'] for c in before if c['extraction_method'] == 'local_ocr']
    assert len({c['pdf_page'] for c in before if c['extraction_method'] == 'local_ocr'}) == 3, before
    import sqlite3, struct
    vp = manager._course_dir(cid) / 'vector_index.sqlite3'
    conn = sqlite3.connect(str(vp))
    conn.execute('CREATE TABLE vectors (idx INTEGER PRIMARY KEY, chunk_id TEXT, unit_id TEXT, source TEXT, page INTEGER, text TEXT, dim INTEGER, vec BLOB)')
    conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)'); conn.execute("INSERT INTO meta VALUES ('count',?)", (str(len(before)),))
    conn.executemany('INSERT INTO vectors VALUES (?,?,?,?,?,?,?,?)', [(i + 1, c['id'], c['unit_id'], c['source'], c['pdf_page'], c['text'], 1, struct.pack('f', 1.0)) for i, c in enumerate(before)])
    conn.commit(); conn.close()
    manager._index_missing_chunks = lambda course: 0
    manager._prepare_figures(cid, 'gemma', lambda *a: '')
    after = chunks()
    assert not [c for c in after if c['extraction_method'] == 'local_ocr'], 'interim OCR text was not replaced'
    assert {c['pdf_page'] for c in after if c['extraction_method'] == 'local_vision_figure'} == {1, 2, 3}
    conn = sqlite3.connect(str(vp)); left = {r[0] for r in conn.execute('SELECT chunk_id FROM vectors')}; conn.close()
    assert not left & set(ocr_ids), 'replaced OCR passages were left in the semantic index'
    assert manager.manifest(cid)['preparation']['figures']['ocr_pages_replaced'] == 3
    # 8. A source re-read stopped by a server restart is resumed (it used to show its last page forever).
    def interrupted(m):
        m['preparation'].update({'sources': {'status': 'preparing', 'message': 'Reading saved PDF: page 2'}}); m['figure_pages'] = []
    manager._update_manifest(cid, interrupted)
    messages = []
    real_update = manager._update_manifest
    def watching(course, change):
        result = real_update(course, change)
        messages.append(str(((result or {}).get('preparation') or {}).get('sources', {}).get('message')))
        return result
    manager._update_manifest = watching
    started = []
    manager._start_preparation = lambda course, *args, **kwargs: started.append(course)
    restarted = cr.CourseManager.__new__(cr.CourseManager); restarted.__dict__.update(manager.__dict__)
    restarted.running = set()
    restarted.resume_pending(lambda *a: '')
    for _ in range(200):
        if (restarted.manifest(cid)['preparation'].get('sources') or {}).get('status') != 'preparing': break
        time.sleep(0.05)
    sources = restarted.manifest(cid)['preparation']['sources']
    assert sources['status'] == 'ready', sources
    assert any(' / 3 pages)' in m for m in messages), messages
    assert cid in started, 'preparation continues after the re-read'
    assert len(restarted.manifest(cid)['figure_pages']) == 3, 'pages re-read text-first are queued for background image reading'
print('[OK] text-first reading: slide text or OCR opens the course, images are read afterwards and reach Q&A in batches; '
      'reviewed readings replace interim OCR text; interrupted source re-reads resume with page counts; pages with no text are still read by vision during the build; the R15 full reading remains selectable')
