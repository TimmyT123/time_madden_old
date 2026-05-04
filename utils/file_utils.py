# utils/file_utils.py

import json
import os
import hashlib


def load_json_file(path, default=None, logger=None):
    if default is None:
        default = {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except FileNotFoundError:
        return default

    except Exception as e:
        if logger:
            logger.warning(f"Failed to load JSON file {path}: {e}")
        return default


def save_json_file(path, data, indent=2, logger=None):
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)

        tmp_path = path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)

        os.replace(tmp_path, path)
        return True

    except Exception as e:
        if logger:
            logger.warning(f"Failed to save JSON file {path}: {e}")
        return False


def hash_json(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode("utf-8")
    ).hexdigest()


def save_json_if_changed(path, new_data, logger=None, indent=2):
    old_data = load_json_file(path, default=None, logger=logger)

    if old_data is not None and hash_json(old_data) == hash_json(new_data):
        if logger:
            logger.info(f"{path} unchanged — not rewriting")
        return False

    return save_json_file(path, new_data, indent=indent, logger=logger)


def load_week_state(week_state_file, logger=None):
    data = load_json_file(
        week_state_file,
        default={
            "week": 0,
            "matchups": [],
            "pre_reminder_sent": False,
            "advance_time": None
        },
        logger=logger
    )

    if "pre_reminder_sent" not in data:
        data["pre_reminder_sent"] = False

    if "advance_time" not in data:
        data["advance_time"] = None

    return data


def save_week_state(
    week_state_file,
    wk,
    pairs,
    pre_sent=None,
    advance_time="__KEEP__",
    logger=None
):
    existing = load_week_state(week_state_file, logger=logger)

    data = {
        "week": wk,
        "matchups": pairs,
        "pre_reminder_sent": (
            pre_sent if pre_sent is not None
            else existing.get("pre_reminder_sent", False)
        ),
        "advance_time": (
            existing.get("advance_time")
            if advance_time == "__KEEP__"
            else advance_time
        )
    }

    return save_json_file(week_state_file, data, logger=logger)


def get_current_week_and_matchups_from_file(week_state_file, logger=None):
    st = load_week_state(week_state_file, logger=logger)
    return int(st.get("week", 0)), st.get("matchups", [])


def load_playtime_map(playtime_file, logger=None) -> dict[str, str]:
    data = load_json_file(playtime_file, default={}, logger=logger)
    return {str(k): str(v) for k, v in data.items()}


def save_playtime_map(playtime_file, data: dict[str, str], logger=None) -> bool:
    return save_json_file(playtime_file, data, logger=logger)


def load_gotw_config_from_file(gotw_config_file, logger=None):
    default_config = {
        "enabled": True,
        "start_week": 5,
        "min_games": 2,
        "max_games": 5,
        "delay_seconds": 480
    }

    loaded = load_json_file(gotw_config_file, default={}, logger=logger)

    if isinstance(loaded, dict):
        default_config.update(loaded)

    return default_config


def load_gotw_state_from_file(gotw_state_file, logger=None):
    return load_json_file(gotw_state_file, default={}, logger=logger)


def save_gotw_state_to_file(gotw_state_file, state, logger=None):
    return save_json_file(gotw_state_file, state, logger=logger)


def save_week_cache_if_changed(week_cache_path, new_cache, logger=None):
    changed = save_json_if_changed(week_cache_path, new_cache, logger=logger)

    if changed and logger:
        logger.info(f"Week cache written: {week_cache_path}")

    return changed


def load_notified_set(ap_notified_file, logger=None):
    raw = load_json_file(ap_notified_file, default=[], logger=logger)

    try:
        return set(tuple(x) for x in raw)
    except Exception:
        return set()


def save_notified_set(ap_notified_file, notified_set: set[tuple], logger=None):
    return save_json_file(ap_notified_file, list(notified_set), logger=logger)


def load_last_ap_state_from_file(ap_state_file, logger=None):
    return load_json_file(ap_state_file, default=[], logger=logger)


def save_ap_state_to_file(ap_state_file, state, logger=None):
    return save_json_file(ap_state_file, state, logger=logger)
