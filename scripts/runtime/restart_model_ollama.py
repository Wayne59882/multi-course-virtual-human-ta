#!/usr/bin/env python3
"""Restart only the private model Ollama so students and course preparation share the GPU.

Run on the teaching server as the same Unix user (Python 3.6+):

    python3 scripts/runtime/restart_model_ollama.py

* Finds the Ollama listening on OLLAMA_HOST (default 127.0.0.1:11435). The
  embedding Ollama (11438) and other users' processes are not touched.
* Keeps that Ollama's binary, model folder and environment, stops it, waits
  for its GPU memory to be released, and reads the free GPU memory.
* Starts it again with a parallel request count and loaded-model count chosen
  from that free memory (same thresholds as scripts/lib/gpu_capacity.sh), then
  loads Gemma (and Ministral when two models fit).
* The teaching services read the new values from the running Ollama within a
  minute (app/gpu_policy.py); they are not restarted.

Requests in progress during the restart (about half a minute) are retried by
the teaching services; students may need to ask again.
"""
from __future__ import print_function
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
import gpu_policy  # noqa: E402


def say(message):
    print(message)
    sys.stdout.flush()


def free_mib(env):
    command = ['nvidia-smi', '--query-gpu=index,memory.free,memory.total', '--format=csv,noheader,nounits']
    try:
        output = subprocess.check_output(command, universal_newlines=True, stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None, None
    rows = []
    for line in output.strip().splitlines():
        try:
            index, free, total = [part.strip() for part in line.split(',')]
            rows.append((index, int(float(free)), int(float(total))))
        except ValueError:
            continue
    visible = str(env.get('CUDA_VISIBLE_DEVICES', '')).split(',')[0].strip()
    if visible:
        rows = [row for row in rows if row[0] == visible] or rows
    if not rows:
        return None, None
    best = max(rows, key=lambda row: row[1])
    return best[1], best[2]


def choose(free):
    full = int(os.environ.get('VQ_GPU_FULL_MIB', '20000'))
    shared = int(os.environ.get('VQ_GPU_SHARED_MIB', '14000'))
    if free is not None and free >= full:
        return 3, 2
    if free is not None and free >= shared:
        return 2, 2
    return 1, 1


def api(base, path, payload=None, timeout=10):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'},
                                     method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


def wait_until(test, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if test():
            return True
        time.sleep(1)
    return False


def main():
    port = gpu_policy.ollama_port()
    base = 'http://127.0.0.1:%d' % port
    found = gpu_policy.process_on_port(port)
    if not found:
        say('[ERROR] No Ollama of this user is listening on port %d; nothing changed.' % port)
        say('        Start the teaching service normally (bash scripts/start.sh).')
        return 2
    pid, env = found
    try:
        binary = os.readlink('/proc/%d/exe' % pid)
        with open('/proc/%d/cmdline' % pid, 'rb') as stream:
            args = [part.decode('utf-8', 'replace') for part in stream.read().split(b'\0') if part]
    except OSError as error:
        say('[ERROR] Cannot inspect Ollama process %d: %s' % (pid, error))
        return 2
    index = next((i for i, arg in enumerate(args) if os.path.basename(arg) == 'ollama'), None)
    if index is None or 'serve' not in args[index + 1:]:
        say('[ERROR] Port %d is not served by "ollama serve" (%s); nothing changed.' % (port, ' '.join(args)[:120]))
        return 2
    executable, rest = args[index], args[index + 1:]
    if not os.path.isfile(executable):
        executable = binary.split(' (deleted)')[0]
    old = (env.get('OLLAMA_NUM_PARALLEL', '1'), env.get('OLLAMA_MAX_LOADED_MODELS', '1'))
    # Model names as the running Reference service uses them.
    service = gpu_policy.process_on_port(int(os.environ.get('VQ_REFERENCE_PORT', '8182')))
    service_env = service[1] if service else {}
    say('[1/4] Model Ollama: pid %d, port %d, parallel %s, loaded models %s' % (pid, port, old[0], old[1]))

    say('[2/4] Stopping it (the embedding Ollama keeps running)...')
    os.kill(pid, signal.SIGTERM)
    if not wait_until(lambda: not os.path.exists('/proc/%d' % pid), 30):
        os.kill(pid, signal.SIGKILL)
        wait_until(lambda: not os.path.exists('/proc/%d' % pid), 10)
    time.sleep(4)  # let the GPU release the model memory
    free, total = free_mib(env)
    parallel, loaded = choose(free)
    say('[3/4] Free GPU memory: %s MiB of %s MiB -> parallel %d, loaded models %d (%s)' % (
        free if free is not None else 'unknown', total if total is not None else 'unknown', parallel, loaded,
        'students and course preparation run together' if parallel >= 2 else 'students first; too little free memory to run together'))

    env = dict(env)
    env.update({'OLLAMA_NUM_PARALLEL': str(parallel), 'OLLAMA_MAX_LOADED_MODELS': str(loaded)})
    resolved = Path(executable).resolve()
    home = resolved.parents[2] if len(resolved.parents) > 2 else Path.home()  # <base>/ollama/bin/ollama
    log = home / 'ollama.log'
    with open(str(log), 'ab') as stream:
        subprocess.Popen([executable] + rest, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                         start_new_session=True)
    try:
        (home / 'ollama-capacity.env').write_text(
            'export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-%d}"\nexport OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-%d}"\n'
            % (parallel, loaded))
    except OSError:
        pass

    def ready():
        try:
            return bool(api(base, '/api/version', timeout=3))
        except Exception:
            return False
    if not wait_until(ready, 60):
        say('[ERROR] Ollama did not answer after the restart. Log: %s' % log)
        return 1
    models = [service_env.get('VQ_GEMMA_MODEL_TAG') or os.environ.get('VQ_GEMMA_MODEL_TAG') or 'gemma4:12b']
    if loaded >= 2:
        models.append(service_env.get('VQ_MISTRAL_MODEL_TAG') or os.environ.get('VQ_MISTRAL_MODEL_TAG') or 'ministral-3:8b-instruct-2512-q4_K_M')
    keep = service_env.get('VQ_MODEL_KEEP_ALIVE') or '30m'
    try:  # the teaching services' context size (ollama_cancel.OLLAMA_NUM_CTX)
        num_ctx = max(8192, int(service_env.get('VQ_NUM_CTX') or 8192))
    except ValueError:
        num_ctx = 8192
    for model in models:
        try:
            # Same context size as the teaching services, so their first request does not reload it.
            api(base, '/api/generate', {'model': model, 'prompt': '', 'keep_alive': keep, 'stream': False,
                                        'options': {'num_ctx': num_ctx}}, timeout=240)
        except Exception as error:
            say('      Could not load %s now (%s); it loads on first use.' % (model, str(error)[:120]))
    placed = []
    try:
        for row in api(base, '/api/ps', timeout=10).get('models', []):
            size = float(row.get('size') or 0)
            share = 100 if not size else int(round(100 * float(row.get('size_vram') or 0) / size))
            placed.append('%s %d%% GPU' % (row.get('name'), share))
    except Exception:
        pass
    say('[4/4] Restarted. Loaded: %s' % (', '.join(placed) or 'none yet'))
    if parallel >= 2:
        say('[DONE] 學生問答和備課現在可以同時進行（同時 %d 件，%d 個模型）。網站一分鐘內自動套用，不用重新啟動網站。' % (parallel, loaded))
        if any('100%' not in item for item in placed):
            say('[NOTE] 有模型沒有完全放在 GPU 上，可能會變慢；請把這段訊息貼給 Claude。')
    else:
        say('[DONE] GPU 剩餘記憶體不夠，維持「學生優先」模式。等伺服器比較空的時候再執行一次這個指令即可。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
