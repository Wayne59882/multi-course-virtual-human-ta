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
both modes (student activity is tracked in ollama_cancel). VQ_GPU_SHARING=exclusive or concurrent overrides the automatic
choice.
"""
import os


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


def ollama_parallel():
    return max(1, _int('OLLAMA_NUM_PARALLEL', 1))


def ollama_loaded_models():
    return max(1, _int('OLLAMA_MAX_LOADED_MODELS', 1))


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
            'figure_idle_seconds': idle_seconds()}
