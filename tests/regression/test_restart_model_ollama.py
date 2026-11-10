"""Restarting only the model Ollama switches the services to shared GPU use.

A stand-in "ollama serve" listens on a free port and a stand-in nvidia-smi
reports free GPU memory. The restart script must stop only that process, start
it again with the chosen parallel/loaded-model settings and load the models,
and app/gpu_policy must read the new settings from the running process without
restarting the teaching services. A second stand-in (the embedding Ollama) is
left running.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'app'))
if not Path('/proc/self/fd').is_dir():
    print('SKIP model Ollama restart: /proc is required');raise SystemExit(0)
import gpu_policy

SERVER='''#!%s
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(os.environ['OLLAMA_HOST'].rsplit(':', 1)[1])
log = os.environ['STANDIN_LOG']
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, value):
        body = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == '/api/version': self.reply({'version': 'stand-in'})
        elif self.path == '/api/ps':
            spill = os.environ.get('OLLAMA_NUM_PARALLEL') in open(os.environ['STANDIN_SPILL']).read().split(',')
            self.reply({'models': [{'name': m, 'size': 10, 'size_vram': 5 if spill else 10} for m in loaded]})
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])).decode())
        with open(log, 'a') as stream: stream.write(json.dumps({'path': self.path, 'payload': payload, 'parallel': os.environ.get('OLLAMA_NUM_PARALLEL')}) + '\\n')
        loaded.append(payload['model']); self.reply({'done': True})
loaded = []
assert sys.argv[1:] == ['serve']
HTTPServer(('127.0.0.1', port), Handler).serve_forever()
''' % sys.executable


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));return probe.getsockname()[1]


def wait(test,seconds=15):
    deadline=time.time()+seconds
    while time.time()<deadline:
        if test():return True
        time.sleep(.2)
    return False


with tempfile.TemporaryDirectory() as temporary:
    base=Path(temporary)/'server base'
    binary=base/'ollama'/'bin'/'ollama';binary.parent.mkdir(parents=True)
    binary.write_text(SERVER);binary.chmod(0o755)
    tools=Path(temporary)/'tools';tools.mkdir()
    smi=tools/'nvidia-smi';smi.write_text('#!/bin/sh\necho "0, 23100, 24564"\n');smi.chmod(0o755)
    def gpu_free(mib):smi.write_text('#!/bin/sh\necho "0, %d, 24564"\n'%mib)
    log=Path(temporary)/'calls.jsonl'
    model_port,embed_port=free_port(),free_port()
    env=dict(os.environ,STANDIN_LOG=str(log),OLLAMA_NUM_PARALLEL='1',OLLAMA_MAX_LOADED_MODELS='1',STANDIN_SPILL=str(Path(temporary)/'spill'))
    (Path(temporary)/'spill').write_text('')
    priority=Path(temporary)/'priority'
    script=[sys.executable,str(ROOT/'scripts/runtime/restart_model_ollama.py')]
    def restart(*extra):
        return subprocess.run(script+list(extra),env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],VQ_REFERENCE_PORT=str(free_port()),
                              VQ_GPU_RELEASE_SEC='0',VQ_MODEL_PRIORITY_DIR=str(priority)),
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True,timeout=120)
    def current():
        gpu_policy._CACHE['at']=-1e9
        found=gpu_policy.process_on_port(model_port)
        return found[0],found[1]['OLLAMA_NUM_PARALLEL'],found[1]['OLLAMA_MAX_LOADED_MODELS']
    model=subprocess.Popen([str(binary),'serve'],env=dict(env,OLLAMA_HOST='127.0.0.1:%d'%model_port),start_new_session=True)
    embed=subprocess.Popen([str(binary),'serve'],env=dict(env,OLLAMA_HOST='127.0.0.1:%d'%embed_port),start_new_session=True)
    restarted=None
    try:
        assert wait(lambda:gpu_policy.process_on_port(model_port) is not None and gpu_policy.process_on_port(embed_port) is not None)
        os.environ['OLLAMA_HOST']='127.0.0.1:%d'%model_port
        gpu_policy._CACHE['at']=-1e9
        assert not gpu_policy.concurrent() and gpu_policy.describe()['settings_source']=='ollama-process'

        result=restart()
        assert result.returncode==0,result.stdout
        assert 'Trying parallel 3, loaded models 2' in result.stdout and '[DONE]' in result.stdout,result.stdout
        assert model.wait(10) is not None,'the old model Ollama must be stopped'
        assert embed.poll() is None,'the embedding Ollama must keep running'
        found=gpu_policy.process_on_port(model_port)
        assert found and found[0]!=model.pid
        restarted=found[0]
        assert found[1]['OLLAMA_NUM_PARALLEL']=='3' and found[1]['OLLAMA_MAX_LOADED_MODELS']=='2'
        calls=[json.loads(line) for line in log.read_text().splitlines()]
        assert [call['payload']['model'] for call in calls]==['gemma4:12b','ministral-3:8b-instruct-2512-q4_K_M'],calls
        assert all(call['payload']['options']['num_ctx']>=8192 and call['parallel']=='3' for call in calls)
        assert 'OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-3}"' in (base/'ollama-capacity.env').read_text()
        # The running services pick the new settings up without a restart.
        gpu_policy._CACHE['at']=-1e9
        assert gpu_policy.concurrent() and gpu_policy.background_slots()==2 and gpu_policy.keep_models_loaded()

        # Room for one model (the lab server's case): Gemma shared, Ministral not loaded.
        gpu_free(10891);log.write_text('')
        result=restart()
        assert result.returncode==0 and 'Trying parallel 2, loaded models 1' in result.stdout,result.stdout
        restarted,parallel,loaded=current()
        assert (parallel,loaded)==('2','1') and gpu_policy.concurrent() and gpu_policy.single_model_sharing()
        assert [json.loads(line)['payload']['model'] for line in log.read_text().splitlines()]==['gemma4:12b']

        # Too little free memory: one request at a time, students first.
        gpu_free(6000)
        result=restart()
        assert result.returncode==0 and 'Trying parallel 1, loaded models 1' in result.stdout,result.stdout
        restarted,parallel,loaded=current()
        assert not gpu_policy.concurrent()

        # A setting whose models do not fit completely steps down by itself.
        gpu_free(23100)
        (Path(temporary)/'spill').write_text('3')
        result=restart()
        assert 'did not fit completely' in result.stdout and 'Trying parallel 2, loaded models 2' in result.stdout,result.stdout
        restarted,parallel,loaded=current()
        assert (parallel,loaded)==('2','2'),(parallel,loaded)
        (Path(temporary)/'spill').write_text('')

        # --auto (run by the updater): nothing changes while the setting is right ...
        result=restart('--auto')
        assert '[OK] GPU sharing unchanged' in result.stdout and current()[0]==restarted,result.stdout
        # ... or while students were active recently, even if more memory is free ...
        gpu_free(6000);restart();restarted=current()[0]
        gpu_free(23100)
        Path(str(priority)+'.last-student').write_text('now')
        result=restart('--auto')
        assert '[OK]' in result.stdout and current()[0]==restarted,result.stdout
        # ... and moves up once students have been quiet.
        old=time.time()-3600;os.utime(str(priority)+'.last-student',(old,old))
        result=restart('--auto')
        assert '[AUTO] more GPU memory is free now' in result.stdout,result.stdout
        restarted,parallel,loaded=current()
        # The 3-request setting did not fit with this much memory before: not tried again.
        assert (parallel,loaded)==('2','2') and 'Trying parallel 3' not in result.stdout,result.stdout
    finally:
        for pid in (restarted,):
            if pid:
                try:os.kill(pid,15)
                except OSError:pass
        for process in (model,embed):
            if process.poll() is None:process.terminate()
            process.wait(5)
# The updater checks at most every ten minutes, so nobody has to run this by hand.
import importlib.util
spec=importlib.util.spec_from_file_location('auto_sync',str(ROOT/'scripts/deploy/auto_sync.py'))
auto_sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(auto_sync)
with tempfile.TemporaryDirectory() as temporary:
    target=Path(temporary);(target/'logs').mkdir()
    calls=[]
    run=lambda args:calls.append(args) or 'checked'
    assert auto_sync.gpu_step(target,now=1000,runner=run)=='checked' and calls[0][-1]=='--auto'
    assert auto_sync.gpu_step(target,now=1300,runner=run) is None
    assert auto_sync.gpu_step(target,now=1000+auto_sync.GPU_CHECK_SECONDS+1,runner=run)=='checked' and len(calls)==2
print('PASS model Ollama restart: only the model Ollama restarts, capacity follows free GPU memory, models load with the services\' context size, services read the new settings without restarting, the updater checks every ten minutes')
