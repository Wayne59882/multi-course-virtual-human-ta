# Change Log

## R18h — 2026-11-09

- 管理員（VQ_AUTH_ADMIN_EMAIL 的帳號）可以管理所有老師的課程：課程清單列出全部課程並標示擁有者，可以開啟、刪除、重試背景準備、查看與取消任何老師的建課進度。一般老師仍只看得到自己的課程，學生仍需課程代碼。
- 管理員可以代老師上傳：建課區多一個「Course Owner」選單，選擇已核准的老師後建出的課程直接屬於那位老師（老師登入後就看得到）。已存在的課程也可以在「課程擁有者 Owner」選單轉給另一位老師。
- OCR 安裝程式（install_ocr_user.sh）：伺服器連 GitHub 逾時（504）時，語言檔改從 jsDelivr 或 npm（tesseract.js-data）下載，micromamba 也有備用來源；重跑時沿用已下載的 Tesseract。安裝完成前會實際用 OCR 讀一頁測試。
- OCR 改以命令列指定語言資料夾（--tessdata-dir）並以設定值輸出 TSV，不再依賴語言資料夾裡的 configs/tsv；另外下載的語言資料夾也能正確讀出文字位置與信心分數。

## R18g — 2026-11-09

- 不需要管理員權限也能安裝 OCR：scripts/setup/install_ocr_user.sh 把 Tesseract（英文＋繁體中文）裝在自己的資料夾（預設 /work103/wayne6/ocr），並寫入 <網站資料夾>/logs/tesseract.json；網站會自動找到它，不用重新啟動。先用 conda-forge（micromamba），失敗時改用 GitHub 上的 Tesseract AppImage。

## R18f — 2026-11-09

- 先文字後圖片：投影片只要有自己的文字（例如只有標題，10 個英數字以上）就直接開放，圖片改在背景讀；原本要 40 個字以上，標題短的投影片會在建課時就交給影像模型逐頁讀圖。完全沒有文字、OCR 也讀不到的頁面仍在建課時讀圖。

## R18e — 2026-11-09

- 修正：自 11/3 起所有課程的中文講稿都失敗（script=error，講稿進度一直是 0）。原因是講稿核對要求本機模型為每段提供教材原文引句，並以「逐字完全相同」比對；模型抄寫時常改變大小寫、引號、破折號、連字（ﬁ）、換行斷字或用「…」省略一段，因此每一份講稿都被判定為引句無法核對。現在這些不改變意思的差異可以通過；8 個字以上的引句允許每 10 個字最多 1 個字不同，但數字與否定詞（not、no、不、無…）必須與教材完全一致，用「…」省略的部分不得超過 30 個字，也不得略過數字或否定詞。段落是否有教材支持仍由模型逐段審核，規則不變。
- 狀態報告（scripts/diagnostics/course_status.py）加上：模型在顯示卡或 CPU 上執行的比例、顯示卡上的其他程式、伺服器是否已安裝 OCR（Tesseract）。

## R18d — 2026-11-09

- 修正：「Retry Background Preparation」觸發的重新讀取原始 PDF（Reading saved PDF: page …）若被伺服器重新啟動（例如自動更新）中斷，畫面會永遠停在最後一頁、Retry 按鈕也無法再按，講稿與題目也被擋住。現在伺服器重新啟動後會自動接續重讀，完成後繼續準備講稿與題目。
- 重讀進度改為顯示「第幾頁（已完成 / 總頁數）」，並區分 OCR 與本機讀圖。
- 重讀時用「先文字後圖片」讀到的頁面，也會排進背景讀圖並核對，不會遺漏圖片內容。
- 新增 scripts/diagnostics/course_status.py：在伺服器上執行，列出每門課程的準備狀態、工作、模型與顯示卡使用情形與最近錯誤（唯讀，不顯示密碼、金鑰或 Email）。

## R18c — 2026-11-06

- 上傳 PDF 預設改為「先文字後圖片」：有文字層的頁面直接使用文字、沒有文字層的頁面先用本機 OCR，全部讀完文字課程就開放 Q&A，接著寫講稿與出題；本機影像模型（Gemma 4 12B，抄寫＋原頁獨立核對）改在背景讀圖，每讀完 10 頁就把結果加入課程 Q&A。原本每張有圖的投影片都要先讀圖兩次（每張約 1–2 分鐘）才開放，1000 多張的投影片要一天以上。連 OCR 都讀不到文字的頁面仍在建課時直接讀圖。
- 建課畫面新增「Read every slide image before the course opens」選項，勾選即為 R15 的完整讀圖流程，論文結果可重現。更新前已在進行中的建課，伺服器重新啟動後會改用先文字後圖片繼續，已讀過的頁面不會重讀。
- 本機影像模型讀完（並核對過）某一頁後，該頁先前暫用的 OCR 文字會從課程 Q&A（含語意索引）移除，改用核對過的讀圖結果；讀圖失敗的頁面保留 OCR 文字。最終內容與 R15 完整讀圖一致，且不增加任何模型呼叫。
- 新增測試：先文字後圖片、OCR 頁面延後讀圖、無文字頁面仍即時讀圖、完整模式不沿用先文字的快取、圖片段落分批加入 Q&A。

## R18b — 2026-11-06

- 登入頁的密碼欄加上「顯示／隱藏」按鈕（登入與申請帳號都有），可以先確認自己打的密碼再登入；送出時自動改回隱藏，瀏覽器仍可記住密碼。密碼欄關閉自動大寫與拼字檢查，避免手機鍵盤把第一個字母改成大寫。修改密碼視窗也可勾選「顯示密碼」。
- 雲端模型（ChatGPT／Claude／Gemini）與自然對話的 API Key 欄也加上「顯示／隱藏」按鈕，可確認貼上的 Key 是否正確。

## R18 — 2026-11-05

- 網址改為論文名稱：**https://multicourse-ta.twchu.workers.dev**。自動更新會建立這個新網址（一個小 Worker 透過 service binding 把每個請求交給原本的教學網站 Worker，原本的 tunnel、設定與登入都不變）；確認新網址能打開網站後，舊網址 lungta.twchu.workers.dev 才開始自動轉到新網址。若新網址暫時無法確認，舊網址照常服務並每 30 分鐘重試。帳號通知信的網站連結也跟著改成新網址。換網址後需在新網址重新登入一次。
- 教學單元偵測：PDF 書籤不照頁碼排列時（例如 PowerPoint 匯出的投影片），改依頁碼排序後再算頁數範圍，不再出現「pp. 3–1138」這類錯誤範圍；「未命名的章節」等預設名稱改用該段第一張投影片的標題；重複名稱加上 (2)；投影片檔會用置中的大標題分段頁（section title slide）命名書籤前的開頭投影片、把 30 頁以上的長單元在分段頁切開，沒有書籤時也用分段頁建立單元。一般教科書（直式頁面、正常書籤）結果不變。
- 建課前可直接修改每個單元的名稱與起訖頁碼（有框線的輸入欄），頁碼超出 PDF 範圍或起頁大於迄頁時不會開始建課。PDF 標題「Microsoft PowerPoint - 檔名.pptm」只保留檔名。
- 雲端模型（自備 API Key）：除 ChatGPT（OpenAI）外新增 Claude（Anthropic）與 Gemini（Google），每家提供多個版本並依費用由低到高排列：GPT-6 Luna／GPT-6.1 Terra／GPT-6.1 Sol（另保留論文評估用的 GPT-5.6 Luna）、Claude Haiku 5.5／Sonnet 5.5／Opus 5.5、Gemini 3.5 Flash-Lite／3.8 Flash／4 Argon。每家公司一把 Key（只存在此瀏覽器分頁），每個模型先驗證才使用；同一家的其他模型會用已存的 Key 自動驗證。所有雲端模型都用同一套教材檢索、範圍判斷與答案核對；思考程度設為低以節省 token。
- 新增測試：單元偵測與頁碼修正、三家雲端模型的請求格式／驗證／白名單、網址搬移與舊網址轉址。

## R17f — 2026-11-04

- 自然對話只保留一種回答方式：伺服器先在選定課程（內建教材或上傳的 PDF）搜尋，Gemini 只依回傳的教材片段回答，超出範圍由伺服器拒答。移除「Checked reply」選項及其程式（由 Gemma／Ministral／Luna 寫答案再由 Gemini 念出）；左邊原本的 Q&A 與模型完全不變。
- 說明文字只留「Press Start and talk any time. Answers use only the course material.」

## R17e — 2026-11-04

- 自然對話的選項改名，避免誤以為答案來自 Gemini 自己的知識：兩種方式都只用課程教材（內建教材或上傳的 PDF）。「Fast reply · Gemini」由 Gemini 依教材片段直接回答；「Checked reply · <選定模型> · slower」由 Gemma／Ministral／Luna 依教材寫答案並核對，Gemini 只負責念。標題改為「NATURAL CONVERSATION」，說明文字註明答案只用課程教材。

## R17d — 2026-11-04

- 修正：自然對話一開始沒人說話卻用西班牙文開頭。背景雜音被當成說話，Gemini 再從雜音猜語言。現在預設語言為台灣華語（繁體用語），開始時先用中文簡短問候；學生清楚說出完整的其他語言句子才切換；雜音不會再決定語言，也較不容易被誤判為說話。
- 自然對話說明文字精簡為一行：「Press Start and talk any time — you can interrupt. Voice is processed by Google Gemini.」；狀態列簡化為「Listening — talk any time.」。

## R17c — 2026-11-04

- 修正：自然對話（Gemini Live）把內建課程名稱「Respiratory Physiology」當成範圍，拒答腎臟、腸胃等其他生理章節。內建教材本來就涵蓋整套生理學教科書；現在範圍只由伺服器的教材搜尋決定，伺服器找到教材片段時 Gemini 必須依片段回答，不再自行依課程名稱判斷。
- 內建預設課程名稱改為「Built-in Physiology (default)」／「內建生理學課程」。只改畫面文字；Q&A 模型提示、講稿、題目產生方式與 V/Q 模型不變。

## R17 — 2026-11-04

- 新增選用的「自然對話（Gemini Live）」模式，與原本的 Q&A 並存。學生直接說話、可隨時插話；每個課程問題先由本系統伺服器搜尋目前選定的課程（內建教材或老師上傳的課程），超出範圍由伺服器判定並拒答，Gemini 只能依回傳的教材片段回答，並列出使用的來源。
- 使用者自行輸入 Gemini API Key（只存在此瀏覽器分頁）；學生語音與相關教材片段會送到 Google，回答為即時語音，未像原本 Q&A 逐段核對。原本的 Q&A、核對流程與模型完全不變。
- 聲音跟隨老師設定：Yating 女聲（Gemini Kore）、Zhiwei 男聲（Gemini Charon）；對話中切換會重新連線並延續對話。3 分鐘無人說話自動結束。
- 畫面改為左右並排：左邊原本的 Q&A（打字／錄音），右邊自然對話。自然對話可選回答來源：「Gemini（最快）」或「目前選定的 Q&A 模型」——Gemini 只負責聽與說，答案由 Gemma／Ministral（本機）或 GPT-5.6 Luna 透過原本的伺服器流程產生並經教材核對；等待時先說「我查一下教材」，新問題會取消仍在準備的舊答案。

## R16 — 2026-11-03

- 修正：本機模型回答時出現「Invalid job response」。回答進度查詢誤把內部的取消控制物件一起回傳，伺服器無法轉成資料而中斷連線；新增實際伺服器 HTTP 測試。
- 學生問答優先：背景準備課程（講稿、題目、PDF 圖片）遇到學生提問時立即讓出模型，答完再接續；所有本機模型呼叫使用同一個 context 大小，避免模型反覆重新載入。
- 切換模型：已送出的題目改由新選的模型重新回答，舊模型的工作在伺服器端取消，不會再出現第二個答案；Confirm API Key 先驗證金鑰，失敗時維持原本的本機模型並顯示原因。
- Stop Recording & Answer：顯示轉錄進度，伺服器轉錄最多等 10 秒即改用即時字幕自動送出；連按 Submit 只建立一個回答工作。
- 課程處理中可隨時 Delete Course；建課中可 Cancel Build，背景工作會停止且不會重建已刪除的檔案。
- 教材處理方法維持 R15：講稿、題目與圖片辨識的產生方式不變。另提供選用設定 `VQ_PDF_VISION_FIGURES=background`（文字充足頁面的圖片改在講稿後背景辨識），預設關閉。
- 已變更的網頁程式更新快取版本，手機與瀏覽器會載入新版；新增學生優先／刪除／取消測試與真實瀏覽器 Q&A 流程測試。

## R15 — 2026-11-03

- 修正 KB 啟動缺少 SharedModelLock；共用模型鎖與背景預熱，新增不依賴模型暖機的版本 ready 檢查。
- PDF 圖片／損壞文字／重要圖像改先使用已安裝的本機視覺模型，再以原始像素核對；保持逐頁快取及 OCR 備援，未讀內容明確列示。
- 講稿、本機／雲端問答逐段核對教材引文，情境題增加引文檢查；封住傳入 context 及舊更正快取繞過課程範圍的路徑。
- 舊生成內容備份後重新核對，保留上傳原檔、帳號與作答紀錄；新增實際三服務啟動與跨學科來源測試。

## R14 — 2026-11-02

- Isolate unreadable PDF pages/units and retain durable page checkpoints for large-material retries.
- Accept scanned PDFs and add page-local Tesseract fallback with confidence gates, bounded resources, progress and cached OCR retries from retained originals.
- Retain private upload receipts and expose original-file diagnostics only to the owning teacher and administrator.
- Preserve cross-subject text/symbols and mathematical notation; reuse avatar resources during speech playback.
- Add a CI-gated private-server pull updater with backups, private-data preservation and rollback.
- Add controlled 1,001-page PDF, content, playback, upload-access and updater regressions.

## Operator layout and English README — 2026-11-01

- Separate setup, runtime, deployment, and corpus-ingestion tools under `scripts/`.
- Resolve launcher paths independently of the caller's working directory while retaining application data under `app/`.
- Replace the deployment tool's archived 3D-check dependency with a current local web-resource check.
- Let the speech setup helper create the expected Python environment instead of referring to an obsolete preparation script.
- Add isolated operator-script routing checks and document the verification limits.
- Rewrite the main and application READMEs in English with consistent feature, installation, usage, configuration, architecture, troubleshooting, and project-structure sections.

## 專案結構重整 — 2026-11-01

- 正式程式集中至 `app/`，測試、研究資料、版本說明與歷史工具分開分類。
- 共用 26 組 JavaScript、9 組 CSS 及圖片／音訊資產，保留原有腳本順序。
- 加入前端搬移契約、HTTP 資源與匿名頁面轉址檢查。
- 重寫完整 README，更新安裝、操作、架構、評測與遷移路徑。


## 文件整理草案 — 2026-11-01

- 建立專案首頁、主程式 README 與統一文件導覽。
- 補充安裝前提、系統架構、程式位置、評測定義及展示流程。
- 整理真實版本識別、已知工程項目與可公開重跑的驗證範圍。
- 新增離線檢查入口、GitHub Actions 設定、issue／PR 範本與引用資訊。
- 更新過時的教材檢索目錄說明，修正三模型來源文件中的模型數量文字。

此項為對基準 `8d33615` 的文件與工具整理；尚未成為公開發布版本。

## 既有更新文件索引

下表日期與內容依儲存庫原有文件整理，保留作為歷史索引，不代表已存在同名 Git tag、release 或分次 commit。

| 文件標示 | 內容 | 原始說明 |
|---|---|---|
| R12 · 2026-10-30 | 帳號登入、教師／學生及課程權限 | [R12](docs/releases/deployment/README_ACCOUNTS_R12_20261030.md) |
| R11 · 2026-10-28 | 問答備援、雲端輸出長度及課程名稱處理 | [R11](docs/releases/deployment/README_QA_FIX_R11_20261028.md) |
| R9 · 2026-10-21 | 語音問題與停止／送出流程 | [R9](docs/releases/deployment/README_VOICE_FIX_R9_20261021.md) |
| R8 · 2026-10-21 | 預設 V/Q 課程相關調整 | [R8](docs/releases/deployment/README_DEFAULT_VQ_ONLY_R8_20261021.md) |
| R7 · 2026-10-21 | 手動錄音流程 | [R7](docs/releases/deployment/README_MANUAL_RECORDING_R7_20261021.md) |
