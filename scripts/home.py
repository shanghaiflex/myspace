#!/usr/bin/env python3
"""Smart home: the Zigbee lamps and the smart plug through Zigbee2MQTT on the mini.

The broker (aedes, `~/mqtt/broker.js`) runs on the mini next to serve.py, so production talks to localhost:1883.
There is no paho on the mini, so this is a minimal MQTT 3.1.1 client on raw sockets: connect, subscribe, publish,
QoS 0 only — enough for Zigbee2MQTT.

Devices (names are Zigbee2MQTT friendly names):
  lamps   group of the two Tuya TS0505B sconces by the painting (lamp + lamp2)
  plug    Tuya TS011F plug with power metering — powers the round IKEA VARMBLIXT on the shelf

  python3 scripts/home.py state                 # what every device reports
  python3 scripts/home.py set lamps '{"state":"ON","brightness":120}'
  python3 scripts/home.py scene cozy | amber | tv | ...
  python3 scripts/home.py effect fire | candle | torch | off
"""
import json, os, select, socket, struct, subprocess, sys, time

# Broker: MQTT_HOST wins; otherwise localhost on the mini (where ~/mqtt lives), else the mini's LAN name and VPN
# address — the laptop has its own idle mosquitto on localhost that knows no devices, so it must not be tried there.
EFFECT_JS = os.path.expanduser(os.environ.get("HOME_EFFECT_JS") or "~/mqtt/effect.js")
ON_MINI = os.path.exists(EFFECT_JS)
HOSTS = ([os.environ["MQTT_HOST"]] if os.environ.get("MQTT_HOST") else ["localhost"] if ON_MINI else ["Mac-mini-Sergey.local", "10.8.1.5"])
PORT = int(os.environ.get("MQTT_PORT") or 1883)
PREFIX = "zigbee2mqtt"
LAMPS = ("lamp", "lamp2")             # members of the group `lamps`
DEVICES = ("lamp", "lamp2", "plug")   # topics that report state
TARGETS = ("lamps", "lamp", "lamp2", "plug")
FIELDS = ("state", "brightness", "color_temp", "color", "color_mode", "power", "voltage", "current", "energy", "last_seen", "linkquality")
ALLOWED = {"state", "brightness", "color_temp", "color", "transition"}

# Static scenes, the same payloads as the `lamp` CLI (~/.claude/skills/lamp/lamp).
SCENES = {
    "cozy":    {"title": "Уютно",   "payload": {"state": "ON", "brightness": 110, "color_temp": 435, "transition": 3}},
    "golden":  {"title": "Золотой", "payload": {"state": "ON", "brightness": 180, "color_temp": 370, "transition": 3}},
    "amber":   {"title": "Янтарь",  "payload": {"state": "ON", "brightness": 140, "color": {"r": 255, "g": 163, "b": 48}, "transition": 3}},
    "candle":  {"title": "Свеча",   "payload": {"state": "ON", "brightness": 70, "color": {"r": 255, "g": 110, "b": 15}, "transition": 3}},
    "sunset":  {"title": "Закат",   "payload": {"state": "ON", "brightness": 120, "color": {"r": 255, "g": 70, "b": 10}, "transition": 3}},
    "tv":      {"title": "Кино",    "payload": {"state": "ON", "brightness": 60, "color": {"r": 190, "g": 200, "b": 255}, "transition": 3}},
    "green":   {"title": "Зелёный", "payload": {"state": "ON", "brightness": 70, "color": {"r": 80, "g": 255, "b": 120}, "transition": 3}},
    "sleepy":  {"title": "Ко сну",  "payload": {"state": "ON", "brightness": 25, "color_temp": 500, "transition": 60}},
    "night":   {"title": "Ночник",  "payload": {"state": "ON", "brightness": 8, "color_temp": 500, "transition": 2}},
}
# Flicker effects run as a node process on the mini (~/mqtt/effect.js), like the CLI does over ssh.
EFFECTS = {"fire": "Камин", "candlelight": "Свеча живая", "torch": "Факел"}
EFFECT_ARG = {"fire": "fire", "candlelight": "candle", "torch": "torch"}
NODE = os.path.expanduser("~/node/bin/node")
SCHEDULE = "19:00 уютно · 22:00 янтарь · 23:00 выкл"   # launchd on the mini (local.lamp.*, local.plug.*)

_host = {"name": None, "at": 0}


# ---------------------------------------------------------------- tiny MQTT

def _varint(n):
    out = b""
    while True:
        b, n = n % 128, n // 128
        out += bytes([b | 0x80]) if n else bytes([b])
        if not n:
            return out


def _str(s):
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def _packet(kind, body):
    return bytes([kind]) + _varint(len(body)) + body


class Mqtt:
    def __init__(self, host, port=PORT, timeout=2.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        self.pid = 1
        cid = f"bow-{os.getpid()}-{int(time.time() * 1000) % 100000}"
        body = _str("MQTT") + bytes([4, 0x02]) + struct.pack(">H", 30) + _str(cid)
        self.sock.sendall(_packet(0x10, body))
        kind, payload = self._read(timeout)
        if kind != 0x20 or payload[1:2] != b"\x00":
            raise ConnectionError(f"mqtt connack {kind:#x} {payload!r}")

    def publish(self, topic, payload):
        if not isinstance(payload, (bytes, str)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode()
        self.sock.sendall(_packet(0x30, _str(topic) + payload))

    def subscribe(self, topics):
        self.pid += 1
        body = struct.pack(">H", self.pid) + b"".join(_str(t) + b"\x00" for t in topics)
        self.sock.sendall(_packet(0x82, body))

    def messages(self, timeout):
        """Yields (topic, payload) until `timeout` seconds have passed."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                return
            try:
                kind, payload = self._read(left)
            except TimeoutError:
                return
            if kind & 0xF0 == 0x30:
                n = struct.unpack(">H", payload[:2])[0]
                topic = payload[2:2 + n].decode(errors="replace")
                rest = payload[2 + n:]
                if (kind >> 1) & 3:           # QoS > 0 carries a packet id
                    rest = rest[2:]
                yield topic, rest

    def _read(self, timeout):
        end = time.time() + timeout
        while True:
            pkt = self._parse()
            if pkt:
                return pkt
            left = end - time.time()
            if left <= 0:
                raise TimeoutError
            r, _, _ = select.select([self.sock], [], [], left)
            if not r:
                raise TimeoutError
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("mqtt closed")
            self.buf += chunk

    def _parse(self):
        if len(self.buf) < 2:
            return None
        n, mult, i = 0, 1, 1
        while True:
            if i >= len(self.buf):
                return None
            b = self.buf[i]
            n += (b & 0x7F) * mult
            mult *= 128
            i += 1
            if not b & 0x80:
                break
        if len(self.buf) < i + n:
            return None
        kind, body = self.buf[0], self.buf[i:i + n]
        self.buf = self.buf[i + n:]
        return kind, body

    def close(self):
        try:
            self.sock.sendall(b"\xe0\x00")
        except OSError:
            pass
        self.sock.close()


def host():
    """The first broker that answers; remembered for 5 minutes."""
    if _host["name"] and time.time() - _host["at"] < 300:
        return _host["name"]
    for h in HOSTS:
        try:
            with socket.create_connection((h, PORT), timeout=1):
                _host.update(name=h, at=time.time())
                return h
        except OSError:
            continue
    raise ConnectionError("mqtt broker unreachable: " + ", ".join(HOSTS))


def connect():
    return Mqtt(host())


# ---------------------------------------------------------------- devices

def state(timeout=2.0):
    """Current report of every device: asks Zigbee2MQTT (`/get`) and waits for the answers."""
    out = {d: None for d in DEVICES}
    c = connect()
    try:
        c.subscribe([f"{PREFIX}/{d}" for d in DEVICES])
        for d in DEVICES:
            c.publish(f"{PREFIX}/{d}/get", {"state": ""})
        for topic, payload in c.messages(timeout):
            d = topic.split("/")[-1]
            try:
                rep = json.loads(payload or b"{}")
            except ValueError:
                continue
            if d in out and isinstance(rep, dict):
                out[d] = {k: rep[k] for k in FIELDS if k in rep}
                if all(out.values()):
                    break
    finally:
        c.close()
    return out


def set_device(name, payload):
    """Publish a Zigbee2MQTT `set` for a device or the group; only the light keys are let through."""
    if name not in TARGETS:
        raise ValueError(f"unknown device {name}")
    body = {}
    for k, v in (payload or {}).items():
        if k not in ALLOWED:
            continue
        if k == "state":
            v = str(v).upper()
            if v not in ("ON", "OFF", "TOGGLE"):
                raise ValueError("state must be ON/OFF/TOGGLE")
        elif k == "brightness":
            v = max(0, min(254, int(v)))
        elif k == "color_temp":
            v = max(153, min(500, int(v)))
        elif k == "transition":
            v = max(0, min(600, float(v)))
        elif k == "color":
            if not isinstance(v, dict) or not all(x in v for x in "rgb"):
                raise ValueError("color must be {r,g,b}")
            v = {x: max(0, min(255, int(v[x]))) for x in "rgb"}
        body[k] = v
    if not body:
        raise ValueError("nothing to set")
    if name != "plug" and any(k in body for k in ("brightness", "color_temp", "color")):
        stop_effect()  # a static setting ends a flicker, like the CLI
    c = connect()
    try:
        c.publish(f"{PREFIX}/{name}/set", body)
    finally:
        c.close()
    return body


def scene(name, target="lamps"):
    if name not in SCENES:
        raise ValueError(f"unknown scene {name}")
    stop_effect()
    return set_device(target, SCENES[name]["payload"])


# ---------------------------------------------------------------- effects

def effect_running():
    """Name of the flicker effect currently running on this machine, or None."""
    try:
        r = subprocess.run(["pgrep", "-fl", "mqtt/effect.js"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    for line in r.stdout.splitlines():
        arg = line.strip().split()[-1]
        for name, a in EFFECT_ARG.items():
            if arg == a:
                return name
    return "fire" if r.stdout.strip() else None


def stop_effect():
    subprocess.run(["pkill", "-f", "mqtt/effect.js"], capture_output=True, timeout=5)


def start_effect(name, target="lamps"):
    if name not in EFFECTS:
        raise ValueError(f"unknown effect {name}")
    if not os.path.exists(EFFECT_JS):
        raise FileNotFoundError("effects run on the mini only (no ~/mqtt/effect.js here)")
    stop_effect()
    node = NODE if os.path.exists(NODE) else "node"
    log = open(os.path.expanduser("~/logs/effect.log"), "ab") if os.path.isdir(os.path.expanduser("~/logs")) else subprocess.DEVNULL
    subprocess.Popen([node, EFFECT_JS, EFFECT_ARG[name]], env={**os.environ, "LAMP": target},
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    return name


def summary():
    """What the page shows: devices, running effect, the scene/effect catalogue."""
    try:
        devs = state()
        err = None
    except Exception as e:
        devs, err = {d: None for d in DEVICES}, f"{type(e).__name__}: {e}"
    return {
        "devices": devs,
        "effect": effect_running(),
        "scenes": [{"id": k, "title": v["title"]} for k, v in SCENES.items()],
        "effects": [{"id": k, "title": v} for k, v in EFFECTS.items()] if os.path.exists(EFFECT_JS) else [],
        "schedule": SCHEDULE,
        "error": err,
    }


# ---------------------------------------------------------------- cli

def main(argv):
    cmd = argv[1] if len(argv) > 1 else "state"
    if cmd == "state":
        print(json.dumps(summary(), ensure_ascii=False, indent=2))
    elif cmd == "set":
        print(set_device(argv[2], json.loads(argv[3])))
    elif cmd == "scene":
        print(scene(argv[2]))
    elif cmd == "effect":
        if argv[2] == "off":
            stop_effect(); print("stopped")
        else:
            print(start_effect(argv[2]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
