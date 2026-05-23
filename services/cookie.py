"""QQ 空间 Cookie 管理。

5 种获取方式（按顺序尝试）：
- adapter: 通过 napcat-adapter 插件的 @API 取（新 SDK 推荐）
- napcat:  直接 HTTP 调 Napcat /get_cookies
- clientkey: 通过本机 QQ 客户端取 clientkey 再换 cookie
- qrcode:  扫码登录
- local:   读本地缓存

Cookie 保存路径：data/cookies-<uin>.json。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from typing import Any, List, Optional

import httpx

from .persistence import (
    get_cookie_file_path,
    get_qrcode_path,
    load_cookie_stats,
    record_cookie_attempt,
)

logger = logging.getLogger(__name__)

COOKIE_METHODS = ("adapter", "napcat", "clientkey", "qrcode", "local")
# 需要用户交互或仅作 fallback 的方式：不参与按成功率重排，始终保持原顺序末尾
_INTERACTIVE_OR_FALLBACK_METHODS = frozenset({"qrcode", "local"})

_QRCODE_URL = (
    "https://ssl.ptlogin2.qq.com/ptqrshow?appid=549000912&e=2&l=M&s=3&d=72&v=4"
    "&t=0.31232733520361844&daid=5&pt_3rd_aid=0"
)
_LOGIN_CHECK_URL = (
    "https://xui.ptlogin2.qq.com/ssl/ptqrlogin?u1=https://qzs.qq.com/qzone/v5/loginsucc.html"
    "?para=izone&ptqrtoken={}&ptredirect=0&h=1&t=1&g=1&from_ui=1&ptlang=2052"
    "&action=0-0-1656992258324&js_ver=22070111&js_type=1&login_sig=&pt_uistyle=40"
    "&aid=549000912&daid=5&has_onekey=1&&o1vId=1e61428d61cb5015701ad73d5fb59f73"
)
_CHECK_SIG_URL = (
    "https://ptlogin2.qzone.qq.com/check_sig?pttype=1&uin={}&service=ptqrlogin&nodirect=1"
    "&ptsigx={}&s_url=https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone&f_url=&ptlang=2052"
    "&ptredirect=100&aid=549000912&daid=5&j_later=0&low_login_hour=0&regmaster=0"
    "&pt_login_type=3&pt_aid=0&pt_aaid=16&pt_light=0&pt_3rd_aid=0"
)

# 全局 cookie 状态（debug 命令 + 频率节流）
_cookie_state: dict = {
    "last_save_time": 0.0,
    "last_method": "",
    "last_error": "",
    "uin": "",
}


def get_cookie_state() -> dict:
    """供 /zn debug cookie 读取当前 cookie 状态。"""
    return dict(_cookie_state)


def _parse_cookie_string(cookie_str: str) -> dict:
    """'k1=v1; k2=v2' → {k1: v1, k2: v2}"""
    return {p.split("=", 1)[0]: p.split("=", 1)[1] for p in cookie_str.split("; ") if "=" in p}


def _ptqrtoken(qrsig: str) -> str:
    e = 0
    for ch in qrsig:
        e += (e << 5) + ord(ch)
    return str(2147483647 & e)


# ----- 各种获取方式 -----


async def fetch_cookies_by_adapter(ctx) -> Optional[dict]:
    """通过 napcat-adapter 插件 @API 取 cookie。"""
    try:
        result = await ctx.api.call(
            "maibot-team.napcat-adapter.adapter.napcat.account.get_cookies",
            params={"domain": "user.qzone.qq.com"},
        )
    except Exception as exc:
        logger.warning("adapter API 调用异常: %s", exc)
        return None
    if not isinstance(result, dict) or result.get("status") != "ok":
        logger.info("adapter 返回非 ok: %s", result)
        return None
    data = result.get("data") or {}
    cookie_str = data.get("cookies") if isinstance(data, dict) else None
    if not cookie_str:
        return None
    return _parse_cookie_string(cookie_str)


async def fetch_cookies_by_napcat(host: str, port: str, napcat_token: str = "") -> Optional[dict]:
    """通过 Napcat HTTP 直连 /get_cookies。"""
    url = f"http://{host}:{port}/get_cookies"
    headers = {"Content-Type": "application/json"}
    if napcat_token:
        headers["Authorization"] = f"Bearer {napcat_token}"
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            resp = await client.post(
                url,
                json={"domain": "user.qzone.qq.com"},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "ok" or "cookies" not in data.get("data", {}):
            logger.error("Napcat 返回异常: %s", data)
            return None
        return _parse_cookie_string(data["data"]["cookies"])
    except httpx.RequestError as exc:
        logger.error("无法连接 Napcat (%s): %s", url, exc)
    except Exception as exc:
        logger.error("Napcat 取 cookie 异常: %s", exc)
    return None


async def fetch_cookies_by_clientkey(uin: str) -> Optional[dict]:
    """通过本机 QQ 客户端 clientkey 取 cookie（需 QQ 在同一机器运行）。"""
    if not uin:
        return None
    local_key_url = (
        "https://xui.ptlogin2.qq.com/cgi-bin-xlogin"
        "?appid=715021417&s_url=https%3A%2F%2Fhuifu.qq.com%2Findex.html"
    )
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(local_key_url, headers={"User-Agent": ua})
            pt_local_token = resp.cookies.get("pt_local_token", "")
            if not pt_local_token:
                logger.warning("clientkey 取 pt_local_token 失败")
                return None
            client_key_url = (
                f"https://localhost.ptlogin2.qq.com:4301/pt_get_st"
                f"?clientuin={uin}&callback=ptui_getst_CB&r=0.7284667321181328"
                f"&pt_local_tk={pt_local_token}"
            )
            resp = await client.get(
                client_key_url,
                headers={"User-Agent": ua, "Referer": "https://xui.ptlogin2.qq.com/"},
                cookies=resp.cookies,
            )
            if resp.status_code == 400:
                logger.warning("clientkey 取失败: %s", resp.text[:200])
                return None
            clientkey = resp.cookies.get("clientkey", "")
            if not clientkey:
                return None
            login_url = (
                f"https://ssl.ptlogin2.qq.com/jump?ptlang=1033&clientuin={uin}&clientkey={clientkey}"
                f"&u1=https%3A%2F%2Fuser.qzone.qq.com%2F{uin}%2Finfocenter&keyindex=19"
            )
            resp = await client.get(
                login_url, headers={"User-Agent": ua}, follow_redirects=False,
            )
            resp = await client.get(
                resp.headers["Location"],
                headers={"User-Agent": ua, "Referer": "https://ssl.ptlogin2.qq.com/"},
                cookies=resp.cookies,
                follow_redirects=False,
            )
            return {c.name: c.value for c in resp.cookies.jar}
    except Exception as exc:
        logger.warning("clientkey 取 cookie 异常: %s", exc)
        return None


async def fetch_cookies_by_qrcode(max_attempts: int = 3) -> Optional[dict]:
    """生成二维码 → 等待手机 QQ 扫码 → 取 cookie。"""
    qrcode_path = get_qrcode_path()
    for _attempt in range(max_attempts):
        async with httpx.AsyncClient() as client:
            req = await client.get(_QRCODE_URL)
            qrsig = ""
            for piece in req.headers.get("Set-Cookie", "").split(";"):
                if piece.startswith("qrsig"):
                    qrsig = piece.split("=", 1)[1]
                    break
            if not qrsig:
                logger.error("qrcode 取 qrsig 失败")
                continue
            ptqrtoken = _ptqrtoken(qrsig)
            with qrcode_path.open("wb") as f:
                f.write(req.content)
            logger.info("二维码已保存于 %s，请两分钟内使用手机 QQ 扫描登录", qrcode_path)

            for _ in range(60):
                await asyncio.sleep(2)
                resp = await client.get(
                    _LOGIN_CHECK_URL.format(ptqrtoken),
                    cookies={"qrsig": qrsig},
                )
                if "二维码已失效" in resp.text:
                    logger.info("二维码已失效，重新获取...")
                    break
                if "登录成功" in resp.text:
                    # 解析 url
                    parts = resp.text.replace("ptuiCB", "").strip("()").split(",")
                    url = parts[2].strip().strip("'\"") if len(parts) >= 3 else ""
                    m_sigx = re.search(r"ptsigx=([A-Za-z0-9]+)", url)
                    m_uin = re.search(r"uin=(\d+)", url)
                    if not m_sigx or not m_uin:
                        continue
                    ptsigx, uin = m_sigx.group(1), m_uin.group(1)
                    res = await client.get(
                        _CHECK_SIG_URL.format(uin, ptsigx),
                        cookies={"qrsig": qrsig},
                        headers={"Cookie": resp.headers.get("Set-Cookie", "")},
                    )
                    final_cookie_dict: dict = {}
                    for set_cookie in res.headers.get("Set-Cookie", "").split(";, "):
                        for ck in set_cookie.split(";"):
                            kv = ck.split("=", 1)
                            if len(kv) == 2 and kv[0] not in final_cookie_dict:
                                final_cookie_dict[kv[0]] = kv[1]
                    if qrcode_path.exists():
                        try:
                            qrcode_path.unlink()
                        except OSError:
                            pass
                    return final_cookie_dict
    logger.error("qrcode 登录最大重试次数耗尽")
    return None


def read_local_cookies(uin: str) -> Optional[dict]:
    """读 data/cookies-<uin>.json。"""
    file_path = get_cookie_file_path(uin)
    if not file_path.exists():
        logger.info("本地 cookie 文件不存在: %s", file_path)
        return None
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("读本地 cookie 失败: %s", exc)
        return None


# ----- 顶层入口：renew_cookies -----


def _reorder_methods(methods: List[str], stats: dict) -> List[str]:
    """按经验重排 cookie 方式：自动方式按 success_rate × 时间衰减降序，qrcode/local 保持原顺序末尾。

    用户配置的 method 集合不变，只调整顺序。
    """
    if not methods:
        return methods

    auto_methods: List[str] = []
    fallback_methods: List[str] = []
    for m in methods:
        if m in _INTERACTIVE_OR_FALLBACK_METHODS:
            fallback_methods.append(m)
        else:
            auto_methods.append(m)

    if len(auto_methods) <= 1 or not stats:
        return auto_methods + fallback_methods

    now = time.time()

    def score(method: str) -> float:
        entry = stats.get(method) or {}
        success = int(entry.get("success", 0) or 0)
        failure = int(entry.get("failure", 0) or 0)
        total = success + failure
        # 没用过的给中性 0.5
        success_rate = (success / total) if total > 0 else 0.5
        last_ts = float(entry.get("last_success_ts", 0.0) or 0.0)
        if last_ts > 0:
            hours = max(0.0, (now - last_ts) / 3600.0)
            decay = math.exp(-hours / 24.0)
        else:
            decay = 0.3  # 从未成功过的方式略低权重
        return success_rate * decay

    auto_methods.sort(key=score, reverse=True)
    return auto_methods + fallback_methods


async def renew_cookies(
    ctx,
    *,
    host: str,
    port: str,
    napcat_token: str,
    uin: str,
    methods: List[str],
    fallback_to_local: bool = True,
    min_refresh_interval_seconds: float = 3600.0,
    skip_qr_if_recent_seconds: float = 20 * 3600.0,
) -> bool:
    """按 methods 顺序尝试取 cookie 并保存到 data/cookies-<uin>.json。

    - 距离上次成功保存 < min_refresh_interval_seconds 时跳过（默认 1 小时）。
    - 二维码方式在 skip_qr_if_recent_seconds 内跳过（默认 20 小时）。
    - 全部失败且 fallback_to_local=True 时回退读本地缓存。
    - 自动方式（adapter/napcat/clientkey）按 cookie_stats.json 的成功率重排。
    """
    now = time.time()
    last_save = _cookie_state["last_save_time"]
    if last_save and (now - last_save) < min_refresh_interval_seconds:
        logger.debug("距上次 cookie 刷新 < %ds，跳过", int(min_refresh_interval_seconds))
        return True

    valid_methods = [m for m in methods if m in COOKIE_METHODS]
    if not valid_methods:
        logger.warning("无有效 cookie 方法，使用默认全部")
        valid_methods = list(COOKIE_METHODS)

    # 按经验重排
    stats = await load_cookie_stats()
    reordered = _reorder_methods(valid_methods, stats)
    if reordered != valid_methods:
        logger.info("cookie 方法按经验重排: %s → %s", valid_methods, reordered)
    else:
        logger.info("使用 cookie 方法: %s", reordered)

    cookie_dict: Optional[dict] = None
    last_error: Optional[Exception] = None
    used_method: str = ""

    for method in reordered:
        attempt_ok = False
        try:
            if method == "adapter":
                logger.info("尝试通过 napcat-adapter API 取 cookie...")
                cookie_dict = await fetch_cookies_by_adapter(ctx)
            elif method == "napcat":
                logger.info("尝试通过 Napcat HTTP 取 cookie...")
                cookie_dict = await fetch_cookies_by_napcat(host, port, napcat_token)
            elif method == "clientkey":
                logger.info("尝试通过 clientkey 取 cookie...")
                cookie_dict = await fetch_cookies_by_clientkey(uin)
            elif method == "qrcode":
                if last_save and (now - last_save) < skip_qr_if_recent_seconds:
                    logger.info("上次扫码登录在 20h 内，跳过 qrcode")
                    continue
                logger.info("尝试通过扫码登录取 cookie...")
                cookie_dict = await fetch_cookies_by_qrcode()
            elif method == "local":
                logger.info("尝试读本地 cookie 文件...")
                cookie_dict = read_local_cookies(uin)
            if cookie_dict:
                logger.info("[%s] 取 cookie 成功", method)
                attempt_ok = True
                used_method = method
                # local 不计入"主动获取"的成功率统计，避免污染
                if method != "local":
                    await record_cookie_attempt(method, True)
                break
            logger.info("[%s] 失败，尝试下一种", method)
            if method != "local":
                await record_cookie_attempt(method, False)
        except Exception as exc:
            logger.error("[%s] 异常: %s", method, exc)
            last_error = exc
            _cookie_state["last_error"] = f"[{method}] {exc}"
            if method != "local":
                await record_cookie_attempt(method, False)

        del attempt_ok  # silence linters

    if cookie_dict is None and fallback_to_local and "local" not in valid_methods:
        logger.info("所有方法失败，回退读本地 cookie")
        cookie_dict = read_local_cookies(uin)
        if cookie_dict:
            used_method = "local"

    if not cookie_dict:
        if last_error:
            logger.error("全部 cookie 方法失败，最后错误: %s", last_error)
            _cookie_state["last_error"] = str(last_error)
        else:
            logger.error("全部 cookie 方法失败")
            _cookie_state["last_error"] = "全部方法失败"
        return False

    # 保存
    try:
        file_path = get_cookie_file_path(uin)
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(cookie_dict, f, indent=4, ensure_ascii=False)
        _cookie_state["last_save_time"] = time.time()
        _cookie_state["last_method"] = used_method
        _cookie_state["last_error"] = ""
        _cookie_state["uin"] = uin
        logger.info("cookies 已保存至 %s", file_path)
        return True
    except Exception as exc:
        logger.error("保存 cookie 失败: %s", exc)
        _cookie_state["last_error"] = f"保存失败: {exc}"
        return False
