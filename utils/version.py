"""The project version, read from ``pyproject.toml`` (#329)."""

import re
from pathlib import Path


def project_version(root):
    """The version in ``root``'s ``pyproject.toml``; ``"unknown"`` on failure."""
    try:
        pyproject_path = Path(root) / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                return match.group(1)
    except Exception:  # nosec B110 - an unreadable file reads as unknown
        pass
    return "unknown"
