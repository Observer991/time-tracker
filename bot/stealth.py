"""
봇 감지 우회 모듈.

적용 기법:
1. navigator.webdriver 제거 (JS 주입)
2. 실제 Chrome 사용 (Chromium 대신)
3. AutomationControlled 플래그 비활성화
4. 랜덤 딜레이 (행동 간격 불규칙화)
5. 사람처럼 타이핑 (글자 단위, 랜덤 속도)
6. 클릭 전 랜덤 마우스 이동
7. 랜덤 스크롤
8. 현실적인 뷰포트 / User-Agent / 언어 설정
"""
import asyncio
import random
from typing import Optional

from playwright.async_api import BrowserContext, Page

# ── 1. 브라우저 실행 인자 ──────────────────────────────────────────────────────

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
    "--start-maximized",
]

# ── 2. 실제 User-Agent 풀 ──────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.207 Safari/537.36",
]

# 현실적 화면 해상도 후보
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1280, "height": 720},
]

# ── 3. 스텔스 JS (페이지 로드 전 주입) ────────────────────────────────────────

STEALTH_INIT_SCRIPT = """
// webdriver 플래그 제거
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 언어/플러그인 현실화
Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            { filename: 'internal-pdf-viewer', description: 'Portable Document Format', name: 'Chrome PDF Plugin' },
            { filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', name: 'Chrome PDF Viewer' },
            { filename: 'internal-nacl-plugin', description: 'Native Client', name: 'Native Client' },
        ];
        p.__proto__ = PluginArray.prototype;
        return p;
    }
});

// chrome 런타임 스텁
if (!window.chrome) {
    window.chrome = {
        runtime: {
            id: undefined,
            connect: () => {},
            sendMessage: () => {},
        }
    };
}

// permissions API 패치
const origQuery = window.navigator.permissions ? window.navigator.permissions.query.bind(window.navigator.permissions) : null;
if (origQuery) {
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(parameters);
}

// Notification.permission 현실화
if (Notification.permission === 'default') {
    Object.defineProperty(Notification, 'permission', { get: () => 'default' });
}
"""

# ── 4. 컨텍스트 설정 ──────────────────────────────────────────────────────────

def random_context_options() -> dict:
    ua = random.choice(USER_AGENTS)
    vp = random.choice(VIEWPORTS)
    return {
        "user_agent": ua,
        "viewport": vp,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "ignore_https_errors": True,
        "extra_http_headers": {
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }


async def setup_stealth_context(context: BrowserContext) -> None:
    """컨텍스트 생성 후 스텔스 스크립트 주입."""
    await context.add_init_script(STEALTH_INIT_SCRIPT)


# ── 5. 랜덤 딜레이 ────────────────────────────────────────────────────────────

async def rand_sleep(min_ms: int = 300, max_ms: int = 1200) -> None:
    """행동 사이 불규칙한 대기 (ms)."""
    await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)


async def human_delay() -> None:
    """짧은 인간 반응 시간 (0.3~1.5s)."""
    await rand_sleep(300, 1500)


async def page_think_delay() -> None:
    """페이지 읽는 시간 시뮬레이션 (1~3.5s)."""
    await rand_sleep(1000, 3500)


# ── 6. 사람처럼 타이핑 ────────────────────────────────────────────────────────

async def human_type(page: Page, selector: str, text: str) -> None:
    """글자 단위 랜덤 속도 입력."""
    await page.click(selector)
    await page.evaluate(f"document.querySelector('{selector}').value = ''")
    for ch in text:
        await page.type(selector, ch, delay=random.randint(40, 180))
        if random.random() < 0.05:          # 5% 확률로 잠깐 멈춤 (생각하는 척)
            await rand_sleep(200, 600)


# ── 7. 랜덤 마우스 이동 후 클릭 ──────────────────────────────────────────────

async def human_move_and_click(page: Page, selector: str) -> None:
    """클릭 전 마우스를 살짝 다른 위치로 이동."""
    el = page.locator(selector).first
    box = await el.bounding_box()
    if box:
        # 요소 주변 랜덤 지점으로 먼저 이동
        jitter_x = box["x"] + random.randint(-60, 60)
        jitter_y = box["y"] + random.randint(-30, 30)
        jitter_x = max(0, jitter_x)
        jitter_y = max(0, jitter_y)
        await page.mouse.move(jitter_x, jitter_y)
        await rand_sleep(80, 300)
        # 실제 요소 클릭
        center_x = box["x"] + box["width"]  / 2 + random.randint(-3, 3)
        center_y = box["y"] + box["height"] / 2 + random.randint(-2, 2)
        await page.mouse.move(center_x, center_y)
        await rand_sleep(50, 150)
    await el.click()


# ── 8. 랜덤 스크롤 ────────────────────────────────────────────────────────────

async def random_scroll(page: Page) -> None:
    """페이지를 자연스럽게 조금 스크롤."""
    dist = random.randint(100, 400)
    await page.evaluate(f"window.scrollBy(0, {dist})")
    await rand_sleep(200, 500)
    await page.evaluate(f"window.scrollBy(0, -{dist // 2})")
