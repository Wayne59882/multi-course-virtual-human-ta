#!/usr/bin/env python3
"""Read-only report on uploaded courses and background preparation.

Run on the server (nothing is changed, no passwords, keys or e-mail addresses are printed):
    python3 /work103/wayne6/mc-vhta-source/scripts/diagnostics/course_status.py [APP_DIR]
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

APP = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('VHTA_APP_DIR') or
           '/work103/wayne6/NTULUNG_TA_v241_12_1_QUIZ_NAV_VERIFIED')
COURSES = APP / 'course_workspaces'
NOW = time.time()


def ago(seconds):
    if not seconds:
        return '?'
    minutes = int((NOW - float(seconds)) // 60)
    return '%d min ago' % minutes if minutes < 120 else '%.1f h ago' % (minutes / 60.0)


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def run(command, timeout=10):
    try:
        out = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return out.stdout.decode('utf-8', 'replace').strip()
    except Exception as error:  # report only
        return '(%s: %s)' % (command[0], error)


def tail(path, lines=25, keep=None):
    try:
        rows = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    if keep:
        rows = [row for row in rows if any(word in row for word in keep)]
    return rows[-lines:]


print('=== MC-VHTA course status  %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
print('app:', APP)
state = read(APP / 'logs' / 'auto_sync_state.json') or {}
print('deployed commit:', str(state.get('commit', '?'))[:7], '| updated', ago(state.get('updated_at')),
      '| site', state.get('site_url') or ('pending' if state.get('site_pending') else '-'))
for name in ('reference_server', 'gateway', 'kb_server'):
    log = APP / 'logs' / (name + '_sync.log')
    if log.exists():
        print('%s log last written %s' % (name, ago(log.stat().st_mtime)))

print('\n--- jobs (newest 8) ---')
jobs = sorted((read(p) or {} for p in (COURSES / '_jobs').glob('*.json')), key=lambda j: j.get('updated_at') or 0)
for job in jobs[-8:]:
    print('%s %-8s %3s%% reading=%s updated %s | %s' % (str(job.get('id'))[:8], job.get('status'), job.get('progress'),
          job.get('reading', '(old)'), ago(job.get('updated_at')), str(job.get('message') or job.get('error') or '')[:150]))

print('\n--- courses ---')
for manifest_path in sorted(COURSES.glob('*/course_manifest.json'), key=lambda p: p.stat().st_mtime):
    m = read(manifest_path) or {}
    units = m.get('units') or []
    pages = sum(int(u.get('end_page') or 0) - int(u.get('start_page') or 0) + 1 for u in units)
    print('\n# %s  [%s]  status=%s  manifest updated %s' % (m.get('course_name'), str(m.get('course_id'))[:8], m.get('status'), ago(manifest_path.stat().st_mtime)))
    print('  units=%d pages=%d figure_pages=%d source_review_required=%s' % (
        len(units), pages, len(m.get('figure_pages') or []), bool((m.get('source_review') or {}).get('required'))))
    for kind, info in sorted((m.get('preparation') or {}).items()):
        info = info or {}
        detail = ' '.join('%s=%s' % (k, info[k]) for k in ('completed', 'total', 'completed_units', 'total_units', 'ready_units', 'current_page') if k in info)
        print('  %-9s %-10s %s %s %s' % (kind, info.get('status'), detail, str(info.get('message') or '')[:110],
              ('ERROR: ' + str(info.get('error'))[:200]) if info.get('error') else ''))
    for u in units:
        print('   - %-34s pp.%s-%s script=%s questions=%s blocked=%s' % (
            str(u.get('title'))[:34], u.get('start_page'), u.get('end_page'), u.get('script_status') or ('ready' if u.get('script') else '-'),
            u.get('question_count'), bool(u.get('source_blocked'))))
    figures = read(manifest_path.parent / 'figure_cache.json')
    if figures is not None:
        print('  figure pages read so far:', len(figures))

print('\n--- processes ---')
print(run(['bash', '-c', "ps -eo pid,user,etime,pcpu,rss,args | grep -E 'reference_server|gateway.py|kb_server|ollama|llama-server|whisper' | grep -v -E 'grep|bash -c' | cut -c1-170"]))
print('\n--- models loaded (PROCESSOR shows GPU vs CPU share; anything on CPU is many times slower) ---')
for host in ('127.0.0.1:11435', '127.0.0.1:11434'):
    binary = '/work103/wayne6/ollama/bin/ollama' if Path('/work103/wayne6/ollama/bin/ollama').exists() else 'ollama'
    print('[%s]' % host)
    print(run(['bash', '-c', 'OLLAMA_HOST=%s %s ps 2>&1 | head -6' % (host, binary)]))
print('\n--- GPU ---')
print(run(['bash', '-c', 'nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>&1 | head -4']))
print(run(['bash', '-c', 'nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv,noheader 2>&1 | head -12']))
print('\n--- OCR (Tesseract) ---')
print(run(['bash', '-c', 'command -v tesseract >/dev/null && { tesseract --version 2>&1 | head -1; tesseract --list-langs 2>&1 | tail -n +2 | tr "\\n" " "; } || echo "tesseract NOT installed: pages without a text layer must be read by the vision model"']))
print('\n--- recent reference_server errors ---')
for row in tail(APP / 'logs' / 'reference_server_sync.log', 30, ('Error', 'Traceback', 'error', 'Exception', 'preempt', 'Killed')):
    print(row[:220])
print('\n--- last auto-sync lines ---')
for row in tail(APP / 'logs' / 'auto_sync.log', 12):
    print(row[:220])
print('\n=== end of report ===')
