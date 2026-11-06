# macOS App 打包與發版

這份寫給維護者。**標「你要做」的步驟都不會自動發生。**沒有人會替你發布、產生正式金鑰或付費。

## 這裡有什麼

| 檔案 | 用途 |
|---|---|
| `launcher.py` | App 入口:設定路徑、找埠、啟動本機伺服器、乾淨結束 |
| `mac_window.py` | 原生視窗(pywebview + 系統 WebKit);外部連結交給系統瀏覽器 |
| `mac_shell.py` | 開不了視窗時的備案:Dock 圖示 + 瀏覽器 |
| `h0lab.spec`、`build_app.py`、`make_icon.py` | PyInstaller 設定、打包腳本、圖示 |
| `release_tools.py` | 產生金鑰、manifest、簽章、checksums、第三方授權清單 |
| `update_public_key.txt` | 更新公鑰(可以公開);空的時候 App 不能一鍵更新 |
| `requirements-build.txt` | 打包工具與視窗依賴(pywebview、pyobjc),只裝在獨立 venv;原始碼安裝用不到 |
| `../.github/workflows/release.yml`(開發 repo 放在 `oss/overlay/`,只在公開 repo 生效) | 推 `v*` tag 後建立 draft release |
| `../lab/version.py` | 版本號與公開 repo 名稱(`RELEASE_REPO`),要改名只改這裡 |

## 第一次設定(你要做,只做一次)

### 1. 決定公開 repo 名稱
改 `lab/version.py` 的 `RELEASE_REPO`(目前是 `JWcod/h0-lab`)。

### 2. 產生更新金鑰
在自己電腦上、repo 外面執行:

```bash
python3 -m venv /tmp/h0-keygen
```

```bash
/tmp/h0-keygen/bin/pip install -r packaging/requirements-build.txt
```

```bash
/tmp/h0-keygen/bin/python packaging/release_tools.py keygen --out ~/h0-lab-update-key.txt
```

- 螢幕會印出**公鑰**:貼進 `packaging/update_public_key.txt` 並 commit。
- `~/h0-lab-update-key.txt` 是**私鑰**:絕不進 repo、`.env`、`.env.example`、聊天紀錄。把內容放進下面的 GitHub secret,然後離線備份(密碼管理員),再把檔案丟垃圾桶。
- **私鑰遺失或外洩的後果**:App 只相信內嵌的公鑰。遺失 = 舊版 App 再也無法一鍵更新(使用者要手動下載一次新版)。外洩 = 別人可以簽更新,但還要能放到你的 GitHub Release 才會被下載。公鑰寫死在 App 裡,沒有遠端撤銷:處理方式是立刻把 repo 的 Release 權限收回、下架可疑版本、換金鑰,然後公告請所有人**手動**下載新版(換過金鑰的版本舊 App 不會接受)。

### 3. 設定 GitHub secrets(公開 repo 的 Settings → Secrets and variables → Actions)

| Secret | 必要嗎 | 內容 |
|---|---|---|
| `H0_UPDATE_SIGNING_KEY` | 必要 | 私鑰檔的內容。**放在 Environment `release`,不要放 repo 層級**(見下) |
| `APPLE_CERT_P12_BASE64`、`APPLE_CERT_PASSWORD`、`APPLE_SIGN_IDENTITY`、`APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_APP_PASSWORD` | 選用 | Apple 開發者憑證。目前決定不買帳號,不用設;流程照跑,維持 ad-hoc 簽章。之後設了就會自動公證 |

到 Settings → Environments 建立 `release`,設 Required reviewers(自己)與 Deployment branches and tags 只允許 `v*`,再把 `H0_UPDATE_SIGNING_KEY` 放進這個 Environment。這樣改 workflow 或推 tag 都不能自動拿到私鑰,每次簽章要你按批准;另外 workflow 會拒絕簽不在 `main` 上的 tag,簽章那一步也只裝鎖 hash 的 `cryptography`(`requirements-sign.txt`)。

## 發版步驟(你要做)

1. 改 `lab/version.py` 的 `__version__`,commit。使用者資料格式有不相容的變更時,同時把 `DATA_FORMAT_VERSION` 加 1(更新時會提示並先備份)。
2. 打 tag 並推上去:`git tag v0.2.0`、`git push origin v0.2.0`。
3. 等 Actions 跑完(測試 → 打包 Apple Silicon 版 → 簽章 → **draft** release)。
4. 到 GitHub Releases 打開 draft,寫更新內容(App 的「查看更新內容」顯示的就是這段),**貼上下面「第一次開啟」那段**,下載 dmg 自己試裝一次。
5. 確認沒問題才按 **Publish release**。按下去之後,所有開著的 App 一天內會看到新版。

draft 和 prerelease 都不會被 App 看到,想先給少數人試可以發 prerelease。

### 發布的檔案(檔名固定)

| 檔案 | 給誰 |
|---|---|
| `H0-Lab-macOS.dmg` | 使用者下載。README 的下載鈕可以直連 `https://github.com/<repo>/releases/latest/download/H0-Lab-macOS.dmg` |
| `H0-Lab-macOS-update.zip` | App 一鍵更新用 |
| `update-manifest.json`、`.sig` | 更新資訊與 Ed25519 簽章 |
| `SHA256SUMS.txt`、`THIRD_PARTY_NOTICES.txt` | 雜湊與第三方授權清單 |

**系統需求**:Apple Silicon、macOS 14 以上(打包進來的 scipy 等函式庫最低要求,`LSMinimumSystemVersion` 也設成 14.0)。不提供 Intel 版。打包機器是 `macos-15`(`macos-14` runner 已在 2026-11-02 退役),`release_tools.py check-portable` 會擋下要求更新版 macOS 或依賴 App 外面函式庫的版本。

**乾淨機器測試**:Actions 手動跑 `Clean machine test`,在全新的 macOS 15、26 上安裝並啟動 DMG(`packaging/clean_machine_test.sh`),勾 online 時 macOS 15 再抓少量資料。執行紀錄公開後別人看得到,所以腳本只印通過或失敗,抓到的資料不離開 runner。它測不到 Gatekeeper 畫面、原生視窗和實際操作,發版前仍要用另一個 macOS 使用者帳號實測一次。

## 本機打包(開發用)

```bash
python3.13 -m venv /tmp/h0-bvenv
```

```bash
/tmp/h0-bvenv/bin/pip install -r requirements.lock.txt -r packaging/requirements-build.txt
```

```bash
/tmp/h0-bvenv/bin/python packaging/build_app.py --out /tmp/h0-dist
```

不要裝進專案的 `.venv`。測試 App 時請設空的 `HOME`,否則會用到你真實的 `~/Library/Application Support/H0 Lab/`:

```bash
HOME=/tmp/h0-home H0_LAB_NO_BROWSER=1 INVEST_LAB_PORT=18765 "/tmp/h0-dist/dist/H0 Lab.app/Contents/MacOS/H0 Lab"
```

`--test-key`、`--test-api-base` 會產生標記 `test_build` 的版本(可以連本機假伺服器);`release_tools.py check-app` 與 `--require-key` 會拒絕這種版本。

## 第一次開啟(發行說明也要附這段)

目前不做 Apple 公證(付費帳號 US$99 / 年,決定暫不買),所以是 ad-hoc 簽章,macOS 不認得開發者,第一次會擋。發行說明請附:

> 1. 打開 dmg,把 **H0 Lab** 拖到「應用程式」。
> 2. 到「應用程式」雙擊 H0 Lab。macOS 會說無法驗證開發者,先按「完成」。
> 3. 打開**系統設定 → 隱私權與安全性**,往下找到 H0 Lab,按「強制打開」,輸入密碼確認。(macOS 14 也可以對 App 按右鍵 → 打開。)
> 4. 之後就可以正常雙擊。
>
> H0 Lab 是開源專案,還沒有付費的 Apple 公證;可以對照 `SHA256SUMS.txt` 確認下載的檔案沒被改過。

一鍵更新不受影響:更新檔由 App 自己下載,沒有隔離標記,而且先過 Ed25519 簽章與 SHA-256。

**這個流程還沒有在真正下載的 dmg 上驗證過**(本機沒有隔離標記)。公開前請實際從 GitHub 下載一次走過,把上面的步驟改成實際看到的畫面。

## 更新怎麼運作

1. 每天最多查一次 GitHub Releases(只送固定的 User-Agent 與 ETag,沒有使用者資料)。離線模式、`INVEST_LAB_UPDATE_CHECK=0`、設定裡關閉時完全不連線。
2. 按「一鍵更新」:下載 `update-manifest.json` 與 `.sig`,用內嵌公鑰驗證(manifest 含版本、大小、SHA-256,所以版本不能被竄改或降版)。
3. 下載 zip 到 App 旁邊的暫存夾,比對大小與 SHA-256,安全解壓(擋 zip slip、symlink 逃逸、zip bomb)。
4. 檢查版本、`codesign --verify`,並實際跑新版的 `--self-test`。任何一步失敗就中止,舊版沒動過。
5. 舊版視窗關閉,helper 用 `renamex_np(RENAME_SWAP)` 一次對調新舊 `.app`,再跑一次 self-test;失敗就對調回來並重開舊版。
   (本機 API 只防瀏覽器與其他網站;同一台 Mac 上的其他程式能連 127.0.0.1 改設定,但改不了更新來源,更新仍要過簽章。)
6. 新版啟動後,舊版移到 `~/Library/Application Support/H0 Lab/update/previous/` 保留一份。

使用者資料都在 `~/Library/Application Support/H0 Lab/`,更新不會碰。

## 回滾怎麼做

- **發現某個版本有問題(發布者)**:到 GitHub 把那個 release 改回 draft 或刪除,還沒更新的人就不會再收到;接著發一個版本號**更高**的修正版。App 不接受比目前舊的版本,所以不能靠重新發舊版來回滾。
- **使用者要退回上一版**:結束 H0 Lab,把「應用程式」裡的 H0 Lab 丟垃圾桶,再把 `~/Library/Application Support/H0 Lab/update/previous/H0 Lab.app` 搬回「應用程式」(或從 GitHub Releases 下載舊版 dmg)。如果那次更新改過資料格式,還原 `~/Library/Application Support/H0 Lab/backups/` 裡對應的備份。

## 原始碼安裝

`git clone` 的使用者沒有一鍵更新,也沒有原生視窗(維持開瀏覽器):有新版時只顯示要執行的 `git pull --ff-only` 與 `./install.sh`。下載 ZIP 或 git worktree 則顯示新版資訊與下載頁。
