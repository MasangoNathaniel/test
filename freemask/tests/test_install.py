"""The Resolve menu installer writes the script, with the install path baked in, per OS."""

import platform

import pytest

from freemask import install_resolve


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_install_and_remove(tmp_path, monkeypatch, system):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))

    (target,) = install_resolve.install(home=tmp_path / "My Apps" / "FreeMask")
    expected = {
        "Darwin": "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
        "Windows": "AppData/Roaming/Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility",
        "Linux": ".local/share/DaVinciResolve/Fusion/Scripts/Utility",
    }[system]
    assert target == tmp_path / expected / "FreeMask.lua"
    text = target.read_text()
    assert "__FREEMASK_HOME__" not in text
    assert f"local FREEMASK_HOME = [[{tmp_path / 'My Apps' / 'FreeMask'}]]" in text

    assert install_resolve.remove() == [target]
    assert not target.exists()
