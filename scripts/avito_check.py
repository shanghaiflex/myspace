#!/usr/bin/env python3
"""«Пройти проверку Авито» с телефона: окно Chrome на mini — картинкой, касания — его мышью.

Капчу на Авито (кнопка «Продолжить» → hCaptcha с картинками) решает человек, а не прогон слежки
(scripts/avito_watch.py). Но окно живёт на mini без монитора, а VNC есть только из дома. Поэтому serve.py
показывает эту вкладку на странице avito-check.html: снимок экрана раз в секунду и обратно — касания и
перетаскивания, которые человек сделал пальцем, теми же точками и с теми же паузами. Решает по-прежнему
человек; mini только передаёт картинку и его движения, как удалённый рабочий стол.

Доступ — только к вкладке avito.ru браузера слежки (CDP 127.0.0.1:9222) и только мышью внутри её окна, за
логином сайта. Клиент протокола — свой, на голом сокете: serve.py живёт на stdlib, playwright есть только в
venv прогона.

Снимок обрезается до самой проверки: окно 1440 px, на телефоне картинки hCaptcha иначе вышли бы по 30 px.
Если на странице видна рамка hCaptcha — по ней, иначе — по видимому тексту страницы.

  avito_check.py state      что сейчас во вкладке
  avito_check.py shot F     снимок в файл F (для отладки)
"""
import base64
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.request

CDP = os.environ.get("AVITO_CDP", "http://127.0.0.1:9222")
BLOCK = ("Доступ ограничен", "Ошибка 4", "Captcha", "Проблема с IP")
_lock = threading.Lock()      # один разговор со вкладкой за раз: касания не должны перемешиваться


class NoTab(Exception):
    pass


class _WS:
    """Минимальный клиент WebSocket (RFC 6455) — ровно на CDP: текстовые кадры, без расширений.
    Origin не шлём: Chrome с 111 отвергает чужой Origin без --remote-allow-origins, а без заголовка пускает."""

    def __init__(self, url, timeout=15):
        assert url.startswith("ws://")
        host, _, path = url[5:].partition("/")
        h, _, p = host.partition(":")
        self.s = socket.create_connection((h, int(p or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise NoTab("CDP закрыл соединение при рукопожатии")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise NoTab("CDP не принял WebSocket: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def _read(self, k):
        while len(self.buf) < k:
            chunk = self.s.recv(max(65536, k - len(self.buf)))
            if not chunk:
                raise NoTab("CDP оборвал соединение")
            self.buf += chunk
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.s.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:
                raise NoTab("CDP закрыл соединение")
            if op in (0x9, 0xA):          # ping/pong — CDP их не шлёт, но пропустить не страшно
                continue
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def call(self, method, params=None):
        self.n += 1
        self.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def _tab():
    try:
        with urllib.request.urlopen(f"{CDP}/json/list", timeout=3) as r:
            tabs = json.load(r)
    except Exception:
        raise NoTab("браузер слежки не запущен")
    for t in tabs:
        if t.get("type") == "page" and "avito.ru" in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
            return t
    raise NoTab("в браузере слежки нет вкладки avito.ru")


def _session():
    # Chrome пишет в адресе свой порт; через ssh-туннель (AVITO_CDP=…:9333) ходить надо на наш.
    ws = _tab()["webSocketDebuggerUrl"]
    host = CDP.split("://", 1)[1].rstrip("/")
    return _WS("ws://" + host + "/" + ws.split("://", 1)[1].split("/", 1)[1])


def state():
    """{state: captcha|ok|none, title, url} — без похода на Авито: только что показывает вкладка."""
    try:
        t = _tab()
    except NoTab as e:
        return {"state": "none", "title": str(e), "url": None}
    title = t.get("title") or ""
    return {"state": "captcha" if any(b.lower() in title.lower() for b in BLOCK) else "ok",
            "title": title, "url": t.get("url")}


_CLIP_JS = r"""(() => {
  const W = innerWidth, H = innerHeight, pad = 16;
  const vis = r => r.width > 40 && r.height > 40 && r.bottom > 0 && r.right > 0 && r.top < H && r.left < W;
  // Рамка hCaptcha: виджет и окно с картинками — iframe с hcaptcha.com; невидимые стоят на -9999.
  let box = null;
  for (const f of document.querySelectorAll('iframe')) {
    if (!/hcaptcha|captcha/i.test(f.src || '')) continue;
    const r = f.getBoundingClientRect();
    if (!vis(r)) continue;
    box = box ? {l: Math.min(box.l, r.left), t: Math.min(box.t, r.top), r: Math.max(box.r, r.right), b: Math.max(box.b, r.bottom)}
              : {l: r.left, t: r.top, r: r.right, b: r.bottom};
  }
  if (!box) {   // проверки ещё нет на экране (кнопка «Продолжить») — по видимому тексту и кнопкам
    for (const el of document.querySelectorAll('h1,h2,h3,p,button,a,input,img,svg,li')) {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > H) continue;
      box = box ? {l: Math.min(box.l, r.left), t: Math.min(box.t, r.top), r: Math.max(box.r, r.right), b: Math.max(box.b, r.bottom)}
                : {l: r.left, t: r.top, r: r.right, b: r.bottom};
    }
  }
  if (!box) box = {l: 0, t: 0, r: W, b: H};
  const x = Math.max(0, box.l - pad), y = Math.max(0, box.t - pad);
  return {x, y, w: Math.min(W, box.r + pad) - x, h: Math.min(H, box.b + pad) - y, vw: W, vh: H};
})()"""


def shot(full=False):
    """(jpeg, clip): снимок вкладки, обрезанный до проверки; clip — какой кусок окна (CSS px) на картинке."""
    with _lock:
        ws = _session()
        try:
            r = ws.call("Runtime.evaluate", {"expression": _CLIP_JS, "returnByValue": True})
            clip = (r.get("result") or {}).get("value") or {}
            if full or not clip.get("w"):
                clip = {"x": 0, "y": 0, "w": clip.get("vw") or 1440, "h": clip.get("vh") or 900,
                        "vw": clip.get("vw"), "vh": clip.get("vh")}
            # Две точки на пиксель: окно на mini без ретины, а картинки hCaptcha на телефоне смотрят вблизи.
            scale = 2 if clip["w"] <= 800 else 1
            img = ws.call("Page.captureScreenshot", {
                "format": "jpeg", "quality": 80, "captureBeyondViewport": False,
                "clip": {"x": clip["x"], "y": clip["y"], "width": clip["w"], "height": clip["h"], "scale": scale}})
            return base64.b64decode(img["data"]), clip
        finally:
            ws.close()


MAX_EVENTS = 400
MAX_SPAN_S = 15.0


def pointer(events):
    """Повторить жест человека во вкладке: [{t: down|move|up, x, y, dt}], x/y — CSS px окна, dt — мс с
    прошлого события. Паузы те же, что были под пальцем (не больше секунды каждая, жест не дольше 15 с)."""
    if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
        raise ValueError("нужен список событий (1–400)")
    kinds = {"down": "mousePressed", "move": "mouseMoved", "up": "mouseReleased"}
    clean, span = [], 0.0
    for e in events:
        t = kinds.get(e.get("t"))
        if not t:
            raise ValueError(f"неизвестное событие {e.get('t')!r}")
        dt = min(max(float(e.get("dt") or 0), 0.0), 1000.0) / 1000.0
        span += dt
        clean.append((t, float(e["x"]), float(e["y"]), dt))
    if span > MAX_SPAN_S:
        raise ValueError("жест слишком длинный")
    with _lock:
        ws = _session()
        try:
            m = ws.call("Runtime.evaluate", {"expression": "[innerWidth, innerHeight]", "returnByValue": True})
            vw, vh = (m.get("result") or {}).get("value") or [1440, 900]
            pressed = False
            for t, x, y, dt in clean:
                if dt:
                    time.sleep(dt)
                x, y = min(max(x, 0), vw - 1), min(max(y, 0), vh - 1)
                p = {"type": t, "x": x, "y": y, "button": "left" if t != "mouseMoved" or pressed else "none",
                     "buttons": 1 if (pressed or t == "mousePressed") and t != "mouseReleased" else 0}
                if t != "mouseMoved":
                    p["clickCount"] = 1
                ws.call("Input.dispatchMouseEvent", p)
                pressed = t == "mousePressed" or (pressed and t == "mouseMoved")
            if pressed:     # палец ушёл за край картинки без «up» — кнопку всё равно надо отпустить
                ws.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                                                     "clickCount": 1})
        finally:
            ws.close()
    return state()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "state":
        print(json.dumps(state(), ensure_ascii=False))
    elif len(sys.argv) > 2 and sys.argv[1] == "shot":
        data, clip = shot("--full" in sys.argv)
        open(sys.argv[2], "wb").write(data)
        print(json.dumps(clip))
    else:
        sys.exit(__doc__)
