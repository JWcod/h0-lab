"""Point-in-time(PIT)資料層:所有特徵只能透過 `store.as_of(d)` 拿資料(共用介面,只有維護者改)。

三種資料:
- facts:財報與其他「有公布日」的數字(長表)。欄位:
    entity(代號)、field(標準欄位名,例 revenue)、period_end(期末日)、period(FY / Q1..Q4 / M01..M12)、
    value(數值)、available_at(最早可以用在決策的那一天:公布日 +1 交易日或法定期限 +1 交易日)、
    source、filed(實際申報日,沒有就 NaT)、basis_at(股數基準日:每股數字與股數是以哪一天的股數基準表示;
    換到判斷日 d 一律用 `view.split_factor(entity, basis_at)`。美股 = filed(原始申報值);台股 = available_at
    (建置時已正規化,原始值另存 eps_as_reported 等欄)。沒填時預設 filed,再沒有就 available_at)。
  同一個 (entity, field, period_end, period) 可以有多筆(原始申報、之後的重編);as_of(d) 只取 available_at ≤ d 中最新的一筆。
- prices:每個代號一張日表(index = 交易日),**原始未調整**的 open / high / low / close / volume,以及 cash_div(當天除息的現金股利,沒有就 0)。
- events:公司行動。欄位:entity、date(除權 / 生效日)、kind、ratio。
  - kind = split / stock_div / reduction:ratio = 每 1 股變成幾股(1 拆 4 = 4.0、配股 0.5 元面額 10 元 = 1.05、減資 3 成 = 0.7)。
    影響股數基準(split_factor)與 'split' / 'total' 價格。
  - kind = spinoff(分拆):ratio = 分拆日前價格要乘上的倍數(= 1 − 分拆出去的價值 ÷ 前一日收盤,例 IBM 2021 約 0.96)。
    股數不變,所以不影響 split_factor 與 'split' 價格;只在 'total' 報酬裡補償分拆當天的跌價。
  as_of(d) 只套用 date ≤ d 的事件,所以不會把之後才發生的分割 / 配股往回套到過去的價格(Verifier 發現的問題)。

決策慣例:在 d 收盤後做決策,只能用 available_at ≤ d 的資料;成交在下一個交易日(由回測框架處理)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

FACT_COLS = ["entity", "field", "period_end", "period", "value", "available_at", "source", "filed", "basis_at"]
_DATE_COLS = ("period_end", "available_at", "filed", "basis_at")
EVENT_COLS = ["entity", "date", "kind", "ratio"]
PRICE_COLS = ["open", "high", "low", "close", "volume", "cash_div"]


def _empty_facts() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c in _DATE_COLS else
                                      float if c == "value" else object) for c in FACT_COLS})


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame({"entity": pd.Series(dtype=object), "date": pd.Series(dtype="datetime64[ns]"),
                         "kind": pd.Series(dtype=object), "ratio": pd.Series(dtype=float)})


@dataclass
class PITStore:
    facts: pd.DataFrame = field(default_factory=_empty_facts)
    prices: dict[str, pd.DataFrame] = field(default_factory=dict)
    events: pd.DataFrame = field(default_factory=_empty_events)
    vintage: str = ""

    def __post_init__(self):
        f = self.facts
        if "filed" not in f:
            f["filed"] = pd.NaT
        if "basis_at" not in f:
            f["basis_at"] = pd.to_datetime(f["filed"]).fillna(pd.to_datetime(f["available_at"]))
        else:
            f["basis_at"] = pd.to_datetime(f["basis_at"]).fillna(pd.to_datetime(f["filed"])).fillna(
                pd.to_datetime(f["available_at"]))

    def as_of(self, d) -> "PITView":
        d = pd.Timestamp(d)
        facts = self.facts[self.facts["available_at"] <= d].copy()
        events = self.events[self.events["date"] <= d].copy()
        prices = {entity: frame.loc[frame.index <= d].copy() for entity, frame in self.prices.items()}
        return PITView(facts, prices, events, d)

    # ---------- 存取 ----------
    def save(self, path: str | Path) -> None:
        """存成 SQLite(標準函式庫,不需要額外套件)。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.sqlite")
        if tmp.exists():
            tmp.unlink()
        con = sqlite3.connect(tmp)
        try:
            f = self.facts.copy()
            for c in _DATE_COLS:
                f[c] = pd.to_datetime(f[c]).dt.strftime("%Y-%m-%d")
            f.to_sql("facts", con, index=False)
            e = self.events.copy()
            e["date"] = pd.to_datetime(e["date"]).dt.strftime("%Y-%m-%d")
            e.to_sql("events", con, index=False)
            rows = []
            for ent, df in self.prices.items():
                x = df.reindex(columns=PRICE_COLS).copy()
                x.index = pd.DatetimeIndex(x.index).strftime("%Y-%m-%d")
                x = x.rename_axis("date").reset_index()
                x.insert(0, "entity", ent)
                rows.append(x)
            (pd.concat(rows) if rows else pd.DataFrame(columns=["entity", "date"] + PRICE_COLS)).to_sql("prices", con, index=False)
            pd.DataFrame({"key": ["vintage"], "value": [self.vintage]}).to_sql("meta", con, index=False)
            con.execute("CREATE INDEX ix_facts ON facts(entity, field)")
            con.execute("CREATE INDEX ix_prices ON prices(entity, date)")
            con.commit()
        finally:
            con.close()
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path, entities: list[str] | None = None) -> "PITStore":
        con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            where, args = "", []
            if entities:
                where = f" WHERE entity IN ({','.join('?' * len(entities))})"
                args = list(entities)
            f = pd.read_sql(f"SELECT * FROM facts{where}", con, params=args)
            for c in _DATE_COLS:
                if c in f:
                    f[c] = pd.to_datetime(f[c])
            e = pd.read_sql(f"SELECT * FROM events{where}", con, params=args)
            e["date"] = pd.to_datetime(e["date"])
            p = pd.read_sql(f"SELECT * FROM prices{where}", con, params=args)
            prices = {}
            for ent, g in p.groupby("entity"):
                prices[ent] = g.drop(columns=["entity", "date"]).set_index(pd.DatetimeIndex(pd.to_datetime(g["date"]))).sort_index().rename_axis(None)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        finally:
            con.close()
        return cls(f, prices, e, meta.get("vintage", ""))


class PITView:
    """某一天收盤後「看得到」的資料。特徵函式只拿得到這個物件。"""

    def __init__(self, facts: pd.DataFrame, prices: dict[str, pd.DataFrame], events: pd.DataFrame, d: pd.Timestamp):
        """只保留判斷日可見的資料，不能從 view 取回完整 PITStore。"""
        self._facts = facts
        self._prices = prices
        self._events = events
        self.d = d

    def facts(self, entity: str, fields: list[str] | None = None, period: str | None = None) -> pd.DataFrame:
        """寬表:index = period_end,columns = field。每格是 d 當時看得到的最新版本(含當時已知的重編)。

        period:'FY' 只取年度、'Q' 只取單季(Q1..Q4)、'M' 只取月資料;None 全部。
        """
        f = self._facts
        m = f["entity"] == entity
        if fields:
            m &= f["field"].isin(fields)
        if period:
            m &= f["period"].astype(str).str.startswith(period) if period != "FY" else f["period"].eq("FY")
        x = f[m]
        if x.empty:
            return pd.DataFrame(columns=fields or [])
        x = x.sort_values("available_at").drop_duplicates(["field", "period_end", "period"], keep="last")
        w = x.pivot_table(index="period_end", columns="field", values="value", aggfunc="last").sort_index()
        return w.reindex(columns=fields) if fields else w

    def facts_long(self, entity: str, fields: list[str] | None = None, period: str | None = None) -> pd.DataFrame:
        """和 facts() 同樣的版本選擇,但回傳長表(含 available_at、filed、source),給需要知道「這個數字何時公布」的轉換用
        (例如用 split_factor 把每股數字換到 d 的股數基準)。"""
        f = self._facts
        m = f["entity"] == entity
        if fields:
            m &= f["field"].isin(fields)
        if period:
            m &= f["period"].astype(str).str.startswith(period) if period != "FY" else f["period"].eq("FY")
        x = f[m].sort_values("available_at").drop_duplicates(["field", "period_end", "period"], keep="last")
        return x.sort_values(["field", "period_end"]).reset_index(drop=True)

    def first_available(self, entity: str, field: str) -> pd.Series:
        """每個期間最早可用的日期(index = period_end)。給「財報剛公布」這類事件特徵用。"""
        f = self._facts
        x = f[(f["entity"] == entity) & (f["field"] == field)]
        return x.groupby("period_end")["available_at"].min().sort_index()

    def events(self, entity: str) -> pd.DataFrame:
        e = self._events
        return e[e["entity"] == entity].sort_values("date")

    def split_factor(self, entity: str, after) -> float:
        """after(不含)到 d(含)之間所有分割 / 配股 / 減資的累積股數倍數。

        用途:把「當時公布」的每股數字(EPS、每股淨值、股數)換到 d 當時的股數基準,和 prices(adjust='split') 一致:
            eps_on_d_basis = eps / view.split_factor(entity, basis_at)
            shares_on_d_basis = shares * view.split_factor(entity, basis_at)
        after 一律用該數字的 basis_at(facts_long 會給;規約見 docs/架構.md)。不要自己用 filed 補 available_at。
        (同一期數字若在分割後重新申報,as_of 會取到新版本,其 available_at 在分割之後,倍數自然是 1,不會重複調整。)
        """
        after = pd.Timestamp(after)
        e = self.events(entity)
        r = e[(e["date"] > after) & (e["kind"] != "spinoff")]["ratio"].astype(float)
        r = r[(r > 0) & np.isfinite(r)]
        return float(r.prod()) if len(r) else 1.0

    def prices(self, entity: str, adjust: str = "split", lookback_days: int | None = None) -> pd.DataFrame:
        """d 以前的日價格。

        adjust:
        - 'none':原始價格(當天實際成交價)
        - 'split':只用 d 以前發生的分割 / 配股 / 減資,把更早的價格換算成 d 當時的股數基準
          (和 d 當時公布的每股數字一致;不會套用 d 之後才發生的事件)
        - 'total':在 'split' 之上再把 d 以前的現金股利再投入、補償分拆(算報酬用)
        """
        p = self._prices.get(entity)
        if p is None:
            return pd.DataFrame(columns=PRICE_COLS)
        if lookback_days:
            p = p[p.index > self.d - pd.Timedelta(days=lookback_days)]
        if adjust == "none" or p.empty:
            return p.copy()
        out = p.copy()
        factor = pd.Series(1.0, index=out.index)
        evs = self.events(entity)
        for _, ev in evs[evs["kind"] != "spinoff"].iterrows():
            r = float(ev["ratio"])
            if r > 0 and np.isfinite(r):
                factor[out.index < ev["date"]] /= r
        for c in ("open", "high", "low", "close"):
            if c in out:
                out[c] = out[c] * factor
        if "volume" in out:
            out["volume"] = out["volume"] / factor
        if adjust == "total" and "cash_div" in out:
            div = (out["cash_div"].fillna(0) * factor).to_numpy()
            close = out["close"].to_numpy()
            # 除息日當天價格掉下來;把除息日以前的價格乘上(除息前收盤 − 股利)/ 除息前收盤(標準還原法)。
            # 除息前收盤用「前一個有效收盤」(前一列缺值時不能漏掉股利);股利 ≥ 前收盤屬資料異常(例如減資退款誤記),忽略並計數
            prev_valid = pd.Series(close).shift(1).ffill().to_numpy()
            adj = np.ones(len(close))
            run = 1.0
            self.bad_dividends = getattr(self, "bad_dividends", 0)
            for i in range(len(close) - 1, -1, -1):
                adj[i] = run
                pc = prev_valid[i] if i > 0 else np.nan
                if div[i] > 0 and pc > 0:
                    if div[i] < pc:
                        run *= (pc - div[i]) / pc
                    else:
                        self.bad_dividends += 1
            for _, ev in evs[evs["kind"] == "spinoff"].iterrows():
                r = float(ev["ratio"])
                if 0 < r < 1:
                    adj[out.index < ev["date"]] *= r
            for c in ("open", "high", "low", "close"):
                out[c] = out[c] * adj
        return out

    def calendar(self, entity: str) -> pd.DatetimeIndex:
        p = self._prices.get(entity)
        return pd.DatetimeIndex([]) if p is None else p.index
