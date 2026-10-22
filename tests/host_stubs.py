"""One stand-in for ComfyUI's ``folder_paths`` module, shared by every test.

Test modules that import the API standalone need a ``folder_paths`` module.
It must be a real module, not a ``MagicMock``: ``patch("folder_paths.x",
create=True)`` then adds and removes plain attributes, while a mock would
hand a later test an auto-created child (or lose the attribute on exit), so
the outcome depended on which test module discovery imported first.
"""

import sys
import tempfile
import types


def install_folder_paths_stub() -> types.ModuleType:
    """Install the shared ``folder_paths`` stub unless a real module is there.

    A test that installs its own stand-in for a while (and restores it) is
    left alone; a ``MagicMock`` left behind by older code is replaced.
    """
    current = sys.modules.get("folder_paths")
    if isinstance(current, types.ModuleType):
        return current
    stub = types.ModuleType("folder_paths")
    stub.models_dir = tempfile.gettempdir()
    stub.base_path = tempfile.gettempdir()
    stub.get_output_directory = tempfile.gettempdir
    sys.modules["folder_paths"] = stub
    return stub
