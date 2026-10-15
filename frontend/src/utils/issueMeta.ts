/**
 * 问题中心的展示元数据 —— 图标 / 配色 / 文案。
 *
 * ## 配色的一条硬规矩：**不用红色**
 *
 * 问题中心里绝大多数条目的语义是"值得看一眼"，不是"出错了"（手册 §二.2：异常不等于错误）。
 * 一旦刷红，用户第一次用就会把这个页面当成噪音源 —— 而它本来是要帮人回答
 * "现在有什么值得关注"的。
 *
 * | 档 | 用色 | 场景 |
 * | --- | --- | --- |
 * | `review` | 琥珀 | 需要人看一眼（差异 / 挂在已删点位） |
 * | `info` | 灰蓝 | 提示（账没填全 / 没挂点位） |
 *
 * 颜色刻意与 `RelationCheckCard`（P0 那张卡）保持同一套琥珀，两个入口看起来才是同一个系统。
 */

import type { InsightRule, InsightSeverity } from '@/types'

export interface SeverityMeta {
  label: string
  icon: string
  color: string
  bg: string
  border: string
}

export const SEVERITY_META: Record<string, SeverityMeta> = {
  review: {
    label: '待核对',
    icon: '◐',
    color: '#b45309',
    bg: '#fffdf5',
    border: '#fde68a',
  },
  info: {
    label: '提示',
    icon: '·',
    color: '#475569',
    bg: '#f8fafc',
    border: '#e2e8f0',
  },
  ok: {
    label: '正常',
    icon: '●',
    color: '#0f7b3f',
    bg: '#f0fdf4',
    border: '#bbf7d0',
  },
}

export function severityMeta(severity: string): SeverityMeta {
  return SEVERITY_META[severity] ?? SEVERITY_META.info
}

/** 每条规则的图标。让一堆规则卡能靠形状区分，而不是只能读字。 */
const RULE_ICONS: Record<string, string> = {
  station_vs_asset_user: '👥',
  asset_user_unregistered: '❓',
  asset_unbound: '📍',
  asset_on_deleted_station: '🗑️',
  relation_exception_stale: '🩹',
}

export function ruleIcon(rule: InsightRule | string): string {
  return RULE_ICONS[rule] ?? '•'
}
