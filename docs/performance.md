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

By default a background model call finishes before yielding to a waiting
student. Vision transcription and pixel review release the shared GPU lock
between calls. Student cancellation, course deletion and a bounded background
wall-clock timeout can still interrupt a call. The unfinished call may need to
run again; token-level model-state restoration is not provided. Aging admits
long-waiting background work; it does not reserve GPU time against other users'
independent applications.

Text-rich pages with a bounded graphical region can be cropped for visual
reading. Every embedded image and vector drawing is included, with margins for
captions. Scans, unreadable text and unsupported APIs retain the full page.
Independent pixel review is retained. The image preparation progress row is
removed from the teaching interface; image processing and diagnostics continue.

## Resource configuration

The GPU model lock remains one request across the teaching services. This is a
conservative shared-GPU policy, not a measured optimum or a GPU reservation.
CPU OCR rendering/recognition and course embedding requests share a process-wide
and cross-process slot budget. Tesseract and embedding calls use one internal
CPU thread. PDF documents are not passed into OCR threads.

| Setting | Default | Meaning |
|---|---|---|
| `VQ_CPU_WORKERS` | Smaller of 2 and available CPU cores | Combined CPU-heavy task slots; constrained by CPU affinity and cgroup quota. |
| `VQ_CPU_SLOT_DIR` | Per-user directory under the system temporary directory | Same slot directory and limit must be used by all teaching services. |
| `VQ_OCR_WORKERS` | CPU work budget | OCR workers, capped by the combined CPU budget. Prefetch is at most twice this count. |
| `VQ_BACKGROUND_PREEMPT` | `boundary` | Finish the current step before yielding. `immediate` restores immediate student preemption. |
| `VQ_BACKGROUND_MAX_WAIT_SEC` | `120` | Age waiting background work to reduce starvation within this application. |
| `VQ_BACKGROUND_STEP_TIMEOUT_SEC` | `300` | Background streaming-call wall-clock limit; completed earlier steps remain saved. |
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
during review, partial-ingestion recovery, chapter publication, queue aging,
step timeouts and the preparation UI. They do not establish a production
speedup on the laboratory's shared GPU.
