# 군포시 테니스 코트 자동 예약 봇

군포시 통합예약시스템(gunpouc.or.kr)의 시민체육광장 테니스장 **추첨 신청**을 자동화한다.

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

> Python 3.13 에서는 `playwright==1.47.0` 이 greenlet 빌드 실패로 설치되지 않는다.
> `requirements.txt` 는 `playwright>=1.49` 로 고정되어 있다.

## 설정

`config.example.json` 을 복사해 `config.json` 을 만든다 (git 추적 제외됨).

```json
{
  "credentials": { "id": "아이디", "password": "비밀번호" },
  "target": {
    "center_value": "GUNPO02",
    "part_value": "14",
    "place_values": [
      { "label": "1코트", "value": "61" },
      { "label": "2코트", "value": "62" }
    ],
    "specific_dates": ["20261003", "20261004"],
    "start_times": ["1800", "2000"],
    "rent_type": "1001"
  },
  "form": {
    "team_nm": "대표자명", "title": "테니스", "purpose": "테니스",
    "users": "4", "mobile_tel": ""
  },
  "schedule": { "open_datetime": "2026-10-01 10:00:00", "advance_seconds": 30 },
  "browser": { "headless": false }
}
```

| 키 | 설명 |
|----|------|
| `place_values` | 코트 목록. 1~9코트 = `61`~`69` |
| `specific_dates` | `YYYYMMDD`. 한 달 안의 날짜여야 함 (달력 이동 대상월을 여기서 추론) |
| `start_times` | 신청할 **시작시각** `HHMM`. 정확히 일치하는 슬롯만 신청한다 |
| `rent_type` | `1001`=체육경기, `2001`=체육이외경기 |
| `mobile_tel` | 비우면 계정 기본값 사용 |

## 실행

```bash
.venv/bin/python main.py now          # 즉시 신청
.venv/bin/python main.py run          # schedule.open_datetime 에 맞춰 대기 후 실행
.venv/bin/python main.py login-test   # 로그인/세션 확인
.venv/bin/python main.py history      # 사이트 기준 신청내역 조회
```

## 동작 개요

1. 영속 프로필(`.chrome-profile`)로 실제 Chrome 실행 → 쿠키(`session.json`)를 복원해 재로그인 회피
2. 센터/시설/코트 드롭다운 선택 후 **선택값이 실제 반영됐는지 검증**
3. 목표월로 달력 이동 → 날짜 클릭 후 `base_date` 로 **선택 날짜 검증**
4. `start_times` 와 정확히 일치하는 슬롯만 신청 → 폼 작성 → 제출
5. 제출 결과를 alert / 폼 잔류 / 본문 문구로 3-상태(성공·실패·판정불가) 판정
6. 실행 후 대관내역을 조회해 사이트 기준으로 교차 검증

성공한 건은 `applied.json` 에 기록되어 재실행 시 중복 신청하지 않는다.

## 주의: 같은 id 를 쓰는 `<td>` 와 `<a>`

달력의 날짜는 `<td id="date-20261003">` 와 그 안의 `<a id="date-20261003">` 가
**같은 id** 를 공유한다. `document.getElementById()` 는 `<td>` 를 반환하는데
`<td>` 에는 클릭 핸들러가 없어 클릭이 무시되고, 페이지 기본 날짜(그 달 1일)의
시간표가 그대로 남는다 → **엉뚱한 날짜로 신청되는 사고**가 난다.
반드시 `document.querySelector('a[id="date-..."]')` 로 `<a>` 를 잡아야 하며,
클릭 후 `input[name="base_date"]` 값으로 반영 여부를 검증한다.

## 사이트 제약: 사용일 1일당 2건

테니스장은 **같은 사용일에 최대 2건**까지만 신청할 수 있다. 상한을 넘기면
`업장 신청일 최대 예약 제한 횟수(2회) 이상 예약 할 수 없습니다.` 라는 alert 가 뜬다.

- 이 제한은 **코트가 아니라 날짜 단위**다. 1코트로 18시·20시를 채우면
  같은 날 2·3코트는 신청할 수 없다.
- 따라서 여러 코트를 `place_values` 에 넣어도 하루 2건이 상한이며,
  봇은 상한 메시지를 만나면 그 날짜의 남은 슬롯·코트 시도를 건너뛴다.
- 같은 장소·같은 시간 중복 신청도 막힌다:
  `동일한 장소, 동일한 시간에 1회만 신청이 가능합니다.`

## 세션 만료와 동시 접속

사이트는 **같은 계정의 동시 접속을 허용하지 않는다.** 봇이 도는 중에 브라우저로
같은 계정에 로그인하면 봇 세션이 끊기고, 이후 신청은 전부
`로그인을 하셔야만 이용가능합니다.` alert 만 뜨며 조용히 실패한다.

봇은 이 문구를 감지하면 자동으로 재로그인하고 그 슬롯을 한 번 더 시도한다.
그래도 실행 중에는 다른 곳에서 같은 계정을 쓰지 않는 편이 안전하다.

## 휴대전화번호 형식

`mobile_tel` 은 **하이픈 형식**이어야 통과한다. `01012345678` 처럼 넣으면
`휴대전화번호를 정확히 입력하세요.` 로 거부되므로, 설정값은 자동으로
`010-1234-5678` 형태로 변환된다. 제출 직전 실제 입력값을 다시 읽어
불일치하면 제출을 중단한다.

접수된 신청은 **수정할 수 없고 예약취소만 가능**하다
(`?rent_no=...&comcd=...&action=cancel`). 연락처를 바꾸려면 취소 후 재신청해야 한다.

## 추첨 일정 (사이트 공지 기준)

- 관내 추첨 접수: 사용일 **전월 1일 10:00 ~ 3일 23:59**
- 추첨 발표: 전월 **4일**
- 추첨 후 잔여분은 사용일 전일 23:59까지 실시간 예약 가능
