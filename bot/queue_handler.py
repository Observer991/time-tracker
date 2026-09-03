import asyncio
import logging

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# TRACER 대기 화면의 특징적 텍스트/URL 패턴
QUEUE_INDICATORS = [
    "대기중",
    "잠시 기다려",
    "순번",
    "waiting",
    "tracer",
]


async def is_in_queue(page: Page) -> bool:
    url = page.url.lower()
    if any(kw in url for kw in ["tracer", "waiting", "queue"]):
        return True

    try:
        content = await page.content()
        content_lower = content.lower()
        return any(kw in content_lower for kw in QUEUE_INDICATORS)
    except Exception:
        return False


async def wait_for_queue(page: Page, timeout_sec: int = 600) -> bool:
    elapsed = 0
    poll_interval = 10

    if not await is_in_queue(page):
        return True

    logger.info("TRACER 대기열 진입 — 순번 대기 중...")

    while elapsed < timeout_sec:
        if not await is_in_queue(page):
            logger.info("대기열 통과 완료")
            return True

        # 남은 대기 정보 추출 시도
        try:
            queue_text = await page.locator("[class*='wait'], [id*='wait'], [class*='queue']").first.text_content()
            if queue_text:
                logger.info(f"  대기 정보: {queue_text.strip()}")
        except Exception:
            pass

        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if elapsed % 30 == 0:
            logger.info(f"  대기 중... ({elapsed}초 경과 / 최대 {timeout_sec}초)")

    logger.error(f"대기열 타임아웃: {timeout_sec}초 초과")
    return False
