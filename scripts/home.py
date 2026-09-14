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
  python3 scripts/home.py scene cozy | dimmed | read | ...

Every set is verified: group commands are Zigbee broadcasts without acknowledgement, and a bulb that missed one
keeps the old light. `transition` + 1.5 s after a command the bulbs are asked what they show, and any bulb that
disagrees gets the same payload again, unicast (acknowledged). Only the latest command per target is verified.
"""
import json, os, select, socket, struct, subprocess, sys, threading, time

# Broker: MQTT_HOST wins; otherwise localhost on the mini (where ~/mqtt lives), else the mini's LAN name and VPN
# address — the laptop has its own idle mosquitto on localhost that knows no devices, so it must not be tried there.
ON_MINI = os.path.exists(os.path.expanduser("~/mqtt/broker.js"))
HOSTS = ([os.environ["MQTT_HOST"]] if os.environ.get("MQTT_HOST") else ["localhost"] if ON_MINI else ["Mac-mini-Sergey.local", "10.8.1.5"])
PORT = int(os.environ.get("MQTT_PORT") or 1883)
PREFIX = "zigbee2mqtt"
LAMPS = ("lamp", "lamp2")             # members of the group `lamps`
DEVICES = ("lamp", "lamp2", "plug")   # topics that report state
TARGETS = ("lamps", "lamp", "lamp2", "plug")
FIELDS = ("state", "brightness", "color_temp", "color", "color_mode", "power", "voltage", "current", "energy", "last_seen", "linkquality")
ALLOWED = {"state", "brightness", "color_temp", "color", "transition"}

# Static scenes: white light only, built around the user's «Уютно» (43 %, 2300 K). Philips Hue's recipes gave the
# temperatures and the proportions, but Hue's brightness numbers do not transfer: the Tuya TS0505B dims more linearly,
# so Hue Dimmed (30 %, 2730 K) looked like a reading light here (2026-09-14). Hue bri is scaled by 0.76 = cozy/Hue Relax,
# and «Приглушённо» is simply cozy at a lower level. Coloured WiZ-style presets were dropped as ugly the same day;
# the 22:00 amber schedule on the mini is untouched.
SCENES = {
    "cozy":   {"title": "Уютно",       "payload": {"state": "ON", "brightness": 110, "color_temp": 435, "transition": 3}},
    "dimmed": {"title": "Приглушённо", "payload": {"state": "ON", "brightness": 46, "color_temp": 435, "transition": 3}},
    "rest":   {"title": "Отдых",       "payload": {"state": "ON", "brightness": 68, "color_temp": 500, "transition": 3}},
    "read":   {"title": "Чтение",      "payload": {"state": "ON", "brightness": 193, "color_temp": 343, "transition": 3}},
    "bright": {"title": "Ярко",        "payload": {"state": "ON", "brightness": 254, "color_temp": 367, "transition": 3}},
    "focus":  {"title": "Собраться",   "payload": {"state": "ON", "brightness": 254, "color_temp": 230, "transition": 3}},
    "night":  {"title": "Ночник",      "payload": {"state": "ON", "brightness": 3, "color_temp": 447, "transition": 2}},
    "sleepy": {"title": "Ко сну",      "payload": {"state": "ON", "brightness": 25, "color_temp": 500, "transition": 60}},
}
SCHEDULE = "19:00 уютно · 22:00 янтарь · 23:00 выкл"   # launchd on the mini (local.lamp.*, local.plug.*)

_host = {"name": None, "at": 0}
_seq = {}          # target -> number of the latest command; a verify for an older one is dropped
VERIFY_GRACE = 1.5


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


def set_device(name, payload, verify=True):
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
        _stop_cli_effect()  # a static setting ends a flicker started by the `lamp` CLI over ssh
    publish(name, body)
    if verify:
        _seq[name] = seq = _seq.get(name, 0) + 1
        threading.Thread(target=_verify, args=(name, body, seq), daemon=True).start()
    return body


def publish(name, body):
    c = connect()
    try:
        c.publish(f"{PREFIX}/{name}/set", body)
    finally:
        c.close()


def _matches(rep, body):
    if not rep:
        return False
    want = body.get("state")
    if want == "TOGGLE":
        return True
    if want and rep.get("state") != want:
        return False
    if rep.get("state") == "OFF":
        return True          # nothing else is visible while off
    if "brightness" in body and abs((rep.get("brightness") or 0) - body["brightness"]) > 2:
        return False
    if "color_temp" in body and (rep.get("color_mode") != "color_temp" or abs((rep.get("color_temp") or 0) - body["color_temp"]) > 3):
        return False
    if "color" in body and rep.get("color_mode") != "xy":
        return False
    return True


def _verify(name, body, seq):
    """After the transition, ask the bulbs and resend, one by one, to those that missed the command."""
    time.sleep(float(body.get("transition", 0)) + VERIFY_GRACE)
    if _seq.get(name) != seq:
        return            # a newer command took over; verifying this one would fight it
    members = LAMPS if name == "lamps" else (name,)
    try:
        rep = state()
    except Exception as e:
        print(f"home verify {name}: no state ({e})", flush=True)
        return
    again = {**body, "transition": 1}
    again.pop("state", None) if body.get("state") == "TOGGLE" else None
    for m in members:
        if not _matches(rep.get(m), body):
            print(f"home verify {m}: shows {rep.get(m)} — resending {again}", flush=True)
            try:
                publish(m, again)
            except Exception as e:
                print(f"home verify {m}: resend failed ({e})", flush=True)


def scene(name, target="lamps"):
    if name not in SCENES:
        raise ValueError(f"unknown scene {name}")
    return set_device(target, SCENES[name]["payload"])


def _stop_cli_effect():
    subprocess.run(["pkill", "-f", "mqtt/effect.js"], capture_output=True, timeout=5)


def summary():
    """What the page shows: devices and the scene catalogue."""
    try:
        devs = state()
        err = None
    except Exception as e:
        devs, err = {d: None for d in DEVICES}, f"{type(e).__name__}: {e}"
    return {
        "devices": devs,
        "scenes": [{"id": k, "title": v["title"]} for k, v in SCENES.items()],
        "schedule": SCHEDULE,
        "error": err,
    }


# ---------------------------------------------------------------- cli

def main(argv):
    cmd = argv[1] if len(argv) > 1 else "state"
    if cmd == "state":
        print(json.dumps(summary(), ensure_ascii=False, indent=2))
    elif cmd == "set":
        body = set_device(argv[2], json.loads(argv[3])); print(body); time.sleep(float(body.get("transition", 0)) + VERIFY_GRACE + 3)
    elif cmd == "scene":
        print(scene(argv[2])); time.sleep(float(SCENES[argv[2]]["payload"].get("transition", 0)) + VERIFY_GRACE + 3)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
