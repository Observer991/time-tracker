import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_at(open_datetime_str: str, advance_seconds: int, task_fn: Callable[[], Awaitable[None]]) -> None:
    open_dt = datetime.strptime(open_datetime_str, "%Y-%m-%d %H:%M:%S")
    start_dt = open_dt - timedelta(seconds=advance_seconds)
    now = datetime.now()

    if start_dt <= now:
        logger.info("예약 준비 시각이 이미 지났습니다 — 즉시 실행합니다.")
        await task_fn()
        return

    wait_secs = (start_dt - now).total_seconds()
    logger.info(f"예약 오픈: {open_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"브라우저 준비 시각: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} ({wait_secs:.0f}초 후)")

    # 긴 대기: 30초 단위로 남은 시간 로그 출력
    while True:
        remaining = (start_dt - datetime.now()).total_seconds()
        if remaining <= 0:
            break
        if remaining > 60:
            await asyncio.sleep(30)
            logger.info(f"  대기 중 — 시작까지 {(start_dt - datetime.now()).total_seconds():.0f}초 남음")
        else:
            await asyncio.sleep(max(0, remaining))
            break

    logger.info("브라우저 준비 시작")
    await task_fn()
