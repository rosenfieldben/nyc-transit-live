"""Record every socket destination and every hostname resolution, in every Python
process that inherits PYTHONPATH.

WHY sitecustomize. Python imports this module automatically at interpreter startup if
it is importable, so putting this directory on PYTHONPATH instruments the whole
process tree (run_all.sh's scripts, and anything they spawn) with no sudo, no dtrace
and no change to the scripts being measured. Inert unless NJT_SOCKET_TRACE is set.

THE STARTUP MARKER IS NOT DECORATION. An empty trace file and an absent trace file
look identical to a reader, and both look identical to a tracer that never loaded.
Writing one line per interpreter at startup makes "nothing connected anywhere" a
POSITIVE measurement: N markers and zero connects is evidence, while no file at all
proves only that nothing wrote one.
"""
import os
import sys

_TRACE = os.environ.get("NJT_SOCKET_TRACE")
if _TRACE:
    def _record(text):
        try:
            with open(_TRACE, "a") as fh:
                fh.write(text)
        except Exception:
            pass

    def _audit(event, args):
        # Only the two socket events; everything else returns immediately, including
        # the "open" event our own _record raises.
        if event == "socket.getaddrinfo":
            _record(f"resolve\t{args[0]!r}\n")
        elif event == "socket.connect":
            _record(f"connect\t{args[1]!r}\n")

    _record(f"start\t{' '.join(sys.argv)[:160]!r}\n")
    sys.addaudithook(_audit)
