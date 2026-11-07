"""原生視窗:用系統 WebKit(pywebview)開本機網頁,不跳瀏覽器。

視窗只顯示本機伺服器的頁面;外部連結一律交給系統瀏覽器(只限 http/https),其他 scheme 擋掉。
關閉視窗就結束 App。拿不到視窗時 run() 回傳 False,launcher 退回「Dock 圖示 + 瀏覽器」(mac_shell.py)。
pyobjc 與 webview 都在函式裡才 import:classify_url 要能在沒有打包依賴的環境測試。
"""
from __future__ import annotations

import locale
import os
import signal
import subprocess
import threading
from typing import Callable
from urllib.parse import urlsplit

WINDOW_TITLE = "H0 Lab"
# 網頁載入前看到的視窗底色:和網頁預設的淺色背景(web/css/tokens.css 的 --wash-top)一樣,打開時才不會先閃一片深色
WINDOW_BG = "#F8FAFF"
EXIT_GRACE_SECONDS = 5
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
_ZH_MENU = {"cocoa.menu.about": "關於", "cocoa.menu.services": "服務", "cocoa.menu.view": "顯示方式", "cocoa.menu.edit": "編輯",
            "cocoa.menu.hide": "隱藏", "cocoa.menu.hideOthers": "隱藏其他", "cocoa.menu.showAll": "全部顯示", "cocoa.menu.quit": "結束",
            "cocoa.menu.fullscreen": "進入全螢幕", "cocoa.menu.cut": "剪下", "cocoa.menu.copy": "拷貝", "cocoa.menu.paste": "貼上",
            "cocoa.menu.selectAll": "全選", "global.quit": "結束", "global.ok": "好", "global.cancel": "取消", "global.saveFile": "儲存檔案"}

window = None   # 目前的視窗(整合測試要從外面操作)


def classify_url(url: str, port: int) -> str:
    """視窗要載入一個網址時怎麼處理。internal:留在視窗;external:交給系統瀏覽器;blocked:不理它。"""
    try:
        u = urlsplit(url)
        scheme, host, url_port = u.scheme.lower(), (u.hostname or "").lower(), u.port
    except ValueError:
        return "blocked"
    if scheme == "about":
        return "internal"
    if scheme == "blob":    # 匯出檔案用:只認自己頁面產生的 blob
        return "internal" if classify_url(url[len("blob:"):], port) == "internal" else "blocked"
    if scheme not in ("http", "https") or not host:
        return "blocked"
    return "internal" if scheme == "http" and host in _LOCAL_HOSTS and url_port == port else "external"


def open_external(url: str) -> None:
    """不用 webbrowser 模組:它在 macOS 走 osascript。呼叫前 classify_url 已確認是 http/https。"""
    threading.Thread(target=subprocess.run, args=(["/usr/bin/open", url],), kwargs={"capture_output": True, "timeout": 30, "check": False},
                     daemon=True).start()


def _install_link_policy(cocoa, port: int) -> None:
    """包住 pywebview 的 delegate:同一個視窗內跳去外部網站的情況,它自己不會擋。"""
    import objc
    import WebKit
    base = cocoa.BrowserView.BrowserDelegate
    cancel = getattr(WebKit, "WKNavigationActionPolicyCancel", 0)

    class H0LabBrowserDelegate(base):
        def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
            url = str(action.request().URL().absoluteString())
            kind = classify_url(url, port)
            if kind == "internal":
                objc.super(H0LabBrowserDelegate, self).webView_decidePolicyForNavigationAction_decisionHandler_(webview, action, handler)
                return
            frame = action.targetFrame()
            if kind == "external" and (frame is None or frame.isMainFrame()):
                open_external(url)
            handler(cancel)

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, webview, config, action, features):
            # target=_blank 與 window.open:不開新視窗
            url = str(action.request().URL().absoluteString())
            if classify_url(url, port) == "external":
                open_external(url)
            return None

    cocoa.BrowserView.BrowserDelegate = H0LabBrowserDelegate


def run(url: str, port: int, on_quit: Callable[[], None], is_alive: Callable[[], bool], set_shutdown_hook: Callable) -> bool:
    """開視窗並跑事件迴圈,結束時呼叫 on_quit 後直接結束程序(不會回來)。開不了視窗回傳 False。"""
    global window
    try:
        import AppKit
        import Foundation
        import webview
        from webview.platforms import cocoa
    except Exception as e:  # noqa: BLE001  少了什麼都退回瀏覽器
        print(f"[H0 Lab] 無法載入原生視窗:{type(e).__name__}: {e}", flush=True)
        return False

    done = threading.Lock()

    def finish() -> None:
        if done.acquire(blocking=False):
            try:
                on_quit()
            finally:
                os._exit(0)     # 背景執行緒可能還在等網路,不等它們
        threading.Event().wait()    # 別的執行緒正在結束:不能回到 launcher 走退回瀏覽器的路

    pending_term = threading.Event()

    def close_window() -> None:
        threading.Timer(EXIT_GRACE_SECONDS, finish).start()     # 事件迴圈沒有照預期停下來也要能結束(更新在等)
        window.destroy()

    class H0LabBridge(AppKit.NSObject):
        def willTerminate_(self, _note):        # Dock「結束」與 Cmd+Q:AppKit 直接 exit,不會回到 webview.start 之後
            finish()

        def tick_(self, _timer):                # AppKit 事件迴圈不會讓 Python 處理訊號:定期回到 Python,也順便看伺服器還活著嗎
            if pending_term.is_set() or not is_alive():
                pending_term.clear()
                threading.Thread(target=close_window, daemon=True).start()

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True      # 匯出報告用
        _install_link_policy(cocoa, port)
        window = webview.create_window(WINDOW_TITLE, url, width=1360, height=880, min_size=(980, 640), text_select=True, zoomable=True,
                                       background_color=WINDOW_BG)
        bridge = H0LabBridge.alloc().init()
        Foundation.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            bridge, "willTerminate:", AppKit.NSApplicationWillTerminateNotification, None)
        timer = Foundation.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.5, bridge, "tick:", None, True)
        Foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, Foundation.NSRunLoopCommonModes)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: pending_term.set())
        set_shutdown_hook(pending_term.set)
        lang = (locale.getlocale()[0] or os.environ.get("LANG") or "").lower()
        try:
            langs = Foundation.NSLocale.preferredLanguages()
            lang = str(langs[0]).lower() if langs else lang
        except Exception:  # noqa: BLE001
            pass
        # private_mode=False:外觀、語言等 localStorage 設定才留得住
        webview.start(gui="cocoa", private_mode=False, debug=False, localization=_ZH_MENU if lang.startswith("zh") else {})
    except Exception as e:  # noqa: BLE001
        print(f"[H0 Lab] 無法開啟原生視窗:{type(e).__name__}: {e}", flush=True)
        set_shutdown_hook(None)
        return False
    finish()
    return True     # 到不了:finish 一定會結束程序
