"""Interactive Magic-Mask-style editor built on OpenCV windows.

Click on what you want, track it, correct any frame that drifts, track again, export.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

from .refine import RefineSettings, auto_refine_radius, finish_matte, temporal_soft_mask
from .tracker import MaskStore, PromptSet, apply_prompts, track

WIN = "FreeMask"
REFINE_WIN = "FreeMask - Refine"

# BGR colours per object id.
PALETTE = [
    (0, 0, 0),
    (60, 200, 255),
    (255, 140, 40),
    (120, 255, 120),
    (255, 80, 220),
    (80, 80, 255),
    (255, 255, 80),
    (200, 160, 255),
    (40, 200, 160),
    (160, 120, 60),
]

HELP = """
  FreeMask controls
  -----------------
  Left click ............ add (include) point
  Right click ........... subtract (exclude) point
  Left drag ............. draw a box around the object
  1-9 ................... choose which object you are marking
  Z ..................... undo last point on this frame/object
  C ..................... clear this frame's marks for this object
  T or Space ............ track forwards AND backwards
  ] ..................... track forwards only
  [ ..................... track backwards only
  A / D or arrows ....... previous / next frame
  W / S ................. jump -10 / +10 frames
  Home / End (or G / J) . first / last frame
  N ..................... jump to next marked frame
  V ..................... view: overlay -> refined matte -> cutout -> original
  H ..................... hide/show the current object in the final matte
  P ..................... save session (marks + refine settings)
  E ..................... export mattes
  ? ..................... print this help
  Q / Esc ............... quit  (Esc also cancels tracking)
"""

LEFT_KEYS = {ord("a"), ord(","), 2424832, 65361, 63234}
RIGHT_KEYS = {ord("d"), ord("."), 2555904, 65363, 63235}
HOME_KEYS = {ord("g"), 2359296, 65360, 63273}
END_KEYS = {ord("j"), 2293760, 65367, 63275}
VIEWS = ["overlay", "matte", "cutout", "original"]


class Editor:
    def __init__(self, info, frames_dir, engine, prompts: PromptSet, settings: RefineSettings,
                 on_save, on_export, max_display=(1400, 820)):
        self.info = info
        self.frames_dir = frames_dir
        self.engine = engine
        self.prompts = prompts
        self.settings = settings
        self.on_save = on_save
        self.on_export = on_export
        self.store = MaskStore(info.frame_count, info.proc_height, info.proc_width)
        self.frame = 0
        self.obj = 1
        self.view = 0
        self.stale = False
        self.dirty = True
        self.drag_start = None
        self.drag_now = None
        self.message = "Click the object you want to mask, then press T to track."
        self.message_until = time.time() + 8
        self._frame_cache: dict[int, np.ndarray] = {}
        self._panel_ready = False

        s = min(max_display[0] / info.proc_width, max_display[1] / info.proc_height, 1.0)
        self.disp_scale = s
        self.disp_size = (int(info.proc_width * s), int(info.proc_height * s))
        # Preview at tracking resolution: scale pixel-based settings accordingly.
        self.preview_ratio = info.proc_width / info.width

        if prompts.nonempty():
            apply_prompts(engine, prompts, self.store)
            self.frame = prompts.frames()[0]
            self.stale = True

    # ------------------------------------------------------------------ helpers

    def load_frame(self, i: int) -> np.ndarray:
        img = self._frame_cache.get(i)
        if img is None:
            img = cv2.imread(f"{self.frames_dir}/{i:05d}.jpg")
            if len(self._frame_cache) > 48:
                self._frame_cache.pop(next(iter(self._frame_cache)))
            self._frame_cache[i] = img
        return img

    def say(self, text: str, secs: float = 4.0):
        self.message = text
        self.message_until = time.time() + secs
        print(text)
        self.dirty = True

    def preview_settings(self) -> RefineSettings:
        s = RefineSettings.from_dict(self.settings.to_dict())
        r = self.preview_ratio
        s.grow = int(round(s.grow * r))
        s.feather = s.feather * r
        if s.smart_refine < 0:
            s.smart_refine = auto_refine_radius(self.info.width, self.info.height)
        s.smart_refine = max(1, int(round(s.smart_refine * r))) if s.smart_refine else 0
        return s

    # ------------------------------------------------------------------ drawing

    def compose(self, i: int, live_masks=None) -> np.ndarray:
        img = self.load_frame(i).copy()
        view = VIEWS[self.view]

        if view in ("matte", "cutout"):
            s = self.preview_settings()
            soft = temporal_soft_mask(self.store.combined, i, self.info.frame_count, s)
            if soft is None:
                soft = np.zeros(img.shape[:2], np.float32)
            matte = finish_matte(soft, s, (img.shape[1], img.shape[0]), img)
            if view == "matte":
                img = cv2.cvtColor((matte * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            else:
                checker = np.indices(img.shape[:2]).sum(axis=0) // 16 % 2
                bg = np.where(checker[..., None] == 1, 110, 70).astype(np.float32)
                img = (img * matte[..., None] + bg * (1 - matte[..., None])).astype(np.uint8)
        elif view == "overlay":
            objs = live_masks if live_masks is not None else {
                o: self.store.get_obj(i, o) for o in self.store.objects_at(i)}
            for o, m in objs.items():
                if m is None or not m.any():
                    continue
                color = np.array(PALETTE[o % len(PALETTE)], np.float32)
                alpha = 0.55 if o == self.obj else 0.3
                if o in self.store.hidden:
                    alpha = 0.12
                img[m] = (img[m] * (1 - alpha) + color * alpha).astype(np.uint8)
                contours, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(img, contours, -1, PALETTE[o % len(PALETTE)], 2)

        img = cv2.resize(img, self.disp_size, interpolation=cv2.INTER_AREA)
        if view != "original":
            self.draw_prompts(img, i)
        self.draw_hud(img, i)
        return img

    def draw_prompts(self, img, i):
        s = self.disp_scale
        for (f, o), p in self.prompts.items.items():
            if f != i:
                continue
            col = PALETTE[o % len(PALETTE)]
            if p.box is not None:
                x1, y1, x2, y2 = (int(v * s) for v in p.box)
                cv2.rectangle(img, (x1, y1), (x2, y2), col, 2)
            for (x, y), lab in zip(p.points, p.labels):
                c = (int(x * s), int(y * s))
                cv2.circle(img, c, 7, (255, 255, 255), -1)
                cv2.circle(img, c, 5, (40, 200, 40) if lab else (40, 40, 230), -1)
        if self.drag_start and self.drag_now:
            cv2.rectangle(img, self.drag_start, self.drag_now, PALETTE[self.obj % len(PALETTE)], 1)

    def draw_hud(self, img, i):
        marked = "  [MARKED]" if i in self.prompts.frames() else ""
        tracked = f"tracked {self.store.tracked_count()}/{self.info.frame_count}"
        state = "  RE-TRACK NEEDED (T)" if self.stale else ""
        hidden = " (hidden)" if self.obj in self.store.hidden else ""
        line1 = (f"Frame {i + self.info.first_frame}  ({i + 1}/{self.info.frame_count}){marked}   "
                 f"Object {self.obj}{hidden}   View: {VIEWS[self.view]}   {tracked}{state}")
        self.text(img, line1, (10, 24))
        if time.time() < self.message_until:
            self.text(img, self.message, (10, img.shape[0] - 14))
        else:
            self.text(img, "?: help   T: track   E: export   P: save   Q: quit", (10, img.shape[0] - 14))

    @staticmethod
    def text(img, s, org):
        cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, s, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    # ------------------------------------------------------------------ input

    def to_proc(self, x, y):
        s = self.disp_scale
        return [min(max(x / s, 0), self.info.proc_width - 1), min(max(y / s, 0), self.info.proc_height - 1)]

    def update_prompt(self):
        p = self.prompts.get(self.frame, self.obj)
        masks = self.engine.set_prompt(self.frame, self.obj, p)
        if p.empty():
            # Drop the stale mask for this object on this frame.
            self.store.drop(self.frame, self.obj)
            if self.obj not in self.prompts.objects():
                self.engine.remove_object(self.obj)
                self.store.drop_object(self.obj)
        for o, m in masks.items():
            self.store.set(self.frame, o, m)
        self.stale = True
        self.dirty = True

    def on_mouse(self, event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drag_start, self.drag_now = (x, y), None
        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start and flags & cv2.EVENT_FLAG_LBUTTON:
            self.drag_now = (x, y)
            self.dirty = True
        elif event == cv2.EVENT_LBUTTONUP and self.drag_start:
            x0, y0 = self.drag_start
            p = self.prompts.get(self.frame, self.obj)
            if abs(x - x0) > 8 and abs(y - y0) > 8:
                a, b = self.to_proc(min(x0, x), min(y0, y)), self.to_proc(max(x0, x), max(y0, y))
                p.box = a + b
            else:
                p.points.append(self.to_proc(x, y))
                p.labels.append(1)
            self.drag_start = self.drag_now = None
            self.update_prompt()
        elif event == cv2.EVENT_RBUTTONDOWN:
            p = self.prompts.get(self.frame, self.obj)
            p.points.append(self.to_proc(x, y))
            p.labels.append(0)
            self.update_prompt()

    # ------------------------------------------------------------------ actions

    def do_track(self, direction):
        if not self.prompts.frames():
            self.say("Nothing marked yet - click on the object first.")
            return
        self.say(f"Tracking {direction}... (Esc to stop)", 2)
        cancelled = [False]
        t0 = time.time()

        def progress(frame, done, masks):
            self.frame = frame
            cv2.imshow(WIN, self.compose(frame, live_masks=masks if VIEWS[self.view] == "overlay" else None))
            k = cv2.waitKey(1) & 0xFF
            if k == 27:
                cancelled[0] = True

        finished = track(self.engine, self.prompts, self.store, direction, progress,
                         should_stop=lambda: cancelled[0])
        dt = time.time() - t0
        if finished:
            self.stale = False
            self.say(f"Tracked in {dt:.1f}s. Scrub through; click to fix any frame, then track again.", 8)
        else:
            self.say("Tracking stopped.")
        self.dirty = True

    def step(self, delta):
        self.frame = int(np.clip(self.frame + delta, 0, self.info.frame_count - 1))
        cv2.setTrackbarPos("frame", WIN, self.frame)
        self.dirty = True

    def next_marked(self):
        frames = self.prompts.frames()
        if not frames:
            return
        later = [f for f in frames if f > self.frame]
        self.frame = later[0] if later else frames[0]
        cv2.setTrackbarPos("frame", WIN, self.frame)
        self.dirty = True

    # ------------------------------------------------------------------ refine panel

    def build_refine_panel(self):
        cv2.namedWindow(REFINE_WIN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(REFINE_WIN, 460, 420)
        s = self.settings
        auto = auto_refine_radius(self.info.width, self.info.height)
        sliders = [
            ("grow (50=0)", s.grow + 50, 100),
            ("feather", int(round(s.feather)), 50),
            ("smart refine", auto if s.smart_refine < 0 else s.smart_refine, 30),
            ("consolidate", s.consolidate, 6),
            ("despeckle", min(s.remove_specks // 10, 300), 300),
            ("fill holes", min(s.fill_holes // 10, 500), 500),
            ("clean black %", int(s.clean_black * 100), 50),
            ("clean white %", int(s.clean_white * 100), 50),
            ("invert", int(s.invert), 1),
        ]
        for name, val, mx in sliders:
            cv2.createTrackbar(name, REFINE_WIN, int(val), mx, self._on_refine)
        cv2.imshow(REFINE_WIN, np.full((1, 460, 3), 40, np.uint8))
        self._panel_ready = True

    def _on_refine(self, _):
        if not self._panel_ready:
            return
        g = lambda n: cv2.getTrackbarPos(n, REFINE_WIN)  # noqa: E731
        self.settings.grow = g("grow (50=0)") - 50
        self.settings.feather = float(g("feather"))
        self.settings.smart_refine = g("smart refine")
        self.settings.consolidate = g("consolidate")
        self.settings.remove_specks = g("despeckle") * 10
        self.settings.fill_holes = g("fill holes") * 10
        self.settings.clean_black = g("clean black %") / 100
        self.settings.clean_white = g("clean white %") / 100
        self.settings.invert = bool(g("invert"))
        if VIEWS[self.view] == "overlay":
            self.view = VIEWS.index("matte")
        self.dirty = True

    # ------------------------------------------------------------------ main loop

    def run(self):
        print(HELP)
        cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WIN, self.on_mouse)
        cv2.createTrackbar("frame", WIN, self.frame, max(1, self.info.frame_count - 1), self._on_seek)
        self.build_refine_panel()

        while True:
            if self.dirty:
                cv2.imshow(WIN, self.compose(self.frame))
                self.dirty = False
            k = cv2.waitKeyEx(30)
            if k == -1:
                if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if time.time() > self.message_until and self.message:
                    self.message = ""
                    self.dirty = True
                continue
            special = k if k in LEFT_KEYS | RIGHT_KEYS | HOME_KEYS | END_KEYS else None
            ch = "" if special and k > 255 else chr(k & 0xFF).lower()

            if ch in ("\x1b", "q"):
                break
            elif ch and ch in "123456789":
                self.obj = int(ch)
                self.say(f"Marking object {self.obj}")
            elif ch in ("t", " "):
                self.do_track("both")
            elif ch == "]":
                self.do_track("forward")
            elif ch == "[":
                self.do_track("backward")
            elif special in LEFT_KEYS:
                self.step(-1)
            elif special in RIGHT_KEYS:
                self.step(1)
            elif ch == "w":
                self.step(-10)
            elif ch == "s":
                self.step(10)
            elif special in HOME_KEYS:
                self.step(-self.info.frame_count)
            elif special in END_KEYS:
                self.step(self.info.frame_count)
            elif ch == "n":
                self.next_marked()
            elif ch == "z":
                p = self.prompts.get(self.frame, self.obj)
                if p.points:
                    p.points.pop()
                    p.labels.pop()
                elif p.box is not None:
                    p.box = None
                self.update_prompt()
            elif ch == "c":
                p = self.prompts.get(self.frame, self.obj)
                p.points, p.labels, p.box = [], [], None
                self.update_prompt()
            elif ch == "v":
                self.view = (self.view + 1) % len(VIEWS)
                self.dirty = True
            elif ch == "h":
                self.store.hidden ^= {self.obj}
                self.say(f"Object {self.obj} {'hidden from' if self.obj in self.store.hidden else 'included in'} the matte")
            elif ch == "p":
                self.say(f"Session saved to {self.on_save(self)}")
            elif ch == "e":
                if self.stale or self.store.tracked_count() < self.info.frame_count:
                    self.say("Tracking first...", 1)
                    self.do_track("both")
                self.say("Exporting mattes...", 2)
                cv2.imshow(WIN, self.compose(self.frame))
                cv2.waitKey(1)
                paths = self.on_export(self)
                self.say("Exported: " + ", ".join(str(p) for p in paths.values()), 12)
            elif ch == "?":
                print(HELP)
                self.say("Help printed in the terminal window.")
        cv2.destroyAllWindows()

    def _on_seek(self, pos):
        if pos != self.frame:
            self.frame = pos
            self.dirty = True
