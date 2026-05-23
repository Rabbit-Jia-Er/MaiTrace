"""Token 估算与截断。"""

import re


TOKEN_LIMIT_50K = 50000
TOKEN_LIMIT_126K = 126000
MAX_DIARY_LENGTH = 8000
DEFAULT_QZONE_WORD_COUNT = 300
MIN_MESSAGE_COUNT = 3


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 / 1.5，其他 / 4。"""
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def truncate_by_tokens(text: str, max_tokens: int) -> str:
    """按 token 上限截断文本，尽量在句末。"""
    if not text:
        return text
    current = estimate_tokens(text)
    if current <= max_tokens:
        return text
    ratio = max_tokens / current
    target = int(len(text) * ratio * 0.95)
    truncated = text[:target]
    # 找最后一个句末标点
    for i in range(len(truncated) - 1, len(truncated) // 2, -1):
        if truncated[i] in ("。", "！", "？", "\n"):
            return truncated[: i + 1] + "\n\n[聊天记录过长,已截断]"
    return truncated + "\n\n[聊天记录过长,已截断]"


def smart_truncate(text: str, max_length: int = MAX_DIARY_LENGTH) -> str:
    """在 max_length 内尽量按句末截断；找不到则强截 + '...'。"""
    if not text or len(text) <= max_length:
        return text
    for i in range(max_length - 3, max_length // 2, -1):
        if text[i] in ("。", "！", "？", "~"):
            return text[: i + 1]
    return text[: max_length - 3] + "..."
