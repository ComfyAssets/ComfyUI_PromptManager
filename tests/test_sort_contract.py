"""The node's Recent request must name a sort the server whitelists."""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database.operations import SORT_ORDERS

NODE_EXTENSION = os.path.join(ROOT, "web", "comfy", "prompt_manager.js")


class TestSortContract(unittest.TestCase):
    def test_node_recent_button_requests_recently_used(self):
        # The node extension can't import the shared module (ComfyUI load order is
        # not guaranteed), so it sends the key directly; it must be a server key.
        with open(NODE_EXTENSION, encoding="utf-8") as fh:
            source = fh.read()
        match = re.search(r"/prompt_manager/recent\?[^`'\"]*sort=([a-z_]+)", source)

        self.assertIsNotNone(match, "node Recent request does not send a sort")
        self.assertEqual(match.group(1), "last_used_desc")
        self.assertIn(match.group(1), SORT_ORDERS)


if __name__ == "__main__":
    unittest.main()
