import json
import sys
from typing import Any, Dict


REQUIRED_KEYS = {
    "credentials": ["id", "password"],
    "target": ["center_value", "part_value"],
    "schedule": ["open_datetime"],
    "browser": ["headless"],
}


def load_config(path: str = "config.json") -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.exit(f"[오류] 설정 파일을 찾을 수 없습니다: {path}\n  config.example.json 을 복사해 config.json 을 만드세요.")
    except json.JSONDecodeError as e:
        sys.exit(f"[오류] 설정 파일 JSON 파싱 실패: {e}")

    for section, keys in REQUIRED_KEYS.items():
        if section not in cfg:
            sys.exit(f"[오류] 설정 파일에 '{section}' 섹션이 없습니다.")
        for key in keys:
            if key not in cfg[section]:
                sys.exit(f"[오류] 설정 파일 '{section}.{key}' 값이 없습니다.")

    cfg.setdefault("schedule", {}).setdefault("advance_seconds", 30)
    cfg.setdefault("browser", {}).setdefault("slow_mo", 50)

    target = cfg["target"]
    target.setdefault("rent_type", "1001")          # 1001=체육경기
    target.setdefault("start_times", ["1800", "2000"])
    target.setdefault("place_values", [{"label": "1코트", "value": "61"}])

    # 시작시각은 "1800" 형태의 4자리 HHMM 이어야 슬롯 매칭이 된다.
    normalized = []
    for t in target["start_times"]:
        t = str(t).replace(":", "").strip().zfill(4)
        if not (t.isdigit() and len(t) == 4 and int(t[:2]) < 24 and int(t[2:]) < 60):
            sys.exit(f"[오류] target.start_times 값이 잘못되었습니다: {t!r} (예: \"1800\")")
        normalized.append(t)
    target["start_times"] = normalized

    # 날짜는 YYYYMMDD (달력 링크 id 가 date-YYYYMMDD 형식)
    dates = []
    for d in target.get("specific_dates", []):
        d = str(d).replace("-", "").strip()
        if len(d) != 8 or not d.isdigit():
            sys.exit(f"[오류] target.specific_dates 값이 잘못되었습니다: {d!r} (예: \"20261003\")")
        dates.append(d)
    if dates:
        target["specific_dates"] = dates
        # 달력 이동 목표월을 날짜에서 자동 추론 (설정 누락/불일치 방지)
        target.setdefault("target_year",  int(dates[0][:4]))
        target.setdefault("target_month", int(dates[0][4:6]))
        months = {d[:6] for d in dates}
        if len(months) > 1:
            sys.exit(f"[오류] specific_dates 가 여러 달에 걸쳐 있습니다: {sorted(months)}")

    return cfg
