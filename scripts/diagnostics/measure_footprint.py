#!/usr/bin/env python3
"""Measure what the teaching system really uses on this host while it works.

Read-only: it asks the private Ollama which models are loaded (/api/ps), reads
nvidia-smi and /proc/meminfo, and writes a short report. It sends no prompts,
restarts nothing and changes no setting. Run it on the teaching server, start
it, then use the site as in a normal day for the whole window: students asking
questions while a course is being prepared. The peak values it prints are the
measured minimum the system needs on that hardware.

    python3 scripts/diagnostics/measure_footprint.py --minutes 10
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from urllib.request import urlopen

MIB = 1024.0 * 1024.0


def run(command):
    try:
        return subprocess.check_output(command, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10)
    except Exception:
        return ''


def loaded_models(base):
    """[{name,size,size_vram,context}] of the models Ollama holds right now; None if it cannot be reached."""
    try:
        with urlopen(base.rstrip('/') + '/api/ps', timeout=5) as response:
            rows = json.loads(response.read().decode('utf-8')).get('models') or []
    except Exception:
        return None
    return [{'name': str(row.get('name') or row.get('model') or '?'), 'size': int(row.get('size') or 0),
             'size_vram': int(row.get('size_vram') or 0), 'context': row.get('context_length')} for row in rows]


def gpu_rows():
    """[(name,total_mib,used_mib,free_mib)] per GPU, empty without nvidia-smi."""
    rows = []
    for line in run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,memory.free', '--format=csv,noheader,nounits']).splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) == 4 and all(part.isdigit() for part in parts[1:]):
            rows.append((parts[0], int(parts[1]), int(parts[2]), int(parts[3])))
    return rows


def memory_mib():
    """(total, used) system RAM in MiB; used = total - available."""
    values = {}
    try:
        with open('/proc/meminfo') as handle:
            for line in handle:
                key, _, rest = line.partition(':')
                values[key] = int(rest.split()[0]) / 1024.0
    except Exception:
        return 0, 0
    total = values.get('MemTotal', 0)
    return total, total - values.get('MemAvailable', 0)


def own_rss_mib():
    """RAM held by this user's Ollama and Python processes."""
    total = 0.0
    for line in run(['ps', '-u', str(os.getuid()), '-o', 'rss=,comm=']).splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and any(name in parts[1] for name in ('ollama', 'python')):
            total += int(parts[0]) / 1024.0
    return total


def cpu_cores():
    try:
        return len(os.sched_getaffinity(0))
    except Exception:
        return os.cpu_count() or 0


def measure(base, seconds, interval, now=time.time, sleep=time.sleep):
    start = now()
    gpus = gpu_rows()
    ram_total, _ = memory_mib()
    report = {'started': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'cpu_cores': cpu_cores(), 'ram_total_mib': round(ram_total),
              'gpu': [{'name': g[0], 'total_mib': g[1], 'used_at_start_mib': g[2], 'free_at_start_mib': g[3]} for g in gpus],
              'ollama_reachable': False, 'samples': 0, 'peak_ollama_vram_mib': 0, 'peak_models_loaded': 0,
              'peak_gpu_used_mib': 0, 'peak_ram_used_mib': 0, 'peak_own_process_rss_mib': 0, 'models': {}}
    while True:
        report['samples'] += 1
        models = loaded_models(base)
        if models is not None:
            report['ollama_reachable'] = True
            report['peak_models_loaded'] = max(report['peak_models_loaded'], len(models))
            report['peak_ollama_vram_mib'] = max(report['peak_ollama_vram_mib'], round(sum(m['size_vram'] for m in models) / MIB))
            for m in models:
                seen = report['models'].setdefault(m['name'], {'size_mib': 0, 'vram_mib': 0, 'context': None})
                seen['size_mib'] = max(seen['size_mib'], round(m['size'] / MIB))
                seen['vram_mib'] = max(seen['vram_mib'], round(m['size_vram'] / MIB))
                seen['context'] = m['context'] or seen['context']
        used = [g[2] for g in gpu_rows()]
        if used:
            report['peak_gpu_used_mib'] = max(report['peak_gpu_used_mib'], max(used))
        _, ram_used = memory_mib()
        report['peak_ram_used_mib'] = max(report['peak_ram_used_mib'], round(ram_used))
        report['peak_own_process_rss_mib'] = max(report['peak_own_process_rss_mib'], round(own_rss_mib()))
        if now() - start >= seconds:
            return report
        sleep(interval)


def summary(report):
    lines = ['Measured on %s' % report['started'], 'CPU cores available: %s; system RAM: %.1f GB' % (report['cpu_cores'], report['ram_total_mib'] / 1024.0)]
    for g in report['gpu']:
        lines.append('GPU: %s, %.1f GB total, %.1f GB free when the measurement started' % (g['name'], g['total_mib'] / 1024.0, g['free_at_start_mib'] / 1024.0))
    if not report['gpu']:
        lines.append('GPU: nvidia-smi not available')
    if not report['ollama_reachable']:
        lines.append('Ollama could not be reached; give --ollama http://HOST:PORT (default is the private Ollama, 127.0.0.1:11435).')
        return '\n'.join(lines)
    lines.append('Samples: %d. Loaded models at peak: %d.' % (report['samples'], report['peak_models_loaded']))
    lines.append('PEAK GPU memory held by the loaded models (all of this system, in GPU memory): %.1f GB' % (report['peak_ollama_vram_mib'] / 1024.0))
    if report['peak_gpu_used_mib']:
        lines.append('Peak GPU memory in use on the whole card, other users included: %.1f GB' % (report['peak_gpu_used_mib'] / 1024.0))
    lines.append('Peak system RAM in use: %.1f GB; of it this user\'s Ollama and Python: %.1f GB' % (report['peak_ram_used_mib'] / 1024.0, report['peak_own_process_rss_mib'] / 1024.0))
    for name in sorted(report['models']):
        m = report['models'][name]
        lines.append('  model %s: %.1f GB loaded, %.1f GB of it in GPU memory, context %s' % (name, m['size_mib'] / 1024.0, m['vram_mib'] / 1024.0, m['context'] or 'n/a'))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--minutes', type=float, default=10.0, help='how long to sample while the site is in normal use')
    parser.add_argument('--interval', type=float, default=2.0, help='seconds between samples')
    parser.add_argument('--ollama', default=os.environ.get('VQ_MEASURE_OLLAMA') or 'http://' + os.environ.get('OLLAMA_HOST', '127.0.0.1:11435').replace('http://', ''))
    parser.add_argument('--output', help='also write the full JSON report here')
    args = parser.parse_args(argv)
    print('Sampling for %.1f minutes. Use the site normally now: student questions while a course is prepared.' % args.minutes, flush=True)
    report = measure(args.ollama, args.minutes * 60.0, max(0.2, args.interval))
    text = summary(report)
    print(text)
    if args.output:
        with open(args.output, 'w') as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
        print('Full report written to %s' % args.output)
    return 0 if report['ollama_reachable'] else 1


if __name__ == '__main__':
    sys.exit(main())
