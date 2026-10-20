"""Ingestion keeps the default resolver profile; only inspection routes moved (D1).

The live monitor and the admin scan write pairs and links to the user's
database, so their resolver semantics stay byte-for-byte today's. f03 (506
executable nodes) separates the profiles: the default profile's 500-node gate
leaves it unresolved, while the inspection profile resolves it.
"""

import logging
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from utils import prompt_pair_resolver as resolver

try:
    from tests import inspection_parity as parity
except ImportError:  # pragma: no cover - discover -s tests puts tests/ first
    import inspection_parity as parity

GATED = "Resolution limit reached"


def _f03_metadata():
    return {"prompt": parity.build("f03")["prompt"]}


class _Spy:
    """Wrap the real resolver, recording each call's profile and outcome."""

    def __init__(self):
        self.calls = []

    def __call__(self, metadata, **kwargs):
        result = resolver.resolve_prompt_pair(metadata, **kwargs)
        self.calls.append((kwargs.get("profile", resolver.DEFAULT_PROFILE), result))
        return result

    def assert_default_and_gated(self, case):
        case.assertTrue(self.calls, "the ingestion path resolved the metadata")
        for profile, result in self.calls:
            case.assertIs(profile, resolver.DEFAULT_PROFILE)
            case.assertEqual((result["state"], result["reason"]), ("unresolved", GATED))


class TestIngestionKeepsDefaultProfile(unittest.TestCase):
    def test_f03_separates_the_profiles(self):
        metadata = _f03_metadata()
        default = resolver.resolve_prompt_pair(metadata)
        self.assertEqual((default["state"], default["reason"]), ("unresolved", GATED))
        inspection = resolver.resolve_prompt_pair(
            metadata, profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(inspection["state"], "resolved")

    def test_live_monitor_lookup_and_linking_use_the_default_profile(self):
        from utils.image_monitor import ImageGenerationHandler

        handler = ImageGenerationHandler.__new__(ImageGenerationHandler)
        handler.db_manager = Mock()
        handler.db_manager.get_prompt_by_hash.return_value = None
        handler.logger = logging.getLogger("test.ingestion-profile")
        spy, metadata = _Spy(), _f03_metadata()
        with patch("utils.image_monitor.resolve_prompt_pair", spy):
            handler._find_prompt_from_metadata(metadata)
            handler.link_image_to_prompt("/out/f03.png", {"id": 1}, metadata)
        self.assertEqual(len(spy.calls), 2)
        spy.assert_default_and_gated(self)
        handler.db_manager.link_image_to_prompt.assert_called_once()

    def test_admin_scan_ingestion_uses_the_default_profile(self):
        from py.api import admin

        mixin = admin.AdminRoutesMixin.__new__(admin.AdminRoutesMixin)
        mixin.db = Mock()
        mixin.db.get_prompt_by_hash.return_value = None
        mixin.db.save_prompt.return_value = 7
        spy = _Spy()
        with patch.object(admin, "resolve_prompt_pair", spy):
            status = mixin._ingest_scanned_file(Path("/out/f03.png"), _f03_metadata())
        spy.assert_default_and_gated(self)
        # The resolver leaves f03 unresolved; the scan links the prompt the
        # browser parser traces, as the inspector shows it.
        self.assertEqual(status, "added")
        self.assertEqual(
            mixin.db.save_prompt.call_args.args[0], "a red fox in snow, detailed fur"
        )
        mixin.db.link_image_to_prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
