"""訊號目錄:精靈第 ② 步的訊號卡片(散戶看得懂的指標說明)。規格 docs/上手精靈規格.md §4.1、§7。

- 由出廠模型的指標清單(lab/models/jw-mf-2.0.json)與 lab/metric_dictionary.py 組出來;範本預定、但基礎變數還沒有的
  指標(template.pending_metrics)也列出來,標 ready = False。
- 白話名稱與一句話說明是這裡新寫的內容(≤ 40 字),用 {"zh-TW": ...} 結構,英文版之後再補 "en"(§7)。
  公式、出處仍以 metric_dictionary.py 為準,這裡只放 dictionary_key 讓前端連過去,不重寫。
- good_when 由打分函數推出(linear 看 good 和 bad 的大小、peak 看 center、map 看表的單調方向、builtin 寫死)。
- backtestable 依基礎變數在回測(backtest_pit judge → 原始值快取)的可得性,見 BACKTEST_MISSING。
前端只讀 /api/catalog,不自己寫說明文字。研究用途,非投資建議。
"""
from __future__ import annotations

from ..analysis.scoring import BASE_VARS, DEFAULT_MODEL, PILLAR_LABEL
from .schema import PILLARS, load_shipped

LANG = "zh-TW"


def tr(text: str) -> dict:
    """多語文字(這一階段只填繁中)。"""
    return {LANG: text}


# 重要度三段(精靈與之後的 AI 助手共用;API 也接受中文標籤)
LEVELS = ("off", "normal", "high")
LEVEL_LABEL = {"off": "不看", "normal": "普通", "high": "很重要"}


def levels() -> list[dict]:
    return [{"value": v, "label": tr(LEVEL_LABEL[v])} for v in LEVELS]


# 面向的白話說明
PILLAR_PLAIN = {
    "quality": ("品質", "公司賺錢的能力好不好、穩不穩"),
    "growth": ("成長", "營收、獲利是不是越來越多"),
    "valuation": ("估值", "現在的股價相對公司價值貴不貴"),
    "health": ("財務安全", "負債、現金夠不夠撐過不景氣"),
    "momentum": ("技術動能", "股價的趨勢是往上還是往下"),
    "flows": ("籌碼", "法人、大戶、內部人在買還是在賣"),
    "revisions": ("預期修正", "分析師最近把獲利預估往上還是往下調"),
    "sentiment": ("消息情緒", "最近新聞標題偏正面還是偏負面"),
}

# 訊號 key → (白話名稱, 一句話說明, metric_dictionary 的 key 或 None)
PLAIN: dict[str, tuple[str, str, str | None]] = {
    # 品質
    "roic": ("用錢賺錢的效率", "公司投入的所有資本,一年能賺回多少", "roic"),
    "roe": ("股東的錢賺多少", "股東每投入 100 元,一年賺多少", "roe"),
    "roa": ("資產賺錢的效率", "每 100 元資產能賺多少,銀行業特別看這個", "roa"),
    "gp_assets": ("資產的毛利產出", "每 100 元資產帶來多少毛利,不受研發、廣告費影響", "gp_assets"),
    "gross_margin": ("毛利率", "每賣 100 元,扣掉直接成本還剩多少", "gross_margin"),
    "operating_margin": ("本業利潤率", "扣掉研發、管銷後,本業還賺多少", "operating_margin"),
    "gm_std_10y": ("毛利率穩不穩", "近 10 年毛利率起伏越小,生意越穩", "gm_std_10y"),
    "cfo_to_ni": ("獲利有沒有收到現金", "帳上的獲利有多少真的變成現金", "cfo_to_ni"),
    "accruals": ("帳面獲利的水分", "獲利裡還沒收到現金的部分,越少越踏實", "accruals"),
    "loss_years": ("過去虧損的次數", "近 10 年有幾年賠錢", None),
    # 成長
    "revenue_yoy": ("營收成長", "營收比去年同期多了多少", "revenue_yoy"),
    "revenue_cagr_3y": ("3 年營收成長", "近 3 年營收平均每年成長多少", "revenue_cagr_3y"),
    "eps_yoy": ("每股獲利成長", "每股賺的錢比去年多了多少", "eps_yoy"),
    "eps_cagr_3y": ("3 年獲利成長", "近 3 年每股獲利平均每年成長多少", "eps_yoy"),
    "fwd_rev": ("分析師看的營收成長", "分析師預估明年營收會成長多少", "fwd_growth"),
    "fwd_eps": ("分析師看的獲利成長", "分析師預估明年每股獲利會成長多少", "fwd_growth"),
    "q_gm_yoy": ("毛利率有沒有變好", "最近一季毛利率比去年同季高還是低", "gross_margin"),
    "mr_yoy3": ("近 3 個月營收成長", "台股每月公布營收,近 3 個月比去年同期多多少", "month_revenue"),
    "mr_record": ("月營收創新高", "最新月營收是不是歷史新高", "month_revenue"),
    "mr_sur": ("營收意外", "這個月營收成長比平常好還是差", "month_revenue"),
    # 估值
    "fv_upside": ("離公平價還有多少空間", "模型估的公平價比現價高多少", "fair_value"),
    "pe_rank": ("本益比在自己歷史的位置", "現在本益比在過去 5 年偏低還是偏高", "pe_band"),
    "fpe_vs_peers": ("比同業低嗎", "預估本益比和同業中位數相比", "forward_pe"),
    "fcf_yield": ("現金流殖利率", "把公司買下來,每年能拿回多少現金", "fcf_yield"),
    "ev_ebitda": ("企業價值倍數", "買下整間公司要花幾年的現金獲利", "ev_ebitda"),
    "peg": ("成長調整後的本益比", "本益比除以成長率,成長快可以接受較高本益比", "peg"),
    "pb": ("股價淨值比", "股價是每股淨值的幾倍,金融股常用", "pb"),
    # 財務安全
    "altman_z": ("破產風險分數", "經典的破產預警分數,越高越安全", "altman_z"),
    "piotroski": ("基本面體檢分數", "9 項財務檢查通過幾項", "piotroski"),
    "net_debt_ebitda": ("幾年還得清負債", "用目前的現金獲利,幾年能還清淨負債", "net_debt_ebitda"),
    "interest_coverage": ("付利息的能力", "本業獲利是利息的幾倍", "interest_coverage"),
    "current_ratio": ("短期還債能力", "一年內要還的債,手上流動資產夠不夠", "current_ratio"),
    "de": ("負債比重", "負債是股東權益的幾倍", None),
    "beneish": ("財報作假警訊", "用 8 個財務比率偵測財報可能被美化", "beneish"),
    "equity_assets": ("銀行的資本厚度", "股東權益占總資產多少,金融業越厚越安全", None),
    "loss_years_fin": ("過去虧損的次數(金融業)", "近 10 年有幾年賠錢", None),
    # 技術動能
    "mom_12_1": ("一年來的漲幅", "過去一年(扣掉最近一個月)股價漲了多少", "mom_12_1"),
    "from_high": ("離一年高點多遠", "現價比 52 週最高價低多少", "pct_from_52w_high"),
    "vs_ma200": ("在年線上方還是下方", "股價比 200 日均線高或低多少", None),
    "ma200_slope": ("年線方向", "200 日均線最近 20 天是往上還是往下", None),
    "rel_6m": ("半年來贏大盤多少", "近 6 個月漲跌幅減掉大盤", None),
    "rsi": ("短期冷熱", "RSI 指標,太冷或太熱都扣分", "rsi"),
    "trend": ("趨勢狀態", "用均線排列判斷多頭、盤整或空頭", None),
    # 籌碼
    "foreign_20d": ("外資近一個月買賣", "外資 20 天買賣超占股本多少", "inst_flow"),
    "trust_20d": ("投信近一個月買賣", "投信 20 天買賣超占股本多少", "inst_flow"),
    "foreign_streak": ("外資連買或連賣幾天", "外資連續買超(正)或賣超(負)的天數", "inst_flow"),
    "foreign_hold_60d": ("外資持股變化", "外資持股比例近 60 天增加還是減少", "inst_flow"),
    "margin_20d": ("融資增減", "融資餘額 20 天變化,散戶借錢買越多越扣分", "margin"),
    "big1000_chg": ("大戶持股變化", "千張大戶持股比例這週增加還是減少", "tdcc"),
    "holders_chg": ("股東人數變化", "股東人數減少代表籌碼往少數人集中", "tdcc"),
    "insider": ("公司內部人買股", "董監經理人近 6 個月在公開市場買入的次數", "insider"),
    "short_float": ("放空比例", "流通股裡被放空的比例", "short_interest"),
    "short_chg": ("放空增減", "放空股數比上個月多還是少", "short_interest"),
    "inst_adding": ("大機構增持比例", "前十大機構股東裡有多少在增持", None),
    # 預期修正
    "eps0y_90d": ("今年獲利預估調整", "分析師近 90 天把今年 EPS 預估上調或下調多少", "eps_revision"),
    "eps1y_90d": ("明年獲利預估調整", "分析師近 90 天把明年 EPS 預估上調或下調多少", "eps_revision"),
    "eps0y_30d": ("最近一個月預估調整", "分析師近 30 天把今年 EPS 預估上調或下調多少", "eps_revision"),
    "breadth": ("上調的分析師多不多", "上調預估的家數減掉下調的家數", "eps_revision"),
    "surprise": ("財報驚喜", "近 4 季實際獲利比預期好多少", "surprise"),
    "beats": ("打敗預期的次數", "近 4 季有幾季獲利優於預期", "surprise"),
    "rec_chg": ("評等變化", "分析師平均評等近 3 個月變好還是變差", None),
    "rating_net": ("升評與目標價調整", "近 90 天升評、調高目標價的淨次數", None),
    # 消息情緒
    "news7": ("近一週新聞氣氛", "近 7 天新聞標題偏正面還是負面", "news_sentiment"),
    "news30": ("近一個月新聞氣氛", "近 30 天新聞標題偏正面還是負面", "news_sentiment"),
    # 範本預定(S4 新基礎變數)
    "dividend_yield": ("現金殖利率", "近 12 個月已除息的現金股利 ÷ 股價", None),
    "div_paid_years": ("配息穩定度", "近 5 年有幾年發現金股利", None),
    "vol_1y": ("股價波動大小", "近 1 年每天漲跌的起伏換算成一年", None),
    "beta_2y": ("跟大盤連動程度", "大盤漲跌 1%,這檔平均跟著動多少", "beta"),
}

# ---------------- 回測可得性 ----------------
# 回測(backtest_pit judge → lab/research/backtest.py judge_at)沒有的資料:分析師預估與修正、同業、新聞、
# 美股籌碼(內部人、放空、機構)、台股集保分級;預估本益比與 PEG 也因此是 None。
BACKTEST_MISSING = frozenset({
    "fwd_rev", "fwd_eps", "forward_pe", "fpe_rel_peers", "peg",
    "peer_pct_roe", "peer_pct_gross_margin", "peer_pct_operating_margin", "peer_pct_revenue_yoy", "peer_pct_ev_ebitda",
    "eps0y_chg_90d", "eps1y_chg_90d", "eps0y_chg_30d", "rev_breadth", "surprise_avg_4q", "beats_4q",
    "rec_score_chg_3m", "rating_changes_present", "rating_net",
    "news_d7_avg", "news_d7_n", "news_d30_avg", "news_d30_n",
    "big1000_chg", "holders_chg",
    "insider_present", "insider_buy_count_6m", "insider_sell_value_6m", "short_pct_float", "short_chg_vs_prior",
    "inst_net_adding",
})
# 台股法人與融資:只有參數 tw_chips_history 打開時,回測才用 PIT 籌碼歷史(P2-H07;目前關閉)
TW_CHIPS_HISTORY_VARS = frozenset({"foreign_d20_pct", "trust_d20_pct", "foreign_streak", "foreign_hold_chg_60d",
                                   "margin_chg_20d", "margin_to_shares"})
# 美股回測沒有季報表(只有年報與 TTM),單季毛利率年變化算不出來;
# ev_ebitda、beneish_m 在美股回測快取裡全部是空值(2026-11-02 用真實快取確認 0 / 4973)
US_BACKTEST_MISSING = frozenset({"q_gm_change_yoy", "ev_ebitda", "beneish_m"})
# 只有台股才有的變數
TW_ONLY_VARS = frozenset({"mr_present", "mr_yoy_3m", "mr_sur", "mr_record_high", "mr_record_same_month", "mr_record"})


def var_backtestable(var: str, market: str, params: dict | None = None) -> bool:
    if var not in BASE_VARS or var in BACKTEST_MISSING:
        return False
    if var in TW_CHIPS_HISTORY_VARS:
        return market == "TW" and bool((params or {}).get("tw_chips_history"))
    if market == "US" and (var in US_BACKTEST_MISSING or var in TW_ONLY_VARS):
        return False
    return True


# ---------------- good_when ----------------
_BUILTIN_DIR = {"mr_record": "higher", "net_debt_ebitda": "lower", "beneish": "lower", "insider": "higher"}
_DIR_TEXT = {"higher": "數字越高越好", "lower": "數字越低越好"}


def good_when(score: dict) -> dict:
    """由打分函數推出「什麼時候算好」:{direction: higher | lower | target | none, target?, text}。"""
    t = score.get("type")
    d = None
    if t == "linear":
        d = "higher" if score["good"] > score["bad"] else "lower" if score["good"] < score["bad"] else "none"
    elif t == "peak":
        c = score["center"]
        return {"direction": "target", "target": c, "text": tr(f"接近 {c:g} 最好")}
    elif t == "map":
        pts = sorted((float(k), v) for k, v in score["table"].items())
        vals = [v for _, v in pts]
        d = ("higher" if vals == sorted(vals) and vals[0] != vals[-1]
             else "lower" if vals == sorted(vals, reverse=True) and vals[0] != vals[-1] else "none")
    elif t == "builtin":
        d = _BUILTIN_DIR.get(score.get("fn"), "none")
    if d == "none" or d is None:
        return {"direction": "none", "text": tr("看組合判斷")}
    return {"direction": d, "text": tr(_DIR_TEXT[d])}


def _vars_of(m: dict) -> set[str]:
    v = m.get("value") or {}
    out = {v["var"]} if "var" in v else set()
    if m.get("requires"):
        out.add(m["requires"])
    return out


def _markets_of(m: dict) -> set[str]:
    mk = m.get("market", "all")
    if mk in ("TW", "US"):
        return {mk}
    return {"TW"} if _vars_of(m) & TW_ONLY_VARS else {"TW", "US"}


def _template_pending() -> list[dict]:
    from . import wizard   # 延後匯入:wizard 也會用到這個模組
    out, seen = [], set()
    for t in wizard.raw_templates():
        for m in t["template"].get("pending_metrics") or []:
            if m["key"] not in seen:
                seen.add(m["key"])
                out.append(m)
    return out


def _dictionary() -> dict:
    from .. import metric_dictionary
    return {d["key"]: d for d in metric_dictionary.as_list()}


def signals(market: str | None = None, params: dict | None = None) -> list[dict]:
    """訊號清單(依出廠模型的指標順序;同一個 key 的金融 / 非金融分支合併成一筆)。market 給了就只列這個市場有的訊號。"""
    if params is None:
        from ..config import load_params
        params = load_params()
    base = load_shipped(DEFAULT_MODEL, readonly=True)["metrics"]
    rows: dict[str, list[dict]] = {}
    for m in [*base, *_template_pending()]:
        rows.setdefault(m["key"], []).append(m)
    dic = _dictionary()
    out = []
    for key, ms in rows.items():
        first = ms[0]
        vars_ = set().union(*(_vars_of(m) for m in ms))
        markets = sorted(set().union(*(_markets_of(m) for m in ms)))
        if market and market not in markets:
            continue
        ready = vars_ <= set(BASE_VARS)
        by_mk = {mk: ready and all(var_backtestable(v, mk, params) for v in vars_) for mk in markets}
        name, one, dkey = PLAIN.get(key, (first["label"], "", None))
        d = dic.get(dkey or key) or {}
        out.append({
            "key": key, "pillar": first["pillar"], "pillar_name": tr(PILLAR_LABEL[first["pillar"]]),
            "label": first["label"], "plain_name": tr(name), "one_liner": tr(one or d.get("meaning") or first["label"]),
            "good_when": good_when(first["score"]), "markets": markets,
            "applies": sorted({m.get("applies", "all") for m in ms}),
            "backtestable": by_mk[market] if market else all(by_mk.values()), "backtestable_by_market": by_mk,
            "ready": ready, "requires_vars": sorted(v for v in vars_ if v not in BASE_VARS),
            "dictionary_key": dkey if dkey in dic else None,
        })
    return out


def pillars() -> list[dict]:
    return [{"key": p, "name": tr(PILLAR_PLAIN[p][0]), "one_liner": tr(PILLAR_PLAIN[p][1])} for p in PILLARS]


NOT_BACKTESTABLE_NOTE = "此訊號無法回測:回測沒有這項資料,回測時這個訊號不計分"


def catalog(market: str | None = None, params: dict | None = None) -> dict:
    return {"market": market, "pillars": pillars(), "signals": signals(market, params), "levels": levels(),
            "not_backtestable_note": tr(NOT_BACKTESTABLE_NOTE), "disclaimer": tr("研究用途,非投資建議")}
