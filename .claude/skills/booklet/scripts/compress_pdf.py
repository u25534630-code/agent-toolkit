#!/usr/bin/env python3
"""
Локальное сжатие готового PDF через Ghostscript.

Default settings are tuned for screen/mobile reading:
- 160 dpi image downsampling
- JPEG quality 84
- links and page count are preserved by Ghostscript
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def file_size(path: Path) -> int:
    return path.stat().st_size


def format_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


# На Windows Ghostscript ставится как gswin64c.exe, на macOS и Linux — как gs.
GHOSTSCRIPT_NAMES = ["gs", "gswin64c", "gswin32c"]


def find_ghostscript() -> str:
    """Найти исполняемый файл Ghostscript независимо от системы."""
    for name in GHOSTSCRIPT_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "❌ Ghostscript не найден (искал: " + ", ".join(GHOSTSCRIPT_NAMES) + ").\n"
        "   macOS:   brew install ghostscript\n"
        "   Windows: winget install --id ArtifexSoftware.GhostScript\n"
        "            после установки перезапусти терминал, чтобы обновился PATH\n"
        "   Linux:   apt install ghostscript"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сжать PDF через Ghostscript")
    parser.add_argument("input", type=Path, help="Input PDF")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output compressed PDF")
    parser.add_argument("--dpi", type=int, default=160, help="Color/gray image resolution")
    parser.add_argument("--jpeg-quality", type=int, default=84, help="JPEG quality, 1-100")
    parser.add_argument(
        "--compatibility",
        default="1.6",
        help="PDF compatibility level, default 1.6",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        raise SystemExit(f"Input PDF not found: {input_path}")
    if input_path == output_path:
        raise SystemExit("Input and output paths must be different.")

    gs = find_ghostscript()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        gs,
        "-sDEVICE=pdfwrite",
        f"-dCompatibilityLevel={args.compatibility}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={args.dpi}",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={args.dpi}",
        "-dMonoImageDownsampleType=/Bicubic",
        "-dMonoImageResolution=300",
        "-dAutoFilterColorImages=false",
        "-dColorImageFilter=/DCTEncode",
        f"-dJPEGQ={args.jpeg_quality}",
        f"-sOutputFile={output_path}",
        str(input_path),
    ]

    result = run(command)
    if result.returncode != 0:
        if output_path.exists():
            output_path.unlink()
        raise SystemExit(result.stderr.strip() or "Ghostscript compression failed")

    before = file_size(input_path)
    after = file_size(output_path)
    ratio = after / before if before else 0
    print(f"Compressed PDF created: {output_path}")
    print(f"Before: {format_size(before)}")
    print(f"After: {format_size(after)}")
    print(f"Ratio: {ratio:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
