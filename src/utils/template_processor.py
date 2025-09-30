"""Template processor for HTML cache busting.

Simple template processor that injects cache-busting version parameters
into script and stylesheet URLs in HTML files.
"""

import re
import time
from pathlib import Path
from typing import Optional


class TemplateProcessor:
    """Process HTML templates to add cache-busting version parameters."""

    def __init__(self, version: Optional[str] = None):
        """Initialize processor with version string.

        Args:
            version: Version string to use. Defaults to current timestamp.
        """
        self.version = version or str(int(time.time()))

    def process_file(self, html_path: Path) -> str:
        """Process HTML file and return content with versioned URLs.

        Args:
            html_path: Path to HTML file

        Returns:
            Processed HTML content with version parameters added
        """
        content = html_path.read_text(encoding='utf-8')
        return self.process_content(content)

    def process_content(self, html: str) -> str:
        """Process HTML content and add version parameters to local resources.

        Args:
            html: HTML content string

        Returns:
            Processed HTML with version parameters
        """
        # Add version to <script src="/prompt_manager/...">
        html = re.sub(
            r'(<script[^>]+src=")(/prompt_manager/[^"?]+)("[^>]*>)',
            rf'\1\2?v={self.version}\3',
            html
        )

        # Add version to <link rel="stylesheet" href="/prompt_manager/...">
        html = re.sub(
            r'(<link[^>]+href=")(/prompt_manager/[^"?]+\.css)("[^>]*>)',
            rf'\1\2?v={self.version}\3',
            html
        )

        return html


def get_cache_version() -> str:
    """Get current cache busting version.

    Returns timestamp in dev, or PROMPTMANAGER_VERSION env var if set.
    """
    import os
    return os.environ.get('PROMPTMANAGER_VERSION', str(int(time.time())))
