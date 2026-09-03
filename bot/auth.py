import logging
import random
from typing import Any, Dict

from playwright.async_api import Page
from bot.stealth import rand_sleep, page_think_delay

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.gunpouc.or.kr/fmcs/160"


async def login(page: Page, config: Dict[str, Any]) -> bool:
    cred = config["credentials"]
    logger.info("로그인 페이지 이동 중...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")

    try:
        await page.wait_for_selector("input[name='user_id']", timeout=15000)
    except Exception:
        logger.error("로그인 폼을 찾지 못했습니다. 페이지 상태를 확인하세요.")
        return False

    await page_think_delay()   # 페이지를 읽는 척

    # 사람처럼 글자 단위 타이핑
    id_field = page.locator("input[name='user_id']")
    await id_field.click()
    await rand_sleep(200, 500)
    for ch in cred["id"]:
        await id_field.type(ch, delay=random.randint(60, 180))

    await rand_sleep(300, 700)

    pw_field = page.locator("input[name='user_password']")
    await pw_field.click()
    await rand_sleep(150, 400)
    for ch in cred["password"]:
        await pw_field.type(ch, delay=random.randint(50, 160))

    await rand_sleep(500, 1000)

    # 로그인 버튼 클릭 후 네비게이션 대기
    login_btn = page.locator("button:has-text('로그인')").first
    async with page.expect_navigation(timeout=15000):
        await login_btn.click()

    await page.wait_for_load_state("domcontentloaded", timeout=10000)
    current_url = page.url

    if "/fmcs/160" not in current_url:
        logger.info(f"로그인 성공 (이동: {current_url})")
        return True

    # 실패 메시지 확인
    try:
        error_els = page.locator("[class*='error'], [class*='alert'], .msg, #msg")
        if await error_els.count() > 0:
            error_text = await error_els.first.text_content()
            logger.error(f"로그인 실패: {(error_text or '').strip()}")
        else:
            logger.error("로그인 실패: 알 수 없는 오류")
    except Exception:
        logger.error("로그인 실패")
    return False
