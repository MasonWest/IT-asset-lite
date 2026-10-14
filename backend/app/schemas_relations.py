"""关系核对 + 例外确认 + 点位迁移的请求 / 响应结构。

单独一个文件而不是往 schemas.py / schemas_workstations.py 里堆：
那两个已经 600 / 300 多行，再加两百行只会让它们继续膨胀。

命名与 `services/relation_check.py` 的 dataclass 一一对应（`*Out` 是它的对外投影），
所以「纯函数返回什么、接口就吐什么」—— 中间不再有一层翻译，也就不存在
"测试测的和线上跑的不是同一份"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .schemas import _blank_to_none

# --------------------------------------------------------------------------- #
# 关系核对
# --------------------------------------------------------------------------- #


class RelationSubjectOut(BaseModel):
    """一条问题涉及的对象。"""

    kind: str  # workstation | asset
    id: int
    code: str
    user_name: Optional[str] = None
    label: str = ""


class RelationExceptionOut(BaseModel):
    """一条例外确认。`valid=False` 表示关系已经变了、这条确认不再生效。"""

    id: int
    rule: str
    workstation_id: int
    #: 0 表示点位级例外（见 models.RelationException 的说明）
    asset_id: int
    asset_scope: bool = False
    reason: str
    valid: bool = True
    created_by: Optional[str] = None
    created_at: datetime
    checked_at: Optional[datetime] = None


class RelationIssueOut(BaseModel):
    rule: str
    rule_label: str
    status: str
    status_label: str
    severity: str
    severity_label: str
    scope: str  # station | asset
    asset_id: Optional[int] = None
    title: str
    detail: str
    suggestion: str
    #: 关系指纹。前端原样回传，用于确认 / 重新核对。
    fingerprint: str
    subjects: list[RelationSubjectOut] = []
    #: 已确认的例外（可能 valid=False，即已失效）
    exception: Optional[RelationExceptionOut] = None
    #: 因有效例外而**被压住**：不必再提示，但用户看得到这是被谁确认掉的
    suppressed: bool = False


class RelationAssetCheckOut(BaseModel):
    asset_id: int
    asset_code: str
    asset_user: Optional[str] = None
    state: str
    state_label: str


class StationRelationCheckOut(BaseModel):
    """一个点位的关系核对结果。**每次现算，不落库。**"""

    workstation_id: int
    code: str
    user_name: Optional[str] = None
    #: 有没有可核对的对象（没有挂载设备时 False）
    applicable: bool
    verdict: str
    verdict_label: str
    #: 需要人看一眼的条数（mismatch）
    review_count: int = 0
    issues: list[RelationIssueOut] = []
    asset_checks: list[RelationAssetCheckOut] = []
    checked_at: datetime


class RelationExceptionCreate(BaseModel):
    """确认一条例外。**理由必填** —— 没理由的例外就是把提示关掉。"""

    rule: str = Field(min_length=1, max_length=48)
    #: 单台设备级传设备 id；点位级留空
    asset_id: Optional[int] = None
    fingerprint: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=255)
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("reason", "operator", mode="before")(_blank_to_none)


class RelationExceptionRevoke(BaseModel):
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("operator", mode="before")(_blank_to_none)


# --------------------------------------------------------------------------- #
# 点位迁移
# --------------------------------------------------------------------------- #
class AssetMoveOut(BaseModel):
    """一台设备要从哪挪到哪。"""

    asset_id: int
    asset_code: str
    asset_user: Optional[str] = None
    from_workstation_id: Optional[int] = None
    from_workstation_code: Optional[str] = None
    to_workstation_id: Optional[int] = None
    to_workstation_code: Optional[str] = None
    #: 该设备当前（迁移前）的关系核对状态，给用户一个"它会怎样"的预期
    relation_state: str = ""


class RelocationPreviewOut(BaseModel):
    """迁移预览。**绝不写任何数据**（要求：迁移预览不修改任何业务数据）。"""

    mode: str  # assets | station
    mode_label: str = ""
    ok: bool = False
    #: 阻止执行的问题（有它就不能落库）
    conflicts: list[str] = []
    #: 需要用户确认的差异 / 影响 —— 不阻止，但必须显式点头
    confirmations: list[str] = []
    moves: list[AssetMoveOut] = []
    noop_count: int = 0
    #: 点位级的变更说明（mode=station 时才有内容）：改使用人 / 退役源点位…
    station_changes: list[str] = []
    source: Optional[dict] = None
    target: Optional[dict] = None
    #: 预览态指纹。执行时带回来，不一致说明期间有人动过数据 → 409，让用户重新预览。
    preview_token: str = ""
    affected_asset_count: int = 0


class RelocationPreviewRequest(BaseModel):
    """预览请求。字段与执行请求共用一套语义，只是没有令牌和确认开关。"""

    mode: str
    asset_ids: list[int] = []
    source_workstation_id: Optional[int] = None
    target_workstation_id: Optional[int] = None
    move_assets: bool = True
    move_user: bool = False
    retire_source: bool = False

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        allowed = {"assets", "station"}
        if v not in allowed:
            raise ValueError(f"mode 只能是 {sorted(allowed)} 之一")
        return v


class RelocationApplyRequest(BaseModel):
    """正式执行。`preview_token` 是**防止"预览完被人抢先改了"与"重复提交"**的关键。"""

    mode: str
    #: mode=assets
    asset_ids: list[int] = []
    #: mode=station
    source_workstation_id: Optional[int] = None
    target_workstation_id: Optional[int] = None
    #: mode=station 的开关
    move_assets: bool = True
    move_user: bool = False
    retire_source: bool = False
    #: 预览时拿到的令牌，原样带回
    preview_token: str = Field(default="", max_length=128)
    operator: Optional[str] = Field(default=None, max_length=64)
    #: 用户明知有差异仍要执行时为 True（预览里列出 confirmations 之后）
    acknowledge_warnings: bool = False

    _clean = field_validator("operator", mode="before")(_blank_to_none)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        allowed = {"assets", "station"}
        if v not in allowed:
            raise ValueError(f"mode 只能是 {sorted(allowed)} 之一")
        return v


class RelocationApplyResult(BaseModel):
    ok: bool
    mode: str
    moved: int = 0
    moved_codes: list[str] = []
    station_changes: list[str] = []
    #: 执行后重新核对受影响点位的结论（要求：迁移后 P0 检查结果与最新数据一致）
    relation_checks: list[StationRelationCheckOut] = []
    summary: str = ""
