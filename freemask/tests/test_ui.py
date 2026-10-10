"""Browser tests: drive the real UI in headless Chromium, like a user would."""

import os
import shutil
import time

import pytest
from fakes import ball_center, make_clip
from test_server import Server

sync_api = pytest.importorskip("playwright.sync_api")


def _chromium(p):
    candidates = [os.environ.get("FREEMASK_CHROMIUM"), "/opt/pw-browsers/chromium", shutil.which("chromium"),
                  shutil.which("google-chrome")]
    for exe in candidates:
        if exe and os.path.exists(exe):
            return p.chromium.launch(executable_path=exe)
    try:
        return p.chromium.launch()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"No Chromium available: {exc}")


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        b = _chromium(p)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    # 4xx responses are expected (the UI turns them into toasts); anything else is a bug.
    pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
          and pg.errors.append(m.text))
    yield pg
    assert pg.errors == [], pg.errors


def screen(pg):
    return pg.evaluate("document.querySelector('#app').dataset.screen")


def wait_screen(pg, name, timeout=20000):
    pg.wait_for_function(f"document.querySelector('#app').dataset.screen === '{name}'", timeout=timeout)


def wait_idle(pg, timeout=30000):
    pg.wait_for_function("ui.state && ui.state.job && ui.state.job.done", timeout=timeout)
    time.sleep(0.2)


def click_proc(pg, x, y, button="left"):
    """Click at tracking-resolution coordinates on the viewer."""
    box = pg.locator("#uiCanvas").bounding_box()
    st = pg.evaluate("ui.state.clip")
    pg.mouse.click(box["x"] + box["width"] * x / st["proc_width"], box["y"] + box["height"] * y / st["proc_height"],
                   button=button)


def test_open_track_refine_export_finish(tmp_path, page):
    clip = make_clip(tmp_path / "walk.mp4", n=24)
    done = tmp_path / "out.done"
    srv = Server(tmp_path, done_file=str(done))
    try:
        page.goto(srv.url)
        wait_screen(page, "open")
        assert page.locator(".open-card h1").inner_text().startswith("Mask anything")

        # Bad path -> friendly error toast, stays on the open screen
        page.fill("#openPath", str(tmp_path / "nope.mov"))
        page.click("#openForm button[type=submit]")
        page.wait_for_selector(".toast.err")
        assert "not found" in page.locator(".toast.err").first.inner_text().lower()

        page.fill("#openPath", str(clip))
        page.fill("#openStart", "2")
        page.fill("#openEnd", "21")
        page.click("#openForm button[type=submit]")
        wait_screen(page, "editor")
        assert page.locator("#clipName").inner_text() == "walk"
        assert page.locator("#tcFrame").inner_text() == "2"
        assert page.locator("#coach").is_visible()
        assert page.locator("#exportBtn").is_disabled()

        # Go to source frame 6 with the keyboard, click the ball
        for _ in range(4):
            page.keyboard.press("ArrowRight")
        assert page.locator("#tcFrame").inner_text() == "6"
        x, y = ball_center(6)
        click_proc(page, x, y)
        page.wait_for_function("ui.state.prompts.length === 1 && ui.state.stale")
        assert not page.locator("#coach").is_visible()
        assert page.locator("#staleBanner").is_hidden()  # nothing tracked yet

        # Right-click on the background adds an exclude point
        click_proc(page, 10, 10, button="right")
        page.wait_for_function("ui.state.prompts[0].labels.length === 2")
        assert page.evaluate("ui.state.prompts[0].labels") == [1, 0]
        page.keyboard.press("Meta+z")
        page.wait_for_function("ui.state.prompts[0].labels.length === 1")

        # Track both ways with the T key
        page.keyboard.press("t")
        wait_idle(page)
        assert page.evaluate("ui.state.tracked_count") == 20
        page.wait_for_selector(".toast.ok")
        assert "Tracked 20 frames" in page.locator(".toast.ok").first.inner_text()
        assert page.locator("#trackedInfo").inner_text().startswith("20")
        assert not page.locator("#exportBtn").is_disabled()

        # Views
        for v in ("matte", "cutout", "original", "overlay"):
            page.click(f"[data-view={v}]")
            assert page.locator(f"[data-view={v}]").get_attribute("class").endswith("active")
        # Refine slider switches to the matte view and reaches the server
        slider = page.locator("[data-key=grow]")
        slider.fill("6")
        page.wait_for_function("ui.state.settings.grow === 6")
        assert page.evaluate("ui.view") == "matte"
        page.wait_for_function("1", timeout=1000)
        time.sleep(0.4)
        assert srv.session.settings.grow == 6
        page.click(".switch-row")
        page.wait_for_function("ui.state.settings.invert === true")

        # Second object via the sidebar, box-drag around the ball on another frame
        page.click("#addObject")
        assert page.evaluate("ui.obj") == 2
        page.keyboard.press("End")
        x, y = ball_center(21)
        box = page.locator("#uiCanvas").bounding_box()
        st = page.evaluate("ui.state.clip")
        sx, sy = box["width"] / st["proc_width"], box["height"] / st["proc_height"]
        page.mouse.move(box["x"] + (x - 35) * sx, box["y"] + (y - 35) * sy)
        page.mouse.down()
        page.mouse.move(box["x"] + (x + 35) * sx, box["y"] + (y + 35) * sy, steps=6)
        page.mouse.up()
        page.wait_for_function("ui.state.prompts.some(p => p.object === 2 && p.box)")
        assert page.locator(".obj").count() == 2
        assert page.locator("#staleBanner").is_visible()

        # Hide object 2 from the matte with the eye button
        page.locator(".obj").nth(1).locator("button").click()
        page.wait_for_function("ui.state.hidden.includes(2)")

        # Export: re-tracks first because marks changed, then shows the done dialog
        page.locator(".fmt[data-fmt=cutout]").click()  # untick cutout
        page.click("#exportBtnTop")
        page.wait_for_selector("#doneModal:not([hidden])", timeout=30000)
        paths = page.locator("#donePaths li").all_inner_texts()
        assert len(paths) == 2 and any("walk_matte.mov" in p for p in paths)
        assert not (tmp_path / "walk_freemask" / "walk_cutout").exists()
        assert len(list((tmp_path / "walk_freemask" / "walk_matte").glob("*.png"))) == 20

        page.click("#doneFinish")
        page.wait_for_selector("text=Sent to DaVinci Resolve")
        srv.thread.join(10)
        assert not srv.thread.is_alive()
        kv = dict(line.split("=", 1) for line in done.read_text().splitlines())
        assert kv["first_frame"] == "2" and kv["frame_count"] == "20" and "cutout" not in kv
    finally:
        if srv.thread.is_alive():
            srv.stop()


def test_live_tracking_and_stop(tmp_path, page):
    clip = make_clip(tmp_path / "s.mp4", n=30)
    srv = Server(tmp_path, slow=0.12)
    try:
        srv.session.open_clip(str(clip), wait=True)
        page.goto(srv.url)
        wait_screen(page, "editor")
        x, y = ball_center(0)
        click_proc(page, x, y)
        page.wait_for_function("ui.state.prompts.length === 1")
        page.click("#trackFwd")
        page.wait_for_selector("#job:not([hidden])")
        assert page.locator("#statusPill").is_visible()
        page.wait_for_function("ui.frame >= 3")  # playhead follows the tracker
        assert page.locator("#trackBoth").is_disabled()
        page.keyboard.press("Escape")
        wait_idle(page)
        n = page.evaluate("ui.state.tracked_count")
        assert 3 <= n < 30
        page.wait_for_selector("text=Tracking stopped")
        assert page.locator("#job").is_hidden()
    finally:
        srv.stop()


def test_loading_screen_and_error_screen(tmp_path, page):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video at all")
    srv = Server(tmp_path)
    try:
        srv.session.open_clip(str(bad))
        page.goto(srv.url)
        wait_screen(page, "error")
        assert "render the shot to ProRes" in page.locator("#errorMsg").inner_text()
        page.click("#errorBack")
        assert screen(page) == "open"
    finally:
        srv.stop()


def test_small_window_layout(tmp_path, browser):
    clip = make_clip(tmp_path / "s.mp4", n=6)
    srv = Server(tmp_path)
    try:
        srv.session.open_clip(str(clip), wait=True)
        pg = browser.new_page(viewport={"width": 1024, "height": 680})
        pg.goto(srv.url)
        wait_screen(pg, "editor")
        time.sleep(0.3)
        # Nothing spills sideways and the viewer keeps its aspect ratio
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        c = pg.locator("#imgCanvas").bounding_box()
        assert abs(c["width"] / c["height"] - 16 / 9) < 0.02
        tb = pg.locator(".viewer-toolbar").bounding_box()
        assert tb["x"] >= 0 and tb["x"] + tb["width"] <= 1024 - 280
    finally:
        srv.stop()
