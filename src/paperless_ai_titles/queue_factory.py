import redis
from rq import Queue

from .services.settings import SettingsService


def _derived_timeout_seconds(settings) -> int:
    return int(settings.llm_request_timeout) + 10


def get_queue() -> Queue:
    settings = SettingsService().effective_settings()
    redis_conn = redis.from_url(settings.redis_url)
    return Queue(
        settings.queue_name,
        connection=redis_conn,
        default_timeout=_derived_timeout_seconds(settings),
    )
