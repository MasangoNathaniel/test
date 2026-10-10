"""The real SAM 2 code path (skipped when torch/sam2 aren't installed).

Checkpoints are downloaded on first use; when that isn't possible (offline CI) the model
is built with random weights. That still exercises every call FreeMask makes into SAM 2
(prompting, two-way propagation, removing objects, shapes and dtypes) — just not the
quality of the masks.
"""

import os

import numpy as np
import pytest
from fakes import ball_center, make_clip

torch = pytest.importorskip("torch")
pytest.importorskip("sam2")

from sam2.build_sam import build_sam2_video_predictor  # noqa: E402
from sam2.sam2_video_predictor import SAM2VideoPredictor  # noqa: E402

from freemask import tracker  # noqa: E402
from freemask.session import Session  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("FREEMASK_SKIP_SAM2") == "1", reason="disabled")


@pytest.fixture(autouse=True)
def offline_weights(monkeypatch):
    real = SAM2VideoPredictor.from_pretrained.__func__

    def from_pretrained(cls, model_id, **kw):
        try:
            return real(cls, model_id, **kw)
        except Exception:
            cfg = {"tiny": "t", "small": "s", "base-plus": "b+", "large": "l"}[model_id.split("hiera-")[1]]
            return build_sam2_video_predictor(f"configs/sam2.1/sam2.1_hiera_{cfg}.yaml", None, **kw)

    monkeypatch.setattr(SAM2VideoPredictor, "from_pretrained", classmethod(from_pretrained))


def test_session_with_real_sam2(tmp_path):
    clip = make_clip(tmp_path / "shot.mp4", n=6)
    s = Session(model="tiny", device="cpu", engine_factory=lambda d, m, dev: tracker.Sam2Engine(d, m, dev))
    job = s.open_clip(str(clip), out_dir=str(tmp_path / "out"), wait=True)
    assert job.error is None, job.error
    assert s.engine.device == "cpu"

    x, y = ball_center(3)
    s.add_point(3, 1, x, y, 1)
    s.add_point(3, 1, 5, 5, 0)
    s.set_box(3, 2, 10, 10, 60, 60)
    assert s.store.objects_at(3) == [1, 2]
    m = s.store.get_obj(3, 1)
    assert m.shape == (180, 320) and m.dtype == bool

    assert s.track("both", wait=True).error is None
    assert s.store.tracked_count() == 6

    # Correct a frame after tracking, drop an object entirely, re-track.
    s.add_point(5, 1, *ball_center(5), 1)
    s.clear(3, 2)
    assert 2 not in s.prompts.objects()
    assert s.track("both", wait=True).error is None
    assert s.store.objects_at(0) == [1]

    job = s.export({"png", "video"}, wait=True)
    assert job.error is None, job.error
    assert len(list((tmp_path / "out" / "shot_matte").glob("*.png"))) == 6
    assert np.frombuffer(s.overlay_png(0), np.uint8).size > 0
    s.close()


def test_real_weights_track_the_subject(tmp_path, monkeypatch):
    """With the real downloaded model, the matte must actually follow the subject."""
    monkeypatch.undo()  # no random-weight fallback here
    try:
        from sam2.build_sam import _hf_download

        _hf_download("facebook/sam2.1-hiera-tiny")
    except Exception as exc:
        pytest.skip(f"model weights not downloadable here: {exc}")

    import cv2

    from fakes import H, W

    n = 10
    clip = make_clip(tmp_path / "shot.mp4", n=n)
    s = Session(model="tiny", device="cpu", engine_factory=lambda d, m, dev: tracker.Sam2Engine(d, m, dev))
    assert s.open_clip(str(clip), wait=True).error is None
    mid = n // 2
    s.add_point(mid, 1, *ball_center(mid), 1)
    assert s.track("both", wait=True).error is None
    for i in range(n):
        truth = np.zeros((H, W), np.uint8)
        cv2.circle(truth, ball_center(i), 25, 1, -1)
        m = s.store.get_obj(i, 1)
        iou = (m & truth.astype(bool)).sum() / max(1, (m | truth.astype(bool)).sum())
        assert iou > 0.85, f"frame {i}: IoU {iou:.2f}"
    s.close()
