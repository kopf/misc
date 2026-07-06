#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "matplotlib",
# ]
# ///

from __future__ import annotations

import argparse
import math
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".webm",
    ".mpg",
    ".mpeg",
    ".flv",
    ".wmv",
}

NICE_BREAKPOINTS = [
    1,
    2,
    5,
    10,
    15,
    20,
    30,
    45,
    60,
    90,
    120,
    180,
    300,
    600,
    900,
    1200,
    1800,
    2400,
    3600,
    5400,
    7200,
    10800,
]


@dataclass
class VideoDuration:
    path: Path
    seconds: float


@dataclass
class Bucket:
    label: str
    count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a directory for videos, calculate durations, and create a "
            "duration distribution graph."
        )
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory to scan for video files.",
    )
    parser.add_argument(
        "--chart",
        choices=["histogram", "pie"],
        default="histogram",
        help="Chart type to generate (default: histogram).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output PNG path. Defaults to ./video-duration-<chart>.png",
    )
    parser.add_argument(
        "--top-level-only",
        action="store_true",
        help="Only scan files directly in the provided directory.",
    )
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(VIDEO_EXTENSIONS)),
        help=(
            "Comma-separated file extensions to include. "
            "Example: .mp4,.mkv,.mov"
        ),
    )
    return parser.parse_args()


def ensure_ffprobe_available() -> None:
    if shutil.which("ffprobe"):
        return
    raise RuntimeError(
        "ffprobe was not found in PATH. Install ffmpeg/ffprobe first, "
        "then re-run this script."
    )


def normalize_extensions(raw_extensions: str) -> set[str]:
    normalized: set[str] = set()
    for extension in raw_extensions.split(","):
        cleaned = extension.strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        normalized.add(cleaned)
    return normalized


def discover_video_files(root: Path, extensions: set[str], recursive: bool) -> list[Path]:
    files: list[Path] = []
    if recursive:
        for candidate in root.rglob("*"):
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            if candidate.suffix.lower() in extensions:
                files.append(candidate)
    else:
        for candidate in root.iterdir():
            if not candidate.is_file() or candidate.name.startswith("."):
                continue
            if candidate.suffix.lower() in extensions:
                files.append(candidate)
    return sorted(files)


def probe_duration_seconds(video_path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip()
    if not output:
        raise RuntimeError("ffprobe returned no duration value")
    return float(output)


def even_sample(values: Sequence[int], target_count: int) -> list[int]:
    if target_count <= 0 or not values:
        return []
    if len(values) <= target_count:
        return list(values)
    indexes = {
        round(i * (len(values) - 1) / (target_count - 1))
        for i in range(target_count)
    }
    return [values[index] for index in sorted(indexes)]


def select_breakpoints(durations: Sequence[float]) -> list[int]:
    min_duration = min(durations)
    max_duration = max(durations)

    candidates = [point for point in NICE_BREAKPOINTS if min_duration < point < max_duration]
    if not candidates:
        fallback = max(1, int(math.ceil(max_duration / 2)))
        return [fallback]

    target_breakpoints = 10

    if max_duration >= 900 and min_duration < 120:
        low_detail = [point for point in [10, 20, 30, 45, 60, 90, 120] if point in candidates]
        high_values = [point for point in candidates if point > 120]
        remaining_slots = max(0, target_breakpoints - len(low_detail))
        return low_detail + even_sample(high_values, remaining_slots)

    return even_sample(candidates, target_breakpoints)


def format_duration(seconds: int | float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"

    if seconds % 60 == 0:
        return f"{seconds // 60}m"

    minutes = seconds / 60
    if minutes < 10:
        minute_label = f"{minutes:.1f}".rstrip("0").rstrip(".")
        return f"{minute_label}m"

    return f"{int(round(minutes))}m"


def build_buckets(durations: Sequence[float]) -> list[Bucket]:
    breakpoints = select_breakpoints(durations)

    buckets: list[Bucket] = []

    first_upper = breakpoints[0]
    first_count = sum(1 for duration in durations if duration < first_upper)
    buckets.append(Bucket(label=f"< {format_duration(first_upper)}", count=first_count))

    for lower, upper in zip(breakpoints, breakpoints[1:]):
        count = sum(1 for duration in durations if lower <= duration < upper)
        buckets.append(
            Bucket(
                label=f"{format_duration(lower)}-{format_duration(upper)}",
                count=count,
            )
        )

    last_lower = breakpoints[-1]
    last_count = sum(1 for duration in durations if duration >= last_lower)
    buckets.append(Bucket(label=f"{format_duration(last_lower)}+", count=last_count))

    return buckets


def plot_histogram(buckets: Sequence[Bucket], output_path: Path, title: str) -> None:
    labels = [bucket.label for bucket in buckets]
    counts = [bucket.count for bucket in buckets]

    plt.figure(figsize=(12, 6))
    plt.bar(labels, counts, color="#4C72B0")
    plt.title(title)
    plt.xlabel("Duration bucket")
    plt.ylabel("Number of videos")
    plt.xticks(rotation=35, ha="right")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def plot_pie_chart(buckets: Sequence[Bucket], output_path: Path, title: str) -> None:
    non_empty = [bucket for bucket in buckets if bucket.count > 0]
    labels = [bucket.label for bucket in non_empty]
    counts = [bucket.count for bucket in non_empty]

    plt.figure(figsize=(9, 9))
    plt.pie(
        counts,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
    )
    plt.title(title)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def default_output_path(chart: str) -> Path:
    return Path.cwd() / f"video-duration-{chart}.png"


def print_summary(
    videos: Sequence[VideoDuration],
    skipped: Sequence[tuple[Path, str]],
    output_path: Path,
    scanned_count: int,
) -> None:
    values = [entry.seconds for entry in videos]

    print(f"Scanned files: {scanned_count}")
    print(f"Videos with parsed durations: {len(values)}")
    print(f"Skipped files: {len(skipped)}")
    print(f"Min duration: {format_duration(min(values))}")
    print(f"Median duration: {format_duration(statistics.median(values))}")
    print(f"Max duration: {format_duration(max(values))}")
    print(f"Output graph: {output_path}")

    if skipped:
        print("\nSkipped file details:")
        for path, reason in skipped[:10]:
            print(f"- {path}: {reason}")
        if len(skipped) > 10:
            print(f"- ... {len(skipped) - 10} more")


def main() -> int:
    args = parse_args()

    directory = args.directory.expanduser().resolve()
    recursive = not args.top_level_only

    if not directory.exists():
        print(f"Error: path does not exist: {directory}", file=sys.stderr)
        return 1

    if not directory.is_dir():
        print(f"Error: path is not a directory: {directory}", file=sys.stderr)
        return 1

    extensions = normalize_extensions(args.extensions)
    if not extensions:
        print("Error: no valid extensions were provided.", file=sys.stderr)
        return 1

    try:
        ensure_ffprobe_available()
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    discovered = discover_video_files(directory, extensions=extensions, recursive=recursive)
    if not discovered:
        print("Error: no matching video files found.", file=sys.stderr)
        return 1

    parsed: list[VideoDuration] = []
    skipped: list[tuple[Path, str]] = []

    for video_file in discovered:
        try:
            duration = probe_duration_seconds(video_file)
            parsed.append(VideoDuration(path=video_file, seconds=duration))
        except (ValueError, RuntimeError, subprocess.SubprocessError) as error:
            skipped.append((video_file, str(error)))

    if not parsed:
        print("Error: no video durations could be parsed.", file=sys.stderr)
        return 1

    buckets = build_buckets([item.seconds for item in parsed])

    output_path = (
        args.output.expanduser().resolve() if args.output is not None else default_output_path(args.chart)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    title = f"Video Duration Distribution ({len(parsed)} videos)"
    if args.chart == "histogram":
        plot_histogram(buckets, output_path=output_path, title=title)
    else:
        plot_pie_chart(buckets, output_path=output_path, title=title)

    print_summary(parsed, skipped=skipped, output_path=output_path, scanned_count=len(discovered))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
