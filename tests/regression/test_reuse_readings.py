"""Uploading the same PDF again reuses every page already read (also readings an earlier upload
left in its own folder), and background work pauses on courses nobody has opened for days and
resumes when someone opens them. No model is called."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
os.environ['VQ_MODEL_PRIORITY_DIR'] = tempfile.mkdtemp(prefix='vq-reuse-')
os.environ['VQ_OCR_WORKERS'] = '1'
try:
    import fitz
except ImportError:
    fitz = None
import course_runtime as cr  # noqa: E402
import ollama_cancel as oc  # noqa: E402
from model_lock import SharedModelLock  # noqa: E402
import pdf_ingestion  # noqa: E402


class CountingOCR:
    fingerprint = 'ocr-now'
    requested = 'eng'
    error = ''

    def __init__(self):
        self.calls = 0

    def read(self, page):
        self.calls += 1
        return {'ok': True, 'text': 'Recovered scanned slide text about DNA microarray hybridization and probes.'}


class Vision:
    fingerprint = 'vision-1'

    def __init__(self):
        self.calls = 0

    def read(self, page):
        self.calls += 1
        return {'ok': True, 'text': 'Reviewed reading of the slide figure.'}


with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    cr.COURSE_ROOT = folder / 'courses'
    cr.STAGING_ROOT = cr.COURSE_ROOT / '_staging'
    cr.STAGING_ROOT.mkdir(parents=True)
    if fitz is not None:
        picture = fitz.open()
        sample = picture.new_page(width=200, height=100)
        sample.draw_rect(sample.rect, color=(0, 0, 0), fill=(0.2, 0.4, 0.6))
        pixmap = sample.get_pixmap()
        document = fitz.open()
        for number in range(6):
            page = document.new_page()
            page.insert_image(page.rect, pixmap=pixmap)
        # An earlier upload of this PDF read it into its own stage folder.
        old_stage = cr.STAGING_ROOT / 'stage-old'
        first = CountingOCR()
        list(pdf_ingestion.iter_pages(document, 1, 6, old_stage / 'page_cache', 'sha-genomics', ocr=first, vision=Vision(), mode='fast'))
        assert first.calls == 6
        # The same PDF uploaded again (new stage, another teacher): nothing is read again.
        new_stage = cr.STAGING_ROOT / 'stage-new'
        shared = cr._shared_pages(new_stage, 'sha-genomics')
        assert shared == cr.COURSE_ROOT / '_page_cache'
        again = CountingOCR()
        rows = list(pdf_ingestion.iter_pages(document, 1, 6, shared, 'sha-genomics', ocr=again, vision=Vision(), mode='fast'))
        assert again.calls == 0 and len(rows) == 6 and all(row['extraction_method'] == 'local_ocr' for row in rows)
        # A different PDF is read normally.
        other = CountingOCR()
        list(pdf_ingestion.iter_pages(document, 1, 2, cr._shared_pages(new_stage, 'sha-other'), 'sha-other', ocr=other, vision=Vision(), mode='fast'))
        assert other.calls == 2
        # A page the vision model already read and reviewed is kept even after OCR changed.
        cache = next((cr.COURSE_ROOT / '_page_cache').glob('*/000001.json'))
        row = json.loads(cache.read_text(encoding='utf-8'))
        row.update({'extraction_method': 'local_vision', 'image_content_read': True, 'ocr_config': 'ocr-before', 'figure_pending': False,
                    'text': 'Reviewed reading of the slide figure.'})
        cache.write_text(json.dumps(row), encoding='utf-8')
        vision = Vision()
        changed = CountingOCR()
        changed.fingerprint = 'ocr-installed-later'
        rows = list(pdf_ingestion.iter_pages(document, 1, 1, cr.COURSE_ROOT / '_page_cache', 'sha-genomics', ocr=changed, vision=vision, mode='fast'))
        assert changed.calls == 0 and vision.calls == 0 and rows[0]['extraction_method'] == 'local_vision'

    # Courses nobody has opened for three days pause; opening resumes; new courses never pause.
    manager = cr.CourseManager()
    now = time.time()
    manager.created.update({'old-test': now - 10 * 86400, 'new-upload': now - 3600})
    assert manager.dormant('old-test') and not manager.dormant('new-upload')
    assert manager._rank('old-test', 'text') == oc.PAUSED and manager._rank('old-test', 'build') != oc.PAUSED
    manager.viewed('old-test')
    assert not manager.dormant('old-test') and manager._rank('old-test', 'text') != oc.PAUSED
    # Views are remembered across a restart.
    assert json.loads((cr.COURSE_ROOT / '_views.json').read_text(encoding='utf-8'))['old-test'] > now - 5
    assert 'old-test' in cr.CourseManager().views

    # A paused course's call waits even with the model free, and goes as soon as it is opened.
    lock_path = os.path.join(tempfile.mkdtemp(prefix='vq-reuse-lock-'), 'model.lock')
    state = {'rank': oc.PAUSED}
    ran = threading.Event()

    def paused_course():
        with oc.BackgroundWork(rank=lambda: state['rank']):
            with SharedModelLock(lock_path):
                ran.set()
    thread = threading.Thread(target=paused_course, daemon=True)
    thread.start()
    assert not ran.wait(0.8), 'a paused course used the model'
    other_ran = []
    with oc.BackgroundWork(rank=lambda: (1, 1, 0)):
        with SharedModelLock(lock_path):
            other_ran.append(True)  # other courses are not held up by a paused one
    state['rank'] = (1, 0, 0)  # someone opened the course
    assert ran.wait(3) and other_ran
runtime = (ROOT / 'app/course_runtime.py').read_text(encoding='utf-8')
assert runtime.count('_shared_pages(sd,') == 2 and "COURSE_ROOT/'_figure_cache'" in runtime
print('[OK] reuse and pause: the same PDF is not read again (also earlier stage readings), reviewed vision readings survive an OCR change; unopened courses pause their background work and resume when opened')
