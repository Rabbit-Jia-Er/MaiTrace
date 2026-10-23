"""日程生成、前夜推断和角色裁判共用主程序 LLM，显式传递 RPC 超时。"""

from typing import Any, Dict

from ..llm_runner import LLMRunner


async def generate_text(
    plugin: Any,
    *,
    prompt: str,
    model: str,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """保留规划代码的结果形状，模型失败原因交由业务入口完整报告。"""
    runner = LLMRunner(
        plugin.ctx, model,
        timeout=int(plugin.config.schedule.generation_timeout),
    )
    success, response = await runner.generate(
        prompt, temperature=temperature, max_tokens=max_tokens
    )
    return {"success": success, "response": response, "error": "" if success else response}
