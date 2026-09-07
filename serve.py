#!/usr/bin/env python3
"""Local server for the catalog: static files + a tiny JSON API used by index.html.

  GET    /api/ping                → {"ok": true}
  PATCH  /api/movie/<imdbId>      body: {rating?, status?, note?, watchedAt?}
  DELETE /api/movie/<imdbId>
  POST   /api/mix                 body: {url}   → resolves metadata and adds the mix
  DELETE /api/mix/<id>
  PATCH  /api/lecture/<id>        body: {status?, position?, note?}
  PATCH  /api/book/<id>           body: {status?, rating?, comment?}
  DELETE /api/book/<id>
  GET    /api/weather             today's weather (Open-Meteo, or Yandex when YANDEX_WEATHER_KEY is set), cached 20 min
  POST   /api/lecture/<id>/audio  start audio download in the background; GET the same URL for status
  GET    /audio/<file>            audio files with HTTP Range support (needed by iOS)
  GET    /healthz                 200 "ok" (no auth; the Health Bridge iOS app pings it)
  POST   /v1/ingest/health/<workouts|sleep|metrics>   Apple Health batches from the iOS app, Authorization: Bearer <HEALTH_TOKENS>
  GET    /api/health              latest Claude note + daily table (scripts/health.py summary)
  POST   /api/health/review       run the review now (scripts/health_review.sh --force) in the background

Run: python3 serve.py [port]   (default 8787, binds to 127.0.0.1 only)

Auth: if MOVIES_PASSWORD is set (env or .env file next to this script) every page and API call
requires a login; the session is a signed cookie valid for 90 days. Without a password the server
is open, which is fine for localhost.
"""
import hashlib, hmac, json, os, secrets, sys, threading, time, urllib.parse
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import movies as M  # noqa: E402
import mixes as X  # noqa: E402
import lectures as L  # noqa: E402
import books as B  # noqa: E402
import weather as W  # noqa: E402
import health as H  # noqa: E402

AUDIO_JOBS = {}  # video id -> "running" | "done" | "error: ..."
REVIEW_JOB = {"status": "idle", "started": 0}  # manual health review run

STATUSES = set(M.STATUSES)
LOCK = threading.Lock()  # load-modify-save must not interleave


# ---------------------------------------------------------------- auth
def load_dotenv():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()
PASSWORD = os.environ.get("MOVIES_PASSWORD") or ""
# Bearer tokens for the Health Bridge iOS app: HEALTH_TOKENS="iphone=abc,ipad=def" or a single HEALTH_TOKEN.
HEALTH_TOKENS = {}
for _pair in (os.environ.get("HEALTH_TOKENS") or "").split(","):
    if "=" in _pair:
        _n, _, _t = _pair.partition("=")
        HEALTH_TOKENS[_t.strip()] = _n.strip()
if os.environ.get("HEALTH_TOKEN"):
    HEALTH_TOKENS[os.environ["HEALTH_TOKEN"].strip()] = "default"
SESSION_DAYS = 90
COOKIE = "movies_session"
FAILS = {}  # ip -> [count, first_ts]


def session_secret():
    p = os.path.join(ROOT, ".session_secret")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip()


SECRET = session_secret() if PASSWORD else ""


def make_token():
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(SECRET.encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(tok):
    try:
        exp, sig = tok.split(".", 1)
        good = hmac.new(SECRET.encode(), exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


LOGIN_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход</title><style>
:root{color-scheme:light dark;--bg:#fafafa;--surface:#fff;--text:#1d1d1f;--text-2:#6e6e73;--line:rgba(0,0,0,.08);--accent:#0071e3}
@media(prefers-color-scheme:dark){:root{--bg:#000;--surface:#1c1c1e;--text:#f5f5f7;--text-2:#a1a1a6;--line:rgba(255,255,255,.1);--accent:#2997ff}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Inter,system-ui,sans-serif;-webkit-font-smoothing:antialiased;letter-spacing:-.01em}
form{width:min(360px,90vw);background:var(--surface);border-radius:22px;padding:32px;box-shadow:0 2px 8px rgba(0,0,0,.08),0 24px 64px rgba(0,0,0,.18)}
h1{margin:0 0 4px;font-size:24px;letter-spacing:-.03em}p{margin:0 0 20px;color:var(--text-2);font-size:14px}
input{width:100%;font:inherit;color:inherit;border:1px solid var(--line);background:transparent;border-radius:12px;padding:11px 14px;font-size:16px;outline:0}
input:focus{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent);border-color:transparent}
button{width:100%;margin-top:14px;font:inherit;font-weight:600;border:0;border-radius:12px;padding:12px;background:var(--text);color:var(--bg);cursor:pointer}
.err{color:#ff3b30;font-size:13px;margin:10px 0 0;min-height:1em}
</style></head><body><form method="post" action="/login"><h1>Фильмы и миксы</h1><p>Введи пароль, чтобы войти</p>
<input type="password" name="password" placeholder="Пароль" autofocus autocomplete="current-password"><input type="hidden" name="next" value="{next}">
<div class="err">{error}</div><button>Войти</button></form></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        # HTML/JS/CSS must not be cached: after a deploy the browser has to pick up the new page immediately.
        if self.command in ("GET", "HEAD") and self.path.split("?")[0].rsplit(".", 1)[-1] in ("html", "js", "css", "") and "/api/" not in self.path:
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else "") or "/v1/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # ---- auth helpers ----
    def client_ip(self):
        return self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto") == "https" or '"scheme":"https"' in (self.headers.get("CF-Visitor") or "")

    def logged_in(self):
        if not PASSWORD:
            return True
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return COOKIE in c and token_ok(c[COOKIE].value)

    def send_html(self, code, html, extra=None):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def login_page(self, error="", nxt="/"):
        self.send_html(200 if not error else 401, LOGIN_HTML.replace("{error}", error).replace("{next}", nxt.replace('"', "")))

    def require_login(self):
        """Returns True if the request may proceed; otherwise a login redirect / 401 has been sent."""
        if self.logged_in():
            return True
        if self.path.startswith("/api/"):
            self.send_json(401, {"error": "login required"})
        else:
            self.send_response(302)
            self.send_header("Location", "/login?next=" + urllib.parse.quote(self.path))
            self.end_headers()
        return False

    def health_device(self):
        """Name of the device whose bearer token matches, or None. Open when no password and no tokens (localhost dev)."""
        auth = self.headers.get("Authorization") or ""
        tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if HEALTH_TOKENS:
            for t, name in HEALTH_TOKENS.items():
                if hmac.compare_digest(tok.encode(), t.encode()):
                    return name
            return None
        return "open" if not PASSWORD else None

    def handle_login_post(self):
        ip = self.client_ip()
        cnt, first = FAILS.get(ip, [0, 0])
        if cnt >= 10 and time.time() - first < 900:
            return self.login_page("Слишком много попыток, подожди 15 минут")
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        pw = (form.get("password") or [""])[0]
        nxt = (form.get("next") or ["/"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
            FAILS.pop(ip, None)
            cookie = f"{COOKIE}={make_token()}; Path=/; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax" + ("; Secure" if self.is_https() else "")
            self.send_response(302)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Location", nxt)
            self.end_headers()
            return
        time.sleep(1)
        FAILS[ip] = [cnt + 1, first or time.time()] if time.time() - first < 900 else [1, time.time()]
        self.login_page("Неверный пароль", nxt)

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def path_id(self, prefix):
        if not self.path.startswith(prefix):
            return None
        return urllib.parse.unquote(self.path[len(prefix):].split("?")[0])

    def movie_id(self):
        return self.path_id("/api/movie/")

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/login":
            if self.logged_in():
                self.send_response(302); self.send_header("Location", "/"); self.end_headers(); return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self.login_page("", (q.get("next") or ["/"])[0])
        if route == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if route == "/api/ping":
            return self.send_json(200, {"ok": True, "auth": bool(PASSWORD)})
        if route == "/healthz":
            return self.send_json(200, {"ok": True})
        if not self.require_login():
            return
        if route == "/api/health":
            try:
                out = H.summary()
            except Exception as e:
                return self.send_json(500, {"error": f"{type(e).__name__}: {e}"})
            out["reviewJob"] = REVIEW_JOB["status"]
            return self.send_json(200, out)
        if self.path.startswith("/movies.json"):
            return self.send_json(200, M.load())
        if self.path.startswith("/mixes.json"):
            return self.send_json(200, X.load())
        if self.path.startswith("/books.json"):
            return self.send_json(200, B.load())
        if route == "/api/weather":
            try:
                return self.send_json(200, W.today())
            except Exception as e:
                return self.send_json(502, {"error": f"{type(e).__name__}: {e}"})
        if self.path.startswith("/lectures.json"):
            db = L.load()
            for l in db["lectures"]:
                l["audio"] = L.has_audio(l["id"])
            return self.send_json(200, db)
        if route.startswith("/api/lecture/") and route.endswith("/audio"):
            vid = urllib.parse.unquote(route.split("/")[3])
            st = "done" if L.has_audio(vid) else AUDIO_JOBS.get(vid, "none")
            return self.send_json(200, {"id": vid, "status": st})
        if route.startswith("/audio/"):
            return self.send_range_file(L.audio_path(urllib.parse.unquote(os.path.basename(route)).rsplit(".", 1)[0]) or "")
        if self.path.startswith("/api/backgrounds"):
            d = os.path.join(ROOT, "backgrounds")
            files = sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) if os.path.isdir(d) else []
            return self.send_json(200, ["backgrounds/" + f for f in files])
        return super().do_GET()

    def do_HEAD(self):
        if self.require_login():
            super().do_HEAD()

    def send_range_file(self, path):
        if not os.path.isfile(path):
            return self.send_json(404, {"error": "no such audio"})
        size = os.path.getsize(path)
        ctype = {"m4a": "audio/mp4", "mp3": "audio/mpeg", "webm": "audio/webm", "opus": "audio/ogg"}.get(path.rsplit(".", 1)[-1], "application/octet-stream")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            try:
                start = int(a) if a else max(0, size - int(b))
                end = int(b) if (b and a) else size - 1
            except ValueError:
                return self.send_json(416, {"error": "bad range"})
            if start > end or start >= size:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, max-age=86400")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def start_audio_job(self, vid):
        if L.has_audio(vid):
            return "done"
        if AUDIO_JOBS.get(vid) == "running":
            return "running"
        AUDIO_JOBS[vid] = "running"

        def run():
            try:
                L.download_audio(vid)
                AUDIO_JOBS[vid] = "done"
            except Exception as e:
                AUDIO_JOBS[vid] = f"error: {e}"
        threading.Thread(target=run, daemon=True).start()
        return "running"

    def start_review_job(self):
        if REVIEW_JOB["status"] == "running" and time.time() - REVIEW_JOB["started"] < 900:
            return "running"
        REVIEW_JOB.update(status="running", started=time.time())

        def run():
            import subprocess
            try:
                r = subprocess.run(["/bin/sh", os.path.join(ROOT, "scripts", "health_review.sh"), "--force"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=900)
                REVIEW_JOB["status"] = "done" if r.returncode == 0 else f"error: {(r.stderr or r.stdout).strip()[-300:]}"
            except Exception as e:
                REVIEW_JOB["status"] = f"error: {type(e).__name__}: {e}"

        threading.Thread(target=run, daemon=True).start()
        return "running"

    def do_POST(self):
        route = self.path.split("?")[0]
        if route == "/login":
            return self.handle_login_post() if PASSWORD else self.send_json(404, {"error": "auth disabled"})
        if route.startswith("/v1/ingest/health/"):
            kind = route.rsplit("/", 1)[1]
            dev = self.health_device()
            if not dev:
                return self.send_json(401, {"error": "bad token"})
            if kind not in ("workouts", "sleep", "metrics"):
                return self.send_json(404, {"error": "unknown sample type"})
            try:
                batch = self.read_json()
                stats = H.ingest(kind, batch)
            except (ValueError, TypeError) as e:
                return self.send_json(400, {"error": f"bad payload: {e}"})
            except Exception as e:
                return self.send_json(500, {"error": f"{type(e).__name__}: {e}"})
            print(f"health ingest {kind} from {dev}: {stats}", flush=True)
            return self.send_json(200, {"ok": True, **stats})
        if not self.require_login():
            return
        if route == "/api/health/review":
            return self.send_json(200, {"status": self.start_review_job()})
        if route.startswith("/api/lecture/") and route.endswith("/audio"):
            vid = urllib.parse.unquote(route.split("/")[3])
            if not any(l["id"] == vid for l in L.load()["lectures"]):
                return self.send_json(404, {"error": "no such lecture"})
            return self.send_json(200, {"id": vid, "status": self.start_audio_job(vid)})
        if route != "/api/mix":
            return self.send_json(404, {"error": "not found"})
        try:
            url = (self.read_json().get("url") or "").strip()
        except Exception:
            return self.send_json(400, {"error": "bad json"})
        if not url.startswith("http"):
            return self.send_json(400, {"error": "url required"})
        with LOCK:
            try:
                mix = X.add_mix(url)
            except SystemExit as e:
                return self.send_json(400, {"error": str(e)})
            except Exception as e:
                return self.send_json(500, {"error": f"{type(e).__name__}: {e}"})
        return self.send_json(200, mix)

    def do_PATCH(self):
        if not self.require_login():
            return
        with LOCK:
            return self._patch()

    def _patch(self):
        bid = self.path_id("/api/book/")
        if bid:
            try:
                body = self.read_json()
            except Exception:
                return self.send_json(400, {"error": "bad json"})
            books = B.load()
            b = next((x for x in books if x["id"] == bid), None)
            if not b:
                return self.send_json(404, {"error": "no such book"})
            if "status" in body:
                if body["status"] not in B.STATUSES:
                    return self.send_json(400, {"error": "bad status"})
                b["status"] = body["status"]
                if body["status"] == "read":
                    b["finishedAt"] = b.get("finishedAt") or __import__("datetime").date.today().isoformat()
            if "rating" in body:
                r = body["rating"]
                if r is not None:
                    try:
                        r = float(r)
                    except (TypeError, ValueError):
                        return self.send_json(400, {"error": "bad rating"})
                    if not 0.5 <= r <= 10:
                        return self.send_json(400, {"error": "rating must be 0.5–10"})
                    r = round(r * 2) / 2
                    r = int(r) if r == int(r) else r
                b["rating"] = r
            if "comment" in body:
                b["comment"] = (body["comment"] or "").strip() or None
            B.save(books)
            return self.send_json(200, b)
        lid = self.path_id("/api/lecture/")
        if lid:
            try:
                body = self.read_json()
            except Exception:
                return self.send_json(400, {"error": "bad json"})
            db = L.load()
            l = next((x for x in db["lectures"] if x["id"] == lid), None)
            if not l:
                return self.send_json(404, {"error": "no such lecture"})
            if "status" in body:
                if body["status"] not in L.STATUSES:
                    return self.send_json(400, {"error": "bad status"})
                l["status"] = body["status"]
                if body["status"] == "listened":
                    l["listenedAt"] = l.get("listenedAt") or __import__("datetime").date.today().isoformat()
                if body["status"] == "queued":
                    l["queuedAt"] = l.get("queuedAt") or int(time.time())
                if body["status"] == "new":
                    l["position"] = 0
            if "position" in body:
                try:
                    l["position"] = max(0, int(float(body["position"])))
                except (TypeError, ValueError):
                    return self.send_json(400, {"error": "bad position"})
                if l["status"] == "new" and l["position"] > 60:
                    l["status"] = "listening"
            if "note" in body:
                l["note"] = (body["note"] or "").strip() or None
            L.save(db)
            l["audio"] = L.has_audio(lid)
            return self.send_json(200, l)
        mid = self.movie_id()
        if not mid:
            return self.send_json(404, {"error": "not found"})
        try:
            body = self.read_json()
        except Exception:
            return self.send_json(400, {"error": "bad json"})
        movies = M.load()
        m = next((x for x in movies if x["id"] == mid), None)
        if not m:
            return self.send_json(404, {"error": "no such movie"})

        if "rating" in body:
            r = body["rating"]
            if r is not None:
                try:
                    r = float(r)
                except (TypeError, ValueError):
                    return self.send_json(400, {"error": "bad rating"})
                if not 0.5 <= r <= 10:
                    return self.send_json(400, {"error": "rating must be 0.5–10"})
                r = round(r * 2) / 2
                r = int(r) if r == int(r) else r
            m["rating"] = r
        if "status" in body:
            if body["status"] not in STATUSES:
                return self.send_json(400, {"error": "bad status"})
            m["status"] = body["status"]
        if "note" in body:
            note = (body["note"] or "").strip()
            m["note"] = note or None
        if "watchedAt" in body:
            m["watchedAt"] = body["watchedAt"] or None
        if m["status"] == "to-watch":
            m["rating"] = None
        M.save(movies)
        return self.send_json(200, m)

    def do_DELETE(self):
        if not self.require_login():
            return
        with LOCK:
            return self._delete()

    def _delete(self):
        bid = self.path_id("/api/book/")
        if bid:
            books = B.load()
            b = next((x for x in books if x["id"] == bid), None)
            if not b:
                return self.send_json(404, {"error": "no such book"})
            books.remove(b)
            p = os.path.join(ROOT, b.get("cover") or "")
            if b.get("cover") and os.path.isfile(p):
                os.remove(p)
            B.save(books)
            return self.send_json(200, {"ok": True})
        xid = self.path_id("/api/mix/")
        if xid:
            mixes = X.load()
            m = next((x for x in mixes if x["id"] == xid), None)
            if not m:
                return self.send_json(404, {"error": "no such mix"})
            mixes.remove(m)
            X.save(mixes)
            return self.send_json(200, {"ok": True})
        mid = self.movie_id()
        if not mid:
            return self.send_json(404, {"error": "not found"})
        movies = M.load()
        m = next((x for x in movies if x["id"] == mid), None)
        if not m:
            return self.send_json(404, {"error": "no such movie"})
        movies.remove(m)
        p = os.path.join(ROOT, m.get("poster") or "")
        if m.get("poster") and os.path.isfile(p):
            os.remove(p)
        M.save(movies)
        return self.send_json(200, {"ok": True})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    host = os.environ.get("MOVIES_BIND", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Movies → http://localhost:{port}" + ("  (password protected)" if PASSWORD else "  (no password: open)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
