"""资产套装聚合 —— 把「主机 + 它挂着的显示器」合并成列表里的一行。

这是**纯展示层的视角切换**，底层一台设备还是一台设备：各自的编号、状态、保修、
使用人一个字都不改，`asset_relations` 也不动一根毫毛。列表页勾上「套装聚合」时走
`/api/assets/grouped`，不勾还是走 `/api/assets` 的明细口径。

口径（2026-09-24 与业务方逐条确认）：

1. **只有 `asset_relations` 里 active 的主机 ↔ 显示器配对才算套装。**
   一体机 / 笔记本不参与 —— 它们不在任何 active 配对里，天然就是一行散设备，
   不需要为它们额外写规则。
2. **组内任意一台设备命中筛选，整组就出现，并且完整展示全部成员。**
   不能只显示"命中的那一半"：主机命中、显示器没命中时若只显示主机，
   用户会看到「未配显示器」——那是错误信息，比多显示一行更糟。
3. **状态不一致不阻断、不自动改**，只在行的 flags 里标一个 `status_mismatch` 交给前端轻提示。
   状态是资产级属性，套装没有权力替主机或显示器改它。
4. **排序键 = 组内最大的资产 id**。明细视图是 `Asset.id.desc()`（最新录入在前），
   聚合行取组内最新那台的 id 当排序键，切开关时上下位置不会乱跳。
5. **一台显示器挂多台主机（多主机）在当前数据模型下不可能** ——
   `pairing.bind()` 会先查 `active_host_relation_of()` 并 409 挡住，
   应用层保证「一台显示器同一时刻最多一条 active 关系」。
   这里仍然按**连通分量**划分而不是"以主机为根"，真出现脏数据也不会漏设备或抛异常。

**分页在聚合后的行上做**（服务端分页），不是先分页再聚合 ——
后者会把同一套装拆到两页，用户在任意一页都看不到完整的机器。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, AssetRelation, DeviceCategory

#: 组的类型标记
KIND_BUNDLE = "bundle"
KIND_SINGLE = "single"


@dataclass
class AssetBundle:
    """列表里的一行。

    套装 = 1 台主机 + N 台显示器；散设备 = 只有一台的退化情形。
    `primary` 是这一行的门面（套装取主机，散设备取它自己），
    其余成员放在 `monitors` / `extra_hosts` 里，前端展开看明细。
    """

    primary: Asset
    monitors: list[Asset] = field(default_factory=list)
    #: 只在脏数据下才非空（一台显示器挂了多台主机），正常情况下恒为空
    extra_hosts: list[Asset] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return KIND_BUNDLE if (self.monitors or self.extra_hosts) else KIND_SINGLE

    @property
    def members(self) -> list[Asset]:
        return [self.primary, *self.extra_hosts, *self.monitors]

    @property
    def sort_id(self) -> int:
        return max(a.id for a in self.members)

    @property
    def flags(self) -> dict[str, bool]:
        """需要前端轻提示、但**不阻断也不自动修**的几种情况。"""
        return {
            "multi_host": bool(self.extra_hosts),
            "multi_monitor": len(self.monitors) > 1,
            "status_mismatch": len({a.status for a in self.members}) > 1,
        }


@dataclass
class BundleResult:
    bundles: list[AssetBundle]
    #: 命中筛选的明细台数 —— 等于同一条件下 `/api/assets` 的 total
    matched_total: int
    #: 聚合后展开的总台数 —— 因为「整组出现」，它可能大于 matched_total
    expanded_total: int


def _category(asset: Asset) -> str:
    """用已 joined 的 device_type 取类别，不再查一次类型表。"""
    dtype = asset.device_type
    if dtype is None:
        return DeviceCategory.OTHER
    return dtype.category


def _components(db: Session) -> list[list[int]]:
    """按 active 配对关系把资产 id 划成连通分量（并查集）。

    同一显示器若出现多条 active 关系（理论上被应用层挡住），这里会把它和那几台
    主机并到同一组里，结果是多出一个 `multi_host` 标记 —— 不丢设备、不报错。
    """
    rows = db.execute(
        select(AssetRelation.host_id, AssetRelation.monitor_id)
        .where(AssetRelation.active.is_(True))
        .order_by(AssetRelation.id.asc())
    ).all()

    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        # 路径压缩：顺手把路上所有节点直接挂到根上，后续查询接近 O(1)
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for host_id, monitor_id in rows:
        a, b = find(int(host_id)), find(int(monitor_id))
        if a != b:
            parent[b] = a

    grouped: dict[int, list[int]] = defaultdict(list)
    for node in parent:
        grouped[find(node)].append(node)

    # 按组内最小 id 排序，保证同一份数据每次跑出来顺序一致（便于调试和测试）
    return [sorted(ids) for ids in sorted(grouped.values(), key=min)]


def build_bundles(db: Session, *, conditions: list) -> BundleResult:
    """命中筛选的资产 → 划分连通分量 → 组装成行。不做分页，调用方自己切。"""
    matched_ids: set[int] = set(db.execute(select(Asset.id).where(*conditions)).scalars().all())

    components = _components(db)

    # 一次性把要用到的资产查出来：命中的 + 图上的（图上那些可能是被"顺带展示"的成员）
    wanted: set[int] = set(matched_ids)
    for ids in components:
        wanted.update(ids)

    assets: dict[int, Asset] = {}
    if wanted:
        assets = {
            a.id: a
            for a in db.execute(select(Asset).where(Asset.id.in_(wanted))).scalars().all()
        }

    bundles: list[AssetBundle] = []
    consumed: set[int] = set()

    # ---- 套装：组内任意一台命中，整组出现 ----
    for ids in components:
        if not (set(ids) & matched_ids):
            continue
        members = [assets[i] for i in ids if i in assets]
        if not members:
            continue

        hosts = [a for a in members if _category(a) == DeviceCategory.HOST]
        # 显示类 + 类别被改坏成 other 的成员都算"显示器侧"，它们本来就是被主机挂着的
        others = [a for a in members if _category(a) != DeviceCategory.HOST]

        primary = max(hosts, key=lambda a: a.id) if hosts else max(others, key=lambda a: a.id)
        bundles.append(
            AssetBundle(
                primary=primary,
                monitors=[a for a in others if a.id != primary.id],
                extra_hosts=[a for a in hosts if a.id != primary.id],
            )
        )
        consumed.update(ids)

    # ---- 散设备：没参与任何配对的孤点，按明细口径各占一行 ----
    for aid in sorted(matched_ids - consumed):
        asset = assets.get(aid)
        if asset is not None:
            bundles.append(AssetBundle(primary=asset))

    bundles.sort(key=lambda b: b.sort_id, reverse=True)

    return BundleResult(
        bundles=bundles,
        matched_total=len(matched_ids),
        expanded_total=sum(len(b.members) for b in bundles),
    )
