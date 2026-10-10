"""Tests for everything except the SAM 2 network itself (a fake engine stands in for it)."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from freemask import cli, tracker
from freemask.export import export_mattes
from freemask.refine import RefineSettings, clean_binary, finish_matte, temporal_soft_mask
from freemask.tracker import MaskStore, PromptSet, apply_prompts, track
from freemask.video_io import extract_frames

W, H, N = 320, 180, 12


def ball_center(i):
    return 40 + i * 20, 90


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "shot.mp4"
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (W, H))
    for i in range(N):
        img = np.full((H, W, 3), 30, np.uint8)
        cv2.circle(img, ball_center(i), 25, (230, 230, 230), -1)
        vw.write(img)
    vw.release()
    return path


class FakeEngine:
    """Segments the bright blob under the clicked point and follows it frame to frame."""

    device = "fake"

    def __init__(self, frames_dir, *a, **k):
        self.dir = Path(frames_dir)
        self.n = len(list(self.dir.glob("*.jpg")))
        self.cond = {}  # frame -> {obj: mask}

    def _blob(self, frame, x, y):
        g = cv2.imread(str(self.dir / f"{frame:05d}.jpg"), cv2.IMREAD_GRAYSCALE)
        _, labels = cv2.connectedComponents((g > 128).astype(np.uint8))
        lab = labels[int(y), int(x)]
        return labels == lab if lab else np.zeros_like(g, bool)

    def set_prompt(self, frame, obj, prompt):
        if prompt.empty():
            self.cond.get(frame, {}).pop(obj, None)
            return {}
        pos = [pt for pt, lab in zip(prompt.points, prompt.labels) if lab] or [
            [(prompt.box[0] + prompt.box[2]) / 2, (prompt.box[1] + prompt.box[3]) / 2]]
        m = self._blob(frame, *pos[0])
        self.cond.setdefault(frame, {})[obj] = m
        return {obj: m}

    def remove_object(self, obj):
        for d in self.cond.values():
            d.pop(obj, None)

    def propagate(self, start, reverse):
        prev = dict(self.cond.get(start, {}))
        rng = range(start, -1, -1) if reverse else range(start, self.n)
        for f in rng:
            out = {}
            for obj, pm in prev.items():
                if f in self.cond and obj in self.cond[f]:
                    out[obj] = self.cond[f][obj]
                    continue
                ys, xs = np.nonzero(pm)
                out[obj] = self._blob(f, xs.mean(), ys.mean()) if len(xs) else pm
                if not out[obj].any():  # moved too far; search the frame
                    out[obj] = self._blob(f, *ball_center(f))
            prev = out
            yield f, out


# ----------------------------------------------------------------------------- refine


def test_clean_binary_removes_specks_and_fills_holes():
    m = np.zeros((50, 50), bool)
    m[10:40, 10:40] = True
    m[20:23, 20:23] = False  # small hole
    m[2, 2] = True  # speck
    out = clean_binary(m, remove_specks=5, fill_holes=20)
    assert not out[2, 2]
    assert out[21, 21]


def test_clean_binary_never_erases_everything():
    m = np.zeros((20, 20), bool)
    m[5, 5] = True
    assert clean_binary(m, remove_specks=100, fill_holes=0)[5, 5]


def test_finish_matte_grow_feather_invert():
    soft = np.zeros((40, 40), np.float32)
    soft[10:30, 10:30] = 1
    base = finish_matte(soft, RefineSettings(smart_refine=0), (80, 80))
    grown = finish_matte(soft, RefineSettings(smart_refine=0, grow=4), (80, 80))
    assert base.shape == (80, 80)
    assert grown.sum() > base.sum()
    inv = finish_matte(soft, RefineSettings(smart_refine=0, invert=True), (80, 80))
    np.testing.assert_allclose(inv, 1 - base, atol=1e-6)
    fe = finish_matte(soft, RefineSettings(smart_refine=0, feather=3), (80, 80))
    assert 0 < fe[40, 19] < 1


def test_smart_refine_snaps_to_edges():
    guide = np.zeros((100, 100, 3), np.uint8)
    guide[:, 50:] = 255  # true edge at x=50
    soft = np.zeros((100, 100), np.float32)
    soft[:, 47:] = 1  # mask edge 3px off, as a low-res tracker would give
    out = finish_matte(soft, RefineSettings(smart_refine=8), (100, 100), guide)
    assert out[50, 49] < 0.5 < out[50, 50]
    assert out[50, 30] < 0.01 and out[50, 70] > 0.99


def test_consolidate_smooths_a_one_frame_dropout():
    masks = {i: np.ones((10, 10), bool) for i in range(5)}
    masks[2] = np.zeros((10, 10), bool)
    s = RefineSettings(consolidate=2, remove_specks=0)
    soft = temporal_soft_mask(lambda i: masks[i], 2, 5, s)
    assert soft.mean() > 0.5


# ----------------------------------------------------------------------------- prompts / store


def test_prompt_json_roundtrip_with_scale_and_offset():
    ps = PromptSet()
    p = ps.get(3, 2)
    p.points, p.labels, p.box = [[10, 20]], [1], [1, 2, 3, 4]
    data = ps.to_json(scale_to_source=2.0, frame_offset=100)
    assert data["prompts"][0]["frame"] == 103
    assert data["prompts"][0]["points"] == [[20, 40]]
    back = PromptSet.from_json(data, scale_from_source=0.5, frame_offset=100)
    q = back.get(3, 2)
    assert q.points == [[10, 20]] and q.box == [1, 2, 3, 4]


def test_mask_store_union_and_hidden():
    st = MaskStore(2, 4, 4)
    a = np.zeros((4, 4), bool); a[0] = True
    b = np.zeros((4, 4), bool); b[3] = True
    st.set(0, 1, a)
    st.set(0, 2, b)
    assert st.combined(0).sum() == 8
    st.hidden.add(2)
    assert st.combined(0).sum() == 4
    assert st.combined(1) is None


# ----------------------------------------------------------------------------- pipeline


def test_track_both_ways_and_export(clip, tmp_path):
    info = extract_frames(str(clip), str(tmp_path / "frames"), max_side=160)
    assert (info.width, info.height, info.frame_count) == (W, H, N)
    assert (info.proc_width, info.proc_height) == (160, 90)

    eng = FakeEngine(tmp_path / "frames")
    prompts = PromptSet()
    mid = 6
    cx, cy = ball_center(mid)
    p = prompts.get(mid, 1)
    p.points, p.labels = [[cx / 2, cy / 2]], [1]
    store = MaskStore(info.frame_count, info.proc_height, info.proc_width)
    apply_prompts(eng, prompts, store)
    assert track(eng, prompts, store, "both")
    assert store.tracked_count() == N  # frames before AND after the marked frame

    out = tmp_path / "out"
    paths = export_mattes(info, store, RefineSettings(), str(out), "shot", {"png", "cutout", "video"})
    pngs = sorted(paths["png"].glob("*.png"))
    assert len(pngs) == N and len(list(paths["cutout"].glob("*.png"))) == N
    assert paths["video"].exists() and paths["video"].stat().st_size > 0

    for i in (0, mid, N - 1):
        m = cv2.imread(str(pngs[i]), cv2.IMREAD_UNCHANGED)
        assert m.dtype == np.uint16 and m.shape == (H, W)
        x, y = ball_center(i)
        assert m[y, x] > 60000  # inside the ball
        assert m[10, 10] < 1000  # background
    rgba = cv2.imread(str(sorted(paths["cutout"].glob("*.png"))[0]), cv2.IMREAD_UNCHANGED)
    assert rgba.shape == (H, W, 4)


def test_forward_only_leaves_earlier_frames_empty(clip, tmp_path):
    info = extract_frames(str(clip), str(tmp_path / "frames"), max_side=320)
    eng = FakeEngine(tmp_path / "frames")
    prompts = PromptSet()
    p = prompts.get(5, 1)
    p.points, p.labels = [list(ball_center(5))], [1]
    store = MaskStore(info.frame_count, info.proc_height, info.proc_width)
    apply_prompts(eng, prompts, store)
    track(eng, prompts, store, "forward")
    assert not store.has(4) and store.has(N - 1)


def test_range_extraction(clip, tmp_path):
    info = extract_frames(str(clip), str(tmp_path / "frames"), start=3, end=7)
    assert info.first_frame == 3 and info.frame_count == 5


def test_cli_headless_end_to_end(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "Sam2Engine", FakeEngine)
    out = tmp_path / "cli_out"
    x, y = ball_center(8)
    cli.main([str(clip), "--headless", "--start", "2", "--end", "10", "--quality", "160",
              "--point", f"8:{x},{y}", "-o", str(out), "--grow", "2", "--format", "png"])
    files = sorted((out / "shot_matte").glob("*.png"))
    assert [f.name for f in files][0] == "shot_matte_000002.png"
    assert len(files) == 9
    meta = json.loads((out / "shot_freemask.json").read_text())
    assert meta["refine"]["grow"] == 2 and meta["first_frame"] == 2


def test_cli_rejects_frame_outside_range(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "Sam2Engine", FakeEngine)
    with pytest.raises(SystemExit):
        cli.main([str(clip), "--headless", "--end", "5", "--point", "9:10,10", "-o", str(tmp_path)])


def test_cli_session_file(clip, tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "Sam2Engine", FakeEngine)
    x, y = ball_center(4)
    session = {"prompts": [{"frame": 4, "object": 1, "points": [[x, y]], "labels": [1], "box": None}],
               "refine": {"invert": True}}
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(session))
    out = tmp_path / "o"
    cli.main([str(clip), "--headless", "--session", str(sp), "-o", str(out)])
    m = cv2.imread(str(out / "shot_matte" / "shot_matte_000004.png"), cv2.IMREAD_UNCHANGED)
    assert m[y, x] < 1000 and m[10, 10] > 60000  # inverted


def test_editor_clicks_views_and_export_without_a_window(clip, tmp_path):
    from freemask.gui import VIEWS, Editor

    info = extract_frames(str(clip), str(tmp_path / "frames"), max_side=320)
    eng = FakeEngine(tmp_path / "frames")
    exported = {}
    ed = Editor(info, str(tmp_path / "frames"), eng, PromptSet(), RefineSettings(),
                on_save=lambda e: "x", on_export=lambda e: exported.setdefault("ok", e.store))
    ed.frame = 3
    x, y = ball_center(3)
    s = ed.disp_scale
    ed.on_mouse(cv2.EVENT_LBUTTONDOWN, int(x * s), int(y * s), 0, None)
    ed.on_mouse(cv2.EVENT_LBUTTONUP, int(x * s), int(y * s), 0, None)
    assert ed.store.has(3) and ed.stale
    ed.on_mouse(cv2.EVENT_RBUTTONDOWN, 5, 5, 0, None)
    assert ed.prompts.get(3, 1).labels == [1, 0]

    for v in range(len(VIEWS)):
        ed.view = v
        img = ed.compose(3)
        assert img.shape[:2] == (ed.disp_size[1], ed.disp_size[0])

    track(eng, ed.prompts, ed.store, "both")
    assert ed.store.tracked_count() == N

    # Clearing the only marks removes the object so SAM 2 never sees an empty object.
    p = ed.prompts.get(3, 1)
    p.points, p.labels = [], []
    ed.update_prompt()
    assert not ed.store.has(0) and not ed.prompts.frames()
