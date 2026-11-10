# Operator Scripts

Run the commands below from the repository root after preparing the environment.
Shell helpers resolve the application directory relative to their own location,
so an absolute script path can also be called from a different working directory.
The launchers do not automatically load `.env`.

## Entry points

| Command | Purpose / effects |
|---|---|
| `python3 scripts/check_offline.py` | Run offline regression checks, source syntax checks, and documentation-link checks. |
| `python3 scripts/check_web_assets.py` | Validate local HTML resources and required avatar URL declarations; no network requests. |
| `bash scripts/start.sh` | Start the complete service; checks prerequisites and restarts existing matching teaching backends. |
| `bash scripts/setup/prepare_reference.sh` | Check/start the embedding service, download BGE-M3 when needed, and build/check the private textbook index. |
| `bash scripts/setup/prepare_stt.sh` | Create the expected speech Python environment if absent and install Faster-Whisper. FFmpeg is installed separately. |
| `bash scripts/setup/prepare_models.sh` | Download the existing Granite, Gemma, and Ministral model tags; Granite is a legacy model rather than a current student-selectable option. |
| `bash scripts/setup/prepare_tts.sh` | Install the server-side edge-tts environment. |
| `python3 scripts/data/prepare_reference.py /path/to/material.pdf --title "Reference material"` | Replace `app/reference_corpus/chunks.jsonl` from a PDF; requires PyMuPDF and overwrites the existing corpus. |
| `bash scripts/deploy/cloudflare_login.sh` | Start the Wrangler device-login flow. |
| `bash scripts/deploy/start_tunnel.sh` | Start/restart the quick Tunnel and write the origin URL under `app/logs/`. |
| `bash scripts/deploy/deploy_worker.sh` | Check local assets, obtain/configure the HTTPS origin, and deploy the Worker and web assets. |

Prepare and source your own settings before starting or deploying the service:

```bash
source .env
bash scripts/start.sh
```

Deployment scripts are operator tools; the offline checks do not execute a real
Cloudflare login, Tunnel, or deployment. The launcher regression test substitutes
external commands in a temporary copy of the project.

## Performance diagnostics

`python3 scripts/diagnostics/performance.py` inspects the host without changing
services. Use `--pdf /actual/material.pdf --pages 20 --repeats 3` to compare CPU
worker counts on the teaching server. See the [performance guide](../docs/performance.md)
for scope, resource limits and interrupted-step recovery.

## Internal helpers

`runtime/` contains service startup helpers. `lib/paths.sh` defines the repository,
application, and script locations for the shell scripts. Application state and
logs remain under `app/` or the configured private environment; they are not
written beside these operator scripts.

The fine-tuning and feedback subsystems retain their own specialized tools under
`app/finetune/` and `app/feedback/`. Historical installation chains remain in
`archive/` and are not used by the current startup entry point.

[Project home](../README.md) · [Development guide](../docs/development.md) · [Setup guide](../docs/getting-started.md) · [Migration guide](../docs/migration.md)
