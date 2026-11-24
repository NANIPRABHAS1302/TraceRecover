# TraceRecover Live Demo Script (8–10 minutes)

Use PowerShell from the project root and the deterministic local provider. The
commands below use no API key or network service.

## 1. Introduce the pipeline (1 minute)

Explain that TraceRecover logs tool calls, detects and recovers failures,
checks claims against successful tool outputs, and then answers, warns, or
abstains. Point to [the architecture diagram](system_architecture.md).

## 2. Show a supported answer (1 minute)

```powershell
python app.py --provider local --question "What is the capital of France?"
```

Recorded output: claim `SUPPORTED`, evidence citation `step 1`, grounding
score `1.000`, uncertainty `0.000`, decision `ANSWER`.

## 3. Show a successful recovery (1 minute)

```powershell
python app.py --provider local --failure-rate 20 --seed 1 --question "What is the capital of France?"
```

With seed 1, the search call receives a simulated timeout. The retry succeeds;
the answer is cited to recovery trace step 2. The recorded run reports one
detected failure, one successful retry, and decision `ANSWER`.

## 4. Show WARN (1 minute)

```powershell
python app.py --provider local --failure-rate 20 --seed 1 `
  --answer-threshold 0.1 --abstain-threshold 0.55 `
  --question "What is the capital of France?"
```

The same evidence remains cited. The configured answer threshold is below the
measured uncertainty `0.147`, so this run returns `WARN` rather than silently
removing the answer.

## 5. Show unsupported evidence and abstention (1 minute)

```powershell
python app.py --provider local --question "Who discovered the fictional element unobtainium?"
```

The local search returns no usable evidence. The claim is `UNSUPPORTED`, has
no citation IDs, and the decision is `ABSTAIN`. The “no local evidence”
sentinel is not treated as factual support.

## 6. Show recovery exhaustion (1 minute)

```powershell
python app.py --provider local --failure-rate 100 --seed 42 `
  --max-recovery-attempts 1 `
  --question "What is the capital of France?"
```

The initial call and its one retry both fail. The final candidate has no
eligible support, so the system abstains; the failed attempts do not become
citations.

## 7. Show contradiction handling (1–2 minutes)

The saved controlled scenario at
`logs/phase8-scenarios/contradiction/` used a deterministic scripted planner:
it retrieved “The capital of France is Paris” and then proposed “The capital
of France is Rome.” The checker marked that claim `CONTRADICTED`, retained the
conflicting source at step 1, and chose `ABSTAIN`.

For a live rerun, execute this short snippet:

```powershell
@'
from agent.react_agent import ReactAgent
from config import LLMClient

class ContradictionPlanner:
    def next_action(self, question, observations, excluded_tools=None):
        if not observations:
            return {"thought": "Retrieve a source.", "action": "search",
                    "arguments": {"query": "What is the capital of France?"},
                    "final_answer": None}
        return {"thought": "Test a conflicting claim.", "action": "final",
                "arguments": {},
                "final_answer": "The capital of France is Rome."}

result = ReactAgent(client=LLMClient(provider="local"),
                    planner=ContradictionPlanner(),
                    log_directory="logs/phase8-scenarios/contradiction-live") \
    .run("What is the capital of France?")
print(result.grounding_report["claims"])
print(result.final_decision, result.final_response)
'@ | python -
```

## 8. Show multi-tool evidence (1 minute)

The saved evaluation task `MULTI-04` uses two independent tools. Its final
answer is `sum=7; product=12`; the saved result cites `sum=7` to step 1 and
`product=12` to step 2. Inspect the trace and claim table under
`logs/evaluation/phase8-run-20261112T183222398011Z/tracerecover-full/failure-0/MULTI-04/`.

## 9. Finish with measured evaluation (1 minute)

```powershell
python -m eval.run_all --seed 42
```

This runs all 30 tasks at 0%, 20%, and 40% for baseline, full TraceRecover,
and the three ablations. Results and SVG charts go to a new timestamped
directory. Discuss the measured results and limitations in
[the evaluation report](experimental_evaluation.md); do not describe the
heuristic uncertainty score as a calibrated probability.
