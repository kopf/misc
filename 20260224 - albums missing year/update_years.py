#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "mutagen",
#     "musicbrainzngs",
#     "requests",
#     "python-Levenshtein",
# ]
# ///

"""
Find albums with missing or non-YYYY dates and offer to update MP3 ID3 tags with
the discovered year. This script prefers Discogs as the primary data source and
falls back to MusicBrainz when Discogs returns no usable results.

Run via `uv` so no separate requirements file is required:
    ./update_years.py /path/to/db
"""

import argparse
import os
import re
import sqlite3
import sys
from typing import Optional, List, Tuple

import requests
import musicbrainzngs
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TDRC, ID3NoHeaderError
import json
from datetime import datetime
try:
    from Levenshtein import ratio
except ImportError:
    from difflib import SequenceMatcher
    def ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()


YEAR_RE = re.compile(r"^(\d{4})$")


def find_problem_albums(conn: sqlite3.Connection, with_plays_or_ratings: bool = False) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # date empty or not a 4-digit year
    base_query = """
        SELECT id, name, album_artist, date
        FROM album
        WHERE date IS NULL OR date = '' OR date NOT GLOB '[0-9][0-9][0-9][0-9]'
    """
    if with_plays_or_ratings:
        # Inner join with annotation to ensure tracks have plays or ratings
        query = f"""
        {base_query}
        AND id IN (
            SELECT DISTINCT album_id FROM media_file mf
            WHERE album_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM annotation
                WHERE item_id = mf.id AND item_type = 'track' AND (play_count > 0 OR rating > 0)
            )
        )
        ORDER BY name
        """
    else:
        query = base_query + "ORDER BY name"
    cur.execute(query)
    return cur.fetchall()


def lookup_year_discogs(artist: str, album: str, token: Optional[str], user_agent: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (year, url, artist_name, album_name) from Discogs or None."""
    if not artist and not album:
        return None
    url = "https://api.discogs.com/database/search"
    params = {
        "artist": artist or "",
        "release_title": album or "",
        "per_page": 5,
    }
    if token:
        params["token"] = token
    headers = {"User-Agent": user_agent}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
    except Exception:
        return None
    data = r.json()
    results = data.get("results", [])
    years = []
    result_url = None
    result_artist = None
    result_album = None
    for res in results:
        y = res.get("year")
        if isinstance(y, int) and 1000 <= y <= 9999:
            years.append(y)
            if not result_url:
                result_url = res.get("uri")
                result_artist = res.get("artist", "")
                result_album = res.get("title", "")
    if years:
        return (str(min(years)), result_url, result_artist or "", result_album or "")
    return None


def lookup_year_mb(artist: str, album: str) -> Optional[Tuple[str, str, str, str]]:
    """Return (year, url, artist_name, album_name) from MusicBrainz or None.
    Prioritizes actual release dates (release-events) over release group first-release-date.
    """
    result_url = None
    result_artist = None
    result_album = None

    # Search releases first (more authoritative dates via release-events)
    try:
        r = musicbrainzngs.search_releases(artist=artist or "", release=album or "", limit=10)
        rels = r.get("release-list", [])
        for rel in rels:
            # prefer release-events which have actual structured dates
            events = rel.get("release-event-list", [])
            for event in events:
                d = event.get("date")
                if d and re.match(r"^\d{4}-\d{2}-\d{2}", d):  # validate YYYY-MM-DD format
                    m = re.match(r"^(\d{4})", d)
                    if m:
                        year = int(m.group(1))
                        result_url = f"https://musicbrainz.org/release/{rel.get("id")}" if rel.get("id") else None
                        result_artist = rel.get("artist-credit-phrase", "")
                        result_album = rel.get("title", "")
                        # Return immediately on first valid date found
                        return (str(year), result_url, result_artist or "", result_album or "")
            # fallback to top-level date if no events found
            if not events:
                d = rel.get("date")
                if d and re.match(r"^\d{4}-\d{2}-\d{2}", d):  # validate YYYY-MM-DD format
                    m = re.match(r"^(\d{4})", d)
                    if m:
                        year = int(m.group(1))
                        result_url = f"https://musicbrainz.org/release/{rel.get("id")}" if rel.get("id") else None
                        result_artist = rel.get("artist-credit-phrase", "")
                        result_album = rel.get("title", "")
                        # Return immediately on first valid date found
                        return (str(year), result_url, result_artist or "", result_album or "")
    except Exception:
        pass

    # Fallback to release groups if release search didn't yield results
    try:
        res = musicbrainzngs.search_release_groups(artist=artist or "", releasegroup=album or "", limit=5)
        rgs = res.get("release-group-list", [])
        for rg in rgs:
            d = rg.get("first-release-date")
            if d and re.match(r"^\d{4}-\d{2}-\d{2}", d):  # validate YYYY-MM-DD format
                m = re.match(r"^(\d{4})", d)
                if m:
                    year = int(m.group(1))
                    result_url = f"https://musicbrainz.org/release-group/{rg.get("id")}"
                    result_artist = rg.get("artist-credit-phrase", "")
                    result_album = rg.get("title", "")
                    # Return immediately on first valid date found
                    return (str(year), result_url, result_artist or "", result_album or "")
    except Exception:
        pass

    return None


def check_similarity(db_artist: str, db_album: str, api_artist: str, api_album: str, threshold: float = 0.7) -> bool:
    """Check if API results match DB values using Levenshtein distance. Returns True if similar enough."""
    artist_sim = ratio(db_artist, api_artist) if db_artist and api_artist else 0.0
    album_sim = ratio(db_album, api_album) if db_album and api_album else 0.0
    avg_sim = (artist_sim + album_sim) / 2.0
    return avg_sim >= threshold


def find_mp3_files(conn: sqlite3.Connection, album_id: str, media_root: Optional[str]) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT path FROM media_file WHERE album_id = ?", (album_id,))
    rows = cur.fetchall()
    paths = [r[0] for r in rows if r[0]]
    files = []
    for p in paths:
        if media_root and not os.path.isabs(p):
            p2 = os.path.join(media_root, p)
        else:
            p2 = p
        if p2.lower().endswith('.mp3'):
            files.append(p2)
    return files


def set_id3_year(path: str, year: str) -> bool:
    if not os.path.exists(path):
        return False
    # quick check for ID3v1 tag (last 128 bytes start with 'TAG')
    has_v1 = False
    try:
        with open(path, 'rb') as fh:
            fh.seek(-128, os.SEEK_END)
            tag = fh.read(128)
            if len(tag) == 128 and tag[:3] == b'TAG':
                has_v1 = True
    except Exception:
        pass

    # Try to use ID3v2 if present
    try:
        id3 = ID3(path)
        ver = getattr(id3, 'version', None)
        # prefer v2.4 -> TDRC, v2.3 -> TYER
        if ver and len(ver) >= 2 and ver[1] == 4:
            id3.delall('TDRC')
            id3.add(TDRC(encoding=3, text=year))
        else:
            # default to v2.3 TYER for v2.3 and other unknown v2 versions
            from mutagen.id3 import TYER
            id3.delall('TYER')
            id3.add(TYER(encoding=3, text=year))
        id3.save(path)
        return True
    except ID3NoHeaderError:
        # No ID3v2 header found
        if has_v1:
            # update ID3v1 year field at offset 93..96 of the 128-byte tag
            # try:
            #     with open(path, 'r+b') as fh:
            #         fh.seek(-128, os.SEEK_END)
            #         tag = bytearray(fh.read(128))
            #         if tag[:3] == b'TAG':
            #             yb = year.encode('ascii')[:4]
            #             # pad/truncate to 4 bytes
            #             yb = yb.ljust(4, b' ')
            #             tag[93:97] = yb
            #             fh.seek(-128, os.SEEK_END)
            #             fh.write(tag)
            #             return True
            # except Exception:
            #     return False

            # this is too dodgy for my liking, just skip instead
            print(f"ID3v1 found, skipping {path}")
            return False
        # no existing tags at all: create ID3v2.3 tag by default
        try:
            id3 = ID3()
            from mutagen.id3 import TYER
            id3.add(TYER(encoding=3, text=year))
            # save defaulting to v2.3 for compatibility
            id3.save(path, v2_version=(2, 3, 0))
            return True
        except Exception:
            return False
    except Exception:
        return False


def prompt_yes_no_all(prompt: str) -> str:
    # returns 'y', 'n', 'a' (all), 's' (skip all), 'q' (quit)
    while True:
        resp = input(prompt + " [y/N/a/s/q]: ").strip().lower()
        if resp == 'y' or resp == 'n' or resp == 'a' or resp == 's' or resp == 'q':
            return resp
        if resp == '':
            return 'n'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('db', help='Path to sqlite database')
    parser.add_argument('--user-agent', help='User-Agent for web APIs', default='albums-missing-year-script/0.1 (example@example.com)')
    parser.add_argument('--discogs-token', help='Discogs personal access token (optional)', default=None)
    parser.add_argument('--media-root', help='Prefix to join with media_file.path when path is relative', default=None)
    parser.add_argument('--state-file', help='JSON file to track processed albums', default='update_years_state.json')
    parser.add_argument('--force', help='Reprocess albums even if present in state file', action='store_true')
    parser.add_argument('--similarity-threshold', help='Minimum Levenshtein similarity (0-1) for artist/album match', type=float, default=0.7)
    parser.add_argument('--with-plays-or-ratings', help='Only process albums with tracks that have plays or ratings', action='store_true')
    parser.add_argument('--dry-run', help="Don't write tags; just print what would be done", action='store_true')
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print('Database not found:', args.db)
        sys.exit(1)

    musicbrainzngs.set_useragent('albums-missing-year-script', '0.1', args.user_agent)

    conn = sqlite3.connect(args.db)

    # load state of processed albums
    def load_state(path: str):
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception:
            return {}

    def save_state(path: str, data: dict):
        tmp = path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def mark_processed(state: dict, album_id: str, name: str, artist: str, year: str, decision: str, dry_run: bool):
        state[album_id] = {
            'name': name,
            'artist': artist,
            'year': year,
            'decision': decision,
            'dry_run': bool(dry_run),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }
        save_state(args.state_file, state)

    state = load_state(args.state_file)

    albums = find_problem_albums(conn, args.with_plays_or_ratings)
    if not albums:
        print('No albums with missing/non-YYYY dates found.')
        return

    apply_all = None  # None means ask; True means apply all; False means skip all

    for alb in albums:
        album_id = alb['id']
        name = alb['name']
        artist = alb['album_artist']
        cur_date = alb['date']
        # skip if processed already unless --force
        if not args.force and album_id in state:
            print(f"Skipping already-processed album: {name} ({album_id}) -> {state[album_id].get('decision')}")
            continue
        print('\nAlbum:', name)
        print('Artist:', artist)
        print('Current date field:', repr(cur_date))
        year = None
        source_url = None
        api_artist = None
        api_album = None
        # Try Discogs first
        try:
            result = lookup_year_discogs(artist, name, args.discogs_token, args.user_agent)
            if result:
                year, source_url, api_artist, api_album = result
        except Exception:
            year = None
        if not year:
            result = lookup_year_mb(artist, name)
            if result:
                year, source_url, api_artist, api_album = result
        if not year:
            print('Could not find a reliable year via Discogs/MusicBrainz.')
            continue
        # Check similarity between DB values and API results
        if not check_similarity(artist, name, api_artist, api_album, args.similarity_threshold):
            print(f'Skipping: Low similarity match (API: {api_artist} - {api_album})')
            mark_processed(state, album_id, name, artist, year, 'skipped_mismatch', args.dry_run)
            continue
        print('Discovered year:', year)
        if source_url:
            print('Source URL:', source_url)
        mp3s = find_mp3_files(conn, album_id, args.media_root)
        print('MP3 files found for album:', len(mp3s))

        if apply_all is True:
            do_apply = True
        elif apply_all is False:
            do_apply = False
        else:
            resp = prompt_yes_no_all(f"Update ID3 year to {year} for this album?")
            if resp == 'y':
                do_apply = True
            elif resp == 'n':
                do_apply = False
            elif resp == 'a':
                do_apply = True
                apply_all = True
            elif resp == 's':
                do_apply = False
                apply_all = False
            elif resp == 'q':
                print('Quitting.')
                break

        if do_apply:
            # record decision as 'accepted'
            mark_processed(state, album_id, name, artist, year, 'accepted', args.dry_run)
            if args.dry_run:
                for p in mp3s:
                    print('Would update:', p)
                continue
            for p in mp3s:
                ok = set_id3_year(p, year)
                print(('Updated' if ok else 'Failed to update'), p)
        else:
            # record decision as 'rejected'
            mark_processed(state, album_id, name, artist, year, 'rejected', args.dry_run)

    conn.close()


if __name__ == '__main__':
    main()
