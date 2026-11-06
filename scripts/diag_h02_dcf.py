"""P2-H02 描述性診斷:只看估值輸出(DCF ÷ 現價、利潤率、再投資),不讀任何報酬。判斷日:2016–2023 每年 12 月。

用法(離線、凍結 v2.0、v1.1 參數;INVEST_LAB_OUT_DIR 指到含 bt_pit/ store 與逐筆檔的回測輸出):
  PYTHONPATH=. INVEST_LAB_OFFLINE=1 INVEST_LAB_DATA_DIR=data/frozen/v2.0_2026-10-16 INVEST_LAB_OUT_DIR=<輸出> \
  INVEST_LAB_PARAMS=data/frozen/v2.0_2026-10-16/model_params.json .venv/bin/python scripts/diag_h02_dcf.py <輸出 csv>
"""
import json
import sys

import numpy as np
import pandas as pd

from lab.config import load_params
from lab.pit import PITStore
from lab.research import backtest as BT
from lab.research import backtest_pit as BP
from lab.valuation import dcf as D

CAP = []
_run = BT.VM.run


def spy(ctx, params):
    out = _run(ctx, params)
    CAP.append((ctx, out))
    return out


def vd_dcf(rev0, g0, m0, mT, wacc, gT, years, nd, sh):
    """終值用價值驅動公式:利潤率由 m0 線性收斂到 mT = NOPAT 率 × (1 − g / RONIC),RONIC = WACC。"""
    if wacc - gT < 0.015 or sh <= 0:
        return None
    gs = D.growth_path(g0, gT, years)
    revs = rev0 * np.cumprod(1 + gs)
    ms = np.array([m0 + (mT - m0) * i / (years - 1) for i in range(years)])
    disc = (1 + wacc) ** np.arange(1, years + 1)
    tv = revs[-1] * (1 + gT) * mT / (wacc - gT)
    return (np.sum(revs * ms / disc) + tv / disc[-1] - nd) / sh


def collect() -> pd.DataFrame:
    BT.VM.run = spy
    params, P = load_params(), BP.paths()
    macro = PITStore.load(P["macro"])
    out = []
    for mk, u in (("TW", "tw_pit50"), ("US", "us_large40")):
        rows = pd.read_csv(P[mk].parent / f"rows_{u}.csv")
        rows["date"] = pd.to_datetime(rows["date"])
        sel = rows[(rows.date.dt.month == 12) & (rows.date.dt.year.between(2016, 2023))]
        for sym, g in sel.groupby("symbol"):
            st = PITStore.load(P[mk], entities=[BP.entity_of(sym, mk)])
            it, ik = BP._industry(mk, sym)
            for d in g.date:
                CAP.clear()
                r = BP.judge_pit(st.as_of(d), mk, sym, params, it, ik, macro.as_of(d))
                if not r or not CAP:
                    continue
                ctx, val = CAP[-1]
                dcf = val.get("dcf") or {}
                if not dcf.get("base"):
                    continue
                inp = dcf["inputs"]
                rev, tax = inp["revenue_ttm"], ctx.get("tax_rate") or 0.2
                nopat_m = (ctx["ebit_norm"] * (1 - tax) / rev) if ctx.get("ebit_norm") and rev else None
                row = {"mkt": mk, "sym": sym, "date": d.date().isoformat(), "price": ctx["price"], "dcf_ps": dcf["base"],
                       "m_fcff": inp["fcff_margin"], "nopat_m": nopat_m, "wacc": inp["wacc"], "g_term": inp["g_terminal"],
                       "g0": inp["g_start"], "tv_share": dcf.get("tv_share")}
                if nopat_m and nopat_m > 0:
                    row["mT"] = nopat_m * (1 - inp["g_terminal"] / inp["wacc"])
                    row["vd_ps"] = vd_dcf(rev, inp["g_start"], inp["fcff_margin"], row["mT"], inp["wacc"], inp["g_terminal"],
                                          inp["years"], inp["net_debt"], inp["shares"])
                out.append(row)
    return pd.DataFrame(out)


def summarize(df: pd.DataFrame) -> dict:
    df = df.assign(r_now=df.dcf_ps / df.price, r_vd=df.vd_ps / df.price, reinv=1 - df.m_fcff / df.nopat_m)
    res = {}
    for mk, g in df.groupby("mkt"):
        h = g.dropna(subset=["r_vd", "reinv"])
        h = h.assign(t=pd.qcut(h.reinv.rank(method="first"), 3, labels=["再投資低", "中", "高"]))
        res[mk] = {"n": len(g), "median_dcf_over_price": round(g.r_now.median(), 3),
                   "dcf_over_price_p10_p90": [round(g.r_now.quantile(0.1), 2), round(g.r_now.quantile(0.9), 2)],
                   "share_dcf_below_price": round((g.r_now < 1).mean(), 3),
                   "share_fcff_margin_above_nopat": round((g.m_fcff > g.nopat_m).mean(), 3),
                   "median_reinvest_rate": round(h.reinv.median(), 3), "vd_over_now_median": round((h.vd_ps / h.dcf_ps).median(), 3),
                   "by_reinvest_tercile": {str(k): {"n": len(x), "now": round(x.r_now.median(), 3), "vd": round(x.r_vd.median(), 3)}
                                           for k, x in h.groupby("t", observed=True)}}
    return res


if __name__ == "__main__":
    data = collect()
    data.to_csv(sys.argv[1], index=False)
    print(json.dumps(summarize(data), ensure_ascii=False, indent=1))
