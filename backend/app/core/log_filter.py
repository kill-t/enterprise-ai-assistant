import logging

from app.common.context.request_context import (
    get_request_id,
)


class RequestIdFilter(logging.Filter):
    """为每条日志补充当前请求的 request_id 字段。"""

    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:
        """写入请求标识并允许该日志记录继续输出。"""

        record.request_id = get_request_id() or "-"

        return True
