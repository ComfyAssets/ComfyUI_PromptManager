"""The read-only SQLite URI of a path, safe for Windows network shares."""

import ntpath
import os
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import quote


def readonly_uri(db_path, *, windows=None):
    """The ``mode=ro`` SQLite URI of *db_path*, on this platform or the given one.

    pathlib spells a UNC path ``\\\\server\\share\\x.db`` as
    ``file://server/share/x.db``, a remote authority SQLite refuses to open.
    With an empty authority (``file:////server/share/x.db``) SQLite hands
    Windows the ``//server/share`` path, which it opens as the UNC path.
    """
    if windows is None:
        windows = os.name == "nt"
        db_path = os.path.abspath(db_path)
    if not windows:
        return PurePosixPath(db_path).as_uri() + "?mode=ro"
    path = PureWindowsPath(db_path)
    if ntpath.splitdrive(str(path))[0].startswith("\\\\"):
        return "file:" + quote("//" + path.as_posix()) + "?mode=ro"
    return path.as_uri() + "?mode=ro"
