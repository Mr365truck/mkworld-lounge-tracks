"""Apply Appendix A to the database.

Idempotent, and deliberately non-destructive: an existing track keeps whatever
`good_from_first` / `gate_note` / `active` values were set from the Settings screen,
and aliases added by hand through the "add as alias for..." prompt survive a re-seed.
"""
from sqlalchemy import select

from .schema import track_aliases, tracks
from .seed_data import TRACKS


def seed_tracks(conn) -> dict:
    """Insert missing tracks and aliases. Returns a count of what changed."""
    existing = {r.code: r.id for r in conn.execute(select(tracks.c.code, tracks.c.id))}
    added_tracks = 0
    added_aliases = 0

    for code, full_name, is_retro, has_gate, aliases in TRACKS:
        track_id = existing.get(code)
        if track_id is None:
            track_id = conn.execute(
                tracks.insert().values(
                    code=code, full_name=full_name,
                    is_retro=bool(is_retro), has_gate=bool(has_gate),
                )
            ).inserted_primary_key[0]
            existing[code] = track_id
            added_tracks += 1

        # The canonical code is always resolvable as an alias, so typing `BCi`
        # matches even though `bci` is the only alias listed for it.
        wanted = {a.lower() for a in aliases} | {code.lower()}
        have = {
            r.alias for r in conn.execute(
                select(track_aliases.c.alias).where(track_aliases.c.track_id == track_id)
            )
        }
        for alias in sorted(wanted - have):
            # An alias may already point at a different track (a hand-added one).
            # Leave it alone rather than stealing it; a collision is worth seeing.
            owner = conn.execute(
                select(track_aliases.c.track_id).where(track_aliases.c.alias == alias)
            ).scalar()
            if owner is None:
                conn.execute(track_aliases.insert().values(track_id=track_id, alias=alias))
                added_aliases += 1

    return {"tracks_added": added_tracks, "aliases_added": added_aliases}
