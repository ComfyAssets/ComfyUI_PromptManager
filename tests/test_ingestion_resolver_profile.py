"""Ingestion decides with the default resolver profile first (D1, #247 D-Q1).

The live monitor and the admin scan write pairs and links to the user's
database, so the default profile decides every file it can, byte-for-byte as
before. f03 (506 executable nodes) separates the profiles: the default
profile's 500-node gate leaves it unresolved, while the inspection profile
resolves it. Only such an undecided file reaches the inspector's resolve
(``inspection_resolution``), and only its positive is stored (#247).
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
        handler.db_manager.link_image_to_prompt_or_raise.assert_called_once()

    def test_admin_scan_ingestion_uses_the_default_profile_first(self):
        from py.api import admin
        from utils import file_prompt

        mixin = admin.AdminRoutesMixin.__new__(admin.AdminRoutesMixin)
        mixin.db = Mock()
        spy = _Spy()
        with (
            patch.object(file_prompt, "resolve_prompt_pair", spy),
            patch.object(
                admin, "store_scanned_prompt", return_value=("added", 7)
            ) as store,
        ):
            status = mixin._ingest_scanned_file(Path("/out/f03.png"), _f03_metadata())
        spy.assert_default_and_gated(self)
        # The default profile leaves f03 unresolved; the inspector's resolve
        # names the prompt the inspector shows, and the scan stores it.
        self.assertEqual(status, "added")
        self.assertEqual(store.call_args.args[3], "a red fox in snow, detailed fur")
        mixin.db.link_image_to_prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
