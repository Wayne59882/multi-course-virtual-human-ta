"""One CPU-heavy work budget shared by this deployment's processes.

OCR and course embeddings use up to VQ_CPU_WORKERS slots (default 4) while no
student is asking, and VQ_CPU_WORKERS_BUSY slots (default 2) while students
are active, so reading scanned pages never slows their answers. Both are
capped by the CPUs this process may use. This cannot reserve resources
against other users on the host. All services must use the same settings and
VQ_CPU_SLOT_DIR. GPU inference is separate.
"""
import functools
import os
from pathlib import Path
import tempfile
import threading
import time
from contextlib import contextmanager
from work_steps import metric
try:
    import fcntl
except ImportError:
    fcntl = None

_LOCAL = threading.local()
_FALLBACK = threading.BoundedSemaphore(1)


def available_cpus():
    count = float(len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 1)
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            count = min(count, float(quota) / float(period))
    except (OSError, ValueError):
        try:
            root = Path('/sys/fs/cgroup/cpu')
            quota = float((root / 'cpu.cfs_quota_us').read_text())
            if quota > 0:
                count = min(count, quota / float((root / 'cpu.cfs_period_us').read_text()))
        except (OSError, ValueError):
            pass
    return max(1, int(count))


def _requested(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return 1


def cpu_workers_idle():
    """Most CPU-heavy tasks at once (while no student is asking)."""
    available = available_cpus()
    return max(1, min(_requested('VQ_CPU_WORKERS', 4), available))


def cpu_workers():
    """CPU-heavy tasks allowed right now: fewer while students are active."""
    idle = cpu_workers_idle()
    try:
        from ollama_cancel import student_recently_active
        busy = student_recently_active(float(os.environ.get('VQ_CPU_BUSY_WINDOW_SEC', '60')))
    except Exception:
        busy = False
    if not busy:
        return idle
    return max(1, min(idle, _requested('VQ_CPU_WORKERS_BUSY', 2)))


@contextmanager
def cpu_slot():
    if getattr(_LOCAL, 'depth', 0):
        _LOCAL.depth += 1
        try:
            yield
        finally:
            _LOCAL.depth -= 1
        return
    started = time.monotonic()
    stream = None
    if fcntl is None:
        _FALLBACK.acquire()
    else:
        directory = Path(os.environ.get('VQ_CPU_SLOT_DIR',
                         str(Path(tempfile.gettempdir()) / ('mc-vhta-cpu-' + str(os.getuid())))))
        directory.mkdir(parents=True, exist_ok=True)
        while stream is None:
            for index in range(cpu_workers()):
                candidate = (directory / ('%d.lock' % index)).open('a+')
                try:
                    fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    stream = candidate
                    break
                except BlockingIOError:
                    candidate.close()
            if stream is None:
                time.sleep(.05)
    _LOCAL.depth = 1
    try:
        metric('cpu_queue_seconds', time.monotonic() - started)
        yield
    finally:
        _LOCAL.depth = 0
        if stream is not None:
            stream.close()
        else:
            _FALLBACK.release()


def cpu_bound(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        with cpu_slot():
            return function(*args, **kwargs)
    return run
