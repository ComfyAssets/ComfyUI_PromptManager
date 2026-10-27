"""Final stops for the background threads a test starts (#301, #302).

Every real ``ImageGenerationHandler``, ``ImageMonitor`` or ``PromptTracker``
a test constructs ends with its final stop and an assertion, before the
test's folder and databases go: ``close()`` for a handler,
``stop_monitoring()`` for a monitor, ``shutdown()`` for a tracker.
"""

from utils.prompt_tracker import PromptTracker


def stop_tracker(testcase, tracker):
    """Shut a real tracker's cleanup thread down; mocks are left alone."""
    if isinstance(tracker, PromptTracker):
        testcase.assertTrue(tracker.shutdown(), "prompt cleanup thread still running")


def close_handler(testcase, handler):
    """Close ``handler`` for good, then stop the tracker it was built with."""
    testcase.assertTrue(handler.close(), "image worker still running")
    stop_tracker(testcase, getattr(handler, "prompt_tracker", None))


def stop_monitor(testcase, monitor):
    """Stop ``monitor`` (its observer and worker), then its tracker."""
    testcase.assertTrue(monitor.stop_monitoring(), "image monitor still running")
    stop_tracker(testcase, getattr(monitor, "prompt_tracker", None))
