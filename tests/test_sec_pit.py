"""美股 PIT 管線(lab/pit/build_us.py):申報版本、可用日、季報與 Q4、分割基準、CIK、竄改測試。

前半段用合成資料(任何環境都會跑);後半段需要凍結資料,而且必須在離線模式下執行
(INVEST_LAB_OFFLINE=1、INVEST_LAB_DATA_DIR=<凍結資料夾>),否則 skip。
"""
import numpy as np
import pandas as pd
import pytest

from lab import config
from lab.pit import PITStore
from lab.pit import build_us as B
from lab.pit.tamper import assert_pit_invariant
from lab.sources import sec

T = pd.Timestamp


# ---------------- 合成資料 ----------------


def _v(val, start, end, form, filed, accn, fp="FY"):
    d = {"val": val, "end": end, "form": form, "filed": filed, "accn": accn, "fp": fp, "fy": 0}
    if start:
        d["start"] = start
    return d


def _facts(**concepts):
    gaap = {}
    for c, (unit, vals) in concepts.items():
        gaap[c] = {"units": {unit: vals}}
    return {"facts": {"us-gaap": gaap}}


CAL = pd.bdate_range("2019-01-01", "2023-12-29")


def _synthetic():
    """FY2021(1/1–12/31):Q1–Q3 季報、全年年報;FY2020 在 2022 年報裡重編;2021-08-02 一拆四。"""
    k20, q1, q2, q3, k21 = "a-20K", "a-21Q1", "a-21Q2", "a-21Q3", "a-21K"
    ni = [
        _v(100.0, "2020-01-01", "2020-12-31", "10-K", "2021-02-01", k20),
        _v(30.0, "2021-01-01", "2021-03-31", "10-Q", "2021-04-30", q1, "Q1"),
        _v(31.0, "2021-04-01", "2021-06-30", "10-Q", "2021-07-30", q2, "Q2"),
        _v(61.0, "2021-01-01", "2021-06-30", "10-Q", "2021-07-30", q2, "Q2"),
        _v(32.0, "2021-07-01", "2021-09-30", "10-Q", "2021-10-29", q3, "Q3"),
        _v(130.0, "2021-01-01", "2021-12-31", "10-K", "2022-02-01", k21),
        _v(90.0, "2020-01-01", "2020-12-31", "10-K", "2022-02-01", k21),  # FY2020 重編 100 → 90
    ]
    # 現金流量:季報只有年初至今(3、6、9 個月)
    cfo = [
        _v(10.0, "2021-01-01", "2021-03-31", "10-Q", "2021-04-30", q1, "Q1"),
        _v(25.0, "2021-01-01", "2021-06-30", "10-Q", "2021-07-30", q2, "Q2"),
        _v(45.0, "2021-01-01", "2021-09-30", "10-Q", "2021-10-29", q3, "Q3"),
        _v(70.0, "2021-01-01", "2021-12-31", "10-K", "2022-02-01", k21),
        # 隔年季報才第一次給 2021 Q2 的單季 3 個月數字(比較欄):之後改用它,之前用年初至今差
        _v(16.0, "2021-04-01", "2021-06-30", "10-Q", "2022-07-29", "a-22Q2", "Q2"),
    ]
    # EPS:Q1、Q2 是分割前(當時)申報的;Q3 與全年是分割後
    eps = [
        _v(4.0, "2021-01-01", "2021-03-31", "10-Q", "2021-04-30", q1, "Q1"),
        _v(4.0, "2021-04-01", "2021-06-30", "10-Q", "2021-07-30", q2, "Q2"),
        _v(1.0, "2021-07-01", "2021-09-30", "10-Q", "2021-10-29", q3, "Q3"),
        _v(4.2, "2021-01-01", "2021-12-31", "10-K", "2022-02-01", k21),
    ]
    assets = [
        _v(500.0, None, "2020-12-31", "10-K", "2021-02-01", k20),
        _v(510.0, None, "2021-09-30", "10-Q", "2021-10-29", q3, "Q3"),
        _v(505.0, None, "2020-12-31", "10-Q", "2021-10-29", q3, "Q3"),  # 季報附的上年底餘額:不收
        _v(520.0, None, "2021-12-31", "10-K", "2022-02-01", k21),
        _v(501.0, None, "2020-12-31", "10-K", "2022-02-01", k21),
    ]
    fj = _facts(NetIncomeLoss=("USD", ni), NetCashProvidedByUsedInOperatingActivities=("USD", cfo),
                EarningsPerShareDiluted=("USD/shares", eps), Assets=("USD", assets))
    ev = pd.DataFrame([{"entity": "X", "date": T("2021-08-02"), "kind": "split", "ratio": 4.0}])
    f, _ = B.build_facts("X", fj, CAL, ev)
    idx = pd.bdate_range("2021-01-04", "2022-06-30")
    px = pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1e6, "cash_div": 0.0},
                      index=idx)
    return PITStore(f, {"X": px}, ev, "synthetic"), f


def test_available_at_is_next_trading_day():
    cal = pd.DatetimeIndex(["2021-04-29", "2021-04-30", "2021-05-03", "2021-05-04"])
    got = B.next_trading_day(["2021-04-30", "2021-04-29", "2018-03-02"], cal)
    assert list(got) == [T("2021-05-03"), T("2021-04-30"), T("2018-03-05")]  # 日曆外用下一個平日


def test_every_filing_version_is_kept_and_as_of_picks_the_one_known_then():
    s, f = _synthetic()
    fy20 = f[(f.field == "net_income") & (f.period == "FY") & (f.period_end == T("2020-12-31"))]
    assert sorted(fy20["value"]) == [90.0, 100.0]
    assert set(fy20["available_at"]) == {T("2021-02-02"), T("2022-02-02")}  # 申報日的下一個交易日
    assert B.annual_table_asof(s.as_of("2021-06-30"), "X").loc[T("2020-12-31"), "net_income"] == 100.0
    assert B.annual_table_asof(s.as_of("2022-02-01"), "X").loc[T("2020-12-31"), "net_income"] == 100.0  # 申報當天還不能用
    assert B.annual_table_asof(s.as_of("2022-02-02"), "X").loc[T("2020-12-31"), "net_income"] == 90.0


def test_quarters_ytd_difference_and_q4():
    _, f = _synthetic()

    def q(field, period):
        x = f[(f.field == field) & (f.period == period)]
        return list(zip(x["value"], x["available_at"]))

    assert q("net_income", "Q4") == [(130.0 - 30 - 31 - 32, T("2022-02-02"))]  # Q4 = 全年 − Q1 − Q2 − Q3,可用日 = 年報
    # 現金流量:單季 = 年初至今差;一年後出現直接的單季數字 → 新版本(衍生值在那之前照用,不因未來有直接數字而消失)
    assert q("cfo", "Q2") == [(15.0, T("2021-08-02")), (16.0, T("2022-08-01"))]
    # Q3 = 9 個月 − Q1 − Q2:Q2 改版 → Q3 跟著出新版本;Q4 = 全年 − 三季,數值沒變就不出新版本
    assert q("cfo", "Q3") == [(20.0, T("2021-11-01")), (19.0, T("2022-08-01"))]
    assert q("cfo", "Q4") == [(25.0, T("2022-02-02"))]
    # 時點科目:季報本季季底收、季報附的上年底餘額不收
    a = f[f.field == "total_assets"]
    assert set(zip(a["period_end"], a["period"], a["value"])) == {
        (T("2020-12-31"), "FY", 500.0), (T("2020-12-31"), "FY", 501.0), (T("2021-09-30"), "Q3", 510.0),
        (T("2021-12-31"), "FY", 520.0)}


def test_q4_eps_uses_one_share_basis_and_ttm_on_decision_basis():
    s, f = _synthetic()
    # 全年 4.2(分割後)− Q1 4.0/4 − Q2 4.0/4 − Q3 1.0 = 1.2(Q1、Q2 先換到分割後基準再相減)
    q4 = f[(f.field == "eps_diluted") & (f.period == "Q4")]
    assert q4["value"].tolist() == pytest.approx([1.2])
    t = B.ttm_asof(s.as_of("2022-03-01"), "X")
    assert t["eps_diluted"] == pytest.approx(1.0 + 1.0 + 1.0 + 1.2)
    assert t["net_income"] == pytest.approx(130.0) and t["total_assets"] == 520.0
    # 分割前看:只有三季 → None;分割後、年報前:四季不齊 → None
    assert B.ttm_asof(s.as_of("2021-07-30"), "X") is None
    assert B.ttm_asof(s.as_of("2021-12-31"), "X") is None


def test_eps_split_adjusted_exactly_once():
    s, _ = _synthetic()
    v = s.as_of("2021-09-30")  # 分割後:Q2(分割前申報 4.0)要換成 1.0
    x = v.facts_long("X", ["eps_diluted"], period="Q")
    got = dict(zip(x["period_end"], B._to_d_basis(v, "X", x)))
    assert got[T("2021-06-30")] == pytest.approx(1.0)
    v2 = s.as_of("2021-07-30")  # 分割前:原值
    x2 = v2.facts_long("X", ["eps_diluted"], period="Q")
    assert dict(zip(x2["period_end"], B._to_d_basis(v2, "X", x2)))[T("2021-03-31")] == 4.0


def test_concept_choice_does_not_switch_meaning_inside_one_filing():
    """MA 型:新申報同時有「總額」與「淨額」兩個 concept 時,沿用和舊數字一致的那個,不照優先順序換成總額。"""
    rfc = [_v(236.0, "2020-01-01", "2020-12-31", "10-K", "2021-02-12", "k21")]
    rev = [_v(153.0, "2020-01-01", "2020-12-31", "10-K", "2021-02-12", "k21"),
           _v(169.0, "2019-01-01", "2019-12-31", "10-K", "2021-02-12", "k21")]
    old = [_v(169.0, "2019-01-01", "2019-12-31", "10-K", "2020-02-14", "k20")]
    fj = _facts(RevenueFromContractWithCustomerExcludingAssessedTax=("USD", rfc), Revenues=("USD", rev),
                SalesRevenueNet=("USD", old))
    r = B.raw_records(fj)
    got = r[(r.field == "revenue") & (r.accn == "k21")].set_index("end")["val"].to_dict()
    assert got == {T("2020-12-31"): 153.0, T("2019-12-31"): 169.0}


def test_raw_prices_undo_yahoo_split_adjustment():
    idx = pd.bdate_range("2022-07-13", "2022-07-20")
    adj = pd.Series([110.0, 111.0, 112.0, 113.0, 114.0, 115.0], index=idx)
    h = pd.DataFrame({"Open": adj, "High": adj, "Low": adj, "Close": adj, "Volume": 1000.0,
                      "Dividends": [0.0, 0.5, 0, 0, 0, 0], "Stock Splits": [0, 0, 0, 20.0, 0, 0]})
    raw = B.raw_prices(h)
    assert raw["close"].tolist()[:3] == [2200.0, 2220.0, 2240.0] and raw["close"].tolist()[3:] == [113.0, 114.0, 115.0]
    assert raw["cash_div"].iloc[1] == 10.0 and raw["volume"].iloc[0] == 50.0
    ev = B.split_events("G", h)
    s = PITStore(pd.DataFrame(columns=B.FACT_COLS), {"G": raw}, ev, "t")
    back = s.as_of("2022-07-20").prices("G")["close"]  # 分割後看 = Yahoo 調整價
    assert back.tolist() == pytest.approx(adj.tolist())
    assert s.as_of("2022-07-15").prices("G")["close"].iloc[-1] == 2240.0  # 分割前看 = 原始價


def test_filer_status_and_cik_override():
    assert B.filer_status({"facts": {"ifrs-full": {"Revenue": {}}}})[0] == "no_us_gaap"
    f20 = _facts(Revenues=("USD", [_v(1.0, "2020-01-01", "2020-12-31", "20-F", "2021-03-01", "x")]))
    assert B.filer_status(f20)[0] == "20-F"
    few = _facts(Revenues=("USD", [_v(1.0, "2020-01-01", "2020-12-31", "10-K", "2021-03-01", "x")]))
    assert B.filer_status(few)[0] == "few_concepts"
    assert sec.cik_for("XOM", overrides=True) == 34088  # 覆寫表(不需要連網)


# ---------------- 凍結資料 ----------------

HAVE_FROZEN = config.OFFLINE and (config.DATA_DIR / "freeze_date.txt").exists() and (config.CACHE_DIR / "sec_facts").exists()
frozen = pytest.mark.skipif(not HAVE_FROZEN, reason="需要離線模式 + 凍結資料(INVEST_LAB_OFFLINE=1、INVEST_LAB_DATA_DIR)")
SYMS = ["GOOGL", "NVDA", "AMZN", "AAPL", "JNJ", "MSFT", "COST", "AVGO", "DELL", "IBM", "XOM", "TSM", "ASML", "ARM"]


@pytest.fixture(scope="module")
def built():
    if not HAVE_FROZEN:
        pytest.skip("需要離線模式 + 凍結資料")
    return B.build_store(SYMS)


@frozen
def test_excluded_symbols_reported(built):
    _, rep = built
    st = {s: i["status"] for s, i in rep["symbols"].items()}
    excluded = {s for s, v in st.items() if v != "ok"}
    # 20-F 申報者一律排除;XOM 取決於資料版本(v1.1 凍結資料沒有 CIK 34088 的 companyfacts,v2.0 有)
    assert {"TSM", "ASML", "ARM"} <= excluded <= {"XOM", "TSM", "ASML", "ARM"}
    assert rep["symbols"]["XOM"]["cik"] == 34088 and rep["symbols"]["XOM"]["cik_legacy"] == 2115436
    assert sec.cik_for("XOM") == 2115436  # 預設維持 v1.1 行為(基準逐位元重現)


@frozen
@pytest.mark.parametrize("sym", ["GOOGL", "NVDA", "AAPL"])
@pytest.mark.parametrize("d", ["2019-06-28", "2022-08-01", "2024-06-14"])
def test_tamper_invariance(built, sym, d):
    store, _ = built
    assert_pit_invariant(lambda v: B.annual_table_asof(v, sym), store, d)
    assert_pit_invariant(lambda v: B.ttm_asof(v, sym), store, d)
    assert_pit_invariant(lambda v: B.ttm_asof(v, sym, skip=4), store, d)   # P2-H08:一年前的 TTM
    assert_pit_invariant(lambda v: v.prices(sym), store, d)
    assert_pit_invariant(lambda v: v.prices(sym, adjust="total"), store, d)


SPLIT_CASES = [  # (代號, 分割日, 分割前判斷日, 分割後判斷日)
    ("GOOGL", "2022-07-18", "2022-06-30", "2022-09-30"),
    ("NVDA", "2021-07-20", "2021-06-30", "2021-09-30"),
    ("NVDA", "2024-06-10", "2024-05-31", "2024-07-31"),
    ("AMZN", "2022-06-06", "2022-05-31", "2022-07-29"),
]


def _yahoo_pe(sym, d, row):
    """獨立算法:Yahoo 調整後收盤(今天的股數基準)÷ 當時公布的 EPS 換到今天的基準(用 Yahoo 的分割欄)。"""
    from lab.sources import yahoo
    h = yahoo.history(sym, yahoo.Status())
    px = float(h["Close"][h.index <= d].iloc[-1])
    sp = h["Stock Splits"]
    later = sp[(sp.index > row["filed"]) & (sp > 0)]
    return px / (row["value"] / float(later.prod() if len(later) else 1.0))


@frozen
@pytest.mark.parametrize("sym,split,before,after", SPLIT_CASES)
def test_pe_is_sane_across_splits(built, sym, split, before, after):
    store, _ = built
    pes = {}
    for d in (before, after):
        v = store.as_of(d)
        price = float(v.prices(sym, adjust="none")["close"].iloc[-1])  # d 當天實際成交價
        a = B.annual_table_asof(v, sym)
        eps_fy = float(a["eps_diluted"].dropna().iloc[-1])
        ttm = B.ttm_asof(v, sym)
        pe_fy, pe_ttm = price / eps_fy, price / ttm["eps_diluted"]
        assert 5 < pe_fy < 150 and 5 < pe_ttm < 150, (sym, d, pe_fy, pe_ttm)
        # 和「同時期 Yahoo 價格 / 當時公布 EPS」一致(兩邊都換到今天的基準)
        fl = v.facts_long(sym, ["eps_diluted"], period="FY")
        row = fl[fl["period_end"] == a["eps_diluted"].dropna().index[-1]].iloc[-1]
        assert pe_fy == pytest.approx(_yahoo_pe(sym, d, row), rel=0.01)
        pes[d] = pe_ttm
    # 分割前後一個月內,本益比不會差 4、10、20 倍
    assert 0.6 < pes[after] / pes[before] < 1.6, pes


@frozen
def test_restatement_as_of_returns_first_filed_value(built):
    """JNJ FY2021(2022-01-02 結束)營收:2022-02 首次申報 937.75 億;2024-02 年報因 Kenvue 分拆重編成 787.4 億。"""
    store, _ = built
    end = T("2022-01-02")
    v1 = store.as_of("2023-06-30")
    v2 = store.as_of("2024-06-28")
    assert B.annual_table_asof(v1, "JNJ").loc[end, "revenue"] == 93_775_000_000
    assert B.annual_table_asof(v2, "JNJ").loc[end, "revenue"] == 78_740_000_000
    # 舊做法(取最新申報)在任何判斷日都拿到重編後的數字
    assert sec.annual_table(sec.cik_for("JNJ")).loc[end, "revenue"] == 78_740_000_000
    # 一般化:每個「首次申報 ≠ 最新」的年度數字,在首次可用日都拿到首次申報值
    f = store.facts[(store.facts.period == "FY") & ~store.facts.source.str.startswith("sec-derived")]
    g = f.sort_values("available_at").groupby(["entity", "field", "period_end"])
    first, last = g.first(), g.last()
    diff = first[~np.isclose(first["value"], last["value"])]
    assert len(diff) > 20
    for (ent, fld, pe), r in diff.sample(20, random_state=0).iterrows():
        w = store.as_of(r["available_at"]).facts(ent, [fld], period="FY")
        assert w.loc[pe, fld] == r["value"]


@frozen
def test_quarterly_matches_annual(built):
    """單季加總 = 全年(Q4 是衍生值,這裡驗證 Q1–Q3 + Q4 = FY,以及 TTM 在年報公布後 = 全年)。"""
    store, _ = built
    v = store.as_of("2025-06-30")
    for sym in ("AAPL", "MSFT", "NVDA"):
        a = B.annual_table_asof(v, sym)
        fy_end = a.index[-1]
        t = B.ttm_asof(store.as_of(store.facts[(store.facts.entity == sym) & (store.facts.period == "Q4") &
                                               (store.facts.period_end == fy_end)]["available_at"].min()), sym)
        assert t["quarters"][-1] == fy_end.date().isoformat()
        assert t["revenue"] == pytest.approx(a.loc[fy_end, "revenue"], rel=1e-9)
        assert t["cfo"] == pytest.approx(a.loc[fy_end, "cfo"], rel=1e-9)


@frozen
def test_annual_table_asof_matches_legacy_columns(built):
    store, _ = built
    v = store.as_of(config.today())
    a = B.annual_table_asof(v, "AAPL")
    old = sec.annual_table(sec.cik_for("AAPL"))
    assert list(a.columns) == list(old.columns) and a.index.name == old.index.name
    common = a.index.intersection(old.index)[-5:]
    # 最新年度:兩邊都是最新申報;營收、淨利應完全一致
    for c in ("revenue", "net_income", "total_assets"):
        assert a.loc[common, c].tolist() == old.loc[common, c].tolist()


# ---------------- Verifier 修正(第二輪) ----------------


@frozen
@pytest.mark.parametrize("sym,date,ratio", [("DELL", "2021-11-02", 1 / 1.973), ("IBM", "2021-11-04", 1 / 1.046)])
def test_spinoff_compensated_in_total_return_only(built, sym, date, ratio):
    """Yahoo 記成分割的分拆 → kind='spinoff':含息報酬在分拆日不再有沒補償的跌幅,股數基準不受影響。"""
    store, _ = built
    ev = store.events[(store.events.entity == sym) & (store.events.date == T(date))]
    assert ev["kind"].tolist() == ["spinoff"] and ev["ratio"].iloc[0] == pytest.approx(ratio)
    v = store.as_of("2022-06-30")
    tot = v.prices(sym, adjust="total")["close"]
    i = tot.index.get_loc(T(date))
    assert abs(tot.iloc[i] / tot.iloc[i - 1] - 1) < 0.10  # 未補償時 DELL −51%、IBM −4.9%
    assert v.split_factor(sym, "2021-01-01") == 1.0
    # DELL 2018-12-28 換股:之前的價格不收,所以不建事件
    assert not ((store.events.entity == "DELL") & (store.events.date == T("2018-12-28"))).any()


@frozen
def test_avgo_q4_does_not_mix_concepts(built):
    """AVGO FY2018:全年 NetIncomeLoss、2018 Q1 只有 ProfitLoss(含非控制權益)→ 不能相減;
    等 NetIncomeLoss 版本的 Q1 出現後才衍生,Q4 = 11.15 億(= 年報標註值),不是 7.79 億。"""
    store, _ = built
    f = store.facts
    q4 = f[(f.entity == "AVGO") & (f.field == "net_income") & (f.period == "Q4") & (f.period_end == T("2018-11-04"))]
    assert q4["value"].tolist() == [1_115_000_000.0]
    assert q4["source"].str.endswith("NetIncomeLoss").all()


@frozen
def test_cost_ttm_with_16_week_quarter(built):
    """COST 季度 12/12/12/16 週:半年 168 天、9 個月 252 天也要收,第 4 季 112 天也算連續。"""
    store, _ = built
    px = store.prices["COST"]
    idx = px.index[px.index >= T("2017-01-01")]
    months = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).max().tolist()
    none = [d for d in months if B.ttm_asof(store.as_of(d), "COST") is None]
    assert none == []
    t = B.ttm_asof(store.as_of("2024-12-31"), "COST")
    a = B.annual_table_asof(store.as_of("2024-12-31"), "COST")
    assert t["cfo"] is not None and t["revenue"] > a["revenue"].iloc[-1] * 0.95


def _truncate(fj, d):
    """只留 filed < d 的申報(= 判斷日 d 當時 SEC 上看得到的全部)。"""
    out = {"facts": {"us-gaap": {}}}
    for c, node in fj["facts"].get("us-gaap", {}).items():
        out["facts"]["us-gaap"][c] = {"units": {u: [v for v in vs if v.get("filed", "9999") < d]
                                                for u, vs in node.get("units", {}).items()}}
    return out


TRUNC_SYMS = ["KLAC", "MRVL", "AAPL", "NVDA", "COST", "AVGO", "DELL", "MA", "PG", "JNJ"]
TRUNC_DATES = ["2011-01-28", "2016-11-30", "2019-03-29", "2021-08-27", "2024-05-31"]


@frozen
def test_truncated_rebuild_matches_as_of():
    """建置期不能有未來依賴:只用 filed < d 的申報重建,as_of(d) 必須和完整 store 一模一樣
    (Verifier 抓到的 KLAC 2011-01-28、MRVL 2021-08-27 是季別起始日用了未來申報)。"""
    from lab.pit.tamper import _equal
    from lab.sources import yahoo
    cal = B.trading_calendar()
    cols = ["field", "period_end", "period", "value", "available_at"]
    n = 0
    for sym in TRUNC_SYMS:
        fj = sec.companyfacts(sec.cik_for(sym, overrides=True))
        hist = yahoo.history(sym, yahoo.Status())
        ev = B.split_events(sym, hist, yahoo._get(sym, "splits", "info", yahoo.Status(), False))
        px = {sym: B.raw_prices(hist)}
        full = PITStore(B.build_facts(sym, fj, cal, ev)[0], px, ev, "full")
        for d in TRUNC_DATES:
            tr = PITStore(B.build_facts(sym, _truncate(fj, d), cal, ev)[0], px, ev, "trunc")
            a, b = full.as_of(d), tr.as_of(d)
            x, y = a.facts_long(sym), b.facts_long(sym)
            if x.empty and y.empty:
                continue
            n += 1
            x = x[cols].sort_values(cols).reset_index(drop=True)
            y = y[cols].sort_values(cols).reset_index(drop=True)
            for got in (_equal(x, y), _equal(B.annual_table_asof(a, sym), B.annual_table_asof(b, sym)),
                        _equal(B.ttm_asof(a, sym), B.ttm_asof(b, sym))):
                assert got is None, (sym, d, got)
    assert n >= 35


@frozen
@pytest.mark.parametrize("d", ["2019-06-28", "2022-08-01", "2024-06-14"])
def test_ttm_skip_is_previous_four_quarters(built, d):
    """P2-H08:skip = 4 的 TTM 是緊接在最新四季之前的四季。"""
    store, _ = built
    v = store.as_of(d)
    now, prev = B.ttm_asof(v, "AAPL"), B.ttm_asof(v, "AAPL", skip=4)
    assert now and prev
    q = sorted(pd.DatetimeIndex(v.facts_long("AAPL", period="Q")["period_end"].unique()))
    assert [x.date().isoformat() for x in q[-8:-4]] == prev["quarters"]
    assert [x.date().isoformat() for x in q[-4:]] == now["quarters"]


def test_ttm_skip_needs_enough_quarters():
    s, _ = _synthetic()
    assert B.ttm_asof(s.as_of("2022-03-01"), "X") is not None
    assert B.ttm_asof(s.as_of("2022-03-01"), "X", skip=4) is None    # 合成資料只有四季
