"""
UI utilities for agent output rendering.

This module handles rich markdown rendering including tables.
"""

import re

# Optional: rich for markdown rendering
try:
    from rich.console import Console, Group
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
    console = Console()

    def render_cell(cell: str) -> Text:
        """Render markdown formatting in a table cell."""
        # Pre-process: convert <br>, <br/>, <br /> to newlines
        cell = re.sub(r'<br\s*/?>', '\n', cell, flags=re.IGNORECASE)
        text = Text()
        i = 0
        while i < len(cell):
            # Bold: **text**
            if cell[i:i+2] == '**':
                end = cell.find('**', i + 2)
                if end != -1:
                    text.append(cell[i+2:end], style="bold")
                    i = end + 2
                    continue
            # Code: `text`
            if cell[i] == '`':
                end = cell.find('`', i + 1)
                if end != -1:
                    text.append(cell[i+1:end], style="cyan")
                    i = end + 1
                    continue
            # Regular character
            text.append(cell[i])
            i += 1
        return text

    def fix_markdown_tables(content: str) -> str:
        """Fix markdown tables that have rows collapsed onto single lines."""
        # Fix: | ... | |--- (header followed by separator on same line)
        content = re.sub(r'\|\s*\|(\s*-+\s*\|)', r'|\n|\1', content)
        # Fix: | ... | | ... | (data rows concatenated)
        content = re.sub(r'\|\s*\|\s*(?=[^-\s\n])', '|\n| ', content)
        return content

    def parse_markdown_table(table_text: str) -> tuple[list[str], list[list[str]]]:
        """Parse a markdown table into headers and rows."""
        lines = [l.strip() for l in table_text.strip().split('\n') if l.strip()]
        if len(lines) < 2:
            return [], []

        def parse_row(line: str) -> list[str]:
            # Remove leading/trailing pipes and split
            line = line.strip()
            if line.startswith('|'):
                line = line[1:]
            if line.endswith('|'):
                line = line[:-1]
            return [cell.strip() for cell in line.split('|')]

        headers = parse_row(lines[0])
        rows = []

        for line in lines[1:]:
            # Skip separator line
            if re.match(r'^[\|\s\-:]+$', line):
                continue
            rows.append(parse_row(line))

        return headers, rows

    def render_content_with_tables(content: str) -> Group:
        """Render content, converting markdown tables to rich Tables."""
        content = fix_markdown_tables(content)

        # Pattern to match markdown tables (header + separator + rows)
        table_pattern = re.compile(
            r'(\|[^\n]+\|\s*\n\|[\s\-:|]+\|\s*\n(?:\|[^\n]+\|\s*\n?)+)',
            re.MULTILINE
        )

        parts = []
        last_end = 0

        for match in table_pattern.finditer(content):
            # Add text before the table
            before = content[last_end:match.start()].strip()
            if before:
                parts.append(Markdown(before))

            # Parse and render the table
            headers, rows = parse_markdown_table(match.group(1))
            if headers:
                table = Table(show_header=True, header_style="bold")
                for header in headers:
                    table.add_column(header)
                for row in rows:
                    # Pad row if needed
                    while len(row) < len(headers):
                        row.append("")
                    # Render markdown in cells
                    rendered = [render_cell(cell) for cell in row[:len(headers)]]
                    table.add_row(*rendered)
                parts.append(table)

            last_end = match.end()

        # Add remaining text after last table
        after = content[last_end:].strip()
        if after:
            parts.append(Markdown(after))

        return Group(*parts) if parts else Markdown(content)

except ImportError:
    RICH_AVAILABLE = False
    console = None
    render_cell = None
    fix_markdown_tables = None
    parse_markdown_table = None
    render_content_with_tables = None


def print_response(content: str) -> None:
    """Print agent response with optional rich formatting."""
    if RICH_AVAILABLE and console:
        try:
            rendered = render_content_with_tables(content)
            console.print(rendered)
        except Exception:
            # Fallback to plain print if rich rendering fails
            print(content)
    else:
        print(content)
