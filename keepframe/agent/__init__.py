"""The KeepFrame agent: a Strands planner over the OpenCV 5 tools.

Modules
  context        RunContext: work set, per-photo evidence, counters (JSON-serialisable, lives in agent.state)
  policies       P1–P9 policy checks the hooks enforce (pure functions over RunContext + worker policies)
  hooks          caps, retries, policy enforcement + approval interrupts, trace emission
  toolset        Strands @tool wrappers over the plain tool functions (stub or real backend)
  fake_model     deterministic scripted planner (CULL_MODEL_PROVIDER=fake)
  models         provider switch bedrock (default) | anthropic (optional) | fake; Secrets Manager bootstrap
  strands_agent  build_agent / run_event_agent / resume_with_decisions
  run            CLI: python -m keepframe.agent.run --event-id X --run-id Y [--resume decisions.json]
"""
