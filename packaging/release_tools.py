"""發版工具:產生金鑰、manifest、簽章、checksums、第三方授權清單。

金鑰與簽章用 cryptography(只在打包用的 venv,不進 App);App 只內建驗證(lab/ed25519.py)。

    python packaging/release_tools.py keygen --out ~/h0-lab-update-key.txt
    python packaging/release_tools.py manifest --version 0.2.0 --asset arm64=dist/H0-Lab-macOS-update.zip --out dist/update-manifest.json
    H0_UPDATE_SIGNING_KEY=... python packaging/release_tools.py sign dist/update-manifest.json
    python packaging/release_tools.py checksums dist
    python packaging/release_tools.py notices --out-dir dist
    python packaging/release_tools.py check-app "dist/H0 Lab.app" --version 0.2.0
    python packaging/release_tools.py check-portable "dist/H0 Lab.app"
私鑰絕不進 repo、.env.example、log;這支程式不印私鑰。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lab import ed25519  # noqa: E402
from lab.version import DATA_FORMAT_VERSION, __version__  # noqa: E402

PUBLIC_KEY_FILE = ROOT / "packaging" / "update_public_key.txt"
MANIFEST_NAME = "update-manifest.json"
PRODUCT = "h0-lab"
# 只列打包工具,不算進 App 的第三方清單
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "macholib", "cryptography", "pip", "setuptools", "packaging",
              "pytest", "ruff", "iniconfig", "pluggy", "pygments",
              "dmgbuild", "ds_store", "ds-store", "mac_alias", "mac-alias"}
# Mach-O 執行檔開頭的 4 個位元組:64 位元(兩種位元組順序)與 universal
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
ALLOWED_REF_PREFIXES = ("@", "/usr/lib/", "/System/")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def cmd_keygen(a) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    out = Path(a.out).expanduser().resolve()
    if out.is_relative_to(ROOT):
        raise SystemExit("私鑰不能放在 repo 裡,請換一個位置")
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(base64.b64encode(seed).decode() + "\n")
    print(f"私鑰已寫到 {out}(權限 0600)。把內容放進 GitHub secret H0_UPDATE_SIGNING_KEY,另外離線備份。")
    print("公鑰(貼進 packaging/update_public_key.txt):")
    print(base64.b64encode(pub).decode())
    return 0


def build_manifest(version: str, assets: dict[str, Path], data_format: int = DATA_FORMAT_VERSION) -> dict:
    return {"schema": 1, "product": PRODUCT, "version": version, "data_format": data_format,
            "assets": {arch: {"name": p.name, "size": p.stat().st_size, "sha256": sha256_file(p)} for arch, p in sorted(assets.items())}}


def cmd_manifest(a) -> int:
    assets = {}
    for item in a.asset:
        arch, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"--asset 格式是 arch=檔案:{item}")
        assets[arch] = Path(path)
    if a.version != __version__:
        raise SystemExit(f"版本 {a.version} 和 lab/version.py 的 {__version__} 不一致")
    Path(a.out).write_text(json.dumps(build_manifest(a.version, assets), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def _load_seed() -> bytes:
    raw = os.environ.get("H0_UPDATE_SIGNING_KEY", "").strip()
    if not raw:
        raise SystemExit("環境變數 H0_UPDATE_SIGNING_KEY 沒有設定")
    seed = base64.b64decode(raw, validate=True)
    if len(seed) != 32:
        raise SystemExit("H0_UPDATE_SIGNING_KEY 格式不對(要 32 位元組的 base64)")
    return seed


def cmd_sign(a) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(_load_seed())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    expected = PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip()
    if expected and base64.b64encode(pub).decode() != expected:
        raise SystemExit("私鑰和 packaging/update_public_key.txt 的公鑰不是一對:用這把簽的更新,使用者的 App 會拒絕")
    msg = Path(a.file).read_bytes()
    sig = key.sign(msg)
    if not ed25519.verify(pub, msg, sig):   # App 內建的驗證也要過
        raise SystemExit("簽章自我驗證失敗")
    Path(str(a.file) + ".sig").write_text(base64.b64encode(sig).decode() + "\n", encoding="utf-8")
    return 0


def cmd_checksums(a) -> int:
    d = Path(a.dir)
    lines = [f"{sha256_file(p)}  {p.name}" for p in sorted(d.iterdir()) if p.is_file() and p.name != "SHA256SUMS.txt"]
    (d / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def _licenses() -> list[dict]:
    from importlib import metadata
    rows = []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name") or ""
        if not name or name.lower() in BUILD_ONLY:
            continue
        lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if not lic or len(lic) > 80:
            classifiers = [c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or [] if c.startswith("License ::")]
            lic = ", ".join(classifiers) or (lic.splitlines()[0][:80] if lic else "見套件內 LICENSE")
        home = dist.metadata.get("Home-page") or ""
        for u in dist.metadata.get_all("Project-URL") or []:
            if not home and "," in u:
                home = u.split(",", 1)[1].strip()
        rows.append({"name": name, "version": dist.version, "license": lic, "url": home})
    return sorted(rows, key=lambda r: r["name"].lower())


def cmd_notices(a) -> int:
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = _licenses()
    text = ["H0 Lab 使用的第三方套件 / Third-party packages used by H0 Lab", "另含 Python 直譯器(PSF License)與本地 ECharts(Apache-2.0)。", ""]
    text += [f"{r['name']} {r['version']} | {r['license']} | {r['url']}" for r in rows]
    (d / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    return 0


def cmd_check_app(a) -> int:
    """發版前最後檢查:不是測試版、有公鑰、版本一致。"""
    app = Path(a.app)
    info = json.loads(next(app.joinpath("Contents").rglob("_build_info.json")).read_text(encoding="utf-8"))
    problems = []
    if info.get("test_build"):
        problems.append("這是測試版(test_build)")
    if "update_api_base" in info:
        problems.append("內含測試用的更新伺服器")
    if not info.get("update_public_key") or info["update_public_key"] != PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip():
        problems.append("公鑰和 packaging/update_public_key.txt 不一致")
    if info.get("version") != a.version:
        problems.append(f"版本是 {info.get('version')},應該是 {a.version}")
    if problems:
        raise SystemExit("不能發布:" + ";".join(problems))
    print("check-app 通過")
    return 0


def is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGIC
    except OSError:
        return False


def parse_minos(vtool_output: str) -> tuple[int, ...] | None:
    """vtool -show-build 的輸出裡最高的 minos;舊格式的執行檔沒有這一行,回傳 None。"""
    found = [tuple(int(x) for x in m.split(".")) for m in re.findall(r"^\s*minos\s+([\d.]+)\s*$", vtool_output, re.M)]
    return max(found) if found else None


def own_ids(otool_d_output: str) -> set[str]:
    """otool -D 列出的函式庫自己的名字;包含多種架構的檔案每個架構各有一個。"""
    return {ln.strip() for ln in otool_d_output.splitlines() if ln.strip() and not ln.rstrip().endswith(":")}


def outside_refs(otool_output: str, own: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """otool -L 的輸出裡不在白名單的函式庫:只有 App 內部(@)和系統內建(/usr/lib、/System)是每台 Mac 都有的。"""
    refs = [ln.strip().split(" (")[0] for ln in otool_output.splitlines() if ln.strip() and not ln.rstrip().endswith(":")]
    return [r for r in refs if r not in own and not r.startswith(ALLOWED_REF_PREFIXES)]


def outside_rpaths(otool_load_commands: str) -> list[str]:
    """otool -l 裡指向 App 外面的 LC_RPATH:@rpath 的函式庫可能因此從使用者的電腦以外找不到。"""
    found = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset \d+\)", otool_load_commands)
    return [p for p in found if not p.startswith("@")]


def _tool(*cmd: str) -> str:
    """工具失敗就中止:輸出是空的不代表沒問題。"""
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"{cmd[0]} 失敗(結束碼 {r.returncode}):{cmd[-1]}")
    return r.stdout


def cmd_check_portable(a) -> int:
    """打包進來的函式庫不能要求比 Info.plist 宣告更新的 macOS,也不能依賴 App 外面的 Homebrew、Python。

    build 機器換成新版 macOS 時,這項檢查確認使用者端的最低版本沒有被默默拉高。"""
    app = Path(a.app)
    with open(app / "Contents" / "Info.plist", "rb") as f:
        declared = tuple(int(x) for x in plistlib.load(f)["LSMinimumSystemVersion"].split("."))
    too_new, outside, n = [], [], 0
    for p in sorted(x for x in app.rglob("*") if x.is_file() and not x.is_symlink() and is_macho(x)):
        n += 1
        rel = p.relative_to(app)
        minos = parse_minos(_tool("/usr/bin/vtool", "-show-build", str(p)))
        if minos and minos > declared:
            too_new.append(f"{rel}(要 macOS {'.'.join(map(str, minos))})")
        own = own_ids(_tool("/usr/bin/otool", "-D", str(p)))
        outside += [f"{rel} → {r}" for r in outside_refs(_tool("/usr/bin/otool", "-L", str(p)), own)]
        outside += [f"{rel} → rpath {r}" for r in outside_rpaths(_tool("/usr/bin/otool", "-l", str(p)))]
    if not n:
        raise SystemExit("App 裡找不到任何執行檔")
    if too_new or outside:
        raise SystemExit("不能發布:\n" + "\n".join(too_new + outside))
    print(f"check-portable 通過:{n} 個執行檔,最低 macOS {'.'.join(map(str, declared))},沒有依賴 App 外面的函式庫")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H0 Lab 發版工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("keygen")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_keygen)
    p = sub.add_parser("manifest")
    p.add_argument("--version", required=True)
    p.add_argument("--asset", action="append", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_manifest)
    p = sub.add_parser("sign")
    p.add_argument("file")
    p.set_defaults(fn=cmd_sign)
    p = sub.add_parser("checksums")
    p.add_argument("dir")
    p.set_defaults(fn=cmd_checksums)
    p = sub.add_parser("notices")
    p.add_argument("--out-dir", required=True)
    p.set_defaults(fn=cmd_notices)
    p = sub.add_parser("check-app")
    p.add_argument("app")
    p.add_argument("--version", required=True)
    p.set_defaults(fn=cmd_check_app)
    p = sub.add_parser("check-portable")
    p.add_argument("app")
    p.set_defaults(fn=cmd_check_portable)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
