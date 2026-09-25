"""Decode the committed bus capture into the rows /api/buses serves, for the Rail pair.

The capture is backend/tests/fixtures/bus_vehicle_positions.pb, the OneBusAway response that
section 6.0's probe committed: 2136 positioned buses from one real poll. It is decoded by the
backend's own decoder, feeds.fetch_vehicle_positions, with its HTTP client stubbed exactly as
backend/tests/test_models.py stubs it, so the rows are the rows the endpoint would serve.

NOTHING HERE CAN REACH A NETWORK OR SPEND A CREDENTIAL. The client is a stub that returns the
committed bytes. The credentials are set BEFORE the backend is imported, because importing it
loads the project's .env and that file carries real ones on the operator's machine: load_dotenv
never overrides a variable that is already set, so the fake key and the two empty NJ Transit
names are what the process sees. No NJ Transit code runs here at all; the blanking is so that a
future edit to this file cannot make one mint by accident.

    backend/.venv/bin/python docs/reviews/map-redesign/followup-1/decode_capture.py OUT.json
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]

os.environ["BUS_TIME_API_KEY"] = "capture-decode-only"
os.environ["NJT_USERNAME"] = ""
os.environ["NJT_PASSWORD"] = ""
sys.path.insert(0, str(ROOT / "backend"))

import feeds  # noqa: E402


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def get(self, url: str, params: dict | None = None) -> _Resp:
        return _Resp(self._content)


def main(out: str) -> None:
    raw = (ROOT / "backend" / "tests" / "fixtures" / "bus_vehicle_positions.pb").read_bytes()
    vehicles, feed_timestamp = asyncio.run(feeds.fetch_vehicle_positions(_Client(raw)))
    pathlib.Path(out).write_text(json.dumps({"feed_timestamp": feed_timestamp, "rows": vehicles}))
    print(f"{len(vehicles)} buses written to {out}")


if __name__ == "__main__":
    main(sys.argv[1])
