"""
군포시 시설 자동 예약 봇

사용법:
  python main.py run          # config.json 스케줄에 따라 자동 실행
  python main.py now          # 즉시 예약 실행
  python main.py login-test   # 로그인/세션만 확인
  python main.py history      # 계정의 대관 신청내역 조회
"""

import argparse
import asyncio
import json
import logging
import os
import sys

from playwright.async_api import async_playwright

from bot.auth import ensure_login
from bot.config import load_config
from bot.history import fetch_history, log_history
from bot.reservation import _install_dialog_handler, reserve
from bot.scheduler import run_at
from bot.stealth import LAUNCH_ARGS, load_or_create_profile, setup_stealth_context


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("reservation.log", encoding="utf-8"),
        ],
    )


async def _open_browser(pw, browser_cfg: dict):
    """영속 프로필로 브라우저를 연다.

    launch_persistent_context 는 쿠키·localStorage 를 디스크에 유지하므로
    실행할 때마다 다시 로그인할 필요가 없다. 지문(UA/뷰포트)도 profile.json 에
    고정해 매번 같은 브라우저로 접속하는 실제 사용자처럼 보이게 한다.
    """
    profile_dir = browser_cfg.get("user_data_dir", ".chrome-profile")
    headless    = browser_cfg.get("headless", False)
    opts        = load_or_create_profile(browser_cfg.get("profile_file", "profile.json"))

    common = dict(user_data_dir=profile_dir, headless=headless, args=LAUNCH_ARGS, **opts)
    try:
        context = await pw.chromium.launch_persistent_context(channel="chrome", **common)
        logging.info(f"  [브라우저] 실제 Chrome · 프로필={profile_dir}")
    except Exception as e:
        logging.info(f"  [브라우저] Chrome 실행 실패({e}) → Chromium 사용")
        context = await pw.chromium.launch_persistent_context(**common)

    await setup_stealth_context(context)
    page = context.pages[0] if context.pages else await context.new_page()
    logging.info(f"  [브라우저] UA={opts['user_agent'][:52]}... viewport={opts['viewport']}")
    return context, page


async def _restore_cookies(context, path: str) -> None:
    """저장해 둔 쿠키를 복원.

    이 사이트의 로그인 쿠키는 만료시각이 없는 '세션 쿠키'라 브라우저를 닫으면
    프로필에 남지 않는다. 그래서 별도로 저장했다가 다시 넣어줘야 재로그인을 피한다.
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            cookies = json.load(f).get("cookies", [])
        if cookies:
            await context.add_cookies(cookies)
            logging.info(f"  [세션] 저장된 쿠키 {len(cookies)}개 복원")
    except (json.JSONDecodeError, OSError, Exception) as e:
        logging.info(f"  [세션] 쿠키 복원 생략 ({e})")


async def _save_cookies(context, path: str) -> None:
    try:
        state = await context.storage_state()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"  [세션] 쿠키 저장 실패: {e}")


async def _with_session(config: dict, work):
    """브라우저 1개 · 세션 1개로 로그인부터 작업까지 이어서 처리."""
    browser_cfg  = config.get("browser", {})
    session_path = browser_cfg.get("session_file", "session.json")

    async with async_playwright() as pw:
        context, page = await _open_browser(pw, browser_cfg)
        try:
            # 로그인 확인 단계부터 alert 가 뜨므로 핸들러를 먼저 설치
            _install_dialog_handler(page)
            await _restore_cookies(context, session_path)

            if not await ensure_login(page, config):
                logging.error("로그인 실패 — 종료")
                return None
            await _save_cookies(context, session_path)

            return await work(page)
        finally:
            await context.close()


async def _run_reservation(config: dict) -> None:
    async def work(page):
        ok = await reserve(page, config)
        logging.info("예약 신청 성공 건 있음" if ok else "성공한 신청 건 없음")
        # 같은 세션에서 바로 신청내역을 조회해 실제 반영 여부를 교차 검증
        log_history(await fetch_history(page), title="신청 후 대관내역 (사이트 기준)")
        return ok

    await _with_session(config, work)


async def cmd_run(config: dict) -> None:
    sched = config.get("schedule", {})
    open_dt = sched.get("open_datetime")
    if not open_dt:
        logging.error("config.json 에 schedule.open_datetime 이 없습니다.")
        return
    await run_at(open_dt, sched.get("advance_seconds", 30), lambda: _run_reservation(config))


async def cmd_now(config: dict) -> None:
    await _run_reservation(config)


async def cmd_login_test(config: dict) -> None:
    await _with_session(config, lambda page: _ok())


async def _ok() -> bool:
    logging.info("[login-test] 세션 정상")
    return True


async def cmd_history(config: dict) -> None:
    await _with_session(config, lambda page: _show_history(page))


async def _show_history(page):
    log_history(await fetch_history(page))
    return True


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description="군포시 테니스 코트 자동 예약 봇")
    parser.add_argument("command", choices=["run", "now", "login-test", "history"])
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = load_config(args.config)
    asyncio.run({
        "run":        cmd_run,
        "now":        cmd_now,
        "login-test": cmd_login_test,
        "history":    cmd_history,
    }[args.command](config))


if __name__ == "__main__":
    main()
