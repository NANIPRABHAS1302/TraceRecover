"""Command-line demonstration for the TraceRecover baseline agent."""

from __future__ import annotations

import argparse

from agent.react_agent import ReactAgent
from config import LLMClient, SUPPORTED_PROVIDERS, get_settings
from healing.recovery import RecoveryEngine
from tools.failure_injector import FailureInjector, FailureType
from uncertainty.abstention import AbstentionPolicy


SAMPLE_QUESTIONS = (
    "What is the capital of France?",
    "What is 19 * 23 + 4?",
    "What is the status and owner of ticket T-104?",
)


def main() -> None:
    """Run the three reproducible sample questions or one custom question."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the TraceRecover baseline demo.")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=settings.provider,
        help="LLM provider; use 'local' for the deterministic offline demo.",
    )
    parser.add_argument("--question", help="Run a single custom question.")
    parser.add_argument(
        "--failure-rate",
        type=float,
        default=0,
        metavar="PERCENT",
        help="Percentage chance of injecting one tool failure (0-100).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used to reproduce failure selection.",
    )
    parser.add_argument(
        "--no-failure-injection",
        action="store_true",
        help="Disable all injected failures.",
    )
    parser.add_argument(
        "--disable-failure-type",
        choices=[failure_type.value for failure_type in FailureType],
        action="append",
        default=[],
        metavar="TYPE",
        help="Disable a failure type; may be supplied more than once.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Maximum number of retry attempts per detected failure.",
    )
    parser.add_argument(
        "--max-recovery-attempts",
        type=int,
        default=4,
        help="Maximum total recovery attempts for one failure.",
    )
    parser.add_argument(
        "--max-recovery-seconds",
        type=float,
        default=15.0,
        help="Maximum recovery window in seconds (checked between attempts).",
    )
    parser.add_argument(
        "--answer-threshold",
        type=float,
        default=0.15,
        help="Maximum uncertainty score for an ANSWER decision.",
    )
    parser.add_argument(
        "--abstain-threshold",
        type=float,
        default=0.55,
        help="Uncertainty score at or above which the agent abstains.",
    )
    args = parser.parse_args()

    enabled_types = [
        failure_type
        for failure_type in FailureType
        if failure_type.value not in args.disable_failure_type
    ]
    injector = FailureInjector(
        failure_rate=args.failure_rate,
        seed=args.seed,
        enabled=not args.no_failure_injection,
        enabled_types=enabled_types,
    )
    print(
        f"TraceRecover baseline | provider={args.provider} | "
        f"failure_rate={args.failure_rate:g}% | seed={args.seed}"
    )
    agent = ReactAgent(
        client=LLMClient(provider=args.provider),
        failure_injector=injector,
        recovery_engine=RecoveryEngine(
            max_retries=args.max_retries,
            max_total_attempts=args.max_recovery_attempts,
            max_duration_seconds=args.max_recovery_seconds,
        ),
        abstention_policy=AbstentionPolicy(
            answer_threshold=args.answer_threshold,
            abstain_threshold=args.abstain_threshold,
        ),
    )
    questions = (args.question,) if args.question else SAMPLE_QUESTIONS
    for question in questions:
        result = agent.run(question)
        print(f"\nQuestion: {question}")
        print(f"Original generated answer (audit only): {result.answer}")
        print(f"Trace: {result.log_path}")
        report = result.failure_report
        if report["failure_detected"]:
            print(f"Failure detection: {report['total_failures']} failure(s)")
            for failure in report["failures"]:
                print(
                    f"  Step {failure['step_id']} | {failure['tool_name']} | "
                    f"{failure['failure_category']} | {failure['severity']} | "
                    f"source={failure['failure_origin']}"
                )
            print(f"Failure report: {result.report_path}")
        else:
            print("Failure detection: no failures")
        recovery = result.recovery_report
        print(
            "Recovery summary: "
            f"detected={recovery['total_detected_failures']}, "
            f"attempts={recovery['total_recovery_attempts']}, "
            f"successful={recovery['successful_recoveries']}, "
            f"failed={recovery['failed_recoveries']}, "
            f"strategies={','.join(recovery['strategies_used']) or 'none'}"
        )
        print(f"Recovery log: {result.recovery_log_path}")
        grounding = result.grounding_report
        score = grounding["grounding_score"]
        score_text = "N/A (no factual claims)" if score is None else f"{score:.3f}"
        print(
            "Grounding summary: "
            f"claims={grounding['total_claim_count']}, "
            f"supported={grounding['supported_count']}, "
            f"unsupported={grounding['unsupported_count']}, "
            f"contradicted={grounding['contradicted_count']}, "
            f"score={score_text}"
        )
        for claim in grounding["claims"]:
            citation_ids = list(
                dict.fromkeys(
                    item["step_id"] for item in claim["evidence"]
                )
            )
            citations = (
                ", ".join(f"step {step_id}" for step_id in citation_ids)
                if citation_ids
                else "none"
            )
            print(
                f"  {claim['claim_id']} | {claim['status']} | "
                f"citations={citations}"
            )
        print(f"Grounding report: {result.grounding_report_path}")
        uncertainty = result.uncertainty_report
        tool_ratio = uncertainty["tool_success_ratio"]
        tool_ratio_text = (
            "N/A"
            if tool_ratio is None
            else f"{tool_ratio:.3f} "
            f"({uncertainty['tool_success_count']}/"
            f"{uncertainty['tool_execution_count']})"
        )
        recovery_reliability = uncertainty["recovery_reliability"]
        recovery_text = (
            "N/A"
            if recovery_reliability is None
            else f"{recovery_reliability:.3f}"
        )
        self_consistency = uncertainty["self_consistency_score"]
        self_consistency_text = (
            "unavailable"
            if self_consistency is None
            else f"{self_consistency:.3f}"
        )
        uncertainty_score = uncertainty["uncertainty_score"]
        uncertainty_text = (
            "N/A" if uncertainty_score is None else f"{uncertainty_score:.3f}"
        )
        print(f"Tool success ratio: {tool_ratio_text}")
        print(
            "Recovery reliability: "
            f"{recovery_text} | attempts={uncertainty['recovery_attempt_count']} | "
            f"successful incidents={uncertainty['recovery_success_count']} | "
            f"failed incidents={uncertainty['recovery_failure_count']}"
        )
        print(f"Self-consistency: {self_consistency_text}")
        print(f"Uncertainty score: {uncertainty_text}")
        print(f"Final decision: {result.final_decision}")
        print(f"Final user-facing response:\n{result.final_response}")
        print(f"Uncertainty report: {result.uncertainty_report_path}")


if __name__ == "__main__":
    main()