"""Prompt construction and deterministic planning for the baseline agent."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence


TOOL_DESCRIPTIONS = {
    "search": "Search the local factual knowledge base. Input: {query: string}.",
    "calculator": "Calculate a basic arithmetic expression. Input: {expression: string}.",
    "python_exec": (
        "Evaluate a safe Python expression without imports or side effects. "
        "Input: {code: string}."
    ),
    "mock_api": "Look up a simulated support ticket. Input: {ticket_id: string}.",
}


def build_prompt(
    question: str,
    observations: list[dict[str, Any]],
    excluded_tools: Sequence[str] = (),
) -> str:
    """Build the JSON-only ReAct instruction for a remote language model."""
    return (
        "You are a tool-using assistant. Use a tool before answering factual or "
        "computational questions. Choose exactly one next action per response. "
        "Return only a JSON object with keys thought, action, arguments, and "
        "final_answer. For tool calls, action is a listed tool name, arguments "
        "is an object, and final_answer is null. To finish, action is 'final', "
        "arguments is {}, and final_answer is a string grounded in observations. "
        "Do not invent tool outputs.\n"
        f"Tools that must not be repeated: {json.dumps(list(excluded_tools))}\n"
        f"Available tools: {json.dumps(TOOL_DESCRIPTIONS)}\n"
        f"Question: {question}\n"
        f"Prior observations: {json.dumps(observations, ensure_ascii=True)}"
    )


def parse_decision(text: str) -> dict[str, Any]:
    """Parse and minimally validate a model's JSON action, wrapping plain text safely."""
    raw_text = (text or "").strip()
    if not raw_text:
        raise ValueError("The LLM returned an empty response.")
    content = raw_text
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I).strip()
    decision: Any = None
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if match:
            try:
                decision = json.loads(match.group(0))
            except json.JSONDecodeError:
                decision = None
    if (
        isinstance(decision, dict)
        and isinstance(decision.get("action"), str)
        and decision["action"].strip()
    ):
        thought = decision.get("thought", "")
        if not isinstance(thought, str):
            thought = str(thought)
        arguments = decision.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        action = decision["action"].strip()
        final_answer = decision.get("final_answer")
        if action == "final" and not isinstance(final_answer, str):
            final_answer = raw_text
        return {
            "thought": thought,
            "action": action,
            "arguments": arguments,
            "final_answer": final_answer,
        }
    return {
        "thought": "Direct LLM answer",
        "action": "final",
        "arguments": {},
        "final_answer": content or raw_text,
    }


class RuleBasedPlanner:
    """A deterministic local planner for reproducible, no-cost demonstrations."""

    _arithmetic = re.compile(r"^[\d\s.+\-*/()%]+$")
    _ticket_id = re.compile(r"\bT-\d+\b", re.IGNORECASE)

    def next_action(
        self,
        question: str,
        observations: list[dict[str, Any]],
        excluded_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        """Choose one tool action, then answer from its observation."""
        if excluded_tools:
            return self._plan_question(question, excluded_tools)
        if observations:
            latest = observations[-1]
            if latest.get("error"):
                answer = f"I could not complete the request: {latest['error']}"
            else:
                answer = str(latest["output"])
            return {
                "thought": "Use the latest tool observation to answer.",
                "action": "final",
                "arguments": {},
                "final_answer": answer,
            }

        return self._plan_question(question, set())

    def _plan_question(
        self, question: str, excluded_tools: set[str]
    ) -> dict[str, Any]:
        expression = self._extract_arithmetic(question)
        if expression is not None:
            if "calculator" in excluded_tools:
                if "python_exec" not in excluded_tools:
                    return {
                        "thought": "Re-plan the arithmetic with the safe Python tool.",
                        "action": "python_exec",
                        "arguments": {"code": expression},
                        "final_answer": None,
                    }
                return self._cannot_replan()
            return {
                "thought": "The question contains an arithmetic expression.",
                "action": "calculator",
                "arguments": {"expression": expression},
                "final_answer": None,
            }
        ticket = self._ticket_id.search(question)
        if ticket:
            if "mock_api" in excluded_tools:
                return self._cannot_replan()
            return {
                "thought": "The question asks about a support ticket.",
                "action": "mock_api",
                "arguments": {"ticket_id": ticket.group(0).upper()},
                "final_answer": None,
            }
        if "search" in excluded_tools:
            return self._cannot_replan()
        return {
            "thought": "Search the local knowledge base for factual evidence.",
            "action": "search",
            "arguments": {"query": question},
            "final_answer": None,
        }

    @staticmethod
    def _cannot_replan() -> dict[str, Any]:
        return {
            "thought": "No unused compatible tool remains for this question.",
            "action": "final",
            "arguments": {},
            "final_answer": "I cannot complete the request with the remaining tools.",
        }

    def _extract_arithmetic(self, question: str) -> str | None:
        """Extract arithmetic only when the question has an unambiguous expression."""
        candidates = re.findall(r"[\d][\d\s.+\-*/()%]*", question)
        if not candidates:
            return None
        expression = max(candidates, key=len).strip()
        if not expression or not self._arithmetic.fullmatch(expression):
            return None
        if not any(operator in expression for operator in "+-*/%"):
            return None
        return expression