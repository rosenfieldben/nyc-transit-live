"""The two contract fields the committed goldens have not caught up with yet.

Section 6.1 of docs/design/freshness-contract.md lands in three steps: the models
declare observed_at and provenance, the decoders fill them, and the goldens are
regenerated last. Between the decoders commit and the goldens commit every golden
in this directory is NARROWER than the decode output it pins, by exactly these two
names and by nothing else.

THIS WHOLE FILE IS DELETED BY THE GOLDENS COMMIT, and its deletion is the evidence
that the goldens caught up. A file that has to disappear cannot be forgotten the
way a tolerant assertion can, and `grep -rn _PENDING\\|contract_pending` on the
branch tip is the check.

WHAT THIS IS NOT. It is not a relaxed comparison. without_pending() removes exactly
two keys, so every assertion it feeds still fails on a renamed field, a dropped
field, an added third field, or any value that changed. What it tolerates is the
two names, at the one moment in this branch when tolerating them is the truth.
"""

from __future__ import annotations

PENDING_IN_GOLDEN = ("observed_at", "provenance")


def without_pending(value):
    """`value` with the two pending keys removed from every dict inside it.

    Recurses through lists and dicts so a golden of any shape (a flat row list, the
    railroad's {stop: {bucket: [rows]}}, NJT's flat arrivals index) can be compared
    without each caller knowing its own nesting.
    """
    if isinstance(value, list):
        return [without_pending(item) for item in value]
    if isinstance(value, dict):
        return {
            key: without_pending(item)
            for key, item in value.items()
            if key not in PENDING_IN_GOLDEN
        }
    return value
