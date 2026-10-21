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
  station_vs_asset_user: 'users',
  // 「在库」的设备，人和工位都说它在被这个人用 —— 三样信息互相打架。
  // 用双箭头（⇄）而不是警告三角：这条是「待核对」，不是「出错了」，
  // 三角在视觉上就是"系统觉得你错了"，和 severity=review 的语义相冲。
  asset_status_vs_holder: 'swap',
  asset_user_unregistered: 'help',
  asset_unbound: 'pin',
  asset_on_deleted_station: 'trash',
  relation_exception_stale: 'bandage',
  // 「记在某人手上，而这个人有好几个点位」—— 用问号，不是警告。
  // 它不是错误，只是位置信息不够（系统给的是候选，不是结论）。
  light_user_station_ambiguous: 'help',
}

export function ruleIcon(rule: InsightRule | string): string {
  return RULE_ICONS[rule] ?? 'dot'
}
