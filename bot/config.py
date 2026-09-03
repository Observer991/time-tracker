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
    cfg["target"].setdefault("purpose", "체육경기")

    return cfg
