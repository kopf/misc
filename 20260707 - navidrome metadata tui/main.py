#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "click",
#   "textual",
# ]
# ///

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import click
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RadioButton,
    RadioSet,
    Static,
)

SearchScope = Literal["all", "artist", "album", "track"]
TransferMode = Literal["playcount", "rating", "playcount_and_rating"]
SortField = Literal["id", "artist", "album", "title", "rating", "play_count"]

DEFAULT_LIMIT = 500
MIN_WIDTH = 120
MIN_HEIGHT = 36
TRACK_ITEM_TYPE_CANDIDATES: tuple[str, ...] = ("track", "media_file", "song")
DEFAULT_PAGE_SIZE = 200

COLUMN_DEFS: list[tuple[str, str, int]] = [
    ("id", "ID", 16),
    ("artist", "Artist", 24),
    ("album", "Album", 30),
    ("title", "Track Title", 34),
    ("rating", "Rating", 8),
    ("play_count", "Playcount", 10),
]


def truncate_for_column(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None


def dt_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S")


@dataclass(slots=True)
class TrackRow:
    id: str
    artist: str
    album: str
    title: str
    rating: int
    play_count: int
    play_date: str | None
    rated_at: str | None
    disc_number: int
    track_number: int
    duration: float
    year: int
    path: str
    album_id: str


class NavidromeRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.track_item_type = self._detect_track_item_type()

    def _detect_track_item_type(self) -> str:
        placeholders = ",".join("?" for _ in TRACK_ITEM_TYPE_CANDIDATES)
        row = self._conn.execute(
            f"""
            SELECT item_type, COUNT(*) AS n
            FROM annotation
            WHERE item_type IN ({placeholders})
            GROUP BY item_type
            ORDER BY n DESC
            LIMIT 1
            """,
            TRACK_ITEM_TYPE_CANDIDATES,
        ).fetchone()
        if not row:
            return "track"
        return str(row["item_type"])

    def close(self) -> None:
        self._conn.close()

    def validate_schema(self) -> None:
        required_tables = {"media_file", "annotation", "album", "user"}
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        found = {row["name"] for row in rows}
        missing = required_tables - found
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise RuntimeError(f"Database missing required tables: {missing_text}")

    def list_users(self) -> list[tuple[str, str]]:
        rows = self._conn.execute(
            """
            SELECT id, user_name
            FROM user
            ORDER BY user_name COLLATE NOCASE
            """
        ).fetchall()
        return [(row["id"], row["user_name"]) for row in rows]

    def resolve_user(self, user_input: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            """
            SELECT id, user_name
            FROM user
            WHERE id = ? OR user_name = ?
            LIMIT 1
            """,
            (user_input, user_input),
        ).fetchone()
        if not row:
            return None
        return (row["id"], row["user_name"])

    def search_tracks(
        self,
        user_id: str,
        term: str,
        scope: SearchScope,
        sort_field: SortField = "artist",
        sort_desc: bool = False,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[list[TrackRow], int]:
        terms = [part for part in term.strip().split() if part]
        if not terms:
            terms = [""]

        scope_fields: list[str]
        if scope == "artist":
            scope_fields = [
                "mf.artist",
                "mf.order_artist_name",
                "mf.sort_artist_name",
            ]
        elif scope == "album":
            scope_fields = [
                "mf.album",
                "mf.order_album_name",
                "mf.sort_album_name",
            ]
        elif scope == "track":
            scope_fields = [
                "mf.title",
                "mf.order_title",
                "mf.sort_title",
            ]
        else:
            scope_fields = [
                "mf.artist",
                "mf.album",
                "mf.title",
                "mf.full_text",
            ]

        search_clauses: list[str] = []
        params: list[object] = []
        for search_term in terms:
            like = f"%{search_term}%"
            search_clauses.append(
                "(" + " OR ".join(f"{field} LIKE ? COLLATE NOCASE" for field in scope_fields) + ")"
            )
            params.extend([like] * len(scope_fields))

        scope_sql = " AND ".join(search_clauses)

        rating_expr = "COALESCE(NULLIF(ann.rating, 0), CAST(ROUND(mf.average_rating) AS INTEGER), 0)"

        order_map: dict[SortField, str] = {
            "id": "mf.id",
            "artist": "mf.order_artist_name",
            "album": "mf.order_album_name",
            "title": "COALESCE(NULLIF(mf.sort_title, ''), mf.order_title)",
            "rating": rating_expr,
            "play_count": "COALESCE(ann.play_count, 0)",
        }
        resolved_sort = order_map.get(sort_field, order_map["artist"])
        direction = "DESC" if sort_desc else "ASC"

        item_type_placeholders = ",".join("?" for _ in TRACK_ITEM_TYPE_CANDIDATES)
        ann_params: list[object] = [user_id, *TRACK_ITEM_TYPE_CANDIDATES]
        count_row = self._conn.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM media_file mf
            WHERE mf.missing = FALSE
              AND {scope_sql}
            """,
            params,
        ).fetchone()
        total = int(count_row["total"] if count_row else 0)

        query_params = [*ann_params, *params, limit, offset]

        rows = self._conn.execute(
            f"""
            WITH ranked_annotation AS (
                SELECT
                    item_id,
                    play_count,
                    rating,
                    play_date,
                    rated_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY item_id
                        ORDER BY CASE item_type
                            WHEN 'track' THEN 0
                            WHEN 'media_file' THEN 1
                            WHEN 'song' THEN 2
                            ELSE 100
                        END
                    ) AS rn
                FROM annotation
                WHERE user_id = ?
                  AND item_type IN ({item_type_placeholders})
            )
            SELECT
                mf.id,
                mf.artist,
                mf.album,
                mf.title,
                {rating_expr} AS rating,
                COALESCE(ann.play_count, 0) AS play_count,
                ann.play_date,
                ann.rated_at,
                mf.disc_number,
                mf.track_number,
                mf.duration,
                mf.year,
                mf.path,
                mf.album_id
            FROM media_file mf
                        LEFT JOIN ranked_annotation ann
                                ON ann.item_id = mf.id
                             AND ann.rn = 1
            WHERE mf.missing = FALSE
              AND {scope_sql}
            ORDER BY {resolved_sort} {direction},
                     mf.order_artist_name,
                     mf.order_album_name,
                     mf.disc_number,
                     mf.track_number
            LIMIT ?
            OFFSET ?
            """,
            query_params,
        ).fetchall()

        return ([
            TrackRow(
                id=row["id"],
                artist=row["artist"] or "",
                album=row["album"] or "",
                title=row["title"] or "",
                rating=int(row["rating"] or 0),
                play_count=int(row["play_count"] or 0),
                play_date=row["play_date"],
                rated_at=row["rated_at"],
                disc_number=int(row["disc_number"] or 0),
                track_number=int(row["track_number"] or 0),
                duration=float(row["duration"] or 0),
                year=int(row["year"] or 0),
                path=row["path"] or "",
                album_id=row["album_id"] or "",
            )
            for row in rows
        ], total)

    def get_track(self, user_id: str, track_id: str) -> TrackRow | None:
        item_type_placeholders = ",".join("?" for _ in TRACK_ITEM_TYPE_CANDIDATES)
        row = self._conn.execute(
            f"""
            WITH ranked_annotation AS (
                SELECT
                    item_id,
                    play_count,
                    rating,
                    play_date,
                    rated_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY item_id
                        ORDER BY CASE item_type
                            WHEN 'track' THEN 0
                            WHEN 'media_file' THEN 1
                            WHEN 'song' THEN 2
                            ELSE 100
                        END
                    ) AS rn
                FROM annotation
                WHERE user_id = ?
                  AND item_type IN ({item_type_placeholders})
            )
            SELECT
                mf.id,
                mf.artist,
                mf.album,
                mf.title,
                COALESCE(ann.rating, 0) AS rating,
                COALESCE(ann.play_count, 0) AS play_count,
                ann.play_date,
                ann.rated_at,
                mf.disc_number,
                mf.track_number,
                mf.duration,
                mf.year,
                mf.path,
                mf.album_id
            FROM media_file mf
            LEFT JOIN ranked_annotation ann
                ON ann.item_id = mf.id
               AND ann.rn = 1
            WHERE mf.id = ?
            LIMIT 1
            """,
            (user_id, *TRACK_ITEM_TYPE_CANDIDATES, track_id),
        ).fetchone()
        if not row:
            return None

        return TrackRow(
            id=row["id"],
            artist=row["artist"] or "",
            album=row["album"] or "",
            title=row["title"] or "",
            rating=int(row["rating"] or 0),
            play_count=int(row["play_count"] or 0),
            play_date=row["play_date"],
            rated_at=row["rated_at"],
            disc_number=int(row["disc_number"] or 0),
            track_number=int(row["track_number"] or 0),
            duration=float(row["duration"] or 0),
            year=int(row["year"] or 0),
            path=row["path"] or "",
            album_id=row["album_id"] or "",
        )

    def transfer_metadata(
        self,
        user_id: str,
        source_track_id: str,
        target_track_id: str,
        mode: TransferMode,
    ) -> None:
        if source_track_id == target_track_id:
            raise ValueError("Source and target tracks must be different")

        source_track = self._conn.execute(
            "SELECT id, album_id FROM media_file WHERE id = ?",
            (source_track_id,),
        ).fetchone()
        target_track = self._conn.execute(
            "SELECT id, album_id FROM media_file WHERE id = ?",
            (target_track_id,),
        ).fetchone()
        if not source_track or not target_track:
            raise ValueError("Source or target track does not exist")

        source_ann = self._get_track_annotation(user_id, source_track_id)
        target_ann = self._get_track_annotation(user_id, target_track_id)

        source_item_type = str(source_ann["item_type"]) if source_ann else self.track_item_type
        target_item_type = str(target_ann["item_type"]) if target_ann else self.track_item_type

        source_play_count = int(source_ann["play_count"] if source_ann else 0)
        source_rating = int(source_ann["rating"] if source_ann else 0)
        source_play_date = parse_dt(source_ann["play_date"] if source_ann else None)
        source_rated_at = source_ann["rated_at"] if source_ann else None

        target_play_count = int(target_ann["play_count"] if target_ann else 0)
        target_rating = int(target_ann["rating"] if target_ann else 0)
        target_play_date = parse_dt(target_ann["play_date"] if target_ann else None)
        target_rated_at = target_ann["rated_at"] if target_ann else None

        new_source_play_count = source_play_count
        new_target_play_count = target_play_count
        new_source_rating = source_rating
        new_target_rating = target_rating
        new_source_play_date = source_play_date
        new_target_play_date = target_play_date
        new_source_rated_at = source_rated_at
        new_target_rated_at = target_rated_at

        if mode in ("playcount", "playcount_and_rating"):
            new_target_play_count = target_play_count + source_play_count
            new_source_play_count = 0
            if source_play_date is not None:
                if target_play_date is None or target_play_date <= source_play_date:
                    new_target_play_date = source_play_date
            new_source_play_date = None

        if mode in ("rating", "playcount_and_rating"):
            new_target_rating = source_rating
            new_source_rating = 0
            new_target_rated_at = source_rated_at
            new_source_rated_at = None

        with self._conn:
            self._upsert_track_annotation(
                user_id=user_id,
                track_id=source_track_id,
                item_type=source_item_type,
                play_count=new_source_play_count,
                rating=new_source_rating,
                play_date=dt_to_db(new_source_play_date),
                rated_at=new_source_rated_at,
            )
            self._upsert_track_annotation(
                user_id=user_id,
                track_id=target_track_id,
                item_type=target_item_type,
                play_count=new_target_play_count,
                rating=new_target_rating,
                play_date=dt_to_db(new_target_play_date),
                rated_at=new_target_rated_at,
            )

            if mode in ("playcount", "playcount_and_rating"):
                source_album_id = source_track["album_id"] or ""
                target_album_id = target_track["album_id"] or ""
                affected_album_ids = {aid for aid in (source_album_id, target_album_id) if aid}
                for album_id in affected_album_ids:
                    self._recompute_album_annotation(user_id=user_id, album_id=album_id)

    def _get_track_annotation(self, user_id: str, track_id: str) -> sqlite3.Row | None:
        placeholders = ",".join("?" for _ in TRACK_ITEM_TYPE_CANDIDATES)
        params: list[object] = [user_id, track_id, *TRACK_ITEM_TYPE_CANDIDATES]
        return self._conn.execute(
            f"""
            SELECT item_type, play_count, rating, play_date, rated_at
            FROM annotation
            WHERE user_id = ?
              AND item_id = ?
              AND item_type IN ({placeholders})
            ORDER BY CASE item_type
                WHEN 'track' THEN 0
                WHEN 'media_file' THEN 1
                WHEN 'song' THEN 2
                ELSE 100
            END
            LIMIT 1
            """,
            params,
        ).fetchone()

    def _upsert_track_annotation(
        self,
        user_id: str,
        track_id: str,
        item_type: str,
        play_count: int,
        rating: int,
        play_date: str | None,
        rated_at: str | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO annotation (user_id, item_id, item_type, play_count, rating, play_date, rated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, item_id, item_type) DO UPDATE SET
                play_count = excluded.play_count,
                rating = excluded.rating,
                play_date = excluded.play_date,
                rated_at = excluded.rated_at
            """,
            (user_id, track_id, item_type, play_count, rating, play_date, rated_at),
        )

    def _recompute_album_annotation(self, user_id: str, album_id: str) -> None:
        item_type_placeholders = ",".join("?" for _ in TRACK_ITEM_TYPE_CANDIDATES)
        aggregate = self._conn.execute(
            f"""
            WITH ranked_annotation AS (
                SELECT
                    item_id,
                    play_count,
                    play_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY item_id
                        ORDER BY CASE item_type
                            WHEN 'track' THEN 0
                            WHEN 'media_file' THEN 1
                            WHEN 'song' THEN 2
                            ELSE 100
                        END
                    ) AS rn
                FROM annotation
                WHERE user_id = ?
                  AND item_type IN ({item_type_placeholders})
            )
            SELECT
                COALESCE(SUM(COALESCE(ann.play_count, 0)), 0) AS album_play_count,
                MAX(ann.play_date) AS album_last_played
            FROM media_file mf
            LEFT JOIN ranked_annotation ann
                ON ann.item_id = mf.id
               AND ann.rn = 1
            WHERE mf.album_id = ?
              AND mf.missing = FALSE
            """,
            (user_id, *TRACK_ITEM_TYPE_CANDIDATES, album_id),
        ).fetchone()

        play_count = int(aggregate["album_play_count"] or 0)
        last_played = aggregate["album_last_played"]

        self._conn.execute(
            """
            INSERT INTO annotation (user_id, item_id, item_type, play_count, play_date)
            VALUES (?, ?, 'album', ?, ?)
            ON CONFLICT(user_id, item_id, item_type) DO UPDATE SET
                play_count = excluded.play_count,
                play_date = excluded.play_date
            """,
            (user_id, album_id, play_count, last_played),
        )


class UserSelectScreen(ModalScreen[str | None]):
    CSS = """
    UserSelectScreen {
        align: center middle;
    }

    #user-modal {
        width: 70%;
        height: 60%;
        border: tall $primary;
        background: $surface;
        padding: 1 2;
    }

    #user-list {
        height: 1fr;
        margin-top: 1;
        margin-bottom: 1;
    }

    #user-actions {
        height: auto;
        align-horizontal: right;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Confirm"),
    ]

    def __init__(self, users: list[tuple[str, str]]) -> None:
        super().__init__()
        self.users = users
        self.user_ids = [user_id for user_id, _ in users]
        self.item_to_user_id: dict[str, str] = {
            f"user-{index}": user_id for index, (user_id, _) in enumerate(users)
        }
        self.selected_user_id: str | None = users[0][0] if users else None

    def compose(self) -> ComposeResult:
        with Container(id="user-modal"):
            yield Static("Select Navidrome User", id="user-title")
            with ListView(id="user-list"):
                for index, (user_id, username) in enumerate(self.users):
                    yield ListItem(
                        Label(f"{username} ({user_id})"),
                        id=f"user-{index}",
                    )
            with Container(id="user-actions"):
                yield Button("Cancel", id="cancel")
                yield Button("Use Selected User", id="confirm", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#user-list", ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if not item_id:
            self.selected_user_id = None
            return
        self.selected_user_id = self.item_to_user_id.get(item_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(self.selected_user_id)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        self.dismiss(self.selected_user_id)


class TransferActionScreen(ModalScreen[TransferMode | None]):
    CSS = """
    TransferActionScreen {
        align: center middle;
    }

    #action-modal {
        width: 48;
        height: auto;
        border: tall $primary;
        background: $surface;
        padding: 1 2;
    }

    #action-buttons {
        layout: vertical;
        height: auto;
        margin-top: 1;
    }

    #action-buttons Button {
        width: 100%;
        margin-bottom: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="action-modal"):
            yield Static("Track Actions")
            with Container(id="action-buttons"):
                yield Button("transfer playcount", id="playcount")
                yield Button("transfer rating", id="rating")
                yield Button("transfer playcount & rating", id="playcount_and_rating")
                yield Button("cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id in {"playcount", "rating", "playcount_and_rating"}:
            self.dismiss(button_id)
            return
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TargetPickerScreen(ModalScreen[str | None]):
    CSS = """
    TargetPickerScreen {
        align: center middle;
    }

    #target-modal {
        width: 92%;
        height: 88%;
        border: tall $accent;
        background: $surface;
        padding: 1;
        layout: vertical;
    }

    #target-search {
        height: auto;
        margin-top: 1;
    }

    #target-scopes {
        layout: horizontal;
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }

    #target-scopes RadioButton {
        margin-right: 2;
    }

    #target-table {
        height: 1fr;
    }

    #target-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "transfer", "Transfer"),
    ]

    def __init__(
        self,
        repo: NavidromeRepository,
        user_id: str,
        source_track: TrackRow,
    ) -> None:
        super().__init__()
        self.repo = repo
        self.user_id = user_id
        self.source_track = source_track
        self.search_scope: SearchScope = "all"
        self._search_timer: Timer | None = None
        self.selected_track_id: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id="target-modal"):
            yield Static("Subscreen: Choose target track for transfer")
            yield Static(
                f"Source: {self.source_track.artist} - {self.source_track.album} - {self.source_track.title}",
                id="target-source",
            )
            yield Input(placeholder="Search tracks...", id="target-search")
            with RadioSet(id="target-scopes"):
                yield RadioButton("all", id="target-scope-all", value=True)
                yield RadioButton("artist", id="target-scope-artist")
                yield RadioButton("album", id="target-scope-album")
                yield RadioButton("track", id="target-scope-track")
            yield DataTable(id="target-table")
            with Container(id="target-actions"):
                yield Button("Cancel", id="target-cancel")
                yield Button("Transfer", id="target-transfer", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#target-table", DataTable)
        table.cursor_type = "row"
        for _, label, width in COLUMN_DEFS:
            table.add_column(label, width=width)
        self._refresh_tracks()

    def _refresh_tracks(self) -> None:
        table = self.query_one("#target-table", DataTable)
        search_term = self.query_one("#target-search", Input).value
        rows, _ = self.repo.search_tracks(
            self.user_id,
            search_term,
            self.search_scope,
            limit=DEFAULT_LIMIT,
            offset=0,
        )
        table.clear()
        self.selected_track_id = None

        for row in rows:
            if row.id == self.source_track.id:
                continue
            table.add_row(
                truncate_for_column(row.id, COLUMN_DEFS[0][2]),
                truncate_for_column(row.artist, COLUMN_DEFS[1][2]),
                truncate_for_column(row.album, COLUMN_DEFS[2][2]),
                truncate_for_column(row.title, COLUMN_DEFS[3][2]),
                truncate_for_column(str(row.rating), COLUMN_DEFS[4][2]),
                truncate_for_column(str(row.play_count), COLUMN_DEFS[5][2]),
                key=row.id,
            )

    def _schedule_refresh(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.2, self._refresh_tracks)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "target-search":
            self._schedule_refresh()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        pressed_id = event.pressed.id or ""
        if pressed_id.endswith("artist"):
            self.search_scope = "artist"
        elif pressed_id.endswith("album"):
            self.search_scope = "album"
        elif pressed_id.endswith("track"):
            self.search_scope = "track"
        else:
            self.search_scope = "all"
        self._refresh_tracks()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "target-table":
            return
        self.selected_track_id = str(event.row_key.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "target-transfer":
            self.dismiss(self.selected_track_id)
        else:
            self.dismiss(None)

    def action_transfer(self) -> None:
        self.dismiss(self.selected_track_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NavidromeMetadataApp(App[None]):
    TITLE = "Navidrome Metadata TUI"

    CSS = """
    Screen {
        layout: vertical;
    }

    #too-small {
        display: none;
        height: 1fr;
        content-align: center middle;
        text-align: center;
    }

    #main-layout {
        height: 1fr;
        layout: vertical;
    }

    #detail-pane {
        height: 1fr;
        border: round $primary;
        padding: 1;
        overflow-y: auto;
    }

    #search-panel {
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
    }

    #search-input {
        height: auto;
    }

    #scopes {
        layout: horizontal;
        height: auto;
        margin-top: 1;
    }

    #scopes RadioButton {
        margin-right: 2;
    }

    #results-pane {
        height: 2fr;
        border: round $primary;
        padding: 1;
    }

    #results-table {
        height: 1fr;
    }

    #status {
        height: auto;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("m", "open_transfer_menu", "Actions"),
        Binding("ctrl+m", "open_transfer_menu", "Actions"),
        Binding("n", "next_page", "Next Page"),
        Binding("p", "prev_page", "Prev Page"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, repo: NavidromeRepository, user_hint: str | None) -> None:
        super().__init__()
        self.repo = repo
        self.user_hint = user_hint
        self.user_id: str | None = None
        self.user_name: str | None = None
        self.search_scope: SearchScope = "all"
        self.sort_field: SortField = "artist"
        self.sort_desc = False
        self.page_index = 0
        self.page_size = DEFAULT_PAGE_SIZE
        self.total_results = 0
        self.selected_track_id: str | None = None
        self._search_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f"Terminal too small. Minimum required: {MIN_WIDTH}x{MIN_HEIGHT}",
            id="too-small",
        )
        with Vertical(id="main-layout"):
            yield Static("Select a track to view full details.", id="detail-pane")
            with Container(id="search-panel"):
                yield Input(placeholder="Search tracks...", id="search-input")
                with RadioSet(id="scopes"):
                    yield RadioButton("all", id="scope-all", value=True)
                    yield RadioButton("artist", id="scope-artist")
                    yield RadioButton("album", id="scope-album")
                    yield RadioButton("track", id="scope-track")
            with Container(id="results-pane"):
                yield DataTable(id="results-table")
            yield Static("Ready", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.repo.validate_schema()
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        for key, label, width in COLUMN_DEFS:
            table.add_column(label, key=key, width=width)

        self._update_layout_visibility()
        self._pick_user_then_load()

    def on_resize(self, event: events.Resize) -> None:
        self._update_layout_visibility(width=event.size.width, height=event.size.height)

    def on_unmount(self) -> None:
        self.repo.close()

    def _update_layout_visibility(self, width: int | None = None, height: int | None = None) -> None:
        if width is None or height is None:
            width = self.size.width
            height = self.size.height

        too_small = width < MIN_WIDTH or height < MIN_HEIGHT
        too_small_widget = self.query_one("#too-small", Static)
        main_layout = self.query_one("#main-layout", Vertical)

        too_small_widget.display = too_small
        main_layout.display = not too_small

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def _pick_user_then_load(self) -> None:
        users = self.repo.list_users()
        if not users:
            raise RuntimeError("No users found in Navidrome database")

        if self.user_hint:
            resolved = self.repo.resolve_user(self.user_hint)
            if not resolved:
                raise RuntimeError(f"User '{self.user_hint}' not found in database")
            self.user_id, self.user_name = resolved
            self._set_status(f"User: {self.user_name}")
            self._refresh_tracks()
            return

        self.push_screen(UserSelectScreen(users), self._after_user_selected)

    def _after_user_selected(self, selected_user_id: str | None) -> None:
        if not selected_user_id:
            self.exit()
            return

        resolved = self.repo.resolve_user(selected_user_id)
        if not resolved:
            self.exit(message="Selected user is no longer available")
            return

        self.user_id, self.user_name = resolved
        self._set_status(f"User: {self.user_name}")
        self._refresh_tracks()

    def _schedule_refresh(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(0.2, self._refresh_tracks)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self.page_index = 0
            self._schedule_refresh()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        pressed_id = event.pressed.id or ""
        if pressed_id.endswith("artist"):
            self.search_scope = "artist"
        elif pressed_id.endswith("album"):
            self.search_scope = "album"
        elif pressed_id.endswith("track"):
            self.search_scope = "track"
        else:
            self.search_scope = "all"
        self.page_index = 0
        self._refresh_tracks()

    def _refresh_tracks(self) -> None:
        if not self.user_id:
            return

        search_term = self.query_one("#search-input", Input).value
        offset = self.page_index * self.page_size
        rows, total = self.repo.search_tracks(
            self.user_id,
            search_term,
            self.search_scope,
            sort_field=self.sort_field,
            sort_desc=self.sort_desc,
            limit=self.page_size,
            offset=offset,
        )

        max_page_index = max(0, (total - 1) // self.page_size) if total else 0
        if self.page_index > max_page_index:
            self.page_index = max_page_index
            offset = self.page_index * self.page_size
            rows, total = self.repo.search_tracks(
                self.user_id,
                search_term,
                self.search_scope,
                sort_field=self.sort_field,
                sort_desc=self.sort_desc,
                limit=self.page_size,
                offset=offset,
            )

        self.total_results = total
        table = self.query_one("#results-table", DataTable)
        table.clear()

        for row in rows:
            table.add_row(
                truncate_for_column(row.id, COLUMN_DEFS[0][2]),
                truncate_for_column(row.artist, COLUMN_DEFS[1][2]),
                truncate_for_column(row.album, COLUMN_DEFS[2][2]),
                truncate_for_column(row.title, COLUMN_DEFS[3][2]),
                truncate_for_column(str(row.rating), COLUMN_DEFS[4][2]),
                truncate_for_column(str(row.play_count), COLUMN_DEFS[5][2]),
                key=row.id,
            )

        if rows:
            self.selected_track_id = rows[0].id
            self._show_track_details(rows[0])
            total_pages = max(1, (total + self.page_size - 1) // self.page_size)
            current_page = self.page_index + 1
            self._set_status(
                f"User: {self.user_name} | Results: {total} | Page: {current_page}/{total_pages} | Scope: {self.search_scope} | Sort: {self.sort_field} {'desc' if self.sort_desc else 'asc'}"
            )
        else:
            self.selected_track_id = None
            self.query_one("#detail-pane", Static).update("No results")
            self._set_status(
                f"User: {self.user_name} | Results: 0 | Page: 1/1 | Scope: {self.search_scope} | Sort: {self.sort_field} {'desc' if self.sort_desc else 'asc'}"
            )

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table.id != "results-table":
            return

        clicked_key_obj = getattr(event, "column_key", None)
        clicked_key: str | None
        if clicked_key_obj is not None:
            clicked_key = str(getattr(clicked_key_obj, "value", clicked_key_obj))
        else:
            column_index = getattr(event, "column_index", None)
            if column_index is None or not (0 <= column_index < len(COLUMN_DEFS)):
                return
            clicked_key = COLUMN_DEFS[column_index][0]

        valid_fields = {key for key, _, _ in COLUMN_DEFS}
        if clicked_key not in valid_fields:
            return

        next_field = clicked_key
        if next_field == self.sort_field:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_field = next_field
            self.sort_desc = False

        self.page_index = 0
        self._refresh_tracks()

    def action_next_page(self) -> None:
        if self.total_results <= 0:
            return
        max_page_index = max(0, (self.total_results - 1) // self.page_size)
        if self.page_index >= max_page_index:
            return
        self.page_index += 1
        self._refresh_tracks()

    def action_prev_page(self) -> None:
        if self.page_index <= 0:
            return
        self.page_index -= 1
        self._refresh_tracks()

    def _show_track_details(self, track: TrackRow) -> None:
        detail = (
            f"ID: {track.id}\n"
            f"Artist: {track.artist}\n"
            f"Album: {track.album}\n"
            f"Track: {track.title}\n"
            f"Disc/Track: {track.disc_number}/{track.track_number}\n"
            f"Year: {track.year}\n"
            f"Duration: {track.duration:.1f}s\n"
            f"Rating: {track.rating}\n"
            f"Playcount: {track.play_count}\n"
            f"Last Played: {track.play_date or '-'}\n"
            f"Rated At: {track.rated_at or '-'}\n"
            f"Path: {track.path}"
        )
        self.query_one("#detail-pane", Static).update(detail)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "results-table" or not self.user_id:
            return

        selected_id = str(event.row_key.value)
        self.selected_track_id = selected_id
        track = self.repo.get_track(self.user_id, selected_id)
        if track:
            self._show_track_details(track)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "results-table" or not self.user_id:
            return

        selected_id = str(event.row_key.value)
        self.selected_track_id = selected_id
        track = self.repo.get_track(self.user_id, selected_id)
        if track:
            self._show_track_details(track)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 3 and self.selected_track_id:
            self.action_open_transfer_menu()

    def action_open_transfer_menu(self) -> None:
        if not self.user_id:
            return
        if not self.selected_track_id:
            self._set_status("No source track selected")
            return

        source_track = self.repo.get_track(self.user_id, self.selected_track_id)
        if not source_track:
            self._set_status("Source track no longer exists")
            return

        self.push_screen(
            TransferActionScreen(),
            lambda mode: self._after_transfer_action(mode, source_track),
        )

    def _after_transfer_action(
        self,
        mode: TransferMode | None,
        source_track: TrackRow,
    ) -> None:
        if mode is None:
            return

        self.push_screen(
            TargetPickerScreen(self.repo, self.user_id or "", source_track),
            lambda target_id: self._execute_transfer(mode, source_track.id, target_id),
        )

    def _execute_transfer(
        self,
        mode: TransferMode,
        source_track_id: str,
        target_track_id: str | None,
    ) -> None:
        if not target_track_id:
            self._set_status("Transfer cancelled")
            return

        try:
            self.repo.transfer_metadata(
                user_id=self.user_id or "",
                source_track_id=source_track_id,
                target_track_id=target_track_id,
                mode=mode,
            )
        except Exception as exc:
            self._set_status(f"Transfer failed: {exc}")
            return

        self._refresh_tracks()
        refreshed_source = self.repo.get_track(self.user_id or "", source_track_id)
        if refreshed_source:
            self._show_track_details(refreshed_source)
        self._set_status(f"Transfer complete: {mode}")


@click.command()
@click.argument("db_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--user", "user_hint", default=None, help="User ID or username")
def cli(db_path: Path, user_hint: str | None) -> None:
    """Browse and clean Navidrome metadata via TUI."""
    repo = NavidromeRepository(db_path)
    app = NavidromeMetadataApp(repo=repo, user_hint=user_hint)
    app.run()


if __name__ == "__main__":
    cli()
