# 2FA OTP Extraction Experiments

這是一個 OTP 與驗證連結擷取實驗專案，用 128 筆 Email/SMS 樣本比較：

1. 論文 baseline（Regex + BGE Sentence Transformer）
2. Regex v2 + BGE
3. Regex v2 + BGE + OpenAI fallback
4. Regex v2 + OpenAI（所有含 Regex 候選的訊息都送 API）

OpenAI 只能從 Regex 提供的候選 ID 中選擇，不得自行生成 OTP。Ground truth 在預測完成後才用於計分，不會送入 prompt 或路由邏輯。

## 實驗結果

| 架構 | OTP Top-1 | OTP Top-3 | 驗證連結 | 負樣本誤抽 | API 呼叫 | API 成本 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 論文 baseline | 88/103 | 89/103 | 7/10 | 3/15 | 0 | US$0 |
| Regex v2 + BGE | 91/103 | 95/103 | 7/10 | 5/15 | 0 | US$0 |
| Regex v2 + BGE + OpenAI fallback | 103/103 | 103/103 | 7/10 | 0/15 | 66 | US$0.017872 |
| Regex v2 + OpenAI | 103/103 | 103/103 | 7/10 | 0/15 | 123 | US$0.022116 |

這份資料上，兩種 OpenAI 架構品質相同。Hybrid 少 57 次 API 呼叫，節省約 19.2% API 成本，並減少外傳訊息數。正式部署前應另用未參與開發的 holdout set 檢查泛化能力。

## 環境需求

- Windows 10/11
- Python 3.12 64-bit
- 不需要獨立顯示卡，PyTorch 使用 CPU 版本
- BGE 實驗第一次執行需要網路下載 `BAAI/bge-large-zh-v1.5`
- OpenAI 實驗才需要 API key

## 安裝

```powershell
git clone https://github.com/Kevin171717/2FAOTPTest.git
cd 2FAOTPTest
.\setup.cmd
```

`setup.cmd` 會建立 `.venv`、安裝 CPU 版依賴，並在不存在時從 `.env.example` 建立 `.env`。

手動安裝方式：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-reproduction.txt
Copy-Item .env.example .env
```

## API 設定

只把 API key 放在本機 `.env`：

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-luna
```

`.env` 已加入 `.gitignore`。請不要把 API key 放在 source code、command line、Notebook 或實驗結果中。部署到 CI/CD 時，應使用 GitHub Actions secret `OPENAI_API_KEY`，不要寫入 repository variable 或 workflow YAML。

## 不付費驗證

執行全部單元測試：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p "test_*.py"
```

檢查兩種 API 流程但不發出 API 要求：

```powershell
.\run_bge_openai_experiment.cmd --dry-run
.\run_regex_openai_experiment.cmd --dry-run
```

## 實驗指令

復現論文 baseline：

```powershell
.\run_paper_reproduction.cmd
```

Regex v2 + BGE + OpenAI fallback 小規模付費 smoke test：

```powershell
.\run_bge_openai_experiment.cmd --ids 86,92,103,128,131,137 --max-api-calls 6
```

Regex v2 + OpenAI 小規模付費 smoke test：

```powershell
.\run_regex_openai_experiment.cmd --ids 4,86,92,103,128,137 --max-api-calls 6 --output results\regex_openai\smoke_test.json
```

完整付費實驗：

```powershell
.\run_bge_openai_experiment.cmd
.\run_regex_openai_experiment.cmd
```

實驗每完成一筆就寫入 checkpoint。中斷後可加上 `--resume` 繼續。付費執行前建議先使用 `--dry-run` 和 `--max-api-calls`。

## 離線模式

第一次 BGE 執行成功、模型已存在 Hugging Face cache 後，可用：

```powershell
$env:OTP_OFFLINE_MODE = "1"
.\run_bge_openai_experiment.cmd --dry-run
```

新電腦不要先開啟離線模式，否則無法下載 BGE。

## 輸出與報表

| 路徑 | 用途 |
| --- | --- |
| `results/reproduction/` | 論文 baseline 與復現結果 |
| `results/regex_v2/` | Regex v2 + BGE 基準資料 |
| `results/bge_openai/` | Hybrid JSON checkpoint，預設不進 Git |
| `results/regex_openai/` | Regex + OpenAI JSON checkpoint，預設不進 Git |
| `outputs/` | 可閱讀的 Excel 比較報表 |
| `reports/` | 可公開發佈的精簡比較報表 |

OpenAI JSON 可能包含候選上下文與 response ID，因此預設不推送 GitHub。Excel 報表中的長 URL 已截斷顯示。

`scripts/*.mjs` 是本次 Codex 環境的 Excel 報表產生器，需要 `@oai/artifact-tool`。一般 Python 部署不必安裝它，也不影響核心實驗。

## 專案結構

| 路徑 | 內容 |
| --- | --- |
| `src/` | Regex v2、BGE gate、OpenAI client 與實驗 runner |
| `tests/` | 候選 recall、路由、prompt 隔離與 API schema 測試 |
| `legacy/` | 論文原始 extractor 與復現程式 |
| `docs/` | 各架構的實驗說明 |
| `otp_dataset_dedup.xlsx` | 128 筆去重複實驗資料 |

## 安全準則

- API request 設定 `store=false`。
- API key 不會寫入 JSON 或 Excel。
- `.env`、`.venv`、API JSON、對談紀錄、截圖與本機參考檔都由 `.gitignore` 排除。
- 上傳前應執行 secret scan，並以 `git status` 檢查 staged files。

更完整的實驗說明見 [docs/BGE_OPENAI_EXPERIMENT.md](docs/BGE_OPENAI_EXPERIMENT.md)、[docs/REGEX_OPENAI_EXPERIMENT.md](docs/REGEX_OPENAI_EXPERIMENT.md) 與 [docs/REPRODUCTION.md](docs/REPRODUCTION.md)。
