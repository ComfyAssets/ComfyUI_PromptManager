"""Temporary #487 profile of a few modules (removed before merge)."""

import cProfile
import logging
import pstats
import sys
import unittest

logging.disable(logging.CRITICAL)
names = sys.argv[1:]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
pr = cProfile.Profile()
pr.enable()
unittest.TextTestRunner(stream=open(__import__("os").devnull, "w")).run(suite)
pr.disable()
st = pstats.Stats(pr)
st.sort_stats("tottime").print_stats(30)
st.sort_stats("cumtime").print_stats(60)
