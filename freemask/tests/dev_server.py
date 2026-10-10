"""Run the FreeMask UI on a synthetic clip with the fake tracker (no model needed).

    python tests/dev_server.py [--port 8765] [--frames 48] [--real]
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from fakes import FakeEngine, make_clip  # noqa: E402

from freemask.server import App  # noqa: E402
from freemask.session import Session  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--empty", action="store_true", help="start on the open-clip screen")
    ap.add_argument("--clip", help="use this clip instead of a synthetic one")
    ap.add_argument("--done-file")
    ap.add_argument("--slow", type=float, default=0.0, help="seconds per tracked frame")
    args = ap.parse_args()

    class SlowFake(FakeEngine):
        def propagate(self, start, reverse):
            import time

            for item in super().propagate(start, reverse):
                time.sleep(args.slow)
                yield item

    tmp = Path(tempfile.mkdtemp(prefix="freemask_dev_"))
    clip = args.clip or str(make_clip(tmp / "sunset_walk.mp4", n=args.frames, size=(1280, 720), pretty=True))
    session = Session(engine_factory=lambda d, m, dev: SlowFake(d), quality=960)
    if not args.empty:
        session.open_clip(clip, out_dir=str(tmp / "out"))
    print("clip:", clip, flush=True)
    App(session, done_file=args.done_file, idle_timeout=3600).serve(port=args.port, open_window=False)


if __name__ == "__main__":
    main()
