from __future__ import annotations

import logging

from redis import Redis
from rq import Queue
from rq import Retry

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_queue() -> Queue:
    settings = get_settings()
    conn = Redis.from_url(settings.redis_url)
    return Queue("default", connection=conn)


def enqueue_job(job_name: str, *args, **kwargs) -> str:
    settings = get_settings()
    if settings.queue_mode == "inline":
        from app.workers import jobs

        handler = getattr(jobs, job_name)
        handler(*args, **kwargs)
        return f"inline-{job_name}"

    try:
        queue = _get_queue()
        retry = None
        if settings.queue_retry_max > 0:
            retry = Retry(
                max=settings.queue_retry_max,
                interval=settings.queue_retry_interval_seconds,
            )
        job = queue.enqueue(
            f"app.workers.jobs.{job_name}",
            *args,
            retry=retry,
            **kwargs,
        )
        logger.info("enqueued_job", extra={"job_name": job_name, "job_id": job.id})
        return job.id
    except Exception:  # pragma: no cover - network failure fallback
        logger.exception("redis_enqueue_failed_falling_back_inline", extra={"job_name": job_name})
        from app.workers import jobs

        handler = getattr(jobs, job_name)
        handler(*args, **kwargs)
        return f"fallback-inline-{job_name}"
