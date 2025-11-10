import json
import logging
import os
import signal
import sys
import time
from typing import Optional, Tuple

import redis
from dotenv import load_dotenv

from crawler_apify import run as run_crawler

load_dotenv(encoding="utf-8")

REDIS_URL = os.getenv("REDIS_URL")
QUEUE_NAME = os.getenv("QUEUE_NAME", "apify:dataset:jobs")
QUEUE_TIMEOUT = int(os.getenv("QUEUE_WORKER_TIMEOUT", "30"))
RETRY_SLEEP = int(os.getenv("QUEUE_WORKER_RETRY_SLEEP", "5"))


logger = logging.getLogger("redis-worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s. Shutting down worker.", signum)
    _running = False


def _parse_job(payload: str) -> Optional[dict]:
    try:
        job = json.loads(payload)
        if not isinstance(job, dict):
            raise ValueError("Job payload is not a JSON object")
        return job
    except Exception as exc:
        logger.error("Failed to parse job payload: %s | payload=%s", exc, payload[:200])
        return None


def _process_job(job: dict):
    dataset_id = job.get("dataset_id")
    actor_task_id = job.get("actor_task_id") or job.get("task_id")
    run_id = job.get("run_id")
    finished_at = job.get("finished_at")

    logger.info(
        "Processing job | dataset_id=%s actor_task_id=%s run_id=%s finished_at=%s",
        dataset_id,
        actor_task_id,
        run_id,
        finished_at,
    )

    try:
        run_crawler(
            job_dataset_id=dataset_id,
            job_task_id=actor_task_id,
            job_run_id=run_id,
            job_finished_at=finished_at,
        )
    except Exception:
        logger.exception(
            "Crawler execution failed for job | dataset_id=%s actor_task_id=%s",
            dataset_id,
            actor_task_id,
        )


def main():
    global _running

    if not REDIS_URL:
        logger.critical("REDIS_URL is not configured. Worker cannot start.")
        sys.exit(1)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    redis_client: Optional[redis.Redis] = None

    while _running:
        if redis_client is None:
            try:
                redis_client = redis.from_url(REDIS_URL, decode_responses=True)
                redis_client.ping()
                logger.info("Connected to Redis at %s", REDIS_URL)
            except Exception as exc:
                logger.error("Failed to connect to Redis: %s", exc)
                redis_client = None
                time.sleep(RETRY_SLEEP)
                continue

        try:
            item: Optional[Tuple[str, str]] = redis_client.blpop(QUEUE_NAME, timeout=QUEUE_TIMEOUT)
        except Exception as exc:
            logger.error("Redis BLPOP failed: %s", exc)
            redis_client = None
            time.sleep(RETRY_SLEEP)
            continue

        if not item:
            continue

        _, payload = item
        job = _parse_job(payload)
        if not job:
            continue

        _process_job(job)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()

