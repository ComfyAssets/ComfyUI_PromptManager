"""API fixtures never open the configured database before installing test state."""

import unittest.mock

from py.api import PromptManagerAPI


class TestAPIDatabaseIsolation(unittest.TestCase):
    def test_injected_database_never_constructs_the_configured_database(self):
        database = unittest.mock.MagicMock()
        database.cleanup_duplicates.return_value = 0
        with (
            unittest.mock.patch(
                "py.api.PromptDatabase", side_effect=AssertionError("default DB opened")
            ) as factory,
            unittest.mock.patch("py.api.get_logger"),
        ):
            api = PromptManagerAPI(db=database)
        self.assertIs(api.db, database)
        factory.assert_not_called()
        database.cleanup_duplicates.assert_called_once_with()

    def test_falsey_injected_database_is_still_used(self):
        database = unittest.mock.MagicMock()
        database.__bool__.return_value = False
        database.cleanup_duplicates.return_value = 0
        with (
            unittest.mock.patch(
                "py.api.PromptDatabase", side_effect=AssertionError("default DB opened")
            ) as factory,
            unittest.mock.patch("py.api.get_logger"),
        ):
            api = PromptManagerAPI(db=database)
        self.assertIs(api.db, database)
        factory.assert_not_called()

    def test_default_runtime_construction_still_uses_the_configured_factory(self):
        database = unittest.mock.MagicMock()
        database.cleanup_duplicates.return_value = 2
        with (
            unittest.mock.patch(
                "py.api.PromptDatabase", return_value=database
            ) as factory,
            unittest.mock.patch("py.api.get_logger") as logger,
        ):
            api = PromptManagerAPI()
        self.assertIs(api.db, database)
        factory.assert_called_once_with()
        logger.return_value.info.assert_any_call(
            "Startup cleanup: removed 2 duplicate prompts"
        )

    def test_injected_cleanup_failure_is_logged_without_falling_back_to_default(self):
        database = unittest.mock.MagicMock()
        database.cleanup_duplicates.side_effect = RuntimeError("test cleanup")
        with (
            unittest.mock.patch(
                "py.api.PromptDatabase", side_effect=AssertionError("default DB opened")
            ) as factory,
            unittest.mock.patch("py.api.get_logger") as logger,
        ):
            api = PromptManagerAPI(db=database)
        self.assertIs(api.db, database)
        factory.assert_not_called()
        logger.return_value.error.assert_called_once_with(
            "Startup cleanup failed: test cleanup"
        )


if __name__ == "__main__":
    unittest.main()
