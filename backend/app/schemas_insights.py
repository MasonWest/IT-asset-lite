"""问题中心的 DTO。

字段含义与"为什么这么定"见 `services/issues.py` 的模块头 —— 这里只做形状，
不放任何业务判断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class InsightSubjectOut(BaseModel):
    """一条问题涉及的对象。"""

    kind: str  # workstation | asset
    id: int
    code: str = ""
    user_name: Optional[str] = None
    label: str = ""


class InsightRuleOut(BaseModel):
    """规则级汇总 —— 界面上的"为什么值得关注"整句来自这里，前端一个字都不拼。"""

    rule: str
    label: str
    severity: str
    severity_label: str = ""
    source: str
    source_label: str = ""
    reason: str
    #: 该规则的**全部**问题数（**含**已被有效例外盖住的）
    count: int = 0
    #: 其中已被**有效**例外盖住的
    handled_count: int = 0
    #: `count - handled_count`，即"还需要看一眼"的条数
    pending: int = 0
    #: 只有当该规则下待办问题的落点是同一个时才给；否则为空，界面只提供"展开逐条"
    action_label: str = ""
    action_url: Optional[str] = None


class InsightProblemOut(BaseModel):
    """一条问题。

    ⚠️ `key` 与 `fingerprint` 是两件事：前者只随**对象身份**变（去重 / 定位 / 跨页面一致），
    后者随**关系状态**变（只用于判例外还算不算数）。**别拿后者当前者用。**
    """

    key: str
    rule: str
    rule_label: str = ""
    severity: str
    severity_label: str = ""
    source: str
    source_label: str = ""
    title: str
    detail: str = ""
    reason: str
    subjects: list[InsightSubjectOut] = []
    action_label: str
    action_url: str
    fingerprint: str = ""
    #: 已被**有效**例外盖住 → 不计入待办，但仍可查看 / 可撤销
    handled: bool = False
    exception_id: Optional[int] = None
    exception_reason: Optional[str] = None


class InsightCheckedOut(BaseModel):
    """这一轮**检查了什么**。

    专门给空状态用：页面不能说"没问题"就完事，要让人看见"这是被查出来的"，
    否则空状态跟"功能坏了"长得一模一样。
    """

    rules: list[str] = []
    stations: int = 0
    assets: int = 0


class InsightsOut(BaseModel):
    generated_at: datetime
    #: = Σ(by_rule.pending)，**只数"还需要看一眼"的**
    total: int = 0
    counts: dict[str, int] = {}
    checked: InsightCheckedOut = Field(default_factory=InsightCheckedOut)
    #: 全库口径，**不受 rule / severity 过滤影响**（汇总要稳定，钻取才跟着筛）
    by_rule: list[InsightRuleOut] = []
    items: list[InsightProblemOut] = []
    page: int = 1
    page_size: int = 20
    pages: int = 0
