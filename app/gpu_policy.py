"""How student answers and course preparation share the local GPU (Python 3.6).

The private Ollama server is started with a parallel request count and a
loaded-model count chosen from the GPU memory that is free at start-up
(scripts/lib/gpu_capacity.sh). This module reads the same settings:

* concurrent mode (Ollama can serve at least two requests and keep two
  models): a student answer and course preparation run at the same time.
  Students keep their own request slot, so preparation never sits in front of
  them; preparation uses the remaining slots, which also lets several chapters
  be written at once. Gemma and Ministral may stay loaded together.
* exclusive mode (too little free GPU memory): one request at a time, as in
  earlier releases. Students go first; a running preparation step gets a short
  grace period to finish and is then interrupted. Completed steps are saved,
  so only the interrupted step runs again.

Figure reading starts only while no student has asked anything recently, in
both modes (student activity is tracked in ollama_cancel). VQ_GPU_SHARING=exclusive
or concurrent overrides the automatic choice.

The settings are read from the running private Ollama process itself (the
process listening on OLLAMA_HOST, same Unix user), so restarting only Ollama
with new values (scripts/runtime/restart_model_ollama.py) takes effect within
a minute without restarting the teaching services. When the process cannot be
inspected, the services' own OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS
are used.
"""
import os
import time

_CACHE = {'at': -1e9, 'value': None}


def _listening_inodes(port):
    inodes = set()
    for name in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(name) as stream:
                next(stream, None)
                for line in stream:
                    fields = line.split()
                    if len(fields) > 9 and fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == port:
                        inodes.add(fields[9])
        except (OSError, ValueError, IndexError):
            pass
    return inodes


def process_on_port(port):
    """(pid, environment) of this user's process listening on a local port, or None."""
    inodes = _listening_inodes(int(port))
    if not inodes:
        return None
    targets = set('socket:[%s]' % inode for inode in inodes)
    try:
        pids = [name for name in os.listdir('/proc') if name.isdigit()]
    except OSError:
        return None
    for pid in pids:
        directory = os.path.join('/proc', pid, 'fd')
        try:
            names = os.listdir(directory)
        except OSError:
            continue  # another user's process
        for name in names:
            try:
                if os.readlink(os.path.join(directory, name)) in targets:
                    with open(os.path.join('/proc', pid, 'environ'), 'rb') as stream:
                        raw = stream.read().decode('utf-8', 'replace')
                    env = dict(item.split('=', 1) for item in raw.split('\0') if '=' in item)
                    return int(pid), env
            except OSError:
                continue
    return None


def ollama_port():
    host = str(os.environ.get('OLLAMA_HOST', '127.0.0.1:11435')).strip()
    host = host.split('://', 1)[-1].split('/', 1)[0]
    try:
        return int(host.rsplit(':', 1)[1])
    except (IndexError, ValueError):
        return 11434


def _ollama_process():
    """Look up the running private Ollama at most once a minute."""
    now = time.monotonic()
    if now - _CACHE['at'] < 60:
        return _CACHE['value']
    found = None
    if str(os.environ.get('VQ_GPU_DETECT_OLLAMA', '1')).strip().lower() not in ('0', 'false', 'off'):
        try:
            found = process_on_port(ollama_port())
        except Exception:
            found = None
    _CACHE.update(at=now, value=found)
    return found


def ollama_settings():
    """The running private Ollama's settings, else this process's environment."""
    found = _ollama_process()
    env = found[1] if found else os.environ
    return {'OLLAMA_NUM_PARALLEL': env.get('OLLAMA_NUM_PARALLEL', '1'),
            'OLLAMA_MAX_LOADED_MODELS': env.get('OLLAMA_MAX_LOADED_MODELS', '1'),
            'source': 'ollama-process' if found else 'environment'}


def _int(name, default):
    try:
        return int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return int(default)


def _float(name, default):
    try:
        return float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return float(default)


def _setting(name):
    try:
        return max(1, int(str(ollama_settings().get(name) or 1).strip()))
    except (TypeError, ValueError):
        return 1


def ollama_parallel():
    return _setting('OLLAMA_NUM_PARALLEL')


def ollama_loaded_models():
    return _setting('OLLAMA_MAX_LOADED_MODELS')


def sharing_mode():
    mode = str(os.environ.get('VQ_GPU_SHARING', 'auto')).strip().lower()
    return mode if mode in ('auto', 'concurrent', 'exclusive') else 'auto'


def concurrent():
    """True when students and preparation may use the model at the same time."""
    mode = sharing_mode()
    if mode == 'exclusive':
        return False
    if mode == 'concurrent':
        return True
    return ollama_parallel() >= 2 and ollama_loaded_models() >= 2


def student_slots():
    return max(1, _int('VQ_STUDENT_MODEL_SLOTS', 1))


def background_slots():
    """Preparation requests that may run at once without delaying students."""
    if not concurrent():
        return 1
    room = max(1, ollama_parallel() - student_slots())
    return max(1, min(room, _int('VQ_BACKGROUND_MODEL_SLOTS', room)))


def keep_models_loaded():
    """Keep Gemma and Ministral loaded together instead of unloading one."""
    return concurrent() and ollama_loaded_models() >= 2


def grace_seconds():
    """Exclusive mode: how long a running preparation step may finish first."""
    mode = str(os.environ.get('VQ_BACKGROUND_PREEMPT', 'grace')).strip().lower()
    if mode == 'immediate':
        return 0.0
    if mode == 'boundary':
        return None  # finish the step; never interrupt
    return max(0.0, _float('VQ_BACKGROUND_GRACE_SEC', 12))


def idle_seconds():
    """Figure reading waits until no student has asked for this long."""
    return max(0.0, _float('VQ_FIGURE_IDLE_SEC', 60))


def describe():
    return {'mode': 'concurrent' if concurrent() else 'exclusive', 'setting': sharing_mode(),
            'ollama_parallel': ollama_parallel(), 'ollama_loaded_models': ollama_loaded_models(),
            'student_slots': student_slots(), 'background_slots': background_slots(),
            'keep_models_loaded': keep_models_loaded(), 'grace_seconds': grace_seconds(),
            'figure_idle_seconds': idle_seconds(), 'settings_source': ollama_settings().get('source')}
