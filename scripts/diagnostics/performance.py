#!/usr/bin/env python3
"""Inspect the actual teaching host; optionally benchmark CPU PDF work.

No model inference, cloud requests, service restarts or setting changes. Run on
the teaching server, not on the browser computer. Recommendations apply only
to the sampled PDF stage; Q&A latency and shared-GPU load must also be checked.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from cpu_budget import available_cpus, cpu_workers, cpu_workers_idle
import gpu_policy


def resources():
    sharing = gpu_policy.describe()
    result = {'available_cpu_cores': available_cpus(), 'configured_cpu_workers': cpu_workers_idle(),
              'cpu_workers_now': cpu_workers(), 'gpu_sharing': sharing,
              'gpu_inference_limit': sharing['student_slots'] + sharing['background_slots'] if sharing['mode'] == 'concurrent' else 1,
              'measured_gpu_throughput': False}
    try:
        result['memory'] = {row.split(':')[0]: row.split(':')[1].strip()
                            for row in Path('/proc/meminfo').read_text().splitlines()
                            if row.startswith(('MemTotal:', 'MemAvailable:', 'SwapTotal:', 'SwapFree:'))}
    except OSError:
        result['memory'] = 'unavailable'
    try:
        command = ['nvidia-smi', '--query-gpu=name,memory.total,memory.used,utilization.gpu',
                   '--format=csv,noheader,nounits']
        response = subprocess.run(command, capture_output=True, text=True, timeout=10)
        result['gpu_snapshot'] = response.stdout.strip() if response.returncode == 0 else 'unavailable'
    except (OSError, subprocess.TimeoutExpired):
        result['gpu_snapshot'] = 'unavailable'
    return result


def read_page(job):
    filename, number, use_ocr = job
    import fitz
    from content_integrity import extract_page
    from pdf_ocr import LocalOCR
    # Each process owns its Document. No PyMuPDF objects cross a thread/process.
    with fitz.open(filename) as document:
        page = document[number]
        try:
            row = extract_page(page, number + 1)
        except Exception:
            row = {'text': ''}
        method = 'native'
        if use_ocr and len(row.get('text', '').strip()) < 40:
            row = LocalOCR().read(page)
            method = 'ocr'
        return {'page': number + 1, 'characters': len(row.get('text', '')),
                'method': method, 'ok': bool(row.get('text'))}


def benchmark(filename, pages=12, repeats=3, candidates=(1, 2, 4)):
    import fitz
    with fitz.open(filename) as document:
        count = document.page_count
    if count < 1:
        raise ValueError('PDF has no pages')
    # Spread the sample over the book rather than measuring only its cover.
    sample = sorted(set(round(i * (count - 1) / max(1, min(count, pages) - 1))
                        for i in range(min(count, pages))))
    results = []
    previous = os.environ.get('VQ_CPU_WORKERS')
    try:
        for workers in sorted(set(n for n in candidates if 1 <= n <= available_cpus())):
            times = []
            coverage = []
            for repeat in range(repeats):
                os.environ['VQ_CPU_WORKERS'] = str(workers)
                started = time.monotonic()
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    rows = list(pool.map(read_page, [(filename, n, True) for n in sample]))
                times.append(time.monotonic() - started)
                coverage.append(sum(row['ok'] for row in rows))
            results.append({'workers': workers, 'seconds': times,
                            'median_seconds': statistics.median(times),
                            'readable_pages': coverage, 'sampled_pages': len(sample)})
    finally:
        if previous is None:
            os.environ.pop('VQ_CPU_WORKERS', None)
        else:
            os.environ['VQ_CPU_WORKERS'] = previous
    # A faster run which lost pages is never selected.
    coverage = max(max(row['readable_pages']) for row in results)
    eligible = [row for row in results if coverage>0 and min(row['readable_pages']) == coverage]
    recommended = min(eligible, key=lambda row: row['median_seconds'])['workers'] if eligible else None
    return {'scope': 'CPU text extraction and OCR only; no model accuracy or GPU benchmark',
            'sampled_page_numbers': [n + 1 for n in sample], 'results': results,
            'candidate_cpu_workers': recommended,
            'note': 'Repeat under representative shared load; verify formula/table accuracy and Q&A latency before applying.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--pages', type=int, default=12)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--course-directory', type=Path, help='Private course workspace whose step metrics should be included')
    args = parser.parse_args()
    if args.pages < 1 or args.repeats < 1:
        parser.error('pages and repeats must be positive')
    report = {'started_at': time.time(), 'resources_before': resources()}
    if args.course_directory:
        report['course_steps']={}
        for path in sorted((args.course_directory/'_steps').glob('*/metrics.json')):
            report['course_steps'][path.parent.name]=json.loads(path.read_text(encoding='utf-8'))
    if args.pdf:
        report['cpu_benchmark'] = benchmark(str(args.pdf.resolve()), args.pages, args.repeats)
    report['resources_after'] = resources()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        from work_steps import atomic_json
        atomic_json(args.output, report)
    print(text)


if __name__ == '__main__':
    main()
