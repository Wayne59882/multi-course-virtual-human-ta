#!/usr/bin/env bash
# Install Tesseract OCR (English + Traditional Chinese) without administrator rights.
#
#   bash /work103/wayne6/mc-vhta-source/scripts/setup/install_ocr_user.sh
#
# Everything goes into one folder you own (default /work103/wayne6/ocr). The teaching
# site finds it through <site folder>/logs/tesseract.json; no restart is needed.
# Running it again is safe and reuses whatever was already downloaded. Every file has
# several download sources, because the server's connection to GitHub can time out (504).
# Nothing outside these two locations is changed.
set -u
APP="${1:-/work103/wayne6/NTULUNG_TA_v241_12_1_QUIZ_NAV_VERIFIED}"
DEST="${OCR_HOME:-/work103/wayne6/ocr}"
REQUIRED="eng chi_tra"
OPTIONAL="osd chi_tra_vert"

say() { printf '%s\n' "$*"; }
fail() { say "[FAILED] $*"; say "請把上面全部的訊息貼給 Claude。"; exit 1; }
fetch() { curl -fL --retry 3 --retry-delay 5 --connect-timeout 20 --max-time 900 -sS -o "$2" "$1"; }
# fetch_any DEST URL...: the first source that works wins.
fetch_any() {
  local out="$1" url; shift
  for url in "$@"; do
    if fetch "$url" "$out.tmp" && [ -s "$out.tmp" ]; then mv "$out.tmp" "$out"; return 0; fi
    say "      （來源無法使用，換下一個：${url%%/*}//$(printf '%s' "$url" | cut -d/ -f3)）"
  done
  rm -f "$out.tmp"; return 1
}

[ -d "$APP/logs" ] || fail "找不到網站資料夾 $APP/logs"
mkdir -p "$DEST/bin" 2>/dev/null || DEST="$HOME/mc-vhta-ocr"
mkdir -p "$DEST/bin" || fail "無法建立 $DEST"
command -v curl >/dev/null || fail "伺服器沒有 curl"
say "[1/4] 安裝位置：$DEST"

TESS=""; TESSDATA=""
# Reuse an earlier run.
if [ -x "$DEST/env/bin/tesseract" ]; then
  TESS="$DEST/env/bin/tesseract"; TESSDATA="$DEST/env/share/tessdata"; say "[2/4] 沿用已安裝的 Tesseract"
elif [ -x "$DEST/squashfs-root/AppRun" ]; then
  TESS="$DEST/squashfs-root/AppRun"; TESSDATA="$DEST/tessdata"; say "[2/4] 沿用已下載的 Tesseract"
fi

# Route A: conda-forge Tesseract through micromamba (a single static program).
if [ -z "$TESS" ]; then
  MM="$DEST/bin/micromamba"
  if [ ! -x "$MM" ]; then
    say "[2/4] 下載 micromamba（安裝工具）"
    if fetch_any "$MM" "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64"; then
      chmod +x "$MM"
    elif fetch_any "$DEST/micromamba.tar.bz2" "https://micro.mamba.pm/api/micromamba/linux-64/latest" \
        && tar -xjf "$DEST/micromamba.tar.bz2" -C "$DEST" bin/micromamba 2>/dev/null; then
      rm -f "$DEST/micromamba.tar.bz2"; chmod +x "$MM"
    else
      say "[WARN] 無法下載 micromamba"; rm -f "$DEST/micromamba.tar.bz2"
    fi
  fi
  if [ -x "$MM" ]; then
    say "[2/4] 從 conda-forge 安裝 Tesseract（約 1–3 分鐘）"
    export MAMBA_ROOT_PREFIX="$DEST/mamba"
    if "$MM" create -y -q -p "$DEST/env" -c conda-forge --override-channels tesseract >"$DEST/install.log" 2>&1 \
        && [ -x "$DEST/env/bin/tesseract" ]; then
      TESS="$DEST/env/bin/tesseract"; TESSDATA="$DEST/env/share/tessdata"
      rm -rf "$MAMBA_ROOT_PREFIX/pkgs"  # downloaded packages are no longer needed (saves ~400 MB)
    else
      say "[WARN] conda-forge 安裝失敗（最後幾行）："; tail -n 5 "$DEST/install.log"
    fi
  fi
fi

# Route B: the Tesseract AppImage from GitHub, unpacked (no FUSE needed).
if [ -z "$TESS" ]; then
  say "[2/4] 改用 GitHub 上的 Tesseract AppImage"
  URL=$(curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 20 https://api.github.com/repos/AlexanderP/tesseract-appimage/releases/latest | python3 -c "
import json,sys
assets=json.load(sys.stdin).get('assets') or []
print(next((a['browser_download_url'] for a in assets if a['name'].endswith('x86_64.AppImage')),''))" 2>/dev/null)
  [ -n "$URL" ] || fail "兩種下載方式都失敗：伺服器可能無法連到 GitHub 下載檔或 conda-forge"
  fetch_any "$DEST/tesseract.AppImage" "$URL" || fail "下載 AppImage 失敗：$URL"
  chmod +x "$DEST/tesseract.AppImage"
  (cd "$DEST" && rm -rf squashfs-root && ./tesseract.AppImage --appimage-extract >/dev/null 2>&1) || fail "無法解開 AppImage"
  rm -f "$DEST/tesseract.AppImage"
  TESS="$DEST/squashfs-root/AppRun"; TESSDATA="$DEST/tessdata"
fi

say "[3/4] 準備語言資料：$REQUIRED $OPTIONAL"
mkdir -p "$TESSDATA" || fail "無法建立 $TESSDATA"
# Language files that came with the program are used first.
find "$DEST/squashfs-root" -name '*.traineddata' 2>/dev/null | while read -r bundled; do
  [ -s "$TESSDATA/$(basename "$bundled")" ] || cp "$bundled" "$TESSDATA/"
done
language() {  # language NAME: tessdata_fast from GitHub or jsDelivr, else the npm copy (tesseract.js-data)
  local lang="$1" file="$TESSDATA/$1.traineddata"
  [ -s "$file" ] && return 0
  fetch_any "$file" \
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$lang.traineddata" \
    "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@main/$lang.traineddata" \
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$lang.traineddata" && return 0
  local pack="$DEST/npm-$lang"
  rm -rf "$pack"; mkdir -p "$pack"
  if fetch_any "$pack/data.tgz" "https://registry.npmjs.org/@tesseract.js-data/$lang/-/$lang-1.0.0.tgz" \
      && tar -xzf "$pack/data.tgz" -C "$pack" "package/4.0.0_best_int/$lang.traineddata.gz" 2>/dev/null \
      && gunzip -c "$pack/package/4.0.0_best_int/$lang.traineddata.gz" >"$file.tmp" && [ -s "$file.tmp" ]; then
    mv "$file.tmp" "$file"; rm -rf "$pack"; return 0
  fi
  rm -rf "$pack" "$file.tmp"; return 1
}
for lang in $REQUIRED; do
  say "      $lang"
  language "$lang" || fail "下載 $lang.traineddata 失敗（所有來源都無法連線）"
done
for lang in $OPTIONAL; do
  say "      $lang"
  language "$lang" || say "[WARN] 略過 $lang（不影響使用）"
done

say "[4/4] 檢查"
LIST=$("$TESS" --tessdata-dir "$TESSDATA" --list-langs 2>/dev/null) || fail "Tesseract 無法執行：$("$TESS" --version 2>&1 | head -n 3)"
for lang in $REQUIRED; do
  printf '%s\n' "$LIST" | grep -qx "$lang" || fail "Tesseract 找不到語言 $lang：$LIST"
done
python3 - "$APP/logs/tesseract.json" "$TESS" "$TESSDATA" <<'PY'
import json, sys
with open(sys.argv[1], 'w') as f:
    json.dump({'executable': sys.argv[2], 'tessdata': sys.argv[3]}, f)
PY
REPORT=$(cd "$APP" && python3 - <<'PY' 2>&1
import pdf_ocr
engine = pdf_ocr.LocalOCR()
report = engine.report()
if not report.get('available'):
    print('NOT available: ' + str(report))
else:
    try:  # read one generated page, the same way uploaded pages are read (old and new PyMuPDF names)
        import fitz
        document = fitz.open()
        page = (getattr(document, 'new_page', None) or document.newPage)()
        (getattr(page, 'insert_text', None) or page.insertText)((40, 80), 'Southern blot detects DNA fragments', fontsize=20)
        result = engine.read(page)
        readable = result.get('words', 0) >= 3 or 'Southern' in str(result.get('text', ''))
        print('available' if readable else 'NOT available: test page unreadable ' + str(result)[:300])
    except ImportError:
        print('available')
PY
)
"$TESS" --version 2>&1 | head -n 1
say "網站的 OCR 狀態：$REPORT"
case "$REPORT" in
  available) say "[DONE] OCR 已安裝完成。之後上傳的 PDF，沒有文字的頁面會先用 OCR（每頁幾秒）。" ;;
  *) say "[NOTE] Tesseract 已裝好，但網站的測試沒有通過（網站程式若剛更新，等 5 分鐘再執行一次）；仍失敗請把上面全部的訊息貼給 Claude。" ;;
esac
