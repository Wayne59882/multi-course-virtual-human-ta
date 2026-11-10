"""One CPU-heavy work budget shared by this deployment's processes.

This cannot reserve resources against other users on the host. All services
must use the same VQ_CPU_WORKERS and VQ_CPU_SLOT_DIR. GPU inference is separate.
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


def cpu_workers():
    available = available_cpus()
    try:
        requested = int(os.environ.get('VQ_CPU_WORKERS', str(min(2, available))))
    except ValueError:
        requested = 1
    return max(1, min(requested, available))


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
