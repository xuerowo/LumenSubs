# LumenSubs · AI 字幕翻譯工作室

把影片或音訊自動轉錄、翻譯成指定語言，編輯後輸出字幕檔或嵌入字幕的影片。

- **影片翻譯**：影片 → 轉錄 → 翻譯 → 雙語字幕 → 燒錄硬字幕（MP4）或內封軟字幕（MKV）
- **音訊製片**：音訊 + 背景圖 → 帶字幕（及音訊律動動畫）的影片
- **只轉錄**：關閉「自動翻譯」即可完全在本機產生原文字幕，不需要 API Key，逐字稿不會離開這台電腦
- 字幕檔匯出：SRT / VTT / ASS（保留樣式）/ TXT，雙語、僅譯文或僅原文
- **匯入既有字幕**（SRT / VTT / ASS / SSA，自動辨識編碼與雙語排列，也可手動指定編碼），可直接校對、重新翻譯或燒錄
- 可自選輸出資料夾（系統原生對話框，會記住上次位置）與檔案名稱；同名檔案會先詢問是否覆蓋
- 原文／譯文對照編輯、時間碼微調、時間軸拖曳、分割／合併、單句重新翻譯、整體偏移
- 字幕樣式（字型、字級、字重、顏色、描邊、陰影、圓角底框）與位置可調，可直接在畫面上拖曳
- 自動偵測原文語言（30 種；其中 11 種有字級精準對齊，見下方「轉錄」），可翻譯為 33 種語言

## 系統需求

| 項目 | 最低 | 建議 |
|---|---|---|
| 作業系統 | Windows 10／11 64 位元 | |
| 顯示卡 | 無（改用 CPU，轉錄非常慢） | NVIDIA，**顯示卡記憶體 8 GB 以上**（語音模型約佔 6.5 GB） |
| 記憶體 | 8 GB（使用顯示卡時） | 16 GB 以上（**只用 CPU 時至少 16 GB**，模型約佔 13 GB） |
| 硬碟空間 | 約 12 GB（見下表） | 另加影片專案所需空間（每支影片約原檔大小的 1–2 倍） |
| Python | 3.10–3.13（建議 3.12；3.14 以上尚不支援） | |

AMD／Intel 顯示卡目前只能以 CPU 執行。第一次啟動時，程式會檢查顯示卡記憶體與系統記憶體，不足時會提醒。

| 存放位置 | 內容 | 大小 |
|---|---|---|
| 專案資料夾 `.venv` | Python 套件（含 PyTorch） | 約 4–5 GB |
| `C:\Users\<你>\.cache\huggingface` | 語音模型 | 約 6.5 GB |
| pip 下載快取（C 槽使用者資料夾） | 安裝時暫存 | 約 2–3 GB（可刪除） |
| 專案資料夾 `workspace` | 你的專案、設定與紀錄檔 | 依使用量 |

想把模型放到其他磁碟，可在執行 `start.bat` 前設定環境變數 `HF_HOME`（例如 `D:\hf`）。

## 安裝與啟動（Windows）

**事前準備**：安裝 [Python 3.12](https://www.python.org/downloads/release/python-31210/)（下載「Windows installer (64-bit)」，安裝時勾選「Add python.exe to PATH」與「tcl/tk and IDLE」）。
也可以在命令提示字元執行 `winget install -e --id Python.Python.3.12`。

下載或 `git clone` 本專案（建議放在路徑較短、不在 OneDrive 內的資料夾，例如 `D:\LumenSubs`），**雙擊 `start.bat`** 即可。第一次執行會自動：

1. 在專案內建立獨立的虛擬環境 `.venv`（不影響系統的 Python）
2. 偵測顯示卡驅動，安裝對應的 **CUDA 版 PyTorch**，並實際在顯示卡上試算確認可用（無 NVIDIA 顯示卡或顯示卡太舊則安裝 CPU 版）
3. 安裝其餘套件（優先使用 `requirements.lock` 中驗證過的版本）
4. 檢查 FFmpeg（含 ffprobe），沒有的話詢問是否用 `winget` 自動安裝
5. 下載語音模型 Qwen3-ASR-1.7B、Qwen3-ForcedAligner-0.6B、Silero VAD（約 6.5 GB；下載中斷後重新執行會**接續下載**並檢查檔案完整）

首次安裝視網速約需 10–30 分鐘；之後再執行 `start.bat` 只做快速檢查，數秒內啟動。
程式會啟動本機伺服器（預設 `http://127.0.0.1:8765`，被占用時自動改用下一個連接埠）並以 Edge／Chrome 應用程式視窗開啟。
**同一時間只會執行一個 LumenSubs**：再次執行 `start.bat` 只會叫出已開啟的視窗，避免兩個程式同時修改同一份資料。

**翻譯服務的 API Key**：啟動後到右上角「設定 → 翻譯」填入（或在專案根目錄放一個內容為 key 的 `apikey.txt`）。預設使用 DeepSeek，也可以改成其他 OpenAI 相容的服務（修改 Base URL 與模型名稱）。

| 參數 | 用途 |
|---|---|
| `start.bat --reinstall` | 重新偵測顯示卡並重裝 PyTorch、更新其餘套件（例如更新顯示卡驅動後） |
| `start.bat --cpu` | 改用 CPU 版 PyTorch（會記住，之後以 `--reinstall` 改回 GPU） |
| `start.bat --no-browser` | 只啟動伺服器，不開視窗（主控台會印出含存取金鑰的網址） |

**手動安裝**（進階）：先依 [pytorch.org](https://pytorch.org) 安裝 CUDA 版 PyTorch（例如 `pip install torch --index-url https://download.pytorch.org/whl/cu128`），
**再** `pip install -r requirements.txt -c requirements.lock`（順序不能反，否則會先裝上 CPU 版 PyTorch），並確認 `ffmpeg`／`ffprobe` 在 PATH 上（或放在專案的 `ffmpeg\bin`），最後執行 `python run.py`。

### 安裝遇到問題

安裝過程的完整紀錄在 `workspace\logs\setup.log`，失敗時畫面也會說明可能原因。常見情況：

- **網路中斷**：直接重新執行 `start.bat`，已下載的部分不必重來。
- **公司網路／代理伺服器**：設定環境變數 `HTTPS_PROXY`（例如 `http://proxy.company.com:8080`）；若公司攔截 HTTPS 導致憑證錯誤，請洽 IT。
- **無法連上 huggingface.co**：可設定環境變數 `HF_ENDPOINT` 使用**你信任的**鏡像站（鏡像站可以提供任何內容，請謹慎選擇）。
- **沒有 winget**（部分精簡版 Windows）：從 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下載 `ffmpeg-release-essentials.zip`，把裡面的 `bin` 資料夾複製到專案的 `ffmpeg\bin`。
- **磁碟空間不足**：見上方「系統需求」的空間表。
- **模型損毀**：刪除 `C:\Users\<你>\.cache\huggingface\hub\models--Qwen--*` 後重新執行 `start.bat`。

## 更新與解除安裝

- **更新**：下載新版後，把檔案**覆蓋到原本的資料夾**（或在原資料夾執行 `git pull`），再執行 `start.bat`。`workspace`（專案、設定、API Key）與 `.venv` 會保留；若套件有變動會自動更新。
  若解壓到新資料夾，請把舊資料夾的 `workspace` 整個搬過去（也可以用環境變數 `LUMEN_WORKSPACE` 指定資料存放位置）。
- **目前版本**：「設定 → 關於」。回報問題時請按「複製診斷資訊」，並附上 `workspace\logs` 內的紀錄檔（不含 API Key 與字幕內容）。
- **解除安裝**：刪除專案資料夾（含 `workspace` 的專案與設定）；刪除 `C:\Users\<你>\.cache\huggingface\hub` 內的 `models--Qwen--Qwen3-ASR-1.7B`、`models--Qwen--Qwen3-ForcedAligner-0.6B`、`models--onnx-community--silero-vad`；
  若 FFmpeg 是由本程式透過 winget 安裝，可執行 `winget uninstall Gyan.FFmpeg`。pip 下載快取可用 `pip cache purge` 清除。

## 隱私與安全

- **轉錄在本機完成**，音訊不會上傳。
- **翻譯會把內容送到翻譯服務**（預設 DeepSeek，或你在設定中指定的 API 位址）：完整逐字稿（每個翻譯批次都附上全文作為上下文）、術語表、AI 產生的內容摘要，以及單句重新翻譯時前後幾句目前的譯文（包含你編輯過的）。第一次翻譯前程式會說明並請你確認。
  DeepSeek 為中國公司，依其隱私政策資料存放於中國境內的伺服器；內容敏感（公司會議、客戶訪談、未公開影片）時，請先確認是否允許傳送到境外服務，或關閉「自動翻譯」只在本機轉錄。
- 請確認你有權處理所上傳影片或音訊的內容；翻譯服務的使用需遵守其服務條款。
- 字型由本機伺服器提供：第一次用到時由本程式向 Google Fonts 下載並快取到 `workspace/fonts/`（Google 會看到你的 IP 位址與所用字型；安裝時會先下載介面與預設字幕字型，約 22 MB），之後可離線使用，瀏覽器本身不會連到外部網站。無法連線時，尚未下載的字型改用系統字型。
- 本機伺服器只接受本機連線，並要求每次啟動時產生的**存取金鑰**（啟動時自動帶入視窗，媒體檔的網址也需要），其他網頁無法操作它；頁面設有內容安全政策（CSP），只執行本程式自己的腳本。只有本程式輸出的檔案可以從介面開啟，字幕只能寫成字幕檔副檔名；播放清單等會參照其他檔案的「偽裝影片」會被拒絕讀取。
- API Key 以明文存在 `workspace/settings.json`（或 `apikey.txt`），兩者都已列入 `.gitignore`；分享專案資料夾前請先移除。更換 API 位址時需一併重新輸入金鑰。
  **共用電腦**：若這台電腦還有其他 Windows 帳號，他們可能讀得到專案資料夾（取決於資料夾權限）。請把 LumenSubs 放在只有你能存取的位置（例如 `C:\Users\<你>\` 底下），或用 `LUMEN_WORKSPACE` 把資料放到那裡。
- 翻譯用量（tokens）會記錄在「設定 → 翻譯」下方，方便掌握費用；「重新翻譯全部」與「重新生成」前都會提醒會再次計費。

## 品質設計

### 轉錄（Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B，本機 GPU）

1. **Silero VAD** 找出停頓，將時間軸切成 ≤20 秒的「語句窗」，再組成約 100 秒的長段。
2. **雙軌辨識**：長段辨識（上下文完整、用字最準）＋ 語句窗辨識（局部精確）。以字元序列比對把長段文字分配回各語句窗，逐窗擇優；若長段漏字，改用語句窗結果；長段多出、語句窗沒聽到的內容（例如音樂處「腦補」的句子）會被剪掉。
3. **防迴圈**：解碼器陷入重複（如長時間的喘息、音樂）時提前停止，殘留的重複字會壓縮成「前兩次＋…」，並以較短分段重新解碼該段，避免漏句與長時間卡住。
4. **語言漂移修正**：以主要語言與文字字元系統判斷，重新解碼被誤判語言的片段（明顯是另一種文字的段落，例如日語影片中的英文，會保留原語言）。
5. **窗內強制對齊**：在短窗內做字級對齊（穩定、不會長段崩潰），再用 VAD 修正吞掉靜音或零長度的異常時間；對齊失敗的片段自動改用估算時間，不會讓整份轉錄失敗。
   字級對齊支援 11 種語言：中、英、粵、日、韓、法、德、義、葡、俄、西。**其他語言的字幕時間是依語音段落估算的**，程式會提醒，建議播放檢查。
6. **智慧斷句**：依句末標點（含阿拉伯文、印地文等）、停頓、長度、雙語可讀性與語言黏著規則（不在助詞前斷、不以冠詞結尾）切分；過短的語氣詞併入相鄰字幕（「はい」「いいえ」等短回答一定保留）；加入適度停留與最短顯示時間，且不重疊。

18 分鐘日語音訊在 RTX 5060 Ti 上轉錄約 55 秒。

### 翻譯（DeepSeek `deepseek-flash`，或其他 OpenAI 相容服務）

1. 先分析全文，產生劇情摘要、說話者關係與語氣、術語表，讓平行批次保持一致。
2. 每個批次都附上完整逐字稿作為上下文（前綴快取，成本很低；逐字稿超過約 6 萬 tokens 時改附前後各數十句），批次在句子結束處切開；以 ID 對應嚴格 JSON 輸出並驗證，缺漏或未翻譯（照抄原文）自動重試。
   被服務拒絕的單一批次只會讓那幾句失敗，其他照常完成；金鑰錯誤、餘額不足等無法重試的錯誤會立即停止。
3. 翻譯時同步**校正辨識錯字**（同音字、聽錯的詞）：只接受小幅、像聽錯的修正（不改數字、不加否定、英文等只接受拼法相近的字），被修正的句子標示「**AI 校正**」，點擊即可還原。
4. 目標語言字幕慣例：台灣正體用語（模型含簡體字時以 OpenCC 轉換；已是正體的大陸用語無法自動改寫）、中文句末不加句號、跨句自然分配到各字幕。

### 所見即所得

預覽與匯出使用**同一個 Canvas 字幕渲染器**（自動換行、平衡斷行、日文禁則、RTL、描邊、陰影、圓角底框；過長的網址或單字會強制斷行，不會超出畫面）。
燒錄硬字幕時，瀏覽器以輸出解析度逐句繪製字幕畫格，FFmpeg 依時間碼疊加（逐格精準）後以 NVENC 編碼（寬或高超過 4096 的影片，或 NVENC 無法使用時，自動改用 x264）——成品與預覽一致。
**HDR 影片**（例如 iPhone 拍攝）的預覽與硬字幕成品會轉為一般色彩（SDR），避免字幕在 HDR 螢幕上過亮；需要保留 HDR 請改用內封軟字幕（MKV，影像不重新編碼）。
ASS 字幕檔與 MKV 軟字幕則是**近似樣式**：換行位置、字級、顏色、描邊與位置沿用預覽，但圓角底框、柔和陰影與雙語間距無法以 ASS 完整表達。
MKV 會內嵌所用字型（一般與粗體，中日韓字型每個約 7 MB），並保留原片所有音軌與可相容的字幕軌；單獨匯出的 ASS 檔則需播放端安裝相同字型。
音訊律動動畫的頻帶資料由後端預先計算，預覽與匯出共用同一份。

## 資料安全

- 專案檔與設定檔採原子寫入（寫完整份才替換）並保留一份自動備份（`*.bak`）；檔案損毀時自動改用備份，清單中也會標示無法讀取的專案，不會默默消失。
- 設定檔讀取失敗時不會被覆寫（避免 API Key 與術語表被清空）。
- 編輯內容停止輸入約 0.7 秒即自動儲存，失敗會重試並在瀏覽器留一份本機備份；兩個視窗同時編輯同一專案時，後儲存的一方會被詢問，而不是悄悄覆蓋。
- 關閉視窗後，進行中的生成、翻譯、媒體處理與影片輸出會在背景繼續，重新開啟專案時自動接回；關閉主控台視窗會一併結束所有 FFmpeg 程序。
- 超過 14 天未開啟的專案會自動清掉可重新產生的音訊暫存（約每小時音訊 230 MB）。

## 專案結構

```
start.bat           一鍵安裝／啟動（Windows）
bootstrap.py        環境檢查：.venv、PyTorch（自動選 CUDA 版本）、套件、FFmpeg、模型；紀錄寫到 workspace/logs/setup.log
run.py              啟動器（單一執行個體、產生存取金鑰、開啟 app 視窗、紀錄檔）
requirements.txt    直接相依套件（版本範圍）
requirements.lock   驗證過的完整版本組合（安裝時優先採用）
app/
  server.py         FastAPI API（存取控制、專案、背景工作）
  config.py         路徑、設定檔、防損毀的 JSON 讀寫、模型完整性檢查、翻譯用量
  fonts.py          字型快取（本機提供字型、MKV 內嵌）
  asr.py            轉錄管線（VAD、雙軌辨識、融合、對齊、時間修正）
  vad.py            Silero VAD 與切窗
  segmenter.py      斷句與時間整理
  translator.py     翻譯與校正（DeepSeek／OpenAI 相容服務）
  media.py          FFmpeg 探測、預覽轉檔、16 kHz 音訊、波形
  viz.py            音訊律動頻帶
  exporter.py       影片輸出（硬字幕／軟字幕／音訊製片）
  jobs.py           背景工作
  procs.py          子程序管理（程式結束時一併結束 FFmpeg）
  sysinfo.py        硬體資訊（顯示卡、記憶體）與不足時的提醒
  dialogs.py        系統原生的資料夾選擇對話框
  version.py        版本號
web/
  index.html  app.css  app.js
  render.js         共用字幕渲染器（預覽與燒錄）
  subparse.js       字幕檔匯入（SRT / VTT / ASS，編碼偵測）
  subexport.js      字幕檔匯出（SRT / VTT / ASS / TXT）
workspace/          專案資料、設定、紀錄檔（自動建立）
tests/              單元與 API 測試（不需 GPU、模型或 FFmpeg）
```

## 開發

```
pip install -r requirements-dev.txt
python -m pytest
node --test tests/js/subparse.test.js tests/js/subexport.test.js tests/js/render.test.js
```

直接執行 `python run.py` 時會自動產生存取金鑰並開啟視窗；以 `uvicorn app.server:app` 啟動時，金鑰會印在 log 中（或事先設定環境變數 `LUMEN_TOKEN`），開啟 `http://127.0.0.1:<port>/#k=<金鑰>`。

| 環境變數 | 用途 |
|---|---|
| `LUMEN_PORT` | 指定連接埠（預設從 8765 起找空的） |
| `LUMEN_TOKEN` | 指定存取金鑰（預設每次啟動隨機產生） |
| `LUMEN_WORKSPACE` | 專案、設定與紀錄檔的存放位置（預設為專案內的 `workspace`） |
| `LUMEN_ASR_MODEL` / `LUMEN_ALIGNER_MODEL` | 改用其他 Hugging Face 上的語音／對齊模型 |
| `LUMEN_VAD_ONNX` | 指定 Silero VAD 的 ONNX 檔 |
| `LUMEN_FONTS_API` | 字型下載來源（預設 Google Fonts） |
| `LUMEN_NO_PRELOAD` | 啟動時不預先載入語音模型 |
| `LUMEN_KEEP_FRAMES` | 保留硬字幕的畫格檔（除錯用） |
| `HF_HOME` / `HF_ENDPOINT` | 模型存放位置／下載鏡像站 |

版本號在 `app/version.py`；升級相依套件時請重新測試並更新 `requirements.lock`（`pip freeze`，去掉 torch）。

## 快捷鍵

Space 播放／暫停 · ←/→ 1 秒（Shift 5 秒）· ↑/↓ 上下句 · S 分割 · W 展開時間軸 · Del 刪除 · Ctrl+Z/Y 復原／重做 · Ctrl+S 立即儲存 · Ctrl+E 匯出 · Ctrl+滾輪 縮放時間軸

## 授權

[MIT](LICENSE)。語音模型 Qwen3-ASR / Qwen3-ForcedAligner 採 Apache-2.0，Silero VAD 採 MIT，字幕字型為 Google Fonts（SIL Open Font License，允許內嵌）。使用 DeepSeek 或其他翻譯服務需遵守其服務條款。

第三方套件由安裝程式從 PyPI 下載，授權各自獨立。其中 qwen-asr 用於韓文對齊的 `soynlp` 採 **GPLv3**：以原始碼形式散布本專案沒有問題，但若要把整個程式（含已安裝的套件）打包成執行檔或安裝程式再散布，需另行遵守 GPL 條款。
