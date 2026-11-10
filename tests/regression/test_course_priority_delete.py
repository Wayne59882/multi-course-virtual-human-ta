"""Student priority over background course work, delete/cancel at any time, deferred figures.

Offline: a fake streaming Ollama, generated PDFs and stand-in model calls.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
PRIORITY = tempfile.mkdtemp(prefix='vq-priority-')
os.environ['VQ_MODEL_PRIORITY_DIR'] = PRIORITY
os.environ['VQ_BACKGROUND_PREEMPT'] = 'immediate'
os.environ['VQ_PDF_OCR'] = 'off'
os.environ['VQ_PDF_VISION'] = 'off'
os.environ['VQ_PDF_VISION_FIGURES'] = 'background'  # exercise the optional deferred mode
import ollama_cancel as oc
from model_lock import SharedModelLock

try:
    import fitz
except ImportError:
    fitz = None


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode())
        self.server.calls.append(self.path)
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        parts = ['short'] if self.path == '/short' else ['token'] * 120
        try:
            for index, part in enumerate(parts):
                self.wfile.write((json.dumps({'message': {'content': part}, 'done': index == len(parts) - 1}) + '\n').encode())
                self.wfile.flush()
                if index == 0:
                    self.server.started.set()
                time.sleep(0.02)
        except OSError:
            pass


def check_priority():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), FakeOllama)
    srv.calls = [];srv.started = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'http://127.0.0.1:%d' % srv.server_port
    payload = {'model': 'fake', 'messages': [], 'stream': False}
    lock = SharedModelLock(str(Path(PRIORITY) / 'model.lock'))
    state = {'attempts': 0}

    def background():
        with oc.BackgroundWork():
            while True:
                state['attempts'] += 1
                try:
                    with lock:
                        state['result'] = oc.stream_chat_json(url + '/long', payload, 10)
                    return
                except oc.BackgroundPreempted:
                    continue
    worker = threading.Thread(target=background, daemon=True);worker.start()
    try:
        assert srv.started.wait(3), 'background generation never started'
        began = time.monotonic()
        with oc.CancellationScope():
            with lock:
                waited = time.monotonic() - began
                assert oc.priority_waiting(), 'student claim must be visible while answering'
                short = oc.stream_chat_json(url + '/short', payload, 5)
        assert short['message']['content'] == 'short'
        assert waited < 1.5, 'student waited %.2fs behind background work' % waited
        assert not oc.priority_waiting(), 'student claim must be removed after the answer'
        worker.join(15)
        assert not worker.is_alive(), 'preempted background work did not resume'
        assert state['attempts'] >= 2 and state['result']['message']['content'] == 'token' * 120
        # A claim left by a process that no longer exists is ignored and removed.
        stale = Path(PRIORITY) / '999999-stale'
        stale.write_text('0')
        assert not oc.priority_waiting() and not stale.exists()
        # One context size for every Ollama call: no runner reload between tasks.
        assert oc.OLLAMA_NUM_CTX >= 8192
    finally:
        srv.shutdown();srv.server_close()
    return waited, state['attempts']


def make_pdf(path, pages, image_page=None):
    document = fitz.open()
    for number in range(pages):
        page = document.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 400),
            ('Chapter %d. Diffusion moves solutes down their concentration gradient across membranes. '
             'Facilitated diffusion uses carrier proteins and saturates at high solute concentration. '
             'Active transport consumes ATP and moves solutes against their gradient. ') % (number + 1), fontsize=11)
    if image_page is not None:
        pix = document[0].get_pixmap(matrix=fitz.Matrix(.5, .5))
        document[image_page].insert_image(fitz.Rect(40, 420, 560, 800), pixmap=pix)
    document.set_toc([[1, 'Membrane transport', 1], [1, 'Carriers', max(2, pages // 2 + 1)]])
    document.save(str(path));document.close()


def check_courses():
    import course_runtime as cr
    import pdf_ingestion as ingestion
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        cr.COURSE_ROOT = directory / 'courses';cr.STAGING_ROOT = cr.COURSE_ROOT / '_staging';cr.STAGING_ROOT.mkdir(parents=True)
        cr.EMBED_BASE = 'http://127.0.0.1:9'  # embedding fails fast; text search remains
        manager = cr.CourseManager()

        # 1) Figures on text-rich pages do not run vision during the build.
        original = directory / 'transport.pdf';make_pdf(original, 4, image_page=1)
        calls = []
        class Vision:
            fingerprint = 'fixture'
            def read(self, page):
                calls.append(page.number);return {'ok': False, 'error': 'fixture'}
        with fitz.open(str(original)) as doc:
            row = ingestion.inspect_page(doc[1], 2, vision=Vision())
        assert row.get('figure_pending') and row['status'] == 'readable' and not calls, row
        assert ingestion.FIGURES_MODE == 'background'
        previous = ingestion.FIGURES_MODE;ingestion.FIGURES_MODE = 'inline'
        try:
            with fitz.open(str(original)) as doc:
                ingestion.inspect_page(doc[1], 2, vision=Vision())
            assert calls == [1], 'inline mode keeps the R15 behavior'
        finally:
            ingestion.FIGURES_MODE = previous

        # 2) Delete while background preparation is running.
        stage = manager.inspect([('transport.pdf', original)], course_name='Transport')
        model_started = threading.Event()
        def slow_model(key, system, user, limit):
            model_started.set()
            for _ in range(200):  # ~20 s unless the course is deleted
                time.sleep(0.1);oc.background_check()
            return ''
        job = manager.start_build(stage['stage_id'], 'Transport', 'none', stage['units'], 'gemma', slow_model)
        for _ in range(100):
            if manager.job_status(job['id']).get('status') == 'done':break
            time.sleep(0.1)
        cid = job['course_id']
        assert manager.manifest(cid), manager.job_status(job['id'])
        assert manager.manifest(cid)['figure_pages'], 'pages with figures are queued for background reading'
        assert model_started.wait(10), 'background preparation never called the model'
        began = time.monotonic()
        result = manager.delete_course(cid)
        assert result['ok'] and time.monotonic() - began < 2, 'delete must not wait for preparation'
        assert not manager.manifest(cid) and cid not in [c['course_id'] for c in manager.list_courses()]
        for _ in range(100):
            with manager.running_lock:
                busy = [item for item in manager.running if item[0] == cid]
            if not busy:break
            time.sleep(0.1)
        assert not busy, 'background work kept running after delete: %s' % busy
        time.sleep(0.5)
        assert not manager._course_dir(cid).exists(), 'a stopped worker recreated the deleted course'

        # 3) Cancel a build while pages are still being read; the upload can be built again.
        big = directory / 'big.pdf';make_pdf(big, 40)
        stage2 = manager.inspect([('big.pdf', big)], course_name='Big')
        real_iter = cr.iter_pages
        def slow_iter(*args, **kwargs):
            for row in real_iter(*args, **kwargs):
                time.sleep(0.15);yield row
        cr.iter_pages = slow_iter
        try:
            job2 = manager.start_build(stage2['stage_id'], 'Big', 'none', stage2['units'], 'gemma', slow_model)
            for _ in range(50):
                if (manager.job_status(job2['id']).get('processed_pages') or 0) >= 5:break
                time.sleep(0.1)
            cancelled = manager.cancel_build(job2['id'])
            assert cancelled['status'] == 'cancelled'
            for _ in range(60):
                if manager.job_status(job2['id']).get('status') == 'cancelled' and not manager._course_dir(job2['course_id']).exists():break
                time.sleep(0.1)
            assert manager.job_status(job2['id'])['status'] == 'cancelled'
            time.sleep(0.6)
            assert not manager._course_dir(job2['course_id']).exists() and not manager.manifest(job2['course_id'])
        finally:
            cr.iter_pages = real_iter
        again = manager.start_build(stage2['stage_id'], 'Big', 'none', stage2['units'], 'gemma', slow_model)
        assert again['id'] != job2['id'] and again['course_id'] != job2['course_id'], 'a cancelled upload must be buildable again'
        for _ in range(100):
            if manager.job_status(again['id']).get('status') == 'done':break
            time.sleep(0.1)
        manager.delete_course(again['course_id'])


def main():
    waited, attempts = check_priority()
    if fitz is None:
        print('SKIP course deletion fixtures: PyMuPDF is not installed')
    else:
        check_courses()
    print('[OK] student answer waited %.2fs behind background work (preempted %d time%s); delete/cancel stop background work; figures deferred'
          % (waited, attempts - 1, '' if attempts == 2 else 's'))


if __name__ == '__main__':
    main()
