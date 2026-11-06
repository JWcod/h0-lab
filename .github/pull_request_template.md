<!-- The maintainer reviews every pull request and decides whether to merge it. See CONTRIBUTING.md. 每個 PR 由維護者審查並決定是否合併,見 CONTRIBUTING.md。 -->

## What and why / 改了什麼、為什麼

Closes #

## How it was tested / 怎麼測試

## Checklist

- [ ] Every commit is signed off (`git commit -s`, DCO). 每個 commit 都有 sign-off。
- [ ] **No third-party data:** no prices, statements, recorded API responses, or screenshots with real market data. Test fixtures are synthetic. **不含第三方資料**,測試資料是合成的。
- [ ] No keys, tokens, personal data, `.env`, `data/`, or `private/`. 不含金鑰、個資、`.env`、`data/`、`private/`。
- [ ] `ruff check .` and `pytest -q` pass offline. 離線通過 ruff 與快速測試。
- [ ] No broker connection, order placement, paid API, telemetry, or stock recommendation. 沒有券商、下單、付費 API、遙測、個股推薦。
- [ ] No new dependency, or it was agreed in an issue first. 沒有新依賴,或已先在 issue 討論。

## If this changes the model or research code / 如果動到模型或研究程式

- [ ] Pre-registered in `docs/預先登記.md`, and the `scripts/gate.py` result is attached. 已預先登記,附閘門結果。
- [ ] Features read data only through `PITStore.as_of(d)`, with a tamper test. 只透過 PIT 取資料,有竄改測試。
- [ ] The holdout period (2024-10-01 onward) was not used. 沒有使用 holdout 期間。
- [ ] Data changes and model changes are in separate commits. 資料與模型改動分開 commit。
