"""The minimum-specification check gives a verdict from what Ollama reports; a stand-in Ollama plays each machine."""
import argparse
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_minimum_spec', str(ROOT / 'scripts' / 'diagnostics' / 'check_minimum_spec.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
GIB = 1024 ** 3
both = ['gemma4:12b', 'ministral-3:8b-instruct-2512-q4_K_M']
mod.host = lambda: {'cpu_cores': 8, 'ram_gb': 32.0, 'gpu': 'stand-in GPU', 'gpu_gb': 12.0}

machine = {}   # what the stand-in reports
last = []      # the model asked most recently


class Handler(BaseHTTPRequestHandler):
    def _send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        assert self.path == '/api/ps', self.path
        self._send({'models': [{'name': name, 'size': 8 * GIB, 'size_vram': int(8 * GIB * machine['gpu_share'])} for name in ([last[-1]] if machine['one_at_a_time'] else both)]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])).decode())
        last.append(payload['model'])
        assert payload['options']['num_ctx'] == 8192 and payload['stream'] is False, payload
        self._send({'message': {'content': 'ok answer'}, 'eval_count': 100, 'eval_duration': int(100 / machine['tps'] * 1e9)})

    def log_message(self, *args):
        pass


server = HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
args = argparse.Namespace(ollama='http://127.0.0.1:%d' % server.server_address[1], gemma='gemma4:12b', mistral='ministral-3:8b-instruct-2512-q4_K_M', ctx=8192, min_tps=10.0)
def verdict(**state):
    machine.update(state)
    lines = []
    return mod.run(args, out=lines.append), lines


assert verdict(gpu_share=1.0, tps=30.0, one_at_a_time=False)[0] == 'MEETS THE RECOMMENDED SPECIFICATION'
# Only one model stays loaded (a 12 GB card): still the minimum.
assert verdict(gpu_share=1.0, tps=30.0, one_at_a_time=True)[0].startswith('MEETS THE MINIMUM SPECIFICATION')
# A model partly in system RAM (not enough GPU memory) or answering too slowly is below the minimum.
assert verdict(gpu_share=0.6, tps=30.0, one_at_a_time=False)[0] == 'BELOW THE MINIMUM SPECIFICATION'
assert verdict(gpu_share=1.0, tps=4.0, one_at_a_time=False)[0] == 'BELOW THE MINIMUM SPECIFICATION'
# An unreachable Ollama is a failed check, never an exception.
args.ollama = 'http://127.0.0.1:9'
assert verdict(gpu_share=1.0, tps=30.0, one_at_a_time=False)[0] == 'BELOW THE MINIMUM SPECIFICATION'
server.shutdown()
print('PASS: minimum-specification check separates recommended, minimum and below-minimum machines')
