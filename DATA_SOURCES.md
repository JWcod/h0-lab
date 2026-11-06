# Data sources and privacy

[繁體中文](#資料來源與隱私)

**H0 Lab does not distribute market data.** This repository has no prices, statements, or recorded responses. Your own computer downloads the data directly from each source, for your own research. **You are responsible for following each source's terms.** Do not republish the data or use it commercially unless the source allows it.

## Your data stays on your Mac

There are no accounts, no project servers, no analytics, and no telemetry. The local site listens on `127.0.0.1` only. Everything stays on your Mac, in `~/Library/Application Support/H0 Lab/` for the app and in the project folder for source installs: `private/` (watchlists, notes, your models), `.env` (optional settings), and `data/` (download cache, backtest results). Your notes and models are never sent anywhere. The web page loads no external scripts, fonts, or trackers.

## Every outgoing connection

While running, the app only connects to these hosts. Each host sees your IP address and what is being looked up. Offline mode (`INVEST_LAB_OFFLINE=1`) blocks all of them.

| Host | Used for | Code |
|---|---|---|
| Yahoo Finance (`query1.finance.yahoo.com`, `query2.finance.yahoo.com`, `fc.yahoo.com`), through the unofficial `yfinance` library | Prices, statements, estimates, profiles, US news | `lab/sources/yahoo.py`, `lab/research/backtest.py`, `scripts/fetch_sp500_prices.py` |
| `api.finmindtrade.com` | Taiwan statements, monthly revenue, institutional flows. Sends your FinMind token if you set one | `lab/sources/finmind.py` |
| `www.sec.gov`, `data.sec.gov` | US statements and filings. Sends your `SEC_USER_AGENT`, or a placeholder with no personal details | `lab/sources/sec.py` |
| `openapi.twse.com.tw` | Taiwan daily quotes, valuation, material information | `lab/sources/twse.py`, `lab/sources/mops.py` |
| `www.twse.com.tw` | Market history for research data (only when you build it) | `lab/sources/twse_hist.py` |
| `www.tpex.org.tw` | OTC valuation data | `lab/sources/twse.py` |
| `opendata.tdcc.com.tw` | Shareholder distribution | `lab/sources/tdcc.py` |
| `news.google.com` | News headlines (RSS) | `lab/sources/news.py` |
| `en.wikipedia.org` | Current S&P 500 members, one research script only | `scripts/fetch_sp500_prices.py` |
| `api.github.com`, `github.com`, `*.githubusercontent.com` (download files) | macOS app only: checking for a new version, and downloading it after you confirm | App update check |

Installing from source (`install.sh`) also downloads Python packages from `pypi.org` and `files.pythonhosted.org`.

`yfinance` uses `curl_cffi` with a browser TLS profile; H0 Lab's own requests do not. Requests are cached on disk, rate limited, and retried with backoff. When a source fails, the app shows older cached data or marks the item as unavailable.

## Terms of each source

Last reviewed 2026-11-03. This is not legal advice, and terms change, so read the originals.

| Source | Terms | Note |
|---|---|---|
| Yahoo Finance | [Yahoo terms](https://help.yahoo.com/kb/SLN2310) | Personal use, no redistribution. Unofficial access can break at any time |
| [FinMind](https://finmind.github.io/en/) | FinMind site | Educational, non-commercial use. A free token raises the quota |
| [SEC EDGAR](https://www.sec.gov/os/accessing-edgar-data) | SEC fair access | Set `SEC_USER_AGENT` with your contact details. At most 10 requests per second |
| TWSE (`openapi.twse.com.tw`, `www.twse.com.tw`) | [TWSE terms](https://www.twse.com.tw/zh/page/terms/use.html) | The data comes from requests your own computer makes, and you are responsible for following the TWSE terms |
| TPEx | TPEx site | Read the TPEx terms before use |
| TDCC | [Open government data license](https://data.gov.tw/license) | Weekly file |
| Google News RSS | [Google terms](https://policies.google.com/terms) | Headlines link to the original article |
| Wikipedia | [Wikimedia terms](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use) | CC BY-SA |

Research files you download by hand, not fetched by the app: Taiwan central bank annual reports (historical 10-year rates), [Damodaran](https://pages.stern.nyu.edu/~adamodar/) equity risk premiums, and the [Ken French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) for one diagnostic script. Keep them local.

If you represent a data provider and have a concern, please [open an issue](https://github.com/JWcod/h0-lab/issues).

---

# 資料來源與隱私

**H0 Lab 不散布市場資料。** 本 repo 不含價格、財報或錄下來的回應。資料由你自己的電腦直接向各來源下載,供你自己研究。**遵守各來源條款是你自己的責任。** 除非來源允許,不要再散布或商業使用。

## 你的資料留在你的 Mac

沒有帳號、沒有專案伺服器、沒有分析工具、沒有遙測。本機網站只監聽 `127.0.0.1`。所有東西都留在你的 Mac(App 在 `~/Library/Application Support/H0 Lab/`,原始碼安裝在專案資料夾):`private/`(觀察清單、筆記、你的模型)、`.env`(選填設定)、`data/`(下載快取、回測結果)。筆記與模型不會傳到任何地方。網頁不載入外部腳本、字型或追蹤器。

## 所有對外連線

執行時,程式只會連到下列主機。每台主機看得到你的 IP 位址與查詢內容。離線模式(`INVEST_LAB_OFFLINE=1`)會擋掉全部連線。

| 主機 | 用途 | 程式 |
|---|---|---|
| Yahoo Finance(`query1.finance.yahoo.com`、`query2.finance.yahoo.com`、`fc.yahoo.com`,透過非官方的 `yfinance` 套件) | 價格、財報、預估、公司資料、美股新聞 | `lab/sources/yahoo.py`、`lab/research/backtest.py`、`scripts/fetch_sp500_prices.py` |
| `api.finmindtrade.com` | 台股財報、月營收、籌碼;有設定就會送出你的 FinMind token | `lab/sources/finmind.py` |
| `www.sec.gov`、`data.sec.gov` | 美股財報與申報;送出你的 `SEC_USER_AGENT`,沒填就送不含個資的預設值 | `lab/sources/sec.py` |
| `openapi.twse.com.tw` | 台股每日行情、估值、重大訊息 | `lab/sources/twse.py`、`lab/sources/mops.py` |
| `www.twse.com.tw` | 研究資料用的歷史行情(只在你建立研究資料時) | `lab/sources/twse_hist.py` |
| `www.tpex.org.tw` | 上櫃估值資料 | `lab/sources/twse.py` |
| `opendata.tdcc.com.tw` | 股權分散 | `lab/sources/tdcc.py` |
| `news.google.com` | 新聞標題(RSS) | `lab/sources/news.py` |
| `en.wikipedia.org` | S&P 500 現有成分,只有一支研究腳本使用 | `scripts/fetch_sp500_prices.py` |
| `api.github.com`、`github.com`、`*.githubusercontent.com`(下載檔案) | 只有 macOS App:檢查新版本,你確認後才下載 | App 的更新檢查 |

從原始碼安裝(`install.sh`)時,另外會從 `pypi.org`、`files.pythonhosted.org` 下載 Python 套件。

`yfinance` 會用 `curl_cffi` 的瀏覽器 TLS 設定;H0 Lab 自己的請求不用。請求有磁碟快取、限速與退避重試;來源失敗時顯示舊快取或標示無法取得。

## 各來源條款

最後檢視 2026-11-03。以下不是法律意見,條款會變動,請讀原文。

| 來源 | 條款 | 說明 |
|---|---|---|
| Yahoo Finance | [Yahoo 條款](https://help.yahoo.com/kb/SLN2310) | 個人使用,不得再散布;非官方存取,隨時可能失效 |
| [FinMind](https://finmind.github.io/en/) | FinMind 網站 | 教育、非商業用途;免費 token 可提高額度 |
| [SEC EDGAR](https://www.sec.gov/os/accessing-edgar-data) | SEC 公平存取規定 | `SEC_USER_AGENT` 要填你的聯絡方式;每秒最多 10 次 |
| 證交所(`openapi.twse.com.tw`、`www.twse.com.tw`) | [證交所使用條款](https://www.twse.com.tw/zh/page/terms/use.html) | 資料來自你本機發出的請求,遵守證交所條款由你自己負責 |
| 櫃買中心 | 櫃買中心網站 | 使用前請先讀櫃買中心條款 |
| 集保 | [政府資料開放授權](https://data.gov.tw/license) | 每週檔案 |
| Google News RSS | [Google 條款](https://policies.google.com/terms) | 標題連到原文 |
| Wikipedia | [Wikimedia 條款](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use) | CC BY-SA |

需要自己手動下載、App 不會去抓的研究檔案:中央銀行年報(台灣 10 年期公債歷史利率)、[Damodaran](https://pages.stern.nyu.edu/~adamodar/) 股權風險溢酬、一支診斷腳本用的 [Ken French 因子資料庫](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html)。這些檔案只留在本機。

如果你代表資料提供者並有疑慮,請[開 issue](https://github.com/JWcod/h0-lab/issues)。
