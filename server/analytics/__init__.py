"""The read-only analytics layer: snapshot, stats, cache.

Nothing in this package writes to Notion. `snapshot.py` pulls the eight
databases (or the parser CSVs) into one JSON file under `cache/`, and every
module under `stats/` turns that file into one result JSON. See SPEC.md.
"""
