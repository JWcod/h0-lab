"""DMG 打開時的視窗版面:背景圖、視窗大小與圖示位置要對得起來(實際做 DMG 要 dmgbuild,只在打包 venv)。"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import build_app  # noqa: E402

DMG = ROOT / "packaging" / "dmg"


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_background_matches_window_at_1x_and_2x():
    w, h = build_app.DMG_WINDOW
    assert build_app.DMG_BACKGROUND == DMG / "background.png"
    assert _png_size(DMG / "background.png") == (w, h)
    assert _png_size(DMG / "background@2x.png") == (w * 2, h * 2)   # Retina;dmgbuild 會合成 TIFF
    svg = (DMG / "background.svg").read_text(encoding="utf-8")
    assert f'viewBox="0 0 {w} {h}"' in svg


def test_icons_sit_on_the_glass_plates_in_the_background():
    svg = (DMG / "background.svg").read_text(encoding="utf-8")
    plates = [(int(x) + int(s) // 2, int(y) + int(s) // 2)
              for x, y, s in re.findall(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="\3"', svg)]
    assert sorted(plates) == sorted(build_app.DMG_ICONS.values())
    assert build_app.DMG_ICONS[build_app.APP][0] < build_app.DMG_ICONS["Applications"][0]   # 箭頭由左往右
    w, h = build_app.DMG_WINDOW
    assert all(0 < x < w and 0 < y < h for x, y in build_app.DMG_ICONS.values())


def test_dmg_settings(tmp_path):
    app = tmp_path / build_app.APP
    (app / "Contents" / "Resources").mkdir(parents=True)
    (app / "Contents" / "Resources" / "H0Lab.icns").write_bytes(b"icns")
    s = build_app.dmg_settings(app)
    assert s["files"] == [str(app)] and s["symlinks"] == {"Applications": "/Applications"}
    assert s["window_rect"][1] == build_app.DMG_WINDOW and s["icon_locations"] == build_app.DMG_ICONS
    assert s["background"] == str(build_app.DMG_BACKGROUND) and s["default_view"] == "icon-view"
    assert not any(s[k] for k in ("show_toolbar", "show_sidebar", "show_pathbar", "show_status_bar", "show_tab_view"))
    assert s["icon"].endswith("H0Lab.icns") and s["format"] == "UDZO"


def test_dmgbuild_is_pinned_build_only():
    req = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    for pkg in ("dmgbuild", "ds-store", "mac-alias"):
        assert re.search(rf"^{pkg}==\d", req, re.M), pkg
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").lower()
    assert "dmgbuild" not in lock   # 不進 App 與原始碼安裝
