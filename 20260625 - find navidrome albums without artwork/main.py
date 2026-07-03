#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///
"""GUI to find Navidrome albums lacking cover art and download artwork for them."""

import argparse
import os
import sqlite3
import time
import tkinter as tk
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import ceil
from tkinter import messagebox, ttk
from urllib.parse import quote_plus

import requests


@dataclass
class AlbumInfo:
    folder_id: str
    folder_path: str
    folder_name: str
    artist: str
    album: str
    rated_songs: int
    played_songs: int
    total_rating: int
    total_plays: int


def query_albums_without_art(db_path: str) -> list[AlbumInfo]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            f.id AS folder_id,
            f.path AS folder_path,
            f.name AS folder_name,
            COALESCE(
                NULLIF(MAX(mf.album_artist), ''),
                NULLIF(MAX(mf.artist), ''),
                'Unknown Artist'
            ) AS artist,
            COALESCE(NULLIF(MAX(mf.album), ''), f.name) AS album,
            COUNT(CASE WHEN a.rating > 0 THEN 1 END) AS rated_songs,
            COUNT(CASE WHEN a.play_count > 0 THEN 1 END) AS played_songs,
            COALESCE(SUM(CASE WHEN a.rating > 0 THEN a.rating ELSE 0 END), 0) AS total_rating,
            COALESCE(SUM(CASE WHEN a.play_count > 0 THEN a.play_count ELSE 0 END), 0) AS total_plays
        FROM folder f
        JOIN media_file mf ON mf.folder_id = f.id
        LEFT JOIN annotation a ON a.item_id = mf.id AND a.item_type = 'media_file'
        WHERE f.image_files = '[]'
          AND f.path != '.'
          AND f.path != ''
          AND f.num_audio_files > 0
        GROUP BY f.id
        ORDER BY rated_songs DESC, played_songs DESC, total_rating DESC, total_plays DESC
    """)

    albums = [
        AlbumInfo(
            folder_id=row["folder_id"],
            folder_path=row["folder_path"],
            folder_name=row["folder_name"],
            artist=row["artist"],
            album=row["album"],
            rated_songs=row["rated_songs"],
            played_songs=row["played_songs"],
            total_rating=row["total_rating"],
            total_plays=row["total_plays"],
        )
        for row in cursor.fetchall()
    ]

    conn.close()
    return albums


def make_search_url(artist: str, album: str) -> str:
    query = quote_plus(f"{artist} - {album}")
    return f"https://www.google.com/search?tbm=isch&q={query}"


def download_image(url: str, dest_path: str) -> None:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (album-art-downloader)"},
        timeout=30,
        stream=True,
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"URL did not return an image (content-type: {content_type})")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


def trigger_rescan(album_dir: str) -> None:
    """Create and remove a temporary .txt file to trigger Navidrome's filesystem watcher."""
    tmp_file = os.path.join(album_dir, f"{uuid.uuid4().hex}.txt")
    try:
        with open(tmp_file, "w") as f:
            f.write("rescan trigger")
        time.sleep(3)
    finally:
        try:
            os.remove(tmp_file)
        except OSError:
            pass


class AlbumArtApp:
    PAGE_SIZE = 100
    SORT_OPTIONS = {
        "Rating": lambda album: (
            album.rated_songs,
            album.total_rating,
            album.played_songs,
            album.total_plays,
            album.artist.lower(),
            album.album.lower(),
        ),
        "Playcount": lambda album: (
            album.total_plays,
            album.played_songs,
            album.rated_songs,
            album.total_rating,
            album.artist.lower(),
            album.album.lower(),
        ),
        "Artist": lambda album: (
            album.artist.lower(),
            album.album.lower(),
            -album.rated_songs,
            -album.played_songs,
        ),
        "Album": lambda album: (
            album.album.lower(),
            album.artist.lower(),
            -album.rated_songs,
            -album.played_songs,
        ),
    }

    def __init__(self, albums: list[AlbumInfo], music_dir: str):
        self.albums = albums
        self.music_dir = music_dir
        self.current_page = 0
        self.total_pages = max(1, ceil(len(self.albums) / self.PAGE_SIZE))

        self.root = tk.Tk()
        self.root.title(f"Album Art Finder ({len(albums)} albums without art)")
        self.root.geometry("900x700")

        self.album_url_vars = [tk.StringVar(master=self.root) for _ in self.albums]
        self.completed_indices: set[int] = set()
        self.url_entries: list[tuple[int, AlbumInfo, tk.Entry]] = []
        self.sort_var = tk.StringVar(master=self.root, value="Rating")
        self.sorted_album_indices = list(range(len(self.albums)))

        self._build_ui()
        self._apply_sort(reset_page=False)

    def _build_ui(self):
        # Top bar with pagination controls
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.prev_button = ttk.Button(top_frame, text="\u2190 Prev", command=self._prev_page)
        self.prev_button.pack(side=tk.LEFT)

        self.page_label = ttk.Label(top_frame, text="")
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.next_button = ttk.Button(top_frame, text="Next \u2192", command=self._next_page)
        self.next_button.pack(side=tk.LEFT)

        ttk.Label(top_frame, text="Sort by:").pack(side=tk.LEFT, padx=(20, 5))
        self.sort_combo = ttk.Combobox(
            top_frame,
            textvariable=self.sort_var,
            values=list(self.SORT_OPTIONS),
            state="readonly",
            width=12,
        )
        self.sort_combo.pack(side=tk.LEFT)
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_sort_changed)

        # Main frame with scrollbar
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Canvas + scrollbar for scrollable content
        self.canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Bind mousewheel scrolling
        def _on_mousewheel(event):
            self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

        def _on_mousewheel_mac(event):
            self.canvas.yview_scroll(-1 * event.delta, "units")

        self.canvas.bind_all("<MouseWheel>", _on_mousewheel_mac)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

        # Bottom bar with Go button
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=10, pady=10)

        self.status_label = ttk.Label(bottom_frame, text="", wraplength=700)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        go_button = ttk.Button(
            bottom_frame, text="Go!", command=self._on_go, padding=(20, 5)
        )
        go_button.pack(side=tk.RIGHT)

    def _apply_sort(self, reset_page: bool = True):
        sort_label = self.sort_var.get()
        key_func = self.SORT_OPTIONS.get(sort_label, self.SORT_OPTIONS["Rating"])
        reverse = sort_label in {"Rating", "Playcount"}
        self.sorted_album_indices = sorted(
            range(len(self.albums)),
            key=lambda idx: key_func(self.albums[idx]),
            reverse=reverse,
        )

        if reset_page:
            self.current_page = 0

        self._render_page()

    def _on_sort_changed(self, _event=None):
        self._apply_sort(reset_page=True)

    def _render_page(self):
        for child in self.scrollable_frame.winfo_children():
            child.destroy()

        self.url_entries.clear()
        start = self.current_page * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, len(self.sorted_album_indices))

        for display_idx in range(start, end):
            album_idx = self.sorted_album_indices[display_idx]
            self._add_album_row(
                album_idx,
                display_idx,
                self.albums[album_idx],
                self.album_url_vars[album_idx],
            )

        self.page_label.config(
            text=(
                f"Page {self.current_page + 1}/{self.total_pages} "
                f"(showing {start + 1}-{end} of {len(self.sorted_album_indices)})"
            )
        )
        self.prev_button.config(state="normal" if self.current_page > 0 else "disabled")
        self.next_button.config(
            state="normal" if self.current_page < self.total_pages - 1 else "disabled"
        )
        self.canvas.yview_moveto(0)

    def _prev_page(self):
        if self.current_page <= 0:
            return
        self.current_page -= 1
        self._render_page()

    def _next_page(self):
        if self.current_page >= self.total_pages - 1:
            return
        self.current_page += 1
        self._render_page()

    def _add_album_row(
        self,
        album_idx: int,
        display_idx: int,
        album: AlbumInfo,
        url_var: tk.StringVar,
    ):
        frame = ttk.Frame(self.scrollable_frame)
        frame.pack(fill=tk.X, pady=2, padx=5)

        # Row 1: Album info + search link
        info_frame = ttk.Frame(frame)
        info_frame.pack(fill=tk.X)

        label_text = (
            f"[{display_idx + 1}] {album.artist} - {album.album}  "
            f"({album.rated_songs} rated, {album.played_songs} played"
        )
        if album.total_rating > 0:
            label_text += f", rating sum: {album.total_rating}"
        if album.total_plays > 0:
            label_text += f", play sum: {album.total_plays}"
        label_text += ")"

        info_label = ttk.Label(info_frame, text=label_text, font=("TkDefaultFont", 11, "bold"))
        info_label.pack(side=tk.LEFT)

        search_url = make_search_url(album.artist, album.album)
        link_label = ttk.Label(
            info_frame, text="\U0001f50d Search", foreground="blue", cursor="hand2"
        )
        link_label.pack(side=tk.LEFT, padx=(10, 0))
        link_label.bind("<Button-1>", lambda e, url=search_url: webbrowser.open(url))

        # Row 2: URL entry
        entry_frame = ttk.Frame(frame)
        entry_frame.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(entry_frame, text="Image URL:").pack(side=tk.LEFT)
        url_entry = ttk.Entry(entry_frame, textvariable=url_var)
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        if album_idx in self.completed_indices:
            url_entry.config(state="readonly")
        else:
            url_entry.config(state="normal")

        self.url_entries.append((album_idx, album, url_entry))

        # Separator
        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(5, 0))

    def _on_go(self):
        results = []
        to_rescan: list[str] = []

        for idx, album in enumerate(self.albums):
            if idx in self.completed_indices:
                continue

            url = self.album_url_vars[idx].get().strip()
            if not url:
                continue

            album_dir = os.path.join(self.music_dir, album.folder_path, album.folder_name)
            dest_path = os.path.join(album_dir, "folder.jpg")

            try:
                download_image(url, dest_path)
                results.append(f"\u2713 {album.artist} - {album.album}")
                to_rescan.append(album_dir)
                self.completed_indices.add(idx)
                self.album_url_vars[idx].set("\u2713 Done")
            except Exception as e:
                results.append(f"\u2717 {album.artist} - {album.album}: {e}")

        self._render_page()

        # Trigger rescans in parallel
        if to_rescan:
            ThreadPoolExecutor(max_workers=len(to_rescan)).map(trigger_rescan, to_rescan)

        if results:
            self.status_label.config(text="\n".join(results))
            messagebox.showinfo("Done", f"Processed {len(results)} album(s).")
        else:
            messagebox.showwarning("No URLs", "No image URLs were entered.")

    def run(self):
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find Navidrome albums lacking cover art and download artwork.",
    )
    parser.add_argument(
        "--db", "-d",
        default="./navidrome.db",
        help="Path to navidrome.db (default: ./navidrome.db)",
    )
    parser.add_argument(
        "--music-dir", "-m",
        required=True,
        help="Root path of the music library on this machine",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        raise SystemExit(f"Error: Database not found: {args.db}")

    albums = query_albums_without_art(args.db)
    if not albums:
        print("No albums found without cover art!")
        return

    app = AlbumArtApp(albums, args.music_dir)
    app.run()


if __name__ == "__main__":
    main()
