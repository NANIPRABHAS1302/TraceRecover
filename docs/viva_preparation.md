# Viva Preparation

## Explain the project in 30 seconds

Tool-using agents can fail and still produce a confident answer. TraceRecover
records each tool step, detects and localizes failures, and attempts a bounded
recovery. It then extracts answer claims and checks them against successful
tool outputs. Finally, it combines evidence and execution signals to answer,
warn, or abstain. Every final factual claim should be traceable to evidence.

## Core questions

### What is evidence grounding?

It is the process of checking answer claims against real outputs from successful
tool calls. The agent cannot treat its own unsupported wording as evidence.

### Why extract claims?

A complete answer may mix correct and incorrect statements. Splitting it into
small factual claims lets the verifier check and cite each statement separately.

### What do SUPPORTED, UNSUPPORTED, and CONTRADICTED mean?

- **SUPPORTED:** eligible tool evidence directly supports the claim.
- **UNSUPPORTED:** no eligible evidence verifies it.
- **CONTRADICTED:** eligible evidence directly conflicts with it.

Unsupported does not mean false; it means the available trace cannot establish
the claim. Contradicted is stronger because conflicting evidence was found.

### How are citations produced?

Each evidence item already belongs to a logged tool step. If it supports a
claim, that step ID is added as the claim's citation. Failed calls cannot
generate supporting citations.

### How is the grounding score calculated?

For a non-empty claim set, it is the supported claim count divided by all
extracted claims. A claim marked unsupported or contradicted is not counted as
supported. With no claims, the score is unavailable rather than an invented
zero or perfect score.

### Why exclude failed outputs?

A timeout, malformed result, or deliberately corrupted observation is not a
reliable factual source. Allowing it to support an answer would make a failure
look like evidence.

### What is uncertainty estimation?

It summarizes grounding, tool success, recovery reliability, and—only when
multiple independent samples exist—self-consistency. The output is a
normalized research heuristic, not a calibrated probability.

### Why can a confident LLM answer still be wrong?

Language models can generate plausible text without checking it. Confidence in
wording is not the same as verified evidence, successful tool execution, or
truth.

### What are WARN and ABSTAIN?

**WARN** shares a useful, cited answer but explicitly discloses uncertainty.
**ABSTAIN** withholds factual claims when evidence is absent, unusable, or
contradictory.

### Why do we need a baseline and failure rates?

The baseline provides a comparison without the recovery and grounding
components. Testing 0%, 20%, and 40% injected failure rates shows behavior
under normal, moderate, and more severe controlled faults.

### What are task success, recovery rate, hallucination rate, and ECE?

- **Task success:** successful tasks divided by evaluated tasks.
- **Recovery rate:** successful recovery incidents divided by detected
  incidents; it is unavailable when no incident was detected.
- **Hallucination rate:** answers with an unsupported or contradicted factual
  claim divided by answers that contain factual claims.
- **ECE:** a binned comparison between confidence and observed correctness. In
  this project, confidence is `1 - uncertainty`; ECE is only an empirical
  diagnostic and does not prove calibration.

### What does an ablation study test?

It disables one component at a time—grounding, uncertainty, or localization—
while holding the other conditions steady. This helps examine the role of
each component, but the small experiment does not establish a general causal
effect.

### Why reproducibility?

Fixed local data, seeds, failure settings, and recorded run metadata allow
another person to rerun the same experimental conditions and inspect traces
instead of relying on undocumented claims.

## Result caveat

In the recorded seed-42 run, full TraceRecover completed 26/30 tasks at 20%
failures and 25/30 at 40%; the baseline completed 23/30 and 19/30. The
experiment has only 30 local tasks, uses authored tool plans, and runs on one
seed and one machine. These observations are not proof of statistical
significance or broad performance. See
[the evaluation report](experimental_evaluation.md) for all denominators and
limitations.
