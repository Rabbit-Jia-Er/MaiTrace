"""统一 logger 命名空间。

SDK 的 ``RunnerIPCLogHandler`` 默认捕获 ``plugin.<plugin_id>`` 命名空间下
（含子 logger）的所有日志并转发到主进程。

调用方在 services / handlers / utils 子模块顶部统一写：

.. code-block:: python

    from ..utils import get_logger
    logger = get_logger(__name__)

最终 logger 名形如 ``plugin.Rabbit-Jia-Er.MaiTrace.<short_name>``。
"""

from __future__ import annotations

import logging

# 与 _manifest.json 的 ``id`` 字段一致
PLUGIN_ID = "Rabbit-Jia-Er.MaiTrace"

_LOGGER_PREFIX = f"plugin.{PLUGIN_ID}"


def get_logger(name: str) -> logging.Logger:
    """构造 ``plugin.<PLUGIN_ID>.<short>`` 命名空间下的 logger。

    Args:
        name: 通常传 ``__name__``。取最后一段作为 short 名。
              空 / None 时回退到 ``"plugin"``。

    Returns:
        logging.Logger
    """
    short = (name or "").rsplit(".", 1)[-1] or "plugin"
    return logging.getLogger(f"{_LOGGER_PREFIX}.{short}")
