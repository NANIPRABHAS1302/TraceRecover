# TraceRecover Phase 4 Validation & Error Analysis (20261124T160745Z)

- Canonical Benchmark Integrity (`eval/tasks.json` SHA-256): `7e3a535c6609021ace84acc86e62fbc641ca6f567feac77a2d5b457f61f5050c`
- Mission 1–2 Open-Ended Questions Tested: `28` (Unique Query IDs: `28`, Unique Trace IDs: `28`, Stale Answer Leaks: `0`)
- Verdict Counts: `{"PASS": 20, "HONEST_ABSTAIN": 8, "RUBRIC_REVIEW": 0, "FAIL": 0}`
- Mission 7 Unseen Eval Run: `unseen-eval-20261124T160705Z-d1d97a` (26 tasks, seed 2026)

## Query-by-Query Outcomes & Error Analysis

| ID | Domain | Decision | Tools | Unsupported Claims | Verdict | Note |
| --- | --- | --- | --- | ---: | --- | --- |
| P4-01 | general_knowledge | ANSWER | search | 0 | **PASS** | Verified |
| P4-02 | general_knowledge | ANSWER | search | 0 | **PASS** | Verified |
| P4-03 | transformer_architecture | ABSTAIN | none | 1 | **HONEST_ABSTAIN** | Expected under offline deterministic provider without external search or live LLM. |
| P4-04 | transformer_architecture | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | No live external sources retrieved/enabled; abstained transparently. |
| P4-05 | mathematics | ANSWER | calculator | 0 | **PASS** | Verified |
| P4-06 | mathematics | ANSWER | calculator | 0 | **PASS** | Verified |
| P4-07 | programming | ANSWER | python_exec | 0 | **PASS** | Verified |
| P4-08 | programming | ANSWER | python_exec | 0 | **PASS** | Verified |
| P4-09 | programming | ABSTAIN | none | 1 | **HONEST_ABSTAIN** | Expected under offline deterministic provider without external search or live LLM. |
| P4-10 | technical_problem_solving | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | No live external sources retrieved/enabled; abstained transparently. |
| P4-11 | technical_problem_solving | ANSWER | python_exec | 0 | **PASS** | Verified |
| P4-12 | multi_step | ANSWER | mock_api, calculator | 0 | **PASS** | Verified |
| P4-13 | multi_step | ANSWER | search, calculator | 0 | **PASS** | Verified |
| P4-14 | follow_up_context | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | No live external sources retrieved/enabled; abstained transparently. |
| P4-15 | follow_up_context | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | Follow-up context resolved; offline abstention without external search. |
| P4-16 | local_kb_retrieval | ANSWER | search | 0 | **PASS** | Verified |
| P4-17 | local_kb_retrieval | ANSWER | search | 0 | **PASS** | Verified |
| P4-18 | api_lookup | ANSWER | mock_api | 0 | **PASS** | Verified |
| P4-19 | api_lookup | ANSWER | mock_api | 0 | **PASS** | Verified |
| P4-20 | external_research | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | No live external sources retrieved/enabled; abstained transparently. |
| P4-21 | current_information | ABSTAIN | external_search | 1 | **HONEST_ABSTAIN** | No live external sources retrieved/enabled; abstained transparently. |
| P4-22 | ambiguous_question | ABSTAIN | none | 2 | **PASS** | Verified |
| P4-23 | ambiguous_question | ABSTAIN | none | 1 | **PASS** | Verified |
| P4-24 | missing_evidence | ABSTAIN | search | 1 | **PASS** | Verified |
| P4-25 | missing_evidence | ABSTAIN | search | 1 | **PASS** | Verified |
| P4-26 | contradictory_evidence | ABSTAIN | search | 1 | **PASS** | Verified |
| P4-27 | unsupported_request | ABSTAIN | none | 1 | **PASS** | Verified |
| P4-28 | injected_tool_failure | ANSWER | calculator | 0 | **PASS** | Recovered via ['RETRY'] and verified answer 480. |
