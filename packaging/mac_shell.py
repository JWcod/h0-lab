"""開不了原生視窗時的備案(mac_window.py 失敗才用):Dock 圖示、選單、Dock「結束」時乾淨關閉、再次點圖示就開網頁。

只用 ctypes 呼叫 Objective-C runtime:pyobjc 載入失敗時這裡也要能動。介面在瀏覽器裡。
"""
from __future__ import annotations

import ctypes
import locale
import os
import signal
from ctypes import CFUNCTYPE, c_bool, c_char_p, c_double, c_long, c_size_t, c_ulong, c_void_p
from typing import Callable

_objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
_objc.objc_getClass.restype = c_void_p
_objc.objc_getClass.argtypes = [c_char_p]
_objc.sel_registerName.restype = c_void_p
_objc.sel_registerName.argtypes = [c_char_p]
_objc.objc_allocateClassPair.restype = c_void_p
_objc.objc_allocateClassPair.argtypes = [c_void_p, c_char_p, c_size_t]
_objc.class_addMethod.restype = c_bool
_objc.class_addMethod.argtypes = [c_void_p, c_void_p, c_void_p, c_char_p]
_objc.objc_registerClassPair.argtypes = [c_void_p]
_MSG = ctypes.cast(_objc.objc_msgSend, c_void_p).value

NS_TERMINATE_NOW = 1
_ACTIVATION_REGULAR = 0
_KEEP: list = []     # 回呼不能被回收


def _cls(name: str):
    return _objc.objc_getClass(name.encode())


def _sel(name: str):
    return _objc.sel_registerName(name.encode())


def _send(obj, sel: str, *args, restype=c_void_p, argtypes=()):
    return CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(_MSG)(obj, _sel(sel), *args)


def _nsstr(s: str):
    return _send(_cls("NSString"), "stringWithUTF8String:", s.encode("utf-8"), argtypes=(c_char_p,))


def _is_chinese() -> bool:
    try:
        langs = _send(_cls("NSLocale"), "preferredLanguages")
        first = _send(langs, "firstObject")
        return ctypes.string_at(_send(first, "UTF8String")).decode().startswith("zh") if first else False
    except Exception:  # noqa: BLE001
        return (locale.getlocale()[0] or "").startswith("zh")


class MacShell:
    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None], is_alive: Callable[[], bool]):
        self.on_open, self.on_quit, self.is_alive = on_open, on_quit, is_alive
        self._quit_done = False
        self._pending_term = False
        zh = _is_chinese()
        self.t_open = "打開 H0 Lab 網頁" if zh else "Open H0 Lab"
        self.t_quit = "結束 H0 Lab" if zh else "Quit H0 Lab"
        self._app = _send(_cls("NSApplication"), "sharedApplication")
        _send(self._app, "setActivationPolicy:", _ACTIVATION_REGULAR, restype=c_bool, argtypes=(c_long,))
        self._delegate = self._make_delegate()
        _send(self._app, "setDelegate:", self._delegate, restype=None, argtypes=(c_void_p,))
        _send(self._app, "setMainMenu:", self._make_menu(), restype=None, argtypes=(c_void_p,))
        self._dock_menu = _send(self._new_menu(self.t_open, [(self.t_open, "openWeb:", "", self._delegate)]), "retain")

    def _method(self, cls, name: str, enc: bytes, proto, fn) -> None:
        imp = proto(fn)
        _KEEP.append(imp)
        _objc.class_addMethod(cls, _sel(name), ctypes.cast(imp, c_void_p), enc)

    def _make_delegate(self):
        cls = _objc.objc_allocateClassPair(_cls("NSObject"), b"H0LabAppDelegate", 0)
        if not cls:
            raise RuntimeError("objc_allocateClassPair 失敗")
        void3 = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
        self._method(cls, "openWeb:", b"v@:@", void3, lambda *_: self._safe(self.on_open))
        self._method(cls, "tick:", b"v@:@", void3, lambda *_: self._tick())
        self._method(cls, "applicationDidFinishLaunching:", b"v@:@", void3, lambda *_: self._activate())
        self._method(cls, "applicationShouldHandleReopen:hasVisibleWindows:", b"c@:@c",
                     CFUNCTYPE(c_bool, c_void_p, c_void_p, c_void_p, c_bool), lambda *_: (self._safe(self.on_open), True)[1])
        self._method(cls, "applicationShouldTerminate:", b"Q@:@", CFUNCTYPE(c_ulong, c_void_p, c_void_p, c_void_p),
                     lambda *_: self._should_terminate())
        self._method(cls, "applicationWillTerminate:", b"v@:@", void3, lambda *_: os._exit(0))
        self._method(cls, "applicationDockMenu:", b"@@:@", CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p),
                     lambda *_: self._dock_menu)
        _objc.objc_registerClassPair(cls)
        return _send(_send(cls, "alloc"), "init")

    def _new_menu(self, title: str, items: list[tuple]):
        menu = _send(_send(_send(_cls("NSMenu"), "alloc"), "initWithTitle:", _nsstr(title), argtypes=(c_void_p,)), "autorelease")
        for label, action, key, target in items:
            it = _send(_send(_cls("NSMenuItem"), "alloc"), "initWithTitle:action:keyEquivalent:", _nsstr(label), _sel(action),
                       _nsstr(key), argtypes=(c_void_p, c_void_p, c_void_p))
            if target is not None:
                _send(it, "setTarget:", target, restype=None, argtypes=(c_void_p,))
            _send(menu, "addItem:", it, restype=None, argtypes=(c_void_p,))
        return menu

    def _make_menu(self):
        main = self._new_menu("H0 Lab", [("H0 Lab", "noop:", "", None)])
        sub = self._new_menu("H0 Lab", [(self.t_open, "openWeb:", "o", self._delegate), (self.t_quit, "terminate:", "q", None)])
        root_item = _send(main, "itemAtIndex:", 0, argtypes=(c_long,))
        _send(main, "setSubmenu:forItem:", sub, root_item, restype=None, argtypes=(c_void_p, c_void_p))
        return main

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001  例外不能穿過 ctypes 回呼
            print(f"[H0 Lab] {type(e).__name__}: {e}", flush=True)

    def _activate(self) -> None:
        # AppKit 的事件迴圈不會讓 Python 處理訊號:定期回到 Python,SIGTERM 才能乾淨結束,也順便看伺服器還活著嗎
        _send(_cls("NSTimer"), "scheduledTimerWithTimeInterval:target:selector:userInfo:repeats:", 0.5, self._delegate, _sel("tick:"),
              None, True, argtypes=(c_double, c_void_p, c_void_p, c_void_p, c_bool))

    def _tick(self) -> None:
        if self._pending_term or not self.is_alive():
            self._pending_term = False
            _send(self._app, "terminate:", None, restype=None, argtypes=(c_void_p,))

    def _should_terminate(self) -> int:
        if not self._quit_done:
            self._quit_done = True
            self._safe(self.on_quit)
        return NS_TERMINATE_NOW

    def terminate(self) -> None:
        """可以從任何執行緒呼叫:排到主執行緒結束 App。"""
        _send(self._app, "performSelectorOnMainThread:withObject:waitUntilDone:", _sel("terminate:"), None, False,
              restype=None, argtypes=(c_void_p, c_void_p, c_bool))

    def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._on_signal)
        _send(self._app, "run", restype=None)

    def _on_signal(self, *_a) -> None:
        self._pending_term = True
