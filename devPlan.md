# Time Tracker — 설계 계획

## Context

작업시간 추적 및 일정관리 웹 애플리케이션을 Python 기반으로 신규 제작한다.  
디렉토리 `/Users/a16540/DevProjects/time-tracker`는 현재 빈 Git 저장소 상태.

- **인터페이스**: 브라우저 웹앱 (로컬 단일 사용자)
- **기능**: 타이머, 할 일/캘린더, 리포트/통계, 프로젝트·태그 분류
- **Python 버전**: 3.9.6 → `typing.List`, `typing.Optional` 필수 (신형 `list[int]` 불가)

---

## 기술 스택

| 레이어 | 기술 | 비고 |
|--------|------|------|
| Backend | FastAPI 0.115.0 | async, 자동 API 문서 |
| ORM | SQLAlchemy 2.0.35 | |
| DB | SQLite (WAL 모드) | 파일 기반, 설치 불필요 |
| 템플릿 | Jinja2 3.1.4 | |
| 동적 UI | HTMX 1.9.12 (CDN) | 서버 주도 부분 업데이트 |
| 상태 관리 | Alpine.js 3.14.1 (CDN) | 타이머 카운트업 로컬 상태 |
| 스타일 | Tailwind CSS (CDN) | |
| 차트 | Chart.js 4.4.3 (CDN) | |
| 서버 | uvicorn[standard] 0.30.6 | |

CDN 로드 순서 (base.html): Tailwind → HTMX → Alpine(`defer`) → Chart.js  
Alpine을 HTMX 뒤에 `defer`로 로드해야 HTMX swap된 요소를 Alpine이 감지함.

---

## 프로젝트 구조

```
time-tracker/
├── requirements.txt
├── run.py                          # uvicorn 진입점
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 앱 + 라우터 등록
│   ├── database.py                 # 엔진, 세션, Base (WAL PRAGMA 포함)
│   ├── models.py                   # ORM 모델 전체
│   ├── schemas.py                  # Pydantic 응답 스키마
│   └── routers/
│       ├── __init__.py
│       ├── views.py                # 전체 페이지 HTML GET 라우트
│       ├── timer.py                # 타이머 start/stop + TimeEntry CRUD
│       ├── projects.py             # 프로젝트 CRUD
│       ├── tags.py                 # 태그 CRUD
│       ├── tasks.py                # 할 일 CRUD + 상태 토글
│       ├── reports.py              # 집계 → JSON (Chart.js용)
│       └── partials.py             # HTMX 파편 라우트 (/partials/*)
└── templates/
    ├── base.html
    ├── dashboard.html
    ├── timer.html
    ├── tasks.html
    ├── calendar.html
    ├── reports.html
    ├── projects.html
    └── partials/
        ├── timer_widget.html       # Alpine.js 카운트업 + HTMX 30s 폴링
        ├── task_item.html
        ├── task_list.html
        ├── time_entry_row.html
        ├── time_entry_list.html
        └── calendar_grid.html
```

`static/` 디렉토리 불필요 — 모든 JS/CSS는 CDN.

---

## 데이터 모델 (`app/models.py`)

### 연관 테이블 (M2M)
```python
time_entry_tags = Table("time_entry_tags", Base.metadata,
    Column("time_entry_id", Integer, ForeignKey("time_entries.id", ondelete="CASCADE")),
    Column("tag_id",        Integer, ForeignKey("tags.id",         ondelete="CASCADE")),
)
task_tags = Table("task_tags", Base.metadata,
    Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE")),
    Column("tag_id",  Integer, ForeignKey("tags.id",  ondelete="CASCADE")),
)
```

### Project
| 필드 | 타입 | 비고 |
|------|------|------|
| id | Integer PK | |
| name | String(100) | unique |
| color | String(7) | hex, 기본 `#6366f1` |
| description | Text | nullable |
| is_active | Boolean | 기본 True (소프트 삭제) |
| created_at | DateTime | |

### Tag
| 필드 | 타입 | 비고 |
|------|------|------|
| id | Integer PK | |
| name | String(50) | unique |
| color | String(7) | 기본 `#64748b` |

### TimeEntry
| 필드 | 타입 | 비고 |
|------|------|------|
| id | Integer PK | |
| project_id | Integer FK | nullable |
| description | String(500) | 기본 `""` |
| start_time | DateTime | not null |
| end_time | DateTime | **null = 실행 중** |
| duration_seconds | Integer | null일 때는 제외, stop 시 계산 |
| is_running | Boolean | 기본 False |
| created_at | DateTime | |

**불변 조건**: `is_running=True`인 행은 최대 1개. `start_timer` 실행 시 기존 실행 중 항목을 먼저 stop.

### Task
| 필드 | 타입 | 비고 |
|------|------|------|
| id | Integer PK | |
| project_id | Integer FK | nullable |
| title | String(500) | |
| description | Text | nullable |
| due_date | Date | nullable |
| scheduled_date | Date | nullable (캘린더 표시용) |
| priority | Integer | 1=low / 2=medium / 3=high |
| status | String(20) | `todo` / `in_progress` / `done` |
| created_at | DateTime | |
| completed_at | DateTime | status→done 시 기록 |

---

## API 라우트

### 전체 페이지 (`views.py`)
| GET | 경로 | 설명 |
|-----|------|------|
| `/` | 대시보드 | 타이머 위젯 + 오늘 요약 |
| `/timer` | 타이머 | 시작 폼 + 기록 목록 |
| `/tasks` | 할 일 | 필터 + 목록 |
| `/calendar` | 캘린더 | 월별 뷰 |
| `/reports` | 리포트 | Chart.js 차트 |
| `/projects` | 프로젝트 | 프로젝트·태그 관리 |

### HTMX 파편 (`partials.py`)
| GET | `/partials/timer` | 타이머 위젯만 |
| `/partials/time-entries` | 기록 테이블 (`date`, `project_id`) |
| `/partials/tasks` | 할 일 목록 (`status`, `project_id`, `date`) |
| `/partials/calendar/{year}/{month}` | 월 그리드 |

### 타이머/기록 (`timer.py`)
| Method | 경로 | 설명 |
|--------|------|------|
| POST | `/api/timer/start` | 타이머 시작 (Form: `description`, `project_id`, `tag_ids[]`) |
| POST | `/api/timer/stop` | 타이머 정지 |
| GET | `/api/timer/current` | JSON: 현재 실행 중 항목 |
| GET | `/api/time-entries` | 목록 |
| POST | `/api/time-entries` | 수동 기록 |
| PUT | `/api/time-entries/{id}` | 수정 |
| DELETE | `/api/time-entries/{id}` | 삭제 |

### 할 일 (`tasks.py`)
| POST/GET | `/api/tasks` | 생성/목록 |
| PUT/DELETE | `/api/tasks/{id}` | 수정/삭제 |
| PATCH | `/api/tasks/{id}/status` | 상태 변경 → Task HTML 반환 |

### 리포트 (`reports.py`) — JSON 반환
| GET | `/api/reports/daily` | `?date=YYYY-MM-DD` |
| GET | `/api/reports/weekly` | `?start=YYYY-MM-DD` |
| GET | `/api/reports/monthly` | `?year=&month=` |
| GET | `/api/reports/projects` | `?start=&end=` |

---

## 타이머 구현 (핵심)

Alpine.js + HTMX 결합으로 WebSocket 없이 라이브 카운트업 구현:

```html
<!-- templates/partials/timer_widget.html -->
<div id="timer-widget"
     x-data="{
       elapsed: {{ elapsed_seconds }},
       isRunning: {{ 'true' if is_running else 'false' }},
       _iv: null,
       init() { if (this.isRunning) this._iv = setInterval(() => this.elapsed++, 1000) },
       destroy() { clearInterval(this._iv) },
       fmt() {
         const h = Math.floor(this.elapsed / 3600)
         const m = Math.floor((this.elapsed % 3600) / 60)
         const s = this.elapsed % 60
         return [h,m,s].map(n => String(n).padStart(2,'0')).join(':')
       }
     }"
     hx-get="/partials/timer"
     hx-trigger="every 30s"
     hx-swap="outerHTML">
  <div class="text-6xl font-mono" x-text="fmt()"></div>
  ...
</div>
```

- **1초마다**: Alpine `setInterval`이 `elapsed++` (네트워크 없음)
- **30초마다**: HTMX가 `/partials/timer` GET → 서버 기준 시간으로 위젯 재동기화
- **start/stop**: HTMX POST → 서버가 새 위젯 HTML 반환 → Alpine 재초기화

---

## 주요 구현 주의사항

1. **Python 3.9**: `list[int]` 대신 `from typing import List, Optional, Dict` 사용
2. **SQLite WAL**: `database.py`에서 `PRAGMA journal_mode=WAL` 설정 (잠금 오류 방지)
3. **Alpine + HTMX 순서**: base.html에서 HTMX → Alpine(`defer`) 순으로 로드
4. **실행 중 항목 리포트 제외**: 집계 쿼리에 `TimeEntry.end_time.isnot(None)` 조건 추가
5. **다중 태그 Form**: FastAPI에서 `tag_ids: List[int] = Form(default=[])` 선언

---

## 구현 순서

| 단계 | 내용 |
|------|------|
| 1 | 기반: `requirements.txt`, `run.py`, `database.py`, `models.py`, `main.py`, `base.html` |
| 2 | 타이머: start/stop API + `timer_widget.html` + 대시보드 |
| 3 | 프로젝트/태그: CRUD + `projects.html` |
| 4 | 타이머 페이지: 기록 목록 + inline 편집 |
| 5 | 할 일: CRUD + 상태 토글 + `tasks.html` |
| 6 | 캘린더: 월별 그리드 + 날짜 상세 |
| 7 | 리포트: 집계 쿼리 + Chart.js 차트 |

---

## 검증

```bash
cd /Users/a16540/DevProjects/time-tracker
pip install -r requirements.txt
python run.py
# → http://localhost:8000
```

확인 항목:
- 타이머 시작 → 카운트업 표시 → 정지 후 기록 저장
- 할 일 체크박스 클릭 → 페이지 갱신 없이 상태 변경
- 리포트 페이지 차트에 데이터 표시
- 캘린더 날짜별 시간 기록 표시
- 이전/다음 달 이동 (HTMX 부분 갱신)
