#!/usr/bin/env python3
"""Local server for the catalog: static files + a tiny JSON API used by index.html.

  GET    /api/ping                → {"ok": true}
  PATCH  /api/movie/<imdbId>      body: {rating?, status?, note?, watchedAt?}
  DELETE /api/movie/<imdbId>
  POST   /api/mix                 body: {url}   → resolves metadata and adds the mix
  DELETE /api/mix/<id>

Run: python3 serve.py [port]   (default 8787, binds to 127.0.0.1 only)
"""
import json, os, sys, threading, urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import movies as M  # noqa: E402
import mixes as X  # noqa: E402

STATUSES = set(M.STATUSES)
LOCK = threading.Lock()  # load-modify-save must not interleave


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

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
        if self.path.startswith("/api/ping"):
            return self.send_json(200, {"ok": True})
        if self.path.startswith("/movies.json"):
            return self.send_json(200, M.load())
        if self.path.startswith("/mixes.json"):
            return self.send_json(200, X.load())
        if self.path.startswith("/api/backgrounds"):
            d = os.path.join(ROOT, "backgrounds")
            files = sorted(f for f in os.listdir(d) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) if os.path.isdir(d) else []
            return self.send_json(200, ["backgrounds/" + f for f in files])
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/mix":
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
        with LOCK:
            return self._patch()

    def _patch(self):
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
        with LOCK:
            return self._delete()

    def _delete(self):
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
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Movies → http://localhost:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
