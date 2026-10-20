"""问题中心接口 —— 把 `services/issues` 的纯函数接到 HTTP 上。

## 只读

**这个路由没有任何写操作，也不写审计。** 问题是从数据现算出来的视图，
不是一次操作（手册原话：建议是算出来的，不是一次变更）。

也没有"标记已读 / 已解决"这种接口 —— 那种接口的存在就意味着一个状态字段，
而状态字段存下来必然过期（红线 2）。

## 输入校验的口径

| 输入 | 处理 |
| --- | --- |
| `rule` 不认识 | **返回空集**，不 500（一个拼错的筛选条件不该让人看到报错） |
| `severity` 不认识 | 同上，返回空集 |
| `page_size` 越界 | **夹到 [1, 100]**，不 422（一个越界的页长不值得打断人） |
| `page` < 1 | 夹到 1 |
| `action.url` | 只由 `services/issues.py` 的规则模板生成，**从不由外部输入拼** |

`rule` / `severity` **只影响 `items`**：`by_rule` / `counts` / `total` / `checked`
永远是全库口径 —— 汇总要稳定，钻取才跟着筛。
"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Asset,
    AssetStatus,
    LightCategory,
    LightHolding,
    LightHolderKind,
    RelationException,
    Workstation,
)
from ..schemas_insights import (
    InsightCheckedOut,
    InsightProblemOut,
    InsightRuleOut,
    InsightsOut,
    InsightSubjectOut,
)
from ..services import issues as issues_service
from ..services import light as light_service
from ..services import relation_check as rc

router = APIRouter(prefix="/api/insights", tags=["insights"])

#: 页长上限。**夹住而不是报 422** —— 见模块头的口径表。
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


# --------------------------------------------------------------------------- #
# 取数（唯一有 IO 的地方）
# --------------------------------------------------------------------------- #
def _load_scan_inputs(db: Session):
    """把一次全库扫描要的东西捞出来，转成纯函数要的普通数据。

    三个查询，**没有 N+1**：点位一次、资产一次（按点位分组）、未挂点位的一次。
    """
    # 只取判定要用的列，不把整个 ORM 实体（含 joined 关系）拖出来
    stations = list(
        db.execute(
            select(Workstation.id, Workstation.code, Workstation.user_name)
            .where(Workstation.active.is_(True))
            .order_by(Workstation.code.asc())
        ).all()
    )
    station_rows = [
        rc.StationInput(id=int(r[0]), code=r[1], user_name=r[2]) for r in stations
    ]

    bound = list(
        db.execute(
            select(Asset.id, Asset.asset_code, Asset.user_name, Asset.workstation_id)
            .where(Asset.workstation_id.is_not(None))
            .order_by(Asset.workstation_id.asc(), Asset.asset_code.asc())
        ).all()
    )
    by_station: dict[int, list[rc.AssetInput]] = {}
    for r in bound:
        by_station.setdefault(int(r[3]), []).append(
            rc.AssetInput(id=int(r[0]), code=r[1], user_name=r[2])
        )

    scans = [
        issues_service.StationScan(
            station=st, assets=tuple(by_station.get(st.id, ()))
        )
        for st in station_rows
    ]

    # 没挂点位的资产：不含已报废（报废的机器不该催人去给它找座位）
    unbound = [
        issues_service.UnboundAsset(id=int(r[0]), code=r[1], user_name=r[2])
        for r in db.execute(
            select(Asset.id, Asset.asset_code, Asset.user_name)
            .where(
                Asset.workstation_id.is_(None),
                Asset.status != AssetStatus.SCRAPPED,
            )
            .order_by(Asset.asset_code.asc())
        ).all()
    ]

    # 挂在**已删除**点位上的资产：点位没了，归属对照也就没依据了
    orphans = [
        issues_service.OrphanAsset(
            id=int(r[0]),
            code=r[1],
            user_name=r[2],
            station_id=int(r[3]),
            station_code=r[4],
        )
        for r in db.execute(
            select(
                Asset.id,
                Asset.asset_code,
                Asset.user_name,
                Asset.workstation_id,
                Workstation.code,
            )
            .join(Workstation, Asset.workstation_id == Workstation.id, isouter=True)
            .where(
                Asset.workstation_id.is_not(None),
                or_(
                    Workstation.id.is_(None),
                    Workstation.active.is_(False),
                ),
            )
            .order_by(Asset.asset_code.asc())
        ).all()
    ]

    exceptions = [
        issues_service.ConfirmedException(
            id=int(e.id),
            rule=e.rule,
            workstation_id=int(e.workstation_id),
            asset_id=int(e.asset_id),
            reason=e.reason or "",
            fingerprint=e.fingerprint or "",
        )
        for e in db.execute(
            select(RelationException).where(RelationException.active.is_(True))
        ).scalars().all()
    ]

    # 记在人手上的轻资产：只有"这个使用人名下有 >1 个点位"才构成**推断歧义**。
    # ⚠️ 候选点位用与地图 / 点位侧栏**同一个** `station_ids_by_user` 算，
    #    不在这里另写一份 join —— 两处口径一旦漂移，"问题中心说有 2 个候选、
    #    地图只标 1 个"这种没人能解释的现象就会冒出来。
    user_holdings = db.execute(
        select(LightHolding.id, LightHolding.holder_user, LightCategory.name)
        .join(LightCategory, LightCategory.id == LightHolding.category_id)
        .where(
            LightHolding.holder_kind == LightHolderKind.USER,
            LightHolding.quantity > 0,
            LightHolding.holder_user.is_not(None),
        )
        .order_by(LightHolding.id.asc())
    ).all()
    by_user = light_service.station_ids_by_user(db, [r[1] for r in user_holdings])
    light_user_ambiguous = [
        issues_service.LightUserAmbiguity(
            holding_id=int(hid),
            user=str(user),
            category_name=name or "",
            station_codes=tuple(code for _sid, code in by_user.get(str(user).strip(), [])),
        )
        for hid, user, name in user_holdings
        if len(by_user.get(str(user).strip(), [])) > 1
    ]

    total_assets = int(db.scalar(select(func.count(Asset.id))) or 0)
    return scans, unbound, orphans, exceptions, light_user_ambiguous, total_assets


# --------------------------------------------------------------------------- #
# 转换
# --------------------------------------------------------------------------- #
def _subjects_out(problem: issues_service.Problem) -> list[InsightSubjectOut]:
    return [
        InsightSubjectOut(
            kind=s.kind, id=s.id, code=s.code, user_name=s.user_name, label=s.label
        )
        for s in problem.subjects
    ]


def _problem_out(problem: issues_service.Problem) -> InsightProblemOut:
    return InsightProblemOut(
        key=problem.key,
        rule=problem.rule,
        rule_label=problem.rule_label,
        severity=problem.severity,
        severity_label=rc.Severity.LABELS.get(problem.severity, problem.severity),
        source=problem.source,
        source_label=issues_service.IssueSource.LABELS.get(problem.source, problem.source),
        title=problem.title,
        detail=problem.detail,
        reason=problem.reason,
        subjects=_subjects_out(problem),
        action_label=problem.action_label,
        action_url=problem.action_url,
        fingerprint=problem.fingerprint,
        handled=problem.handled,
        exception_id=problem.exception_id,
        exception_reason=problem.exception_reason,
    )


def _rule_out(row: dict) -> InsightRuleOut:
    return InsightRuleOut(
        rule=row["rule"],
        label=row["label"],
        severity=row["severity"],
        severity_label=rc.Severity.LABELS.get(row["severity"], row["severity"]),
        source=row["source"],
        source_label=issues_service.IssueSource.LABELS.get(row["source"], row["source"]),
        reason=row["reason"],
        count=row["count"],
        handled_count=row["handled_count"],
        pending=row["pending"],
        action_label=row["action_label"],
        action_url=row["action_url"],
    )


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@router.get(
    "/issues",
    response_model=InsightsOut,
    summary="统一问题中心（**现算，不落库**）",
)
def list_issues(
    db: Session = Depends(get_db),
    rule: Optional[str] = Query(None, description="只看某个规则；不认识的值返回空集"),
    severity: Optional[str] = Query(None, description="只看某一档严重程度（review / info）"),
    page: int = Query(1, description="从 1 开始；小于 1 会被夹到 1"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, description=f"每页条数；越界夹到 [1, {MAX_PAGE_SIZE}]"),
):
    """一次请求返回：**汇总（全库口径）+ 一页问题明细**。

    前端的两段结构（规则卡 + 展开逐条）吃的是同一份数据 —— 不重复请求。
    """
    (
        scans,
        unbound,
        orphans,
        exceptions,
        light_user_ambiguous,
        total_assets,
    ) = _load_scan_inputs(db)

    all_problems = issues_service.build_problems(
        scans,
        unbound=unbound,
        orphans=orphans,
        exceptions=exceptions,
        light_user_ambiguous=light_user_ambiguous,
    )
    by_rule = issues_service.summarize(all_problems)
    counts = issues_service.count_pending(all_problems)

    # 过滤（不认识的值 = 空集，不是报错）
    items = all_problems
    if rule:
        items = [p for p in items if p.rule == rule]
    if severity:
        items = [p for p in items if p.severity == severity]

    size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    current = max(1, int(page or 1))
    pages = ceil(len(items) / size) if items else 0
    start = (current - 1) * size
    window = items[start : start + size]

    return InsightsOut(
        generated_at=datetime.now(),
        total=sum(counts.values()),
        counts=counts,
        checked=InsightCheckedOut(
            rules=[spec.label for spec in issues_service.RULES],
            stations=len(scans),
            assets=total_assets,
        ),
        by_rule=[_rule_out(r) for r in by_rule],
        items=[_problem_out(p) for p in window],
        page=current,
        page_size=size,
        pages=pages,
    )
