"""Negative GTFS-RT bodies, DERIVED from the committed golden fixtures (C3).

No new binary files. Every negative here is either a constant, a slice of a real
captured feed, or a message built with the protobuf API, so a reader can see
exactly what makes each one bad without opening a hex editor.

WHY TRUNCATING A REAL FIXTURE BEATS HAND-BUILT GARBAGE: a truncated capture is
the actual wire prefix an upstream emits when a response is cut off mid-stream (a
dropped connection, a proxy that flushed a partial body, a CDN that closed early),
tags, lengths and all. Hand-rolled junk bytes only prove the parser rejects bytes
that were never protobuf; a real prefix proves it rejects bytes that ARE protobuf
and simply stop in the middle, which is the failure an upstream actually produces
and the one a lenient parser is most likely to accept.

The empty body is the audit's exact signature and needs no derivation: an HTTP 200
with zero bytes, which ParseFromString accepts silently.
"""

from __future__ import annotations

from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

FIXTURES = Path(__file__).parent / "fixtures"

# The audit's signature: a 200 carrying nothing at all.
EMPTY_BODY = b""

# Not protobuf in any reading: a CDN error page or maintenance HTML served as 200.
GARBAGE_BODY = b"<html><body>503 Service Unavailable</body></html>"


def truncated(name: str, size: int = 40) -> bytes:
    """The first `size` bytes of a committed golden fixture.

    40 bytes lands inside the first entity of every fixture used here, so the
    parser sees a valid header tag followed by a length that runs off the end.
    """
    return (FIXTURES / name).read_bytes()[:size]


def golden(name: str) -> bytes:
    """A committed golden fixture, whole. The positive control for each negative."""
    return (FIXTURES / name).read_bytes()


def header_only(version: str = "2.0", timestamp: int | None = None) -> bytes:
    """A VALID feed with a real header and zero entities.

    This is the case that must keep working: a feed with nothing to report is not
    a broken feed. The ferry's overnight empty is exactly this shape, and the
    parser has to let it through untouched (parse_feed rule 4).
    """
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = version
    if timestamp is not None:
        feed.header.timestamp = timestamp
    return feed.SerializeToString()


def entity_without_header() -> bytes:
    """Wire bytes that PARSE but leave the message uninitialized.

    Field 2 (entity) length-delimited, holding a FeedEntity whose only field is
    its required id. protobuf parses this happily, and IsInitialized() is False
    because FeedHeader.gtfs_realtime_version is `required` in proto2 and no header
    was present. Hand-assembled rather than serialized, because the protobuf API
    refuses to SERIALIZE an uninitialized message: the only way to get these bytes
    is to write them, which is itself the point (an upstream that emits them is
    not using a normal protobuf writer either).
    """
    return b"\x12\x03\x0a\x01x"
