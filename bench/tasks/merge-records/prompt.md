Implement `merge(records: list[dict]) -> list[dict]` in `merge.py`.

It merges records that share the same `id`:
- Group records by their `id` field. Records that have **no** `id` are dropped.
- Within a group, later records update the fields of earlier ones (last-write-wins),
  **except** a value of `None` must not overwrite an existing non-`None` value.
- Preserve the order in which each `id` first appears.
- Return one merged dict per `id`.

Keep the function name and signature; it must remain importable as
`from merge import merge`.

**All existing tests must keep passing.**
