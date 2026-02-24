#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "mutagen",
#     "musicbrainzngs",
#     "requests",
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
from typing import Optional, List

import requests
import musicbrainzngs
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TDRC, ID3NoHeaderError


YEAR_RE = re.compile(r"^(\d{4})$")


def find_problem_albums(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # date empty or not a 4-digit year
    cur.execute(
        """
        SELECT id, name, album_artist, date
        FROM album
        WHERE date IS NULL OR date = '' OR date NOT GLOB '[0-9][0-9][0-9][0-9]'
        ORDER BY name
        """
    )
    return cur.fetchall()


def lookup_year_discogs(artist: str, album: str, token: Optional[str], user_agent: str) -> Optional[str]:
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
    for res in results:
        y = res.get("year")
        if isinstance(y, int) and 1000 <= y <= 9999:
            years.append(y)
    if years:
        return str(min(years))
    return None


def lookup_year_mb(artist: str, album: str) -> Optional[str]:
    try:
        res = musicbrainzngs.search_release_groups(artist=artist or "", releasegroup=album or "", limit=5)
    except Exception:
        return None
    rgs = res.get("release-group-list", [])
    years = []
    for rg in rgs:
        d = rg.get("first-release-date")
        if d:
            m = re.match(r"^(\d{4})", d)
            if m:
                years.append(int(m.group(1)))
    if years:
        return str(min(years))

    try:
        r = musicbrainzngs.search_releases(artist=artist or "", release=album or "", limit=5)
    except Exception:
        return None
    rels = r.get("release-list", [])
    for rel in rels:
        d = rel.get("date")
        if d:
            m = re.match(r"^(\d{4})", d)
            if m:
                years.append(int(m.group(1)))
    if years:
        return str(min(years))
    return None


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
    parser.add_argument('--dry-run', help="Don't write tags; just print what would be done", action='store_true')
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print('Database not found:', args.db)
        sys.exit(1)

    musicbrainzngs.set_useragent('albums-missing-year-script', '0.1', args.user_agent)

    conn = sqlite3.connect(args.db)

    albums = find_problem_albums(conn)
    if not albums:
        print('No albums with missing/non-YYYY dates found.')
        return

    apply_all = None  # None means ask; True means apply all; False means skip all

    for alb in albums:
        album_id = alb['id']
        name = alb['name']
        artist = alb['album_artist']
        cur_date = alb['date']
        print('\nAlbum:', name)
        print('Artist:', artist)
        print('Current date field:', repr(cur_date))
        year = None
        # Try Discogs first
        try:
            year = lookup_year_discogs(artist, name, args.discogs_token, args.user_agent)
        except Exception:
            year = None
        if not year:
            year = lookup_year_mb(artist, name)
        if not year:
            print('Could not find a reliable year via MusicBrainz.')
            continue
        print('Discovered year:', year)
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
            if args.dry_run:
                for p in mp3s:
                    print('Would update:', p)
                continue
            for p in mp3s:
                ok = set_id3_year(p, year)
                print(('Updated' if ok else 'Failed to update'), p)

    conn.close()


if __name__ == '__main__':
    main()
