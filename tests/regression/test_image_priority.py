"""Background course work takes turns on the shared model by rank instead of all courses at
once: builds, then text preparation, then image reading; an open course, then the newest.
A long page re-read does not hold the build slot that new courses need. No model is called."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
os.environ['VQ_MODEL_PRIORITY_DIR'] = tempfile.mkdtemp(prefix='vq-rank-priority-')
import ollama_cancel as oc  # noqa: E402
from model_lock import SharedModelLock  # noqa: E402
import course_runtime as cr  # noqa: E402

lock_path = os.path.join(tempfile.mkdtemp(prefix='vq-rank-lock-'), 'model.lock')
order = []
release = threading.Event()


def worker(name, rank, started, hold=None):
    with oc.BackgroundWork(rank=lambda: rank):
        started.set()
        with SharedModelLock(lock_path):
            order.append(name)
            if hold is not None:
                hold.set()
                release.wait(5)


def start(name, rank, hold=None):
    started = threading.Event()
    thread = threading.Thread(target=worker, args=(name, rank, started, hold), daemon=True)
    thread.start()
    started.wait(2)
    time.sleep(0.25)
    return thread


holding = threading.Event()
threads = [start('running call', (1, 1, 0), holding)]
assert holding.wait(2)
# Waiting in arrival order: an old course's images, an old course's lecture, the newest
# course's questions, the open course's images, a build.
threads += [start('old images', (2, 1, -100)), start('old lecture', (1, 1, -100)),
            start('newest questions', (1, 1, -300)), start('open course images', (2, 0, -100)),
            start('build', (0, 1, -50))]
release.set()
for thread in threads:
    thread.join(5)
assert order == ['running call', 'build', 'newest questions', 'old lecture', 'open course images', 'old images'], order

# The course manager ranks its own work this way (in a throw-away course folder).
cr.COURSE_ROOT = Path(tempfile.mkdtemp(prefix='vq-rank-courses-'))
cr.STAGING_ROOT = cr.COURSE_ROOT / '_staging'
cr.STAGING_ROOT.mkdir()
manager = cr.CourseManager()
manager.created.update({'old': time.time() - 7200, 'new': time.time() - 600})  # both uploaded recently
assert manager._rank('new', 'text') < manager._rank('old', 'text') < manager._rank('new', 'image')
assert manager._rank('old', 'build') < manager._rank('new', 'text')
manager.viewed('old')
assert manager._rank('old', 'text') < manager._rank('new', 'text') and manager._rank('old', 'image') > manager._rank('new', 'text')
runtime = (ROOT / 'app/course_runtime.py').read_text(encoding='utf-8')
assert "self._background(cid,'image' if task[1]=='figures' else 'text')" in runtime
assert "with self._background(cid,'image'):self._repair_sources(cid)" in runtime
assert 'self.build_slots,self._background(cid):self._repair_sources' not in runtime
assert "with self._background(course_id,'build'):" in runtime
assert 'COURSES.viewed(cid)' in (ROOT / 'app/reference_server.py').read_text(encoding='utf-8')
print('[OK] background queue: builds, then lecture scripts/questions/search, then images; the open course, then the newest, goes first; page re-reads do not hold the build slot')
