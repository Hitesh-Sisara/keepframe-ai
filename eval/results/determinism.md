# Agent determinism: E09 run twice

Planner claude-sonnet-5 through the Anthropic-compatible provider setting, real OpenCV tools on the work bucket, DynamoDB in LOCAL_MODE (nothing written to the live tables). Approvals: scripted approve-as-proposed (not human). Same worker output, same code, same thresholds; only the planner's sampling can differ.

| measure | value |
|---|---|
| photos decided by the agent loop in both runs | 24 (either run: 24; only in one: 0) |
| identical final verdicts | 83.33 % of photos decided in both (83.33 % of either) |
| identical reason-code sets | 58.33 % |
| same photos escalated | False (21 in both, 1 in one only) |
| identical proposals on photos escalated in both | 80.95 % |
| identical tool-call sequence | False |
| identical verdicts, decided_by=agent in both runs | 100.0 % of 2 |
| identical verdicts, decided_by=mixed in both runs | 100.0 % of 1 |
| identical verdicts, decided_by=scripted in both runs | 80.95 % of 21 |
| $ per event (list price, mean of runs) | 2.3483 |

| run | status | pauses | wall s | items | decided | verdicts | escalated | tool calls | model cycles | input tok | output tok | cycles with 0 output tok | $ list |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | done | 10 | 342.5 | 12 | 24 | keep 11, review 13 | 21 | 100 | 89 | 1,161,137 | 1,233 | 57 | 2.3346 |
| B | done | 11 | 342.8 | 12 | 24 | keep 11, reject 4, review 9 | 22 | 100 | 91 | 1,175,201 | 1,159 | 62 | 2.362 |

Prices: {'claude-sonnet-5': {'input': 2.0, 'output': 10.0, 'cache_write': 2.5, 'cache_read': 0.2}} (Anthropic list prices per MTok (claude-api model table, cached 2026-09-25)). If the endpoint reports 0 output tokens on tool-only turns, those cycles are counted above and the output-token and $ figures are lower bounds. `decided_by=scripted` rows are the escalated photos settled by the scripted approve-as-proposed reviewer, so their agreement measures the agent's proposals, not a person.
