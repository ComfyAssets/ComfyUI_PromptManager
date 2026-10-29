"""Library output-folder filter (#444): folder names, root prefixes and listing.

A folder is a ``/``-joined path relative to an output root, as Images' folder
filter and the legacy ``/prompt_manager/subfolders`` name them. A parent
matches a folder when one of its linked images (``generated_images.prompt_id``,
the link its previews and image count read) lies in that folder or below it,
under any spelling of any output root.

Matching compares ``generated_images.file_path``, which is always
``normcase(normpath(abspath(path)))`` (``models.normalize_image_path``), with
prefixes built by the same normalization here, so case folding is the
platform's own on both sides (Windows folds non-ASCII too, unlike SQLite's
``lower()``) and the comparison is an exact ``substr`` with no wildcards.
"""

import os
import sqlite3

_MAX_FOLDER = 1024
_MAX_PREFIXES = 64


def normalize_folder(value: object, pathmod=os.path) -> str:
    """The folder as ``a/b``, outer slashes dropped; empty means no filter.

    ``/`` always separates. ``\\`` separates only on Windows (``ntpath``): on
    POSIX it is an ordinary character of a folder's name, and the listing
    (``relative_folder``) names such a folder with it, so both agree.
    ``.``/``..``/empty segments, control characters and (on Windows) a drive
    are refused: the value only ever names a folder below a root.
    """
    if not isinstance(value, str) or len(value) > _MAX_FOLDER:
        raise ValueError(f"folder must be text of at most {_MAX_FOLDER} characters")
    windows = pathmod.sep == "\\"
    folder = (value.replace("\\", "/") if windows else value).strip("/")
    if not folder:
        return ""
    parts = folder.split("/")
    if (
        any(part in ("", ".", "..") for part in parts)
        or (windows and ":" in parts[0])
        or any(ord(char) < 32 or ord(char) == 127 for char in folder)
    ):
        raise ValueError("folder must be a folder below an output root")
    return folder


def _base(root: object, pathmod) -> str:
    return pathmod.normcase(pathmod.normpath(str(root)))


def folder_prefixes(roots, folder: str, pathmod=os.path) -> list[str]:
    """One ``file_path`` prefix per root spelling for a normalized folder."""
    prefixes = []
    for root in roots:
        joined = pathmod.join(_base(root, pathmod), *folder.split("/"))
        prefix = pathmod.normcase(pathmod.normpath(joined)) + pathmod.sep
        if prefix not in prefixes:
            prefixes.append(prefix)
    if not prefixes or len(prefixes) > _MAX_PREFIXES:
        raise ValueError("No output folder to filter by")
    return prefixes


def folder_condition(prefixes: list[str]) -> tuple[str, list]:
    """A condition on alias ``p``: one linked image under any prefix."""
    tests = " OR ".join("substr(fg.file_path,1,?)=?" for _ in prefixes)
    params = [value for prefix in prefixes for value in (len(prefix), prefix)]
    return (
        "EXISTS(SELECT 1 FROM generated_images fg WHERE fg.prompt_id=p.id "
        f"AND ({tests}))",
        params,
    )


def relative_folder(image_path: str, roots, pathmod=os.path) -> str | None:
    """The folder of *image_path* below the first root spelling holding it.

    The spelling of the stored path is kept (its case on Windows); ``None``
    for an image directly in a root or outside every root.
    """
    directory = pathmod.normpath(pathmod.dirname(image_path))
    folded = pathmod.normcase(directory)
    for root in roots:
        base = _base(root, pathmod)
        start = base if base.endswith(pathmod.sep) else base + pathmod.sep
        if not folded.startswith(start):
            continue
        # normcase keeps the length for ordinary names; else show the folded form.
        rest = (directory if len(directory) == len(folded) else folded)[len(start) :]
        parts = rest.split(pathmod.sep)
        if any(part in ("", ".", "..") for part in parts):
            return None
        return "/".join(parts)
    return None


def list_library_folders(
    conn: sqlite3.Connection, roots, eligible: str, pathmod=os.path
) -> list[str]:
    """Every folder (and ancestor) holding an image of an eligible parent."""
    folders = set()
    for (image_path,) in conn.execute(
        "SELECT DISTINCT g.image_path FROM generated_images g "
        "JOIN prompts p ON p.id=g.prompt_id WHERE " + eligible
    ):
        if not isinstance(image_path, str) or not image_path:
            continue
        folder = relative_folder(image_path, roots, pathmod)
        if folder is None:
            continue
        parts = folder.split("/")
        folders.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return sorted(folders, key=lambda value: (value.casefold(), value))
