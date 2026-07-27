import logging

from app.common.context.request_context import (
    get_request_id,
)


class RequestIdFilter(logging.Filter):

    def filter(
        self,
        record: logging.LogRecord,
    ):

        record.request_id = get_request_id() or "-"

        return True
