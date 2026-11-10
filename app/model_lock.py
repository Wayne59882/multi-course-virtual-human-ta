"""Shared answer/vision-model slots for KB and uploaded courses (Python 3.6).

Every model call across the KB and Reference processes holds a slot file.
Students and course preparation have separate slots (see gpu_policy):

* concurrent mode: a student answer never waits for preparation. Preparation
  uses its own slots (several chapters at once) while students keep theirs.
  When only one model fits, a student who needs a different model than the
  running preparation is served first (preparation yields after a grace
  period), because the GPU would otherwise have to swap models back and forth.
* exclusive mode: one model call at a time on the GPU. Students go first;
  preparation does not start while a student waits, and a running step is
  interrupted after a short grace period (ollama_cancel.stream_chat_json).

Preparation never moves ahead of a waiting student. Background calls wait in
rank order (ollama_cancel.BackgroundWork rank); figure reading starts only
while no student has asked recently.
"""
import os
import threading
import time
import gpu_policy
from work_steps import metric
from ollama_cancel import (current_scope, background_active, background_check, background_idle_only,
                           priority_waiting, swap_waiting, student_recently_active, PriorityClaim, SwapClaim, Waiting)
try:
    import fcntl
except ImportError:
    fcntl=None

_FALLBACK=threading.Lock()  # platforms without flock: one call at a time


class _Held(object):
    def __init__(self,model=None):
        self.files=[];self.claim=None;self.swap=None;self.fallback=False;self.model=model


class SharedModelLock:
    def __init__(self,path=None):
        self.path=path or os.environ.get('VQ_SHARED_MODEL_LOCK','/tmp/vq_ollama_answer.lock')
        self._local=threading.local()

    def using(self,model):
        """with lock.using(tag): the next acquisition in this thread is for this model."""
        self._local.model=model
        return self

    # -- slot files -------------------------------------------------------
    def _open(self,path):
        directory=os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):os.makedirs(directory,exist_ok=True)
        return open(path,'a+')

    def _try(self,path,shared=False):
        stream=self._open(path)
        try:
            fcntl.flock(stream.fileno(),(fcntl.LOCK_SH if shared else fcntl.LOCK_EX)|fcntl.LOCK_NB)
            return stream
        except BlockingIOError:
            stream.close();return None

    def _label(self,stream,model):
        # The slot file names the model in use, so other processes can see it.
        try:
            stream.seek(0);stream.truncate();stream.write(str(model or ''));stream.flush()
        except (OSError,ValueError):
            pass

    def _try_any(self,kind,count,model=None):
        for index in range(count):
            stream=self._try('%s.%s.%d'%(self.path,kind,index))
            if stream is not None:
                self._label(stream,model);return stream
        return None

    def _wait_any(self,kind,count,check,model=None):
        while True:
            if check:check()
            stream=self._try_any(kind,count,model)
            if stream is not None:return stream
            time.sleep(0.05)

    def _models_in_use(self,kind,count):
        """Models named by the slots of this kind that are held right now."""
        found=set()
        if fcntl is None:return found
        for index in range(max(count,4)):
            path='%s.%s.%d'%(self.path,kind,index)
            if not os.path.exists(path):continue
            stream=self._try(path)
            if stream is not None:
                stream.close();continue
            try:
                with open(path) as reader:found.add(reader.read().strip())
            except OSError:
                pass
        return found

    def _conflict(self,kind,model):
        return any(other and model and other!=model for other in self._models_in_use(kind,gpu_policy.background_slots()+gpu_policy.student_slots()))

    def _wait_gpu(self,check,blocking):
        if blocking:
            stream=self._open(self.path)
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            return stream
        while True:
            if check:check()
            stream=self._try(self.path)
            if stream is not None:return stream
            time.sleep(0.05)

    def _release(self,held):
        for stream in reversed(held.files):
            try:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            except (OSError,ValueError):
                pass
            finally:
                stream.close()
        held.files=[]
        if held.fallback:
            held.fallback=False;_FALLBACK.release()

    # -- student answers --------------------------------------------------
    def _enter_student(self,held):
        scope=current_scope()
        check=scope.check if scope is not None else None
        held.claim=PriorityClaim().__enter__()  # every process now sees a waiting student
        started=time.monotonic()
        try:
            if fcntl is None:
                while not _FALLBACK.acquire(timeout=0.05):
                    if check:check()
                held.fallback=True
            else:
                held.files.append(self._wait_any('student',gpu_policy.student_slots(),check,held.model))
                if not gpu_policy.concurrent():
                    held.files.append(self._wait_gpu(check,blocking=check is None))
                elif gpu_policy.single_model_sharing() and self._conflict('background',held.model):
                    # Only one model fits and preparation is using another one: go first.
                    held.swap=SwapClaim().__enter__()
                    held.files.append(self._wait_gpu(check,blocking=check is None))
            metric('model_lock_wait_seconds',time.monotonic()-started)
        except BaseException:
            self._release(held);held.claim.__exit__();held.claim=None
            if held.swap is not None:held.swap.__exit__();held.swap=None
            raise

    # -- course preparation -----------------------------------------------
    def _blocked(self,turn,model=None):
        if not gpu_policy.concurrent() and priority_waiting():return True   # students first
        if gpu_policy.single_model_sharing() and (swap_waiting() or self._conflict('student',model)):
            return True  # a student needs the other model
        if background_idle_only() and student_recently_active():return True  # figures wait for quiet
        return turn.paused() or not turn.first()

    def _try_background(self,held):
        if fcntl is None:
            if not _FALLBACK.acquire(False):return False
            held.fallback=True;return True
        slot=self._try_any('background',gpu_policy.background_slots(),held.model)
        if slot is None:return False
        held.files.append(slot)
        if not gpu_policy.concurrent() or gpu_policy.single_model_sharing():
            # Exclusive: the whole GPU. Single model: shared with other preparation,
            # so a student who needs the other model can take it exclusively.
            gpu=self._try(self.path,shared=gpu_policy.concurrent())
            if gpu is None:
                self._release(held);return False
            held.files.append(gpu)
        return True

    def _enter_background(self,held):
        # Background work takes turns by rank (see course_runtime: a course being viewed, then
        # the newest course; builds, then text, then image reading).
        with Waiting() as turn:
            started=time.monotonic()
            while True:
                while self._blocked(turn,held.model):
                    background_check();time.sleep(0.2)
                background_check()
                if self._try_background(held):
                    if not self._blocked(turn,held.model):
                        metric('model_lock_wait_seconds',time.monotonic()-started)
                        metric('background_queue_seconds',time.monotonic()-started)
                        return
                    self._release(held)
                time.sleep(0.05)

    def __enter__(self):
        stack=getattr(self._local,'stack',None)
        if stack is None:stack=self._local.stack=[]
        held=_Held(getattr(self._local,'model',None));self._local.model=None
        if background_active():self._enter_background(held)
        else:self._enter_student(held)
        stack.append(held)
        return self

    def __exit__(self,*args):
        held=self._local.stack.pop()
        try:self._release(held)
        finally:
            if held.swap is not None:held.swap.__exit__()
            if held.claim is not None:held.claim.__exit__()
        return False
