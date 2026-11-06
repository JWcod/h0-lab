"""打包 H0 Lab.app,產生更新用 zip 與給人下載的 dmg。

用獨立 venv 跑(不要裝進專案 .venv):
    python -m venv <暫存>/bvenv
    <暫存>/bvenv/bin/pip install -r requirements.lock.txt -r packaging/requirements-build.txt
    <暫存>/bvenv/bin/python packaging/build_app.py --out <暫存>/dist

--require-key 用在正式發版:packaging/update_public_key.txt 沒填就失敗。
--test-key / --test-api-base 只給本機測試(只會產生標記 test_build 的版本,發版檢查會拒絕)。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lab.version import DATA_FORMAT_VERSION, __version__  # noqa: E402

KEY_FILE = ROOT / "packaging" / "update_public_key.txt"
APP = "H0 Lab.app"
# 檔名固定:README 的下載鈕用 releases/latest/download/<檔名> 直連
DMG_NAME = "H0-Lab-macOS.dmg"
UPDATE_ZIP_NAME = "H0-Lab-macOS-update.zip"


def run(*cmd, **kw) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def make_icon(out: Path, work: Path) -> Path | None:
    from make_icon import write_png   # packaging/make_icon.py
    iconset = work / "H0Lab.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    master = work / "icon.png"
    write_png(master, 1024)
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            run("/usr/bin/sips", "-z", size * scale, size * scale, master, "--out", iconset / f"icon_{size}x{size}{suffix}.png",
                stdout=subprocess.DEVNULL)
    run("/usr/bin/iconutil", "-c", "icns", iconset, "-o", out)
    return out


def package(app: Path, out: Path) -> tuple[Path, Path]:
    zip_path, dmg_path = out / UPDATE_ZIP_NAME, out / DMG_NAME
    zip_path.unlink(missing_ok=True)
    dmg_path.unlink(missing_ok=True)
    # ditto 保留 symlink 與權限;不放資源分支與 __MACOSX
    run("/usr/bin/ditto", "-c", "-k", "--norsrc", "--keepParent", app, zip_path)
    with tempfile.TemporaryDirectory(prefix="h0dmg-") as d:
        staging = Path(d) / "H0 Lab"
        staging.mkdir()
        run("/usr/bin/ditto", app, staging / APP)
        os.symlink("/Applications", staging / "Applications")
        run("/usr/bin/hdiutil", "create", "-volname", "H0 Lab", "-srcfolder", staging, "-ov", "-format", "UDZO", dmg_path,
            stdout=subprocess.DEVNULL)
    return zip_path, dmg_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包 H0 Lab.app")
    ap.add_argument("--out", required=True, type=Path, help="輸出資料夾(不要放在 repo 裡)")
    ap.add_argument("--require-key", action="store_true", help="正式發版:一定要有更新公鑰")
    ap.add_argument("--test-key", help="測試版公鑰(base64),只給本機測試")
    ap.add_argument("--test-api-base", help="測試版的假 release 伺服器,例如 http://127.0.0.1:18999")
    ap.add_argument("--notices", type=Path, help="第三方授權清單,放進 App")
    ap.add_argument("--identity", default="-", help="程式碼簽章身分;預設 - 是 ad-hoc(沒有 Apple 開發者帳號時用)")
    a = ap.parse_args(argv)

    if a.out.resolve().is_relative_to(ROOT):
        raise SystemExit("--out 不要放在 repo 裡")
    if sys.platform != "darwin":
        raise SystemExit("只能在 macOS 打包")
    key = a.test_key or KEY_FILE.read_text(encoding="utf-8").strip()
    if a.require_key and (a.test_key or a.test_api_base or not key):
        raise SystemExit("正式發版要填 packaging/update_public_key.txt,而且不能帶測試參數(見 packaging/README.md)")
    if not key:
        print("[注意] 沒有更新公鑰:這一包不能一鍵更新(只會顯示新版資訊)", flush=True)

    arch = platform.machine()
    a.out.mkdir(parents=True, exist_ok=True)
    work = a.out / "_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    info = {"version": __version__, "commit": git_commit(), "arch": arch, "update_public_key": key,
            "data_format": DATA_FORMAT_VERSION, "test_build": bool(a.test_key or a.test_api_base)}
    if a.test_api_base:
        info["update_api_base"] = a.test_api_base
    info_path = work / "_build_info.json"
    info_path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "packaging"))
    icon = make_icon(work / "H0Lab.icns", work)
    env = dict(os.environ, H0_APP_VERSION=__version__, H0_BUILD_INFO=str(info_path), H0_ICON=str(icon or ""),
               H0_NOTICES=str(a.notices.resolve()) if a.notices else "", PYTHONDONTWRITEBYTECODE="1")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN", "--distpath", a.out / "dist",
        "--workpath", work / "pyi", ROOT / "packaging" / "h0lab.spec", env=env, cwd=str(work))
    app = a.out / "dist" / APP
    # PyInstaller 簽的是個別檔案;整包重簽一次,才能通過 codesign --verify --deep --strict
    sign = ["/usr/bin/codesign", "--force", "--deep", "--sign", a.identity]
    if a.identity != "-":
        sign += ["--options", "runtime", "--timestamp", "--entitlements", ROOT / "packaging" / "entitlements.plist"]
    run(*sign, app)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
    zip_path, dmg_path = package(app, a.out)
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file() and not p.is_symlink())
    print(json.dumps({"app": str(app), "app_mb": round(size / 1024**2), "zip": str(zip_path),
                      "zip_mb": round(zip_path.stat().st_size / 1024**2), "dmg": str(dmg_path),
                      "dmg_mb": round(dmg_path.stat().st_size / 1024**2), "version": __version__, "arch": arch,
                      "test_build": info["test_build"]}, ensure_ascii=False), flush=True)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
