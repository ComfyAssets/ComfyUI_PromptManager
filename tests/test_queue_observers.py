"""Cooperative queue subscriptions preserve the host and other wrappers."""

import queue
import unittest

try:
    from utils.queue_observers import subscribe_dequeue, subscribe_task_done
except ImportError:
    subscribe_dequeue = subscribe_task_done = None


class FakeQueue:
    def __init__(self):
        self.result = ((0, "job", {}, {}, [], {}), 0)
        self.calls = []
        self.failures = ()
        self.done_result = object()

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        for failure in self.failures:
            raise failure
        return self.result

    def task_done(self, *args, **kwargs):
        self.calls.append(("done", args, kwargs))
        for failure in self.failures:
            raise failure
        return self.done_result


class QueueObserversTests(unittest.TestCase):
    def require_api(self):
        self.assertTrue(callable(subscribe_dequeue), "queue subscriptions are absent")
        self.assertTrue(callable(subscribe_task_done), "task subscriptions are absent")

    def test_dequeue_preserves_identity_arguments_empty_and_exception(self):
        self.require_api()
        host, seen, sentinel = FakeQueue(), [], object()
        subscribe_dequeue(host, "capture", seen.append)
        original = host.result
        self.assertIs(host.get(sentinel, timeout=sentinel), original)
        self.assertEqual(host.calls, [("get", (sentinel,), {"timeout": sentinel})])
        self.assertEqual(seen, [original])
        host.result = None
        self.assertIsNone(host.get())
        self.assertEqual(seen, [original])
        for error in (queue.Empty(), RuntimeError("host failure")):
            host.failures = (error,)
            with self.assertRaises(type(error)) as raised:
                host.get(timeout=sentinel)
            self.assertIs(raised.exception, error)
        self.assertEqual(seen, [original])

    def test_task_done_observes_success_only_and_preserves_host_values(self):
        self.require_api()
        host, seen, sentinel = FakeQueue(), [], object()
        subscribe_task_done(host, "capture", lambda a, k: seen.append((a, k)))
        self.assertIs(host.task_done(0, sentinel, status=sentinel), host.done_result)
        self.assertEqual(seen, [((0, sentinel), {"status": sentinel})])
        error = ValueError("host failure")
        host.failures = (error,)
        with self.assertRaises(ValueError) as raised:
            host.task_done(1)
        self.assertIs(raised.exception, error)
        self.assertEqual(len(seen), 1)

    def test_keys_are_idempotent_and_independently_removable(self):
        self.require_api()
        host, first, second = FakeQueue(), [], []
        unsubscribe = subscribe_dequeue(host, "a", first.append)
        subscribe_dequeue(host, "a", lambda value: self.fail("duplicate callback"))
        remove_second = subscribe_dequeue(host, "b", second.append)
        host.get()
        unsubscribe()
        unsubscribe()
        host.get()
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        remove_second()
        subscribe_dequeue(host, "a", first.append)
        unsubscribe()  # old cleanup must not remove a new subscriber with this key
        host.get()
        self.assertEqual(len(first), 2)

    def test_other_wrappers_before_after_cleanup_and_resubscription_survive(self):
        self.require_api()
        host, calls, seen = FakeQueue(), [], []
        original = host.get

        def before(*a, **k):
            calls.append("before")
            return original(*a, **k)

        host.get = before
        unsubscribe = subscribe_dequeue(host, "capture", seen.append)
        observed = host.get

        def after(*a, **k):
            calls.append("after")
            return observed(*a, **k)

        host.get = after
        host.get()
        unsubscribe()
        self.assertIs(host.get, after)
        host.get()
        subscribe_dequeue(host, "capture", seen.append)
        host.get()
        self.assertIs(host.get, after)
        self.assertEqual(calls, ["after", "before"] * 3)
        self.assertEqual(len(seen), 2)

    def test_failing_subscriber_cannot_disable_other_subscribers_or_host(self):
        self.require_api()
        host, seen = FakeQueue(), []

        def fail(_):
            raise ValueError("secret error")

        subscribe_dequeue(host, "bad", fail)
        subscribe_dequeue(host, "good", seen.append)
        with self.assertLogs("prompt_manager.queue_observers", level="WARNING") as logs:
            self.assertIs(host.get(), host.result)
        self.assertEqual(seen, [host.result])
        self.assertNotIn("secret error", "\n".join(logs.output))

    def test_missing_methods_are_rejected_without_installing_a_fallback(self):
        self.require_api()
        for subscribe in (subscribe_dequeue, subscribe_task_done):
            with self.assertRaises(ValueError):
                subscribe(object(), "key", lambda *a: None)
