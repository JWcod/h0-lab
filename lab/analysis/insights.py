"""見解引擎:讀完所有資料後,用規則產生「有證據、有理由」的觀察。

每條見解:面向、方向(pos 正面 / neg 負面 / watch 觀察)、重要性(1–3)、標題、證據、為什麼重要。
規則是透明的,之後可以依復盤結果增減或調整門檻。
"""
from __future__ import annotations


def _p(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def generate(ctx: dict) -> list[dict]:
    out: list[dict] = []

    def add(cat, tone, imp, title, detail, why=""):
        out.append({"category": cat, "tone": tone, "importance": imp, "title": title, "detail": detail, "why": why})

    f = ctx["fund"]
    prof, grw, hl, eff, sh = f["profitability"], f["growth"], f["health"], f.get("efficiency", {}), f.get("shareholder", {})
    val, tech, chips, exp = ctx["valuation"], ctx["technical"], ctx["chips"], ctx["expectations"]
    news, regime, mr = ctx["news_summary"], ctx["regime"], ctx.get("month_revenue")
    cur = ctx.get("currency") or ""

    # ---------- 估值 ----------
    if val.get("fair_value"):
        up = val["upside"]
        tone = "pos" if up > val["margin_of_safety"] / 2 else "neg" if up < -val["margin_of_safety"] / 2 else "watch"
        add("估值", tone, 3, f"模型估值 {val['fair_value']:,.2f} {cur}({up:+.1%})",
            f"區間 {val['fair_low']:,.2f} – {val['fair_high']:,.2f};不確定性「{val['uncertainty']}」,模型使用安全邊際 {val['margin_of_safety']:.0%}。",
            "公平價由多種方法加權,單一方法誤差大,看區間比看單點重要")
    cyc = val.get("cyclical") or {}
    if cyc.get("cyclical"):
        now, mean = cyc.get("om_now"), cyc.get("om_mean")
        pos = "高於" if now is not None and mean is not None and now > mean else "低於"
        add("風險", "watch", 3, "景氣循環股:要看整個循環,不要只看今年",
            f"{cyc.get('reason')}。目前營業利益率 {_p(now)},{pos}長期平均 {_p(mean)}。模型已改用循環平均利潤率估值、不採用本益比河流。",
            "利潤率在循環高峰時本益比看起來偏低,谷底時本益比偏高;單看本益比容易誤判獲利所處的循環位置")
    w = val.get("wacc") or {}
    if w.get("rate_basis") == "美元利率" and (w.get("rf") or 0) >= 0.045 and val.get("company_type") in ("mature", "financial"):
        add("總經", "watch", 2, f"美國 10 年債殖利率 {_p(w['rf'], 2)}:高利率壓低成熟股的合理價",
            f"股權成本 {_p(w.get('cost_of_equity'), 1)}。低成長、高本益比的公司在高利率環境下,DCF 價值會明顯低於市價。",
            "如果你認為利率會回落,可以在敏感度表看 WACC 低 1–2% 時的價值")
    d = val.get("dcf") or {}
    ig = d.get("implied_growth")
    if ig is not None:
        hist_g = grw.get("revenue_cagr_3y")
        fwd = exp.get("fwd_rev_growth_fy1") or exp.get("fwd_rev_growth_fy0")
        ref = fwd if fwd is not None else hist_g
        txt = f"以目前股價反推,市場預期未來營收起始成長約 {_p(ig)}(之後 10 年線性降到永續成長)。"
        if hist_g is not None:
            txt += f"過去 3 年營收 CAGR {_p(hist_g)}"
        if fwd is not None:
            txt += f";分析師預估 {_p(fwd)}"
        txt += "。"
        if ref is not None:
            tone = "pos" if ig < ref - 0.03 else "neg" if ig > ref + 0.05 else "watch"
            add("估值", tone, 3, "反向 DCF:市場正在定價的成長率", txt,
                "比較隱含成長率與實際成長率,可以看出目前價格已反映多少成長預期")
    if d.get("tv_share") and d["tv_share"] > 0.8:
        add("估值", "watch", 1, "DCF 價值高度依賴永續期", f"終值占企業價值 {_p(d['tv_share'])},對折現率和永續成長率非常敏感。",
            "請搭配敏感度表一起看")
    pb = val.get("pe_band") or {}
    if pb.get("current_pct_rank") is not None:
        r = pb["current_pct_rank"]
        if r >= 0.85:
            add("估值", "neg", 2, "本益比在自身 5 年高檔", f"目前本益比 {pb['current_pe']:.1f} 倍,高於過去 5 年 {r:.0%} 的時間。",
                "估值在歷史高檔時,後續要靠獲利成長消化")
        elif r <= 0.2:
            add("估值", "pos", 2, "本益比在自身 5 年低檔", f"目前本益比 {pb['current_pe']:.1f} 倍,只高於過去 5 年 {r:.0%} 的時間。",
                "低檔本益比也可能反映基本面惡化,需要查證是否為價值陷阱")
    epv = val.get("epv")
    if epv and epv.get("growth_premium") is not None:
        gp = epv["growth_premium"]
        if gp > 1.0:
            add("估值", "watch", 1, "股價大部分是「成長的價格」",
                f"零成長的獲利能力價值(EPV)約 {epv['value']:,.2f},現價比它高 {gp:.0%},代表市場付了很多錢買未來成長。",
                "成長不如預期時,這部分價格的波動風險較高")
        elif gp < 0.1:
            add("估值", "pos", 2, "股價接近零成長價值", f"EPV 約 {epv['value']:,.2f},現價只比它高 {gp:+.0%}。",
                "現價幾乎沒有反映成長預期")
    tp = val.get("touch_prob_12m")
    if tp is not None and val.get("fair_value"):
        add("估值", "watch", 1, f"12 個月內觸及公平價的機率約 {tp:.0%}",
            f"以年化波動 {_p(tech.get('vol_1y'))}、零漂移的對數常態模型估算(反射原理)。", "這是純統計機率,沒有考慮基本面方向")

    # ---------- 獲利品質 ----------
    if prof.get("roic") is not None:
        if prof["roic"] >= 0.2:
            add("財務", "pos", 2, f"ROIC {_p(prof['roic'])}:資本效率很高", "每投入 100 元資本,一年賺回超過 20 元稅後營業利益。",
                "高 ROIC 且能持續,通常代表有競爭優勢(護城河)")
        elif prof["roic"] < 0.06:
            add("財務", "neg", 2, f"ROIC 只有 {_p(prof['roic'])}", "資本報酬率可能低於資金成本。", "ROIC < WACC 時,成長反而會摧毀價值")
    cfo_ni = prof.get("cfo_to_ni")
    if cfo_ni is not None:
        if cfo_ni < 0.8:
            add("財務", "neg", 2, "獲利沒有完全轉成現金", f"營業現金流只有淨利的 {cfo_ni:.2f} 倍。",
                "Sloan (1996):應計項目高的公司,後續報酬偏弱;要檢查應收帳款或存貨是否堆積")
        elif cfo_ni > 1.2:
            add("財務", "pos", 1, "現金流品質佳", f"營業現金流是淨利的 {cfo_ni:.2f} 倍。", "獲利有實際現金支持")
    if grw.get("gm_streak_q") is not None:
        s = grw["gm_streak_q"]
        if s >= 3:
            add("財務", "pos", 2, f"毛利率連續 {s} 季上升", "產品組合或定價能力在改善。", "毛利率趨勢常領先獲利修正")
        elif s <= -3:
            add("財務", "neg", 2, f"毛利率連續 {-s} 季下滑", "可能面臨價格競爭、成本上升或產品組合變差。", "毛利率惡化常領先獲利下修")
    if grw.get("q_gm_change_yoy") is not None and abs(grw["q_gm_change_yoy"]) >= 0.03:
        c = grw["q_gm_change_yoy"]
        add("財務", "pos" if c > 0 else "neg", 1, f"單季毛利率年變化 {c * 100:+.1f} 個百分點", "與去年同季相比。", "")
    rg = eff.get("receivables_growth_minus_revenue")
    if rg is not None and rg > 0.15:
        add("風險", "neg", 2, "應收帳款成長遠快於營收", f"應收帳款成長比營收成長多 {_p(rg)}。",
            "可能是放寬收款條件衝營收,Beneish 模型的 DSRI 就是在抓這個")
    ig2 = eff.get("inventory_growth_minus_revenue")
    if ig2 is not None and ig2 > 0.15:
        add("風險", "neg", 2, "存貨成長遠快於營收", f"存貨成長比營收成長多 {_p(ig2)}。",
            "半導體與電子業常見的景氣反轉前兆:需求放緩但還在生產")
    elif ig2 is not None and ig2 < -0.15:
        add("財務", "pos", 1, "存貨去化明顯", f"存貨成長比營收成長少 {_p(-ig2)}。", "庫存調整接近尾聲時,常是景氣回升的訊號")

    # ---------- 成長 ----------
    if grw.get("revenue_yoy") is not None:
        y = grw["revenue_yoy"]
        if y > 0.25:
            add("成長", "pos", 2, f"營收高速成長 {_p(y)}", f"基準:{grw.get('yoy_basis')}。", "")
        elif y < -0.05:
            add("成長", "neg", 2, f"營收衰退 {_p(y)}", f"基準:{grw.get('yoy_basis')}。", "")
    if mr:
        if mr.get("record_high"):
            add("成長", "pos", 3, f"{mr['latest_month']} 月營收創歷史新高", f"單月營收年增 {_p(mr.get('yoy'))},近 3 月年增 {_p(mr.get('yoy_3m'))}。",
                "部分台灣研究發現營收創新高與之後報酬相關(效期近年縮短);本平台未驗證")
        elif mr.get("record_same_month"):
            add("成長", "pos", 2, f"{mr['latest_month']} 營收為歷年同月新高", f"年增 {_p(mr.get('yoy'))}。", "排除季節性後仍在成長")
        if mr.get("yoy_streak") is not None:
            s = mr["yoy_streak"]
            if s >= 6:
                add("成長", "pos", 1, f"月營收連續 {s} 個月年增", "", "")
            elif s <= -3:
                add("成長", "neg", 2, f"月營收連續 {-s} 個月年減", "", "營收動能轉負時,股價常先反映")
        if mr.get("momentum_3_12") is not None:
            m312 = mr["momentum_3_12"]
            if m312 > 1.15:
                add("成長", "pos", 1, "近 3 月營收明顯高於年平均", f"近 3 月平均是近 12 月平均的 {m312:.2f} 倍。", "營收動能在加速")
            elif m312 < 0.85:
                add("成長", "neg", 1, "近 3 月營收低於年平均", f"近 3 月平均是近 12 月平均的 {m312:.2f} 倍。", "營收動能在減速(注意季節性)")

    # ---------- 財務安全 ----------
    z = hl.get("altman_z")
    if z is not None and val.get("company_type") != "financial":
        if z < 1.81:
            add("風險", "neg", 3, f"Altman Z = {z:.2f}:落在財務危機區", "< 1.81 歷史上破產機率明顯較高。", "原始模型針對製造業;科技/輕資產公司參考即可")
        elif z > 2.99:
            add("財務", "pos", 1, f"Altman Z = {z:.2f}:財務安全區", "", "")
    pf = f.get("piotroski")
    if pf:
        if pf["score"] >= 7:
            add("財務", "pos", 2, f"Piotroski F-score {pf['score']}/9:基本面持續改善", "9 項檢查大多通過。", "Piotroski (2000) 用這 9 項檢查衡量基本面變化;本平台未驗證其預測力")
        elif pf["score"] <= 3:
            add("財務", "neg", 2, f"Piotroski F-score {pf['score']}/9:基本面轉弱", "", "")
    bm = f.get("beneish")
    if bm and bm.get("flag"):
        add("風險", "neg" if not bm.get("high_growth_caveat") else "watch", 2, f"Beneish M = {bm['m']:.2f} 高於 −1.78",
            "財報操縱模型亮燈。" + ("但公司營收成長很快(SGI 高),此模型對高成長公司容易誤判。" if bm.get("high_growth_caveat") else "建議細看應收、應計與折舊政策。"),
            "Beneish (1999):8 個變數組合,抓到不少財報舞弊案例")
    if hl.get("net_cash_to_mcap") is not None and hl["net_cash_to_mcap"] > 0.2:
        add("財務", "pos", 1, f"淨現金占市值 {_p(hl['net_cash_to_mcap'])}", "資產負債表的現金比重較高。", "")
    nde = hl.get("net_debt_ebitda")
    if nde is not None and nde > 3.5:
        add("風險", "neg", 2, f"淨負債是 EBITDA 的 {nde:.1f} 倍", "槓桿偏高。", "升息或獲利下滑時,高槓桿公司壓力較大")

    # ---------- 股東回饋 ----------
    if sh.get("share_change_yoy") is not None:
        c = sh["share_change_yoy"]
        if c < -0.02:
            add("財務", "pos", 1, f"流通股數一年減少 {_p(-c)}", "公司在買回庫藏股。", "股數減少讓每股價值提升")
        elif c > 0.03:
            add("風險", "neg", 1, f"流通股數一年增加 {_p(c)}", "股權稀釋(增資或員工股票酬勞)。", "")
    dy = (ctx.get("multiples") or {}).get("dividend_yield")
    if dy is not None and dy >= 0.04:
        ok = sh.get("fcf_payout") is not None and sh["fcf_payout"] < 0.8
        add("財務", "pos" if ok else "watch", 2, f"殖利率 {_p(dy, 2)}" + ("(現金流足以支應)" if ok else ""),
            f"股利 / 自由現金流 = {_p(sh.get('fcf_payout'), 0)}。", "高殖利率要確認是不是來自股價大跌,以及股利能不能持續")
    exd = ctx.get("ex_dividend_date")
    if exd:
        add("財務", "watch", 1, f"下次除息日 {exd}", "除息當天股價會扣掉股利。", "")
    if sh.get("fcf_payout") is not None and sh["fcf_payout"] > 1.0:
        add("風險", "watch", 1, "股利超過自由現金流", f"股利 / 自由現金流 = {sh['fcf_payout']:.2f}。", "長期無法持續,除非現金流回升")

    # ---------- 技術 ----------
    if tech.get("available"):
        tr = tech.get("trend", {})
        add("技術", "pos" if tr.get("code", 0) > 0 else "neg" if tr.get("code", 0) < 0 else "watch", 2,
            f"趨勢:{tr.get('label')}",
            f"股價相對 200 日線 {_p(tech['price_vs_ma'].get('ma200'))},200 日線 20 日斜率 {_p(tech.get('ma200_slope_20d'), 2)}。",
            "文獻稱為時間序列動能;本平台未證實其預測力")
        m121 = tech.get("mom_12_1")
        if m121 is not None and abs(m121) > 0.3:
            add("技術", "pos" if m121 > 0 else "neg", 1, f"12-1 個月動能 {_p(m121)}", "", "橫斷面動能是學術上最穩健的因子之一")
        rel = (tech.get("relative") or {}).get("3m")
        if rel is not None and abs(rel) > 0.1:
            add("技術", "pos" if rel > 0 else "neg", 1, f"近 3 個月{'強於' if rel > 0 else '弱於'}大盤 {abs(rel) * 100:.1f} 個百分點", "", "")
        bt = (tech.get("backtest") or {}).get("conditional") or {}
        if bt:
            cur_state = "在 200 日線上" if (tech["price_vs_ma"].get("ma200") or 0) > 0 else "在 200 日線下"
            c = bt.get(cur_state)
            if c and c.get("n", 0) > 200:
                add("技術", "watch", 1, f"歷史上「{cur_state}」時,之後 60 日平均報酬 {_p(c['avg_fwd60'])}",
                    f"勝率 {c['win_rate']:.0%},樣本 {c['n']} 天(這檔股票自己的 10 年歷史)。", "單一股票樣本內統計,只能當參考")

    # ---------- 籌碼 ----------
    if chips.get("market") == "TW":
        inst = chips.get("institutional") or {}
        fi, it = inst.get("外資", {}), inst.get("投信", {})
        if fi.get("streak") is not None and abs(fi["streak"]) >= 5:
            s = fi["streak"]
            add("籌碼", "pos" if s > 0 else "neg", 2, f"外資連續 {abs(s)} 日{'買超' if s > 0 else '賣超'}",
                f"近 20 日合計 {fi.get('d20', 0):+,.0f} 張({_p(fi.get('d20_pct'), 2)} 股本)。", "台灣研究:外資買賣超對大型股有短期影響,效果約數日")
        if it.get("streak") is not None and abs(it["streak"]) >= 4:
            s = it["streak"]
            add("籌碼", "pos" if s > 0 else "neg", 2, f"投信連續 {abs(s)} 日{'買超' if s > 0 else '賣超'}",
                f"近 20 日合計 {it.get('d20', 0):+,.0f} 張。", "投信對中小型股影響較大;季底常有作帳行情")
        mg = chips.get("margin") or {}
        if mg.get("margin_chg_20d") is not None and mg["margin_chg_20d"] > 0.15:
            add("籌碼", "neg", 1, f"融資 20 日增加 {_p(mg['margin_chg_20d'])}", "融資餘額快速增加。", "融資快速增加代表槓桿部位上升")
        if mg.get("short_margin_ratio") is not None and mg["short_margin_ratio"] > 0.3:
            add("籌碼", "watch", 1, f"券資比 {_p(mg['short_margin_ratio'])}", "融券相對融資偏高。", "股價上漲時可能出現軋空")
        fh = chips.get("foreign_holding") or {}
        if fh.get("chg_60d") is not None and abs(fh["chg_60d"]) >= 1.5:
            add("籌碼", "pos" if fh["chg_60d"] > 0 else "neg", 2, f"外資持股比 60 日{'增加' if fh['chg_60d'] > 0 else '減少'} {abs(fh['chg_60d']):.1f} 個百分點",
                f"目前 {fh.get('ratio'):.2f}%。", "")
        td = chips.get("tdcc") or {}
        if td.get("big1000_chg") is not None and abs(td["big1000_chg"]) >= 0.5:
            c = td["big1000_chg"]
            add("籌碼", "pos" if c > 0 else "neg", 1, f"千張大戶持股比週變化 {c:+.2f} 個百分點", f"目前 {td.get('big1000_pct')}%。", "大戶增加、散戶減少 = 籌碼集中")
    else:
        ins = chips.get("insiders") or {}
        if ins.get("buy_count_6m", 0) >= 1:
            add("籌碼", "pos", 2, f"內部人 6 個月內公開市場買入 {ins['buy_count_6m']} 筆", f"買入者:{', '.join(ins.get('buyers', [])[:4])}。",
                "Cohen, Malloy & Pomorski (2012) 區分例行與非例行的內部人交易;本平台未驗證")
        sh_ = chips.get("short") or {}
        if sh_.get("pct_float") is not None and sh_["pct_float"] > 0.1:
            add("籌碼", "neg", 2, f"放空比例偏高:{_p(sh_['pct_float'])} 流通股", f"回補天數 {sh_.get('days_to_cover')}。",
                "放空比例反映市場部分參與者的看法;比例極高時價格波動也可能放大")

    # ---------- 預期 ----------
    et = (exp.get("eps_trend") or {}).get("0y") or {}
    if et.get("chg_90d") is not None and abs(et["chg_90d"]) > 0.03:
        c = et["chg_90d"]
        add("預期", "pos" if c > 0 else "neg", 3, f"今年 EPS 預估 90 天{'上修' if c > 0 else '下修'} {_p(abs(c))}",
            f"目前共識 {et.get('current'):.2f}。", "盈餘預估修正是常見的研究因子;本平台未驗證其預測力")
    if exp.get("beats_4q") is not None and exp.get("surprise_avg_4q") is not None:
        b = exp["beats_4q"]
        if b >= 4:
            add("預期", "pos", 1, "連續 4 季財報優於預期", f"平均驚喜 {_p(exp['surprise_avg_4q'])}。", "")
        elif b <= 1:
            add("預期", "neg", 2, f"近 4 季只有 {b} 季優於預期", "", "")
    tg = exp.get("targets") or {}
    if tg.get("implied_upside") is not None and tg.get("n"):
        add("預期", "watch", 1, f"分析師目標價中位數隱含 {_p(tg['implied_upside'])} 空間",
            f"{int(tg['n'])} 位分析師,區間 {tg.get('low')} – {tg.get('high')}。",
            "Bradshaw 等 (2013):12 個月後只有約 38% 的目標價達成,平均高估約 15%,本模型已打折使用")
    em = exp.get("earnings_moves")
    if exp.get("days_to_earnings") is not None and 0 <= exp["days_to_earnings"] <= 14:
        dte = exp["days_to_earnings"]
        hist_txt = (f"過去 {em['n']} 次財報前後平均波動 ±{_p(em['avg_abs'], 1)}(最大 {_p(em['max_abs'], 1)},上漲 {em['ups']} 次)。"
                    if em else "")
        add("風險", "watch", 2, ("今天" if dte == 0 else "明天" if dte == 1 else f"{dte} 天後") + "公布財報",
            f"日期 {exp.get('next_earnings')}。" + hist_txt,
            "財報前後波動通常放大,價格可能跳空")

    # ---------- 消息 ----------
    d7 = news.get("d7") or {}
    if (d7.get("n") or 0) >= 5 and d7.get("avg") is not None and abs(d7["avg"]) >= 0.2:
        add("消息", "pos" if d7["avg"] > 0 else "neg", 1, f"近 7 日新聞情緒偏{'正面' if d7['avg'] > 0 else '負面'}",
            f"{d7['n']} 則新聞,正面 {d7['pos']}、負面 {d7['neg']}(詞典法)。", "詞典法準確度有限,重要新聞請點開原文")
    for a in (ctx.get("announcements") or [])[:3]:
        tags = a.get("tags") or []
        tone = "neg" if "風險事件" in tags else "watch"
        add("消息", tone, 2 if tags else 1, f"重大訊息({a.get('date')}):{a.get('subject', '')[:60]}",
            "公開資訊觀測站官方公告。" + ("類別:" + "、".join(tags) if tags else ""), "官方第一手資訊,比新聞早也比新聞準")
    ev = news.get("events") or {}
    hot = [k for k, v in sorted(ev.items(), key=lambda t: -t[1]) if v >= 3][:3]
    if hot:
        add("消息", "watch", 1, "近期新聞焦點:" + "、".join(hot), "依新聞標題關鍵字分類。", "")

    # ---------- 大盤 ----------
    mp = regime.get("market_pe")
    if mp and mp["label"] == "偏高":
        add("總經", "watch", 2, f"大盤本益比 {mp['pe']:.1f} 倍,比長期常態(約 {mp['norm']:.0f} 倍)高 {mp['premium']:.0%}",
            f"以 {mp['etf']} 的本益比當代理;大盤盈餘殖利率 {_p(mp['earnings_yield'], 1)}。",
            "大盤整體偏貴時,個股的估值空間與安全邊際都更重要;模型的折現率刻意不跟著市場下調,避免把高估合理化")
    if regime.get("label"):
        b = regime.get("benchmark", {})
        add("總經", "pos" if regime["label"] == "順風" else "neg" if regime["label"] == "逆風" else "watch", 1,
            f"大盤環境:{regime['label']}", f"{b.get('name')} 相對 200 日線 {_p(b.get('vs_ma200'))}"
            + (f";VIX {regime['vix']['value']:.1f}({regime['vix']['label']})" if regime.get("vix") else "") + "。",
            "大盤環境是短期分數的組成之一")

    order = {"pos": 0, "neg": 1, "watch": 2}
    out.sort(key=lambda x: (-x["importance"], order[x["tone"]]))
    return out
