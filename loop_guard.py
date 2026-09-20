from __future__ import annotations

import hashlib
import json
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class LoopDecision:
    action: Literal["continue", "warn", "stop"]
    reason_code: str
    tool: str
    count: int
    threshold: int

    @property
    def stop(self) -> bool:
        return self.action == "stop"


class LoopGuard:
    """Deterministic, run-scoped loop detection for tool-call adapters.

    The guard keeps only stable hashes and tool names. Raw tool arguments are
    normalized transiently and are never retained in the run history.
    """

    SALIENT_FIELDS = ("path", "url", "query", "command", "pattern", "glob", "cmd")

    def __init__(
        self,
        *,
        warning_limit: int = 3,
        hard_limit: int = 5,
        window: int = 20,
        tool_frequency_warning_limit: int = 30,
        tool_frequency_hard_limit: int = 50,
        tool_freq_overrides: dict[str, tuple[int, int]] | None = None,
        max_runs: int = 128,
    ) -> None:
        self._validate_pair("identical-call", warning_limit, hard_limit)
        self._validate_pair(
            "tool-frequency", tool_frequency_warning_limit, tool_frequency_hard_limit
        )
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            raise ValueError("window must be a positive integer")
        if not isinstance(max_runs, int) or isinstance(max_runs, bool) or max_runs < 1:
            raise ValueError("max_runs must be a positive integer")

        overrides = dict(tool_freq_overrides or {})
        for tool, limits in overrides.items():
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("tool frequency override names must be non-empty strings")
            if not isinstance(limits, tuple) or len(limits) != 2:
                raise ValueError("tool frequency overrides must be (warning, hard) tuples")
            self._validate_pair(f"tool-frequency override for {tool}", limits[0], limits[1])

        self.warning_limit = warning_limit
        self.hard_limit = hard_limit
        self.window = window
        self.tool_frequency_warning_limit = tool_frequency_warning_limit
        self.tool_frequency_hard_limit = tool_frequency_hard_limit
        self.tool_freq_overrides = overrides
        self.max_runs = max_runs
        override_hard = max((hard for _, hard in overrides.values()), default=0)
        self.tool_window = max(window, tool_frequency_hard_limit, override_hard)

        self._run_lru: OrderedDict[str, None] = OrderedDict()
        self._keys: dict[str, deque[str]] = {}
        self._tools: dict[str, deque[str]] = {}
        self._key_warned: dict[str, set[str]] = {}
        self._tool_warned: dict[str, set[str]] = {}

    @staticmethod
    def _validate_pair(name: str, warning: int, hard: int) -> None:
        values = (warning, hard)
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in values):
            raise ValueError(f"{name} limits must be positive integers")
        if warning >= hard:
            raise ValueError(f"{name} warning limit must be lower than hard limit")

    def _touch_run(self, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id is required")
        if run_id in self._run_lru:
            self._run_lru.move_to_end(run_id)
            return
        self._run_lru[run_id] = None
        self._keys[run_id] = deque(maxlen=self.window)
        self._tools[run_id] = deque(maxlen=self.tool_window)
        self._key_warned[run_id] = set()
        self._tool_warned[run_id] = set()
        while len(self._run_lru) > self.max_runs:
            stale, _ = self._run_lru.popitem(last=False)
            self._keys.pop(stale, None)
            self._tools.pop(stale, None)
            self._key_warned.pop(stale, None)
            self._tool_warned.pop(stale, None)

    @staticmethod
    def _as_mapping(args: Any) -> Any:
        if isinstance(args, str):
            try:
                return json.loads(args)
            except (TypeError, ValueError, json.JSONDecodeError):
                return args
        return args

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _normalized_key_material(self, tool: str, args: Any) -> Any:
        value = self._as_mapping(args)
        if tool == "read_file" and isinstance(value, dict):
            return {
                "path": value.get("path"),
                "start": value.get("start"),
                "end": value.get("end") if value.get("end") is not None else "end",
            }
        if tool in {"write_file", "str_replace"}:
            return value
        if isinstance(value, dict):
            salient = {field: value[field] for field in self.SALIENT_FIELDS if field in value}
            return salient or value
        return value

    def _call_key(self, tool: str, args: Any, state_fingerprint: str | None) -> str:
        material = {
            "tool": tool,
            "key": self._normalized_key_material(tool, args),
            "state_fingerprint": state_fingerprint,
        }
        return hashlib.sha256(self._canonical(material).encode("utf-8")).hexdigest()

    def reset(self, run_id: str) -> None:
        self._run_lru.pop(run_id, None)
        self._keys.pop(run_id, None)
        self._tools.pop(run_id, None)
        self._key_warned.pop(run_id, None)
        self._tool_warned.pop(run_id, None)

    def _tool_limits(self, tool: str) -> tuple[int, int]:
        return self.tool_freq_overrides.get(
            tool,
            (self.tool_frequency_warning_limit, self.tool_frequency_hard_limit),
        )

    def observe_tool_call(
        self,
        run_id: str,
        tool: str,
        args: Any,
        *,
        state_fingerprint: str | None = None,
    ) -> LoopDecision:
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError("tool is required")
        if state_fingerprint is not None and not isinstance(state_fingerprint, str):
            raise ValueError("state_fingerprint must be a string or None")

        self._touch_run(run_id)
        key = self._call_key(tool, args, state_fingerprint)
        key_history = self._keys[run_id]
        tool_history = self._tools[run_id]
        key_history.append(key)
        tool_history.append(tool)

        key_count = Counter(key_history)[key]
        tool_count = Counter(tool_history)[tool]
        tool_warning, tool_hard = self._tool_limits(tool)

        if key_count >= self.hard_limit:
            return LoopDecision("stop", "identical_call_hard_limit", tool, key_count, self.hard_limit)
        if tool_count >= tool_hard:
            return LoopDecision("stop", "tool_frequency_hard_limit", tool, tool_count, tool_hard)

        key_warned = self._key_warned[run_id]
        tool_warned = self._tool_warned[run_id]
        if key_count < self.warning_limit:
            key_warned.discard(key)
        if tool_count < tool_warning:
            tool_warned.discard(tool)

        if key_count >= self.warning_limit and key not in key_warned:
            key_warned.add(key)
            return LoopDecision("warn", "identical_call_warning", tool, key_count, self.warning_limit)
        if tool_count >= tool_warning and tool not in tool_warned:
            tool_warned.add(tool)
            return LoopDecision("warn", "tool_frequency_warning", tool, tool_count, tool_warning)

        return LoopDecision("continue", "within_limits", tool, max(key_count, tool_count), 0)
