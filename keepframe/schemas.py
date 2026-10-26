"""Pydantic v2 models shared across components: manifest rows, tool results,
decisions, groups, trace lines (docs/AGENT.md "Trace schema") and the run summary.

Tools return plain dicts (`Model(...).model_dump()`), so the Strands wrapper and the worker
see identical JSON. Models are permissive on input (extra keys allowed on manifest rows) and
strict on the vocabulary that the UI/metrics key on (verdicts, reason codes, labels).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from keepframe.config import reason_codes


class ReasonCode(StrEnum):
    """Fixed reason-code vocabulary. Must match config/reason_codes.yaml exactly (checked at import)."""

    BLUR_MOTION = "BLUR_MOTION"
    BLUR_FOCUS = "BLUR_FOCUS"
    EYES_CLOSED = "EYES_CLOSED"
    EXPOSURE_UNDER = "EXPOSURE_UNDER"
    EXPOSURE_OVER = "EXPOSURE_OVER"
    LOW_CONTRAST = "LOW_CONTRAST"
    DUPLICATE_OF = "DUPLICATE_OF"
    BURST_NOT_BEST = "BURST_NOT_BEST"
    NO_SUBJECT = "NO_SUBJECT"
    KEEP_BEST_OF_GROUP = "KEEP_BEST_OF_GROUP"
    KEEP_SINGLETON = "KEEP_SINGLETON"
    REVIEW_LOW_CONFIDENCE = "REVIEW_LOW_CONFIDENCE"
    REVIEW_POLICY_GROUP_EYES = "REVIEW_POLICY_GROUP_EYES"
    REVIEW_ARTISTIC_INTENT = "REVIEW_ARTISTIC_INTENT"


_YAML_CODES = set(reason_codes())
if _YAML_CODES != {c.value for c in ReasonCode}:
    raise ImportError(f"ReasonCode enum out of sync with config/reason_codes.yaml: {_YAML_CODES ^ set(ReasonCode)}")


def duplicate_of(photo_id: str) -> str:
    return f"{ReasonCode.DUPLICATE_OF.value}:{photo_id}"


def validate_reason_code(code: str) -> str:
    """Accept a bare code or the parameterised DUPLICATE_OF:<photo_id>."""
    base, _, param = code.partition(":")
    if base not in ReasonCode.__members__:
        raise ValueError(f"unknown reason code {code!r}")
    if base == ReasonCode.DUPLICATE_OF and not param:
        raise ValueError("DUPLICATE_OF needs a photo id: DUPLICATE_OF:<photo_id>")
    if base != ReasonCode.DUPLICATE_OF and param:
        raise ValueError(f"{base} takes no parameter")
    return code


Verdict = Literal["keep", "reject", "review"]
DecidedBy = Literal["agent", "human", "worker", "scripted", "lead_test"]  # scripted/lead_test are never a person
HumanSource = Literal["ui", "file", "http", "scripted", "lead_test"]
NON_HUMAN_SOURCES = ("scripted", "lead_test")  # answered, but not a photographer judgement
SharpLabel = Literal["sharp", "borderline", "blurry"]
ExposureLabel = Literal["ok", "under", "over", "low_contrast"]
EyeState = Literal["open", "closed", "partial", "unknown"]
FaceSource = Literal["frsInfo", "rekognition", "yunet"]
Roi = Literal["full", "faces", "saliency"]
Variant = Literal["processed", "original"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- manifest


class FrsFace(BaseModel):
    model_config = ConfigDict(extra="allow")
    bbox: list[float] = Field(description="[l, t, w, h] as ratios of frs_src_w/h")
    det_score: float | None = None
    min_px: float | None = None


class RekFace(BaseModel):
    model_config = ConfigDict(extra="allow")
    bbox: list[float] = Field(description="[l, t, w, h] ratios of the processed image")
    confidence: float | None = None


def to_epoch_s(value: Any) -> float | None:
    """clicked_at/created_at arrive as ISO strings, epoch seconds or epoch millis."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).timestamp()
    if isinstance(value, int | float):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    s = str(value).strip()
    try:
        return to_epoch_s(float(s))
    except ValueError:
        pass
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()


class PhotoRecord(BaseModel):
    """One line of manifests/<eventId>.jsonl (docs/DATA.md §3).

    extra="allow" is deliberate: the exporter adds fields (exif_bucket) and future columns must not break
    old workers. Real-data facts (12 events): mfaces is always null, rek_faces always empty; frs_faces
    bboxes are ratios of the processed image (frs_src_w/h == width/height).
    """

    model_config = ConfigDict(extra="allow")

    photo_id: str
    event_id: str
    album: str | None = None
    name: str | None = None
    clicked_at: str | float | int | None = None
    created_at: str | float | int | None = None
    camera: str | None = None
    lens: str | None = None
    width: int | None = None
    height: int | None = None
    original_width: int | None = None
    original_height: int | None = None
    original_kb: float | None = None
    md5: str | None = None
    processed_key: str | None = None
    original_key: str | None = None
    exif_key: str | None = None
    mfaces: int | None = None
    frs_status: str | None = None
    frs_faces: list[FrsFace] = Field(default_factory=list)
    frs_src_w: int | None = None
    frs_src_h: int | None = None
    rek_faces: list[RekFace] = Field(default_factory=list)
    processed_url: str | None = None
    original_url: str | None = None
    exif_bucket: str | None = None  # which source bucket the sidecar came from (rich/small), set by the exporter

    @field_validator("frs_faces", "rek_faces", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return [] if v is None else v

    def known_face_count(self) -> int | None:
        """Faces known from source metadata: frsInfo first (mfaces is null in real data), then mfaces."""
        status = (self.frs_status or "").lower()
        if status == "indexed":
            return len(self.frs_faces)
        if status == "no_faces":
            return 0
        if self.mfaces is not None:
            return int(self.mfaces)
        if self.rek_faces:
            return len(self.rek_faces)
        return None

    def clicked_ts(self) -> float | None:
        return to_epoch_s(self.clicked_at)

    def created_ts(self) -> float | None:
        return to_epoch_s(self.created_at)


class EventMeta(BaseModel):
    """manifests/<eventId>.meta.json."""

    model_config = ConfigDict(extra="allow")
    event_id: str
    name: str | None = None
    category: str | None = None
    event_date: str | None = None
    whitelabel_id: str | None = None
    sort_by: str | None = None
    n_photos: int | None = None
    n_with_original: int | None = None
    cameras: list[str] = Field(default_factory=list)
    exported_at: str | None = None


# --------------------------------------------------------------------------- tool results


class TimeSpan(_Model):
    start: str | None = None
    end: str | None = None
    seconds: float | None = None


class IngestResult(_Model):
    n_photos: int
    n_with_original: int
    n_with_frs: int
    cameras: list[str]
    time_span: TimeSpan
    n_missing_raw: int = 0
    missing_raw: list[str] = Field(default_factory=list)


class Failure(_Model):
    photo_id: str
    error: str


class HashesResult(_Model):
    computed: int
    cached: int
    failures: list[Failure] = Field(default_factory=list)


class Group(_Model):
    group_id: str
    photo_ids: list[str]
    size: int
    span_s: float


class ClusterResult(_Model):
    n_groups: int
    groups: list[Group]
    singletons: list[str]
    exact_duplicates: dict[str, str] = Field(default_factory=dict, description="photo_id -> md5 twin kept")


class SharpnessResult(_Model):
    lap_var: float
    tenengrad: float
    fft_hf: float
    brisque: float | None = None
    label: SharpLabel
    confidence: float
    blur_kind: Literal["motion", "focus"] | None = None
    motion_ratio: float | None = None
    roi: Roi = "full"
    roi_used: Roi = "full"
    variant: Variant = "processed"
    variant_fallback: bool = False
    votes: dict[str, str] = Field(default_factory=dict)


class ExposureResult(_Model):
    clip_lo_pct: float
    clip_hi_pct: float
    mean_L: float
    std_L: float
    entropy: float
    label: ExposureLabel
    confidence: float
    no_subject: bool = False
    subject_L: float | None = None
    subject_std_L: float | None = None
    subject_source: Literal["faces", "highlights"] | None = None
    under_flag: bool = False  # label "under" AND subject dark/flat AND (event-relative) dark -> EXPOSURE_UNDER
    subject_clip_hi: float | None = None  # max over face boxes of the fraction at L >= clip_hi_bin
    p5_L: float | None = None
    over_flag: bool = False   # label "over" AND subject clipped AND (event-relative) bright -> EXPOSURE_OVER


class FaceOut(_Model):
    face_id: str
    bbox_px: list[float]
    score: float | None = None
    landmarks5: list[list[float]] | None = None
    source: FaceSource
    min_px: float | None = None


class FacesResult(_Model):
    faces: list[FaceOut]
    n_faces: int
    image_w: int | None = Field(default=None, description="width of the frame bbox_px refers to")
    image_h: int | None = None


class EyeFace(_Model):
    face_id: str
    left_open_p: float | None = None
    right_open_p: float | None = None
    state: EyeState
    confidence: float | None = None
    min_px: float | None = None
    is_main: bool | None = None


class EyesResult(_Model):
    per_face: list[EyeFace]
    any_main_subject_closed: bool
    confidence: float
    main_subject_face_id: str | None = None


class FaceQualityFace(_Model):
    face_id: str
    fiqa: float | None = None
    lap_var: float | None = None
    minPx: float | None = None
    good: bool | None = None
    identity_group: int | None = None


class FaceQualityResult(_Model):
    per_face: list[FaceQualityFace]
    main_subject_face_id: str | None = None


class RankEntry(_Model):
    photo_id: str
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)


class BestResult(_Model):
    best_photo_id: str
    ranking: list[RankEntry]
    tie: bool
    margin: float


class CompositionResult(_Model):
    nima_mean: float | None = None
    nima_std: float | None = None
    subject_centrality: float | None = None


class EnhanceResult(_Model):
    s3_key: str
    ops_applied: list[str]
    delta_brisque: float | None = None


class ApprovalItem(_Model):
    photo_id: str | None = None
    group_id: str | None = None
    proposed: Any
    reason: str
    confidence: float
    contact_sheet_key: str | None = None


class ApprovalResult(_Model):
    decisions: list[dict[str, Any]]


class WriteDecisionResult(_Model):
    written: int
    verdict_counts: dict[str, int]


class ContactSheetResult(_Model):
    key: str
    n_tiles: int
    width: int
    height: int


# --------------------------------------------------------------------------- decisions / groups / runs


class Decision(_Model):
    photo_id: str
    event_id: str | None = None
    verdict: Verdict
    reason_codes: list[str] = Field(min_length=1)
    reason_text: str = Field(default="", max_length=140)
    confidence: float = Field(ge=0.0, le=1.0)
    group_id: str | None = None
    decided_by: DecidedBy = "worker"
    human_source: HumanSource | None = None  # how the answer arrived (only for human/scripted)
    agent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)  # agent's own proposed confidence
    run_id: str | None = None
    scores: dict[str, Any] = Field(default_factory=dict)
    needs_approval: bool = False

    @field_validator("reason_codes")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return [validate_reason_code(c) for c in v]


class GroupResult(_Model):
    group_id: str
    members: list[str]
    size: int
    span_s: float
    best_photo_id: str | None = None
    margin: float | None = None
    tie: bool = False
    ranking: list[RankEntry] = Field(default_factory=list)
    needs_approval: bool = False
    contact_sheet_key: str | None = None
    tied_candidates: list[str] = Field(default_factory=list, description="keeper + rivals for the KEEP slot")
    approval_reasons: list[str] = Field(default_factory=list)


TraceType = Literal["run_header", "tool_call", "tool_result", "model_reasoning", "interrupt", "interrupt_resumed",
                    "decision", "error", "run_end"]


class TraceLine(BaseModel):
    """docs/AGENT.md "Trace schema". Optional fields vary by `type`; extra keys allowed for forward compat."""

    model_config = ConfigDict(extra="allow")
    run_id: str
    seq: int = Field(ge=0)
    ts: str
    type: TraceType
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    text: str | None = None
    model: str | None = None
    opencv: str | None = None
    cv2_file: str | None = None
    latency_ms: float | None = None
    decisions: list[dict[str, Any]] | None = None


class StageStat(_Model):
    n: int = 0
    total_ms: float = 0.0
    p50_ms: float | None = None
    p95_ms: float | None = None
    wall_ms: float = 0.0


class RunSummary(_Model):
    run_id: str
    event_id: str
    status: Literal["ok", "partial", "failed"] = "ok"
    started_at: str
    ended_at: str
    wall_s: float
    n_photos: int
    n_groups: int
    n_singletons: int
    verdict_counts: dict[str, int]
    reason_counts: dict[str, int]
    escalation_count: int
    opencv_version: str
    cv2_file: str
    opencv_hal: str | None = None  # short Custom HAL label, e.g. kleidicv-26.03+carotene
    thresholds_sha: str
    flavor: Literal["stock", "cool"]
    instance_type: str
    image_sha: str | None = None  # container image digest sha256:<hex> (env or ECS task metadata)
    results_run_prefix: str | None = None  # results/<eventId>/<runId>/ holding this run's files
    faces_available: bool
    workers: int
    stages: dict[str, StageStat]
    failures: list[Failure] = Field(default_factory=list)
    images_per_s: float | None = None
    s3_get_count: int = 0       # object-store calls (0 in LOCAL_MODE); the summary.json write itself is excluded
    s3_put_count: int = 0
    s3_head_count: int = 0
    s3_list_count: int = 0
    s3_get_bytes: int = 0
    s3_put_bytes: int = 0
