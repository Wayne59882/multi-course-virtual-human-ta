# Course preparation performance

Preparation publishes each extracted chapter before reading the next chapter.
Lecture, question and embedding workers operate on committed snapshots. A source
revision schedules another pass if a chapter arrived during a running worker.
Partially ingested courses remain marked as preparing. A restart resumes the
remaining chapters and discards any chunks not committed in the manifest.

## Completed-step checkpoints

Private `_steps/` directories inside each course workspace store complete model
responses, including drafts pending review. The existing source, formula and
independent answer-review gates still decide what can be published. Drafts are
not approved answers. Request keys include evidence, prompts, endpoint, model
metadata, output budget and an explicit cache revision. Interrupted calls are
not stored as complete. Semantic rejection invalidates the relevant drafts;
transport failures leave completed steps available for another attempt.

## Sharing the GPU between students and preparation

`scripts/lib/gpu_capacity.sh` chooses the private Ollama's parallel request
count and loaded-model count from the GPU memory that is free when Ollama
starts (`app/gpu_policy.py` reads the same values):

| Free GPU memory | Ollama requests at once | Models kept loaded | Mode |
|---|---|---|---|
| at least `VQ_GPU_FULL_MIB` (20000 MiB) | 3 | 2 | concurrent: a student plus two preparation steps |
| at least `VQ_GPU_SHARED_MIB` (14000 MiB) | 2 | 2 | concurrent: a student plus one preparation step |
| at least `VQ_GPU_SINGLE_MIB` (10000 MiB) | 2 | 1 | concurrent on one model (Gemma); a student using Ministral goes first |
| less | 1 | 1 | exclusive |

In concurrent mode a student answer has its own request slot and starts while
course preparation is generating; preparation is not interrupted and uses the
remaining slots, so several chapters' lectures are written at once. Gemma and
Ministral stay loaded together, so a student using Ministral while Gemma
prepares a course does not unload either model.

In exclusive mode one request runs at a time. A waiting student always goes
first: preparation does not start, and a running preparation step gets
`VQ_BACKGROUND_GRACE_SEC` (12 s) to finish before it is interrupted.
Completed steps are saved, so only the interrupted step runs again. Preparation
never moves ahead of a waiting student. Course deletion and the background
wall-clock timeout still interrupt a call immediately.

Figure reading is a supplement. It starts a model call only after students
have been quiet for `VQ_FIGURE_IDLE_SEC` (60 s), and it never holds back
course publication: text-based Q&A, lectures and practice items go live first.

The teaching services read these values from the running private Ollama
process (same Unix user), so only Ollama needs a restart for new values:

```bash
python3 scripts/runtime/restart_model_ollama.py
```

It restarts only the model Ollama (not the embedding Ollama), keeps its
binary, model folder and environment, chooses the values from the GPU memory
free after it stopped, loads Gemma (and Ministral when two models fit) and
checks that each model is fully on the GPU; if not, it steps down to the next
setting and remembers that. The services switch within a minute.

Nobody needs to run it by hand: the automatic updater runs it with `--auto`
every ten minutes. It moves to a larger setting only when more GPU memory is
free and students have been quiet for `VQ_GPU_RESTART_QUIET_SEC` (600 s), and
steps down at once when a model no longer fits. These settings do not reserve
GPU memory against other users' processes.

With one model loaded, a student who picks the other model than the one
preparation is using is served first: preparation yields after the grace
period instead of making the GPU swap models back and forth.

## Automatic recovery

A rejected lecture draft (not mainly Traditional Chinese, unreadable symbols,
unsupported claims) is followed by a differently asked draft: a shorter evenly
spaced excerpt and a tighter five-paragraph structure. While later chapters are
still being extracted, a chapter that failed every variant waits for the final
pass instead of being retried after each new chapter; it gets one more full set
of variants on the final pass. The lecture output limit is
`VQ_LECTURE_MAX_TOKENS` (2000) so normal lectures are not cut off. Review gates
are unchanged: nothing that fails review is published.

Figure pages are read as the cropped graphical region, then the whole page,
then the top and bottom halves of the page, each with the independent pixel
review. A page that no variant can verify with the installed model is not read
again until the model or PDF changes; its text remains in course Q&A. Pages
interrupted by a model or connection failure are read once more in the same
pass and again after a restart.

Chapters without enough readable text are read again automatically, once,
after the course's other preparation finishes (OCR, then the vision model).

Text-rich pages with a bounded graphical region can be cropped for visual
reading. Every embedded image and vector drawing is included, with margins for
captions. Scans, unreadable text and unsupported APIs retain the full page.
The image preparation progress row is removed from the teaching interface;
image processing and diagnostics continue.

## Resource configuration

CPU OCR rendering/recognition and course embedding requests share a process-wide
and cross-process slot budget. Tesseract and embedding calls use one internal
CPU thread. PDF documents are not passed into OCR threads.

| Setting | Default | Meaning |
|---|---|---|
| `VQ_GPU_SHARING` | `auto` | `concurrent` or `exclusive` overrides the choice from Ollama's capacity. |
| `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS` | chosen from free GPU memory | Explicit values override the automatic choice. |
| `VQ_BACKGROUND_MODEL_SLOTS` | Ollama requests minus student slots | Preparation steps at once in concurrent mode. |
| `VQ_CPU_WORKERS` | Smaller of 4 and available CPU cores | CPU-heavy task slots while no student is asking; constrained by CPU affinity and cgroup quota. |
| `VQ_CPU_WORKERS_BUSY` | 2 | CPU-heavy task slots while students are asking (within `VQ_CPU_BUSY_WINDOW_SEC`, 60 s). |
| `VQ_CPU_SLOT_DIR` | Per-user directory under the system temporary directory | Same slot directory and limits must be used by all teaching services. |
| `VQ_OCR_WORKERS` | `VQ_CPU_WORKERS` | OCR threads, capped by the CPU budget. Prefetch is at most twice this count. |
| `VQ_BACKGROUND_PREEMPT` | `grace` | Exclusive mode: `grace` interrupts after `VQ_BACKGROUND_GRACE_SEC`; `immediate` at once; `boundary` never. |
| `VQ_FIGURE_IDLE_SEC` | `60` | Student quiet time before figure reading starts a model call. |
| `VQ_BACKGROUND_STEP_TIMEOUT_SEC` | `300` | Background streaming-call wall-clock limit; completed earlier steps remain saved. |
| `VQ_LECTURE_MAX_TOKENS` | `2000` | Lecture output limit (the prompt still asks for five to seven paragraphs). |
| `VQ_STEP_CACHE_REVISION` | `1` | Change to invalidate model-step keys after operator-controlled configuration changes. |

Metadata is read once per preparation scope. When replacing weights or changing
server configuration during an ongoing run, restart the relevant preparation
workers and increment `VQ_STEP_CACHE_REVISION`. Existing approved page readings
and published content are preserved; a new model evaluation should use a new
workspace rather than treating old approved content as fresh model output.

## Measure on the actual teaching server

From the repository root, inspect resources and a course's step counters:

```bash
python3 scripts/diagnostics/performance.py
python3 scripts/diagnostics/performance.py --course-directory /actual/private/course-workspace
```

Counters separate `model_lock_wait_seconds`, `background_queue_seconds`,
`model_request_seconds`, model load/prompt/token durations, checkpoint hits and
preemptions. Queue counters describe different waits and must not be summed
blindly; API request duration may itself include the model server's queue.

Benchmark CPU extraction/OCR with a representative private PDF:

```bash
python3 scripts/diagnostics/performance.py --pdf /actual/material.pdf --pages 20 --repeats 3 --output /tmp/course-cpu-performance.json
```

The command compares 1, 2 and 4 workers within the detected CPU allowance. Each
worker opens its own PDF; no vision or cloud model is called. It reports a CPU
candidate without applying it or changing services. A faster run that loses
readable pages is not selected. This is not a formula-accuracy evaluation or a
whole-system/GPU benchmark. Repeat under comparable shared load and check
student Q&A latency, RAM/VRAM, first-chapter readiness and total preparation
time before applying a candidate. Record cache state and model configuration.

Offline regressions exercise concurrent checkpoints, a process interrupted
during review, partial-ingestion recovery, chapter publication, student and
preparation GPU sharing, the exclusive-mode grace period, quiet-time figure
reading, automatic recovery, step timeouts and the preparation UI. They use a
stand-in model server and do not establish a production speedup or the VRAM
fit of two models on the laboratory's shared GPU.
