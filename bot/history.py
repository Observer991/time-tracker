"""대관 신청내역 조회 — 신청이 실제로 사이트에 반영됐는지 교차 검증용."""
import logging
from typing import Any, Dict, List

from playwright.async_api import Page

logger = logging.getLogger(__name__)

HISTORY_URL = "https://www.gunpouc.or.kr/fmcs/170"


ROW_JS = r"""
    () => Array.from(document.querySelectorAll('table tr'))
        .map(tr => Array.from(tr.querySelectorAll('td'))
            .map(c => (c.innerText || '').replace(/\s+/g, ' ').trim()))
        .filter(cells => cells.length >= 9)
"""


def _to_items(rows) -> List[Dict[str, str]]:
    # 컬럼: 번호 | 접수번호 | 대관상태 | 접수일자 | 센터명 | 이용장소 | 행사구분 | 대관일시 | 이용시간
    return [
        {
            "no":     r[0], "receipt": r[1], "status": r[2], "applied": r[3],
            "center": r[4], "place":   r[5], "kind":   r[6],
            "date":   r[7], "time":    r[8],
        }
        for r in rows
    ]


async def fetch_history(page: Page, max_pages: int = 10) -> List[Dict[str, str]]:
    """대관내역 전체 페이지를 파싱해 신청 건 목록을 반환.

    목록은 10건씩 페이징된다. 1페이지만 읽으면 최근 건만 보여 이전 신청을
    놓치므로 페이지 링크를 따라가며 모두 모은다.
    """
    try:
        await page.goto(HISTORY_URL, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        logger.warning(f"대관내역 조회 실패: {e}")
        return []

    items: List[Dict[str, str]] = _to_items(await page.evaluate(ROW_JS))
    seen = {it["receipt"] for it in items}

    for pageno in range(2, max_pages + 1):
        link = page.locator(f".paging a:text-is('{pageno}'), .pagination a:text-is('{pageno}')")
        if await link.count() == 0:
            link = page.locator(f"a[href='#now_{pageno}']")
        if await link.count() == 0:
            break
        try:
            await link.first.click()
            await page.wait_for_timeout(2000)
        except Exception:
            break

        page_items = _to_items(await page.evaluate(ROW_JS))
        fresh = [it for it in page_items if it["receipt"] not in seen]
        if not fresh:                 # 페이지가 안 바뀌었으면 중단
            break
        items.extend(fresh)
        seen.update(it["receipt"] for it in fresh)

    return items


def log_history(items: List[Dict[str, str]], title: str = "대관 신청내역") -> None:
    logger.info(f"\n{'='*68}")
    logger.info(title)
    logger.info(f"{'='*68}")
    if not items:
        logger.info("  (내역 없음 또는 조회 실패)")
        return
    for it in items:
        logger.info(f"  [{it['status']:<6}] {it['date']} {it['time']:<15} "
                    f"{it['place']:<6} 접수#{it['receipt']} (신청일 {it['applied']})")
    logger.info(f"{'-'*68}")
    logger.info(f"  총 {len(items)}건")


def summarize_pending(items: List[Dict[str, str]]) -> Dict[str, Any]:
    """추첨 대기중인 건만 (날짜, 시간, 장소) 집합으로 요약."""
    return {
        (it["date"], it["time"].split("~")[0].strip(), it["place"])
        for it in items if "대기" in it["status"]
    }
