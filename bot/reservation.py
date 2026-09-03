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
import json
import logging
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from playwright.async_api import Page
from bot.stealth import rand_sleep, page_think_delay, random_scroll

logger = logging.getLogger(__name__)

RESERVATION_URL = "https://www.gunpouc.or.kr/fmcs/157"
SCREENSHOT_DIR  = "screenshots"

# 결과 판정 키워드 — 좁게 잡아야 페이지 안내문구에 오탐하지 않는다.
SUCCESS_KWS = [
    "신청되었습니다", "접수되었습니다", "완료되었습니다",
    "신청이 완료", "예약이 완료", "신청 완료",
]
# 실행 도중 세션이 끊기면 이 문구의 alert 가 뜬다. 재로그인 후 재시도해야 한다.
LOGIN_REQUIRED_KWS = ["로그인을 하셔야만", "로그인이 필요"]

# 사용일 1일당 신청 가능 건수 상한에 걸렸을 때의 메시지.
# 이 경우 같은 날짜는 다른 코트로도 신청할 수 없으므로 더 시도하지 않는다.
LIMIT_KWS = ["최대 예약 제한 횟수", "제한 횟수"]

FAIL_KWS = [
    "신청할 수 없", "신청하실 수 없", "이미 신청", "초과하였습니다",
    "초과했습니다", "마감되었습니다", "실패하였습니다", "오류가 발생",
    "다시 시도", "선택해 주세요", "선택하세요", "입력해 주세요", "입력하세요",
]


def _load_applied(path: str) -> set:
    """이미 신청 성공한 (코트,날짜,슬롯) 이력을 읽어온다.

    재실행 시 같은 건을 다시 신청해 '이미 신청' 오류를 내는 것을 막는다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return {tuple(item) for item in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return set()


def _save_applied(path: str, done: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(k) for k in done), f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.warning(f"  신청 이력 저장 실패: {e}")


def _install_dialog_handler(page: Page) -> List[str]:
    """페이지당 1개의 영구 dialog 핸들러만 설치하고 메시지 수집 리스트를 반환.

    page.once() 를 여러 곳에서 등록하면 하나의 alert 에 두 핸들러가 붙어
    두 번째 accept() 가 'dialog already handled' 로 터진다. 반드시 한 번만 설치.
    """
    msgs: List[str] = getattr(page, "_dialog_messages", None)
    if msgs is not None:
        return msgs
    msgs = []

    async def on_dialog(dialog):
        msgs.append(dialog.message)
        logger.info(f"  [다이얼로그] {dialog.message}")
        try:
            await dialog.accept()
        except Exception:
            pass

    page.on("dialog", on_dialog)
    page._dialog_messages = msgs  # type: ignore[attr-defined]
    return msgs


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


async def _selected_date(page: Page) -> str:
    """현재 시간표가 보여주는 날짜(hidden input base_date) 를 반환."""
    return await page.evaluate(
        "() => document.querySelector('input[name=\"base_date\"]')?.value || ''"
    ) or ""


async def _click_date(page: Page, date_id: str) -> bool:
    """달력에서 날짜를 선택하고 실제로 반영됐는지 base_date 로 검증.

    주의: <td> 와 <a> 가 같은 id 를 쓰기 때문에 getElementById 는 <td> 를 돌려준다.
    <td> 에는 핸들러가 없어 클릭이 무시되고, 페이지 기본 날짜(그 달 1일)의
    시간표가 그대로 남는다 → 엉뚱한 날짜로 신청되는 사고가 난다.
    반드시 querySelector('a[id=...]') 로 <a> 를 잡아야 한다.
    """
    want = date_id.replace("date-", "")
    exists = await page.evaluate(f"""() => !!document.querySelector('a[id="{date_id}"]')""")
    if not exists:
        logger.warning(f"  날짜 링크 없음: #{date_id}")
        return False

    for attempt in (1, 2):
        await rand_sleep(300, 800)
        # 가끔 페이지를 살짝 스크롤 (사람처럼)
        if random.random() < 0.4:
            await random_scroll(page)
        await page.evaluate(f"""document.querySelector('a[id="{date_id}"]').click()""")
        await rand_sleep(1800, 3000)
        await _dismiss_popups(page)

        got = await _selected_date(page)
        if got == want:
            return True
        logger.warning(f"  날짜 반영 안 됨 (요청={want} 현재={got or '없음'}) — 재시도 {attempt}/2")

    logger.error(f"  날짜 선택 실패: {want} — 잘못된 날짜로 신청하지 않도록 건너뜁니다.")
    return False


async def _get_target_slots(page: Page, start_times: List[str]) -> List[Dict]:
    """start_times(["1800","2000"])로 시작하는 예약 가능 슬롯만 정확히 매칭.

    value 형식: "SLOTID;부명;시작HHMM;종료HHMM;1"
    '18시 이후 전부'가 아니라 '18시·20시 시작'만 뽑아야 하므로 정확 비교한다.
    """
    slots = await page.evaluate(r"""
        () => Array.from(document.querySelectorAll('input[name="time"]')).map(r => ({
            id:       r.id,
            value:    r.value,
            disabled: r.disabled,
            label:    (r.closest('tr')?.innerText || '').replace(/\s+/g, ' ').trim(),
        }))
    """)
    wanted = {t.zfill(4) for t in start_times}
    result = []
    for s in slots:
        parts = s["value"].split(";")
        if len(parts) < 4:
            continue
        if s["disabled"]:
            logger.info(f"    슬롯 {parts[2]}~{parts[3]}: 비활성(선택 불가) — 건너뜀")
            continue
        if parts[2].zfill(4) in wanted:
            result.append(s)
    found = {s["value"].split(";")[2] for s in result}
    missing = wanted - found
    if missing:
        logger.warning(f"    요청 시간대 미발견/불가: {sorted(missing)}")
    return result


async def _open_apply_form(page: Page, slot: Dict, rent_type: str,
                           expect_date: str = "") -> bool:
    """슬롯 선택 → 대관신청 버튼 클릭 → 폼 페이지 진입 확인."""
    try:
        # 신청 직전 최종 확인 — 시간표가 의도한 날짜의 것인지
        if expect_date:
            got = await _selected_date(page)
            if got != expect_date:
                logger.error(f"  날짜 불일치로 신청 중단: 요청={expect_date} 현재={got or '없음'}")
                return False
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

        # 전역 dialog 핸들러(1회 설치)에서 이번 클릭으로 새로 생긴 메시지만 확인
        all_dialogs = _install_dialog_handler(page)
        seen_before = len(all_dialogs)

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
            new_dialogs = all_dialogs[seen_before:]
            if new_dialogs:
                logger.warning(f"  폼 미진입 — 다이얼로그: '{new_dialogs[0]}'")
            else:
                logger.warning(f"  폼 미진입 — 페이지: {page_snippet[:200]}")
            return False
    except Exception as e:
        logger.error(f"  대관신청 버튼 클릭 실패: {e}")
        return False


def _format_tel(raw: str) -> str:
    """휴대전화번호를 사이트가 받는 하이픈 형식으로 정규화.

    사이트 유효성 검사가 '01012345678' 같은 하이픈 없는 값을 거부한다.
    설정에는 어느 형식으로 넣어도 되도록 여기서 맞춰준다.
    """
    d = "".join(c for c in (raw or "") if c.isdigit())
    if len(d) == 11:                      # 010-1234-5678
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10:                      # 011-123-4567
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return raw


async def _fill_and_submit_form(page: Page, form_cfg: Dict) -> bool:
    try:
        team_nm = form_cfg.get("team_nm",   "이규환")
        title   = form_cfg.get("title",     "테니스")
        purpose = form_cfg.get("purpose",   "테니스")
        users   = form_cfg.get("users",     "4")
        mobile  = _format_tel(form_cfg.get("mobile_tel", ""))

        await page_think_delay()   # 폼을 읽는 시간 시뮬레이션

        # 대표자 수정 — JS로 값 강제 설정 후 타이핑으로 덮어씀
        team_field = page.locator("input[name='team_nm']")
        if await team_field.count() > 0:
            await page.evaluate("document.querySelector(\"input[name='team_nm']\").value = ''")
            await team_field.click()
            await page.keyboard.press("ControlOrMeta+a")   # macOS 는 Control+a 가 전체선택이 아님
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
                await page.keyboard.press("ControlOrMeta+a")
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
        # Locator 에는 triple_click 이 없다 — click_count=3 으로 기존 값 전체 선택
        await users_field.click(click_count=3)
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

        # 제출 직전 실제 입력값을 읽어 검증 — 특히 연락처가 잘못 들어가면 안 된다
        actual = await page.evaluate("""() => {
            const v = n => document.querySelector("input[name='" + n + "']")?.value ?? null;
            return {team_nm: v('team_nm'), title: v('title'), purpose: v('purpose'),
                    users: v('users'), mobile_tel: v('mobile_tel')};
        }""")
        logger.info(f"  폼 입력값: 담당자={actual['team_nm']} 행사명={actual['title']} "
                    f"목적={actual['purpose']} 인원={actual['users']} 연락처={actual['mobile_tel']}")

        def _digits(v: str) -> str:
            return "".join(c for c in (v or "") if c.isdigit())

        if mobile and _digits(actual.get("mobile_tel")) != _digits(mobile):
            logger.error(f"  연락처 불일치 — 기대={mobile} 실제={actual.get('mobile_tel')} · 제출 중단")
            return False
        if _digits(actual.get("users")) != _digits(users):
            logger.error(f"  참가인원 불일치 — 기대={users} 실제={actual.get('users')} · 제출 중단")
            return False

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


async def _check_result(page: Page, new_dialogs: List[str]) -> Tuple[str, str]:
    """제출 결과를 판정. 반환: (status, detail), status ∈ {success, fail, unknown}

    이전 구현은 키워드가 하나도 안 걸리면 무조건 True 를 돌려줘서
    실제로는 실패한 건도 성공으로 집계됐다. 이제 3-상태로 구분한다.
    판정 우선순위: 제출 직후 alert > 폼 잔류 여부 > 페이지 본문.
    """
    # 1) 제출로 새로 뜬 alert 가 가장 확실한 신호
    for msg in new_dialogs:
        if any(kw in msg for kw in SUCCESS_KWS):
            return "success", msg
        if any(kw in msg for kw in FAIL_KWS):
            return "fail", msg

    # 2) 폼에 그대로 남아 있으면 유효성 검사에 막힌 것
    still_on_form = await page.locator("input[name='title']").count() > 0

    # 3) 본문 텍스트 (innerText — 숨김 요소·스크립트 제외해 오탐을 줄임)
    body = await page.evaluate("() => document.body.innerText") or ""
    for kw in SUCCESS_KWS:
        if kw in body:
            return "success", kw

    if still_on_form:
        detail = new_dialogs[0] if new_dialogs else "alert 없음"
        return "fail", f"폼 페이지에 잔류 ({detail}) URL={page.url}"
    return "unknown", f"성공 문구 미확인 URL={page.url}"


async def _reset_to_date(page: Page, center: str, part: str, place: str,
                          t_year: int, t_month: int, date_id: str) -> bool:
    """신청 후 다음 슬롯 처리를 위해 날짜 선택 화면으로 복귀."""
    await _goto_reservation(page)
    if not await _select_dropdowns(page, center, part, place):
        return False
    if not await _navigate_to_month(page, t_year, t_month):
        return False
    return await _click_date(page, date_id)


async def _open_form_resilient(page: Page, config: Dict[str, Any], slot: Dict, rent_type: str,
                               date_str: str, dialogs: List[str], center: str, part: str,
                               place_value: str, t_year: int, t_month: int,
                               date_id: str) -> Tuple[bool, str]:
    """폼 진입을 시도하고, 세션이 끊겼으면 재로그인 후 한 번 더 시도.

    장시간 실행 중 서버가 세션을 끊으면 '로그인을 하셔야만 이용가능합니다' alert 만
    뜨고 이후 모든 신청이 조용히 실패한다. 감지해서 다시 로그인해야 한다.
    """
    from bot.auth import login          # 순환 import 방지를 위해 지연 import

    for attempt in (1, 2):
        mark = len(dialogs)
        if await _open_apply_form(page, slot, rent_type, expect_date=date_str):
            return True, ""

        detail = dialogs[mark] if len(dialogs) > mark else "폼 진입 실패"
        if attempt == 2 or not any(kw in detail for kw in LOGIN_REQUIRED_KWS):
            return False, detail

        logger.warning("  세션 만료 감지 — 재로그인 후 재시도합니다.")
        if not await login(page, config):
            return False, "세션 만료 후 재로그인 실패"
        if not await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id):
            return False, "재로그인 후 날짜 화면 복귀 실패"

    return False, "재시도 후에도 폼 진입 실패"


# ── 메인 ───────────────────────────────────────────────────────────────────────

async def reserve(page: Page, config: Dict[str, Any]) -> bool:
    target   = config["target"]
    form_cfg = config.get("form", {})

    center    = target.get("center_value", "GUNPO02")
    part      = target.get("part_value",   "14")
    rent_type = target.get("rent_type",    "1001")
    t_year    = int(target.get("target_year",  datetime.now().year))
    t_month   = int(target.get("target_month", datetime.now().month))

    # 신청할 시작시각 — 기본 18시/20시. 구버전 min_start_hour 도 계속 지원.
    start_times = [str(t).zfill(4) for t in target.get("start_times", [])]
    if not start_times:
        min_hour = int(target.get("min_start_hour", 18))
        logger.warning(f"start_times 미설정 — min_start_hour={min_hour} 이후 전 시간대를 신청합니다.")
        start_times = []

    place_list     = target.get("place_values", [{"label": "1코트", "value": "61"}])
    specific_dates = target.get("specific_dates", [])

    _install_dialog_handler(page)

    state_path = config.get("state_file", "applied.json")
    done = _load_applied(state_path)     # (place, date, slot_id) 중복 신청 방지
    if done:
        logger.info(f"이전 신청 이력 {len(done)}건 로드 ({state_path}) — 해당 건은 건너뜁니다.")

    results: List[Dict[str, str]] = []
    capped_dates: set = set()   # 일일 신청 상한에 걸린 날짜 (다른 코트도 불가)

    for court in place_list:
        if isinstance(court, dict):
            place_value, place_label = court["value"], court.get("label", court["value"])
        else:
            place_value = place_label = str(court)

        logger.info(f"\n{'='*56}")
        logger.info(f"코트: {place_label} (place={place_value})")

        await _goto_reservation(page)
        if not await _select_dropdowns(page, center, part, place_value):
            await _save_screenshot(page, f"error_dropdown_{place_value}")
            continue

        # 드롭다운이 실제로 적용됐는지 확인 — 조용히 다른 코트를 신청하는 사고 방지
        actual = await page.evaluate("() => document.querySelector('select#place')?.value || ''")
        if actual != place_value:
            logger.error(f"  place 선택 불일치: 요청={place_value} 실제={actual} — 이 코트 건너뜀")
            await _save_screenshot(page, f"error_place_mismatch_{place_value}")
            continue

        if not await _navigate_to_month(page, t_year, t_month):
            await _save_screenshot(page, f"error_month_{place_value}")
            continue

        if specific_dates:
            date_ids = [f"date-{d}" for d in specific_dates]
        else:
            date_ids = await page.evaluate("""
                () => Array.from(document.querySelectorAll('.calendar td.weekday5, .calendar td.weekday6'))
                    .map(td => td.querySelector('a[id^="date-"]')?.id || '')
                    .filter(id => id)
            """)

        for date_id in date_ids:
            date_str = date_id.replace("date-", "")

            if date_str in capped_dates:
                logger.info(f"  {date_str}: 이 날짜는 일일 신청 상한 도달 — 남은 코트 시도 생략")
                results.append({"court": place_label, "date": date_str, "slot": "-",
                                "status": "skip", "detail": "일일 신청 상한(다른 코트도 불가)"})
                continue

            if not await _is_date_available(page, date_id):
                logger.info(f"  {date_str}: 추첨신청 불가 — 건너뜀")
                results.append({"court": place_label, "date": date_str, "slot": "-",
                                "status": "skip", "detail": "날짜 신청 불가"})
                continue

            logger.info(f"\n  ── {place_label} / {date_str} ──")
            if not await _click_date(page, date_id):
                continue

            slots = await _get_target_slots(page, start_times)
            if not slots:
                logger.info(f"  {date_str}: 대상 시간대 가용 슬롯 없음")
                results.append({"court": place_label, "date": date_str, "slot": "-",
                                "status": "skip", "detail": "대상 시간대 슬롯 없음"})
                continue

            for i, slot in enumerate(slots):
                parts    = slot["value"].split(";")
                slot_tag = f"{parts[2]}-{parts[3]}"
                key      = (place_value, date_str, parts[0])
                if key in done:
                    logger.info(f"  {slot_tag}: 이미 신청 완료된 건 — 건너뜀")
                    results.append({"court": place_label, "date": date_str, "slot": slot_tag,
                                    "status": "skip", "detail": "이전 실행에서 신청 완료"})
                    continue

                logger.info(f"  슬롯 {slot_tag} ({parts[1]}) 신청 시도")
                dialogs = getattr(page, "_dialog_messages", [])

                opened, detail = await _open_form_resilient(
                    page, config, slot, rent_type, date_str, dialogs,
                    center, part, place_value, t_year, t_month, date_id)
                if not opened:
                    await _save_screenshot(page, f"error_form_{place_value}_{date_str}_{slot_tag}")
                    results.append({"court": place_label, "date": date_str, "slot": slot_tag,
                                    "status": "fail", "detail": detail})

                    if any(kw in detail for kw in LIMIT_KWS):
                        logger.warning(f"  {date_str}: 일일 신청 상한 도달 — 이 날짜의 남은 슬롯/코트를 건너뜁니다.")
                        capped_dates.add(date_str)
                        break

                    await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)
                    continue

                mark = len(dialogs)   # 폼 제출로 새로 뜨는 alert 만 보기 위해 기준점 갱신
                if not await _fill_and_submit_form(page, form_cfg):
                    await _save_screenshot(page, f"error_submit_{place_value}_{date_str}_{slot_tag}")
                    results.append({"court": place_label, "date": date_str, "slot": slot_tag,
                                    "status": "fail", "detail": "폼 작성/제출 오류"})
                    await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)
                    continue

                status, detail = await _check_result(page, dialogs[mark:])
                await _save_screenshot(page, f"{status}_{place_label}_{date_str}_{slot_tag}")
                results.append({"court": place_label, "date": date_str, "slot": slot_tag,
                                "status": status, "detail": detail})

                if status == "success":
                    done.add(key)
                    _save_applied(state_path, done)
                    logger.info(f"  ✓ 신청 완료: {place_label} / {date_str} / {slot_tag}")
                elif status == "fail":
                    logger.warning(f"  ✗ 신청 실패: {place_label} / {date_str} / {slot_tag} — {detail}")
                    if any(kw in detail for kw in LIMIT_KWS):
                        capped_dates.add(date_str)
                        break
                else:
                    logger.warning(f"  ? 판정 불가: {place_label} / {date_str} / {slot_tag} — {detail}")

                # 마지막 슬롯이면 되돌아갈 필요 없음 (다음 날짜는 어차피 새로 진입)
                is_last = (i == len(slots) - 1)
                if not is_last:
                    await _reset_to_date(page, center, part, place_value, t_year, t_month, date_id)

            # 다음 날짜를 달력에서 고르려면 달력 화면으로 복귀
            if date_id != date_ids[-1]:
                await _goto_reservation(page)
                if not await _select_dropdowns(page, center, part, place_value):
                    break
                if not await _navigate_to_month(page, t_year, t_month):
                    break

    _log_summary(results)
    return any(r["status"] == "success" for r in results)


def _log_summary(results: List[Dict[str, str]]) -> None:
    counts = {"success": 0, "fail": 0, "unknown": 0, "skip": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    logger.info(f"\n{'='*56}")
    logger.info("신청 결과 요약")
    logger.info(f"{'='*56}")
    for r in results:
        icon = {"success": "✓", "fail": "✗", "unknown": "?", "skip": "-"}.get(r["status"], "?")
        logger.info(f"  {icon} {r['court']:<5} {r['date']} {r['slot']:<10} {r['detail'][:60]}")
    logger.info(f"{'-'*56}")
    logger.info(f"  성공 {counts['success']} / 실패 {counts['fail']} / "
                f"판정불가 {counts['unknown']} / 건너뜀 {counts['skip']}")
