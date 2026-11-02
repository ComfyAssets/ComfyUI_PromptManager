"""Every read-only SQLite open uses one URI helper (#620).

pathlib writes a Windows network path as ``file://server/share/x.db``, which
SQLite refuses. A reader that builds its own URI breaks backups, restores,
library moves or history reads for a library on a network share.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database.sqlite_uri import readonly_uri  # noqa: E402


class TestReadonlyUri(unittest.TestCase):
    def test_a_network_path_has_an_empty_authority(self):
        self.assertEqual(
            readonly_uri(r"\\server\share\Comfy UI\prompts.db", windows=True),
            "file:////server/share/Comfy%20UI/prompts.db?mode=ro",
        )

    def test_no_reader_builds_its_own_uri(self):
        offenders = []
        for folder in ("database", "py", "utils"):
            for path in (ROOT / folder).rglob("*.py"):
                if path.name == "sqlite_uri.py":
                    continue
                text = path.read_text(encoding="utf-8")
                if re.search(r"as_uri\(\)\s*\+\s*[\"']\?mode=ro", text):
                    offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
