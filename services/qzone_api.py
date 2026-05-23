"""QzoneAPI - QQ 空间底层 HTTP 接口封装。

完全保留旧版 qzone/api.py 的业务逻辑：upload_image / publish_emotion /
like / comment / reply / get_list / monitor_get_list / get_send_history。

与旧版的差异：
- 不再 import src.chat.utils.utils_image：图片描述改为可选注入，
  调用方通过 image_description_provider 提供（不传则用 "[图片]" 占位）。
- uin 在构造时显式传入（而不是读 config_api.get_global_config）。
- cookies 由调用方加载并传入；本类不负责 cookie 持久化。
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import bs4
import httpx
import json5

logger = logging.getLogger(__name__)


ImageDescriptionProvider = Callable[[str], Awaitable[str]]
"""签名：async def(image_base64: str) -> str。返回图片描述文本。"""


# ===== 辅助函数 =====


def _generate_gtk(skey: str) -> str:
    """生成 QQ 空间的 g_tk 值。"""
    hash_val = 5381
    for ch in skey:
        hash_val += (hash_val << 5) + ord(ch)
    return str(hash_val & 2147483647)


def _get_picbo_and_richval(upload_result: dict) -> tuple[str, str]:
    """从上传结果中提取 picbo 和 richval（用于发图片说说）。"""
    if not isinstance(upload_result, dict) or "ret" not in upload_result:
        raise RuntimeError("获取图片 picbo/richval 失败：返回数据不合法")
    if upload_result["ret"] != 0:
        raise RuntimeError(f"上传图片失败: {upload_result}")
    parts = upload_result["data"]["url"].split("&bo=")
    if len(parts) < 2:
        raise RuntimeError("上传图片返回 url 异常")
    picbo = parts[1]
    d = upload_result["data"]
    richval = ",{},{},{},{},{},{},,{},{}".format(
        d["albumid"], d["lloc"], d["sloc"], d["type"],
        d["height"], d["width"], d["height"], d["width"],
    )
    return picbo, richval


def _extract_code_html(html_content: str) -> Any:
    """从 QQ 空间响应 HTML 中提取 frameElement.callback 的 code。"""
    try:
        soup = bs4.BeautifulSoup(html_content, "html.parser")
        for script in soup.find_all("script"):
            text = script.string
            if not text or "frameElement.callback" in text:
                if not text:
                    continue
                start = text.find("frameElement.callback(") + len("frameElement.callback(")
                end = text.rfind(");")
                if 0 < start < end:
                    json_str = text[start:end].strip().rstrip(";")
                    data = json5.loads(json_str)
                    if isinstance(data, dict):
                        return data.get("code")
    except Exception:
        return None
    return None


def _extract_code_json(json_response: Any) -> Any:
    """从 QQ 空间 JSON 响应中提取 code。"""
    try:
        data = json.loads(json_response) if isinstance(json_response, str) else json_response
        return data.get("code", None)
    except (json.JSONDecodeError, KeyError, AttributeError):
        return None


def _image_to_base64(image: bytes) -> str:
    return base64.b64encode(image).decode("ascii")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ===== QzoneAPI =====


class QzoneAPI:
    """QQ 空间 HTTP API 客户端。"""

    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"

    def __init__(
        self,
        cookies: dict,
        uin: str,
        image_description_provider: Optional[ImageDescriptionProvider] = None,
    ) -> None:
        self.cookies = cookies or {}
        self.uin = str(uin).lstrip("o0") if uin else ""
        self.qq_nickname = ""
        self.gtk2 = _generate_gtk(self.cookies["p_skey"]) if "p_skey" in self.cookies else ""
        self._image_description = image_description_provider

    async def _describe_image(self, url: str) -> str:
        """获取图片描述。失败或未注入 provider 时返回 '[图片]'。"""
        if not url or self._image_description is None:
            return "[图片]"
        try:
            image_b64 = await self.get_image_base64_by_url(url)
            if not image_b64:
                return "[图片]"
            return await self._image_description(image_b64) or "[图片]"
        except Exception as exc:
            logger.warning("获取图片描述失败: %s", exc)
            return "[图片]"

    async def _do(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        cookies: Optional[dict] = None,
        timeout: int = 10,
    ) -> httpx.Response:
        """统一 HTTP 请求入口。"""
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await client.request(
                method=method,
                url=url,
                params=params or {},
                data=data or {},
                headers=headers or {},
                cookies=cookies or self.cookies,
            )

    async def get_image_base64_by_url(self, url: str) -> Optional[str]:
        """下载图片并返回 base64。失败返回 None。"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://qzone.qq.com/",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(url, headers=headers, timeout=30.0)
            if resp.status_code != 200:
                logger.warning("下载图片失败 %s 状态 %s", url, resp.status_code)
                return None
            return base64.b64encode(resp.content).decode("ascii")
        except Exception as exc:
            logger.warning("下载图片异常 %s: %s", url, exc)
            return None

    async def upload_image(self, image: bytes) -> dict:
        """上传图片到 QQ 空间，返回响应 JSON。"""
        res = await self._do(
            "POST",
            self.UPLOAD_IMAGE_URL,
            data={
                "filename": "filename",
                "zzpanelkey": "",
                "uploadtype": "1",
                "albumtype": "7",
                "exttype": "0",
                "skey": self.cookies.get("skey", ""),
                "zzpaneluin": self.uin,
                "p_uin": self.uin,
                "uin": self.uin,
                "p_skey": self.cookies.get("p_skey", ""),
                "output_type": "json",
                "qzonetoken": "",
                "refer": "shuoshuo",
                "charset": "utf-8",
                "output_charset": "utf-8",
                "upload_hd": "1",
                "hd_width": "2048",
                "hd_height": "10000",
                "hd_quality": "96",
                "backUrls": (
                    "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,"
                    "http://119.147.64.75/cgi-bin/upload/cgi_upload_image"
                ),
                "url": f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={self.gtk2}",
                "base64": "1",
                "picfile": _image_to_base64(image),
            },
            headers={
                "referer": f"https://user.qzone.qq.com/{self.uin}",
                "origin": "https://user.qzone.qq.com",
            },
            timeout=60,
        )
        if res.status_code != 200:
            raise RuntimeError(f"上传图片失败: HTTP {res.status_code}")
        text = res.text
        return json.loads(text[text.find("{"): text.rfind("}") + 1])

    async def publish_emotion(self, content: str, images: Optional[list[bytes]] = None) -> str:
        """发说说，返回 tid。失败抛异常。"""
        images = images or []
        post_data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
        }

        if images:
            pic_bos: list[str] = []
            richvals: list[str] = []
            for img in images:
                upload_result = await self.upload_image(img)
                picbo, richval = _get_picbo_and_richval(upload_result)
                pic_bos.append(picbo)
                richvals.append(richval)
            post_data["pic_bo"] = ",".join(pic_bos)
            post_data["richtype"] = "1"
            post_data["richval"] = "\t".join(richvals)

        res = await self._do(
            "POST",
            self.EMOTION_PUBLISH_URL,
            params={"g_tk": self.gtk2, "uin": self.uin},
            data=post_data,
            headers={
                "referer": f"https://user.qzone.qq.com/{self.uin}",
                "origin": "https://user.qzone.qq.com",
            },
        )
        if res.status_code != 200:
            raise RuntimeError(f"发表说说失败: HTTP {res.status_code} {res.text[:200]}")
        if _extract_code_json(res.text) != 0:
            raise RuntimeError(f"发表说说失败: {res.text[:200]}")
        return res.json()["tid"]

    async def like(self, fid: str, target_qq: str) -> bool:
        """点赞指定说说。"""
        res = await self._do(
            "POST",
            self.DOLIKE_URL,
            params={"g_tk": self.gtk2},
            data={
                "qzreferrer": f"https://user.qzone.qq.com/{self.uin}",
                "opuin": self.uin,
                "unikey": f"http://user.qzone.qq.com/{target_qq}/mood/{fid}",
                "curkey": f"http://user.qzone.qq.com/{target_qq}/mood/{fid}",
                "appid": 311,
                "from": 1,
                "typeid": 0,
                "abstime": int(time.time()),
                "fid": fid,
                "active": 0,
                "format": "json",
                "fupdate": 1,
            },
            headers={
                "referer": f"https://user.qzone.qq.com/{self.uin}",
                "origin": "https://user.qzone.qq.com",
            },
        )
        if res.status_code != 200:
            logger.error("点赞 HTTP 失败: %s", res.text[:200])
            return False
        if _extract_code_json(res.text) != 0:
            logger.error("点赞业务失败: %s", res.text[:200])
            return False
        return True

    async def comment(self, fid: str, target_qq: str, content: str) -> bool:
        """评论指定说说。"""
        res = await self._do(
            "POST",
            self.COMMENT_URL,
            params={"g_tk": self.gtk2},
            data={
                "topicId": f"{target_qq}_{fid}__1",
                "uin": self.uin,
                "hostUin": target_qq,
                "feedsType": 100,
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "plat": "qzone",
                "source": "ic",
                "platformid": 52,
                "format": "fs",
                "ref": "feeds",
                "content": content,
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                ),
                "referer": f"https://user.qzone.qq.com/{self.uin}",
                "origin": "https://user.qzone.qq.com",
            },
        )
        if res.status_code != 200:
            logger.error("评论 HTTP 失败: %s", res.text[:200])
            return False
        if _extract_code_html(res.text) != 0:
            logger.error("评论业务失败: %s", res.text[:200])
            return False
        return True

    async def reply(
        self,
        fid: str,
        target_qq: str,
        target_nickname: str,
        content: str,
        comment_tid: str,
        host_uin: Optional[str] = None,
    ) -> bool:
        """回复指定评论（通过 @目标昵称 触发提醒）。"""
        host = str(host_uin) if host_uin else self.uin
        res = await self._do(
            "POST",
            self.REPLY_URL,
            params={"g_tk": self.gtk2},
            data={
                "topicId": f"{host}_{fid}__1",
                "uin": self.uin,
                "hostUin": host,
                "content": f"回复@ {target_nickname} ：{content}",
                "format": "fs",
                "plat": "qzone",
                "source": "ic",
                "platformid": 52,
                "ref": "feeds",
                "richtype": "",
                "richval": "",
                "paramstr": f"@{target_nickname}",
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
                ),
            },
        )
        if res.status_code != 200:
            logger.error("回复 HTTP 失败: %s", res.text[:200])
            return False
        if _extract_code_html(res.text) != 0:
            logger.error("回复业务失败: %s", res.text[:200])
            return False
        return True

    async def get_list(self, target_qq: str, num: int) -> list[dict[str, Any]]:
        """获取指定 QQ 的说说列表。失败返回 [{"error": "..."}]。"""
        logger.info("即将获取 %s 的说说列表...", target_qq)
        res = await self._do(
            "GET",
            self.LIST_URL,
            params={
                "g_tk": self.gtk2,
                "uin": target_qq,
                "ftype": 0,
                "sort": 0,
                "pos": 0,
                "num": num,
                "replynum": 100,
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "jsonp",
                "need_comment": 1,
                "need_private_comment": 1,
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                ),
                "Referer": f"https://user.qzone.qq.com/{target_qq}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive",
            },
        )
        if res.status_code != 200:
            return [{"error": f"访问失败: {res.status_code}"}]

        data = res.text
        json_str = data[len("_preloadCallback("): -2] if data.startswith("_preloadCallback(") and data.endswith(");") else data

        try:
            json_data = json.loads(json_str)
        except Exception as exc:
            return [{"error": f"解析 JSON 失败: {exc}"}]

        try:
            uin_nickname = (json_data.get("logininfo") or {}).get("name", "")
            self.qq_nickname = uin_nickname
            if json_data.get("code") != 0:
                return [{"error": json_data.get("message", "未知错误")}]

            feeds_list: list[dict[str, Any]] = []
            msglist = json_data.get("msglist") or []
            for msg in msglist:
                # 已评论过的说说不再阅读
                is_commented = False
                for comment in (msg.get("commentlist") or []):
                    if uin_nickname == comment.get("name") and target_qq != self.uin:
                        is_commented = True
                        break
                if is_commented:
                    continue

                timestamp = msg.get("created_time", "")
                if timestamp:
                    created_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
                else:
                    created_time = msg.get("createTime", "unknown")
                tid = str(msg.get("tid", ""))
                content = msg.get("content", "")

                # 图片
                images: list[str] = []
                for pic in (msg.get("pic") or []):
                    url = pic.get("url1") or pic.get("pic_id") or pic.get("smallurl")
                    if url:
                        images.append(await self._describe_image(url))
                for video in (msg.get("video") or []):
                    url = video.get("url1") or video.get("pic_url")
                    if url:
                        images.append(await self._describe_image(url))

                # 视频
                videos: list[str] = []
                for video in (msg.get("video") or []):
                    url = video.get("url3")
                    if url:
                        videos.append(url)

                # 转发
                rt_con = ""
                rt_data = msg.get("rt_con") or {}
                if isinstance(rt_data, dict):
                    rt_con = rt_data.get("content", "")

                # 评论（含子评论）
                comments: list[dict[str, Any]] = []
                for comment in (msg.get("commentlist") or []):
                    parent_tid = _safe_int(comment.get("tid"))
                    for sub in (comment.get("list_3") or []):
                        comments.append({
                            "content": sub.get("content", ""),
                            "qq_account": str(sub.get("uin", "")),
                            "nickname": sub.get("name", ""),
                            "comment_tid": _safe_int(sub.get("tid")),
                            "created_time": sub.get("createTime", "") or comment.get("createTime2", ""),
                            "parent_tid": parent_tid,
                        })
                    comments.append({
                        "content": comment.get("content", ""),
                        "qq_account": str(comment.get("uin", "")),
                        "nickname": comment.get("name", ""),
                        "comment_tid": parent_tid,
                        "created_time": comment.get("createTime", "") or comment.get("createTime2", ""),
                        "parent_tid": None,
                    })

                feeds_list.append({
                    "target_qq": str(target_qq),
                    "tid": tid,
                    "created_time": created_time,
                    "content": content,
                    "images": images,
                    "videos": videos,
                    "rt_con": rt_con,
                    "comments": comments,
                })

            if not feeds_list:
                return [{"error": "你已经看过最近的所有说说了，没有必要再看一遍"}]
            return feeds_list
        except Exception as exc:
            logger.error("解析说说失败: %s", exc, exc_info=True)
            return [{"error": f"{exc}, 你没有看到任何东西"}]

    async def monitor_get_list(self, self_readnum: int) -> list[dict[str, Any]]:
        """获取空间首页好友说说列表（HTML 解析）。"""
        res = await self._do(
            "GET",
            self.ZONE_LIST_URL,
            params={
                "uin": self.uin,
                "scope": 0,
                "view": 1,
                "filter": "all",
                "flag": 1,
                "applist": "all",
                "pagenum": 1,
                "aisortEndTime": 0,
                "aisortOffset": 0,
                "aisortBeginTime": 0,
                "begintime": 0,
                "format": "json",
                "g_tk": self.gtk2,
                "useutf8": 1,
                "outputhtmlfeed": 1,
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                ),
                "Referer": f"https://user.qzone.qq.com/{self.uin}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive",
            },
        )
        if res.status_code != 200:
            logger.error("monitor_get_list HTTP 失败: %s", res.status_code)
            return []

        text = res.text
        if text.startswith("_Callback(") and text.endswith(");"):
            text = text[len("_Callback("): -2]
        text = text.replace("undefined", "null")

        try:
            data = json5.loads(text)["data"]["data"]
        except Exception as exc:
            logger.error("monitor 解析错误: %s", exc)
            return []

        try:
            feeds_list: list[dict[str, Any]] = []
            num_self = 0
            for feed in data:
                if not feed:
                    continue
                if str(feed.get("appid", "")) != "311":  # 过滤广告
                    continue
                target_qq = feed.get("uin", "")
                if target_qq == self.uin:
                    num_self += 1
                tid = feed.get("key", "")
                if not target_qq or not tid:
                    continue

                html_content = feed.get("html", "")
                if not html_content:
                    continue
                soup = bs4.BeautifulSoup(html_content, "html.parser")

                created_time = feed.get("feedstime", "").strip()
                text_div = soup.find("div", class_="f-info")
                text_value = text_div.get_text(strip=True) if text_div else ""

                rt_con = ""
                txt_box = soup.select_one("div.txt-box")
                if txt_box:
                    rt_con = txt_box.get_text(strip=True)
                    if "：" in rt_con:
                        rt_con = rt_con.split("：", 1)[1].strip()

                image_urls: list[str] = []
                img_box = soup.find("div", class_="img-box")
                if img_box:
                    for img in img_box.find_all("img"):
                        src = img.get("src")
                        if src and not src.startswith("http://qzonestyle.gtimg.cn"):
                            image_urls.append(src)
                # 视频缩略图
                img_tag = soup.select_one("div.video-img img")
                if img_tag and "src" in img_tag.attrs:
                    image_urls.append(img_tag["src"])
                image_urls = list(set(image_urls))

                images: list[str] = []
                for url in image_urls:
                    images.append(await self._describe_image(url))

                videos: list[str] = []
                video_div = soup.select_one("div.img-box.f-video-wrap.play")
                if video_div and "url3" in video_div.attrs:
                    videos.append(video_div["url3"])

                # 评论
                comments_list: list[dict[str, Any]] = []
                for item in soup.select("li.comments-item.bor3"):
                    qq_account = item.get("data-uin", "")
                    comment_tid = item.get("data-tid", "")
                    nickname = item.get("data-nick", "")
                    content_div = item.select_one("div.comments-content")
                    if content_div:
                        for op in content_div.select("div.comments-op"):
                            op.decompose()
                        c_text = content_div.get_text(" ", strip=True)
                    else:
                        c_text = ""
                    c_time_span = item.select_one("span.state")
                    c_time = c_time_span.get_text(strip=True) if c_time_span else ""

                    parent_tid = None
                    parent_div = item.find_parent("div", class_="mod-comments-sub")
                    if parent_div:
                        parent_li = parent_div.find_parent("li", class_="comments-item")
                        if parent_li:
                            parent_tid_raw = parent_li.get("data-tid")
                            parent_tid = int(parent_tid_raw) if parent_tid_raw and parent_tid_raw.isdigit() else None

                    comments_list.append({
                        "qq_account": str(qq_account),
                        "nickname": nickname,
                        "comment_tid": int(comment_tid) if comment_tid.isdigit() else 0,
                        "content": c_text,
                        "created_time": c_time,
                        "parent_tid": parent_tid,
                    })

                feeds_list.append({
                    "target_qq": str(target_qq),
                    "tid": str(tid),
                    "created_time": created_time,
                    "content": text_value,
                    "images": images,
                    "videos": videos,
                    "rt_con": rt_con,
                    "comments": comments_list,
                })

            logger.info("成功解析 %d 条说说（其中自己 %d 条）", len(feeds_list), num_self)
            # 去除自己的说说，单独通过 get_list 拉完整评论
            feeds_list = [f for f in feeds_list if f.get("target_qq") != self.uin]
            self_feeds = await self.get_list(self.uin, self_readnum)
            self_feeds = [f for f in self_feeds if not f.get("error")]
            feeds_list.extend(self_feeds)
            return feeds_list
        except Exception as exc:
            logger.error("monitor 解析说说错误: %s", exc, exc_info=True)
            return []

    async def get_send_history(self, num: int) -> str:
        """构建近期发送历史的 prompt 片段。"""
        feeds_list = await self.get_list(self.uin, num)
        history = "==================="
        for feed in feeds_list:
            if feed.get("error"):
                continue
            if not feed.get("rt_con"):
                history += (
                    f"\n时间：'{feed.get('created_time', '')}'。"
                    f"\n说说内容：'{feed.get('content', '')}'"
                    f"\n图片：'{feed.get('images', [])}'"
                    "\n==================="
                )
            else:
                history += (
                    f"\n时间: '{feed.get('created_time', '')}'。"
                    f"\n转发了一条说说，内容为: '{feed.get('rt_con', '')}'"
                    f"\n图片: '{feed.get('images', [])}'"
                    f"\n对该说说的评论为: '{feed.get('content', '')}'"
                    "\n==================="
                )
        return history


# ===== 工厂函数 =====


def create_qzone_api(
    uin: str,
    *,
    image_description_provider: Optional[ImageDescriptionProvider] = None,
) -> Optional[QzoneAPI]:
    """从 data/cookies-<uin>.json 加载 cookie 并构造 QzoneAPI。

    Returns:
        QzoneAPI 实例；cookie 文件不存在或解析失败返回 None。
    """
    from .persistence import get_cookie_file_path

    cookie_file = get_cookie_file_path(uin)
    if not cookie_file.exists():
        logger.error("cookie 文件不存在: %s", cookie_file)
        return None
    try:
        with cookie_file.open("r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as exc:
        logger.error("读取 cookie 文件失败 %s: %s", cookie_file, exc)
        return None
    if not cookies:
        return None
    return QzoneAPI(cookies, uin, image_description_provider=image_description_provider)
