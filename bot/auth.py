import logging
import random
from typing import Any, Dict

from playwright.async_api import Page
from bot.stealth import rand_sleep, page_think_delay

logger = logging.getLogger(__name__)

LOGIN_URL   = "https://www.gunpouc.or.kr/fmcs/160"
HISTORY_URL = "https://www.gunpouc.or.kr/fmcs/170"   # 대관내역 (로그인 필요)


async def is_logged_in(page: Page) -> bool:
    """로그인이 필요한 페이지에 접근해 현재 세션이 살아있는지 확인.

    이 사이트는 비로그인 상태로 보호된 메뉴에 들어가면 URL 을 바꾸지 않고
    alert 만 띄운다. 따라서 URL·로그인폼 유무로는 판별할 수 없고,
    헤더의 '로그아웃' 링크 존재로 확인해야 한다.
    """
    try:
        await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
    except Exception:
        return False
    if "/fmcs/160" in page.url:                      # 로그인 페이지로 리다이렉트
        return False
    return await page.evaluate(
        """() => Array.from(document.querySelectorAll('a'))
                 .some(a => (a.textContent || '').includes('로그아웃'))"""
    )


async def ensure_login(page: Page, config: Dict[str, Any]) -> bool:
    """세션이 살아있으면 재사용하고, 아니면 로그인.

    영속 프로필을 쓰므로 대개 재로그인이 필요 없다. 실행할 때마다 로그인하는
    것은 불필요한 요청이기도 하고 자동화 티가 나는 패턴이기도 하다.
    """
    if await is_logged_in(page):
        logger.info("기존 세션 재사용 — 로그인 생략")
        return True
    logger.info("세션 없음 — 로그인 진행")
    return await login(page, config)


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
