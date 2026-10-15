"""Safe Telegram callback answers and bounded duplicate tracking."""

from collections import deque
from telegram.error import BadRequest


ANSWERED_CALLBACK_QUERY_IDS = set()
ANSWERED_CALLBACK_QUERY_ORDER = deque()
MAX_REMEMBERED_CALLBACK_QUERIES = 4096
STALE_CALLBACK_QUERY_ERRORS = (
    "query is too old",
    "response timeout expired",
    "query id is invalid",
)


def remember_answered_callback_query(query_id):

    if query_id in ANSWERED_CALLBACK_QUERY_IDS:
        return

    if len(ANSWERED_CALLBACK_QUERY_ORDER) >= MAX_REMEMBERED_CALLBACK_QUERIES:
        oldest_query_id = ANSWERED_CALLBACK_QUERY_ORDER.popleft()
        ANSWERED_CALLBACK_QUERY_IDS.discard(oldest_query_id)

    ANSWERED_CALLBACK_QUERY_ORDER.append(query_id)
    ANSWERED_CALLBACK_QUERY_IDS.add(query_id)


async def answer_callback_query_safely(query, *args, **kwargs):

    if query.id in ANSWERED_CALLBACK_QUERY_IDS:
        return False

    try:
        await query.answer(*args, **kwargs)
    except BadRequest as error:
        error_message = str(error).lower()
        if not any(
            stale_error in error_message
            for stale_error in STALE_CALLBACK_QUERY_ERRORS
        ):
            raise
        remember_answered_callback_query(query.id)
        return False

    remember_answered_callback_query(query.id)
    return True


class SafeCallbackQuery:

    def __init__(self, query):
        self._query = query

    def __getattr__(self, name):
        return getattr(self._query, name)

    async def answer(self, *args, **kwargs):
        return await answer_callback_query_safely(
            self._query,
            *args,
            **kwargs
        )


