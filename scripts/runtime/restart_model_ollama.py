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
  loads Gemma (and Ministral when two models fit). If a model does not fit
  completely on the GPU, it steps down to the next setting by itself.
* --auto (run by scripts/deploy/auto_sync.py): restart only when a better
  setting now fits (and students have been quiet for a while) or when a model
  has spilled out of GPU memory; otherwise change nothing.
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


TIERS = (('VQ_GPU_FULL_MIB', 20000, 3, 2), ('VQ_GPU_SHARED_MIB', 14000, 2, 2),
         ('VQ_GPU_SINGLE_MIB', 10000, 2, 1), (None, 0, 1, 1))


def tier_for(free):
    for index, (name, default, _, _) in enumerate(TIERS):
        threshold = int(os.environ.get(name, default)) if name else 0
        if free is not None and free >= threshold:
            return index
    return len(TIERS) - 1


def tier_of(env):
    current = (env.get('OLLAMA_NUM_PARALLEL', '1'), env.get('OLLAMA_MAX_LOADED_MODELS', '1'))
    for index, (_, _, parallel, loaded) in enumerate(TIERS):
        if current == (str(parallel), str(loaded)):
            return index
    return len(TIERS) - 1


def describe(index):
    parallel, loaded = TIERS[index][2:]
    if parallel >= 2 and loaded >= 2:
        return 'students and course preparation run together; Gemma and Ministral both stay loaded'
    if parallel >= 2:
        return 'students and course preparation run together on Gemma; a student using Ministral goes first'
    return 'students first; too little free memory to run together'


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


def loaded_models(base):
    """[(name, percent on GPU, bytes on GPU)] of the models Ollama has loaded."""
    rows = []
    try:
        for row in api(base, '/api/ps', timeout=10).get('models', []):
            size = float(row.get('size') or 0)
            vram = float(row.get('size_vram') or 0)
            rows.append((row.get('name'), 100 if not size else int(round(100 * vram / size)), vram))
    except Exception:
        pass
    return rows


def load_state(home):
    try:
        return json.loads((home / 'ollama-capacity.json').read_text())
    except (OSError, ValueError):
        return {}


def stop(pid, process=None):
    if process is not None:  # started by this script: wait for it, not for /proc
        process.terminate()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(10)
    else:
        os.kill(pid, signal.SIGTERM)
        if not wait_until(lambda: not os.path.exists('/proc/%d' % pid), 30):
            os.kill(pid, signal.SIGKILL)
            wait_until(lambda: not os.path.exists('/proc/%d' % pid), 10)
    time.sleep(float(os.environ.get('VQ_GPU_RELEASE_SEC', '4')))  # let the GPU release the model memory


def main(argv=None):
    auto = '--auto' in (argv if argv is not None else sys.argv[1:])
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
    # Settings and model names as the running Reference service uses them.
    service = gpu_policy.process_on_port(int(os.environ.get('VQ_REFERENCE_PORT', '8182')))
    service_env = service[1] if service else {}
    current = tier_of(env)
    resolved = Path(executable).resolve()
    home = resolved.parents[2] if len(resolved.parents) > 2 else Path.home()  # <base>/ollama/bin/ollama
    state = load_state(home)
    failed = {int(key): value for key, value in (state.get('did_not_fit') or {}).items()}

    if auto:
        models = loaded_models(base)
        spilled = [name for name, share, _ in models if share < 100]
        free, _ = free_mib(env)
        available = None if free is None else free + int(sum(vram for _, _, vram in models) / 1048576)
        target = None if available is None else tier_for(available)
        # A setting that did not fit before is tried again only with clearly more memory.
        while target is not None and target < current and target in failed and available < failed[target] + 2000:
            target += 1
        better = target is not None and target < current
        activity = (service_env.get('VQ_MODEL_PRIORITY_DIR') or os.environ.get('VQ_MODEL_PRIORITY_DIR')
                    or '/tmp/vq_ollama_priority').rstrip('/') + '.last-student'
        try:
            quiet = time.time() - os.path.getmtime(activity)
        except OSError:
            quiet = float('inf')
        if not spilled and not (better and quiet >= float(os.environ.get('VQ_GPU_RESTART_QUIET_SEC', '600'))):
            say('[OK] GPU sharing unchanged (%s; about %s MiB available to it).' % (describe(current), available))
            return 0
        say('[AUTO] %s' % ('models not fully on the GPU: ' + ', '.join(spilled) if spilled else 'more GPU memory is free now'))

    log = home / 'ollama.log'
    keep = service_env.get('VQ_MODEL_KEEP_ALIVE') or '30m'
    try:  # the teaching services' context size (ollama_cancel.OLLAMA_NUM_CTX)
        num_ctx = max(8192, int(service_env.get('VQ_NUM_CTX') or 8192))
    except ValueError:
        num_ctx = 8192
    wanted = [service_env.get('VQ_GEMMA_MODEL_TAG') or os.environ.get('VQ_GEMMA_MODEL_TAG') or 'gemma4:12b',
              service_env.get('VQ_MISTRAL_MODEL_TAG') or os.environ.get('VQ_MISTRAL_MODEL_TAG') or 'ministral-3:8b-instruct-2512-q4_K_M']

    say('[1/4] Model Ollama: pid %d, port %d, parallel %s, loaded models %s' % (
        pid, port, env.get('OLLAMA_NUM_PARALLEL', '1'), env.get('OLLAMA_MAX_LOADED_MODELS', '1')))
    say('[2/4] Stopping it (the embedding Ollama keeps running)...')
    stop(pid)
    free, total = free_mib(env)
    tier = tier_for(free)
    while tier < len(TIERS) - 1 and tier in failed and (free or 0) < failed[tier] + 2000:
        tier += 1  # did not fit with about this much memory before
    say('[3/4] Free GPU memory: %s MiB of %s MiB' % (free if free is not None else 'unknown', total if total is not None else 'unknown'))
    env = dict(env)
    placed = []
    while True:
        parallel, loaded = TIERS[tier][2:]
        say('      Trying parallel %d, loaded models %d: %s' % (parallel, loaded, describe(tier)))
        env.update({'OLLAMA_NUM_PARALLEL': str(parallel), 'OLLAMA_MAX_LOADED_MODELS': str(loaded)})
        with open(str(log), 'ab') as stream:
            process = subprocess.Popen([executable] + rest, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                                       stderr=stream, start_new_session=True)

        def ready():
            try:
                return bool(api(base, '/api/version', timeout=3))
            except Exception:
                return False
        if not wait_until(ready, 60):
            say('[ERROR] Ollama did not answer after the restart. Log: %s' % log)
            return 1
        for model in wanted[:loaded]:
            try:
                # Same context size as the teaching services, so their first request does not reload it.
                api(base, '/api/generate', {'model': model, 'prompt': '', 'keep_alive': keep, 'stream': False,
                                            'options': {'num_ctx': num_ctx}}, timeout=240)
            except Exception as error:
                say('      Could not load %s now (%s); it loads on first use.' % (model, str(error)[:120]))
        placed = loaded_models(base)
        if all(share >= 100 for _, share, _ in placed) or tier == len(TIERS) - 1:
            break
        say('      A model did not fit completely on the GPU; using a smaller setting.')
        if free is not None:
            failed[tier] = free
        stop(process.pid, process)
        tier += 1
    try:
        (home / 'ollama-capacity.json').write_text(json.dumps({'tier': tier, 'free_mib': free, 'updated_at': time.time(),
                                                               'did_not_fit': {str(key): value for key, value in failed.items()}}))
        (home / 'ollama-capacity.env').write_text(
            'export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-%d}"\nexport OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-%d}"\n'
            % TIERS[tier][2:])
    except OSError:
        pass
    say('[4/4] Restarted. Loaded: %s' % (', '.join('%s %d%% GPU' % (name, share) for name, share, _ in placed) or 'none yet'))
    parallel, loaded = TIERS[tier][2:]
    if parallel >= 2 and loaded >= 2:
        say('[DONE] 學生問答和備課現在可以同時進行（Gemma 和 Ministral 都在 GPU 上）。網站一分鐘內自動套用。')
    elif parallel >= 2:
        say('[DONE] 學生問答和備課現在可以同時進行（用 Gemma 時）。學生改選 Ministral 時會讓學生先。網站一分鐘內自動套用。')
    else:
        say('[DONE] GPU 剩餘記憶體不夠，維持「學生優先」模式。伺服器空出來時，系統會自己切換。')
    if any(share < 100 for _, share, _ in placed):
        say('[NOTE] 有模型沒有完全放在 GPU 上，可能會變慢。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
