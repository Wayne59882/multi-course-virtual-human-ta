"""Private, atomic checkpoints for completed model steps, never for partial tokens.

The caller still performs its normal source/JSON/math review before publishing.
Keys include the complete request and a configuration revision; credentials and
request text are not written to filenames or metrics. Files belong to the server.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager

try:
    import fcntl
except ImportError:
    fcntl = None

_LOCK = threading.RLock()
_LOCAL = threading.local()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def file_lock(path, check=None):
    path = Path(path)
    if check:
        check()
    path.parent.mkdir(parents=True, exist_ok=True)
    # All production services run on Linux. The in-process lock is a fallback
    # for offline checks on platforms without flock.
    guard = _LOCK if fcntl is None else None
    if guard is not None:
        guard.acquire()
    try:
        with path.open('a+') as stream:
            if fcntl is not None:
                while True:
                    if check:
                        check()
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(.05)
            try:
                if check:
                    check()
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        if guard is not None:
            guard.release()


@contextmanager
def step_scope(directory, check=None):
    previous = getattr(_LOCAL, 'scope', None)
    previous_identities = getattr(_LOCAL, 'identities', None)
    _LOCAL.identities = {}
    _LOCAL.scope = (Path(directory), check)
    try:
        yield
    finally:
        _LOCAL.scope = previous
        _LOCAL.identities = previous_identities


def model_signature(endpoint, model, fetch):
    """Fingerprint installed model metadata once per worker scope.

    Ollama show includes the Modelfile's weight digest, template and parameters.
    If discovery fails, don't reuse a possibly stale checkpoint for that scope.
    """
    if getattr(_LOCAL, 'scope', None) is None:
        return None
    identities=_LOCAL.identities
    key=(endpoint,model)
    if key not in identities:
        try:
            data=fetch()
            if not isinstance(data,dict) or not data:
                raise ValueError('No model metadata')
            identities[key]=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
        except Exception:
            identities[key]='unknown-'+uuid.uuid4().hex
    return identities[key]


def metric(name, value=1):
    scope = getattr(_LOCAL, 'scope', None)
    if scope is None:
        return
    directory, check = scope
    if check:
        check()
    with file_lock(directory / 'metrics.lock', check):
        path = directory / 'metrics.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data[name] = round(float(data.get(name, 0)) + float(value), 6)
        data['updated_at'] = time.time()
        atomic_json(path, data)


def completed_call(request, compute):
    """Reuse only complete calls; a killed process retries just its current call.

    A per-key flock prevents two processes from generating the same step.
    Changing prompts, evidence, endpoint, model or explicit revision changes key.
    """
    scope = getattr(_LOCAL, 'scope', None)
    if scope is None:
        return compute()
    directory, check = scope
    revision = os.environ.get('VQ_STEP_CACHE_REVISION', '1')
    key = hashlib.sha256(json.dumps([revision, request], sort_keys=True,
                                   ensure_ascii=False).encode('utf-8')).hexdigest()
    path = directory / (key + '.json')
    used = getattr(_LOCAL, 'used', None)
    if used is not None:
        used.append(key)
    with file_lock(directory / (key + '.lock'), check):
        try:
            entry = json.loads(path.read_text(encoding='utf-8'))
            if entry.get('key') == key and entry.get('complete') is True:
                metric('checkpoint_hits')
                return entry['result']
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        if check:
            check()
        result = compute()
        if check:
            check()
        # Exceptions and interrupted requests never reach this write.
        atomic_json(path, {'key': key, 'complete': True, 'result': result,
                           'completed_at': time.time()})
        metric('checkpoint_writes')
        return result


def clear_steps(directory):
    """Explicit retry of rejected content must be able to obtain a fresh draft.

    Called only after this kind of worker has stopped. Keep lock files and
    metrics; never invalidate another kind of work's completed steps.
    """
    for path in Path(directory).glob('*.json'):
        if path.name != 'metrics.json':
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def steps_mark():
    """Start recording the steps this thread uses (for reject_steps)."""
    _LOCAL.used = []


def reject_steps():
    """Forget completed but semantically rejected drafts, not transport failures.

    After steps_mark() only the steps this thread used since then are removed,
    so chapters prepared in parallel keep their own completed steps.
    """
    scope=getattr(_LOCAL,'scope',None)
    if scope is None:
        return
    used=getattr(_LOCAL,'used',None)
    if used is None:
        clear_steps(scope[0])
        return
    directory=scope[0]
    for key in used:
        try:
            (directory/(key+'.json')).unlink()
        except FileNotFoundError:
            pass
    _LOCAL.used=[]


def discard_call(request):
    scope=getattr(_LOCAL,'scope',None)
    if scope is None:return
    directory,check=scope
    revision=os.environ.get('VQ_STEP_CACHE_REVISION','1')
    key=hashlib.sha256(json.dumps([revision,request],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    path=directory/(key+'.json')
    with file_lock(directory/(key+'.lock'),check):
        try:path.unlink()
        except FileNotFoundError:pass
