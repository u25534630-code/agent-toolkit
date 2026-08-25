#!/usr/bin/env python3
"""
Постраничный QA готового PDF: рендер в PNG и поиск проблем вёрстки.

The script does two things:
1. Renders every PDF page to PNG for human/agent visual review.
2. Builds a text report with suspicious pagination patterns.

It is intentionally conservative: warnings are a review queue, not an
automatic verdict that the PDF is broken.
"""

from __future__ import annotations

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml",
# ]
# ///

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import brand as brand_config

PAGE_NUMBER_RE = re.compile(r"стр\.\s*\d+\s+из\s+\d+", re.IGNORECASE)
SECTION_RE = re.compile(r"^РАЗДЕЛ\s*/\s*\d+", re.IGNORECASE)
LOWERCASE_START_RE = re.compile(r"^[а-яa-zё]")

# Заполняются в configure_brand(): что именно считать колонтитулом и
# финальным CTA, зависит от бренда, а не от кода.
BRAND: dict = dict(brand_config.DEFAULT_BRAND)
RUNNING_HEADER_RE = re.compile(r"^$")
TAIL_BLOCK_RE = re.compile(r"^(Сложность клонирования|©)$", re.IGNORECASE)


def configure_brand(explicit=None, project_dir=None) -> dict:
    """Подставить бренд в шаблоны, по которым QA узнаёт служебные строки."""
    global BRAND, RUNNING_HEADER_RE, TAIL_BLOCK_RE
    BRAND = brand_config.load_brand(explicit, project_dir)

    name = re.escape(BRAND["name"])
    tagline = BRAND.get("tagline") or ""
    if tagline:
        RUNNING_HEADER_RE = re.compile(
            rf"^{name}\s*/\s*{re.escape(tagline)}$", re.IGNORECASE
        )
    else:
        RUNNING_HEADER_RE = re.compile(rf"^{name}$", re.IGNORECASE)

    tail_parts = ["Сложность клонирования", "©"]
    if BRAND.get("site"):
        tail_parts.append(re.escape(BRAND["site"]))
    TAIL_BLOCK_RE = re.compile(rf"^({'|'.join(tail_parts)})$", re.IGNORECASE)
    return BRAND


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    sample: str = ""


@dataclass
class PageReport:
    page: int
    png: Path | None = None
    text_lines: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_binary(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise SystemExit(
            f"❌ Не найдена программа {name} — она входит в poppler.\n"
            "   macOS:   brew install poppler\n"
            "   Windows: скачай сборку poppler для Windows и добавь её папку bin в PATH\n"
            "   Linux:   apt install poppler-utils"
        )
    return binary


def page_count(pdf_path: Path) -> int:
    require_binary("pdfinfo")
    result = run(["pdfinfo", str(pdf_path)])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "pdfinfo failed")
    match = re.search(r"^Pages:\s+(\d+)$", result.stdout, re.MULTILINE)
    if not match:
        raise SystemExit("Could not read page count from pdfinfo output")
    return int(match.group(1))


def render_pages(pdf_path: Path, output_dir: Path, dpi: int) -> dict[int, Path]:
    require_binary("pdftoppm")
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    result = run(["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "pdftoppm failed")

    rendered: dict[int, Path] = {}
    for path in sorted(output_dir.glob("page-*.png")):
        match = re.search(r"page-(\d+)\.png$", path.name)
        if match:
            rendered[int(match.group(1))] = path
    return rendered


def extract_page_text(pdf_path: Path, page: int) -> str:
    require_binary("pdftotext")
    result = run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf_path), "-"])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or f"pdftotext failed on page {page}")
    return result.stdout


def meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if RUNNING_HEADER_RE.match(line):
            continue
        site = BRAND.get("site") or ""
        if site and line == site:
            continue
        if PAGE_NUMBER_RE.search(line) and (
            (site and site in line) or line.startswith("стр.")
        ):
            continue
        lines.append(re.sub(r"\s+", " ", line))
    return lines


def is_probably_heading(line: str) -> bool:
    if SECTION_RE.match(line):
        return True
    if len(line) <= 80 and not line.endswith((".", ":", ";", "»", ")", "?", "!")):
        uppercase_count = sum(1 for char in line if char.isupper())
        letters = sum(1 for char in line if char.isalpha())
        return bool(letters and uppercase_count / max(letters, 1) > 0.5)
    return False


def inspect_page(page: int, lines: list[str], total_pages: int) -> list[Issue]:
    issues: list[Issue] = []
    if page == 1:
        return issues

    if not lines:
        issues.append(Issue("error", "blank-page", "Page has no extracted text."))
        return issues

    first = lines[0]
    if TAIL_BLOCK_RE.match(first):
        issues.append(
            Issue(
                "error",
                "tail-at-top",
                "A tail block starts the page; it should stay with the previous content.",
                first,
            )
        )
    elif not first.startswith(("http://", "https://")) and LOWERCASE_START_RE.match(first):
        issues.append(
            Issue(
                "warn",
                "continuation-at-top",
                "Page starts with a lowercase continuation; visually inspect for an orphaned sentence.",
                first,
            )
        )

    first_section_index = next((index for index, line in enumerate(lines) if SECTION_RE.match(line)), None)
    if first_section_index and first_section_index > 0:
        carryover = lines[:first_section_index]
        if len(carryover) <= 2:
            issues.append(
                Issue(
                    "error",
                    "short-carryover-before-section",
                    "Only 1-2 carryover lines appear before a new section.",
                    " / ".join(carryover),
                )
            )
        elif len(carryover) <= 5:
            issues.append(
                Issue(
                    "warn",
                    "carryover-before-section",
                    "Carryover text appears before a new section; inspect whether it looks intentional.",
                    " / ".join(carryover[:3]),
                )
            )

    last_section_index = max((index for index, line in enumerate(lines) if SECTION_RE.match(line)), default=None)
    if last_section_index is not None:
        lines_after_section = len(lines) - last_section_index - 1
        if lines_after_section <= 2:
            issues.append(
                Issue(
                    "error",
                    "heading-at-bottom",
                    "A section marker or heading is near the bottom without enough following content.",
                    " / ".join(lines[last_section_index:]),
                )
            )

    if len(lines) <= 3 and page not in {1, total_pages}:
        issues.append(
            Issue(
                "warn",
                "sparse-page",
                "Page has very little text; inspect for unintended whitespace.",
                " / ".join(lines),
            )
        )

    if page == total_pages:
        platform_prefix = BRAND["cta"].get("platform_heading_prefix") or ""
        site = BRAND.get("site") or ""
        has_platform = bool(platform_prefix) and any(
            platform_prefix in line for line in lines
        )
        has_link = bool(site) and any(site in line for line in lines)
        if has_link and not has_platform:
            issues.append(
                Issue(
                    "error",
                    "cta-link-alone",
                    "Final CTA link appears without the platform section on the same page.",
                    " / ".join(lines[:4]),
                )
            )

    return issues


def write_report(pdf_path: Path, output_dir: Path, reports: list[PageReport]) -> Path:
    report_path = output_dir / "qa-report.md"
    issue_count = sum(len(report.issues) for report in reports)
    error_count = sum(1 for report in reports for issue in report.issues if issue.severity == "error")
    warn_count = issue_count - error_count

    lines = [
        f"# PDF QA Report: {pdf_path.name}",
        "",
        f"- Pages checked: {len(reports)}",
        f"- Errors: {error_count}",
        f"- Warnings: {warn_count}",
        f"- Rendered PNG folder: `{output_dir}`",
        "",
    ]

    if issue_count:
        lines.extend(["## Issues", ""])
        lines.append("| Page | Severity | Code | Message | Sample | PNG |")
        lines.append("|---:|---|---|---|---|---|")
        for report in reports:
            for issue in report.issues:
                sample = issue.sample.replace("|", "\\|")
                png = report.png.name if report.png else ""
                lines.append(
                    f"| {report.page} | {issue.severity} | `{issue.code}` | "
                    f"{issue.message} | {sample} | `{png}` |"
                )
        lines.append("")
    else:
        lines.extend(["No text-pattern issues found.", ""])

    lines.extend(["## Page Review Checklist", ""])
    lines.extend(
        [
            "- Open every rendered PNG, not only pages listed above.",
            "- Check that no heading is detached from its first paragraph.",
            "- Check that no single tail line starts a page.",
            "- Check that images, tables, callouts, and prompt blocks are not split awkwardly.",
            "- Check that the final CTA page looks intentional.",
            "",
        ]
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Постраничный QA готового PDF")
    parser.add_argument("pdf", type=Path, help="Path to PDF")
    parser.add_argument("-o", "--output-dir", type=Path, help="QA output directory")
    parser.add_argument("--dpi", type=int, default=110, help="PNG render DPI")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if errors are found")
    brand_config.add_brand_argument(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    # PDF лежит в <проект>/output/, поэтому бренд ищем от папки проекта.
    configure_brand(getattr(args, "brand", None), pdf_path.parent.parent)

    output_dir = args.output_dir or pdf_path.parent / "qa-preview"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_pages = page_count(pdf_path)
    rendered = render_pages(pdf_path, output_dir, args.dpi)

    reports: list[PageReport] = []
    for page in range(1, total_pages + 1):
        text = extract_page_text(pdf_path, page)
        lines = meaningful_lines(text)
        reports.append(
            PageReport(
                page=page,
                png=rendered.get(page),
                text_lines=lines,
                issues=inspect_page(page, lines, total_pages),
            )
        )

    report_path = write_report(pdf_path, output_dir, reports)
    error_count = sum(1 for report in reports for issue in report.issues if issue.severity == "error")
    warn_count = sum(1 for report in reports for issue in report.issues if issue.severity == "warn")

    print(f"QA report: {report_path}")
    print(f"Rendered pages: {output_dir}")
    print(f"Errors: {error_count}")
    print(f"Warnings: {warn_count}")

    if args.strict and error_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
