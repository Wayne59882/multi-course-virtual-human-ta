"""Real HTTP: finish a bounded step before yielding; cancellation still works."""
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
os.environ['VQ_MODEL_PRIORITY_DIR']=tempfile.mkdtemp(prefix='mc-vhta-boundary-')
import ollama_cancel as oc
from model_lock import SharedModelLock


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.calls.append(self.path)
        self.send_response(200);self.end_headers()
        try:
            self.wfile.write((json.dumps({'message':{'content':'first'},'done':False})+'\n').encode());self.wfile.flush()
            self.server.started.set()
            self.server.release.wait(4)
            self.wfile.write((json.dumps({'message':{'content':' last'},'done':True})+'\n').encode());self.wfile.flush()
        except OSError:pass


with tempfile.TemporaryDirectory() as temporary:
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.calls=[];server.started=threading.Event();server.release=threading.Event()
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url='http://127.0.0.1:%d/api/chat'%server.server_port
    payload={'model':'fixture','messages':[]};outcomes=[]
    try:
        lock=SharedModelLock(str(Path(temporary)/'model.lock'))
        def work():
            try:
                with oc.BackgroundWork():
                    with lock:outcomes.append(oc.stream_chat_json(url,payload,5)['message']['content'])
            except Exception as error:outcomes.append(error)
        with patch.dict(os.environ,{'VQ_BACKGROUND_PREEMPT':'boundary'}):
            worker=threading.Thread(target=work);worker.start();assert server.started.wait(2)
            with oc.PriorityClaim():
                time.sleep(.35)  # a waiting student must not discard completed tokens
                server.release.set();worker.join(3)
            assert outcomes==['first last'] and len(server.calls)==1,outcomes

        # A continuous student claim does not starve background work forever.
        # This tests our queue only, not another lab user's independent process.
        admitted=threading.Event()
        with patch.dict(os.environ,{'VQ_BACKGROUND_MAX_WAIT_SEC':'1'}),oc.PriorityClaim():
            def waiting():
                with oc.BackgroundWork():
                    with lock:admitted.set()
            waiter=threading.Thread(target=waiting);waiter.start()
            assert not admitted.wait(.2)
            assert admitted.wait(2)
            waiter.join(2)

        server.started.clear();server.release.clear();outcomes.clear()
        with patch.dict(os.environ,{'VQ_BACKGROUND_STEP_TIMEOUT_SEC':'1'}):
            worker=threading.Thread(target=work);worker.start()
            assert server.started.wait(2);worker.join(3)
            assert not worker.is_alive() and isinstance(outcomes[0],TimeoutError),outcomes
    finally:
        server.release.set();server.shutdown();server.server_close()
print('PASS step-boundary handoff avoids repeated generation, aged background work progresses and wall-clock timeout releases the worker')
