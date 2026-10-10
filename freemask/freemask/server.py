"""Local web server behind the FreeMask interface (http://127.0.0.1 only)."""

from __future__ import annotations

import json
import mimetypes
import os
import platform
import shutil
import subprocess
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .session import Busy, Session

WEB = Path(__file__).parent / "web"


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind does a reverse-DNS lookup (socket.getfqdn) that can stall
        # for seconds on macOS. We only ever serve 127.0.0.1, so skip it.
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


class App:
    """Owns the session and the server's lifecycle (heartbeat, quit, done-file)."""

    def __init__(self, session: Session, done_file: str | None = None, idle_timeout: float = 900.0,
                 bye_grace: float = 4.0):
        self.session = session
        self.done_file = done_file
        self.idle_timeout = idle_timeout
        self.bye_grace = bye_grace
        self.last_ping = None
        self.bye_at = None
        self.httpd: ThreadingHTTPServer | None = None
        self._stopping = False

    # lifecycle ------------------------------------------------------------------

    def ping(self):
        self.last_ping = time.time()
        self.bye_at = None

    def bye(self):
        self.bye_at = time.time()

    def quit(self):
        if self._stopping:
            return
        self._stopping = True
        threading.Thread(target=self._shutdown, daemon=True).start()

    def _shutdown(self):
        time.sleep(0.2)  # let the HTTP response go out
        if self.httpd:
            self.httpd.shutdown()

    def _watchdog(self):
        while not self._stopping:
            time.sleep(0.5)
            now = time.time()
            job = self.session.job
            busy = job is not None and not job.done and job.kind == "export"
            if busy:
                continue
            if self.bye_at and now - self.bye_at > self.bye_grace:
                print("Window closed — shutting down.")
                self.quit()
            elif self.last_ping and now - self.last_ping > self.idle_timeout:
                print("Lost contact with the window — shutting down.")
                self.quit()

    def write_done_file(self):
        if not self.done_file:
            return
        exp = self.session.last_export or {}
        info = self.session.info
        lines = [f"{k}={v}" for k, v in sorted(exp.items())]
        if info and exp:
            lines += [f"first_frame={info.first_frame}", f"frame_count={info.frame_count}",
                      f"fps={info.fps}", f"name={self.session.name}"]
        Path(self.done_file).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(self.done_file).with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.done_file)

    def serve(self, host: str = "127.0.0.1", port: int = 0, open_window: bool = True):
        handler = type("Handler", (Handler,), {"app": self})
        self.httpd = _Server((host, port), handler)
        url = f"http://{host}:{self.httpd.server_address[1]}/"
        print(f"FreeMask is running at {url}")
        threading.Thread(target=self._watchdog, daemon=True).start()
        if open_window:
            open_app_window(url)
        try:
            self.httpd.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.httpd.server_close()
            try:
                self.write_done_file()
            finally:
                self.session.close()
        return url


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "FreeMask"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        if os.environ.get("FREEMASK_DEBUG"):
            super().log_message(fmt, *args)

    # helpers ---------------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Bad JSON")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _guard(self, fn):
        try:
            fn()
        except Busy as exc:
            self._json({"error": str(exc)}, 409)
        except (ValueError, FileNotFoundError, KeyError, TypeError) as exc:
            self._json({"error": str(exc) or exc.__class__.__name__}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # pragma: no cover - last resort
            traceback.print_exc()
            self._json({"error": f"Internal error: {exc}"}, 500)

    # GET -------------------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._guard(self._get)

    def _get(self):
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        s = self.app.session
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/assets/"):
            return self._static(path[len("/assets/"):])
        if path == "/api/state":
            return self._json(s.snapshot())
        if path.startswith("/api/frame/"):
            i = int(path.rsplit("/", 1)[1])
            s._require_image(i)
            return self._send(200, s.frame_path(i).read_bytes(), "image/jpeg", "private, max-age=86400")
        if path.startswith("/api/overlay/"):
            i = int(path.rsplit("/", 1)[1])
            sel = int(q.get("obj", ["0"])[0])
            return self._send(200, s.overlay_png(i, sel), "image/png", "private, max-age=86400")
        if path.startswith("/api/matte/"):
            i = int(path.rsplit("/", 1)[1])
            return self._send(200, s.matte_png(i), "image/png", "private, max-age=86400")
        self._json({"error": "Not found"}, 404)

    def _static(self, rel: str):
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self._json({"error": "Not found"}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, "no-cache")

    # POST ------------------------------------------------------------------------

    def do_POST(self):
        self._guard(self._post)

    def _post(self):
        path = urlparse(self.path).path
        b = self._body()
        s = self.app.session
        a = self.app
        if path == "/api/ping":
            a.ping()
            return self._json({"ok": True})
        if path == "/api/bye":
            a.bye()
            return self._json({"ok": True})
        if path == "/api/quit":
            a.quit()
            return self._json({"ok": True})
        if path == "/api/open":
            end = b.get("end")
            s.open_clip(str(b["path"]).strip().strip('"').strip("'"), int(b.get("start") or 0),
                        None if end in (None, "") else int(end))
            return self._json(s.snapshot())
        if path == "/api/point":
            s.add_point(int(b["frame"]), int(b["obj"]), float(b["x"]), float(b["y"]), int(b.get("label", 1)))
        elif path == "/api/box":
            s.set_box(int(b["frame"]), int(b["obj"]), b["x1"], b["y1"], b["x2"], b["y2"])
        elif path == "/api/undo":
            s.undo(int(b["frame"]), int(b["obj"]))
        elif path == "/api/clear":
            s.clear(int(b["frame"]), int(b["obj"]))
        elif path == "/api/hidden":
            s.set_hidden(int(b["obj"]), bool(b["hidden"]))
        elif path == "/api/settings":
            s.update_settings(b)
        elif path == "/api/track":
            s.track(b.get("direction", "both"))
        elif path == "/api/cancel":
            s.cancel()
        elif path == "/api/export":
            s.export(b.get("formats"))
        elif path == "/api/save":
            return self._json({"path": s.save_session()})
        else:
            return self._json({"error": "Not found"}, 404)
        self._json(s.snapshot())


# ----------------------------------------------------------------------------- window


def _browser_candidates() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        apps = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc", "Vivaldi"]
        return [f"/Applications/{a}.app/Contents/MacOS/{a}" for a in apps] + [
            str(Path.home() / f"Applications/{a}.app/Contents/MacOS/{a}") for a in apps]
    if system == "Windows":
        pf = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        return [os.path.join(p, r) for r in rels for p in pf if p]
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]
    return [p for p in (shutil.which(n) for n in names) if p]


def open_app_window(url: str):
    """Open the UI in a chromeless app window when a Chromium browser exists, else a normal tab."""
    if os.environ.get("FREEMASK_NO_BROWSER"):
        return
    for exe in _browser_candidates():
        if os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1500,940", "--new-window"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    webbrowser.open(url)
