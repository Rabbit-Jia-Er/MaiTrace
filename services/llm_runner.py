"""ctx.llm.generate 薄包装。

替代旧 llm_api.get_available_models() + llm_api.generate_with_model() 模式。
"""

from __future__ import annotations

import logging
from typing import Any

from ..utils import peel_envelope

logger = logging.getLogger(__name__)


class LLMRunner:
    """统一 LLM 调用入口。

    用法：
        runner = LLMRunner(plugin.ctx, plugin.config.models.text_model)
        success, text = await runner.generate(prompt, temperature=0.3)
    """

    def __init__(self, ctx: Any, text_model: str = "replyer") -> None:
        self._ctx = ctx
        self._text_model = text_model or "replyer"

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> tuple[bool, str]:
        """生成文本。返回 (success, text)。"""
        if not prompt or not prompt.strip():
            return False, "空 prompt"
        try:
            result = await self._ctx.llm.generate(
                prompt=prompt,
                model=self._text_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            logger.error("ctx.llm.generate 调用异常: %s", exc, exc_info=True)
            return False, f"LLM 调用异常: {exc}"

        result = peel_envelope(result)
        if not isinstance(result, dict):
            return False, f"LLM 返回非 dict: {type(result).__name__}"
        if not result.get("success", True):
            return False, str(result.get("error") or "LLM 返回 success=False")
        return True, str(result.get("response") or "").strip()
