#!/usr/bin/env python3
"""
Собрать PDF из HTML лид-магнита или напрямую из Markdown.
Рендер — WeasyPrint: корректная кириллица и кликабельные ссылки.

Канонический маршрут — HTML, собранный build_html.py.
Прямой маршрут из Markdown оставлен для быстрых черновиков: он не знает
про бренд и собирается системными шрифтами.

Usage:
    uv run scripts/build_pdf.py <project>/output/<slug>.html
    uv run scripts/build_pdf.py <project>/content.md
    uv run scripts/build_pdf.py content.md -o output.pdf
"""

import argparse
import os
import sys
from pathlib import Path

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "weasyprint",
#     "markdown",
# ]
# ///


def get_mobile_optimized_css():
    """
    CSS optimized for mobile reading:
    - Larger font size (16px base)
    - Minimal margins (1cm)
    - Good line height
    - Neutral colors (black/gray), blue only for quotes
    - Prompts don't break across pages
    """
    return """

        @page {
            size: A4;
            margin: 1cm;
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            font-size: 16px;
            line-height: 1.7;
            color: #1f2937;
            padding: 0;
            margin: 0;
        }

        /* Main title */
        h1 {
            font-size: 32px;
            font-weight: 700;
            color: #0f172a;
            margin: 0 0 24px 0;
            line-height: 1.2;
            text-align: center;
        }

        /* Section headers - neutral dark */
        h2 {
            font-size: 24px;
            font-weight: 700;
            color: #0f172a;
            margin: 48px 0 20px 0;
            padding-bottom: 12px;
            border-bottom: 2px solid #e5e7eb;
            page-break-after: avoid;
        }

        /* Subsection headers */
        h3 {
            font-size: 20px;
            font-weight: 600;
            color: #1f2937;
            margin: 32px 0 16px 0;
            page-break-after: avoid;
        }

        h4 {
            font-size: 18px;
            font-weight: 600;
            color: #374151;
            margin: 24px 0 12px 0;
        }

        p {
            margin: 0 0 16px 0;
            orphans: 3;
            widows: 3;
        }

        /* Lists with good spacing */
        ul, ol {
            margin: 0 0 20px 0;
            padding-left: 24px;
        }

        li {
            margin-bottom: 12px;
            line-height: 1.6;
        }

        li > ul, li > ol {
            margin-top: 8px;
            margin-bottom: 8px;
        }

        /* Blockquotes - blue accent for tips/notes */
        blockquote {
            background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
            border-left: 5px solid #2563EB;
            margin: 24px 0;
            padding: 20px 24px;
            border-radius: 0 12px 12px 0;
            font-size: 15px;
            page-break-inside: avoid;
        }

        blockquote p {
            margin: 0;
        }

        blockquote p + p {
            margin-top: 12px;
        }

        /* Tables - neutral style */
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 24px 0;
            font-size: 15px;
            page-break-inside: avoid;
        }

        th {
            background: #f1f5f9;
            color: #0f172a;
            font-weight: 600;
            text-align: left;
            padding: 14px 16px;
            border-bottom: 2px solid #e5e7eb;
        }

        td {
            border-bottom: 1px solid #e5e7eb;
            padding: 14px 16px;
            vertical-align: top;
        }

        tr:nth-child(even) {
            background: #f8fafc;
        }

        /* Inline code */
        code {
            background: #f1f5f9;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 14px;
            font-family: 'SF Mono', Monaco, 'Courier New', monospace;
        }

        /* Code blocks / Prompts - light background, easy to copy */
        pre {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            color: #1e293b;
            padding: 20px;
            border-radius: 12px;
            overflow-x: auto;
            font-size: 15px;
            line-height: 1.6;
            margin: 20px 0;
            page-break-inside: avoid;
            white-space: pre-wrap;
            word-wrap: break-word;
        }

        pre code {
            background: transparent;
            padding: 0;
            color: inherit;
            font-size: 15px;
        }

        /* Horizontal rules */
        hr {
            border: none;
            border-top: 1px solid #e5e7eb;
            margin: 40px 0;
        }

        /* Links - subtle dark blue */
        a {
            color: #1d4ed8;
            text-decoration: none;
            font-weight: 500;
        }

        /* Strong text */
        strong {
            font-weight: 600;
            color: #0f172a;
        }

        /* Avoid orphaned headers */
        h1, h2, h3, h4 {
            page-break-after: avoid;
        }

        /* Keep lists together when possible */
        ul, ol {
            page-break-inside: avoid;
        }
    """


def html_to_pdf(input_path: str, output_path: str = None):
    """Convert a standalone HTML file to PDF."""
    from weasyprint import HTML

    input_file = Path(input_path)

    if not input_file.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    if output_path is None:
        output_path = input_file.with_suffix(".pdf")
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    HTML(filename=str(input_file)).write_pdf(str(output_path))

    file_size = output_path.stat().st_size / 1024
    print(f"✅ PDF created: {output_path}")
    print(f"   Size: {file_size:.1f} KB")

    return str(output_path)


def markdown_to_pdf(input_path: str, output_path: str = None):
    """Convert markdown file to mobile-optimized PDF."""
    import markdown
    from weasyprint import HTML

    input_file = Path(input_path)

    if not input_file.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    # Determine output path
    if output_path is None:
        output_dir = input_file.parent / "output"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"{input_file.stem}.pdf"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # Read markdown
    with open(input_file, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # Convert to HTML with extensions
    html_content = markdown.markdown(
        md_content,
        extensions=['tables', 'fenced_code', 'nl2br']
    )

    # Create full HTML document
    full_html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        {get_mobile_optimized_css()}
    </style>
</head>
<body>
    {html_content}
</body>
</html>'''

    # Generate PDF
    HTML(string=full_html).write_pdf(str(output_path))

    # Report results
    file_size = output_path.stat().st_size / 1024
    print(f"✅ PDF created: {output_path}")
    print(f"   Size: {file_size:.1f} KB")

    return str(output_path)


def main():
    parser = argparse.ArgumentParser(
        description='Generate PDF from branded HTML or legacy Markdown'
    )
    parser.add_argument(
        'input',
        help='Input HTML or Markdown file path'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output PDF file path (default: output/[filename].pdf)'
    )

    args = parser.parse_args()
    input_file = Path(args.input)
    if input_file.suffix.lower() in {'.html', '.htm'}:
        html_to_pdf(args.input, args.output)
    else:
        markdown_to_pdf(args.input, args.output)


if __name__ == '__main__':
    main()
