# Security policy

[繁體中文](#安全政策)

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting: go to the [Security tab](https://github.com/JWcod/h0-lab/security) and choose **Report a vulnerability**. There is no email address for reports.

Include what you found, how to reproduce it, and what an attacker could do. The maintainer reads reports as time allows, usually within a week. This is a volunteer project with no bug bounty.

## Supported versions

Only the latest release and the `main` branch get security fixes.

## Scope

H0 Lab runs on your own computer and listens on `127.0.0.1` only. These are in scope:

- Any way for a website or another machine to reach the local server or trigger its write endpoints.
- Script injection through external text, such as news headlines or filings.
- Reading or leaking files in `private/` or `.env`.
- Code execution through custom model expressions.
- Problems in the update check or the release packaging.

Out of scope: wrong numbers or data quality (use a regular issue), and problems that need someone who already controls your user account.

---

# 安全政策

## 回報漏洞

請不要開公開 issue。請用 GitHub 的私下回報:到 [Security 分頁](https://github.com/JWcod/h0-lab/security) 選 **Report a vulnerability**。本專案沒有回報用的電子郵件信箱。

請說明你發現什麼、怎麼重現、攻擊者能做什麼。維護者會視時間處理,通常一週內回覆。這是志願專案,沒有漏洞獎金。

## 支援的版本

只有最新版本與 `main` 分支會收到安全修正。

## 範圍

H0 Lab 在你自己的電腦上執行,只監聽 `127.0.0.1`。以下屬於範圍內:

- 讓網站或其他機器連到本機伺服器、或觸發會寫入資料的端點。
- 透過外部文字(新聞標題、公告)注入腳本。
- 讀取或外洩 `private/`、`.env` 的內容。
- 透過自訂模型運算式執行程式。
- 更新檢查或發布打包的問題。

範圍外:數字錯誤或資料品質(請開一般 issue),以及需要攻擊者已控制你使用者帳號的問題。
