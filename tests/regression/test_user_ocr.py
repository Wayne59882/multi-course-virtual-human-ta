"""Tesseract installed without administrator rights is found through logs/tesseract.json,
and its language folder is passed to every OCR run. A stand-in program plays Tesseract."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get('VHTA_TEST_APP') or ROOT / 'app')
sys.path.insert(0, str(APP))
import pdf_ocr  # noqa: E402

with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    seen = folder / 'tessdata_seen.txt'
    fake = folder / 'tesseract'
    args = folder / 'args_seen.txt'
    fake.write_text('#!/bin/sh\nprintf "%s" "$*" > ' + str(args) + '\nprintf "%s" "$TESSDATA_PREFIX" > ' + str(seen) +
                    '\nprintf "List of available languages in \\"x\\" (3):\\neng\\nchi_tra\\nosd\\n"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    record = folder / 'tesseract.json'
    record.write_text(json.dumps({'executable': str(fake), 'tessdata': str(folder / 'tessdata')}))
    with patch.object(pdf_ocr, 'USER_OCR', record), patch.object(pdf_ocr.shutil, 'which', lambda name: None), \
            patch.dict(os.environ, {'VQ_TESSERACT': ''}):
        engine = pdf_ocr.LocalOCR()
        assert engine.report()['available'], engine.report()
        assert engine.executable == str(fake) and seen.read_text() == str(folder / 'tessdata')
        # Also on the command line, because a packaged Tesseract may set its own TESSDATA_PREFIX.
        assert '--tessdata-dir ' + str(folder / 'tessdata') in args.read_text(), args.read_text()
        # Nothing recorded and nothing on the PATH: reported as missing, as before.
        record.unlink()
        assert not pdf_ocr.LocalOCR().report()['available']
    # The installer script is valid shell and records the same file the site reads.
    script = ROOT / 'scripts/setup/install_ocr_user.sh'
    assert subprocess.run(['bash', '-n', str(script)]).returncode == 0
    text = script.read_text(encoding='utf-8')
    assert '$APP/logs/tesseract.json' in text and 'sudo' not in text.replace('without administrator', '')
    # GitHub can time out from the server: each language file has other sources, and a rerun
    # reuses an unpacked Tesseract instead of downloading it again.
    for source in ('raw.githubusercontent.com', 'cdn.jsdelivr.net', 'registry.npmjs.org/@tesseract.js-data', 'micro.mamba.pm'):
        assert source in text, source
    assert 'squashfs-root/AppRun" ]; then' in text and '--tessdata-dir "$TESSDATA" --list-langs' in text
    # TSV output does not depend on a configs/ folder next to separately downloaded languages.
    assert "'tessedit_create_tsv=1'" in (APP / 'pdf_ocr.py').read_text(encoding='utf-8')
print('[OK] user-installed OCR: found through logs/tesseract.json with its language folder; installer needs no administrator rights and has fallback download sources')
