"""Demo: selective no-comment when reading others' QZone (pure helpers, no network)."""

from __future__ import annotations


def is_skip_comment(text) -> bool:
    if text is None:
        return True
    t = str(text).strip()
    if not t:
        return True
    for _ in range(2):
        t = t.strip().strip("\"'“”‘’`")
        if len(t) >= 2 and t[0] in "[(（【" and t[-1] in "])）】":
            t = t[1:-1].strip()
    normalized = "".join(t.split()).lower()
    return normalized in {
        "不回复", "不评论", "跳过", "无", "无评论",
        "无需回复", "不用回复", "不必回复",
        "skip", "none", "n/a", "na", "null",
    }


def parse_enable_comment(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "default", "默认"}:
        return default
    if text in {"0", "false", "no", "n", "off", "否", "不", "不回复", "不评论", "skip", "none"}:
        return False
    if text in {"1", "true", "yes", "y", "on", "是", "回复", "评论"}:
        return True
    return default


def with_skip_prompt(prompt: str, allow_skip: bool) -> str:
    if not allow_skip:
        return prompt
    if "不回复" in prompt:
        return prompt
    return prompt.rstrip() + "。若没必要评论只输出不回复；否则只输出评论正文"


def test_skip_tokens():
    assert is_skip_comment("不回复")
    assert is_skip_comment("【不回复】")
    assert is_skip_comment("skip")
    assert not is_skip_comment("今天天气不错")


def test_enable_comment_flag():
    assert parse_enable_comment("false") is False
    assert parse_enable_comment("不回复") is False
    assert parse_enable_comment("true") is True
    assert parse_enable_comment(None) is True


def test_prompt_suffix():
    base = "只输出回复内容"
    out = with_skip_prompt(base, True)
    assert "不回复" in out
    assert with_skip_prompt(out, True) == out


if __name__ == "__main__":
    test_skip_tokens()
    test_enable_comment_flag()
    test_prompt_suffix()
    print("demo OK: MaiTrace skip-comment helpers")
