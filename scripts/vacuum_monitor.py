#!/usr/bin/env python3
"""Текущие ошибки Roborock для BoW; состояние машины в data/roborock-alerts.json.

Вызывается перед уборкой в 10:00 и при запуске в 10:30. Уведомление сохраняет id, пока проблема
не исчезнет; после устранения и повторного появления получает новый id.
"""
import datetime as dt
import json
import os
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "roborock-alerts.json")
STALE_AFTER = dt.timedelta(hours=2)   # даём iOS время забрать результат утренней проверки
DOCK_ERRORS = {
    "water_empty": ("water", "Долей чистой воды и проверь, что бак установлен правильно."),
    "waste_water_tank_full": ("dirty", "Вылей грязную воду и проверь установку бака."),
    "no_dustbin_or_filter": ("bag", "Проверь установку пылесборника и фильтра станции."),
    "no_dustbin": ("bag", "Установи пылесборник станции."),
    "auto_empty_dock_fan_error": ("dock", "Ошибка вентилятора станции. Проверь пылесборник и фильтр."),
    "duct_blockage": ("dock", "Засор в станции. Очисти пылесборник, фильтр и воздуховод."),
    "auto_empty_dock_voltage_error": ("dock", "Ошибка питания станции: не удалось выгрузить пыль."),
    "maintenance_brush_jammed": ("dock", "Заклинила щётка самоочистки станции. Сними и очисти её."),
    "dirty_tank_latch_open": ("dirty", "Очисти фильтр воды и закрой крышку и защёлку бака грязной воды."),
    "cleaning_tank_full_or_blocked": ("dock", "Поддон станции переполнен или засорён. Проверь слив и фильтр."),
}


def problems(s):
    out = {}
    dock = s.get("dock") or {}
    if dock.get("cleanWater"):
        out["water"] = "Долей чистой воды и проверь установку бака станции."
    if dock.get("dirtyWater"):
        out["dirty"] = "Вылей грязную воду и проверь установку бака станции."
    if dock.get("dustBag"):
        out["bag"] = "Замени мешок для пыли и проверь его установку в станции."
    if dock.get("error"):
        code = str(dock["error"])
        key, body = DOCK_ERRORS.get(code, ("dock:" + code, "Ошибка станции: " + code + ". Проверь станцию в приложении Roborock."))
        if key == "dock":
            key += ":" + code
        out[key] = body
    if s.get("error"):
        code = str(s["error"])
        out["robot:" + code] = "Ошибка робота: " + code + ". Проверь робот в приложении Roborock."
    elif s.get("state") in {"error", "charging_problem", "locked"}:
        out["robot:" + s["state"]] = "Робот: " + s.get("stateText", s["state"]) + ". Проверь его в приложении Roborock."
    if s.get("state") == "device_offline":
        out["offline"] = "Roborock не в сети. Проверь питание робота, станции и Wi-Fi."
    return out


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def update(snapshot=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.isoformat(timespec="microseconds")
    old = load()
    failed = snapshot is None or snapshot.get("state") == "device_offline"
    failure_since = (old.get("failureSince") or stamp) if failed else None
    if failed:
        current = {k: v["body"] for k, v in old.get("problems", {}).items()}
        current["offline"] = "При проверке перед уборкой Roborock не ответил. Проверь питание, Wi-Fi и подключение Roborock."
    else:
        current = problems(snapshot)
    active = {}
    for key, body in current.items():
        previous = old.get("problems", {}).get(key)
        active[key] = dict(previous, body=body) if previous else {
            "id": "vacuum:" + uuid.uuid4().hex, "at": stamp, "body": body}
    new = {"checkedAt": stamp, "failureSince": failure_since, "problems": active}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE.removesuffix(".json") + ".tmp.json"  # тоже под исключением data/roborock-*.json
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, ensure_ascii=False)
    os.replace(tmp, STATE)
    for key in active.keys() - old.get("problems", {}).keys():
        print(f"{stamp} + {key}: {active[key]['body']}", flush=True)
    for key in old.get("problems", {}).keys() - active.keys():
        print(f"{stamp} − {key}", flush=True)
    return new


def items(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    st = load()
    try:
        if now - dt.datetime.fromisoformat(st["checkedAt"]) > STALE_AFTER:
            return []
    except (KeyError, ValueError, TypeError):
        return []
    return [{**p, "kind": "vacuum", "title": "BoW: Roborock", "page": "home"}
            for p in st.get("problems", {}).values()]
