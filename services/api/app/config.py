"""Runtime settings. Env var NAMES only; values come from the Lambda environment or Secrets Manager."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    region: str
    work_bucket: str
    decisions_table: str
    traces_table: str
    gsi1_name: str
    gsi2_name: str
    gsi3_name: str
    state_machine_arn: str
    agent_runtime_arn: str
    agent_runtime_qualifier: str
    agent_url: str
    judge_secret_id: str
    alias_secret_id: str
    alias_key_override: str
    public_base: str
    alias_team: bool
    team_secret_id: str
    judge_token_override: str
    team_token_override: str
    show_event_names: bool
    presign_ttl_s: int
    cors_origins: tuple[str, ...]
    local_mode: bool
    local_root: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    ttl = int(os.environ.get("CULL_PRESIGN_TTL_S", "600"))
    origins = tuple(o.strip() for o in os.environ.get("CULL_CORS_ORIGINS", "").split(",") if o.strip())
    return Settings(
        region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")),
        work_bucket=os.environ.get(
            "WORK_BUCKET", f"keepframe-work-{os.environ.get('AWS_ACCOUNT_ID', '<AWS_ACCOUNT_ID>')}-ap-south-1"
        ),
        # keepframe.store.tables reads DECISIONS_TABLE / TRACES_TABLE; accept both spellings.
        decisions_table=os.environ.get("CULL_DECISIONS_TABLE")
        or os.environ.get("DECISIONS_TABLE", "keepframe-decisions"),
        traces_table=os.environ.get("CULL_TRACES_TABLE")
        or os.environ.get("TRACES_TABLE", "keepframe-traces"),
        gsi1_name=os.environ.get("CULL_GSI1_NAME", "gsi1"),
        gsi2_name=os.environ.get("CULL_GSI2_NAME", "gsi2"),
        gsi3_name=os.environ.get("CULL_GSI3_NAME", "gsi3"),
        state_machine_arn=os.environ.get("CULL_STATE_MACHINE_ARN", ""),
        agent_runtime_arn=os.environ.get("CULL_AGENT_RUNTIME_ARN", ""),
        agent_runtime_qualifier=os.environ.get("CULL_AGENT_RUNTIME_QUALIFIER", ""),
        agent_url=os.environ.get("CULL_AGENT_URL", ""),
        judge_secret_id=os.environ.get("CULL_JUDGE_SECRET_ID", "keepframe/judge-token"),
        # HMAC key for client-facing photo aliases and signed image links (S-1). Plain string or {"key": "..."}.
        alias_secret_id=os.environ.get("CULL_ALIAS_SECRET_ID", "keepframe/alias-key"),
        alias_key_override=os.environ.get("CULL_ALIAS_KEY", ""),
        # Prefix for /img links. Empty = same origin as the API (the UI is served from the same HTTP API).
        public_base=os.environ.get("CULL_PUBLIC_BASE", "") or os.environ.get("CULL_LOCAL_PUBLIC_BASE", ""),
        alias_team=_flag("CULL_ALIAS_TEAM"),
        team_secret_id=os.environ.get("CULL_TEAM_SECRET_ID", "keepframe/team-token"),
        judge_token_override=os.environ.get("CULL_JUDGE_TOKEN", ""),
        team_token_override=os.environ.get("CULL_TEAM_TOKEN", ""),
        show_event_names=_flag("CULL_SHOW_EVENT_NAMES"),
        # Hard rule: presigned GETs never live longer than 15 minutes.
        presign_ttl_s=max(30, min(ttl, 900)),
        cors_origins=origins,
        local_mode=_flag("LOCAL_MODE"),
        local_root=os.environ.get("CULL_LOCAL_ROOT", ""),
    )
