"""Shared answer/vision-model lock for KB and uploaded courses (Python 3.6).

One GPU model runs at a time across the KB and Reference processes. Student
answers have priority: while one waits, background course preparation does
not normally start a new model call. Running steps finish and checkpoint
before yielding; immediate preemption remains an explicit opt-in. Background calls
wait in rank order (ollama_cancel.BackgroundWork rank).
"""
import os
import threading
import time
from work_steps import metric
from ollama_cancel import (current_scope, background_active, background_check,
                           priority_waiting, PriorityClaim, Waiting)
try:
    import fcntl
except ImportError:
    fcntl=None


class SharedModelLock:
    def __init__(self,path=None):
        self.path=path or os.environ.get('VQ_SHARED_MODEL_LOCK','/tmp/vq_ollama_answer.lock')
        self.thread_lock=threading.Lock();self.fp=None;self._claim=None

    def _acquire(self,scope,background):
        started=time.monotonic()
        def check():
            if scope is not None:scope.check()
            if background:background_check()
        if scope is None and not background:
            self.thread_lock.acquire()
        else:
            while not self.thread_lock.acquire(timeout=0.05):check()
        try:
            check()
            self.fp=open(self.path,'a+')
            if fcntl is not None:
                if scope is None and not background:
                    fcntl.flock(self.fp.fileno(),fcntl.LOCK_EX)
                else:
                    while True:
                        check()
                        try:
                            fcntl.flock(self.fp.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            time.sleep(0.05)
            metric('model_lock_wait_seconds',time.monotonic()-started)
        except BaseException:
            if self.fp:self.fp.close()
            self.fp=None;self.thread_lock.release();raise

    def _release(self):
        try:
            if self.fp is not None:
                if fcntl is not None:fcntl.flock(self.fp.fileno(),fcntl.LOCK_UN)
                self.fp.close()
        finally:self.fp=None;self.thread_lock.release()

    def __enter__(self):
        scope=current_scope();background=background_active()
        if not background:
            # A student answer: tell background work in every process to yield.
            claim=PriorityClaim().__enter__()
            try:self._acquire(scope,False)
            except BaseException:
                claim.__exit__();raise
            self._claim=claim
            return self
        # Background work takes turns by rank (see course_runtime: a course being viewed, then
        # the newest course; builds, then text, then image reading), not all courses at once.
        with Waiting() as turn:
            # Aging allows a waiting background step to finish even under a
            # continuous stream of questions. This controls only our services.
            maximum=max(1,float(os.environ.get('VQ_BACKGROUND_MAX_WAIT_SEC','120')))
            started=time.monotonic()
            blocked=lambda:(priority_waiting() and time.monotonic()-started<maximum) or turn.paused() or not turn.first()
            while True:
                while blocked():
                    background_check();time.sleep(0.2)
                self._acquire(None,True)
                if not blocked():
                    metric('background_queue_seconds',time.monotonic()-started)
                    return self
                self._release();time.sleep(0.05)

    def __exit__(self,*args):
        claim=self._claim;self._claim=None
        try:self._release()
        finally:
            if claim is not None:claim.__exit__()
        return False
