"""RedTeam2 問題 1:竄改測試只能抓 view 存取層的錯誤,不經過 view 的偷看它抓不到。
這裡用靜態檢查擋住判斷路徑(判斷日 d 的決策會執行到的函式)裡最常見的繞過寫法。"""
import inspect

import pytest

from lab.analysis import chips, fundamentals, income_risk, regime, scoring, technical
from lab.analysis import financials as FN
from lab.pit import build_chips
from lab.research import backtest, backtest_pit
from lab.sources import macro_rates
from lab.valuation import dcf
from lab.valuation import model as VM

# 判斷路徑上的函式與模組(建置期的函式,例如 build_chips._raw、macro_rates.build,不在這裡:它們只在建資料版本時跑)
FUNCS = [backtest.judge_at, backtest._us_ttm_row, backtest_pit.judge_pit, backtest_pit.macro_asof, backtest_pit.us_fdat_asof,
         backtest_pit.fixed_quantile_levels, backtest_pit.cross_sectional_rescore, backtest_pit.industry_neutral_valuation,
         build_chips.fm_asof, macro_rates.rates_asof, chips.tw]
MODULES = [scoring, fundamentals, technical, regime, FN, VM, dcf, income_risk]
FORBIDDEN = ["PITStore.load", "cached(", "today(", "import inspect", "inspect.", "requests.", "import yfinance", "yf.", "open(",
             "read_csv(", "read_sql", "_MACRO_CACHE", "global "]


@pytest.mark.parametrize("obj", FUNCS + MODULES, ids=lambda o: getattr(o, "__qualname__", getattr(o, "__name__", str(o))))
def test_no_out_of_view_data_access(obj):
    src = inspect.getsource(obj)
    hits = [f for f in FORBIDDEN if f in src]
    assert not hits, f"{getattr(obj, '__qualname__', obj)} 出現不經過 view 的資料存取:{hits}"
