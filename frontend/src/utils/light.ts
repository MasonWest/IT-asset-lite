/**
 * 轻资产前端的公共口径。
 *
 * ⚠️ 这里的操作流程表是**后端 `LightOp.FLOWS`（models.py）的镜像**，
 * 唯一作用是收窄对话框里的可选项、挡住明显不合法的组合。
 * 真正的校验以后端为准 —— 两边不一致时以后端为准，别来改这个文件"迁就"界面。
 */

import type { LightHolderKind, LightOp } from '@/types'

/** 引用类型在界面上的称呼。`none` = 系统外部 / 已注销（入库的来源、报损的去向） */
export const HOLDER_KIND_META: Record<LightHolderKind, { label: string; hint: string }> = {
  none: { label: '系统外部', hint: '新采购进来 / 彻底报废出去' },
  stock: { label: '库存', hint: '仓库里的存量' },
  user: { label: '使用人', hint: '直接发给某个人（不关联主机）' },
  asset: { label: '正式资产', hint: '装到 / 放在某台主机上' },
  workstation: { label: '资产点位', hint: '放到某个工位 / 房间里' },
}

export interface LightOpMeta {
  value: LightOp
  label: string
  /** 一句话说明这个操作干什么，对话框里跟着选项走 */
  hint: string
}

/**
 * 操作清单。顺序即对话框里的展示顺序（按业务频率排，不按字母）。
 *
 * 「分配」只对人、「安装」只对物是**刻意不重叠**的（见后端 FLOWS 注释）：
 * 两条都允许 stock → X 的话，会出现"分配一台主机"这种语义上讲不通、
 * 但数据库照收的记录，将来对账没人说得清那是安装还是分配。
 */
export const LIGHT_OP_META: LightOpMeta[] = [
  { value: 'stock_in', label: '入库', hint: '新采购 / 找回的配件进库存' },
  { value: 'allocate', label: '分配', hint: '从库存直接发给某个人' },
  { value: 'attach', label: '安装', hint: '装到某台正式资产上，或放到某个点位' },
  { value: 'transfer', label: '转移', hint: '换手：换个人用，或从人到机器 / 机器到人，总量不变' },
  { value: 'detach', label: '拆卸', hint: '从主机上拆下来，退回库存' },
  { value: 'return', label: '归还', hint: '从使用人 / 资产 / 点位退回库存' },
  { value: 'adjust', label: '调整', hint: '盘点后的库存校正（盘盈 / 盘亏）' },
  { value: 'scrap', label: '报损', hint: '彻底退出管理（不占库存、无持有方）' },
]

export function opMeta(op: LightOp): LightOpMeta {
  return LIGHT_OP_META.find((m) => m.value === op) ?? LIGHT_OP_META[0]
}

/** 每种操作允许的 (from, to) 组合。`null` 对应后端的 None（系统外部 / 注销）。 */
export const LIGHT_FLOWS: Record<LightOp, [LightHolderKind | null, LightHolderKind | null][]> = {
  stock_in: [[null, 'stock']],
  allocate: [['stock', 'user']],
  return: [
    ['user', 'stock'],
    ['asset', 'stock'],
    ['workstation', 'stock'],
  ],
  attach: [
    ['stock', 'asset'],
    ['stock', 'workstation'],
    [null, 'asset'],
    [null, 'workstation'],
  ],
  detach: [
    ['asset', 'stock'],
    ['workstation', 'stock'],
  ],
  transfer: [
    // 直接换手：本来在张三那儿，现在归李四（不必先绕回库存）
    ['user', 'user'],
    ['user', 'asset'],
    ['user', 'workstation'],
    ['asset', 'user'],
    // 机器到机器（B022 → B023）、点位到点位（W23 → W25）都是真实业务动作，
    // 漏了它们就只能绕"先拆卸回库存、再安装过去"，一次挪位被记成两次出入库。
    ['asset', 'asset'],
    ['asset', 'workstation'],
    ['workstation', 'user'],
    ['workstation', 'asset'],
    ['workstation', 'workstation'],
  ],
  scrap: [
    ['stock', null],
    ['user', null],
    ['asset', null],
    ['workstation', null],
  ],
  adjust: [
    [null, 'stock'],
    ['stock', null],
  ],
}

/** 某个操作下，给定 from 之后还能选哪些 to（去重，保持表里的顺序） */
export function toKindsFor(op: LightOp, from: LightHolderKind | null): (LightHolderKind | null)[] {
  const seen = new Set<LightHolderKind | null>()
  const out: (LightHolderKind | null)[] = []
  for (const [f, t] of LIGHT_FLOWS[op] ?? []) {
    if (f !== from) continue
    if (seen.has(t)) continue
    seen.add(t)
    out.push(t)
  }
  return out
}

/** 某个操作下，给定 to 之后还能选哪些 from */
export function fromKindsFor(op: LightOp, to: LightHolderKind | null): (LightHolderKind | null)[] {
  const seen = new Set<LightHolderKind | null>()
  const out: (LightHolderKind | null)[] = []
  for (const [f, t] of LIGHT_FLOWS[op] ?? []) {
    if (t !== to) continue
    if (seen.has(f)) continue
    seen.add(f)
    out.push(f)
  }
  return out
}

/** 组合是否合法（对话框提交前的最后一道自检，后端还会再校一遍） */
export function flowAllows(
  op: LightOp,
  from: LightHolderKind | null,
  to: LightHolderKind | null,
): boolean {
  return (LIGHT_FLOWS[op] ?? []).some(([f, t]) => f === from && t === to)
}

/** 每次打开对话框生成一个提交令牌 —— 重复提交后端 409，不会二次入账 */
export function newRequestId(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  // 老浏览器兜底：时间戳 + 随机数，够用（防的是手抖连点，不是防攻击）
  return `r-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * 持有方 → 图标名（给 `AppIcon` 的 `name`）—— **唯一实现**。
 *
 * 卡片、列表、流水时间线三处都要按"东西在谁那儿"给图标。以前各写一份三元表达式，
 * 多一种持有方（比如将来加"部门"）就得改三个地方、漏一个就是白图标。
 * 认不出的 kind 一律给盒子：界面不该因为数据多了一种可能就空一块。
 */
export function holderIconName(kind: string | null | undefined): string {
  switch (kind) {
    case 'stock':
      return 'box'
    case 'user':
      return 'user'
    case 'asset':
      return 'screen'
    case 'workstation':
      return 'pin'
    default:
      return 'box'
  }
}
