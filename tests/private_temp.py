"""A temp SQLite database in a folder no other test process shares (#346).

``PromptManagerAPI`` sweeps its database's folder at startup for the stages,
uploads and safety-backup copies an interrupted restore or backup left. A
fixture database directly in the system temp folder made every other test
process that started an API (another worktree's suite, a pre-push hook) delete
this process's in-flight restore files, and left safety backups behind there.
"""

import tempfile


def private_temp_db(case):
    """Return a closed ``NamedTemporaryFile`` handle for an empty ``.db`` file.

    The file lives in a new folder that ``case`` removes at cleanup, after its
    own teardown has closed and unlinked the database (cleanups run after
    ``tearDown``), together with anything a restore or backup left next to it.
    """
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(folder.cleanup)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=folder.name)
    handle.close()
    return handle
