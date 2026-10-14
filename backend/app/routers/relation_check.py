"""关系核对接口 —— 把 `services/relation_check` 的纯函数接到 HTTP 上。

## 这一层只干三件事，别让它长胖

1. **取数**：把点位和它挂着的资产从库里捞出来，转成纯函数要的普通数据；
2. **叠例外**：读 `relation_exceptions`，按指纹判断每条确认**现在还有效吗**；
3. **写决定**：确认 / 撤销例外（这里才允许写库，而且只写例外这一张表）。

**核对结论本身没有任何写操作** —— 每次请求现算。所以接口里不存在
"刷新一下问题列表"这种动作，也没有可供前端置位的 `resolved` 字段。

## 为什么例外要按指纹判有效性，而不是查一个状态字段

见 `models.RelationException` 的类说明。要点：关系变了以后旧确认必须自动作废，
否则用户改了使用人却还看到"已确认"，会以为系统坏了。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Asset,
    AuditAction,
    AuditTarget,
    RelationException,
    Workstation,
)
from ..schemas_relations import (
    RelationAssetCheckOut,
    RelationExceptionCreate,
    RelationExceptionOut,
    RelationExceptionRevoke,
    RelationIssueOut,
    RelationSubjectOut,
    StationRelationCheckOut,
)
from ..services import relation_check
from ..services.audit import record_audit

router = APIRouter(prefix="/api/relation-checks", tags=["relation-checks"])


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #
def _get_station_or_404(db: Session, station_id: int) -> Workstation:
    station = db.get(Workstation, station_id)
    if station is None or not station.active:
        raise HTTPException(status_code=404, detail=f"点位 #{station_id} 不存在")
    return station


def _assets_of(db: Session, station_id: int) -> list[Asset]:
    return list(
        db.execute(
            select(Asset)
            .where(Asset.workstation_id == station_id)
            .order_by(Asset.asset_code.asc())
        ).scalars().all()
    )


def _to_inputs(station: Workstation, assets: list[Asset]) -> tuple[
    relation_check.StationInput, list[relation_check.AssetInput]
]:
    return (
        relation_check.StationInput(
            id=station.id, code=station.code, user_name=station.user_name
        ),
        [
            relation_check.AssetInput(id=a.id, code=a.asset_code, user_name=a.user_name)
            for a in assets
        ],
    )


# --------------------------------------------------------------------------- #
# 例外叠加
# --------------------------------------------------------------------------- #
def _load_exceptions(db: Session, station_id: int) -> dict[tuple[str, int], RelationException]:
    """当前点位下所有**未撤销**的例外，键 = (rule, asset_id)。"""
    rows = list(
        db.execute(
            select(RelationException).where(
                RelationException.workstation_id == station_id,
                RelationException.active.is_(True),
            )
        ).scalars().all()
    )
    return {(e.rule, int(e.asset_id)): e for e in rows}


def _exception_out(
    exception: RelationException, *, valid: bool
) -> RelationExceptionOut:
    return RelationExceptionOut(
        id=exception.id,
        rule=exception.rule,
        workstation_id=exception.workstation_id,
        asset_id=int(exception.asset_id),
        asset_scope=int(exception.asset_id) != 0,
        reason=exception.reason,
        valid=valid,
        created_by=exception.created_by,
        created_at=exception.created_at,
        checked_at=exception.checked_at,
    )


def _issue_out(
    issue: relation_check.Issue,
    exception: Optional[RelationException],
) -> RelationIssueOut:
    exc_out: Optional[RelationExceptionOut] = None
    suppressed = False
    if exception is not None:
        # 指纹一致 = 关系没变，这条确认仍然成立；不一致 = 已失效，差异重新冒出来
        valid = exception.fingerprint == issue.fingerprint
        exc_out = _exception_out(exception, valid=valid)
        suppressed = valid

    return RelationIssueOut(
        rule=issue.rule,
        rule_label=issue.rule_label,
        status=issue.status,
        status_label=issue.status_label,
        severity=issue.severity,
        severity_label=issue.severity_label,
        scope=issue.scope,
        asset_id=issue.asset_id,
        title=issue.title,
        detail=issue.detail,
        suggestion=issue.suggestion,
        fingerprint=issue.fingerprint,
        subjects=[
            RelationSubjectOut(
                kind=s.kind, id=s.id, code=s.code, user_name=s.user_name, label=s.label
            )
            for s in issue.subjects
        ],
        exception=exc_out,
        suppressed=suppressed,
    )


def build_check(db: Session, station: Workstation) -> StationRelationCheckOut:
    """跑一次核对并把例外叠上去。**纯读**。"""
    assets = _assets_of(db, station.id)
    station_input, asset_inputs = _to_inputs(station, assets)
    result = relation_check.check_station(station_input, asset_inputs)
    exceptions = _load_exceptions(db, station.id)

    issues = [
        _issue_out(issue, exceptions.get(issue.exception_key)) for issue in result.issues
    ]

    return StationRelationCheckOut(
        workstation_id=result.workstation_id,
        code=result.code,
        user_name=result.user_name,
        applicable=result.applicable,
        verdict=result.verdict,
        verdict_label=result.verdict_label,
        review_count=sum(1 for i in issues if i.status == relation_check.CheckStatus.MISMATCH and not i.suppressed),
        issues=issues,
        asset_checks=[
            RelationAssetCheckOut(
                asset_id=c.asset_id,
                asset_code=c.asset_code,
                asset_user=c.asset_user,
                state=c.state,
                state_label=c.state_label,
            )
            for c in result.asset_checks
        ],
        checked_at=datetime.now(),
    )


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@router.get(
    "/workstation/{station_id}",
    response_model=StationRelationCheckOut,
    summary="点位关系核对（点位使用人 ↔ 设备使用人）",
)
def check_workstation(station_id: int, db: Session = Depends(get_db)):
    """**现算，不落库。** 返回一致 / 差异 / 未登记 / 无法判断四种状态。

    差异只表示"待核对"，不会改任何数据，也不阻断任何资产操作。
    """
    station = _get_station_or_404(db, station_id)
    return build_check(db, station)


@router.get(
    "/exceptions",
    response_model=list[RelationExceptionOut],
    summary="某个点位的例外确认列表（含已失效的）",
)
def list_exceptions(
    workstation_id: int,
    db: Session = Depends(get_db),
    include_inactive: bool = False,
):
    station = _get_station_or_404(db, workstation_id)
    stmt = select(RelationException).where(
        RelationException.workstation_id == workstation_id
    )
    if not include_inactive:
        stmt = stmt.where(RelationException.active.is_(True))
    rows = list(db.execute(stmt.order_by(RelationException.id.desc())).scalars().all())
    assets = _assets_of(db, workstation_id)
    station_input, asset_inputs = _to_inputs(station, assets)
    result = relation_check.check_station(station_input, asset_inputs)
    current: dict[tuple[str, int], str] = {i.exception_key: i.fingerprint for i in result.issues}
    return [
        _exception_out(e, valid=current.get((e.rule, int(e.asset_id))) == e.fingerprint)
        for e in rows
    ]


@router.post(
    "/workstation/{station_id}/exceptions",
    response_model=StationRelationCheckOut,
    summary="确认一条关系差异为例外（理由必填）",
)
def confirm_exception(
    station_id: int,
    payload: RelationExceptionCreate,
    db: Session = Depends(get_db),
):
    """把这个点位的一条差异记为「已确认的例外」。

    - **理由必填**：没理由的例外等于把提示关掉，明天没人说得清为什么。
    - 提交的 `fingerprint` 必须与**当前**关系一致，否则 409 ——
      挡住「看着旧差异点确认、结果确认到了新差异上」这种误操作。
    - 一个 (rule, 设备) 只有一条有效例外：重复确认就地更新（刷新理由与指纹）。
    """
    station = _get_station_or_404(db, station_id)
    result = build_check(db, station)

    matched = next(
        (
            issue
            for issue in result.issues
            if issue.rule == payload.rule
            and (issue.asset_id or 0) == (payload.asset_id or 0)
        ),
        None,
    )
    if matched is None:
        raise HTTPException(
            status_code=404,
            detail="这条差异在当前数据里已经不存在了 —— 可能刚被人改过。刷新一下再操作。",
        )
    if matched.fingerprint != payload.fingerprint:
        raise HTTPException(
            status_code=409,
            detail="这条关系在你确认之前发生了变化，旧差异已经失效。请刷新核对结果后重新确认。",
        )

    asset_id = payload.asset_id or 0
    existing = db.execute(
        select(RelationException).where(
            RelationException.rule == payload.rule,
            RelationException.workstation_id == station.id,
            RelationException.asset_id == asset_id,
        )
    ).scalars().first()

    if existing is None:
        existing = RelationException(
            rule=payload.rule,
            workstation_id=station.id,
            asset_id=asset_id,
            reason=payload.reason or "",
            fingerprint=matched.fingerprint,
            active=True,
            created_by=payload.operator,
        )
        db.add(existing)
    else:
        existing.reason = payload.reason or ""
        existing.fingerprint = matched.fingerprint
        existing.active = True
        existing.created_by = payload.operator or existing.created_by
    existing.checked_at = datetime.now()

    target_label = matched.subjects[1].code if len(matched.subjects) > 1 else station.code
    record_audit(
        db,
        AuditAction.RELATION_EXCEPTION_CONFIRM,
        target_type=AuditTarget.WORKSTATION,
        target_id=station.id,
        target_label=station.code,
        summary=f"确认关系例外：{matched.title}（{target_label}）；理由：{payload.reason}",
        operator=payload.operator,
        detail={
            "workstation_id": station.id,
            "asset_id": asset_id,
            "rule": payload.rule,
            "reason": payload.reason,
            "fingerprint": matched.fingerprint,
        },
    )
    db.commit()
    return build_check(db, station)


@router.delete(
    "/exceptions/{exception_id}",
    response_model=StationRelationCheckOut,
    summary="撤销一条例外确认（软删，记录留着）",
)
def revoke_exception(
    exception_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = None,
):
    """撤销 = 把 `active` 置 0。**不删行** —— 「当初谁批的、后来怎么取消的」是历史。"""
    exception = db.get(RelationException, exception_id)
    if exception is None:
        raise HTTPException(status_code=404, detail=f"例外 #{exception_id} 不存在")

    station = _get_station_or_404(db, exception.workstation_id)
    exception.active = False
    exception.checked_at = datetime.now()

    record_audit(
        db,
        AuditAction.RELATION_EXCEPTION_REVOKE,
        target_type=AuditTarget.WORKSTATION,
        target_id=station.id,
        target_label=station.code,
        summary=f"撤销关系例外：{exception.reason}",
        operator=operator,
        detail={"exception_id": exception.id, "workstation_id": station.id,
                "rule": exception.rule, "asset_id": exception.asset_id},
    )
    db.commit()
    return build_check(db, station)
