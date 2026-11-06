# PyInstaller 設定(由 packaging/build_app.py 呼叫;環境變數 H0_BUILD_INFO、H0_APP_VERSION、H0_ICON、H0_NOTICES 由它提供)
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
version = os.environ["H0_APP_VERSION"]

datas = [(str(ROOT / "web"), "web"), (str(ROOT / "LICENSE"), "."),
         (str(ROOT / "lab" / "model_params.json"), "lab"), (os.environ["H0_BUILD_INFO"], "lab")]
datas += [(str(p), str(Path("lab") / p.parent.relative_to(ROOT / "lab"))) for p in (ROOT / "lab" / "models").rglob("*.json")]
if os.environ.get("H0_NOTICES"):
    datas.append((os.environ["H0_NOTICES"], "."))
datas += collect_data_files("certifi") + collect_data_files("webview", includes=["js/**", "*.js"])
binaries = []
hiddenimports = collect_submodules("lab") + collect_submodules("app") + ["mac_shell", "mac_window", "webview.platforms.cocoa"]
hiddenimports += collect_submodules("uvicorn")
for pkg in ("curl_cffi",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "packaging")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "PIL", "pytest", "uvloop", "httptools", "watchfiles", "cryptography",
              "PyInstaller", "setuptools", "pip", "numpy.tests", "pandas.tests", "scipy.tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="H0 Lab", console=False, upx=False, strip=False,
          argv_emulation=False, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, strip=False, name="H0 Lab")
app = BUNDLE(
    coll,
    name="H0 Lab.app",
    icon=os.environ.get("H0_ICON") or None,
    bundle_identifier="io.github.jwcod.h0lab",
    version=version,
    info_plist={
        "CFBundleName": "H0 Lab",
        "CFBundleDisplayName": "H0 Lab",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "14.0",   # 打包進來的 scipy 等函式庫最低要 macOS 14(otool 掃過)
        "LSApplicationCategoryType": "public.app-category.finance",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "MIT License. Research use only; not investment advice.",
    },
)
