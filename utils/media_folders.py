"""The folders no media walker lists below an output root (#501).

``thumbnails`` holds PromptManager's own previews. A trash folder that sits
inside an output root (``D:\\$RECYCLE.BIN`` when ``D:\\`` is a root,
``.Trash-1000`` at a mount point, ``.Trashes`` on a macOS volume) holds files
the user threw away: they are not outputs, so they are never listed, scanned,
linked, tagged or previewed. Every walker asks these predicates instead of
testing names itself; only folders *below* a root are tested.
"""

TRASH_FOLDER_NAMES = frozenset({".trash", ".trashes", "$recycle.bin"})
PREVIEW_FOLDER = "thumbnails"


def is_trash_folder(name: str) -> bool:
    """A trash folder name, in any case: ``.Trash``, ``.Trash-<uid>``,
    ``.Trashes`` or ``$RECYCLE.BIN``."""
    folded = name.casefold()
    if folded in TRASH_FOLDER_NAMES:
        return True
    return folded.startswith(".trash-") and folded[7:].isdigit()


def is_skipped_folder(name: str) -> bool:
    """A folder no walker descends: ``thumbnails`` (exact) or a trash folder."""
    return name == PREVIEW_FOLDER or is_trash_folder(name)


def in_trash_folder(relative: str) -> bool:
    """Whether a root-relative file path lies in a trash folder (#543).

    Takes either separator. Previews of it (``thumbnails/.Trash-1000/...``) count.
    """
    folders = relative.replace("\\", "/").split("/")[:-1]
    return any(is_trash_folder(part) for part in folders)


def in_skipped_folder(parts) -> bool:
    """Whether any of the folder names *parts* is skipped."""
    return any(is_skipped_folder(part) for part in parts)
