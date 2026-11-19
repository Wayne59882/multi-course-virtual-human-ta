#!/usr/bin/env python3
"""Check whether THIS machine meets the minimum / recommended specification.

Run it on the machine under test (any NVIDIA computer with Ollama running and the two
models pulled). It sends a few short fixed questions to Ollama, so it needs no course,
account or the rest of the application. Each check prints PASS or FAIL and the last
line gives the verdict.

    ollama pull gemma4:12b
    ollama pull ministral-3:8b-instruct-2512-q4_K_M
    python3 scripts/diagnostics/check_minimum_spec.py

Minimum: the preparation model (Gemma) and the student model (Ministral) each load
completely into GPU memory with the application's 8192-token context, answer at a
readable speed, and the machine has 16 GB of RAM.
Recommended: in addition both models stay in GPU memory together and two requests at
once are answered. For that, start Ollama with OLLAMA_MAX_LOADED_MODELS=2 and
OLLAMA_NUM_PARALLEL=2 (the application chooses these itself from free GPU memory).
The speed bar (--min-tps) is this script's own choice, not a published standard.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

GIB = 1024.0 ** 3
QUESTION = 'In three sentences, explain how a ventilation-perfusion mismatch lowers arterial oxygen.'


def post_json(base, path, payload, timeout=900):
    request = Request(base.rstrip('/') + path, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def get_json(base, path, timeout=10):
    with urlopen(base.rstrip('/') + path, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def ask(base, model, ctx, tokens=160):
    """One non-streaming answer; returns (seconds, tokens_per_second, answer_text)."""
    started = time.time()
    data = post_json(base, '/api/chat', {'model': model, 'stream': False, 'keep_alive': '10m',
                                         'messages': [{'role': 'user', 'content': QUESTION}],
                                         'options': {'num_ctx': ctx, 'num_predict': tokens, 'temperature': 0}})
    seconds = time.time() - started
    count, nanos = data.get('eval_count') or 0, data.get('eval_duration') or 0
    return seconds, (count / (nanos / 1e9) if nanos else 0.0), str((data.get('message') or {}).get('content') or '')


def placement(base):
    """{model name: (size, size_vram)} of everything Ollama holds now."""
    return {str(m.get('name') or m.get('model')): (int(m.get('size') or 0), int(m.get('size_vram') or 0)) for m in get_json(base, '/api/ps').get('models') or []}


def fully_on_gpu(base, model):
    """(entirely in GPU memory, size, size in GPU memory) for the loaded model with exactly this tag."""
    size, vram = placement(base).get(model, (0, 0))
    return size > 0 and vram >= 0.98 * size, size, vram


def host():
    info = {'cpu_cores': 0, 'ram_gb': 0.0, 'gpu': 'nvidia-smi not available', 'gpu_gb': 0.0}
    try:
        info['cpu_cores'] = len(os.sched_getaffinity(0))
    except Exception:
        info['cpu_cores'] = os.cpu_count() or 0
    try:
        with open('/proc/meminfo') as handle:
            info['ram_gb'] = int(handle.readline().split()[1]) * 1024.0 / GIB
    except Exception:
        pass
    try:
        line = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'], universal_newlines=True, timeout=10).splitlines()[0]
        name, total = [part.strip() for part in line.rsplit(',', 1)]
        info['gpu'], info['gpu_gb'] = name, int(total) / 1024.0
    except Exception:
        pass
    return info


class Report:
    def __init__(self):
        self.rows = []

    def check(self, label, ok, detail=''):
        self.rows.append((label, bool(ok)))
        print('%s  %s%s' % ('PASS' if ok else 'FAIL', label, ('  (' + detail + ')') if detail else ''), flush=True)
        return bool(ok)

    def passed(self, *labels):
        status = dict(self.rows)
        return all(status.get(label) for label in labels)


def run(args, out=print):
    report = Report()
    machine = host()
    out('Machine: %s, %.1f GB GPU memory, %s CPU cores, %.1f GB RAM' % (machine['gpu'], machine['gpu_gb'], machine['cpu_cores'], machine['ram_gb']))
    report.check('RAM is at least 16 GB', machine['ram_gb'] >= 15.0, '%.1f GB' % machine['ram_gb'])
    names = {}
    for role, model in (('Gemma (preparation)', args.gemma), ('Ministral (students)', args.mistral)):
        label = '%s loads fully into GPU memory at %d tokens of context' % (role, args.ctx)
        try:
            seconds, tps, text = ask(args.ollama, model, args.ctx)
            ok, size, vram = fully_on_gpu(args.ollama, model)
            report.check(label, ok, '%.1f GB loaded, %.1f GB in GPU memory' % (size / GIB, vram / GIB))
            report.check('%s answers at a readable speed' % role, bool(text.strip()) and tps >= args.min_tps, '%.1f tokens/s, %.0f s for the answer; bar %.0f' % (tps, seconds, args.min_tps))
            names[role] = model
        except Exception as error:
            report.check(label, False, 'could not run %s: %s (is it pulled and is Ollama running at %s?)' % (model, error, args.ollama))
    try:
        resident = placement(args.ollama)
        both = len([n for n, (s, v) in resident.items() if s and v >= 0.98 * s]) >= 2
        report.check('Both models stay in GPU memory together', both, '%d model(s) resident: %s' % (len(resident), ', '.join(sorted(resident)) or 'none'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            started = time.time()
            results = list(pool.map(lambda model: ask(args.ollama, model, args.ctx, 80), [args.mistral, args.gemma]))
        report.check('Two requests at once are both answered', all(r[2].strip() for r in results), '%.0f s together' % (time.time() - started))
    except Exception as error:
        report.check('Two requests at once are both answered', False, str(error))
    minimum = ['RAM is at least 16 GB'] + [row[0] for row in report.rows if 'fully into GPU' in row[0] or 'readable speed' in row[0]]
    recommended = minimum + ['Both models stay in GPU memory together', 'Two requests at once are both answered']
    if report.passed(*recommended):
        verdict = 'MEETS THE RECOMMENDED SPECIFICATION'
    elif report.passed(*minimum):
        verdict = 'MEETS THE MINIMUM SPECIFICATION (one model at a time)'
    else:
        verdict = 'BELOW THE MINIMUM SPECIFICATION'
    out('Verdict: %s' % verdict)
    return verdict


def main(argv=None):
    host_default = os.environ.get('OLLAMA_HOST', '127.0.0.1:11434')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ollama', default=host_default if host_default.startswith('http') else 'http://' + host_default)
    parser.add_argument('--gemma', default='gemma4:12b')
    parser.add_argument('--mistral', default='ministral-3:8b-instruct-2512-q4_K_M')
    parser.add_argument('--ctx', type=int, default=8192)
    parser.add_argument('--min-tps', type=float, default=10.0, help='slowest readable answer speed in tokens per second')
    verdict = run(parser.parse_args(argv))
    return 0 if not verdict.startswith('BELOW') else 1


if __name__ == '__main__':
    sys.exit(main())
