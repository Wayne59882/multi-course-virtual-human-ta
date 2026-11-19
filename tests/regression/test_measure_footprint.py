"""The footprint measurement script reads a stand-in Ollama and reports peaks without changing anything."""
import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('measure_footprint', str(ROOT / 'scripts' / 'diagnostics' / 'measure_footprint.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GIB = 1024 ** 3
ROUNDS = [[{'name': 'gemma4:12b', 'size': 9 * GIB, 'size_vram': 8 * GIB, 'context_length': 8192}],
          [{'name': 'gemma4:12b', 'size': 9 * GIB, 'size_vram': 9 * GIB, 'context_length': 8192},
           {'name': 'ministral-3:8b', 'size': 7 * GIB, 'size_vram': 7 * GIB, 'context_length': 8192}],
          []]
calls = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == '/api/ps', self.path
        rows = ROUNDS[min(len(calls), len(ROUNDS) - 1)]
        calls.append(1)
        body = json.dumps({'models': rows}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


server = HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % server.server_address[1]

clock = [0.0]
report = mod.measure(base, 4, 1, now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
assert report['ollama_reachable'] and report['samples'] == 5, report
assert report['peak_models_loaded'] == 2, report
assert report['peak_ollama_vram_mib'] == 16 * 1024, report   # the two models together are the peak, not the largest single sample
assert report['models']['gemma4:12b']['vram_mib'] == 9 * 1024 and report['models']['gemma4:12b']['context'] == 8192, report
text = mod.summary(report)
assert 'PEAK GPU memory' in text and '16.0 GB' in text and 'ministral-3:8b' in text, text

# An unreachable Ollama is reported, never raised, and the exit code says so.
assert mod.loaded_models('http://127.0.0.1:9') is None
unreachable = mod.measure('http://127.0.0.1:9', 0, 1)
assert not unreachable['ollama_reachable'] and 'could not be reached' in mod.summary(unreachable)
server.shutdown()
print('PASS: footprint measurement reports the peak of the loaded models and survives an unreachable Ollama')
