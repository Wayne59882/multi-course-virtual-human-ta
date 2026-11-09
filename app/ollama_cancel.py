"""Cancellation, priority and preemption for local Ollama /api/chat calls (Python 3.6).

* Student Q&A workers enter a CancellationScope: a model switch closes only
  that request's own socket.
* Background course work (PDF reading, lectures, questions) runs inside
  BackgroundWork. It yields the GPU whenever a student answer is waiting:
  the background stream is closed, the shared lock is released, and the same
  call is retried after the student answer finishes.
* Student answers publish a PriorityClaim file so the KB and Reference
  processes see each other's waiting students.
"""
import http.client
import json
import os
import socket
import threading
import time
import uuid
from urllib.parse import urlsplit

PRIORITY_DIR = os.environ.get('VQ_MODEL_PRIORITY_DIR', '/tmp/vq_ollama_priority')
PREEMPT_POLL_SEC = 0.25
CLAIM_MAX_AGE_SEC = 1800
# Ollama reloads a model whenever num_ctx changes between requests, so every
# KB, Reference, course and PDF-vision call uses this one context size.
OLLAMA_NUM_CTX = max(8192, int(os.environ.get('VQ_NUM_CTX', '8192') or 8192))
OLLAMA_KEEP_ALIVE = os.environ.get('VQ_MODEL_KEEP_ALIVE', '30m')


class AnswerCancelled(Exception):
    """The requesting student superseded this answer."""


class BackgroundPreempted(Exception):
    """A background generation yielded the model to a waiting student."""


_LOCAL = threading.local()


def current_scope():
    return getattr(_LOCAL, 'scope', None)


def background_active():
    return bool(getattr(_LOCAL, 'background', False))


def background_check():
    check = getattr(_LOCAL, 'background_check', None)
    if check is not None:
        check()


_WAITERS = {}
_WAITERS_LOCK = threading.Lock()
PAUSED = (1000,)  # a rank at or above this waits until it changes (a course nobody has opened lately)


def background_rank():
    """This thread's place in the background queue (lower goes first), or None."""
    rank = getattr(_LOCAL, 'background_rank', None)
    try:
        return tuple(rank()) if rank is not None else None
    except Exception:
        return (99,)


class Waiting(object):
    """A background model call waiting for its turn; the best-ranked waiter goes first."""
    def __enter__(self):
        self.key = object()
        self.rank = getattr(_LOCAL, 'background_rank', None)
        with _WAITERS_LOCK:
            _WAITERS[self.key] = self.rank
        return self

    def __exit__(self, *args):
        with _WAITERS_LOCK:
            _WAITERS.pop(self.key, None)
        return False

    def paused(self):
        try:
            return self.rank is not None and tuple(self.rank()) >= PAUSED
        except Exception:
            return False

    def first(self):
        """True unless another waiting call in this process is ranked strictly better."""
        with _WAITERS_LOCK:
            waiting = list(_WAITERS.items())

        def value(rank):
            try:
                return tuple(rank()) if rank is not None else (50,)
            except Exception:
                return (99,)
        mine = value(self.rank)
        return all(not value(rank) < mine or value(rank) >= PAUSED for key, rank in waiting if key is not self.key)


class BackgroundWork(object):
    """Mark this thread's model calls as low-priority, preemptible work.

    check() may raise (for example when the course was deleted) and is called
    while waiting for the model and while streaming.
    """
    def __init__(self, check=None, rank=None):
        self.check = check
        self.rank = rank  # callable: lower sorts first in the background queue
        self._previous = None

    def __enter__(self):
        self._previous = (getattr(_LOCAL, 'background', False), getattr(_LOCAL, 'background_check', None),
                          getattr(_LOCAL, 'background_rank', None))
        _LOCAL.background = True
        _LOCAL.background_check = self.check
        if self.rank is not None:
            _LOCAL.background_rank = self.rank
        return self

    def __exit__(self, *args):
        _LOCAL.background, _LOCAL.background_check, _LOCAL.background_rank = self._previous
        return False


def _process_alive(pid):
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def priority_waiting():
    """True while any live server process has a student answer waiting or running."""
    try:
        names = os.listdir(PRIORITY_DIR)
    except OSError:
        return False
    now = time.time()
    for name in names:
        path = os.path.join(PRIORITY_DIR, name)
        try:
            pid = int(name.split('-', 1)[0])
            if _process_alive(pid) and now - os.path.getmtime(path) < CLAIM_MAX_AGE_SEC:
                return True
        except (ValueError, OSError):
            pass
        try:
            os.unlink(path)  # stale claim of a stopped process
        except OSError:
            pass
    return False


class PriorityClaim(object):
    """Visible to every server process while a student answer needs the model."""
    def __init__(self):
        self.path = None

    def __enter__(self):
        try:
            if not os.path.isdir(PRIORITY_DIR):
                os.makedirs(PRIORITY_DIR, exist_ok=True)
            path = os.path.join(PRIORITY_DIR, '%d-%s' % (os.getpid(), uuid.uuid4().hex))
            with open(path, 'w') as stream:
                stream.write('%.3f' % time.time())
            self.path = path
        except OSError:
            self.path = None
        return self

    def __exit__(self, *args):
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.path = None
        return False


def _interrupt(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass


class CancellationScope(object):
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._socket = None
        self._previous = None

    def __enter__(self):
        # Never install a cancelled scope in thread-local state if entry fails.
        self.check()
        self._previous = current_scope()
        _LOCAL.scope = self
        return self

    def __exit__(self, *args):
        _LOCAL.scope = self._previous
        return False

    def check(self):
        if self.cancelled.is_set():
            raise AnswerCancelled('Answer cancelled after model switch')

    def attach_socket(self, sock):
        with self._lock:
            self._socket = sock
            cancelled = self.cancelled.is_set()
        if cancelled:
            if sock is not None:
                _interrupt(sock)
            self.check()

    def attach(self, response):
        # urllib -> http.client.HTTPResponse -> socket.SocketIO -> socket.
        try:
            sock = response.fp.raw._sock
        except (AttributeError, TypeError):
            sock = None
        self.attach_socket(sock)

    def detach(self):
        with self._lock:
            self._socket = None

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            _interrupt(sock)


def _connection(url, timeout):
    parts = urlsplit(url)
    factory = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    conn = factory(parts.hostname, parts.port, timeout=float(timeout))
    path = parts.path or '/'
    if parts.query:
        path += '?' + parts.query
    return conn, path


def stream_chat_json(url, payload, timeout):
    """Stream Ollama NDJSON and return the normal nonstreaming response shape.

    The socket exists before the request is sent, so a model load or long
    prompt evaluation can also be interrupted, not only token output.
    """
    scope = current_scope()
    background = background_active()
    if scope is None and not background:
        raise RuntimeError('Streaming chat requires a cancellation scope or background work')
    if scope is not None:
        scope.check()
    if background:
        background_check()
        if priority_waiting():
            raise BackgroundPreempted('A student answer is waiting for the model')
    conn, path = _connection(url, timeout)
    stop = threading.Event()
    reason = {}
    watchdog = None
    try:
        conn.connect()
        sock = conn.sock
        if scope is not None:
            scope.attach_socket(sock)
        if background:
            def watch():
                while not stop.wait(PREEMPT_POLL_SEC):
                    try:
                        background_check()
                        if priority_waiting():
                            reason['preempted'] = True
                    except Exception as error:  # deleted course etc.
                        reason['error'] = error
                    if reason:
                        _interrupt(sock)
                        return
            watchdog = threading.Thread(target=watch, daemon=True)
            watchdog.start()
        body = json.dumps(dict(payload, stream=True)).encode('utf-8')
        conn.request('POST', path, body=body, headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        if response.status >= 400:
            detail = response.read(2048).decode('utf-8', 'replace')
            raise RuntimeError('Ollama HTTP %s: %s' % (response.status, detail[:300]))
        chunks = []
        last = None
        for line in response:
            if scope is not None:
                scope.check()
            if not line.strip():
                continue
            piece = json.loads(line.decode('utf-8'))
            if piece.get('error'):
                raise RuntimeError(str(piece['error']))
            message = piece.get('message') or {}
            if isinstance(message.get('content'), str):
                chunks.append(message['content'])
            last = piece
            if piece.get('done'):
                break
        if scope is not None:
            scope.check()
        if last is None or last.get('done') is not True:
            if 'error' in reason:
                raise reason['error']
            if reason:
                raise BackgroundPreempted('A student answer is waiting for the model')
            raise RuntimeError('Ollama stream ended before completion')
        result = dict(last)
        message = dict(result.get('message') or {})
        message['content'] = ''.join(chunks)
        result['message'] = message
        return result
    except Exception as exc:
        if scope is not None and scope.cancelled.is_set():
            raise AnswerCancelled('Answer cancelled after model switch')
        if 'error' in reason:
            raise reason['error']
        if 'preempted' in reason or isinstance(exc, BackgroundPreempted):
            raise BackgroundPreempted('A student answer is waiting for the model')
        raise
    finally:
        stop.set()
        if scope is not None:
            scope.detach()
        try:
            conn.close()
        except Exception:
            pass


def wait_for_priority_clear(poll=0.2):
    """Background work waits here while students are waiting for the model."""
    while priority_waiting():
        background_check()
        time.sleep(poll)
    background_check()
