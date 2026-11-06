"""更新與本機設定 API(掛在 app/server.py)。

這些端點能改設定或安裝程式,比一般 API 嚴格:
- 所有端點驗 Host 只接受本機位址與實際的埠(防 DNS rebinding:evil.com 解析到 127.0.0.1 也拿不到權杖)。
- 會改東西的端點再驗 Origin 必須完全相同、Sec-Fetch-Site、自訂標頭與 JSON 內容類型(防惡意網站 CSRF)。
- 套用更新與改本機設定另外要一次性權杖(只能從驗過 Host 的 GET 端點取得,用過就換)。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from lab import setup_job, updater, user_settings
from lab.version import __version__

router = APIRouter()
MAX_BODY = 16 * 1024
_LOCAL_NAMES = ("127.0.0.1", "localhost", "[::1]")


def _expected_hosts(request: Request) -> set[str]:
    server = request.scope.get("server") or (None, None)
    return {f"{n}:{server[1]}" for n in _LOCAL_NAMES}


def is_local_host(request: Request) -> bool:
    """Host 是本機位址 + 伺服器實際的埠。app/server.py 的全域檢查也用這個。"""
    return request.headers.get("host", "").lower() in _expected_hosts(request)


def is_same_origin(request: Request) -> bool:
    """有帶 Origin 就必須跟 Host 完全相同;沒帶(命令列工具)交給呼叫端的其他檢查。"""
    origin = request.headers.get("origin")
    return origin is None or origin.lower() == f"http://{request.headers.get('host', '').lower()}"


def require_local(request: Request) -> None:
    if not is_local_host(request):
        raise HTTPException(403, "forbidden")


def require_local_write(request: Request) -> None:
    require_local(request)
    host = request.headers.get("host", "").lower()
    if request.headers.get("origin", "").lower() != f"http://{host}":
        raise HTTPException(403, "forbidden")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin"):
        raise HTTPException(403, "forbidden")
    if request.headers.get("x-requested-with") != "invest-lab":
        raise HTTPException(403, "forbidden")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "unsupported media type")


async def _body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "too large")
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, RecursionError) as e:
        raise HTTPException(400, "invalid json") from e
    if not isinstance(body, dict):
        raise HTTPException(400, "invalid json")
    return body


@router.get("/api/app/identity")
def api_identity(request: Request):
    """launcher 用來確認這個埠上是不是 H0 Lab。"""
    require_local(request)
    return {"app": "h0-lab", "version": __version__}


@router.get("/api/update/status")
async def api_update_status(request: Request):
    require_local(request)
    return await run_in_threadpool(updater.status)


@router.post("/api/update/apply")
async def api_update_apply(request: Request):
    require_local_write(request)
    body = await _body(request)
    if not updater.consume_token(body.get("token")):
        raise HTTPException(403, "invalid token")
    version = body.get("version")
    if not isinstance(version, str):
        raise HTTPException(400, "version required")
    out = await run_in_threadpool(updater.start_apply, version, body.get("confirm_data_format") is True)
    if not out.get("ok"):
        raise HTTPException(409, out.get("error") or "failed")
    return out


@router.get("/api/settings/local")
def api_local_settings(request: Request):
    """本機設定狀態;金鑰只回有沒有填,不回值。token 是寫入要帶的一次性權杖。"""
    require_local(request)
    return {**user_settings.public_state(), "token": updater.current_token()}


@router.post("/api/settings/local")
async def api_local_settings_save(request: Request):
    require_local_write(request)
    body = await _body(request)
    if not updater.consume_token(body.get("token")):
        raise HTTPException(403, "invalid token")
    try:
        return {**user_settings.save(body), "token": updater.current_token()}
    except user_settings.SettingError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/setup/research/start")
async def api_research_start(request: Request):
    require_local_write(request)
    out = setup_job.start()
    if not out.get("ok"):
        raise HTTPException(409, out.get("error") or "failed")
    return out


@router.get("/api/setup/research/status")
def api_research_status(request: Request):
    require_local(request)
    return setup_job.status()


@router.post("/api/setup/research/stop")
async def api_research_stop(request: Request):
    require_local_write(request)
    return {"stopped": setup_job.stop()}
