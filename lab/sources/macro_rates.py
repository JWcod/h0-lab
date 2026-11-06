"""P2-H01 的歷史折現率資料:台灣 10 年期公債年平均(央行年報)、Damodaran 歷年 ERP。

原始檔(Fetcher 下載一次,放在 DATA_DIR/cache):
- `cbc_annual/<年>.pdf`:中央銀行年報(`cbc.gov.tw/Public/Data/OpenData/經研處/Annual Report_CH_<年>.pdf`,開放資料)。
  數字來自內文「10 年期指標公債殖利率平均為 X%」與附表「10 年期政府公債次級市場利率(平均)」,解析結果另存
  `macro_rates/tw10y_annual.json`(由 `build()` 產生,附來源與抽查值;回測只讀 json,不在回測時解析 PDF)。
- `damodaran/ctryprem<YY>.xls(x)`:Damodaran 國家風險溢酬年度更新;`damodaran/histimpl.xls`:美國歷年隱含 ERP。

可用日(PIT,`docs/預先登記.md` P2-H01;2026-10-16 專案同意台灣利率用「前一年平均」):
- 台灣 10 年期:年度 Y 的平均在 Y+1 年 1 月 1 日起可用(年底時公開市場資料已足以算出;央行年報較晚出版只是彙整)。
- Damodaran:檔案自己寫的更新日起可用(`build()` 讀檔內的「Updated」日期)。
只用標準函式庫讀 .xlsx;.xls 需要 xlrd(使用者自行安裝),沒有就回報缺漏、不猜。
"""
from __future__ import annotations

import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from .. import config

RAW = config.CACHE_DIR
OUT = config.CACHE_DIR / "macro_rates"
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# ---------------- .xlsx(標準函式庫) ----------------

def xlsx_rows(path: Path, sheet: str) -> dict[int, dict[str, object]]:
    """回傳 {列號: {欄字母: 值}};數字轉 float,字串保留原樣。"""
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sh = [s for s in wb.find(_M + "sheets") if s.get("name") == sheet]
    if not sh:
        raise KeyError(f"{path.name} 沒有工作表「{sheet}」")
    tgt = rels[sh[0].get(_R + "id")].lstrip("/")
    tgt = tgt if tgt.startswith("xl/") else "xl/" + tgt
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ["".join(t.text or "" for t in si.iter(_M + "t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_M + "si")]
    rows: dict[int, dict[str, object]] = {}
    for c in ET.fromstring(z.read(tgt)).iter(_M + "c"):
        col, row = re.match(r"([A-Z]+)(\d+)", c.get("r")).groups()
        v = c.find(_M + "v")
        if v is None:
            node = c.find(_M + "is")
            val = "".join(t.text or "" for t in node.iter(_M + "t")) if node is not None else None
        elif c.get("t") == "s":
            val = ss[int(v.text)]
        elif c.get("t") in ("str", "inlineStr", "e"):
            val = v.text
        else:
            try:
                val = float(v.text)
            except (TypeError, ValueError):
                val = v.text
        rows.setdefault(int(row), {})[col] = val
    return rows


def _xls_rows(path: Path, sheet: str) -> dict[int, dict[str, object]]:
    """.xls 需要 xlrd(使用者自行安裝);沒有就報錯,由呼叫端記為缺漏。"""
    import xlrd  # noqa: PLC0415

    sh = xlrd.open_workbook(str(path)).sheet_by_name(sheet)
    out: dict[int, dict[str, object]] = {}
    for r in range(sh.nrows):
        for ci in range(sh.ncols):
            v = sh.cell_value(r, ci)
            if v not in ("", None):
                col = ""
                n = ci + 1
                while n:
                    n, rem = divmod(n - 1, 26)
                    col = chr(65 + rem) + col
                out.setdefault(r + 1, {})[col] = v
    return out


def sheet_rows(path: Path, sheet: str) -> dict[int, dict[str, object]]:
    return xlsx_rows(path, sheet) if path.suffix == ".xlsx" else _xls_rows(path, sheet)


# ---------------- 台灣 10 年期公債年平均(央行年報) ----------------

def _pdf_text(path: Path, layout: bool = False) -> str:
    import subprocess  # noqa: PLC0415  只在建置時用(系統的 pdftotext);回測只讀 json

    args = ["pdftotext"] + (["-layout"] if layout else []) + [str(path), "-"]
    return subprocess.run(args, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def tw10y_from_table(path: Path) -> dict[int, float]:
    """年報附表「國內重要經濟金融指標(續)貨幣金融」:每列「民國年 存款 年增率 放款 年增率 退票率 逾放比 重貼現率
    拆款利率 商業本票利率 10 年期公債利率 …」,取第 10 個數字(10 年期政府公債次級市場利率,平均)。"""
    lines = _pdf_text(path, layout=True).splitlines()
    start = next((i for i, ln in enumerate(lines) if "五大銀行利率" in ln), None)
    if start is None:   # 較新的年報(例 2024)沒有這張附表
        return {}
    out: dict[int, float] = {}
    for ln in lines[start:start + 80]:
        m = re.match(r"^\s*(1\d\d|9\d)\s+(.*)$", ln)
        if not m:
            continue
        nums = re.findall(r"-?\d[\d,]*(?:\.\d+)?", m.group(2))   # 「431,958」是一個數字
        if len(nums) >= 10:
            out[int(m.group(1)) + 1911] = float(nums[9].replace(",", ""))
    return out


def tw10y_from_text(path: Path) -> dict[int, float]:
    """內文「(民國年)…10 年期指標公債殖利率…平均(升 / 降 / 略降)為 X%,較上年(的 Y%)…」:回傳 {西元年: X}(三位小數)。
    民國年取這句前面最近的「NNN 年」;找不到就不回傳。只拿來和附表核對。"""
    t = re.sub(r"\s+", "", _pdf_text(path))
    out: dict[int, float] = {}
    for m in re.finditer(r"10年期指標公債殖利率(?:\d{3}年)?平均(?:略?[升降])?為(\d\.\d+)%", t):
        yrs = re.findall(r"(1\d\d)年", t[max(0, m.start() - 40):m.end()])
        if yrs:
            y = int(yrs[-1]) + 1911
            out.setdefault(y, float(m.group(1)))
    # 「平均(升 / 降)為 X%,較上年的 Y%」:Y 是前一年(內文雙欄排版,年份要從同一句取)
    for m in re.finditer(r"(1\d\d)年[^。]{0,30}?10年期指標公債殖利率平均(?:略?[升降])?為(\d\.\d+)%[,\uff0c]較上年的(\d\.\d+)%", t):
        y = int(m.group(1)) + 1911
        out.setdefault(y, float(m.group(2)))
        out.setdefault(y - 1, float(m.group(3)))
    return out


# ---------------- Damodaran ----------------

_UPD = re.compile(r"(January|July)\s+(\d{1,2})?,?\s*(20\d\d)")


def damodaran_file(yy: int) -> dict:
    """ctryprem<YY>:檔內 E3 = 成熟市場(美國)隱含 ERP,F3 = 更新日說明;「ERPs by country」A 欄 = Taiwan 的 E 欄 = 台灣總 ERP。
    檔名 YY 是 20YY 年底做的更新,適用 20(YY+1) 年初(例:ctryprem21 寫「Updated January 1, 2022」)。"""
    raw = RAW / "damodaran"
    path = raw / f"ctryprem{yy}.xlsx"
    if not path.exists():
        path = raw / f"ctryprem{yy}.xls"
    rows = sheet_rows(path, "ERPs by country")
    mature = rows.get(3, {}).get("E")
    note = str(rows.get(3, {}).get("F") or "")
    tw = next((v for v in rows.values() if str(v.get("A", "")).strip() == "Taiwan"), None)
    # 可用日 = 檔內寫的更新日(例 ctryprem15 是「Updated July 1, 2016」的年中版,不能從 1 月就用);沒寫就用適用年度 1/1
    m = _UPD.search(note)
    avail = (pd.Timestamp(f"{m.group(1)} {m.group(2) or 1}, {m.group(3)}").strftime("%Y-%m-%d") if m
             else f"{2000 + yy + 1}-01-01")
    return {"file": path.name, "mature_erp": float(mature) if mature is not None else None, "note": note,
            "tw_total_erp": float(tw["E"]) if tw and isinstance(tw.get("E"), float) else None,
            "available_at": avail}


# ---------------- 建置 json(Fetcher / 維護者,只在資料版本建置時跑一次) ----------------

def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    cbc = RAW / "cbc_annual"
    pdfs = sorted(cbc.glob("*.pdf"))
    text: dict[int, float] = {}
    for p in pdfs:
        for y, v in tw10y_from_text(p).items():
            text.setdefault(y, v)
    # 附表的欄位排列各年不同:每份附表先和內文值核對(重疊年份差 ≤ 0.006),有任何不一致就整份不用
    table: dict[int, float] = {}
    rejected: dict[str, str] = {}
    for p in pdfs:   # 依年報年份排序:較新的附表覆蓋較舊的(修正值)
        t = tw10y_from_table(p)
        over = [y for y in t if y in text]
        if not t or not over:
            rejected[p.name] = "沒有附表或沒有可核對的年份"
            continue
        if any(abs(t[y] - text[y]) > 0.006 for y in over):
            rejected[p.name] = "附表與內文不一致(欄位排列不同),不用"
            continue
        table.update(t)
    years = sorted(set(table) | set(text))
    annual = {y: (table[y] if y in table else text[y]) for y in years}
    check = {y: {"table": table.get(y), "text": text.get(y),
                 "diff": round(abs(table[y] - text[y]), 4) if y in table and y in text else None} for y in years}
    bad = [y for y, c in check.items() if c["diff"] is not None and c["diff"] > 0.006]
    if bad:
        raise ValueError(f"央行年報附表與內文不一致:{bad} {[check[y] for y in bad]}")
    tw = {"source": "中央銀行年報附表「10 年期政府公債次級市場利率(平均)」(單位 %,兩位小數);"
                    "附表沒有的年份(較新的年報不再附)用內文「10 年期指標公債殖利率平均為 X%」",
          "availability": "年度 Y 的平均在 Y+1 年 1 月 1 日起可用",
          "annual_pct": {str(y): v for y, v in annual.items()},
          "basis": {str(y): ("table" if y in table else "text") for y in years}, "check_vs_text": check,
          "rejected_tables": rejected}
    (OUT / "tw10y_annual.json").write_text(json.dumps(tw, ensure_ascii=False, indent=1), encoding="utf-8")
    dam, missing = {}, []
    for yy in range(14, 25):
        try:
            dam[str(2000 + yy + 1)] = damodaran_file(yy)
        except ImportError:
            missing.append(f"ctryprem{yy}(.xls 需要 xlrd)")
    # 核對:成熟市場 ERP 應等於 histimpl 前一年年底的 Implied ERP (FCFE)(年中版本會不同,例 ctryprem15)
    hist_chk = {}
    try:
        import xlrd  # noqa: PLC0415

        sh = xlrd.open_workbook(str(RAW / "damodaran" / "histimpl.xls")).sheet_by_index(0)
        col = sh.row_values(6).index("Implied ERP (FCFE)")
        hist = {int(r[0]): r[col] for r in (sh.row_values(i) for i in range(sh.nrows))
                if r and isinstance(r[0], float) and isinstance(r[col], float)}
        for y, r in dam.items():
            h = hist.get(int(y) - 1)
            if h is not None and r.get("mature_erp") is not None:
                hist_chk[y] = {"ctryprem": round(r["mature_erp"], 4), "histimpl_prev_yearend": round(h, 4),
                               "diff": round(r["mature_erp"] - h, 4)}
    except (ImportError, FileNotFoundError, ValueError) as e:
        hist_chk = {"error": f"{type(e).__name__}: {e}"}
    dm = {"source": "Damodaran ctryprem 年度更新(pages.stern.nyu.edu/~adamodar)",
          "availability": "檔內寫的更新日起可用(沒寫就用適用年度 1 月 1 日)", "by_year": dam, "missing": missing,
          "check_vs_histimpl": hist_chk}
    (OUT / "damodaran_erp.json").write_text(json.dumps(dm, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tw10y": tw, "damodaran": dm}


# ---------------- 給 PIT store 的 facts(回測只透過 PITStore.as_of(d) 取用) ----------------

ENTITY = "RATES"


def facts() -> pd.DataFrame:
    """把 build() 產生的 json 轉成 PIT facts:tw10y(年平均,年底結算,隔年 1/1 可用)、tw_total_erp、mature_erp
    (Damodaran 年初版本,當年 1/1 可用)。數值一律是小數(1.43% → 0.0143)。json 不存在就回傳空表。"""
    rows = []
    tw_p, dm_p = OUT / "tw10y_annual.json", OUT / "damodaran_erp.json"
    if tw_p.exists():
        for y, v in json.loads(tw_p.read_text(encoding="utf-8"))["annual_pct"].items():
            rows.append({"field": "tw10y", "period_end": f"{y}-12-31", "value": float(v) / 100,
                         "available_at": f"{int(y) + 1}-01-01", "source": "cbc_annual_report"})
    if dm_p.exists():
        for y, r in json.loads(dm_p.read_text(encoding="utf-8"))["by_year"].items():
            for k in ("tw_total_erp", "mature_erp"):
                if r.get(k) is not None:
                    rows.append({"field": k, "period_end": f"{y}-01-01", "value": float(r[k]),
                                 "available_at": r["available_at"], "source": f"damodaran:{r['file']}"})
    if not rows:
        return pd.DataFrame(columns=["entity", "field", "period_end", "period", "value", "available_at", "source", "filed"])
    f = pd.DataFrame(rows)
    f["entity"], f["period"], f["filed"] = ENTITY, "FY", pd.NaT
    for c in ("period_end", "available_at"):
        f[c] = pd.to_datetime(f[c])
    return f


def rates_asof(view) -> dict:
    """d 當時看得到的最新 tw_rf(台灣 10 年期年平均)、tw_erp(台灣總 ERP)、erp_mature(成熟市場 ERP);沒有的鍵不放。"""
    w = view.facts(ENTITY)
    out = {}
    for field, key in (("tw10y", "tw_rf"), ("tw_total_erp", "tw_erp"), ("mature_erp", "erp_mature")):
        if field in w:
            s = w[field].dropna()
            if len(s):
                out[key] = float(s.iloc[-1])
    return out
