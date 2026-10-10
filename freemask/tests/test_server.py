"""HTTP API tests: the server running for real, with the fake tracker."""

import json
import threading
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest
from fakes import FakeEngine, ball_center, make_clip

from freemask.server import App
from freemask.session import Session


class Server:
    def __init__(self, tmp_path, slow=0.0, **app_kw):
        class Slow(FakeEngine):
            def propagate(self, start, reverse):
                for item in super().propagate(start, reverse):
                    time.sleep(slow)
                    yield item

        self.tmp = tmp_path
        self.session = Session(engine_factory=lambda d, m, dev: Slow(d))
        self.app = App(self.session, **app_kw)
        self.thread = threading.Thread(target=self.app.serve, kwargs={"port": 0, "open_window": False}, daemon=True)
        self.thread.start()
        for _ in range(3000):  # up to 30 s on slow CI machines
            if self.app.httpd and self.app.httpd.server_address[1]:
                break
            time.sleep(0.01)
        self.url = f"http://127.0.0.1:{self.app.httpd.server_address[1]}"

    def req(self, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST",
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                payload = resp.read()
                return resp.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_job(self, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, s = self.req("/api/state")
            if s["job"] and s["job"]["done"]:
                return s
            time.sleep(0.05)
        raise TimeoutError

    def stop(self):
        self.app.quit()
        self.thread.join(5)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def clip(tmp_path):
    return make_clip(tmp_path / "shot.mp4", n=16)


def open_clip(srv, clip, **kw):
    code, s = srv.req("/api/open", {"path": str(clip), **kw})
    assert code == 200, s
    s = srv.wait_job()
    assert s["status"] == "ready", s
    return s


def test_static_files_and_404(srv):
    code, html = srv.req("/", raw=True)
    assert code == 200 and b"FreeMask" in html
    for asset in ("app.js", "style.css", "icon.svg"):
        assert srv.req("/assets/" + asset, raw=True)[0] == 200
    assert srv.req("/assets/../server.py", raw=True)[0] == 404
    assert srv.req("/nope")[0] == 404


def test_full_flow(srv, clip, tmp_path):
    code, s = srv.req("/api/state")
    assert s["status"] == "empty"
    s = open_clip(srv, clip, start=2, end=13)
    assert s["clip"]["frames"] == 12 and s["clip"]["first_frame"] == 2

    # Frame/overlay/matte images
    code, jpg = srv.req("/api/frame/0", raw=True)
    assert code == 200 and jpg[:2] == b"\xff\xd8"

    x, y = ball_center(5)  # source frame 5 == relative 3
    code, s = srv.req("/api/point", {"frame": 3, "obj": 1, "x": x, "y": y, "label": 1})
    assert code == 200 and s["stale"] and s["prompts"][0]["points"] == [[x, y]]
    code, png = srv.req("/api/overlay/3?obj=1", raw=True)
    ov = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert ov.shape[2] == 4 and ov[y, x, 3] > 0 and ov[5, 5, 3] == 0

    code, s = srv.req("/api/point", {"frame": 3, "obj": 1, "x": 5, "y": 5, "label": 0})
    code, s = srv.req("/api/undo", {"frame": 3, "obj": 1})
    assert s["prompts"][0]["labels"] == [1]

    code, s = srv.req("/api/track", {"direction": "both"})
    assert code == 200
    s = srv.wait_job()
    assert s["job"]["error"] is None and s["tracked_count"] == 12 and not s["stale"]
    assert s["tracked"] == [[0, 11]]

    code, s = srv.req("/api/settings", {"grow": 3, "feather": 1.5, "invert": False})
    assert s["settings"]["grow"] == 3 and s["settings"]["feather"] == 1.5
    code, png = srv.req("/api/matte/3", raw=True)
    m = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert m[y, x, 3] > 250 and m[5, 5, 3] < 5

    code, s = srv.req("/api/export", {"formats": ["png", "cutout", "video"]})
    assert code == 200
    s = srv.wait_job()
    assert s["job"]["error"] is None, s["job"]
    exp = s["last_export"]
    assert len(list((tmp_path / "shot_freemask" / "shot_matte").glob("*.png"))) == 12
    assert exp["video"].endswith("shot_matte.mov")
    assert (tmp_path / "shot_freemask" / "shot_session.json").exists()

    code, r = srv.req("/api/save", {})
    saved = json.loads(open(r["path"]).read())
    assert saved["prompts"][0]["frame"] == 5  # stored as a source frame number
    assert saved["refine"]["grow"] == 3


def test_errors_are_friendly(srv, clip, tmp_path):
    code, s = srv.req("/api/point", {"frame": 0, "obj": 1, "x": 1, "y": 1})
    assert code == 409 and "Open a clip" in s["error"]
    code, s = srv.req("/api/open", {"path": str(tmp_path / "missing.mov")})
    assert code == 400 and "not found" in s["error"].lower()
    open_clip(srv, clip)
    code, s = srv.req("/api/track", {})
    assert code == 400 and "Click on what you want" in s["error"]
    code, s = srv.req("/api/export", {})
    assert code == 400 and "track it first" in s["error"]
    code, s = srv.req("/api/point", {"frame": 99, "obj": 1, "x": 1, "y": 1})
    assert code == 400
    code, s = srv.req("/api/point", {"frame": 0, "obj": 12, "x": 1, "y": 1})
    assert code == 400
    code, s = srv.req("/api/settings", {"bogus": 1})
    assert code == 400
    code, s = srv.req("/api/box", {"frame": 0, "obj": 1, "x1": 5, "y1": 5, "x2": 6, "y2": 6})
    assert code == 400 and "small" in s["error"]
    r = urllib.request.Request(srv.url + "/api/point", data=b"{not json", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(r)
    assert e.value.code == 400


def test_busy_while_tracking_and_cancel(tmp_path, clip):
    srv = Server(tmp_path, slow=0.15)
    try:
        open_clip(srv, clip)
        x, y = ball_center(0)
        srv.req("/api/point", {"frame": 0, "obj": 1, "x": x, "y": y})
        assert srv.req("/api/track", {"direction": "forward"})[0] == 200
        code, s = srv.req("/api/point", {"frame": 2, "obj": 1, "x": x, "y": y})
        assert code == 409 and "Tracking" in s["error"]
        assert srv.req("/api/track", {})[0] == 409
        srv.req("/api/cancel", {})
        s = srv.wait_job()
        assert s["job"]["message"] == "Stopped" and s["tracked_count"] < 16
    finally:
        srv.stop()


def test_box_and_multiple_objects_and_hidden(srv, clip):
    open_clip(srv, clip)
    x, y = ball_center(4)
    code, s = srv.req("/api/box", {"frame": 4, "obj": 2, "x1": x - 30, "y1": y - 30, "x2": x + 30, "y2": y + 30})
    assert code == 200 and s["objects"] == [2]
    srv.req("/api/track", {})
    srv.wait_job()
    srv.req("/api/hidden", {"obj": 2, "hidden": True})
    code, png = srv.req("/api/matte/4", raw=True)
    m = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert m[..., 3].max() == 0  # the only object is hidden
    code, s = srv.req("/api/clear", {"frame": 4, "obj": 2})
    assert s["prompts"] == []


def test_quit_writes_done_file(tmp_path, clip):
    done = tmp_path / "x.done"
    srv = Server(tmp_path, done_file=str(done))
    open_clip(srv, clip)
    x, y = ball_center(1)
    srv.req("/api/point", {"frame": 1, "obj": 1, "x": x, "y": y})
    srv.req("/api/track", {})
    srv.wait_job()
    srv.req("/api/export", {"formats": ["png"]})
    srv.wait_job()
    srv.req("/api/quit", {})
    srv.thread.join(5)
    assert not srv.thread.is_alive()
    kv = dict(line.split("=", 1) for line in done.read_text().splitlines())
    assert kv["png"].endswith("shot_matte") and kv["frame_count"] == "16" and kv["first_frame"] == "0"


def test_window_closed_shuts_down(tmp_path):
    done = tmp_path / "y.done"
    srv = Server(tmp_path, done_file=str(done), bye_grace=0.3)
    srv.req("/api/ping", {})
    srv.req("/api/bye", {})
    srv.thread.join(5)
    assert not srv.thread.is_alive() and done.read_text().strip() == ""


def test_reload_does_not_shut_down(tmp_path):
    srv = Server(tmp_path, bye_grace=0.6)
    try:
        srv.req("/api/bye", {})
        time.sleep(0.2)
        srv.req("/api/ping", {})  # the reloaded page pings again
        time.sleep(1.0)
        assert srv.thread.is_alive()
    finally:
        srv.stop()


def test_offline_model_download_message(tmp_path):
    clip = make_clip(tmp_path / "c.mp4", n=3)

    class ProxyError(Exception):
        pass

    ProxyError.__module__ = "httpx._exceptions"

    def boom(*a):
        try:
            raise OSError("Tunnel connection failed")
        except OSError as inner:
            raise ProxyError("403 Forbidden") from inner

    s = Session(engine_factory=boom)
    job = s.open_clip(str(clip), wait=True)
    assert s.status == "error"
    assert "Couldn't download the SAM 2 model" in job.error and "internet" in job.error
