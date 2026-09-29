#!/usr/bin/env python3
"""Робот-пылесос Roborock Qrevo CurvC — состояние и команды для страницы «Дом».

Библиотека python-roborock (на mini в ~/.local/python312). Вход — одноразовый, кодом из письма
Roborock (`login request` → `login <код>`), дальше живёт сохранённый ключ `data/roborock-auth.json`.
Робот говорит протоколом V1 и по локальной сети отвечает сам (TCP 58867, ключ из облака лежит в
`data/roborock-cache.json`), облако нужно для первого знакомства и как запасной путь через MQTT.
Аккаунт российского региона (ruiot / mqtt-ru.roborock.com) — с финского выхода VPN он молчит,
поэтому 69.17.16.248/29 в deploy/direct-routes.txt.

  python3 scripts/vacuum.py state                     # что видит страница (JSON)
  python3 scripts/vacuum.py start [--mode vacuum|vac_and_mop|mop]
  python3 scripts/vacuum.py rooms 1,2 [--mode …]      # уборка комнат по id сегментов
  python3 scripts/vacuum.py pause | dock | find
  python3 scripts/vacuum.py login request | login <код>
"""
import asyncio
import json
import os
import pathlib
import pickle
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH = os.path.join(ROOT, "data", "roborock-auth.json")
CACHE = os.path.join(ROOT, "data", "roborock-cache.json")
LOGIN = os.path.join(ROOT, "data", "roborock-login.json")   # device id между запросом кода и входом
EMAIL = os.environ.get("ROBOROCK_EMAIL") or "s.s.filatov94@gmail.com"
TIMEOUT = 25

STATES = {
    "starting": "запускается", "charger_disconnected": "снят с базы", "idle": "ждёт",
    "remote_control_active": "на пульте", "cleaning": "убирает", "returning_home": "едет на базу",
    "manual_mode": "ручной режим", "charging": "на базе", "charging_problem": "не заряжается",
    "paused": "на паузе", "spot_cleaning": "убирает пятно", "error": "ошибка",
    "shutting_down": "выключается", "updating": "обновляется", "docking": "паркуется",
    "going_to_target": "едет к точке", "zoned_cleaning": "убирает зону",
    "segment_cleaning": "убирает комнаты", "emptying_the_bin": "база выгружает пыль",
    "washing_the_mop": "моет швабру", "washing_the_mop_2": "моет швабру",
    "going_to_wash_the_mop": "едет мыть швабру", "mapping": "строит карту",
    "attaching_the_mop": "надевает швабру", "detaching_the_mop": "снимает швабру",
    "charging_complete": "на базе, заряжен", "device_offline": "не в сети", "locked": "заблокирован",
    "air_drying_stopping": "сушка", "robot_status_mopping": "моет пол",
    "segment_mopping": "моет комнаты", "zoned_mopping": "моет зону",
    "back_to_dock_washing_duster": "едет мыть швабру",
}
# «Занят»: команда «убрать» в этих состояниях не нужна, нужна «пауза» / «домой».
BUSY = {"starting", "cleaning", "returning_home", "spot_cleaning", "zoned_cleaning", "segment_cleaning",
        "going_to_target", "washing_the_mop", "washing_the_mop_2", "going_to_wash_the_mop", "mapping",
        "robot_status_mopping", "segment_mopping", "zoned_mopping", "back_to_dock_washing_duster",
        "clean_mop_cleaning", "clean_mop_mopping", "segment_clean_mop_cleaning",
        "segment_clean_mop_mopping", "zoned_clean_mop_cleaning", "zoned_clean_mop_mopping", "docking"}
MODES = {"vacuum": "Пылесос", "vac_and_mop": "Пылесос и швабра", "mop": "Швабра"}
# Ресурс в секундах работы (как в приложении Roborock): основная щётка 300 ч, боковая 200 ч,
# фильтр 150 ч, датчики 30 ч.
CONSUMABLES = (("main_brush", "основная щётка"), ("side_brush", "боковая щётка"),
               ("filter", "фильтр"), ("sensor", "датчики"))


def _need_lib():
    try:
        import roborock  # noqa: F401
    except ImportError:
        raise RuntimeError("нет python-roborock: ~/.local/python312/bin/python3 -m pip install python-roborock")


async def _manager():
    from roborock.data import UserData
    from roborock.devices.device_manager import UserParams, create_device_manager
    from roborock.devices.file_cache import FileCache
    if not os.path.exists(AUTH):
        raise FileNotFoundError("нет входа в Roborock: python3 scripts/vacuum.py login request")
    with open(AUTH) as f:
        a = json.load(f)
    up = UserParams(username=a["username"], user_data=UserData.from_dict(a["user_data"]), base_url=a.get("base_url"))
    cache = FileCache(pathlib.Path(CACHE), serialize_fn=_pickle_cache)
    return await create_device_manager(up, cache=cache), cache


def _pickle_cache(data):
    """python-roborock кладёт в кэш `device_features` живым трейтом со ссылкой на RPC-канал, и pickle
    на нём падает, оставляя пустой файл. Он пересчитывается при каждом подключении — его не пишем."""
    saved = {}
    for duid, dc in (data.device_info or {}).items():
        saved[duid], dc.device_features = dc.device_features, None
    try:
        return pickle.dumps(data)
    finally:
        for duid, f in saved.items():
            data.device_info[duid].device_features = f


def _run(fn):
    """Одна операция = одно соединение: serve.py синхронный, держать живую сессию ему негде."""
    _need_lib()

    async def go():
        dm, cache = await _manager()
        try:
            devs = [d for d in await dm.get_devices() if d.v1_properties]
            if not devs:
                raise RuntimeError("робот не найден в аккаунте Roborock")
            return await fn(devs[0])
        finally:
            await cache.flush()          # IP, ключ и карта робота: без них каждый раз пришлось бы идти в облако
            await dm.close()
    return asyncio.run(asyncio.wait_for(go(), TIMEOUT))


def _pct(left, used):
    if left is None or used is None or left + used <= 0:
        return None
    return round(100 * left / (left + used))


async def _state(d):
    p = d.v1_properties
    await p.status.refresh()
    s = p.status
    out = {
        "name": d.name,
        "state": s.state_name,
        "stateText": STATES.get(s.state_name or "", s.state_name or "?"),
        "busy": (s.state_name or "") in BUSY,
        "battery": s.battery,
        "charging": bool(s.charge_status and int(s.charge_status) == 1),
        "error": None if not s.error_code or int(s.error_code) == 0 else s.error_code_name,
        "mode": s.current_cleaning_mode.value if s.current_cleaning_mode else None,
        "modes": [{"id": k, "title": v} for k, v in MODES.items()],
        "fan": s.fan_speed_name,
        "area": s.square_meter_clean_area,           # м² последней/текущей уборки
        "minutes": round((s.clean_time or 0) / 60),
        "lastClean": s.last_clean_t,
        "dock": {
            "cleanWater": s.clear_water_box_status is not None and int(s.clear_water_box_status) != 0,
            "dirtyWater": s.dirty_water_box_status is not None and int(s.dirty_water_box_status) != 0,
            "dustBag": s.dust_bag_status is not None and int(s.dust_bag_status) != 0,
            "error": None if not s.dock_error_status or int(s.dock_error_status) == 0 else s.dock_error_status.name,
        },
        "local": bool(getattr(d, "is_local_connected", False)),
    }
    try:
        await p.maps.refresh()
        info = p.maps.current_map_info or (p.maps.map_info[0] if p.maps.map_info else None)
        rooms = [{"id": r.id, "name": r.iot_name if r.iot_name and r.iot_name != "Room" else f"Комната {r.id}"}
                 for r in (info.rooms if info else []) or []]
        out["rooms"] = rooms
    except Exception as e:
        out["rooms"], out["roomsError"] = [], f"{type(e).__name__}: {e}"
    try:
        await p.consumables.refresh()
        c = p.consumables
        out["consumables"] = [
            {"id": k, "title": t,
             "left": _pct(getattr(c, f"{k}_time_left", None),
                          getattr(c, f"{k}_work_time", None) if k != "sensor" else c.sensor_dirty_time)}
            for k, t in CONSUMABLES]
        out["consumables"] = [x for x in out["consumables"] if x["left"] is not None]
    except Exception:
        out["consumables"] = []
    return out


def state():
    return _run(_state)


def command(action, rooms=None, mode=None):
    """start | rooms | pause | dock | find. Режим (mode) ставится перед стартом и остаётся у робота."""
    from roborock import RoborockCommand as C
    if action not in ("start", "rooms", "pause", "dock", "find"):
        raise ValueError(f"нет такой команды: {action}")
    if mode is not None and mode not in MODES:
        raise ValueError(f"нет такого режима: {mode}")
    if action == "rooms":
        rooms = [int(r) for r in (rooms or [])]
        if not rooms:
            raise ValueError("какие комнаты?")

    async def go(d):
        p = d.v1_properties
        if mode and action in ("start", "rooms"):
            await p.status.set_cleaning_mode(mode)
        if action == "start":
            await p.command.send(C.APP_START)
        elif action == "rooms":
            await p.command.send(C.APP_SEGMENT_CLEAN, [{"segments": rooms, "repeat": 1}])
        elif action == "pause":
            await p.command.send(C.APP_PAUSE)
        elif action == "dock":
            await p.command.send(C.APP_CHARGE)
        elif action == "find":
            await p.command.send(C.FIND_ME)
        await asyncio.sleep(2)            # чтобы в ответе было уже новое состояние
        return await _state(d)
    return _run(go)


def login(step, code=None):
    """Вход кодом из письма. Код приходит на EMAIL; device id должен совпасть у запроса и входа."""
    _need_lib()
    from roborock.web_api import RoborockApiClient

    async def go():
        if step == "request":
            c = RoborockApiClient(EMAIL)
            await c.request_code_v4()
            os.makedirs(os.path.dirname(LOGIN), exist_ok=True)
            with open(LOGIN, "w") as f:
                json.dump({"dev": c._device_identifier, "base_url": await c.base_url}, f)
            return f"код отправлен на {EMAIL}"
        with open(LOGIN) as f:
            st = json.load(f)
        c = RoborockApiClient(EMAIL, base_url=st["base_url"])
        c._device_identifier = st["dev"]
        ud = await c.code_login_v4(code)
        with open(AUTH, "w") as f:
            json.dump({"username": EMAIL, "base_url": st["base_url"], "user_data": ud.as_dict()}, f)
        os.chmod(AUTH, 0o600)
        os.remove(LOGIN)
        return "вход сохранён"
    return asyncio.run(go())


def main(argv):
    args = argv[1:] or ["state"]
    mode = None
    if "--mode" in args:
        i = args.index("--mode")
        mode = args[i + 1]
        del args[i:i + 2]
    cmd = args[0]
    t = time.time()
    if cmd == "state":
        out = state()
    elif cmd == "login":
        out = login(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "rooms":
        out = command("rooms", rooms=args[1].split(","), mode=mode)
    elif cmd in ("start", "pause", "dock", "find"):
        out = command(cmd, mode=mode)
    else:
        print(__doc__)
        return
    print(json.dumps(out, ensure_ascii=False, indent=2) if not isinstance(out, str) else out)
    print(f"({time.time() - t:.1f} с)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
