"""本機網頁伺服器(只綁 127.0.0.1,不對外)。

啟動:.venv/bin/python -m app.server  → 打開 http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from lab import app_settings, config, i18n, metric_dictionary, notes, public, report, resolver, scan, snapshots, watchlist  # noqa: E402
from lab.config import HOST, PORT, WEB_DIR, load_params  # noqa: E402
from lab.universe import UNIVERSES  # noqa: E402

from app.update_api import is_local_host, is_same_origin  # noqa: E402
from app.update_api import router as update_router  # noqa: E402

app = FastAPI(title="H0 Lab", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(update_router)

_mem: dict[str, tuple[float, dict]] = {}
_locks: dict[str, threading.Lock] = {}
_glock = threading.Lock()
MEM_TTL = 600


@app.middleware("http")
async def local_host_only(request: Request, call_next):
    """所有路徑都只接受本機 Host(防 DNS rebinding:evil.com 解析到 127.0.0.1 也讀不到觀察清單、筆記、模型)。

    最先定義的 middleware 在最內層,所以 403 一樣會帶到安全標頭。
    """
    if not is_local_host(request):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    # 別的網站用 <img src=".../api/report/stream?refresh=1"> 也會觸發重跑、消耗資料額度;首頁不擋,外部連結才進得來
    if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/static/") or request.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"  # 每次都向伺服器確認(ETag),改程式後不會卡舊版
    return resp


# 使用者自己的內容,不翻譯
_NO_I18N_PATHS = ("/api/notes", "/api/watchlist", "/api/search", "/api/settings")


@app.middleware("http")
async def localize_api(request: Request, call_next):
    """英文模式:API 的 JSON 輸出在這裡翻譯(含錯誤訊息);中文模式完全不經過,輸出逐位元不變。"""
    resp = await call_next(request)
    if (i18n.lang_of(request) != "en" or not request.url.path.startswith("/api/") or request.url.path.startswith(_NO_I18N_PATHS)
            or resp.headers.get("content-type", "").split(";")[0] != "application/json"):
        return resp
    raw = b"".join([c async for c in resp.body_iterator])
    try:
        body = json.dumps(i18n.localize(json.loads(raw)), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except ValueError:
        body = raw
    headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
    return Response(body, status_code=resp.status_code, headers=headers, media_type="application/json")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/api/search")
def api_search(q: str = Query("", max_length=60)):
    return {"results": resolver.search(q, 10)}


def _lock(sym: str) -> threading.Lock:
    with _glock:
        return _locks.setdefault(sym, threading.Lock())


def _with_model(r: dict, model_id: str | None) -> dict:
    """報告的對外版本(lab/public.py:不含內建評等、綜合公平價、交易計畫),
    指定使用者模型時加上用它重算的分數(不改記憶體快取裡的報告)。"""
    from lab.analysis.scoring import DEFAULT_MODEL
    from lab.models import apply, store
    from lab.models.schema import ModelError
    if not model_id or model_id == DEFAULT_MODEL:
        return public.report(r)
    if not r.get("model_env"):
        raise HTTPException(409, "這份報告沒有基礎變數快照,請重新整理報告")
    try:
        m = store.get(model_id)
    except ModelError as e:
        raise HTTPException(404, str(e)) from e
    if _is_builtin(m):
        raise HTTPException(400, "開源版不提供內建模型的個股分數,請選你自己的模型")
    try:
        custom = apply.rescore(r["model_env"], m, r["meta"]["market"], load_params().get("validated_pillars"))
    except Exception as e:  # noqa: BLE001  模型檔通過驗證卻算不出來:回明確的錯誤,不要裸 500
        raise HTTPException(422, f"這個模型算不出分數:{type(e).__name__}: {e}") from e
    return {**public.report(r), "custom_model": custom}


@app.get("/api/report")
async def api_report(symbol: str = Query(..., max_length=20), refresh: int = 0,
                     model: str | None = Query(None, max_length=48)):
    sym = symbol.strip().upper()
    if not resolver.valid_symbol(sym):
        raise HTTPException(400, "代號格式不正確")
    now = time.time()
    if not refresh and sym in _mem and now - _mem[sym][0] < MEM_TTL:
        return JSONResponse(_with_model(_mem[sym][1], model))   # 快取命中也要套用模型(M6 發現)

    def work():
        with _lock(sym):
            if not refresh and sym in _mem and time.time() - _mem[sym][0] < MEM_TTL:
                return _mem[sym][1]
            r = report.build(sym, refresh=bool(refresh))
            _mem[sym] = (time.time(), r)
            return r

    try:
        r = await run_in_threadpool(work)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"分析失敗:{type(e).__name__}: {e}") from e
    return JSONResponse(_with_model(r, model))


@app.get("/api/report/stream")
def api_report_stream(request: Request, symbol: str = Query(..., max_length=20), refresh: int = 0):
    """即時串流 agent 的工作過程(Server-Sent Events),完成後前端再取 /api/report(已在記憶體快取)。"""
    import json as _json
    import queue

    sym = symbol.strip().upper()
    if not resolver.valid_symbol(sym):
        raise HTTPException(400, "代號格式不正確")
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            with _lock(sym):
                cached_r = _mem.get(sym)
                if not refresh and cached_r and time.time() - cached_r[0] < MEM_TTL:
                    age = int((time.time() - cached_r[0]) / 60)
                    q.put({"agent": "data", "kind": "start", "msg": f"使用 {age} 分鐘前的分析結果(快取),快速重播各模組的結論", "t": 0})
                    for a in cached_r[1].get("agents") or []:
                        q.put({"agent": a["id"], "kind": "start", "msg": a["role"], "t": 0})
                        for f in a.get("findings", [])[:8]:
                            time.sleep(0.04)
                            q.put({"agent": a["id"], "kind": "finding", "msg": f, "t": 0})
                        q.put({"agent": a["id"], "kind": "done", "msg": a.get("headline", ""), "t": 0})
                else:
                    r = report.build(sym, refresh=bool(refresh), progress=q.put)
                    _mem[sym] = (time.time(), r)
            q.put({"kind": "complete"})
        except ValueError as e:
            q.put({"kind": "fatal", "msg": str(e)})
        except Exception as e:  # noqa: BLE001
            q.put({"kind": "fatal", "msg": f"{type(e).__name__}: {e}"})

    threading.Thread(target=worker, daemon=True).start()

    en = i18n.lang_of(request) == "en"

    def gen():
        yield "retry: 5000\n\n"
        while True:
            try:
                ev = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            pub = public.stream_event(ev)       # 內建模型的結論不播(開源版不提供內建評等)
            if pub is not None:
                yield f"data: {_json.dumps(i18n.localize(pub) if en else pub, ensure_ascii=False, default=str)}\n\n"
            if ev.get("kind") in ("complete", "fatal"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/agents")
def api_agents():
    from lab.agents import AGENTS
    return {"items": AGENTS}


HIDDEN_NOTICE = "預測追蹤與模擬投資組合暫停顯示:它們目前用內建模型的評等,下一版會改成追蹤你自己的模型。前瞻紀錄照常在本機累積。"


@app.get("/api/review")
async def api_review():
    """暫停對外(內建模型的個股評等);snapshots.review 保留,下一版重接到使用者模型。"""
    raise HTTPException(404, HIDDEN_NOTICE)


def _check_csrf(request: Request):
    # 會改資料的請求必須帶自訂標頭:別的網站無法跨來源送出這個標頭(沒有開 CORS),擋掉 CSRF
    if request.headers.get("x-requested-with") != "invest-lab" or not is_same_origin(request):
        raise HTTPException(403, "forbidden")


@app.get("/api/scan/status")
def api_scan_status():
    from lab.sources import finmind
    return {**public.scan_status(scan.status()), "finmind": finmind.budget_status()}


@app.get("/api/scan/latest")
def api_scan_latest(universe: str = Query("tw_large", max_length=20), model: str | None = Query(None, max_length=48)):
    """最近一次掃描。只用使用者選定的模型排名(用掃描時存下的基礎變數重算,不抓資料);沒指定模型就只回資料欄、不排名。
    內建模型的評等、分數、綜合公平價不回傳。"""
    from lab.analysis.scoring import DEFAULT_MODEL
    from lab.models import apply, store
    from lab.models.schema import ModelError
    res = scan.latest(universe) or {"rows": [], "label": universe, "time": None}
    rows = res.get("rows") or []
    out = {k: res.get(k) for k in ("key", "label", "time")} | {"errors": [{"symbol": e.get("symbol")} for e in res.get("errors") or []],
                                                                "n": len(rows), "ranked": False, "rows": []}
    if not model or model == DEFAULT_MODEL:
        out["message"] = "選一個你自己的模型才會排名;開源版不提供內建模型的排名。"
        return out
    try:
        m = store.get(model)
    except ModelError as e:
        raise HTTPException(404, str(e)) from e
    if _is_builtin(m):
        raise HTTPException(400, "只能用你自己的模型排名;開源版不提供內建模型的排名")
    vp = load_params().get("validated_pillars")
    ranked, no_env = [], 0
    for r in rows:
        env = r.get("model_env")
        if not env:
            no_env += 1
            continue
        try:
            c = apply.rescore(env, m, r.get("market") or "TW", vp)
        except Exception:  # noqa: BLE001  單檔算不出來就略過,不讓整張表失敗
            no_env += 1
            continue
        row = public.scan_row(r)
        row["model"] = {"lt_score": c["long_term"].get("score"), "lt_grade": c["long_term"].get("grade"),
                        "st_score": c["short_term"].get("score"), "st_grade": c["short_term"].get("grade"),
                        "pillars": {k: p.get("score") for k, p in c["pillars"].items()}}
        ranked.append(row)
    ranked.sort(key=lambda x: (x["model"]["lt_score"] is None, -(x["model"]["lt_score"] or 0), x["symbol"]))
    for i, x in enumerate(ranked, 1):
        x["rank"] = i if x["model"]["lt_score"] is not None else None
    out |= {"ranked": True, "rows": public.finite(ranked), "model": {"id": m["id"], "name": m["name"]}, "no_env": no_env,
            "disclaimer": "只顯示你的模型算出的分數、等級與排名,不是買賣建議。研究用途,非投資建議。"}
    if no_env and not ranked:
        out["message"] = "這次掃描的結果沒有存基礎變數(較舊的掃描),請按「開始掃描」重新掃描一次"
    return out


@app.get("/api/scan/universes")
def api_scan_universes():
    return {"items": [{"key": k, "label": v[0], "n": len(v[1])} for k, v in UNIVERSES.items()]
            + [{"key": "watchlist", "label": "我的觀察清單", "n": len(watchlist.load())}]}


@app.post("/api/scan/start")
def api_scan_start(request: Request, universe: str = Query(..., max_length=20), refresh: int = 0):
    _check_csrf(request)
    try:
        return public.scan_status(scan.start(universe, bool(refresh)))   # 執行中會回 recent,不能帶內建評等
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/research/factors")
async def api_research_factors(universe: str = Query("tw_pit50", max_length=20)):
    from lab.research import factors
    if universe not in factors.UNIVERSES:
        raise HTTPException(400, "未知的股票池")
    try:
        return await run_in_threadpool(factors.run, universe)
    except FileNotFoundError as e:      # 還沒建 PIT 價格庫:回 409 和建立方法,不是程式錯誤
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"計算失敗:{type(e).__name__}: {e}") from e


@app.get("/api/research/evidence")
def api_research_evidence():
    """證據頁摘要(scripts/evidence.py 產生):只放行 split = dev、holdout 起點正確的檔案;沒有就回傳說明。"""
    from lab.research import splits
    for d in (config.OUT_DIR, config.DATA_DIR):
        f = d / "research" / "evidence.json"
        if f.exists():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"evidence": None, "message": "驗證摘要讀取失敗,請重新產生"}
            if j.get("split") != splits.DEV or j.get("holdout_start") != str(splits.HOLDOUT_START.date()):
                return {"evidence": None, "message": "驗證摘要不是開發期版本(含樣本外測試期),不顯示"}
            return {"evidence": j}
    return {"evidence": None, "message": "還沒有驗證摘要。產生方式詳見方法與資料頁。"}


BACKTEST_NOTICE = ("網站不提供重跑回測(回測引擎 backtest_pit 只在本機執行)。這一頁只顯示開發期(2024-09 以前)的結果:"
                   "每個預測期的結果視窗都在 2024-10-01 前結束;樣本外測試期(2024-10-01 起)不顯示任何成績。")


def _dev_only(res: dict | None) -> tuple[dict | None, str | None]:
    """只放行「開發集版」的舊回測輸出(lab/research/backtest.py v2 起的格式);其他一律不回傳,避免 holdout 成績外洩。"""
    from lab.research import splits
    if not isinstance(res, dict):
        return None, "還沒有開發期的回測結果。"
    hs = str(splits.HOLDOUT_START.date())
    if res.get("split") != splits.DEV or res.get("holdout_start") != hs:
        return None, "現有的回測結果含樣本外測試期(舊格式),不顯示。請在本機重跑開發期版本。"
    years = [y.get("year") for y in res.get("by_year") or []]
    curve = ((res.get("portfolio") or {}).get("curve") or {}).get("date") or []
    if any(isinstance(y, int) and y > splits.HOLDOUT_START.year for y in years) or any(str(d) >= hs[:7] for d in curve):
        return None, "回測結果含樣本外測試期的資料,不顯示。"
    return res, None


@app.get("/api/research/backtest")
def api_backtest(market: str = Query("TW", max_length=2)):
    """只回傳開發集;v1.0 對照檔(含 holdout 期間)不再回傳。"""
    if market not in ("TW", "US"):
        raise HTTPException(400, "market 只能是 TW 或 US")
    from lab.research import backtest
    try:
        raw = backtest.latest(market)
    except (OSError, ValueError):
        raw = None
    cur, why = _dev_only(raw)
    return {"current": cur, "baseline": None, "split": "dev", "notice": BACKTEST_NOTICE, "withheld": why}


@app.post("/api/research/backtest/run")
async def api_backtest_run(market: str = Query("TW", max_length=2)):
    raise HTTPException(403, "網站不提供重跑回測(回測引擎 backtest_pit 只在本機執行)。")


@app.get("/api/home")
async def api_home():
    """首頁儀表板:大盤環境、觀察清單最近一次分析的日期與股價(不含內建評等)、最近掃描時間。"""
    from lab.analysis import regime

    def work():
        out = {"markets": {}}
        for m in ("TW", "US"):
            try:
                r = regime.analyze(m)
                b = r.get("benchmark", {})
                out["markets"][m] = {"label": r.get("label"), "score": r.get("score"), "name": b.get("name"),
                                     "price": b.get("price"), "vs_ma200": b.get("vs_ma200"), "ret_1m": b.get("ret_1m"),
                                     "ret_3m": b.get("ret_3m"), "dd": b.get("dd_from_high_1y"), "vix": r.get("vix"),
                                     "us10y": r.get("us10y"), "usdtwd": r.get("usdtwd"), "market_pe": r.get("market_pe")}
            except Exception:  # noqa: BLE001
                out["markets"][m] = None
        snaps = {}
        for s in snapshots.list_all():
            if s["symbol"] not in snaps or s["date"] > snaps[s["symbol"]]["date"]:
                snaps[s["symbol"]] = s
        out["watch"] = [{**w, "snap": public.snapshot(snaps.get(w["symbol"]))} for w in watchlist.load()]
        out["scans"] = {k: (scan.latest(k) or {}).get("time") for k in ("tw_large", "us_large", "watchlist")}
        return out

    return await run_in_threadpool(work)


@app.get("/api/paper")
def api_paper():
    """暫停對外(模擬帳戶依內建模型的評等交易);lab/paper.py 照常在掃描時運作,下一版重接到使用者模型。"""
    raise HTTPException(404, HIDDEN_NOTICE)


@app.get("/api/notes")
def api_notes_get(symbol: str = Query(..., max_length=20)):
    try:
        return {"note": notes.load(symbol)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/notes")
async def api_notes_save(request: Request):
    _check_csrf(request)
    body = await request.json()
    try:
        note = notes.save(str(body.get("symbol", ""))[:20], body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    sym = str(body.get("symbol", "")).upper()
    if sym in _mem:  # 讓記憶體快取的報告也帶最新筆記
        _mem[sym][1]["notes"] = note
    return {"note": note}


@app.get("/api/watchlist")
def api_watchlist():
    return {"items": watchlist.load()}


@app.post("/api/watchlist")
async def api_watchlist_edit(request: Request):
    _check_csrf(request)
    body = await request.json()
    sym = str(body.get("symbol", ""))[:20]
    try:
        if body.get("action") == "remove":
            return {"items": watchlist.remove(sym)}
        return {"items": watchlist.add(sym, str(body.get("name", ""))[:40])}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/dictionary")
def api_dictionary():
    return {"items": metric_dictionary.as_list()}


# ---------- 模型客製化(M2;規格 docs/模型客製化規格.md §6) ----------
def _model_error(e: Exception, code: int = 400):
    raise HTTPException(code, str(e)) from e


MAX_BODY = 1_000_000   # 模型檔遠小於 1 MB


async def _json_obj(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "請求內容太大(上限 1 MB)")
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError) as e:
        raise HTTPException(400, "請求內容不是 JSON,或巢狀太深") from e
    if not isinstance(body, dict):
        raise HTTPException(400, "請求內容必須是 JSON 物件")
    return body


@app.get("/api/models")
def api_models():
    from lab.analysis.scoring import BASE_VARS, DEFAULT_MODEL
    from lab.models import store
    from lab.models.schema import expr_available
    from lab.research.model_eval import SHIPPED_TRIALS_NOTE, trial_count
    # 介面只列使用者自己的模型(開源版不提供內建模型的個股分數);內建研究基準模型仍在研究紀錄與回測對照裡使用
    ms = [m for m in store.list_models() if m.get("source") != "shipped"]
    for m in ms:
        if m.get("source") != "shipped" and m.get("family_id"):
            m["n_trials"] = trial_count(m["family_id"])
    return {"models": ms, "default": DEFAULT_MODEL, "base_vars": list(BASE_VARS),
            "expr_available": expr_available(), "shipped_trials_note": SHIPPED_TRIALS_NOTE}


@app.get("/api/models/{model_id}")
def api_model_get(model_id: str):
    from lab.models import store
    from lab.models.schema import ModelError
    try:
        return {"model": store.get(model_id)}
    except ModelError as e:
        _model_error(e, 404)


@app.post("/api/models")
async def api_model_save(request: Request):
    _check_csrf(request)
    from lab.models import store
    from lab.models.schema import ModelError
    body = await _json_obj(request)
    try:
        model = body.get("model") or {}
        if not isinstance(model, dict) or not isinstance(model.get("wizard") or {}, dict):
            raise HTTPException(400, "model 必須是 JSON 物件(model.wizard 也是)")
        if body.get("auto_id"):   # 精靈:代號由系統產生、名稱可留空(上手精靈規格 §2)
            from lab.models import wizard
            tid = str((model.get("wizard") or {}).get("template") or "")
            if not str(model.get("name") or "").strip():
                model = {**model, "name": wizard.default_name(tid)}
            return {"saved": store.save_new(model, auto_prefix=tid.replace("_", "-") or "my-model")}
        if body.get("create_only"):
            return {"saved": store.save_new(model)}
        return {"saved": store.save(model)}
    except (ModelError, AttributeError, TypeError, RecursionError) as e:
        _model_error(e)


@app.post("/api/models/validate")
async def api_model_validate(request: Request):
    """驗證模型;給 symbol 時,用記憶體裡已經算好的報告試算(不會為了試算去抓資料)。"""
    _check_csrf(request)
    from lab.models import apply
    from lab.models.schema import ModelError, score_hash, validate
    body = await _json_obj(request)
    m = body.get("model") or {}
    try:
        validate(m)
    except (ModelError, AttributeError, TypeError, RecursionError) as e:
        return {"ok": False, "error": str(e) or type(e).__name__}
    out = {"ok": True, "score_hash": score_hash(m)}
    sym = str(body.get("symbol") or "").strip().upper()[:20]
    if sym and _is_builtin(m):
        out["preview_error"] = "這個設定和內建研究基準模型相同;開源版不提供內建模型的個股分數,請改一個權重或訊號再試算"
    elif sym:
        if sym not in _mem or not _mem[sym][1].get("model_env"):
            out["preview_error"] = "這檔的報告還沒開啟過,先打開報告再試算"
        else:
            r = _mem[sym][1]
            try:
                out["preview"] = apply.rescore(r["model_env"], m, r["meta"]["market"],
                                               load_params().get("validated_pillars"))
            except Exception as e:  # noqa: BLE001
                out["preview_error"] = f"試算失敗:{type(e).__name__}: {e}"
    return out


def _is_builtin(m: dict) -> bool:
    from lab.models import store
    from lab.models.schema import score_hash
    try:
        h = score_hash(m, load_params())
        return h in {score_hash(store.load_shipped(i, readonly=True), load_params()) for i in store.shipped_ids()}
    except Exception:  # noqa: BLE001  算不出指紋就當作不是(validate 已經擋過格式)
        return False


@app.post("/api/models/{model_id}/fork")
async def api_model_fork(model_id: str, request: Request):
    _check_csrf(request)
    from lab.models import store
    from lab.models.schema import ModelError
    body = await _json_obj(request)
    try:
        return {"saved": store.fork(model_id, str(body.get("id", ""))[:48], str(body.get("name", ""))[:60] or "我的模型")}
    except ModelError as e:
        _model_error(e)


@app.post("/api/models/{model_id}/archive")
async def api_model_archive(model_id: str, request: Request):
    _check_csrf(request)
    from lab.models import store
    from lab.models.schema import ModelError
    try:
        store.archive(model_id)
        return {"ok": True}
    except ModelError as e:
        _model_error(e)


def _model_eval_call(model_id: str, body: dict, fn: str):
    from lab.models import apply, store
    from lab.models.schema import ModelError
    from lab.research import model_eval as ME
    try:
        m = store.get(model_id)
    except ModelError as e:
        _model_error(e, 404)
    universe = str(body.get("universe") or "tw_pit50")[:20]
    try:
        if fn == "holdout":
            out = ME.holdout(m, universe, load_params(), confirm=body.get("confirm") is True)
        else:
            out = ME.backtest(m, universe, load_params())
    except ME.ModelEvalError as e:
        raise HTTPException(409, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return apply.json_safe(out)


@app.post("/api/models/{model_id}/backtest")
async def api_model_backtest(model_id: str, request: Request):
    """開發集回測(計入「試了幾次」);同時回報出廠模型與隨機排序對照組。"""
    _check_csrf(request)
    body = await _json_obj(request)
    return await run_in_threadpool(_model_eval_call, model_id, body, "backtest")


@app.post("/api/models/{model_id}/holdout")
async def api_model_holdout(model_id: str, request: Request):
    """holdout(2024-10-01 以後);要帶 confirm: true。每個模型族只有一次乾淨的樣本外。"""
    _check_csrf(request)
    body = await _json_obj(request)
    return await run_in_threadpool(_model_eval_call, model_id, body, "holdout")


# ---------- 上手精靈(S1;規格 docs/上手精靈規格.md §8) ----------
def _market_arg(market: str) -> str | None:
    m = (market or "").strip().upper()
    if m and m not in ("TW", "US"):
        raise HTTPException(400, "market 只能是 TW 或 US")
    return m or None


@app.get("/api/templates")
def api_templates(market: str = Query("", max_length=2)):
    """風格範本清單(含「準備中」狀態與每個範本的預設重要度)。"""
    from lab.models import catalog, wizard
    return {"market": _market_arg(market), "templates": wizard.list_templates(_market_arg(market)),
            "levels": catalog.levels(), "notice": catalog.tr(wizard.TEMPLATE_NOTE),
            "disclaimer": catalog.tr("研究用途,非投資建議")}


@app.get("/api/catalog")
def api_catalog(market: str = Query("", max_length=2)):
    """訊號目錄(白話名稱、一句話說明、好壞方向、能不能回測)。"""
    from lab.models import catalog
    return catalog.catalog(_market_arg(market), load_params())


def _wizard_preview(body: dict) -> dict:
    from lab.models import apply, wizard
    from lab.models.schema import ModelError
    tid = body.get("template")
    if not isinstance(tid, str) or not tid:
        raise HTTPException(400, "請指定範本(template)")
    mk = body.get("market") or "TW"
    if not isinstance(mk, str) or len(mk) > 2:
        raise HTTPException(400, "market 只能是 TW 或 US")
    market = _market_arg(mk)
    universe = body.get("universe")
    if universe is not None and not isinstance(universe, str):
        raise HTTPException(400, "universe 必須是文字")
    top = body.get("top", wizard.TOP_N)
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= 50:
        raise HTTPException(400, "top 必須是 1–50 的整數")
    try:
        out = wizard.preview(tid[:40], body.get("choices"), market, universe and universe[:20], load_params(), top)
    except ModelError as e:   # 找不到範本(換算錯誤在 preview 裡面變成 ok = False)
        raise HTTPException(404, str(e)) from e
    except ValueError as e:   # 範本不適用這個市場、股票池不對
        raise HTTPException(400, str(e)) from e
    return apply.json_safe(out)


@app.post("/api/wizard/preview")
async def api_wizard_preview(request: Request):
    """送範本 id + 選擇,回傳換算後的模型、驗證結果、今天的股票池排名(不存檔、不計入「試了幾次」)。"""
    _check_csrf(request)
    body = await _json_obj(request)
    return await run_in_threadpool(_wizard_preview, body)


@app.get("/api/params")
def api_params():
    return load_params()


@app.get("/api/settings")
def api_settings_get():
    """本機設定(語言、研究工具告知);只存在 private/,不會送出去。"""
    return app_settings.load()


@app.post("/api/settings")
async def api_settings_save(request: Request):
    _check_csrf(request)
    body = await _json_obj(request)
    try:
        return app_settings.update(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/setup/status")
def api_setup_status():
    """第一次執行引導(docs/安裝與上手規格.md §3):每一步做完沒有;只看檔案在不在,不回 token 值。"""
    from lab import setup_status
    return setup_status.status()


def main():
    print(f"H0 Lab → http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
