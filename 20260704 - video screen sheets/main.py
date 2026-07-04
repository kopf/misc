#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["Pillow"]
# ///
"""Generate contact-sheet style video screens for every video in a folder."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".m2ts",
    ".ogv",
    ".ts",
    ".webm",
    ".wmv",
}

BACKGROUND = (18, 18, 22)
PANEL = (28, 30, 36)
PANEL_ALT = (36, 38, 46)
TEXT = (244, 245, 248)
SUBTLE = (170, 176, 188)
ACCENT = (104, 167, 255)
BORDER = (58, 62, 74)


@dataclass(frozen=True)
class VideoStreamInfo:
    codec: str
    width: int
    height: int
    frame_rate: float | None
    bit_rate: int | None
    rotation: int


@dataclass(frozen=True)
class AudioStreamInfo:
    codec: str
    channels: int | None
    sample_rate: int | None


@dataclass(frozen=True)
class VideoMetadata:
    path: Path
    duration_seconds: float | None
    file_size: int
    container: str
    video: VideoStreamInfo | None
    audio_streams: list[AudioStreamInfo]


@dataclass(frozen=True)
class RenderConfig:
    size: tuple[int, int]
    screenshots: int
    columns: int | None
    output_dir: Path
    image_quality: int


class VideoProbeError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a screenshot sheet for every video in a folder.",
    )
    parser.add_argument("folder", type=Path, help="Folder containing video files.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where the generated sheets will be written.",
    )
    parser.add_argument(
        "-n",
        "--screenshots",
        type=int,
        default=18,
        help="Number of screenshots to sample per video.",
    )
    parser.add_argument(
        "--size",
        default="1920x1080",
        help="Output image size as WIDTHxHEIGHT.",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=None,
        help="Number of screenshot columns. Defaults to an auto-fit layout.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=62,
        help="JPEG quality for output images (1-95, lower = smaller files).",
    )
    return parser.parse_args()


def parse_size(value: str) -> tuple[int, int]:
    normalized = value.lower().replace(" ", "")
    if "x" not in normalized:
        raise argparse.ArgumentTypeError("size must look like 1920x1080")

    width_text, height_text = normalized.split("x", maxsplit=1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must contain integers") from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("size values must be positive")

    return width, height


def main() -> int:
    args = parse_args()
    if args.screenshots <= 0:
        raise SystemExit("--screenshots must be greater than zero")
    if not 1 <= args.quality <= 95:
        raise SystemExit("--quality must be between 1 and 95")

    if not args.folder.is_dir():
        raise SystemExit(f"Input folder does not exist: {args.folder}")

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not available on PATH")
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not available on PATH")

    config = RenderConfig(
        size=parse_size(args.size),
        screenshots=args.screenshots,
        columns=args.columns,
        output_dir=args.output_dir or args.folder / "screens",
        image_quality=args.quality,
    )
    config.output_dir.mkdir(parents=True, exist_ok=True)

    videos = sorted(
        path
        for path in args.folder.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )

    if not videos:
        print(f"No video files found in {args.folder}", file=sys.stderr)
        return 1

    rendered = 0
    durations: list[float] = []
    for video_path in videos:
        try:
            metadata = probe_video(video_path)
            if metadata.duration_seconds and metadata.duration_seconds > 0:
                durations.append(metadata.duration_seconds)

            output_path = config.output_dir / f"{video_path.stem}.jpg"
            if output_path.exists() and not args.overwrite:
                print(f"Skipping existing {output_path}")
                continue
            render_video_sheet(metadata, output_path, config)
            print(f"Wrote {output_path}")
            rendered += 1
        except Exception as exc:  # noqa: BLE001
            print(f"Failed for {video_path}: {exc}", file=sys.stderr)

    summary_path = config.output_dir / "video-length-histogram.jpg"
    render_duration_histogram(
        durations_seconds=durations,
        output_path=summary_path,
        size=config.size,
        video_count=len(videos),
        quality=config.image_quality,
    )
    print(f"Wrote {summary_path}")

    print(f"Rendered {rendered} sheet(s)")
    return 0


def probe_video(path: Path) -> VideoMetadata:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise VideoProbeError(completed.stderr.strip() or "ffprobe failed")

    data = json.loads(completed.stdout)
    format_info = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = None
    audio_streams: list[AudioStreamInfo] = []
    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = VideoStreamInfo(
                codec=str(stream.get("codec_name") or "unknown"),
                width=int(stream.get("width") or 0),
                height=int(stream.get("height") or 0),
                frame_rate=parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
                bit_rate=parse_int(stream.get("bit_rate")),
                rotation=parse_rotation(stream),
            )
        elif codec_type == "audio":
            audio_streams.append(
                AudioStreamInfo(
                    codec=str(stream.get("codec_name") or "unknown"),
                    channels=parse_int(stream.get("channels")),
                    sample_rate=parse_int(stream.get("sample_rate")),
                )
            )

    return VideoMetadata(
        path=path,
        duration_seconds=parse_float(format_info.get("duration")),
        file_size=path.stat().st_size,
        container=str(format_info.get("format_name") or path.suffix.lstrip(".") or "unknown"),
        video=video_stream,
        audio_streams=audio_streams,
    )


def parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def parse_frame_rate(value: object) -> float | None:
    if value is None:
        return None
    text = str(value)
    if "/" in text:
        numerator_text, denominator_text = text.split("/", maxsplit=1)
        try:
            numerator = float(numerator_text)
            denominator = float(denominator_text)
        except ValueError:
            return None
        if denominator == 0:
            return None
        return numerator / denominator

    return parse_float(text)


def parse_rotation(stream: dict[str, object]) -> int:
    tags = stream.get("tags")
    if isinstance(tags, dict):
        rotation = parse_int(tags.get("rotate"))
        if rotation is not None:
            return rotation

    side_data_list = stream.get("side_data_list")
    if isinstance(side_data_list, list):
        for side_data in side_data_list:
            if isinstance(side_data, dict):
                rotation = parse_int(side_data.get("rotation"))
                if rotation is not None:
                    return rotation

    return 0


def render_video_sheet(metadata: VideoMetadata, output_path: Path, config: RenderConfig) -> None:
    if metadata.video is None:
        raise VideoProbeError("no video stream found")

    width, height = config.size
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    title_font = load_font(max(28, height // 28), bold=True)
    body_font = load_font(max(16, height // 58))
    small_font = load_font(max(13, height // 72))

    header, header_height = draw_header(
        draw=draw,
        metadata=metadata,
        width=width,
        screenshot_count=config.screenshots,
        title_font=title_font,
        body_font=body_font,
        small_font=small_font,
    )
    image.paste(header, (0, 0))

    layout = build_layout(width, height - header_height, config.screenshots, config.columns)
    timestamps = sample_timestamps(metadata.duration_seconds, config.screenshots)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        for index, timestamp in enumerate(timestamps):
            row, column = divmod(index, layout.columns)
            tile = layout.tile_box(column, row, header_height)
            frame_path = temp_root / f"frame_{index:03d}.png"
            extract_frame(metadata.path, timestamp, layout.thumb_size, frame_path)
            with Image.open(frame_path) as frame_image:
                draw_thumbnail_tile(
                    image=image,
                    frame=frame_image.convert("RGB"),
                    tile=tile,
                    timestamp=timestamp,
                    caption_font=small_font,
                )

    save_compact_jpeg(image, output_path, quality=config.image_quality)


def build_layout(canvas_width: int, canvas_height: int, screenshot_count: int, columns: int | None) -> "SheetLayout":
    if columns is None:
        columns = max(1, round(math.sqrt(screenshot_count * canvas_width / max(canvas_height, 1))))
        columns = min(columns, screenshot_count)
    else:
        columns = max(1, min(columns, screenshot_count))

    rows = max(1, math.ceil(screenshot_count / columns))
    margin = max(6, min(canvas_width, canvas_height) // 90)
    gap = max(4, min(canvas_width, canvas_height) // 140)

    available_width = canvas_width - margin * 2 - gap * (columns - 1)
    available_height = canvas_height - margin * 2 - gap * (rows - 1)

    tile_width = max(1, available_width // columns)
    tile_height = max(1, available_height // rows)
    thumb_height = max(1, tile_height - max(16, tile_height // 12))

    return SheetLayout(
        columns=columns,
        rows=rows,
        margin=margin,
        gap=gap,
        tile_width=tile_width,
        tile_height=tile_height,
        thumb_width=tile_width,
        thumb_height=thumb_height,
    )


@dataclass(frozen=True)
class SheetLayout:
    columns: int
    rows: int
    margin: int
    gap: int
    tile_width: int
    tile_height: int
    thumb_width: int
    thumb_height: int

    @property
    def thumb_size(self) -> tuple[int, int]:
        return self.thumb_width, self.thumb_height

    def tile_box(self, column: int, row: int, header_height: int) -> tuple[int, int, int, int]:
        left = self.margin + column * (self.tile_width + self.gap)
        top = header_height + self.margin + row * (self.tile_height + self.gap)
        return left, top, left + self.tile_width, top + self.tile_height


def sample_timestamps(duration_seconds: float | None, count: int) -> list[float]:
    if count == 1:
        return [max(0.0, (duration_seconds or 0.0) / 2.0)]

    if not duration_seconds or duration_seconds <= 0:
        return [0.0 for _ in range(count)]

    step = duration_seconds / (count + 1)
    return [min(duration_seconds - 0.05, step * (index + 1)) for index in range(count)]


def extract_frame(video_path: Path, timestamp: float, size: tuple[int, int], output_path: Path) -> None:
    target_width, target_height = size
    filter_expression = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        filter_expression,
        "-y",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def draw_header(
    draw: ImageDraw.ImageDraw,
    metadata: VideoMetadata,
    width: int,
    screenshot_count: int,
    title_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> tuple[Image.Image, int]:
    padding = max(12, width // 128)
    line_gap = max(3, padding // 4)

    lines = build_metadata_lines(metadata)
    title_lines = wrap_text(draw, metadata.path.name, title_font, width - padding * 2)
    body_lines = [line for entry in lines for line in wrap_text(draw, entry, body_font, width - padding * 2)]

    title_height = measure_block(draw, title_lines, title_font, line_gap)
    body_height = measure_block(draw, body_lines, body_font, line_gap)
    footer_height = font_size(small_font, 13) + line_gap * 2
    header_height = padding * 2 + title_height + body_height + footer_height + line_gap

    header = Image.new("RGB", (width, header_height), PANEL)
    header_draw = ImageDraw.Draw(header)

    header_draw.rectangle((0, header_height - 3, width, header_height), fill=PANEL_ALT)
    header_draw.rectangle((0, 0, width - 1, header_height - 1), outline=BORDER)
    header_draw.rectangle((padding, padding, padding + 7, header_height - padding), fill=ACCENT)

    current_y = padding
    for line in title_lines:
        header_draw.text((padding + 15, current_y), line, font=title_font, fill=TEXT)
        current_y += text_height(header_draw, line, title_font) + line_gap

    current_y += line_gap
    for line in body_lines:
        header_draw.text((padding + 15, current_y), line, font=body_font, fill=SUBTLE)
        current_y += text_height(header_draw, line, body_font) + line_gap

    footer_text = f"Screenshots: {screenshot_count}"
    header_draw.text((padding + 15, header_height - padding - font_size(small_font, 13)), footer_text, font=small_font, fill=ACCENT)
    return header, header_height


def build_metadata_lines(metadata: VideoMetadata) -> list[str]:
    lines = [
        f"Container: {metadata.container}",
        f"Duration: {format_duration(metadata.duration_seconds)}",
        f"File size: {format_file_size(metadata.file_size)}",
    ]

    if metadata.video is not None:
        video = metadata.video
        video_text = f"Video: {video.codec}"
        if video.width and video.height:
            video_text += f" {video.width}x{video.height}"
        if video.frame_rate:
            video_text += f" @ {video.frame_rate:.3f} fps"
        if video.rotation:
            video_text += f" (rot {video.rotation}°)"
        lines.append(video_text)

        if video.bit_rate:
            lines.append(f"Video bitrate: {format_bit_rate(video.bit_rate)}")

    if metadata.audio_streams:
        audio_chunks = []
        for stream in metadata.audio_streams:
            chunk = stream.codec
            if stream.channels:
                chunk += f" {stream.channels}ch"
            if stream.sample_rate:
                chunk += f" {format_sample_rate(stream.sample_rate)} kHz"
            audio_chunks.append(chunk)
        lines.append("Audio: " + "; ".join(audio_chunks))
    else:
        lines.append("Audio: none detected")

    return lines


def draw_thumbnail_tile(
    image: Image.Image,
    frame: Image.Image,
    tile: tuple[int, int, int, int],
    timestamp: float,
    caption_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = tile
    caption_height = max(16, font_size(caption_font, 13) + 5)
    thumb_box = (left, top, right, bottom - caption_height)
    caption_box = (left, bottom - caption_height, right, bottom)

    draw.rectangle(tile, fill=PANEL, outline=BORDER, width=1)
    draw.rectangle((thumb_box[0] + 1, thumb_box[1] + 1, thumb_box[2] - 1, thumb_box[3] - 1), fill=(10, 10, 12))

    fitted = ImageOps.contain(frame, (thumb_box[2] - thumb_box[0] - 4, thumb_box[3] - thumb_box[1] - 4))
    paste_x = thumb_box[0] + ((thumb_box[2] - thumb_box[0]) - fitted.width) // 2
    paste_y = thumb_box[1] + ((thumb_box[3] - thumb_box[1]) - fitted.height) // 2
    image.paste(fitted, (paste_x, paste_y))

    draw.rectangle(caption_box, fill=PANEL_ALT)
    timestamp_text = format_timestamp(timestamp)
    draw.text((left + 5, bottom - caption_height + 2), timestamp_text, font=caption_font, fill=TEXT)


def save_compact_jpeg(image: Image.Image, output_path: Path, quality: int) -> None:
    rgb = image.convert("RGB")
    rgb.save(
        output_path,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=2,
    )


def render_duration_histogram(
    durations_seconds: list[float],
    output_path: Path,
    size: tuple[int, int],
    video_count: int,
    quality: int,
) -> None:
    width, height = size
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    title_font = load_font(max(26, height // 28), bold=True)
    body_font = load_font(max(15, height // 62))
    small_font = load_font(max(13, height // 80))

    pad_x = max(26, width // 22)
    pad_top = max(26, height // 18)
    chart_top = pad_top + max(46, font_size(title_font, 28) + 20)
    chart_bottom = height - max(120, height // 6)
    chart_left = pad_x
    chart_right = width - pad_x
    chart_width = chart_right - chart_left
    chart_height = max(10, chart_bottom - chart_top)

    draw.rectangle((0, 0, width - 1, height - 1), outline=BORDER)
    draw.text((pad_x, pad_top), "Video Length Distribution", font=title_font, fill=TEXT)

    if not durations_seconds:
        message = f"No valid durations found across {video_count} videos"
        draw.text((pad_x, chart_top + 10), message, font=body_font, fill=SUBTLE)
        save_compact_jpeg(image, output_path, quality=quality)
        return

    sorted_durations = sorted(durations_seconds)
    bins = min(24, max(6, int(round(math.sqrt(len(sorted_durations))))))
    max_duration = max(sorted_durations)
    min_duration = min(sorted_durations)
    if max_duration <= 0:
        max_duration = 1.0

    bin_width = max_duration / bins
    counts = [0] * bins
    for value in sorted_durations:
        index = min(bins - 1, int(value / bin_width))
        counts[index] += 1

    max_count = max(counts) or 1
    axis_color = (120, 126, 140)

    draw.line((chart_left, chart_top, chart_left, chart_bottom), fill=axis_color, width=2)
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=axis_color, width=2)

    bar_gap = max(1, chart_width // (bins * 7))
    bar_width = max(1, (chart_width - bar_gap * (bins - 1)) // bins)
    total_bar_width = bins * bar_width + (bins - 1) * bar_gap
    start_x = chart_left + (chart_width - total_bar_width) // 2

    for idx, count in enumerate(counts):
        left = start_x + idx * (bar_width + bar_gap)
        bar_height = int((count / max_count) * (chart_height - 8))
        top = chart_bottom - bar_height
        fill = ACCENT if idx % 2 == 0 else (84, 143, 223)
        draw.rectangle((left, top, left + bar_width, chart_bottom - 1), fill=fill)

    max_minutes = max_duration / 60
    x_labels = [
        (chart_left, "0m"),
        (chart_left + chart_width // 2, f"{max_minutes / 2:.1f}m"),
        (chart_right, f"{max_minutes:.1f}m"),
    ]
    for x, label in x_labels:
        text_box = draw.textbbox((0, 0), label, font=small_font)
        label_width = text_box[2] - text_box[0]
        draw.text((x - label_width // 2, chart_bottom + 8), label, font=small_font, fill=SUBTLE)

    y_labels = [0, max(1, max_count // 2), max_count]
    for y_value in y_labels:
        ratio = y_value / max_count
        y = chart_bottom - int(ratio * (chart_height - 8))
        label = str(y_value)
        text_box = draw.textbbox((0, 0), label, font=small_font)
        label_w = text_box[2] - text_box[0]
        label_h = text_box[3] - text_box[1]
        draw.text((chart_left - label_w - 10, y - label_h // 2), label, font=small_font, fill=SUBTLE)

    avg_duration = statistics.fmean(sorted_durations)
    median_duration = statistics.median(sorted_durations)
    stats = (
        f"Videos: {len(sorted_durations)}/{video_count} with duration  |  "
        f"Min: {format_duration(min_duration)}  |  "
        f"Median: {format_duration(float(median_duration))}  |  "
        f"Avg: {format_duration(avg_duration)}  |  "
        f"Max: {format_duration(max_duration)}"
    )
    draw.text((pad_x, height - max(62, height // 12)), stats, font=body_font, fill=SUBTLE)

    save_compact_jpeg(image, output_path, quality=quality)


def measure_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    line_gap: int,
) -> int:
    if not lines:
        return 0
    heights = [text_height(draw, line, font) for line in lines]
    return sum(heights) + line_gap * (len(lines) - 1)


def text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return [text]

    wrapped: list[str] = []
    for paragraph in text.splitlines() or [text]:
        words = paragraph.split()
        if not words:
            wrapped.append("")
            continue

        current = words[0]
        for word in words[1:]:
            proposal = f"{current} {word}"
            if draw.textbbox((0, 0), proposal, font=font)[2] <= max_width:
                current = proposal
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
    return wrapped or [text]


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    fractional = seconds - total_seconds
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}.{int(round(fractional * 10)):01d}"
    return f"{minutes:d}:{secs:02d}.{int(round(fractional * 10)):01d}"


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, seconds)
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    secs = int(total_seconds % 60)
    tenths = int(round((total_seconds - int(total_seconds)) * 10))
    if tenths == 10:
        tenths = 0
        secs += 1
    if secs == 60:
        secs = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        hours += 1
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{tenths:d}"
    return f"{minutes:02d}:{secs:02d}.{tenths:d}"


def format_file_size(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def format_bit_rate(bit_rate: int) -> str:
    return f"{format_rate(bit_rate / 1000)} kbps"


def format_sample_rate(sample_rate: int) -> str:
    return f"{sample_rate / 1000:.1f}"


def format_rate(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def font_size(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, fallback: int) -> int:
    size = getattr(font, "size", None)
    return int(size) if isinstance(size, (int, float)) else fallback


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Supplemental/Helvetica Neue Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Helvetica Neue.ttf",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            ]
        )
    candidates.extend(
        [
            "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/DejaVuSans-Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/DejaVuSans.ttf",
        ]
    )

    for font_path in candidates:
        path = Path(font_path)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


if __name__ == "__main__":
    raise SystemExit(main())
