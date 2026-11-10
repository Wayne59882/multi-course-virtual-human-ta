"""Real HTTP: students and course preparation share the GPU.

Concurrent mode (enough GPU memory): a student answer starts while
preparation is generating, preparation is not interrupted, and two chapters
of preparation run at once. Figure reading waits until students are quiet.
Exclusive mode keeps one request at a time with students first.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'app'))
os.environ['VQ_MODEL_PRIORITY_DIR']=tempfile.mkdtemp(prefix='mc-vhta-sharing-')
import gpu_policy
import ollama_cancel as oc
from model_lock import SharedModelLock


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        with self.server.guard:
            self.server.active+=1;self.server.peak=max(self.server.peak,self.server.active)
        self.send_response(200);self.end_headers()
        try:
            count=60 if self.path.endswith('/long') else 10
            for index in range(count):
                self.wfile.write((json.dumps({'message':{'content':'x'},'done':index==count-1})+'\n').encode());self.wfile.flush()
                time.sleep(.05)
        except OSError:pass
        finally:
            with self.server.guard:self.server.active-=1


CONCURRENT={'OLLAMA_NUM_PARALLEL':'3','OLLAMA_MAX_LOADED_MODELS':'2','VQ_GPU_SHARING':'auto'}
EXCLUSIVE={'OLLAMA_NUM_PARALLEL':'1','OLLAMA_MAX_LOADED_MODELS':'1','VQ_GPU_SHARING':'auto'}
ONE_MODEL={'OLLAMA_NUM_PARALLEL':'2','OLLAMA_MAX_LOADED_MODELS':'1','VQ_GPU_SHARING':'auto','VQ_BACKGROUND_GRACE_SEC':'0.5'}

with patch.dict(os.environ,EXCLUSIVE):
    assert not gpu_policy.concurrent() and gpu_policy.background_slots()==1 and not gpu_policy.keep_models_loaded()
with patch.dict(os.environ,CONCURRENT):
    assert gpu_policy.concurrent() and gpu_policy.background_slots()==2 and gpu_policy.keep_models_loaded()
with patch.dict(os.environ,dict(CONCURRENT,VQ_GPU_SHARING='exclusive')):
    assert not gpu_policy.concurrent()
with patch.dict(os.environ,ONE_MODEL):
    assert gpu_policy.concurrent() and gpu_policy.single_model_sharing() and not gpu_policy.keep_models_loaded()

with tempfile.TemporaryDirectory() as temporary:
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.guard=threading.Lock();server.active=0;server.peak=0
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url='http://127.0.0.1:%d/api/chat'%server.server_port
    payload={'model':'fixture','messages':[]}
    lock=SharedModelLock(str(Path(temporary)/'model.lock'))
    try:
        with patch.dict(os.environ,CONCURRENT):
            # Two chapters of preparation at once, and a student alongside them.
            results=[];started=threading.Barrier(3)
            def prepare():
                with oc.BackgroundWork():
                    with lock:
                        started.wait(3)
                        results.append(oc.stream_chat_json(url,payload,5)['message']['content'])
            workers=[threading.Thread(target=prepare) for _ in range(2)]
            for worker in workers:worker.start()
            started.wait(3);time.sleep(.1)
            began=time.monotonic()
            with oc.CancellationScope():
                with lock:
                    waited=time.monotonic()-began
                    answer=oc.stream_chat_json(url,payload,5)['message']['content']
            for worker in workers:worker.join(5)
            assert waited<.3,'student waited %.2fs for course preparation'%waited
            assert answer=='x'*10 and results==['x'*10]*2,(answer,results)  # nothing interrupted
            assert server.peak==3,'student and two preparation steps should run together (peak %s)'%server.peak

            # A third preparation step waits for a preparation slot, never a student's.
            holders=[];release=threading.Event();entered=threading.Event()
            def hold():
                with oc.BackgroundWork():
                    with lock:
                        holders.append(1);entered.set();release.wait(3)
            first=[threading.Thread(target=hold) for _ in range(2)]
            for worker in first:worker.start()
            deadline=time.monotonic()+3
            while len(holders)<2 and time.monotonic()<deadline:time.sleep(.02)
            third=threading.Thread(target=hold);third.start()
            time.sleep(.4)
            assert len(holders)==2,'only two preparation slots exist'
            with oc.CancellationScope():
                began=time.monotonic()
                with lock:assert time.monotonic()-began<.3
            release.set()
            for worker in first+[third]:worker.join(3)
            assert len(holders)==3

            # Figure reading starts only after students have been quiet.
            with patch.dict(os.environ,{'VQ_FIGURE_IDLE_SEC':'1'}):
                oc.note_student_activity()
                admitted=threading.Event()
                def figure():
                    with oc.BackgroundWork(idle_only=True):
                        with lock:admitted.set()
                waiter=threading.Thread(target=figure);waiter.start()
                assert not admitted.wait(.5),'figure reading started while a student was active'
                assert admitted.wait(3),'figure reading did not start once students were quiet'
                waiter.join(2)
            # Ordinary text preparation does not wait for quiet.
            oc.note_student_activity()
            began=time.monotonic()
            with oc.BackgroundWork():
                with lock:pass
            assert time.monotonic()-began<.3

        with patch.dict(os.environ,ONE_MODEL):
            # One model fits: a Gemma student and Gemma preparation run together.
            server.peak=0;results=[];ready=threading.Event()
            def prepare_gemma(path='/api/chat'):
                try:
                    with oc.BackgroundWork():
                        with lock.using('gemma4:12b'):
                            ready.set();results.append(oc.stream_chat_json(url.replace('/api/chat',path),payload,10)['message']['content'])
                except Exception as error:results.append(error)
            worker=threading.Thread(target=prepare_gemma);worker.start();assert ready.wait(3);time.sleep(.1)
            began=time.monotonic()
            with oc.CancellationScope():
                with lock.using('gemma4:12b'):
                    waited=time.monotonic()-began
                    oc.stream_chat_json(url,payload,5)
            worker.join(5)
            assert waited<.3 and results==['x'*10] and server.peak==2,(waited,results,server.peak)

            # A Ministral student while Gemma prepares: the student goes first
            # (preparation yields after the grace period) instead of model swapping.
            results.clear();ready.clear()
            worker=threading.Thread(target=prepare_gemma,args=('/long',));worker.start();assert ready.wait(3);time.sleep(.2)
            began=time.monotonic()
            with oc.CancellationScope():
                with lock.using('ministral-3:8b'):
                    waited=time.monotonic()-began
                    assert oc.swap_waiting()
                    # New Gemma preparation does not start while this student is answered.
                    started=threading.Event()
                    def late():
                        with oc.BackgroundWork():
                            with lock.using('gemma4:12b'):started.set()
                    other=threading.Thread(target=late);other.start()
                    assert not started.wait(.6),'preparation started while a student needed the other model'
            worker.join(5);other.join(5)
            assert started.is_set() and not oc.swap_waiting()
            assert isinstance(results[0],oc.BackgroundPreempted) and .4<=waited<2.5,(results,waited)

        with patch.dict(os.environ,EXCLUSIVE):
            server.peak=0
            with oc.CancellationScope():
                with lock:
                    blocked=threading.Event();done=threading.Event()
                    def prepare_after():
                        with oc.BackgroundWork():
                            blocked.set()
                            with lock:done.set()
                    worker=threading.Thread(target=prepare_after);worker.start()
                    assert blocked.wait(2) and not done.wait(.5),'exclusive mode: preparation must wait for the student'
            assert done.wait(3);worker.join(2)
    finally:
        server.shutdown();server.server_close()
print('PASS concurrent GPU sharing: students never wait for preparation, two chapters prepare at once without interruption, figures wait for quiet; with one model, same-model students share and other-model students go first; exclusive mode keeps students first')
