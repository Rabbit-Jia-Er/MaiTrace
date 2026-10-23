"""外层组件与业务流程共用请求预算，覆盖发布和串行读动态。"""

from typing import Any


def publication_budget_seconds(config: Any) -> int:
    """上游文案、可选聊天摘要、Cookie、配图和空间上传的单次预算。"""
    image = config.image
    generation = image.image_number * ((image.image_retry + 1) * 185 + image.image_retry * 5)
    upload = image.image_number * 60
    return 3 * (config.llm.llm_timeout_seconds + 10) + generation + upload + 300


def feed_read_budget_seconds(config: Any) -> int:
    """读取初始化，以及每条动态的等待、模型评论和评论/点赞 HTTP 预算。"""
    # 初始化留出 Cookie 登录、拉取动态、人物与人格查询的时间。
    # 每条串行执行：最多等待 4 秒、一次模型请求（含传输余量）、两次空间请求。
    per_feed = 4 + config.llm.llm_timeout_seconds + 10 + 2 * 30
    return 600 + config.read.read_number * per_feed
