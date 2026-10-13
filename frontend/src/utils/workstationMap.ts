/**
 * 点位地图的纯函数与常量。
 *
 * 从这里拆出来的理由：这些规则**独立于 Vue 也能讲清楚**，而且改动风险集中在它们身上
 * （网格对齐、状态映射、导出格式）。放在 `.vue` 里就得靠读模板猜。
 */

import type { Facing, MapState, Workstation } from '@/types'
import { hasOwner } from '@/utils/format'

/** 点位格子尺寸，与原型一致 */
export const DESK_W = 80
export const DESK_H = 56

/**
 * 吸附容差与视觉网格。
 *
 * ⚠️ 视觉网格（`#grid-backdrop` 的 background-size）**必须等于这里**：
 * 原型踩过这个坑 —— 视觉上画的是 20px 网格、吸附却按 4px 走，
 * 用户拖完肉眼看不出"对齐了"，只觉得很随便。
 */
export const SNAP = 12
export const GRID = 20

/** 画布兜底尺寸。真实值由 `GET /api/workstations` 的 canvas 字段给 */
export const FALLBACK_CANVAS = { width: 960, height: 1320, grid: GRID }

/**
 * 点位状态 → 视觉元信息。
 *
 * 五种状态里前三种来自后端 `inventory_items.result` 的聚合，
 * 后两种（`empty` / `not_in_scope`）是**中性态**：
 *   - `empty` 这位置没有资产 —— 它是"没有东西可盘"，**不是"盘过了没问题"**。
 *     把空位染成绿色是错误信息（与"使用人写 `/` 显示成灰色未填"同一条原则）。
 *   - `not_in_scope` 有资产，但不在当前选中的这次盘点里 —— **不是"漏盘"**。
 *     这两者必须分得开：漏盘是盘点场景里最需要警觉的结论。
 *
 * 所以中性态一律用灰、且不与"正常"共用绿色。
 */
export interface StateMeta {
  label: string
  /** 角标文字（画在格子上） */
  badge: string
  color: string
  soft: string
}

export const STATE_META: Record<MapState, StateMeta> = {
  checked: { label: '已盘', badge: '✓ 正常', color: '#10b981', soft: '#ecfdf5' },
  abnormal: { label: '异常', badge: '⚠️ 异常', color: '#ef4444', soft: '#fef2f2' },
  pending: { label: '待盘', badge: '待盘', color: '#f59e0b', soft: '#fffbeb' },
  empty: { label: '空位', badge: '空位', color: '#94a3b8', soft: '#f8fafc' },
  not_in_scope: { label: '不在本次范围', badge: '本次不盘', color: '#a1a1aa', soft: '#fafafa' },
}

export function stateMeta(state: string): StateMeta {
  return STATE_META[state as MapState] ?? STATE_META.pending
}

/** 中性态：不参与盘点计数，也不进进度分母 */
export const NEUTRAL_STATES: MapState[] = ['empty', 'not_in_scope']

export function isNeutral(state: string): boolean {
  return NEUTRAL_STATES.includes(state as MapState)
}

/* -------------------------------------------------------------------------
   点位关系层：有人 × 有资产 两个正交维度派生出来的**概况**（v2.2.0 落地）
   -------------------------------------------------------------------------
   ⚠️ **它不是"点位状态"，是"点位关系概况"。** 区别不是文字游戏：
   - 状态是**存在表里的一列**，要人维护、会过期；
   - 概况是**每次渲染时现算**的两个布尔值的组合。
   一旦把它当状态落库，资产一挂上来/一摘走，库里那列就成了过期信息
   （本项目红线 2：一份数据两处存，迟早对不上）。所以这里**只有函数，没有字段**。
   与盘点状态（checked/abnormal/pending）是**两个独立维度**，不要合成一个状态。
   ------------------------------------------------------------------------- */

/** 四种关系。键名就是两个维度的组合，读代码不用回头查表 */
export type RelationKey = 'person_asset' | 'person_none' | 'none_asset' | 'none_none'

export interface RelationMeta {
  /** 短标签，用于筛选条（要窄，手机上四个都放得下） */
  short: string
  /** 完整标签，用于图例与提示 */
  label: string
  /** 一句话说清它意味着什么 —— 这是"概况"而不是"状态"的关键 */
  hint: string
  /** 图例符号。与 CSS 里的四种视觉一一对应 */
  glyph: string
}

/**
 * 视觉刻意**克制**：只有蓝（有人）和灰（无人）两个色系，
 * "有没有资产"用**边框虚实 + 底色深浅**表达，不再加第三、第四个颜色。
 *
 * 四种一屏都是主色系就不会变成"彩色地图"，
 * 而实心 / 虚线 / 实心浅 / 点线 的差别在 80×56 的格子上也看得清。
 */
export const RELATION_META: Record<RelationKey, RelationMeta> = {
  person_asset: {
    short: '有人·有资产',
    label: '有人 · 有资产',
    hint: '正常使用中的点位',
    glyph: '●',
  },
  person_none: {
    short: '有人·无资产',
    label: '有人 · 无资产',
    hint: '人来了设备还没到，值得关注',
    glyph: '◐',
  },
  none_asset: {
    short: '无人·有资产',
    label: '无人 · 有资产',
    hint: '公共设备 / 备用机，很正常',
    glyph: '○',
  },
  none_none: {
    short: '无人·无资产',
    label: '无人 · 无资产',
    hint: '空置点位 / 预留点位',
    glyph: '·',
  },
}

/** 展示顺序：从"最正常"到"最空"。图例、筛选条、统计都按这个顺序 */
export const RELATION_ORDER: RelationKey[] = [
  'person_asset',
  'person_none',
  'none_asset',
  'none_none',
]

/** 只要点位上最小的那部分信息：有没有归属的人、有没有挂着设备 */
export interface RelationInput {
  user_name: string | null
  asset_count: number
}

/**
 * 派生这个点位属于哪种关系。
 *
 * 「有人」的口径与地图现有的 `occupied` 完全一致（`hasOwner()`：空值和 `/` 都算没归属）——
 * 派生函数与渲染类如果用两套判据，同一个格子会既"有人"又"空闲"。
 *
 * ⚠️ CS 里写过"有人 = room 或 user_name 有值"，这里**只取 user_name**：
 * `room` 填的是「总经理办公室 / 前台」这类**地点**，不是"这个位置归谁"；
 * 真实库里两条 room 全都同时有 user_name，加进来不改变结果，只会让语义变浑。
 * 真要用 room 表达"这位置已被预留给某个部门"，那应该是第三个维度，不是这一层。
 */
export function relationOf(station: RelationInput): RelationKey {
  const occupied = hasOwner(station.user_name)
  const hasAsset = (station.asset_count ?? 0) > 0
  if (occupied) return hasAsset ? 'person_asset' : 'person_none'
  return hasAsset ? 'none_asset' : 'none_none'
}

/**
 * 按一个轴吸附：先找已存在的对齐基准（同列 / 同排），没命中再落视觉网格。
 * 返回命中的基准值（用于画参考线）或 null。
 */
export function snapAxis(value: number, anchors: number[]): { v: number; hit: number | null } {
  let best: number | null = null
  let bestDist = Infinity
  for (const anchor of anchors) {
    const dist = Math.abs(value - anchor)
    if (dist <= SNAP && dist < bestDist) {
      best = anchor
      bestDist = dist
    }
  }
  if (best !== null) return { v: best, hit: best }
  return { v: Math.round(value / GRID) * GRID, hit: null }
}

/** 收进画布边界（留出格子本身的高度/宽度） */
export function clampToCanvas(
  x: number,
  y: number,
  canvas: { width: number; height: number },
): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(canvas.width - DESK_W, Math.round(x / 4) * 4)),
    y: Math.max(0, Math.min(canvas.height - DESK_H, Math.round(y / 4) * 4)),
  }
}

/**
 * 导出的点位 JSON（`asset-space-map/workstations@1`）。
 *
 * ⚠️ **刻意不导出 assets**。点位文件只描述点位本身；真实资产归属是
 * `assets.workstation_id` 反着指向点位的，不是由这份文件决定。
 * 原型早期版本在这里带上了本地演示资产，结果那份 JSON 被导进后端时
 * 差点凭空造出一批假设备。
 */
export function buildExportPayload(
  nodes: Workstation[],
  canvas: { width: number; height: number },
): Record<string, unknown> {
  return {
    schema: 'asset-space-map/workstations@1',
    exported_at: new Date().toISOString(),
    canvas: { width: canvas.width, height: canvas.height },
    summary: {
      total: nodes.length,
      assigned: nodes.filter((n) => (n.user_name || '').trim()).length,
      empty: nodes.filter((n) => !(n.user_name || '').trim()).length,
    },
    workstations: nodes.map((n, i) => ({
      workstation_id: `ws-${n.id}`,
      code: n.code,
      user_name: n.user_name || '',
      room: n.room || '',
      x: n.x,
      y: n.y,
      facing: n.facing,
      seq: i + 1,
    })),
  }
}

/** 朝向的中文，用于侧栏 */
export function facingLabel(facing: Facing | string): string {
  return facing === 'up' ? '朝上' : '朝下'
}

/** 形如 `W01` / `A3` 的编码：字母前缀 + 数字 */
const CODE_RE = /^([A-Za-z]*)(\d+)$/

function parseCode(code: string): { prefix: string; num: number } | null {
  const m = CODE_RE.exec((code || '').trim())
  if (!m) return null
  return { prefix: m[1].toUpperCase(), num: Number(m[2]) }
}

/**
 * 点位编码排序：`W1 < W2 < W10 < W20`。
 *
 * ⚠️ **不能用 `localeCompare` 直接排** —— 那是字典序，`W10` 会排在 `W2` 前面，
 * 62 个点位里 W1x/W2x 整段插错位置，比不排还乱。必须按前缀 + 数值比。
 *
 * 为什么要在前端排而不是改后端：`GET /api/workstations` 的顺序是
 * `y, x, id`，那是**地图渲染**的顺序（同一排的挨着给），本身没有任何问题。
 * 「按编码选」是下拉这个**使用场景**的诉求，不是点位数据的固有属性 ——
 * 把排序塞进接口，等于让地图去迁就一个下拉的偏好，还会动到已有的接口断言。
 *
 * 未命中「字母+数字」的自定义编码统一垫在最后（`W01…W62` 是主流，先给它们让位），
 * 彼此之间按中文/字母序。
 */
export function compareStationCode(a: string, b: string): number {
  const pa = parseCode(a)
  const pb = parseCode(b)
  if (pa && pb) {
    if (pa.prefix !== pb.prefix) return pa.prefix < pb.prefix ? -1 : 1
    return pa.num - pb.num
  }
  if (pa) return -1
  if (pb) return 1
  return (a || '').localeCompare(b || '', 'zh-Hans-CN')
}

/**
 * 品牌 + 型号拼成一句，**并去重**。
 *
 * 真实数据里型号常把品牌名又写了一遍（`brand=联想` + `model=联想LI2364A`），
 * 直接拼会显示成「联想 联想LI2364A」。
 */
export function brandModel(brand?: string | null, model?: string | null): string {
  const b = (brand || '').trim()
  const m = (model || '').trim()
  if (!b && !m) return '未填品牌型号'
  if (!m) return b
  if (!b) return m
  if (m.toLowerCase().startsWith(b.toLowerCase())) return m
  return `${b} ${m}`
}

/** 画布坐标 → 人话位置描述，用于列表里显示 */
export function positionText(x: number, y: number): string {
  return `(${Math.round(x)}, ${Math.round(y)})`
}
