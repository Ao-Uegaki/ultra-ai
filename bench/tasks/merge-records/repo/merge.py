"""Record merging."""
from __future__ import annotations


def merge(records):
    """Merge records that share the same 'id'.

    Rules:
    - Group records by their 'id' field. Records without an 'id' are dropped.
    - Within a group, later records update the fields of earlier ones
      (last-write-wins), EXCEPT a value of None must not overwrite an existing
      non-None value.
    - Preserve the order in which each id first appears.
    - Return one merged dict per id.
    """
    raise NotImplementedError
