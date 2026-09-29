# LumenSubs · AI 字幕翻譯工作室

把影片或音訊自動轉錄、翻譯成指定語言，編輯後輸出字幕檔或嵌入字幕的影片。

- **影片翻譯**：影片 → 轉錄 → 翻譯 → 雙語字幕 → 燒錄硬字幕（MP4）或內封軟字幕（MKV）
- **音訊製片**：音訊 + 背景圖 → 帶字幕（及音訊律動動畫）的影片
- 字幕檔匯出：SRT / VTT / ASS（保留樣式）/ TXT，雙語、僅譯文或僅原文
- **匯入既有字幕**（SRT / VTT / ASS / SSA，自動辨識編碼與雙語排列），可直接校對、重新翻譯或燒錄
- 可自選輸出資料夾（系統原生對話框，會記住上次位置）與檔案名稱；同名檔案會先詢問是否覆蓋
- 原文／譯文對照編輯、時間碼微調、時間軸拖曳、分割／合併、單句重新翻譯、整體偏移
- 字幕樣式（字型、字級、字重、顏色、描邊、陰影、圓角底框）與位置可調，可直接在畫面上拖曳
- 自動偵測原文語言（30 種），可翻譯為 33 種語言

## 安裝與啟動（Windows）

**事前準備**：安裝 [Python 3.10–3.13](https://www.python.org/downloads/)（建議 3.12，安裝時勾選「Add python.exe to PATH」）。
建議使用 NVIDIA 顯示卡（CPU 也能執行但轉錄非常慢）。

下載或 `git clone` 本專案後，**雙擊 `start.bat`** 即可。第一次執行會自動：

1. 在專案內建立獨立的虛擬環境 `.venv`（不影響系統的 Python）
2. 偵測顯示卡驅動，安裝對應的 **CUDA 版 PyTorch**（無 NVIDIA 顯示卡則安裝 CPU 版）
3. 安裝其餘套件
4. 檢查 FFmpeg，沒有的話詢問是否用 `winget` 自動安裝
5. 下載語音模型 Qwen3-ASR-1.7B、Qwen3-ForcedAligner-0.6B、Silero VAD（約 4.5 GB）

首次安裝視網速約需 10–30 分鐘；之後再執行 `start.bat` 只做快速檢查，數秒內啟動。
程式會啟動本機伺服器（`http://127.0.0.1:8765`）並以 Edge／Chrome 應用程式視窗開啟。

**DeepSeek API Key**：啟動後到右上角「設定 → 翻譯」填入（或在專案根目錄放一個內容為 key 的 `apikey.txt`）。

| 參數 | 用途 |
|---|---|
| `start.bat --reinstall` | 重新偵測顯示卡並重裝 PyTorch、更新其餘套件（例如更新顯示卡驅動後） |
| `start.bat --cpu` | 改用 CPU 版 PyTorch（會記住，之後以 `--reinstall` 改回 GPU） |
| `start.bat --no-browser` | 只啟動伺服器，不開視窗（主控台會印出含存取金鑰的網址） |

無法連線 huggingface.co 時，可先設定環境變數 `HF_ENDPOINT` 使用鏡像站再執行。
手動安裝：`pip install -r requirements.txt`（PyTorch 請依 [pytorch.org](https://pytorch.org) 安裝 CUDA 版），再執行 `python run.py`。

## 隱私與安全

- **轉錄在本機完成**，音訊不會上傳。
- **翻譯會把逐字稿送到 DeepSeek**（或你在設定中指定的 API 位址）：每個翻譯批次都附上完整逐字稿作為上下文。內容敏感時，可只轉錄、不翻譯。
- 字型由本機伺服器提供：第一次用到時從 Google Fonts 下載並快取到 `workspace/fonts/`（安裝時會先下載介面與預設字幕字型，約 22 MB），之後可離線使用，瀏覽器不會連到外部網站。無法連線時，尚未下載的字型改用系統字型。
- 本機伺服器只接受本機連線，並要求每次啟動時產生的**存取金鑰**（啟動時自動帶入視窗），其他網頁或程式無法操作它。只有本程式輸出的檔案可以從介面開啟，字幕只能寫成字幕檔副檔名。
- API Key 以明文存在 `workspace/settings.json`（或 `apikey.txt`），兩者都已列入 `.gitignore`；分享專案資料夾前請先移除。更換 API 位址時需一併重新輸入金鑰。

## 品質設計

### 轉錄（Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B，本機 GPU）

1. **Silero VAD** 找出停頓，將時間軸切成 ≤20 秒的「語句窗」，再組成約 100 秒的長段。
2. **雙軌辨識**：長段辨識（上下文完整、用字最準）＋ 語句窗辨識（局部精確）。以字元序列比對把長段文字分配回各語句窗，逐窗擇優；若長段漏字，改用語句窗結果。
3. **防迴圈**：解碼器陷入重複（如長時間的喘息、音樂）時提前停止，並以較短分段重新解碼該段，避免漏句與長時間卡住。
4. **語言漂移修正**：以主要語言與文字字元系統判斷，重新解碼被誤判語言的片段。
5. **窗內強制對齊**：在短窗內做字級對齊（穩定、不會長段崩潰），再用 VAD 修正吞掉靜音或零長度的異常時間。
6. **智慧斷句**：依句末標點、停頓、長度、雙語可讀性與語言黏著規則（不在助詞前斷、不以冠詞結尾）切分；合併過短的語氣詞；加入適度停留與最短顯示時間，且不重疊。

18 分鐘日語音訊在 RTX 5060 Ti 上轉錄約 55 秒。

### 翻譯（DeepSeek `deepseek-flash`）

1. 先分析全文，產生劇情摘要、說話者關係與語氣、術語表，讓平行批次保持一致。
2. 每個批次都附上**完整逐字稿**作為上下文（前綴快取，成本極低），以 ID 對應嚴格 JSON 輸出並驗證，缺漏自動重試。
3. 翻譯時同步**校正辨識錯字**（同音字、聽錯的詞），只接受微幅修正，不會改寫內容。
4. 目標語言字幕慣例：台灣正體用語（OpenCC 保險）、中文句末不加句號、跨句自然分配到各字幕。

### 所見即所得

預覽與匯出使用**同一個 Canvas 字幕渲染器**（自動換行、平衡斷行、日文禁則、RTL、描邊、陰影、圓角底框）。
燒錄硬字幕時，瀏覽器以輸出解析度逐句繪製字幕畫格，FFmpeg 依時間碼疊加（逐格精準）後以 NVENC 編碼——成品與預覽完全一致。
ASS 字幕檔與 MKV 軟字幕則是**近似樣式**：換行位置、字級、顏色、描邊與位置沿用預覽，但圓角底框、柔和陰影與雙語間距無法以 ASS 完整表達。
MKV 會內嵌所用字型（一般與粗體，中日韓字型每個約 7 MB），在沒有安裝這些字型的電腦上也能正確顯示；單獨匯出的 ASS 檔則需播放端安裝相同字型。
音訊律動動畫的頻帶資料由後端預先計算，預覽與匯出共用同一份。

## 專案結構

```
start.bat           一鍵安裝／啟動（Windows）
bootstrap.py        環境檢查：.venv、PyTorch（自動選 CUDA 版本）、套件、FFmpeg、模型
run.py              啟動器（產生存取金鑰、開啟 app 視窗）
app/
  server.py         FastAPI API（存取控制、專案、背景工作）
  config.py         路徑與設定檔
  fonts.py          字型快取（本機提供字型、MKV 內嵌）
  asr.py            轉錄管線（VAD、雙軌辨識、融合、對齊、時間修正）
  vad.py            Silero VAD 與切窗
  segmenter.py      斷句與時間整理
  translator.py     DeepSeek 翻譯與校正
  media.py          FFmpeg 探測、預覽轉檔、16 kHz 音訊、波形
  viz.py            音訊律動頻帶
  exporter.py       影片輸出（硬字幕／軟字幕／音訊製片）
  jobs.py           背景工作
  dialogs.py        系統原生的資料夾選擇對話框
web/
  index.html  app.css  app.js
  render.js         共用字幕渲染器（預覽與燒錄）
  subparse.js       字幕檔匯入（SRT / VTT / ASS）
workspace/          專案資料（自動建立）
tests/              單元與 API 測試（不需 GPU、模型或 FFmpeg）
```

## 開發

```
pip install -r requirements-dev.txt
python -m pytest
node --test tests/js/subparse.test.js
```

直接執行 `python run.py` 時會自動產生存取金鑰並開啟視窗；以 `uvicorn app.server:app` 啟動時，金鑰會印在 log 中（或事先設定環境變數 `LUMEN_TOKEN`），開啟 `http://127.0.0.1:<port>/#k=<金鑰>`。

## 快捷鍵

Space 播放／暫停 · ←/→ 1 秒（Shift 5 秒）· ↑/↓ 上下句 · S 分割 · W 展開時間軸 · Del 刪除 · Ctrl+Z/Y 復原／重做 · Ctrl+S 立即儲存 · Ctrl+E 匯出 · Ctrl+滾輪 縮放時間軸

## 授權

[MIT](LICENSE)。語音模型 Qwen3-ASR / Qwen3-ForcedAligner 採 Apache-2.0，Silero VAD 採 MIT，使用 DeepSeek API 需遵守其服務條款。
