"""ctx.llm.generate 薄包装。

通过当前 SDK 显式指定主程序模型任务与 RPC 超时。
``asyncio.wait_for`` 留出传输余量，让 RPC 层先报告具体的超时错误。
"""

from __future__ import annotations

import asyncio

from maibot_sdk.context import PluginContext

from ..utils import get_logger

logger = get_logger(__name__)

# asyncio.wait_for 相对 RPC 超时的额外余量（秒）。
# 让 RPC 层先超时，这样拿到的是"RPC 超时"而不是笼统的 TimeoutError。
_WAIT_SLACK_SECONDS = 10


class LLMRunner:
    """统一 LLM 调用入口。

    用法：
        runner = LLMRunner(plugin.ctx, plugin.config.llm.text_model,
                           timeout=plugin.config.llm.llm_timeout_seconds)
        success, text = await runner.generate(prompt, temperature=0.3)
    """

    def __init__(
        self,
        ctx: PluginContext,
        text_model: str = "replyer",
        *,
        timeout: int = 60,
    ) -> None:
        self._ctx = ctx
        self._text_model = text_model or "replyer"
        self._timeout = max(1, int(timeout or 60))

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> tuple[bool, str]:
        """生成文本。返回 (success, text)。

        Args:
            prompt: 提示词。
            temperature: 采样温度。
            max_tokens: 最大生成 token 数。
            timeout: 本次调用的超时（秒）。省略时用构造时的值。日记这类长 prompt
                任务可以单独调大，不受 ``[llm].llm_timeout_seconds`` 限制。
        """
        if not prompt or not prompt.strip():
            return False, "空 prompt"

        effective_timeout = max(1, int(timeout or self._timeout))
        try:
            result = await asyncio.wait_for(
                # 当前模型代理透传 timeout_ms，task_name 明确指定模型任务。
                self._ctx.llm.generate(
                    timeout_ms=effective_timeout * 1000,
                    prompt=prompt,
                    task_name=self._text_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout=effective_timeout + _WAIT_SLACK_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("llm.generate 超时(%ds)", effective_timeout)
            return False, f"LLM 调用超时({effective_timeout}s)"
        except Exception as exc:
            logger.error("llm.generate 调用异常: %s", exc, exc_info=True)
            return False, f"LLM 调用异常: {exc}"

        if not isinstance(result, dict):
            return False, f"LLM 返回非 dict: {type(result).__name__}"
        if not result.get("success"):
            return False, str(result.get("error") or "LLM 返回 success=False")
        response = result.get("response")
        if response is not None and not isinstance(response, str):
            return False, f"LLM 返回正文类型错误: {type(response).__name__}"
        text = response.strip() if response is not None else ""
        if not text:
            detail = "仅返回推理内容，未返回正文" if result.get("reasoning") else "返回空正文"
            return False, (f"LLM {detail}（模型任务={self._text_model}，max_tokens={max_tokens}，"
                           f"completion_tokens={result.get('completion_tokens', '未知')}）")
        return True, text
