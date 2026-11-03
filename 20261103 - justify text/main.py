#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
#     "pyphen",
# ]
# ///
"""Output a text file fully justified to a given line width, hyphenating English words."""

import re
from collections import deque
from pathlib import Path

import click
import pyphen

HYPHENATOR = pyphen.Pyphen(lang="en_US", left=2, right=3)
ALPHA_RUN = re.compile(r"[A-Za-z]+")


def split_options(word: str) -> list[tuple[str, str]]:
    """Return all (head, tail) ways to break a word across lines."""
    options = []
    for i, char in enumerate(word[:-1]):
        if char == "-" and i > 0:
            options.append((word[: i + 1], word[i + 1 :]))
    for match in ALPHA_RUN.finditer(word):
        for pos in HYPHENATOR.positions(match.group()):
            cut = match.start() + pos
            options.append((word[:cut] + "-", word[cut:]))
    return options


def best_split(word: str, space: int) -> tuple[str, str] | None:
    """Return the split with the longest head that fits in the given space."""
    fitting = [opt for opt in split_options(word) if len(opt[0]) <= space]
    return max(fitting, key=lambda opt: len(opt[0]), default=None)


def wrap_paragraph(words: list[str], width: int) -> list[list[str]]:
    lines: list[list[str]] = []
    current: list[str] = []
    queue = deque(words)
    while queue:
        word = queue.popleft()
        used = sum(map(len, current)) + len(current)  # includes the space before the new word
        if used + len(word) <= width:
            current.append(word)
            continue
        split = best_split(word, width - used)
        if split:
            current.append(split[0])
            queue.appendleft(split[1])
        elif not current:
            # Word too long for a line and no valid hyphenation point: force a break.
            current.append(word[: width - 1] + "-")
            queue.appendleft(word[width - 1 :])
        else:
            queue.appendleft(word)
        lines.append(current)
        current = []
    if current:
        lines.append(current)
    return lines


def justify_line(words: list[str], width: int) -> str:
    gaps = len(words) - 1
    if gaps == 0:
        return words[0]
    base, extra = divmod(width - sum(map(len, words)), gaps)
    parts = []
    for i, word in enumerate(words[:-1]):
        parts.append(word + " " * (base + (1 if i < extra else 0)))
    parts.append(words[-1])
    return "".join(parts)


def justify_paragraph(text: str, width: int) -> str:
    lines = wrap_paragraph(text.split(), width)
    out = [justify_line(line, width) for line in lines[:-1]]
    out.append(" ".join(lines[-1]))
    return "\n".join(out)


@click.command()
@click.argument("width", type=click.IntRange(min=2))
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def main(width: int, path: Path) -> None:
    """Print the text file at PATH fully justified to WIDTH characters."""
    paragraphs = re.split(r"\n\s*\n", path.read_text(encoding="utf-8"))
    click.echo(
        "\n\n".join(justify_paragraph(p, width) for p in paragraphs if p.strip())
    )


if __name__ == "__main__":
    main()
