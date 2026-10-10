"""Run the real Resolve script (resolve/FreeMask.lua) against a simulated DaVinci Resolve.

The simulated Resolve is written in Lua so the script calls it exactly the way it calls
the real one (``obj:Method()``). ``test_end_to_end`` goes all the way: the Lua script
launches the real FreeMask app, the test drives its HTTP API like a user would, and then
checks what the script imported back into the "timeline".
"""

import json
import os
import re
import stat
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from fakes import ball_center, make_clip

lupa = pytest.importorskip("lupa")

ROOT = Path(__file__).resolve().parent.parent
LUA = (ROOT / "resolve" / "FreeMask.lua").read_text()

MOCK_RESOLVE = r"""
calls = { imports = {}, appends = {}, tracks_added = 0, folders = {} }
local function obj(t) return t end

local clip_path = CLIP_PATH
mpi = obj({ GetClipProperty = function(self, k) return clip_path end })

item = obj({
  GetMediaPoolItem = function(self) return HAS_MEDIA and mpi or nil end,
  GetLeftOffset = function(self) return LEFT end,
  GetDuration = function(self) return DURATION end,
  GetStart = function(self) return 90100 end,  -- 01:00:04:00 @ 25 fps
})

local video_tracks = 1
timeline = obj({
  GetCurrentVideoItem = function(self) return HAS_ITEM and item or nil end,
  GetSetting = function(self, k) if k == "timelineFrameRate" then return "25" end end,
  GetCurrentTimecode = function(self) return PLAYHEAD_TC end,
  AddTrack = function(self, kind) video_tracks = video_tracks + 1; calls.tracks_added = calls.tracks_added + 1; return true end,
  GetTrackCount = function(self, kind) return video_tracks end,
  SetTrackName = function(self, kind, i, name) calls.track_name = name; return true end,
})

local function folder(name)
  local f = { name = name, subs = {} }
  f.GetName = function(self) return self.name end
  f.GetSubFolderList = function(self) return self.subs end
  return f
end
local root = folder("Master")

media_pool = obj({
  GetRootFolder = function(self) return root end,
  AddSubFolder = function(self, parent, name)
    local f = folder(name); table.insert(parent.subs, f); table.insert(calls.folders, name); return f end,
  SetCurrentFolder = function(self, f) calls.current_folder = f:GetName(); return true end,
  ImportMedia = function(self, list)
    local out = {}
    for _, v in ipairs(list) do
      table.insert(calls.imports, v)
      table.insert(out, { kind = "mpi", src = v })
    end
    return out
  end,
  AppendToTimeline = function(self, infos)
    for _, v in ipairs(infos) do table.insert(calls.appends, v) end
    return { {} }
  end,
})

project = obj({
  GetCurrentTimeline = function(self) return HAS_TIMELINE and timeline or nil end,
  GetMediaPool = function(self) return media_pool end,
})
resolve = obj({
  GetProjectManager = function(self)
    return { GetCurrentProject = function(self) return project end }
  end,
})
"""


def run_lua(home, clip_path, *, launcher=None, left=0, duration=12, playhead="01:00:04:05",
            has_item=True, has_media=True, has_timeline=True):
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    g = lua.globals()
    g.CLIP_PATH = str(clip_path)
    g.LEFT, g.DURATION, g.PLAYHEAD_TC = left, duration, playhead
    g.HAS_ITEM, g.HAS_MEDIA, g.HAS_TIMELINE = has_item, has_media, has_timeline
    printed = []
    g.print = lambda *a: printed.append(" ".join(str(x) for x in a))
    lua.execute(MOCK_RESOLVE)
    g.bmd = lua.table_from({"wait": lambda s: time.sleep(min(float(s), 0.2))})
    if launcher:
        def execute(cmd):
            if "-m freemask" in cmd:
                launcher(cmd)
                return 0
            return os.system(cmd)

        g.os.execute = execute
    lua.execute(LUA.replace("__FREEMASK_HOME__", str(home)))
    calls = g.calls

    def tolist(t):
        return [dict(v) if lupa.lua_type(v) == "table" else v for v in t.values()] if t else []

    return {
        "ok": g.FREEMASK_RESULT,
        "printed": printed,
        "imports": tolist(calls.imports),
        "appends": tolist(calls.appends),
        "tracks_added": calls.tracks_added,
        "folders": list(calls.folders.values()),
        "track_name": calls.track_name,
    }


@pytest.fixture
def home(tmp_path):
    """A FreeMask install folder whose .venv/bin/python is this test's Python."""
    h = tmp_path / "FreeMask"
    (h / ".venv" / "bin").mkdir(parents=True)
    (h / "freemask").symlink_to(ROOT / "freemask")
    py = h / ".venv" / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    return h


@pytest.fixture
def clip(tmp_path):
    media = tmp_path / "Media"
    media.mkdir()
    return make_clip(media / "A001 C002.mp4", n=20)


def parse_cmd(cmd):
    """Turn the shell command the script built back into its arguments."""
    import shlex

    parts = shlex.split(cmd.split("&&", 1)[1].replace("nohup", "", 1).split(" > /dev/null", 1)[0])
    args = parts[parts.index("freemask") + 1:]
    out = {"source": args[0]}
    it = iter(args[1:])
    for k in it:
        out[k.lstrip("-")] = next(it)
    return out


def test_guards(home, clip):
    r = run_lua(home, clip, has_timeline=False)
    assert "Open a timeline first." in r["printed"][-1]
    r = run_lua(home, clip, has_item=False)
    assert "playhead over a video clip" in r["printed"][-1]
    r = run_lua(home, clip, has_media=False)
    assert "no media file" in r["printed"][-1]
    r = run_lua(home.parent / "not-installed", clip)
    assert "isn't installed" in r["printed"][-1]


def test_command_and_import(home, clip):
    seen = {}

    def launcher(cmd):
        seen["cmd"] = cmd
        a = parse_cmd(cmd)
        seen["args"] = a
        out = Path(a["out"])
        for sub, suffix in (("png", "matte"), ("cutout", "cutout")):
            (out / f"{a['name']}_{suffix}").mkdir(parents=True, exist_ok=True)
        Path(a["done-file"]).write_text(
            f"png={out / (a['name'] + '_matte')}\ncutout={out / (a['name'] + '_cutout')}\n"
            f"video={out / (a['name'] + '_matte.mov')}\nfirst_frame=3\nframe_count=12\nfps=25\nname={a['name']}\n")

    r = run_lua(home, clip, launcher=launcher, left=3, duration=12, playhead="01:00:04:05")
    a = seen["args"]
    assert a["source"] == str(clip)
    assert (a["start"], a["end"]) == ("3", "14")
    # playhead 01:00:04:05 @25 = 90105; the clip starts at 90100 -> 5 frames in
    assert a["frame"] == "8"
    assert a["name"] == "A001_C002_3-14"
    assert a["out"] == str(clip.parent / "A001_C002_freemask")
    assert seen["cmd"].startswith("cd '") and seen["cmd"].rstrip().endswith("&")

    assert r["ok"] is True, r["printed"]
    assert r["folders"] == ["FreeMask"]
    imports = r["imports"]
    seq = [i for i in imports if isinstance(i, dict)]
    assert {Path(i["FilePath"]).name for i in seq} == {"A001_C002_3-14_matte_%06d.png", "A001_C002_3-14_cutout_%06d.png"}
    assert all(i["StartIndex"] == 3 and i["EndIndex"] == 14 for i in seq)
    assert any(isinstance(i, str) and i.endswith(".mov") for i in imports)
    (append,) = r["appends"]
    assert append["recordFrame"] == 90100 and append["startFrame"] == 0 and append["endFrame"] == 11
    assert append["trackIndex"] == 2 and r["tracks_added"] == 1 and r["track_name"] == "FreeMask"
    assert dict(append["mediaPoolItem"])["src"]["FilePath"].endswith("_cutout_%06d.png")


def test_playhead_maps_to_source_frame(home, clip):
    seen = {}

    def launcher(cmd):
        a = parse_cmd(cmd)
        seen.update(a)
        Path(a["done-file"]).write_text("")

    # Timeline item starts at 90100 (01:00:04:00 @25). Playhead 01:00:04:05 -> 5 frames in.
    r = run_lua(home, clip, launcher=launcher, left=3, duration=12, playhead="01:00:04:05")
    assert seen["frame"] == "8"  # left offset 3 + 5
    assert any("Closed without exporting" in p for p in r["printed"])


def test_error_from_app_is_reported(home, clip):
    def launcher(cmd):
        Path(parse_cmd(cmd)["done-file"]).write_text("error=Ran out of memory\n")

    r = run_lua(home, clip, launcher=launcher)
    assert r["ok"] is False
    assert any("Ran out of memory" in p for p in r["printed"])
    assert r["imports"] == []


def test_image_sequence_uses_folder(home, tmp_path):
    seq = tmp_path / "seq"
    seq.mkdir()
    seen = {}

    def launcher(cmd):
        a = parse_cmd(cmd)
        seen.update(a)
        Path(a["done-file"]).write_text("")

    run_lua(home, seq / "plate_[0001-0020].exr", launcher=launcher)
    assert seen["source"] == str(seq)


def test_resumes_previous_session(home, clip):
    out = clip.parent / "A001_C002_freemask"
    out.mkdir()
    (out / "A001_C002_0-11_session.json").write_text("{}")
    seen = {}

    def launcher(cmd):
        a = parse_cmd(cmd)
        seen.update(a)
        Path(a["done-file"]).write_text("")

    run_lua(home, clip, launcher=launcher, left=0, duration=12)
    assert seen["session"].endswith("A001_C002_0-11_session.json")


def test_end_to_end(home, clip, monkeypatch):
    """Resolve script -> real FreeMask process -> user actions over HTTP -> import back."""
    monkeypatch.setenv("FREEMASK_ENGINE", "fakes:FakeEngine")
    monkeypatch.setenv("FREEMASK_NO_BROWSER", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parent))

    result = {}
    t = threading.Thread(target=lambda: result.update(run_lua(home, clip, left=2, duration=10, playhead="01:00:04:03")))
    t.start()

    log = clip.parent / "A001_C002_freemask" / "A001_C002_2-11_freemask.log"
    url = None
    for _ in range(300):
        if log.exists():
            m = re.search(r"running at (http://\S+)", log.read_text())
            if m:
                url = m.group(1).rstrip("/")
                break
        time.sleep(0.1)
    assert url, log.read_text() if log.exists() else "no log"

    def req(path, body=None):
        r = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read())

    def wait():
        for _ in range(400):
            s = req("/api/state")
            if s["job"] and s["job"]["done"]:
                return s
            time.sleep(0.05)
        raise TimeoutError

    s = wait()
    assert s["status"] == "ready", s
    assert s["start_frame"] == 3  # playhead 3 frames into the clip (source frame 5)
    x, y = ball_center(5)
    req("/api/point", {"frame": 3, "obj": 1, "x": x, "y": y})
    req("/api/track", {"direction": "both"})
    assert wait()["tracked_count"] == 10
    req("/api/export", {"formats": ["png", "cutout", "video"]})
    s = wait()
    assert s["job"]["error"] is None, s["job"]
    req("/api/quit", {})

    t.join(30)
    assert not t.is_alive()
    assert result["ok"] is True, result["printed"]
    seq = [i for i in result["imports"] if isinstance(i, dict)]
    assert len(seq) == 2 and all(i["StartIndex"] == 2 and i["EndIndex"] == 11 for i in seq)
    for i in seq:
        first = i["FilePath"].replace("%06d", "000002")
        assert os.path.exists(first), first
    assert result["appends"][0]["recordFrame"] == 90100
    assert any(isinstance(i, str) and os.path.exists(i) for i in result["imports"])  # the .mov
