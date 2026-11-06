# Documents / 文件索引

Most documents here are in Traditional Chinese. They are the project's research record, kept close to how they were written, including mistakes and results that did not work out. Names and internal process details were replaced with neutral wording ("maintainer").

這裡多數文件是繁體中文,是本專案的研究紀錄,盡量保留原貌,包括錯誤與沒有成功的結果。人名與內部流程細節已改成中性敘述(「維護者」)。

| Document | What it is |
|---|---|
| [升級報告.md](升級報告.md) | Final report of the v2 upgrade: the main conclusion and how to reproduce it / v2 總結報告 |
| [預先登記.md](預先登記.md) | Pre-registered hypotheses and each result / 預先登記的假設與每個結果 |
| [holdout 評估日誌.md](holdout%20評估日誌.md) | Every time the holdout period was looked at / 每次看 holdout 的紀錄 |
| [模型白皮書.md](模型白皮書.md) | Model specification with formulas and sources / 模型規格、公式與出處 |
| [架構.md](架構.md) | Architecture, environment variables, evaluation framework, merge gate / 架構與評估框架 |
| [閘門校準.md](閘門校準.md) | How the merge gate was calibrated / 合併閘門校準 |
| [存活者偏誤.md](存活者偏誤.md) | How much survivorship bias inflates returns / 存活者偏誤量化 |
| [v1.1新資料基準.md](v1.1新資料基準.md) | The v1.1 model re-tested on point-in-time data / v1.1 在時點正確資料上的基準 |
| [階段2檢查點.md](階段2檢查點.md)、[階段2診斷.md](階段2診斷.md) | Stage 2 results and diagnostics / 階段 2 結果與診斷 |
| [資料源可得性.md](資料源可得性.md) | What each free source could provide when probed / 各免費資料源的探測紀錄 |
| [模型客製化規格.md](模型客製化規格.md) | Custom model format and expression language / 自訂模型格式與運算式語言 |

## Not published / 未公開的內容

Some documents and code comments refer to files that are not in this repository:

- **Files with third-party data:** `資料品質_TW.md`, `資料品質_US.md`, `tests/baseline/outputs/`, per-judgment `rows_*.csv.gz`, and `data/`. Only SHA-256 fingerprints and aggregate statistics are published, so the slow baseline tests skip without the frozen data.
- **Internal planning and process notes:** `升級計畫.md`, `任務清單.md`, and interface and onboarding specs such as `上手精靈規格.md`, `安裝與上手規格.md`, and `介面商業化規格.md`.

部分文件與程式註解提到的檔案不在本 repo:含第三方資料的檔案(上列)只公開 SHA-256 指紋與彙總統計,因此 slow 基準測試在沒有凍結資料時會 skip;內部規劃與流程筆記則不公開。

Sentences written in the first person, such as "我的建議", and "你" in `預先登記.md`, are from the original working documents: an AI development assistant (Claude) writing to the maintainer.

`預先登記.md` 裡的「我」與「你」來自原始工作文件,是 AI 開發助理(Claude)寫給維護者的文字。
