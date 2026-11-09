#!/usr/bin/env bash
# Install Tesseract OCR (English + Traditional Chinese) without administrator rights.
#
#   bash /work103/wayne6/mc-vhta-source/scripts/setup/install_ocr_user.sh
#
# Everything goes into one folder you own (default /work103/wayne6/ocr). The teaching
# site finds it through <site folder>/logs/tesseract.json; no restart is needed.
# Running it again is safe. Nothing outside these two locations is changed.
set -u
APP="${1:-/work103/wayne6/NTULUNG_TA_v241_12_1_QUIZ_NAV_VERIFIED}"
DEST="${OCR_HOME:-/work103/wayne6/ocr}"
LANGS="eng chi_tra osd"
TESSDATA_URL="https://github.com/tesseract-ocr/tessdata_fast/raw/main"

say() { printf '%s\n' "$*"; }
fail() { say "[FAILED] $*"; say "請把上面全部的訊息貼給 Claude。"; exit 1; }
fetch() { curl -fL --retry 2 --connect-timeout 20 -sS -o "$2" "$1"; }

[ -d "$APP/logs" ] || fail "找不到網站資料夾 $APP/logs"
mkdir -p "$DEST/bin" 2>/dev/null || DEST="$HOME/mc-vhta-ocr"
mkdir -p "$DEST/bin" || fail "無法建立 $DEST"
command -v curl >/dev/null || fail "伺服器沒有 curl"
say "[1/4] 安裝位置：$DEST"

TESS=""; TESSDATA=""
# Route A: conda-forge Tesseract through micromamba (a single static program).
MM="$DEST/bin/micromamba"
if [ ! -x "$MM" ]; then
  say "[2/4] 下載 micromamba（安裝工具）"
  if fetch "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64" "$MM.tmp"; then
    chmod +x "$MM.tmp" && mv "$MM.tmp" "$MM"
  else
    say "[WARN] 無法下載 micromamba"; rm -f "$MM.tmp"
  fi
fi
if [ -x "$MM" ]; then
  say "[2/4] 從 conda-forge 安裝 Tesseract（約 1–3 分鐘）"
  export MAMBA_ROOT_PREFIX="$DEST/mamba"
  if [ -x "$DEST/env/bin/tesseract" ] || "$MM" create -y -q -p "$DEST/env" -c conda-forge --override-channels tesseract >"$DEST/install.log" 2>&1; then
    TESS="$DEST/env/bin/tesseract"; TESSDATA="$DEST/env/share/tessdata"
    rm -rf "$MAMBA_ROOT_PREFIX/pkgs"  # downloaded packages are no longer needed (saves ~400 MB)
  else
    say "[WARN] conda-forge 安裝失敗（最後幾行）："; tail -n 5 "$DEST/install.log"
  fi
fi
# Route B: the Tesseract AppImage from GitHub, unpacked (no FUSE needed).
if [ -z "$TESS" ]; then
  say "[2/4] 改用 GitHub 上的 Tesseract AppImage"
  URL=$(curl -fsSL --connect-timeout 20 https://api.github.com/repos/AlexanderP/tesseract-appimage/releases/latest | python3 -c "
import json,sys
assets=json.load(sys.stdin).get('assets') or []
print(next((a['browser_download_url'] for a in assets if a['name'].endswith('x86_64.AppImage')),''))" 2>/dev/null)
  [ -n "$URL" ] || fail "兩種下載方式都失敗：伺服器可能無法連到 GitHub 下載檔或 conda-forge"
  fetch "$URL" "$DEST/tesseract.AppImage" || fail "下載 AppImage 失敗：$URL"
  chmod +x "$DEST/tesseract.AppImage"
  (cd "$DEST" && rm -rf squashfs-root && ./tesseract.AppImage --appimage-extract >/dev/null 2>&1) || fail "無法解開 AppImage"
  TESS="$DEST/squashfs-root/AppRun"; TESSDATA="$DEST/tessdata"
fi

say "[3/4] 準備語言資料：$LANGS"
mkdir -p "$TESSDATA" || fail "無法建立 $TESSDATA"
for lang in $LANGS; do
  if [ ! -s "$TESSDATA/$lang.traineddata" ]; then
    fetch "$TESSDATA_URL/$lang.traineddata" "$TESSDATA/$lang.traineddata.tmp" && mv "$TESSDATA/$lang.traineddata.tmp" "$TESSDATA/$lang.traineddata" \
      || fail "下載 $lang.traineddata 失敗"
  fi
done

say "[4/4] 檢查"
LIST=$(TESSDATA_PREFIX="$TESSDATA" "$TESS" --list-langs 2>&1) || fail "Tesseract 無法執行：$LIST"
for lang in eng chi_tra; do
  printf '%s\n' "$LIST" | grep -qx "$lang" || fail "Tesseract 找不到語言 $lang：$LIST"
done
python3 - "$APP/logs/tesseract.json" "$TESS" "$TESSDATA" <<'PY'
import json, sys
with open(sys.argv[1], 'w') as f:
    json.dump({'executable': sys.argv[2], 'tessdata': sys.argv[3]}, f)
PY
REPORT=$(cd "$APP" && python3 -c "import pdf_ocr; r=pdf_ocr.LocalOCR().report(); print('available' if r.get('available') else 'NOT available: ' + str(r))" 2>&1)
"$TESS" --version 2>&1 | head -n 1
say "網站的 OCR 狀態：$REPORT"
case "$REPORT" in
  available) say "[DONE] OCR 已安裝完成。之後上傳的 PDF，沒有文字的頁面會先用 OCR（每頁幾秒）。" ;;
  *) say "[NOTE] Tesseract 已裝好，但網站還沒有偵測到；若網站程式還沒更新到最新版，等 5 分鐘後再執行一次這個指令。" ;;
esac
