"""A fake system trash for tests (#480): renames into a folder outside every root.

``install(folder)`` replaces ``utils.trash.BACKEND`` and returns the fake;
``uninstall`` puts the real one back. ``refuse[path] = code`` refuses one
file with that code. The fake never copies or unlinks; it records each call.
The disposable browser server installs it when ``PM_TEST_FAKE_TRASH=1``, so
no test touches a real trash.
"""

import os

from utils import trash


class FakeTrash:
    def __init__(self, folder):
        self.folder = str(folder)
        os.makedirs(self.folder, exist_ok=True)
        self.refuse = {}
        self.moved = []  # (source, destination)
        self.calls = []

    def available(self, path, roots):
        self.calls.append(("available", str(path)))
        return self.refuse.get(str(path))

    def move(self, path, roots):
        self.calls.append(("move", str(path)))
        code = self.refuse.get(str(path))
        if code:
            raise trash.TrashRefused(code)
        destination = os.path.join(
            self.folder, f"{len(self.moved)}-{os.path.basename(path)}"
        )
        if os.path.lexists(destination):
            raise trash.TrashRefused(trash.FAILED)
        os.rename(path, destination)
        self.moved.append((str(path), destination))
        return "fake trash"


_ORIGINAL = trash.BACKEND


def install(folder):
    fake = FakeTrash(folder)
    trash.BACKEND = fake
    return fake


def uninstall():
    trash.BACKEND = _ORIGINAL
