"""Centralized ToolExecutor with AST/schema validation, caching, and output state tagging."""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from tools.calculator import calculate
from tools.failure_injector import FailureInjector, ToolExecution, ToolFunction
from tools.mock_api import get_ticket
from tools.python_exec import execute
from tools.search_tool import search
from trace.trace_logger import TraceLogger


_TICKET_RE = re.compile(r"^T-\d+$", re.IGNORECASE)
_CALC_ALLOWED_AST = (
    ast.Expression,
    ast.Constant,
    ast.UnaryOp,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
)
_PY_SAFE_CALLS = {"abs", "len", "max", "min", "sum"}


@dataclass(frozen=True)
class ToolSpec:
    """Registered tool metadata, schema, and callable."""

    name: str
    function: ToolFunction
    required_fields: dict[str, type]
    description: str = ""


@dataclass(frozen=True)
class ToolCallOutcome:
    """Execution result paired with cache telemetry and committed/discarded state."""

    execution: ToolExecution
    output_state: str  # "committed" or "discarded"
    cache_hit: bool
    latency: float
    validation_error: str | None = None


class ToolExecutor:
    """Unified tool registry, pre-execution AST/schema validator, and deterministic cache.

    Tool-call caching is keyed by ``(tool_name, canonical_json_args)`` and is
    automatically bypassed whenever an active ``FailureInjector`` with
    ``failure_rate > 0`` is supplied so fault-injection runs remain unbiased.
    """

    def __init__(
        self,
        *,
        enable_cache: bool = True,
        validate_before_execute: bool = True,
    ) -> None:
        self.enable_cache = bool(enable_cache)
        self.validate_before_execute = bool(validate_before_execute)
        self._registry: dict[str, ToolSpec] = {}
        self._cache: dict[tuple[str, str], Any] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    @classmethod
    def with_default_tools(
        cls,
        *,
        enable_cache: bool = True,
        validate_before_execute: bool = True,
    ) -> "ToolExecutor":
        """Create a ToolExecutor populated with TraceRecover's four local tools."""
        executor = cls(
            enable_cache=enable_cache,
            validate_before_execute=validate_before_execute,
        )
        executor.register(
            "search",
            lambda args: search(args["query"]),
            required_fields={"query": str},
            description="Deterministic local knowledge-base search.",
        )
        executor.register(
            "calculator",
            lambda args: calculate(args["expression"]),
            required_fields={"expression": str},
            description="AST-allowlisted arithmetic calculator.",
        )
        executor.register(
            "python_exec",
            lambda args: execute(args["code"]),
            required_fields={"code": str},
            description="Safe AST-allowlisted Python expression evaluator.",
        )
        executor.register(
            "mock_api",
            lambda args: get_ticket(args["ticket_id"]),
            required_fields={"ticket_id": str},
            description="Deterministic enterprise SLA ticket lookup.",
        )
        return executor

    def register(
        self,
        name: str,
        function: ToolFunction,
        *,
        required_fields: Mapping[str, type] | None = None,
        description: str = "",
    ) -> None:
        """Register or replace a named tool and its argument schema."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")
        self._registry[name] = ToolSpec(
            name=name,
            function=function,
            required_fields=dict(required_fields or {}),
            description=description,
        )

    def list_tools(self) -> list[str]:
        """Return sorted registered tool names."""
        return sorted(self._registry.keys())

    def as_tool_map(self) -> dict[str, ToolFunction]:
        """Return a mapping of tool name -> callable compatible with ReactAgent."""
        return {name: spec.function for name, spec in self._registry.items()}

    def validate_arguments(
        self, tool_name: str, arguments: Any
    ) -> str | None:
        """Validate argument schema and AST safety prior to tool execution."""
        if tool_name not in self._registry:
            return f"Unknown tool: {tool_name!r}."
        if not isinstance(arguments, Mapping):
            return "Tool arguments must be a mapping/object."
        spec = self._registry[tool_name]
        for field, expected_type in spec.required_fields.items():
            value = arguments.get(field)
            if not isinstance(value, expected_type):
                return f"Argument {field!r} must be of type {expected_type.__name__}."
            if expected_type is str and not value.strip():
                return f"Argument {field!r} must be a non-empty string."

        if tool_name == "mock_api":
            ticket_id = str(arguments["ticket_id"]).strip()
            if not _TICKET_RE.fullmatch(ticket_id):
                return f"Invalid ticket_id format: {ticket_id!r} (expected T-<digits>)."
        elif tool_name == "calculator":
            return self._validate_calculator_ast(str(arguments["expression"]))
        elif tool_name == "python_exec":
            return self._validate_python_ast(str(arguments["code"]))
        return None

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        failure_injector: Any | None = None,
        bypass_cache: bool = False,
    ) -> ToolCallOutcome:
        """Validate, optionally serve from cache, or invoke a registered tool."""
        started = time.perf_counter()
        if tool_name not in self._registry:
            raise ValueError(f"Unknown tool action: {tool_name}")

        args_dict = dict(arguments) if isinstance(arguments, Mapping) else {}
        injector_active = self._is_injector_active(failure_injector)

        # Pre-execution validation (always enforced when enabled)
        if self.validate_before_execute:
            validation_error = self.validate_arguments(tool_name, arguments)
            if validation_error is not None:
                execution = ToolExecution(
                    output=None,
                    error=f"ValueError: {validation_error}",
                    failure_type=None,
                    failure_injected=False,
                    original_arguments=args_dict,
                    execution_arguments=args_dict,
                    execution_status="failed",
                )
                return ToolCallOutcome(
                    execution=execution,
                    output_state="discarded",
                    cache_hit=False,
                    latency=time.perf_counter() - started,
                    validation_error=validation_error,
                )

        # Deterministic cache lookup (bypassed whenever fault injection is active)
        can_use_cache = self.enable_cache and not bypass_cache and not injector_active
        cache_key = self._cache_key(tool_name, args_dict)
        if can_use_cache and cache_key in self._cache:
            self.cache_hits += 1
            cached_output = self._cache[cache_key]
            output_copy = (
                dict(cached_output)
                if isinstance(cached_output, dict)
                else list(cached_output)
                if isinstance(cached_output, list)
                else cached_output
            )
            execution = ToolExecution(
                output=output_copy,
                error=None,
                failure_type=None,
                failure_injected=False,
                original_arguments=args_dict,
                execution_arguments=dict(args_dict),
                execution_status="success",
            )
            return ToolCallOutcome(
                execution=execution,
                output_state="committed",
                cache_hit=True,
                latency=time.perf_counter() - started,
            )

        self.cache_misses += 1
        tool_fn = self._registry[tool_name].function
        if failure_injector is not None:
            execution = failure_injector.execute(tool_name, tool_fn, args_dict)
        else:
            execution = FailureInjector(enabled=False).execute(
                tool_name, tool_fn, args_dict
            )

        succeeded = (
            execution.error is None
            and not execution.failure_injected
            and execution.execution_status == "success"
            and execution.output is not None
        )
        output_state = "committed" if succeeded else "discarded"
        if can_use_cache and succeeded:
            self._cache[cache_key] = execution.output

        return ToolCallOutcome(
            execution=execution,
            output_state=output_state,
            cache_hit=False,
            latency=time.perf_counter() - started,
        )

    def execute_and_log(
        self,
        *,
        thought: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        logger: TraceLogger,
        failure_injector: Any | None = None,
        bypass_cache: bool = False,
    ) -> tuple[ToolCallOutcome, dict[str, Any]]:
        """Execute a tool and append a trace step with explicit committed/discarded state."""
        outcome = self.execute(
            tool_name,
            arguments,
            failure_injector=failure_injector,
            bypass_cache=bypass_cache,
        )
        execution = outcome.execution
        failure_metadata = (
            execution.trace_metadata(tool_name)
            if execution.failure_injected
            else None
        )
        step = logger.log_step(
            thought=thought,
            tool=tool_name,
            input_data=dict(arguments) if isinstance(arguments, Mapping) else arguments,
            output=None if execution.error is not None else execution.output,
            error=execution.error,
            latency=outcome.latency,
            failure_metadata=failure_metadata,
            metadata={"cache_hit": outcome.cache_hit},
            output_state=outcome.output_state,
        )
        return outcome, step

    def clear_cache(self) -> None:
        """Clear cached tool results and reset cache counters."""
        self._cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0

    def cache_stats(self) -> dict[str, int]:
        """Return cache hit/miss counters and current cache size."""
        return {
            "hits": self.cache_hits,
            "misses": self.cache_misses,
            "entries": len(self._cache),
        }

    @staticmethod
    def _is_injector_active(injector: Any | None) -> bool:
        if injector is None:
            return False
        enabled = getattr(injector, "enabled", True)
        rate = getattr(injector, "failure_rate", None)
        if isinstance(enabled, bool) and not enabled:
            return False
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            return rate > 0
        # Custom test injectors without failure_rate attribute are treated as active
        return True

    @staticmethod
    def _cache_key(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, str]:
        return (
            tool_name,
            json.dumps(arguments, sort_keys=True, ensure_ascii=True, default=str),
        )

    @staticmethod
    def _validate_calculator_ast(expression: str) -> str | None:
        if len(expression) > 200:
            return "Calculator expression exceeds maximum length of 200."
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            return f"Invalid calculator syntax: {exc}"
        for node in ast.walk(tree):
            if not isinstance(node, _CALC_ALLOWED_AST):
                return f"Disallowed AST construct in calculator expression: {type(node).__name__}."
            if isinstance(node, ast.Constant) and type(node.value) not in (int, float):
                return "Calculator constants must be numeric."
        return None

    @staticmethod
    def _validate_python_ast(code: str) -> str | None:
        if len(code) > 1000:
            return "Python expression exceeds maximum length of 1000."
        try:
            tree = ast.parse(code, mode="eval")
        except SyntaxError as exc:
            return f"Invalid Python expression syntax: {exc}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Lambda, ast.Attribute)):
                return f"Disallowed AST construct in safe Python expression: {type(node).__name__}."
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _PY_SAFE_CALLS:
                    return "Only abs, len, max, min, and sum calls are allowed."
        return None
