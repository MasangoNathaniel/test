"""Entry point. Kept tiny so that even an import failure is reported back to Resolve."""

import os
import sys
import traceback


def _arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _report(done_file, message):
    if not done_file:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(done_file)), exist_ok=True)
        with open(done_file, "w", encoding="utf-8") as fh:
            fh.write("error=" + " ".join(str(message).split()) + "\n")
    except OSError:
        pass


def run():
    log = _arg("--log")
    if log or sys.stdout is None or sys.stderr is None:
        # Launched from Resolve (or pythonw): there is no console, so keep a log file.
        path = log or os.path.join(os.path.expanduser("~"), "freemask.log")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            stream = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
            sys.stdout = sys.stderr = stream
        except OSError:
            pass
    done_file = _arg("--done-file")
    try:
        from .cli import main

        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _report(done_file, exc.code)
        raise
    except BaseException as exc:  # noqa: BLE001 - report anything to Resolve
        traceback.print_exc()
        _report(done_file, f"{type(exc).__name__}: {exc}")
        raise


run()
