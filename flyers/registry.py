import os
import json
import logging

logger = logging.getLogger("discord_bot")

FLYER_REGISTRY = os.getenv("FLYER_REGISTRY", "data/flyers.json")


def sorted_pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted([a, b]))


def flyer_key(season: int | str, week: int, team_a: str, team_b: str) -> str:
    a, b = sorted_pair(team_a, team_b)
    return f"season:{season}:week:{week}:{a}:{b}"


def _load_registry() -> dict:
    try:
        with open(FLYER_REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load flyer registry: {e}")
        return {}


def _save_registry(data: dict) -> None:
    registry_dir = os.path.dirname(FLYER_REGISTRY)

    if registry_dir:
        os.makedirs(registry_dir, exist_ok=True)

    tmp = FLYER_REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp, FLYER_REGISTRY)


def registry_has(season: int | str, week: int, t1: str, t2: str) -> bool:
    reg = _load_registry()
    return flyer_key(season, week, t1, t2) in reg


def registry_put(season: int | str, week: int, t1: str, t2: str, record: dict) -> None:
    reg = _load_registry()
    reg[flyer_key(season, week, t1, t2)] = record
    _save_registry(reg)
