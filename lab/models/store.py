"""我的模型存取:private/models/<id>.json(不進 git)。出廠模型唯讀,只能經由閘門 / holdout 改。規格 §1、§6。

刪除不做硬刪:封存是把檔案移到 private/models/_archive/(規則:刪檔一律可復原)。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import secrets
import tempfile
from pathlib import Path

from .. import config
from .schema import MODELS_DIR, ModelError, load, load_shipped, score_hash, validate

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def models_dir() -> Path:
    return config.PRIVATE_DIR / "models"


def shipped_ids() -> list[str]:
    return sorted(p.stem for p in MODELS_DIR.glob("*.json"))


def _summary(m: dict, source: str) -> dict:
    return {"id": m["id"], "name": m["name"], "family_id": m["family_id"], "based_on": m.get("based_on"),
            "source": source, "score_hash": score_hash(m), "n_metrics": len(m["metrics"]),
            "n_custom": sum(1 for x in m["metrics"] if "expr" in x["value"]), "created": m.get("created"),
            "updated": m.get("updated")}


def list_models() -> list[dict]:
    out = [_summary(load_shipped(i, readonly=True), "shipped") for i in shipped_ids()]
    d = models_dir()
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                out.append(_summary(load(p), "mine"))
            except (ModelError, ValueError, KeyError):
                out.append({"id": p.stem, "name": p.stem, "source": "mine", "error": "模型檔格式錯誤,無法載入"})
    return out


def get(model_id: str) -> dict:
    if model_id in shipped_ids():
        return load_shipped(model_id)
    if not ID_RE.fullmatch(model_id or ""):
        raise ModelError("模型代號只能用小寫英文、數字、連字號")
    p = models_dir() / f"{model_id}.json"
    if not p.is_file():
        raise ModelError(f"找不到模型 {model_id}")
    return load(p)


def _id_taken(mid: str) -> bool:
    d = models_dir()
    return mid in shipped_ids() or (d / f"{mid}.json").exists() or any((d / "_archive").glob(f"{mid}.*.json"))


def _prefix(prefix: str) -> str:
    p = re.sub(r"[^a-z0-9-]+", "-", (prefix or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", p)[:30].strip("-") or "model"


def new_id(prefix: str) -> str:
    """自動代號 <prefix>-<YYYYMMDD>-<6 碼亂數>:符合 ID_RE,不和現有、封存或出廠模型重複。
    呼叫端要在 _id_lock 裡呼叫並在同一個鎖裡寫檔,才不會兩個請求拿到同一個代號。"""
    base = f"{_prefix(prefix)}-{dt.date.today():%Y%m%d}"
    for _ in range(50):
        mid = f"{base}-{secrets.token_hex(3)}"
        if ID_RE.fullmatch(mid) and not _id_taken(mid):
            return mid
    raise ModelError("自動產生模型代號失敗,請稍後再試或在「進階」自訂代號")


class _id_lock:
    """跨程序鎖(private/models/.id.lock):檢查代號沒人用到寫好檔案之間,別人不能搶同一個代號。"""
    def __enter__(self):
        import fcntl
        d = models_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.f = open(d / ".id.lock", "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def save_new(model: dict, auto_prefix: str | None = None) -> dict:
    """新建我的模型:auto_prefix 有給就自動產生代號(忽略 model.id),否則用 model.id,但代號已經有人用(含封存)就丟
    ModelError,不覆寫。改既有模型請用 save。"""
    with _id_lock():
        if auto_prefix is not None:
            mid = new_id(auto_prefix)
        else:
            mid = (model or {}).get("id") or ""
            if mid not in shipped_ids() and ID_RE.fullmatch(mid) and _id_taken(mid):
                raise ModelError(f"模型代號 {mid} 已經有人用了(含已封存的模型),請換一個")
        return save({**(model or {}), "id": mid})


def save(model: dict, _origin: dict | None = None) -> dict:
    """存我的模型(驗證失敗丟 ModelError)。不能覆寫出廠模型。
    family_id、based_on、created 建立後鎖定:已存在的模型沿用磁碟上的值;新模型 family_id = id、based_on 只有
    fork 能給(_origin)。傳進來的值一律忽略,免得改模型族把「試了幾次」或「已看過 holdout」洗掉(M5 Verifier)。"""
    m = copy.deepcopy(model)
    mid = m.get("id") or ""
    if mid in shipped_ids():   # 先於格式檢查:出廠代號(jw-mf-2.0 有句點)要回「不能覆寫」,不是「格式不正確」
        raise ModelError("不能覆寫出廠模型;請另存成新的模型代號")
    if not ID_RE.fullmatch(mid):
        raise ModelError("模型代號只能用小寫英文、數字、連字號(最多 48 字)")
    today = dt.date.today().isoformat()
    old = models_dir() / f"{mid}.json"
    if old.is_file():
        try:
            prev = json.loads(old.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prev = {}
        lock = {"family_id": prev.get("family_id") or mid, "based_on": prev.get("based_on"),
                "created": prev.get("created") or today}
    else:
        lock = {"family_id": mid, "based_on": None, "created": today} | (_origin or {})
    m.update(lock)
    m["updated"] = today
    validate(m)
    d = models_dir()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, d / f"{mid}.json")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)   # 只清掉自己剛建的暫存檔
        raise
    return _summary(m, "mine")


def fork(base_id: str, new_id: str, name: str) -> dict:
    """從某個模型複製出新的模型族(試了幾次從頭算)。"""
    base = get(base_id)
    if (models_dir() / f"{new_id}.json").is_file():
        raise ModelError(f"模型代號 {new_id} 已經存在")
    m = copy.deepcopy(base)
    m.update({"id": new_id, "name": name, "notes": ""})
    out = save(m, {"family_id": new_id, "based_on": f"{base['id']}@{score_hash(base)}"})
    if base["id"] not in shipped_ids():   # 族譜(只增不改):複製、封存都不能讓「已看過 holdout」消失
        _append_lineage({"time": dt.datetime.now().isoformat(timespec="seconds"), "child": new_id,
                         "child_family": new_id, "parent": base["id"], "parent_family": base.get("family_id") or base["id"]})
    return out


def lineage_path() -> Path:
    return models_dir() / "lineage.jsonl"


def _append_lineage(rec: dict) -> None:
    import fcntl
    p = lineage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def related_families(family: str) -> set[str]:
    """族譜裡和 family 連在一起的所有模型族(往上、往下、兄弟都算;封存不影響,紀錄只增不改)。"""
    edges: dict[str, set] = {}
    p = lineage_path()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            a, b = r.get("child_family"), r.get("parent_family")
            if a and b:
                edges.setdefault(a, set()).add(b)
                edges.setdefault(b, set()).add(a)
    seen, todo = {family}, [family]
    while todo:
        for n in edges.get(todo.pop(), ()):
            if n not in seen:
                seen.add(n)
                todo.append(n)
    return seen


def archive(model_id: str) -> None:
    if model_id in shipped_ids():
        raise ModelError("出廠模型不能封存")
    p = models_dir() / f"{model_id}.json"
    if not ID_RE.fullmatch(model_id or "") or not p.is_file():
        raise ModelError(f"找不到模型 {model_id}")
    a = models_dir() / "_archive"
    a.mkdir(exist_ok=True)
    os.replace(p, a / f"{model_id}.{dt.datetime.now():%Y%m%d%H%M%S}.json")
