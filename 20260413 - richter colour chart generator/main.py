#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pillow",
#   "numpy",
#   "click",
# ]
# ///

import random
import numpy as np
import click
from PIL import Image, ImageDraw

class RichterGenerator:
    def __init__(self, rows, cols, square_size, gap):
        self.rows = rows
        self.cols = cols
        self.square_size = square_size
        self.gap = gap
        self.total_squares = rows * cols
        self.width = (cols * square_size) + ((cols + 1) * gap)
        self.height = (rows * square_size) + ((rows + 1) * gap)

    def generate_colors(self, mode, palette_type):
        if mode == "random":
            return [tuple(np.random.randint(0, 256, 3)) for _ in range(self.total_squares)]
        elif mode == "unique":
            colors = set()
            while len(colors) < self.total_squares:
                r, g, b = np.random.randint(0, 256, 3)
                colors.add((int(r), int(g), int(b)))
            color_list = list(colors)
            random.shuffle(color_list)
            return color_list
        elif mode == "palette":
            colors = []
            for _ in range(self.total_squares):
                if palette_type == "industrial":
                    r, g, b = np.random.randint(60, 140, 3)
                elif palette_type == "vibrant":
                    r = np.random.choice([np.random.randint(0, 40), np.random.randint(210, 256)])
                    g = np.random.choice([np.random.randint(0, 40), np.random.randint(210, 256)])
                    b = np.random.choice([np.random.randint(0, 40), np.random.randint(210, 256)])
                else: # pastel
                    r, g, b = np.random.randint(180, 256, 3)
                colors.append((int(r), int(g), int(b)))
            return colors

    def create_frame(self, colors):
        img = Image.new("RGB", (self.width, self.height), "white")
        draw = ImageDraw.Draw(img)
        idx = 0
        for r in range(self.rows):
            for c in range(self.cols):
                x0 = (c * self.square_size) + ((c + 1) * self.gap)
                y0 = (r * self.square_size) + ((r + 1) * self.gap)
                draw.rectangle([x0, y0, x0 + self.square_size, y0 + self.square_size], fill=colors[idx])
                idx += 1
        return img

    def save_svg(self, colors, filename):
        svg_header = f'<svg width="{self.width}" height="{self.height}" xmlns="http://www.w3.org/2000/svg" style="background:white">\n'
        rects = []
        idx = 0
        for r in range(self.rows):
            for c in range(self.cols):
                x = (c * self.square_size) + ((c + 1) * self.gap)
                y = (r * self.square_size) + ((r + 1) * self.gap)
                color_hex = '#%02x%02x%02x' % colors[idx]
                rects.append(f'  <rect x="{x}" y="{y}" width="{self.square_size}" height="{self.square_size}" fill="{color_hex}" />')
                idx += 1
        with open(filename, "w") as f:
            f.write(svg_header + "\n".join(rects) + "\n</svg>")

# --- CLI DEFINITION ---

@click.group()
def cli():
    """Gerhard Richter Color Chart Generator."""
    pass

def shared_options(f):
    """Decorator to apply common options to both subcommands."""
    options = [
        click.option('--rows', default=64, help='Number of rows.'),
        click.option('--cols', default=64, help='Number of columns.'),
        click.option('--size', default=10, help='Size of each square (px).'),
        click.option('--gap', default=0, help='Gap between squares (px).'),
        click.option('--mode', type=click.Choice(['unique', 'random', 'palette']), default='unique', help='Color logic.'),
        click.option('--palette', type=click.Choice(['industrial', 'vibrant', 'pastel']), default='vibrant', help='Palette for "palette" mode.'),
        click.option('--out', default='richter_output', help='Filename base.')
    ]
    for option in reversed(options):
        f = option(f)
    return f

@cli.command()
@shared_options
@click.option('--format', type=click.Choice(['png', 'svg', 'both']), default='both', help='File format.')
def static(rows, cols, size, gap, mode, palette, out, format):
    """Generate a single static painting."""
    painter = RichterGenerator(rows, cols, size, gap)
    colors = painter.generate_colors(mode, palette)
    
    if format in ['png', 'both']:
        painter.create_frame(colors).save(f"{out}.png")
        click.echo(f"📸 Saved Raster: {out}.png")
    if format in ['svg', 'both']:
        painter.save_svg(colors, f"{out}.svg")
        click.echo(f"📐 Saved Vector: {out}.svg")

@cli.command()
@shared_options
@click.option('--frames', default=10, help='Number of animation frames.')
@click.option('--fps', default=5, help='Frames per second.')
def gif(rows, cols, size, gap, mode, palette, out, frames, fps):
    """Generate an animated GIF of multiple charts."""
    painter = RichterGenerator(rows, cols, size, gap)
    frame_images = []
    
    with click.progressbar(range(frames), label=f"🎬 Rendering {frames} frames") as bar:
        for _ in bar:
            frame_colors = painter.generate_colors(mode, palette)
            frame_images.append(painter.create_frame(frame_colors))
    
    duration_ms = int(1000 / fps)
    frame_images[0].save(
        f"{out}.gif",
        save_all=True,
        append_images=frame_images[1:],
        duration=duration_ms,
        loop=0,
        optimize=True
    )
    click.echo(f"🎞️ Animation saved: {out}.gif")

if __name__ == "__main__":
    cli()
