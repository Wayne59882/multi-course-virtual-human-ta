#!/usr/bin/env python3
"""Run existing fixture checks without models, private corpora or API keys."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TESTS = ROOT / "tests" / "regression"
SKIP = {".git", ".venv", "node_modules", "__pycache__", "course_workspaces",
        "logs", ".wrangler", "archive", "legacy", "legacy_benchmarks"}


def source_files():
    for current, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not
                         (Path(current) == APP and d == "auth"))
        for name in sorted(names):
            yield Path(current) / name


def validate_sources(node):
    counts = {"python": 0, "javascript": 0}
    for path in source_files():
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            counts["python"] += 1
        elif path.suffix in (".js", ".cjs"):
            subprocess.run([node, "--check", str(path)], check=True,
                           capture_output=True, text=True, timeout=30)
            counts["javascript"] += 1
    print("PASS syntax: {python} Python / {javascript} JavaScript files".format(**counts), flush=True)
    return counts


def validate_document_links():
    # Check the maintained documentation only; legacy release notes contain
    # historical machine paths and are intentionally outside this check.
    paths = [ROOT / name for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md")]
    paths += sorted((ROOT / "docs").glob("*.md"))
    paths += [APP / "README.md", APP / "reference_corpus/README.md",
              ROOT / "research/three_model_10q/SOURCE_AUDIT_10Q.md",
              ROOT / "research/README.md", ROOT / "tests/README.md", ROOT / "archive/README.md"]
    paths += [ROOT / "scripts/README.md"]
    checked = 0
    for path in paths:
        body = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", body):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            destination = (path.parent / target.split("#", 1)[0]).resolve()
            if not destination.is_relative_to(ROOT) or not destination.exists():
                raise ValueError("Broken link in {}: {}".format(path.relative_to(ROOT), target))
            checked += 1
    print("PASS documentation: {} local links".format(checked), flush=True)
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write an optional JSON result after success")
    args = parser.parse_args()
    node = shutil.which("node")
    if not node:
        parser.error("Node.js 22 or 24 is required")
    node_version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(node_version.lstrip("v").split(".")[0]) < 22:
        parser.error("Node.js 22 or newer is required")
    print("Environment: Python {} / Node {}".format(sys.version.split()[0], node_version), flush=True)
    syntax = validate_sources(node)
    links = validate_document_links()
    suites = [
        ("V/Q numerical constraints", [node, str(TESTS / "test_vq_conservation.cjs")]),
        ("Quiz navigation", [node, str(TESTS / "test_quiz_navigation.cjs")]),
        ("Batch recovery fixtures", [node, str(TESTS / "test_batch_recovery.cjs")]),
        ("Speech pronunciation fixtures", [node, str(TESTS / "test_speech_pronunciation.cjs")]),
        ("Q&A model handoff", [node, str(TESTS / "test_qa_handoff.cjs")]),
        ("Gemini Live natural conversation", [node, str(TESTS / "test_gemini_live.cjs")]),
        ("Cloud models: ChatGPT, Claude, Gemini", [node, str(TESTS / "test_cloud_models.cjs")]),
        ("Site address and old-address forwarding", [node, str(TESTS / "test_site_address.cjs")]),
        ("Login page show-password buttons", [node, str(TESTS / "test_login_page.cjs")]),
        ("Ollama cancellation", [sys.executable, str(TESTS / "test_ollama_cancel.py")]),
        ("Student priority, delete and cancel", [sys.executable, str(TESTS / "test_course_priority_delete.py")]),
        ("Courses take turns on the model", [sys.executable, str(TESTS / "test_image_priority.py")]),
        ("Parallel OCR of scanned pages", [sys.executable, str(TESTS / "test_parallel_ocr.py")]),
        ("Background PDF inspection and builds in progress", [sys.executable, str(TESTS / "test_upload_background.py")]),
        ("Answer job HTTP responses", [sys.executable, str(TESTS / "test_answer_jobs_http.py")]),
        ("Manual stop", [node, str(TESTS / "test_manual_stop.cjs"),
                         "web/shared/js/speech/recognition-controller.js"]),
        ("Question-generation fixtures", [sys.executable, str(TESTS / "test_question_generation.py")]),
        ("Pilot dataset self-test", [sys.executable,
                                    str(ROOT / "research/three_model_10q/benchmark_3models.py"), "--self-test"]),
        ("Static resources and HTTP routes", [sys.executable, str(TESTS / "test_static_assets.py")]),
        ("Operator script routing", [sys.executable, str(TESTS / "test_operator_scripts.py")]),
    ]
    suites.extend([
        ("All-course text integrity", [sys.executable,str(TESTS / "test_content_integrity.py")]),
        ("Math integrity", [sys.executable,str(TESTS / "test_math_integrity.py")]),
        ("All-course display", [node,str(TESTS / "test_content_display.cjs")]),
        ("Math and audio lifecycle", [node,str(TESTS / "test_math_audio.cjs")]),
        ("Large PDF and private upload diagnostics", [sys.executable,str(TESTS / "test_large_pdf.py")]),
        ("Teaching-unit names and page ranges", [sys.executable,str(TESTS / "test_unit_detection.py")]),
        ("Text-first course reading", [sys.executable,str(TESTS / "test_text_first_reading.py")]),
        ("OCR installed without administrator rights", [sys.executable,str(TESTS / "test_user_ocr.py")]),
        ("Administrator manages every course", [sys.executable,str(TESTS / "test_admin_courses.py")]),
        ("Local OCR and original PDF streaming", [sys.executable,str(TESTS / "test_pdf_ocr.py")]),
        ("Automatic sync preservation and rollback", [sys.executable,str(TESTS / "test_auto_sync.py")]),
        ("Manual ZIP update and rollback", [sys.executable,str(TESTS / "test_manual_release.py")]),
        ("Live service discovery and recovery", [sys.executable,str(TESTS / "test_deployment_services.py")]),
        ("Actual production server entrypoints", [sys.executable,str(TESTS / "test_server_startup.py")]),
        ("All-course source grounding and scope", [sys.executable,str(TESTS / "test_course_grounding.py")]),
        ("Local visual PDF reading", [sys.executable,str(TESTS / "test_pdf_vision.py")]),
    ])
    results = []
    for name, command in suites:
        result = subprocess.run(command, cwd=APP, text=True, capture_output=True, timeout=120)
        if result.returncode:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            raise RuntimeError("FAILED: " + name)
        output = result.stdout.strip().splitlines()
        skipped = bool(output and output[0].startswith("SKIP "))
        print("{} {}: {}".format("SKIP" if skipped else "PASS", name,
                                 output[-1] if output else "completed"), flush=True)
        results.append({"name": name, "status": "skipped" if skipped else "passed", "output": result.stdout.strip()})
    report = {"scope": "offline fixtures and syntax only", "python": sys.version.split()[0],
              "node": node_version, "syntax": syntax, "local_links": links, "suites": results,
              "not_tested": ["real model accuracy or latency", "GPU or private textbook retrieval",
                             "real device audio", "R12 account isolation", "public deployment",
                             "learning outcomes", "GitHub-hosted workflow execution"]}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(result["status"] == "passed" for result in results)
    skipped = len(results) - passed
    print("PASS: {} offline suites; {} skipped; no live-model performance was measured.".format(passed, skipped))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, SyntaxError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
