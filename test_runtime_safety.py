import unittest

from loop_guard import LoopGuard
from tool_receipt import make_tool_receipt, receipt_fingerprint, validate_tool_receipt


class LoopGuardTests(unittest.TestCase):
    def test_same_call_warn_then_stop(self):
        guard = LoopGuard(warning_limit=3, hard_limit=5, window=10)
        actions = [
            guard.observe_tool_call("run-1", "click", {"id": "x"}).action
            for _ in range(5)
        ]
        self.assertEqual(actions, ["continue", "continue", "warn", "continue", "stop"])

    def test_argument_change_avoids_identical_call_stop(self):
        guard = LoopGuard(warning_limit=2, hard_limit=3, window=10)
        decisions = [
            guard.observe_tool_call("run-1", "goto", {"url": f"https://x/{i}"})
            for i in range(5)
        ]
        self.assertFalse(any(item.stop for item in decisions))

    def test_state_fingerprint_distinguishes_progress_from_stagnation(self):
        guard = LoopGuard(warning_limit=2, hard_limit=3, window=10)
        decisions = [
            guard.observe_tool_call(
                "run-1",
                "getPage",
                {},
                state_fingerprint=f"generation:{i}",
            )
            for i in range(5)
        ]
        self.assertFalse(any(item.stop for item in decisions))

        stagnant = LoopGuard(warning_limit=2, hard_limit=3, window=10)
        self.assertEqual(
            [
                stagnant.observe_tool_call(
                    "run-1",
                    "getPage",
                    {},
                    state_fingerprint="generation:7",
                ).action
                for _ in range(3)
            ],
            ["continue", "warn", "stop"],
        )

    def test_tool_frequency_layer_catches_varied_args(self):
        guard = LoopGuard(
            warning_limit=99,
            hard_limit=100,
            window=10,
            tool_frequency_warning_limit=3,
            tool_frequency_hard_limit=4,
        )
        actions = [
            guard.observe_tool_call("run-1", "click", {"id": str(i)}).action
            for i in range(4)
        ]
        self.assertEqual(actions, ["continue", "continue", "warn", "stop"])

    def test_per_tool_overrides(self):
        guard = LoopGuard(
            warning_limit=99,
            hard_limit=100,
            window=10,
            tool_frequency_warning_limit=50,
            tool_frequency_hard_limit=60,
            tool_freq_overrides={"click": (2, 3)},
        )
        self.assertEqual(
            [
                guard.observe_tool_call("run-1", "click", {"id": str(i)}).action
                for i in range(3)
            ],
            ["continue", "warn", "stop"],
        )

    def test_run_isolation_and_reset(self):
        guard = LoopGuard(warning_limit=2, hard_limit=3, window=10)
        self.assertEqual(
            guard.observe_tool_call("a", "click", {"id": "x"}).action,
            "continue",
        )
        self.assertEqual(
            guard.observe_tool_call("a", "click", {"id": "x"}).action,
            "warn",
        )
        self.assertEqual(
            guard.observe_tool_call("b", "click", {"id": "x"}).action,
            "continue",
        )
        guard.reset("a")
        self.assertEqual(
            guard.observe_tool_call("a", "click", {"id": "x"}).action,
            "continue",
        )


class ToolReceiptTests(unittest.TestCase):
    def test_receipt_hashes_without_raw_payload(self):
        receipt = make_tool_receipt(
            run_id="run-1",
            step=1,
            action_id="cmd-1",
            tool="goto",
            status="success",
            args={"url": "https://example.com/private"},
            output={"title": "Example", "secret": "do-not-store"},
            created_at="2026-09-20T00:00:00Z",
        )
        validate_tool_receipt(receipt)
        rendered = str(receipt)
        self.assertNotIn("private", rendered)
        self.assertNotIn("do-not-store", rendered)
        self.assertFalse(receipt["authoritative"])
        self.assertFalse(receipt["acceptance"])
        self.assertTrue(receipt_fingerprint(receipt).startswith("sha256:"))

    def test_invalid_receipt_rejected(self):
        receipt = make_tool_receipt(
            run_id="run-1",
            step=1,
            tool="click",
            status="success",
            args={},
            output="ok",
            created_at="2026-09-20T00:00:00Z",
        )
        receipt["acceptance"] = True
        with self.assertRaisesRegex(ValueError, "not acceptance"):
            validate_tool_receipt(receipt)


if __name__ == "__main__":
    unittest.main()

