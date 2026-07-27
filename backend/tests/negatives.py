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

    WHERE 40 BYTES LANDS DIFFERS BY FIXTURE, and the earlier claim here that it
    always cuts mid-entity was wrong: the subway capture's header alone is 123
    bytes, so 40 cuts mid-HEADER, while the PATH and ferry captures have short
    headers and 40 reaches into their first entity. Both are real dropped-stream
    shapes and both must be rejected, so callers that care about the distinction
    pass an explicit size (see MID_ENTITY_SIZE) rather than relying on a default
    meaning the same thing everywhere.
    """
    return (FIXTURES / name).read_bytes()[:size]


# Past the subway capture's 123-byte header, so this truncation cuts inside an
# ENTITY rather than the header: the two halves of a dropped stream, both of which
# must be rejected.
MID_ENTITY_SIZE = 300


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
    its required id. protobuf parses this happily and the message has NO header,
    which is what parse_feed rejects.

    Hand-assembled rather than serialized only because it is shorter to write than
    to build. It is NOT an exotic shape, and an earlier version of this comment
    claimed it was: protobuf serializes messages missing `required` fields on
    request (SerializePartialToString in Python, and by default in Go, protobuf-js
    and Java's buildPartial), so bodies like this are exactly what a real producer
    can emit. That mistaken belief is what once justified checking IsInitialized()
    here, which is recursive and threw away good feeds over one bad entity; see
    parse_feed rule 3.
    """
    return b"\x12\x03\x0a\x01x"
