# Contributing to H0 Lab

[繁體中文](#參與-h0-lab)

Thanks for your interest. Issues, Discussions, and pull requests are welcome.

## How it works

- **Fork and pull request.** People outside the project cannot create branches here. Fork the repository, make a branch in your fork, and open a pull request against `main`.
- **Open an issue first** for anything bigger than a small fix, so you don't spend time on something that won't be merged.
- **The maintainer decides.** Every pull request is reviewed by the maintainer, and the maintainer decides whether to merge it. A correct pull request can still be declined if it does not fit the project's direction or would be costly to maintain.
- **Security problems** go through private reporting, not issues. See [SECURITY.md](SECURITY.md).
- When reporting a bug, never paste your `.env`, tokens, or anything from `private/`.

## Sign off your commits (DCO)

Every commit needs a `Signed-off-by` line. It certifies that you have the right to submit the work under the project's license (Apache License 2.0), as described in the [Developer Certificate of Origin](https://developercertificate.org/). A GitHub noreply address is fine.

```bash
git commit -s -m "Fix the thing"
```

## Setup and checks

```bash
./install.sh
```

```bash
.venv/bin/python -m pip install "ruff==0.15.22" "pytest==9.2.0"
```

```bash
.venv/bin/ruff check .
```

```bash
INVEST_LAB_OFFLINE=1 .venv/bin/python -m pytest -q
```

Tests run offline. The repository contains no market data, so tests that need frozen data skip themselves. Do not bulk-download data to run tests. CI uses no secrets, and a first-time contributor's CI run waits for the maintainer's approval.

## Project rules

- Python computes every number. No language model does arithmetic or produces a score.
- Features read data only through `PITStore.as_of(d)`, with a tamper test (`lab/pit/tamper.py`).
- Prices from 2024-10-01 on are the holdout period. Do not tune anything against them.
- Keep data changes and model changes in separate commits. New or changed metrics also update `lab/metric_dictionary.py`.
- Interface text goes through the i18n dictionaries (Traditional Chinese and English). No gray or low-contrast text in dark mode. Every page keeps the "not investment advice" notice.

## Not accepted

- Broker or exchange connections, order placement, or any code that could place a real order.
- Paid APIs, or services that need an account or key beyond the existing optional free tokens. This includes LLM APIs.
- Stock picks, ratings presented as the project's opinion, or wording that implies likely or guaranteed profit.
- Third-party data: prices, statements, recorded API responses, or screenshots with real market data. Test fixtures must be synthetic.
- Code that gets around a data provider's rate limits or access rules.
- Telemetry, analytics, or anything that sends user data off the machine.
- New dependencies that were not agreed in an issue first.

---

# 參與 H0 Lab

感謝你的興趣,歡迎開 issue、參加 Discussions 或送 PR。

## 流程

- **Fork 後開 PR。** 外部人士不能在本 repo 建立分支。請先 fork,在你的 fork 開分支,再對 `main` 開 PR。
- **比小修正大的改動請先開 issue**,避免做了不會被合併的東西。
- **由維護者決定。** 每個 PR 都由維護者審查,是否合併由維護者決定。PR 即使正確,不符合專案方向或維護成本太高,也可能不合併。
- **安全問題**請私下回報,不要開 issue,見 [SECURITY.md](SECURITY.md)。
- 回報問題時,絕對不要貼 `.env`、token 或 `private/` 的內容。

## Commit 要 sign-off(DCO)

每個 commit 都要有 `Signed-off-by` 行,代表你依 [Developer Certificate of Origin](https://developercertificate.org/) 確認有權以本專案授權(Apache License 2.0)提交這份內容。可以使用 GitHub noreply 信箱。

```bash
git commit -s -m "修正某個問題"
```

## 環境與檢查

安裝與檢查指令同上方英文段落。測試一律離線;repo 不含市場資料,需要凍結資料的測試會自動 skip,不要為了跑測試大量下載資料。CI 不使用任何 secret,第一次貢獻的人,CI 要等維護者核准才會執行。

## 專案規則

- 數字一律由 Python 計算,語言模型不算數、不產生分數。
- 特徵只能透過 `PITStore.as_of(d)` 取資料,並附竄改測試(`lab/pit/tamper.py`)。
- 2024-10-01 以後的股價是 holdout 期間,不得拿來調任何東西。
- 資料改動與模型改動分開 commit;新增或修改指標要同步更新 `lab/metric_dictionary.py`。
- 介面文字走 i18n 字典(繁體中文與英文);深色模式不用灰色或低對比文字;每頁保留「非投資建議」聲明。

## 不接受的項目

- 連接券商或交易所、下單,或任何可能下真單的程式。
- 付費 API,或需要帳號、金鑰的服務(現有選填的免費 token 除外),包含 LLM API。
- 個股推薦、當成本專案看法的評等,或暗示可能獲利、保證獲利的文字。
- 第三方資料:價格、財報、錄下來的 API 回應、含真實市場資料的截圖。測試資料必須是合成的。
- 規避資料來源限流或存取規則的程式。
- 遙測、分析,或任何把使用者資料送出本機的程式。
- 沒有先在 issue 討論過的新依賴。
