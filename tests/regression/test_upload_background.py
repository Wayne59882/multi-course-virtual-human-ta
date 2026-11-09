"""A long PDF is inspected in the background: the last upload chunk answers at once and the
page polls the upload status, so no request outlives the 100-second proxy limit. Larger
(8 MB) chunks are accepted, and builds in progress are listed for any browser."""
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
    print('SKIP background inspection: PyMuPDF is not installed')
    raise SystemExit(0)
os.environ['VQ_PDF_OCR'] = 'off'
os.environ['VQ_PDF_VISION'] = 'off'
import course_runtime as cr  # noqa: E402

with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    cr.COURSE_ROOT = folder / 'courses'
    cr.STAGING_ROOT = cr.COURSE_ROOT / '_staging'
    cr.STAGING_ROOT.mkdir(parents=True)
    (cr.COURSE_ROOT / '_jobs').mkdir()
    document = fitz.open()
    for number in range(30):
        page = document.new_page()
        page.insert_text((72, 72), 'Gene expression lecture slide %d with readable teaching text.' % (number + 1), fontsize=12)
    pdf = folder / 'Genomics.pdf'
    document.save(str(pdf))
    data = pdf.read_bytes()
    manager = cr.CourseManager()
    release = threading.Event()
    real_inspect = manager.inspect

    def slow_inspect(*args, **kwargs):
        release.wait(5)  # a long PDF's outline is still being read
        return real_inspect(*args, **kwargs)
    manager.inspect = slow_inspect
    middle = len(data) // 2
    first = manager.upload_chunk('stage-bg', 'file-1', 'Genomics.pdf', len(data), 0, data[:middle], background=True)
    assert first == {'offset': middle, 'complete': False, 'stage_id': 'stage-bg'}
    started = time.monotonic()
    last = manager.upload_chunk('stage-bg', 'file-1', 'Genomics.pdf', len(data), middle, data[middle:], background=True)
    assert time.monotonic() - started < 1 and last['inspecting'] and not last['complete'], last
    status = manager.upload_status('stage-bg', 'file-1', background=True)
    assert status['inspecting'] and not status['complete']
    release.set()
    for _ in range(100):
        status = manager.upload_status('stage-bg', 'file-1', background=True)
        if status['complete']:
            break
        time.sleep(0.05)
    assert status['complete'] and status['inspection']['units'], status
    assert len(manager.inspecting) == 0
    # A failed inspection is reported instead of waiting forever.
    manager.inspect = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('outline unreadable'))
    manager.upload_chunk('stage-bad', 'file-2', 'Bad.pdf', len(data), 0, data, background=True)
    for _ in range(100):
        try:
            manager.upload_status('stage-bad', 'file-2', background=True)
        except ValueError as error:
            assert 'outline unreadable' in str(error)
            break
        time.sleep(0.05)
    else:
        raise AssertionError('inspection error was not reported')
    # 8 MB chunks fit; anything over the limit is refused.
    assert cr.CHUNK_LIMIT >= 8 * 1024 * 1024
    try:
        manager.upload_chunk('stage-big', 'file-3', 'Big.pdf', cr.CHUNK_LIMIT + 2, 0, b'x' * (cr.CHUNK_LIMIT + 1))
        raise AssertionError('oversized chunk accepted')
    except ValueError:
        pass
    # Builds in progress are listed from the server, newest first; finished ones are not.
    now = time.time()
    for job_id, status_value, created in [('old-done', 'done', now - 50), ('running', 'running', now - 10), ('queued', 'queued', now - 5), ('failed-long-ago', 'error', now - 200000)]:
        cr._json_dump(cr.COURSE_ROOT / '_jobs' / (job_id + '.json'), {'id': job_id, 'status': status_value, 'course_name': job_id, 'progress': 40, 'message': 'Reading page 3', 'created_at': created, 'updated_at': created})
    assert [row['id'] for row in manager.active_builds()] == ['queued', 'running']
    assert all('files' in row and 'page_methods' in row for row in manager.active_builds())
controller = (ROOT / 'app/web/shared/js/course/course-controller.js').read_text(encoding='utf-8')
assert 'offset+8*1024*1024' in controller and 'waitForInspection(' in controller and "'/api/reference/course-builds'" in controller
assert "DRAFT_KEY='current:'+id" in controller and 'v241-build-cancel' in controller
# The browser must not fill a saved login into the course name or a saved password into API key boxes.
assert 'id="v241CourseName" type="text" name="vq-course-title" autocomplete="off"' in controller
assert 'id="cloudApiKeyInput" type="password" autocomplete="new-password"' in (ROOT / 'app/web/reference/index.html').read_text(encoding='utf-8')
print('[OK] background inspection: the last chunk answers at once, the page polls until units are found, errors are reported; 8 MB chunks; builds in progress listed')
