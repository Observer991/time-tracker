"""
군포시 시설 예약 신청 핵심 로직.

확인된 DOM 구조 (2026-09-03 기준):
- center:   select#center       → GUNPO02 (시민체육광장)
- part:     select#part         → 14 (테니스장_new)
- place:    select#place        → 61~69 (1~9코트)
- 팝업:     [id^="kntool_popup_layerpopup"] → JS로 숨김
- 다음달:   a#next_month        → JS click
- 날짜:     a#date-YYYYMMDD     → JS click, state_15=신청가능/state_20=예약완료
- 시간:     input[name='time']  → value='SLOTID;이름;HHMM시작;HHMM종료;1'
- 신청목적: input[name='rent_type'] → 1001=체육경기
- 신청버튼: button.action_application → 폼으로 이동
- 폼 필드:
    input[name='team_nm']  → 대표자(담당자)
    input[name='title']    → 행사(경기)명
    input[name='purpose']  → 이용목적
    input[name='users']    → 참가인원
    input[name='mobile_tel'] → 휴대전화번호 (계정값 override)
    input#agree_use1       → 동의합니다 체크박스
- 최종제출: button.action_write
"""
import asyncio
import logging
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, List

from playwright.async_api import Page
from bot.stealth import rand_sleep, human_delay, page_think_delay, human_type, random_scroll

logger = logging.getLogger(__name__)

RESERVATION_URL = "https://www.gunpouc.or.kr/fmcs/157"
SCREENSHOT_DIR  = "screenshots"


# ── 내부 유틸 ──────────────────────────────────────────────────────────────────

async def _dismiss_popups(page: Page) -> None:
    await page.evaluate("""
        document.querySelectorAll('[id^="kntool_popup_layerpopup"]').forEach(el => {
            el.style.display = 'none';
            el.style.visibility = 'hidden';
            el.style.pointerEvents = 'none';
        });
    """)
    await page.wait_for_timeout(150)


async def _save_screenshot(page: Page, label: str) -> None:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"{label}_{ts}.png")
    await page.screenshot(path=path, full_page=True)
    logger.info(f"  스크린샷: {path}")


async def _goto_reservation(page: Page) -> None:
    await page.goto(RESERVATION_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    await _dismiss_popups(page)


# ── 단계별 함수 ────────────────────────────────────────────────────────────────

async def _select_dropdowns(page: Page, center: str, part: str, place: str) -> bool:
    try:
        await rand_sleep(400, 900)
        await page.select_option("select#center", value=center)
        await rand_sleep(1200, 2200)          # 동적 로딩 대기 (불규칙)
        await page.select_option("select#part", value=part)
        await rand_sleep(1200, 2200)
        await page.select_option("select#place", value=place)
        await rand_sleep(1800, 2800)
        await _dismiss_popups(page)
        return True
    except Exception as e:
        logger.error(f"  드롭다운 선택 실패: {e}")
        return False


async def _navigate_to_month(page: Page, target_year: int, target_month: int) -> bool:
    for _ in range(14):
        cal_text = await page.locator(".calendar").text_content() or ""
        m = re.search(r"(\d{4})\.(\d{2})", cal_text)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            if y == target_year and mo == target_month:
                return True
            if (y * 12 + mo) > (target_year * 12 + target_month):
                logger.error(f"  달력 목표월 초과: {y}.{mo:02d}")
                return False
        await page.evaluate("document.getElementById('next_month').click()")
        await page.wait_for_timeout(1200)
        await _dismiss_popups(page)
    logger.error("  목표월 이동 실패")
    return False


async def _is_date_available(page: Page, date_id: str) -> bool:
    """해당 날짜 <a> 링크가 추첨신청가능(state_15) 상태인지 확인.

    DOM 구조: <td id="date-YYYYMMDD" class="weekdayN">
                <a id="date-YYYYMMDD" class="state_15" ...>
    getElementById는 <td>를 반환하므로 querySelector('a[id]')로 <a>를 선택.
    """
    info = await page.evaluate(f"""
        () => {{
            // <a> 태그를 직접 찾음 (getElementById는 <td>를 먼저 반환할 수 있음)
            const a = document.querySelector('a[id="{date_id}"]');
            if (!a) return {{exists: false, cls: '', text: ''}};
            return {{exists: true, cls: a.className, text: a.textContent.trim()}};
        }}
    """)
    if not info["exists"]:
        logger.debug(f"    #{date_id}: 링크 없음")
        return False
    if "state_15" in info["cls"]:
        return True
    logger.info(f"    #{date_id}: class='{info['cls']}' — 신청 불가 ({info['text']})")
    return False


async def _click_date(page: Page, date_id: str) -> bool:
    exists = await page.evaluate(f"() => !!document.getElementById('{date_id}')")
    if not exists:
        logger.warning(f"  날짜 링크 없음: #{date_id}")
        return False
    await rand_sleep(300, 800)
    # 가끔 페이지를 살짝 스크롤 (사람처럼)
    if random.random() < 0.4:
        await random_scroll(page)
    await page.evaluate(f"document.getElementById('{date_id}').click()")
    await rand_sleep(1800, 3000)
    await _dismiss_popups(page)
    return True


async def _get_slots_after_hour(page: Page, min_hour: int) -> List[Dict]:
    """min_hour(18) 이상 시작하는 예약 가능 슬롯 목록."""
    slots = await page.evaluate("""
        () => Array.from(document.querySelectorAll('input[name="time"]')).map(r => ({
            id:       r.id,
            value:    r.value,
            disabled: r.disabled,
            label:    r.closest('tr')?.querySelector('label')?.textContent.trim() || '',
        }))
    """)
    result = []
    for s in slots:
        parts = s["value"].split(";")
        if len(parts) < 4 or s["disabled"]:
            continue
        if int(parts[2]) >= min_hour * 100:  # "1800" >= 1800
            result.append(s)
    return result


async def _open_apply_form(page: Page, slot: Dict, rent_type: str) -> bool:
    """슬롯 선택 → 대관신청 버튼 클릭 → 폼 페이지 진입 확인."""
    try:
        await rand_sleep(400, 900)
        await page.evaluate(f"""
            const t = document.getElementById('{slot["id"]}');
            if (t) {{ t.checked = true; t.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)
        await rand_sleep(300, 700)
        await page.evaluate(f"""
            const r = document.querySelector('input[name="rent_type"][value="{rent_type}"]');
            if (r) {{ r.checked = true; r.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        """)
        await rand_sleep(500, 1200)
        btn = page.locator("button.action_application")
        if await btn.count() == 0:
            logger.error("  대관신청 버튼 없음")
            return False

        # 다이얼로그 메시지를 캡처
        dialog_msg: list = []
        async def on_dialog(dialog):
            msg = dialog.message
            dialog_msg.append(msg)
            logger.info(f"  [다이얼로그] {msg}")
            await dialog.accept()
        page.once("dialog", on_dialog)

        # 마우스 이동 후 클릭
        box = await btn.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + random.randint(-40, 40),
                box["y"] + random.randint(-20, 20),
            )
            await rand_sleep(80, 250)
            await page.mouse.move(
                box["x"] + box["width"] / 2 + random.randint(-3, 3),
                box["y"] + box["height"] / 2 + random.randint(-2, 2),
            )
            await rand_sleep(60, 150)
        await btn.click()
        await rand_sleep(2000, 3500)
        await _dismiss_popups(page)

        # 폼 페이지 진입 여부 확인 (input[name='title'] 존재)
        try:
            await page.wait_for_selector("input[name='title']", timeout=8000)
            logger.info("  폼 페이지 진입 확인")
            return True
        except Exception:
            # 폼으로 이동 안 됨 — 다이얼로그 내용 or 현재 페이지 상태 확인
            page_snippet = (await page.evaluate(
                "() => document.body.innerText.substring(0, 300)"
            ) or "").replace("\n", " ")
            if dialog_msg:
                logger.warning(f"  폼 미진입 — 다이얼로그: '{dialog_msg[0]}'")
            else:
                logger.warning(f"  폼 미진입 — 페이지: {page_snippet[:200]}")
            return False
    except Exception as e:
        logger.error(f"  대관신청 버튼 클릭 실패: {e}")
        return False


async def _fill_and_submit_form(page: Page, form_cfg: Dict) -> bool:
    try:
        team_nm = form_cfg.get("team_nm",   "이규환")
        title   = form_cfg.get("title",     "테니스")
        purpose = form_cfg.get("purpose",   "테니스")
        users   = form_cfg.get("users",     "4")
        mobile  = form_cfg.get("mobile_tel", "")

        await page_think_delay()   # 폼을 읽는 시간 시뮬레이션

        # 대표자 수정 — JS로 값 강제 설정 후 타이핑으로 덮어씀
        team_field = page.locator("input[name='team_nm']")
        if await team_field.count() > 0:
            await page.evaluate("document.querySelector(\"input[name='team_nm']\").value = ''")
            await team_field.click()
            await page.keyboard.press("Control+a")
            await rand_sleep(80, 200)
            for ch in team_nm:
                await team_field.type(ch, delay=random.randint(50, 160))

        await rand_sleep(200, 600)

        # 연락처 override — JS로 값 강제 설정 후 타이핑으로 덮어씀
        if mobile:
            tel_field = page.locator("input[name='mobile_tel']")
            if await tel_field.count() > 0:
                await page.evaluate("document.querySelector(\"input[name='mobile_tel']\").value = ''")
                await tel_field.click()
                await page.keyboard.press("Control+a")
                await rand_sleep(80, 200)
                for ch in mobile:
                    await tel_field.type(ch, delay=random.randint(40, 130))

        await rand_sleep(300, 700)

        # 행사명
        title_field = page.locator("input[name='title']")
        await title_field.click()
        await rand_sleep(150, 400)
        for ch in title:
            await title_field.type(ch, delay=random.randint(50, 170))

        await rand_sleep(200, 500)

        # 이용목적
        purpose_field = page.locator("input[name='purpose']")
        await purpose_field.click()
        await rand_sleep(150, 400)
        for ch in purpose:
            await purpose_field.type(ch, delay=random.randint(50, 170))

        await rand_sleep(200, 500)

        # 참가인원
        users_field = page.locator("input[name='users']")
        await users_field.triple_click()
        await rand_sleep(100, 300)
        for ch in users:
            await users_field.type(ch, delay=random.randint(60, 150))

        await rand_sleep(400, 900)

        # 동의 체크
        agree = page.locator("#agree_use1")
        if await agree.count() > 0 and not await agree.is_checked():
            await rand_sleep(200, 500)
            await agree.check()

        await rand_sleep(500, 1200)

        logger.info(f"  폼: 담당자={team_nm}, 목적={purpose}, 인원={users}, 연락처={mobile or '계정기본값'}")

        # 제출 버튼 클릭 — Playwright click 실패 시 JS fallback
        submit_btn = page.locator("button.action_write")
        if await submit_btn.count() == 0:
            logger.error("  시설예약신청 버튼 없음")
            return False

        clicked = False
        box = await submit_btn.bounding_box()
        if box:
            try:
                await page.mouse.move(
                    box["x"] + random.randint(-50, 50),
                    box["y"] + random.randint(-20, 20),
                )
                await rand_sleep(100, 300)
                await page.mouse.move(
                    box["x"] + box["width"] / 2 + random.randint(-4, 4),
                    box["y"] + box["height"] / 2 + random.randint(-3, 3),
                )
                await rand_sleep(80, 200)
                await submit_btn.click(timeout=10000)
                clicked = True
            except Exception:
                pass

        if not clicked:
            # JS 직접 클릭 (오버레이/팝업 무시)
            logger.info("  JS fallback으로 시설예약신청 버튼 클릭")
            await page.evaluate("document.querySelector('button.action_write').click()")

        await rand_sleep(3000, 5000)
        return True
    except Exception as e:
        logger.error(f"  폼 제출 실패: {e}")
        return False


async def _check_result(page: Page) -> bool:
    body_text = await page.evaluate("() => document.body.textContent") or ""
    success_kws = ["신청되었습니다", "접수되었습니다", "완료되었습니다", "신청 완료", "예약이 완료"]
    fail_kws    = ["실패", "초과", "불가", "마감", "error"]
    if any(kw in body_text for kw in success_kws):
        return True
    if any(kw in body_text for kw in fail_kws):
        logger.warning(f"  실패 키워드 감지 (URL: {page.url})")
        return False
    return True


async def _reset_to_date(page: Page, center: str, part: str, place: str,
                          t_year: int, t_month: int, date_id: str) -> bool:
    """신청 후 다음 슬롯 처리를 위해 날짜 선택 화면으로 복귀."""
    await _goto_reservation(page)
    if not await _select_dropdowns(page, center, part, place):
        return False
    if not await _navigate_to_month(page, t_year, t_month):
        return False
    return await _click_date(page, date_id)


# ── 메인 ───────────────────────────────────────────────────────────────────────

async def reserve(page: Page, config: Dict[str, Any]) -> bool:
    target   = config["target"]
    form_cfg = config.get("form", {})

    center    = target.get("center_value", "GUNPO02")
    part      = target.get("part_value",   "14")
    rent_type = target.get("rent_type",    "1001")
    min_hour  = target.get("min_start_hour", 18)
    t_year    = target.get("target_year",  2026)
    t_month   = target.get("target_month", 10)

    # 코트 목록
    place_list = target.get("place_values", [{"label": "1코트", "value": "61"}])

    # 대상 날짜 목록 (specific_dates 우선, 없으면 금·토 자동 탐색)
    specific_dates = target.get("specific_dates", [])

    total_tried = 0
    success_count = 0

    for court in place_list:
        place_value = court["value"] if isinstance(court, dict) else court
        place_label = court.get("label", place_value) if isinstance(court, dict) else place_value
        logger.info(f"\n{'='*50}")
        logger.info(f"코트: {place_label} (value={place_value})")

        # 예약 페이지 + 드롭다운 선택
        await _goto_reservation(page)
        if not await _select_dropdowns(page, center, part, place_value):
            await _save_screenshot(page, f"error_dropdown_{place_value}")
            continue

        # 목표 월 이동
        if not await _navigate_to_month(page, t_year, t_month):
            await _save_screenshot(page, f"error_month_{place_value}")
            continue

        # 날짜 목록 결정
        if specific_dates:
            date_ids = [f"date-{d}" for d in specific_dates]
        else:
            # 금·토 자동 탐색 (fallback)
            date_ids = await page.evaluate("""
                () => Array.from(document.querySelectorAll('.calendar td.weekday5, .calendar td.weekday6'))
                    .map(td => td.querySelector('a[id^="date-"]')?.id || '')
                    .filter(id => id)
            """)

        for date_id in date_ids:
            date_str = date_id.replace("date-", "")

            # 날짜 신청 가능 여부 확인
            if not await _is_date_available(page, date_id):
                logger.info(f"  {date_str}: 신청 불가 (예약완료 또는 없음) — 건너뜀")
                continue

            logger.info(f"\n  날짜: {date_str}")
            if not await _click_date(page, date_id):
                continue

            # 18:00 이후 슬롯 목록
            slots = await _get_slots_after_hour(page, min_hour)
            if not slots:
                logger.info(f"  {date_str}: {min_hour}:00 이후 가용 슬롯 없음")
                continue

            for slot in slots:
                total_tried += 1
                logger.info(f"  슬롯: {slot['label']} (value={slot['value'][:25]})")

                page.once("dialog", lambda d: asyncio.create_task(d.accept()))

                # 대관신청 → 폼
                if not await _open_apply_form(page, slot, rent_type):
                    await _save_screenshot(page, f"error_form_{place_value}_{date_str}")
                    await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)
                    continue

                # 폼 작성 + 제출
                if not await _fill_and_submit_form(page, form_cfg):
                    await _save_screenshot(page, f"error_submit_{place_value}_{date_str}_{slot['id']}")
                    await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)
                    continue

                ok = await _check_result(page)
                tag = "success" if ok else "fail"
                await _save_screenshot(page, f"{tag}_{place_label}_{date_str}_{slot['id']}")

                if ok:
                    success_count += 1
                    logger.info(f"  ✓ 신청 완료: {place_label} / {date_str} / {slot['label']}")
                else:
                    logger.warning(f"  ✗ 신청 실패: {place_label} / {date_str} / {slot['label']}")

                # 다음 슬롯을 위해 날짜 화면으로 복귀
                await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)

    logger.info(f"\n{'='*50}")
    logger.info(f"총 결과: {success_count}건 성공 / {total_tried}건 시도")
    return success_count > 0
