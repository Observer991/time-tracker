"""
군포시 시설 자동 예약 봇

사용법:
  python main.py run          # config.json 스케줄에 따라 자동 실행
  python main.py now          # 즉시 예약 실행 (테스트용)
  python main.py login-test   # 로그인만 테스트
"""

import argparse
import asyncio
import logging
import sys

from playwright.async_api import async_playwright

from bot.auth import login
from bot.config import load_config
from bot.reservation import reserve
from bot.scheduler import run_at
from bot.stealth import (
    LAUNCH_ARGS,
    random_context_options,
    setup_stealth_context,
    page_think_delay,
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("reservation.log", encoding="utf-8"),
        ],
    )


async def _build_browser_and_page(pw, browser_cfg: dict):
    """스텔스 설정이 적용된 브라우저·페이지를 반환."""
    # 실제 Chrome 우선, 없으면 Chromium
    try:
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=browser_cfg.get("headless", False),
            args=LAUNCH_ARGS,
        )
        logging.info("  [스텔스] 실제 Chrome 사용")
    except Exception:
        browser = await pw.chromium.launch(
            headless=browser_cfg.get("headless", False),
            args=LAUNCH_ARGS,
        )
        logging.info("  [스텔스] Chromium 사용 (Chrome 없음)")

    ctx_opts = random_context_options()
    logging.info(f"  [스텔스] UA={ctx_opts['user_agent'][:50]}... viewport={ctx_opts['viewport']}")
    context = await browser.new_context(**ctx_opts)
    await setup_stealth_context(context)

    page = await context.new_page()
    # 페이지 로드 후 짧은 사람 흉내 딜레이
    await page_think_delay()
    return browser, page


async def _run_reservation(config: dict) -> None:
    browser_cfg = config.get("browser", {})
    async with async_playwright() as pw:
        browser, page = await _build_browser_and_page(pw, browser_cfg)
        try:
            ok = await login(page, config)
            if not ok:
                logging.error("로그인 실패 — 종료")
                return

            ok = await reserve(page, config)
            logging.info("예약 신청 성공" if ok else "예약 신청 실패")
        finally:
            await browser.close()


async def cmd_run(config: dict) -> None:
    sched   = config.get("schedule", {})
    open_dt = sched.get("open_datetime")
    advance = sched.get("advance_seconds", 30)
    if not open_dt:
        logging.error("config.json 에 schedule.open_datetime 이 없습니다.")
        return
    await run_at(open_dt, advance, lambda: _run_reservation(config))


async def cmd_now(config: dict) -> None:
    await _run_reservation(config)


async def cmd_login_test(config: dict) -> None:
    browser_cfg = config.get("browser", {})
    async with async_playwright() as pw:
        browser, page = await _build_browser_and_page(pw, browser_cfg)
        try:
            ok = await login(page, config)
            logging.info("[login-test] 로그인 성공" if ok else "[login-test] 로그인 실패")
        finally:
            await browser.close()


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description="군포시 테니스 코트 자동 예약 봇")
    parser.add_argument("command", choices=["run", "now", "login-test"])
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.command == "run":
        asyncio.run(cmd_run(config))
    elif args.command == "now":
        asyncio.run(cmd_now(config))
    elif args.command == "login-test":
        asyncio.run(cmd_login_test(config))


if __name__ == "__main__":
    main()
