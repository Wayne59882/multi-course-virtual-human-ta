"""Scanned slides are recognised by several Tesseract processes at once during text-first
reading, and every page still gets its own text, in page order. A stand-in plays Tesseract."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
try:
    import fitz
except ImportError:
    print('SKIP parallel OCR: PyMuPDF is not installed')
    raise SystemExit(0)
os.environ['VQ_OCR_WORKERS'] = '4'
os.environ['VQ_CPU_WORKERS'] = '4'
# Isolated student-activity marker: other suites' students must not make this run 'busy'.
os.environ['VQ_MODEL_PRIORITY_DIR'] = tempfile.mkdtemp(prefix='vq-ocr-quiet-')
import pdf_ingestion  # noqa: E402
import pdf_ocr  # noqa: E402

SECONDS = 0.4


class StandInOCR(pdf_ocr.LocalOCR):
    def __init__(self):
        self.executable, self.tessdata, self.requested, self.languages, self.error = 'tesseract', '', 'eng', ['eng'], ''
        self.fingerprint = 'stand-in'
        self.lock = threading.Lock()
        self.running = self.most = 0
        self.rendered = []

    def render(self, page):
        assert threading.current_thread() is threading.main_thread(), 'pages render in the reading thread'
        self.rendered.append(page.number + 1)
        return ('page %d' % (page.number + 1)).encode()

    def recognize(self, png):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(SECONDS)
        with self.lock:
            self.running -= 1
        number = png.decode().split()[1]
        return {'ok': True, 'text': 'Scanned slide %s explains gene expression regulation in detail.' % number}


class NoVision:
    fingerprint = 'none'

    def read(self, page):
        raise AssertionError('text-first reading must not call the vision model for OCR-readable pages')


with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    picture = fitz.open()
    sample = picture.new_page(width=200, height=100)
    sample.draw_rect(sample.rect, color=(0, 0, 0), fill=(0.3, 0.5, 0.7))
    pixmap = sample.get_pixmap()
    document = fitz.open()
    pages = 12
    for number in range(pages):
        page = document.new_page()
        if number == 5:
            page.insert_text((72, 72), 'Slide six has its own searchable title text and bullets', fontsize=14)
        else:
            page.insert_image(page.rect, pixmap=pixmap)  # an image-only (scanned) slide
    ocr = StandInOCR()
    started = time.monotonic()
    rows = list(pdf_ingestion.iter_pages(document, 1, pages, folder / 'cache', 'deck', ocr=ocr, vision=NoVision(), mode='fast'))
    elapsed = time.monotonic() - started
    assert [row['page'] for row in rows] == list(range(1, pages + 1))
    for row in rows:
        if row['page'] == 6:
            assert row.get('extraction_method') != 'local_ocr' and 'searchable title' in row['text']
        else:
            assert row['extraction_method'] == 'local_ocr' and 'slide %d ' % row['page'] in row['text'], row
            assert row['figure_pending']
    expected=pdf_ingestion.ocr_workers()
    assert ocr.most==expected, 'OCR did not respect the allocated CPU work budget'
    if expected>1:assert elapsed < (pages - 1) * SECONDS * .9, 'parallel OCR took %.1fs' % elapsed
    assert 6 not in ocr.rendered, 'a page with a text layer is not OCR-ed'
    # Cached pages are not read again.
    again = StandInOCR()
    list(pdf_ingestion.iter_pages(document, 1, pages, folder / 'cache', 'deck', ocr=again, vision=NoVision(), mode='fast'))
    assert again.rendered == []
    # While students are asking, OCR drops to the busy budget (2).
    import ollama_cancel
    ollama_cancel.note_student_activity()
    busy = StandInOCR()
    list(pdf_ingestion.iter_pages(document, 1, pages, folder / 'cache-busy', 'deck', ocr=busy, vision=NoVision(), mode='fast'))
    assert busy.most == min(2, expected), 'OCR did not drop to the busy budget while students were asking (%s)' % busy.most
    os.unlink(ollama_cancel.ACTIVITY_PATH)
    # One worker: the original page-by-page reading.
    os.environ['VQ_OCR_WORKERS'] = '1'
    single = StandInOCR()
    list(pdf_ingestion.iter_pages(document, 1, 3, folder / 'cache-single', 'deck', ocr=single, vision=NoVision(), mode='fast'))
    assert single.most == 1
print('[OK] parallel OCR: scanned pages recognised %d at a time (%.1fs for %d pages instead of %.1fs), each page keeps its own text and order' % (ocr.most, elapsed, pages - 1, (pages - 1) * SECONDS))
