"""掃描用的股票池。目前是現有成分股(注意:用現在的名單回測會有存活者偏誤)。"""

TW_LARGE = [
    ("2330.TW", "台積電"), ("2317.TW", "鴻海"), ("2454.TW", "聯發科"), ("2308.TW", "台達電"), ("2382.TW", "廣達"),
    ("3711.TW", "日月光投控"), ("2303.TW", "聯電"), ("2412.TW", "中華電"), ("2881.TW", "富邦金"), ("2882.TW", "國泰金"),
    ("2891.TW", "中信金"), ("2886.TW", "兆豐金"), ("2884.TW", "玉山金"), ("2885.TW", "元大金"), ("5880.TW", "合庫金"),
    ("1303.TW", "南亞"), ("1301.TW", "台塑"), ("6505.TW", "台塑化"), ("2002.TW", "中鋼"), ("2603.TW", "長榮"),
    ("3008.TW", "大立光"), ("2357.TW", "華碩"), ("3034.TW", "聯詠"), ("2379.TW", "瑞昱"), ("3045.TW", "台灣大"),
    ("6669.TW", "緯穎"), ("3231.TW", "緯創"), ("2345.TW", "智邦"), ("3661.TW", "世芯-KY"), ("2327.TW", "國巨"),
    ("2395.TW", "研華"), ("1216.TW", "統一"), ("2912.TW", "統一超"), ("1101.TW", "台泥"), ("2207.TW", "和泰車"),
    ("3017.TW", "奇鋐"), ("2059.TW", "川湖"), ("2408.TW", "南亞科"), ("3443.TW", "創意"), ("5274.TWO", "信驊"),
]

US_LARGE = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"), ("GOOGL", "Alphabet"), ("AMZN", "Amazon"),
    ("META", "Meta"), ("AVGO", "Broadcom"), ("TSLA", "Tesla"), ("BRK-B", "Berkshire"), ("JPM", "JPMorgan"),
    ("V", "Visa"), ("MA", "Mastercard"), ("LLY", "Eli Lilly"), ("UNH", "UnitedHealth"), ("XOM", "Exxon"),
    ("COST", "Costco"), ("WMT", "Walmart"), ("HD", "Home Depot"), ("PG", "P&G"), ("JNJ", "J&J"),
    ("ORCL", "Oracle"), ("NFLX", "Netflix"), ("AMD", "AMD"), ("MU", "Micron"), ("INTC", "Intel"),
    ("QCOM", "Qualcomm"), ("TXN", "Texas Instruments"), ("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
    ("KLAC", "KLA"), ("ASML", "ASML"), ("TSM", "TSMC ADR"), ("ARM", "Arm"), ("MRVL", "Marvell"), ("DELL", "Dell"),
    ("CSCO", "Cisco"), ("IBM", "IBM"), ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("KO", "Coca-Cola"),
]

UNIVERSES = {"tw_large": ("台股權值 40", TW_LARGE), "us_large": ("美股大型 40", US_LARGE)}

# 因子實驗室用的大股票池(只需要股價,不吃 FinMind 額度)
US_100 = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B", "JPM", "V", "MA", "LLY", "UNH", "XOM", "COST",
    "WMT", "HD", "PG", "JNJ", "ORCL", "NFLX", "AMD", "MU", "INTC", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "CSCO", "IBM",
    "CRM", "ADBE", "KO", "PEP", "MRK", "ABBV", "TMO", "ABT", "DHR", "PFE", "AMGN", "GILD", "BMY", "MDT", "ISRG", "CVS",
    "BAC", "WFC", "C", "GS", "MS", "SCHW", "AXP", "BLK", "SPGI", "CB", "PGR", "CAT", "DE", "HON", "GE", "RTX", "LMT",
    "BA", "UPS", "UNP", "ADP", "LIN", "NEE", "DUK", "SO", "T", "VZ", "TMUS", "CMCSA", "DIS", "MCD", "SBUX", "NKE", "LOW",
    "TGT", "BKNG", "CVX", "COP", "SLB", "EOG", "PM", "MO", "MDLZ", "CL", "INTU", "NOW", "PANW", "AMT", "PLD", "ACN", "ADI",
]


def tw_liquid(n: int = 150) -> list[tuple[str, str]]:
    """台股上市成交金額前 n 大(排除 ETF、權證)。注意:用今天的成交金額挑,有存活者偏誤。"""
    import re

    from .sources import twse
    tv = twse.trade_values()
    codes = [c for c, v in sorted(tv.items(), key=lambda kv: -(kv[1] or 0))
             if re.fullmatch(r"[1-9]\d{3}", c or "")]
    return [(f"{c}.TW", c) for c in codes[:n]]


