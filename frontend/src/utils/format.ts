import type {
  AssetEventType,
  AssetOperation,
  AssetStatus,
  DeviceCategory,
  InventoryResult,
} from '@/types'

/**
 * 设备类型 → emoji（不依赖任何图标包）。
 *
 * ⚠️ 两个坑，2026-09-24 用真实 Chromium 实测出来的（Windows / Segoe UI Emoji）：
 *
 * 1. **主机和显示器绝对不能共用同一个码点。** 原先主机写 `U+1F5A5` + 变体选择符 `FE0F`、
 *    显示器写裸 `U+1F5A5` —— 看着是两个不同的字符，其实是一个码点。Chromium 对这两种写法
 *    fallback 到不同字体，结果**主机画出来是一台显示器、显示器画出来是一台立式设备**，
 *    整个错位。现在两者换成完全不同的码点。
 *
 * 2. **这几个码点在 Windows 上是豆腐块（□），别当候选**：
 *    `U+1F5B5`(SCREEN)、`U+1F5B4`(HARD DISK)、`U+1F5C5`(空文件柜)、`U+1F5C6`(空卡片盒)。
 *    它们名字看着最贴切，实际渲染不出来。
 *
 * 分配口径：Unicode 里**只有一张「电脑屏幕」图**（`U+1F5A5`），
 * 主机 / 显示器 / 一体机 / 电视 四个类型抢它一个，必然有让位 —— **谁最像就归谁**：
 * 显示器拿它（画面就是一块屏），主机改用立式柜体（机箱感），
 * 一体机改用屏键一体的老式电脑，电视用唯一的电视机符号。
 */
const TYPE_ICONS: Record<string, string> = {
  // —— 整机类 ——
  主机: '🗄️',
  台式机: '🗄️',
  笔记本: '💻',
  一体机: '🖳',
  服务器: '🖧',
  // —— 显示类 ——
  显示器: '🖥️',
  电视: '📺',
  投影仪: '📽️',
  // —— 外设 ——
  键盘: '⌨️',
  鼠标: '🖱️',
  打印机: '🖨️',
  扫描仪: '📠',
  摄像头: '📷',
  音箱: '🔊',
  // —— 网络设备 ——
  路由器: '📶',
  交换机: '📡',
  // —— 移动设备 ——
  手机: '📱',
  平板: '📱',
  // —— 其他 ——
  家具: '🪑',
  装饰摆件: '🖼️',
}

/**
 * 类型名没精确命中时，按关键字兜底。
 *
 * 用户能自己建设备类型（「显示器（曲面）」「NAS 存储」…），
 * 一张精确表盖不住 —— 但也不该全都退成 📦，那等于没图标。
 */
const TYPE_KEYWORDS: [string[], string][] = [
  [['一体机', '一体', 'all-in-one'], '🖳'],
  [['笔记本', '手提', 'laptop', 'macbook'], '💻'],
  [['主机', '台式', '塔式', '工控机'], '🗄️'],
  [['服务器', '存储', 'nas', '机架'], '🖧'],
  [['显示', '屏幕', 'monitor', '曲面'], '🖥️'],
  [['电视', 'tv', '智慧屏'], '📺'],
  [['投影'], '📽️'],
  [['打印'], '🖨️'],
  [['扫描'], '📠'],
  [['键盘'], '⌨️'],
  [['鼠标', '轨迹'], '🖱️'],
  [['路由', '网关', 'ap'], '📶'],
  [['交换', '防火墙'], '📡'],
  [['摄像', '相机', '监控'], '📷'],
  [['音', '扬声', '耳'], '🔊'],
  [['手机', '电话'], '📱'],
  [['平板'], '📱'],
  [['桌', '椅', '柜', '家具'], '🪑'],
  [['摆件', '装饰', '画'], '🖼️'],
]

export function typeIcon(name?: string | null): string {
  if (!name) return '📦'
  const exact = TYPE_ICONS[name]
  if (exact) return exact
  const lower = name.toLowerCase()
  for (const [words, icon] of TYPE_KEYWORDS) {
    if (words.some((w) => lower.includes(w))) return icon
  }
  return '📦'
}

export interface StatusMeta {
  label: string
  color: string
  bg: string
  border: string
}

export const STATUS_META: Record<AssetStatus, StatusMeta> = {
  in_use: { label: '在用', color: '#0f7b3f', bg: '#e8f7ee', border: '#b7e6c9' },
  idle: { label: '在库', color: '#0b6bcb', bg: '#eaf2ff', border: '#bed6ff' },
  repair: { label: '维修中', color: '#a15c00', bg: '#fff4e6', border: '#ffd9a3' },
  scrapped: { label: '已报废', color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3' },
}

export function statusMeta(status: string): StatusMeta {
  return STATUS_META[status as AssetStatus] ?? STATUS_META.idle
}

// --------------------------------------------------------------------------- //
// 设备类别
// --------------------------------------------------------------------------- //
export const CATEGORY_META: Record<DeviceCategory, { label: string; icon: string; color: string; bg: string }> = {
  // 图标与 TYPE_ICONS 里「主机 / 显示器」保持一致 —— 类别和类型看着是同一个东西，不能两套图
  host: { label: '主机类', icon: '🗄️', color: '#1f5fd0', bg: '#eaf2ff' },
  display: { label: '显示类', icon: '🖥️', color: '#7a4bd0', bg: '#f2ecff' },
  other: { label: '其他', icon: '📦', color: '#646a73', bg: '#f2f3f5' },
}

export function categoryMeta(category?: string | null) {
  return CATEGORY_META[(category as DeviceCategory) ?? 'other'] ?? CATEGORY_META.other
}

// --------------------------------------------------------------------------- //
// 状态流转操作
// --------------------------------------------------------------------------- //
export interface OperationMeta {
  label: string
  icon: string
  /** 按钮主色，用于区分破坏性操作 */
  tone: 'primary' | 'default' | 'warning' | 'danger'
  /** 需要填使用人 */
  needUser: boolean
  /** 需要二次确认 */
  confirm: boolean
  /** 一句话解释这个操作会做什么 */
  hint: string
}

export const OPERATION_META: Record<AssetOperation, OperationMeta> = {
  checkout: {
    label: '领用',
    icon: '📤',
    tone: 'primary',
    needUser: true,
    confirm: false,
    hint: '把设备交给某人使用，状态变为「在用」，并更新使用人。',
  },
  return: {
    label: '归还',
    icon: '📥',
    tone: 'default',
    needUser: false,
    confirm: false,
    hint: '设备交回，状态变为「在库」，使用人清空。可以顺手改存放位置。',
  },
  repair: {
    label: '送修',
    icon: '🔧',
    tone: 'warning',
    needUser: false,
    confirm: false,
    hint: '设备送修，状态变为「维修中」。',
  },
  repair_done: {
    label: '维修完成',
    icon: '✅',
    tone: 'primary',
    needUser: false,
    confirm: false,
    hint: '维修结束，状态回到「在库」，等待重新发放。',
  },
  scrap: {
    label: '报废',
    icon: '🗑️',
    tone: 'danger',
    needUser: false,
    confirm: true,
    hint: '设备报废，状态变为「已报废」。报废后不能领用，但可以恢复。',
  },
  restore: {
    label: '恢复入库',
    icon: '♻️',
    tone: 'default',
    needUser: false,
    confirm: false,
    hint: '把误报废的设备恢复成「在库」。',
  },
}

export function operationMeta(action: string): OperationMeta {
  return OPERATION_META[action as AssetOperation] ?? {
    label: action,
    icon: '•',
    tone: 'default',
    needUser: false,
    confirm: false,
    hint: '',
  }
}

// --------------------------------------------------------------------------- //
// 变更历史
// --------------------------------------------------------------------------- //
export interface EventMeta {
  icon: string
  color: string
  bg: string
}

export const EVENT_META: Record<string, EventMeta> = {
  create: { icon: '✨', color: '#1f5fd0', bg: '#eaf2ff' },
  update: { icon: '✏️', color: '#646a73', bg: '#f2f3f5' },
  status: { icon: '🔁', color: '#a15c00', bg: '#fff4e6' },
  pair: { icon: '🔗', color: '#7a4bd0', bg: '#f2ecff' },
  unpair: { icon: '✂️', color: '#7a7f87', bg: '#f2f3f5' },
  inventory: { icon: '📋', color: '#0f7b3f', bg: '#e8f7ee' },
  checkout: { icon: '📤', color: '#0f7b3f', bg: '#e8f7ee' },
  return: { icon: '📥', color: '#0b6bcb', bg: '#eaf2ff' },
  repair: { icon: '🔧', color: '#a15c00', bg: '#fff4e6' },
  repair_done: { icon: '✅', color: '#0f7b3f', bg: '#e8f7ee' },
  scrap: { icon: '🗑️', color: '#d54941', bg: '#fdecea' },
  restore: { icon: '♻️', color: '#0b6bcb', bg: '#eaf2ff' },
}

export function eventMeta(eventType: string): EventMeta {
  return EVENT_META[eventType] ?? { icon: '•', color: '#646a73', bg: '#f2f3f5' }
}

/** 让 TS 别抱怨 EVENT_META 的键没用上（资产事件的联合类型在这里收口） */
export const EVENT_TYPES: AssetEventType[] = [
  'create',
  'update',
  'status',
  'pair',
  'unpair',
  'inventory',
  'checkout',
  'return',
  'repair',
  'repair_done',
  'scrap',
  'restore',
]

// --------------------------------------------------------------------------- //
// 盘点结果
// --------------------------------------------------------------------------- //
export interface InventoryResultMeta extends StatusMeta {
  icon: string
}

export const INVENTORY_RESULT_META: Record<InventoryResult, InventoryResultMeta> = {
  pending: { label: '未盘', icon: '⏳', color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3' },
  checked: { label: '已盘', icon: '✅', color: '#0f7b3f', bg: '#e8f7ee', border: '#b7e6c9' },
  abnormal: { label: '异常', icon: '⚠️', color: '#d54941', bg: '#fdecea', border: '#f7c5c1' },
}

export function inventoryResultMeta(result: string): InventoryResultMeta {
  return INVENTORY_RESULT_META[result as InventoryResult] ?? INVENTORY_RESULT_META.pending
}

// --------------------------------------------------------------------------- //
// 操作审计
// --------------------------------------------------------------------------- //
export interface AuditVisual {
  icon: string
  color: string
  bg: string
}

/**
 * 审计动作 → 图标与配色。
 *
 * 不跟后端要图标 —— 后端只给业务标签（那是数据的一部分），
 * 配色和图标是纯展示层的事，换一套视觉不该动接口。
 */
const AUDIT_VISUALS: Record<string, AuditVisual> = {
  asset_create: { icon: '✨', color: '#1f5fd0', bg: '#eaf2ff' },
  asset_update: { icon: '✏️', color: '#646a73', bg: '#f2f3f5' },
  asset_delete: { icon: '🗑️', color: '#d54941', bg: '#fdecea' },
  asset_operate: { icon: '🔁', color: '#a15c00', bg: '#fff4e6' },
  asset_pair: { icon: '🔗', color: '#7a4bd0', bg: '#f2ecff' },
  asset_unpair: { icon: '✂️', color: '#7a7f87', bg: '#f2f3f5' },
  asset_rebind: { icon: '🔀', color: '#7a4bd0', bg: '#f2ecff' },
  asset_import: { icon: '📥', color: '#0f7b3f', bg: '#e8f7ee' },
  asset_export: { icon: '📤', color: '#0b6bcb', bg: '#eaf2ff' },
  inventory_create: { icon: '🧭', color: '#1f5fd0', bg: '#eaf2ff' },
  inventory_mark: { icon: '📋', color: '#0f7b3f', bg: '#e8f7ee' },
  inventory_close: { icon: '🏁', color: '#646a73', bg: '#f2f3f5' },
  inventory_reopen: { icon: '↩️', color: '#a15c00', bg: '#fff4e6' },
  inventory_delete: { icon: '🗑️', color: '#d54941', bg: '#fdecea' },
  type_create: { icon: '🏷️', color: '#1f5fd0', bg: '#eaf2ff' },
  type_update: { icon: '🏷️', color: '#646a73', bg: '#f2f3f5' },
  type_delete: { icon: '🏷️', color: '#d54941', bg: '#fdecea' },
  audit_export: { icon: '📤', color: '#0b6bcb', bg: '#eaf2ff' },
}

export function auditVisual(action: string): AuditVisual {
  return AUDIT_VISUALS[action] ?? { icon: '•', color: '#646a73', bg: '#f2f3f5' }
}

// --------------------------------------------------------------------------- //
// 批量导入
// --------------------------------------------------------------------------- //
export const IMPORT_ACTION_META: Record<
  string,
  { label: string; color: string; bg: string; border: string; icon: string }
> = {
  create: { label: '新增', icon: '✨', color: '#0f7b3f', bg: '#e8f7ee', border: '#b7e6c9' },
  update: { label: '更新', icon: '✏️', color: '#0b6bcb', bg: '#eaf2ff', border: '#bed6ff' },
  unchanged: { label: '无变化', icon: '➖', color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3' },
  error: { label: '错误', icon: '⚠️', color: '#d54941', bg: '#fdecea', border: '#f7c5c1' },
}

export function importActionMeta(action: string) {
  return IMPORT_ACTION_META[action] ?? IMPORT_ACTION_META.unchanged
}

/** 2026-09-24 → 2026-09-01（取当月 1 号，用作筛选的默认起始） */
export function monthStart(value = new Date()): string {
  const d = new Date(value)
  d.setDate(1)
  return toIsoDate(d)
}

export function toIsoDate(value: Date | string): string {
  if (typeof value === 'string') return value.slice(0, 10)
  const y = value.getFullYear()
  const m = String(value.getMonth() + 1).padStart(2, '0')
  const d = String(value.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

// --------------------------------------------------------------------------- //
// 通用格式化
// --------------------------------------------------------------------------- //
/** 2026-01-02 → 2026/01/02；空值 → — */
export function formatDate(value?: string | null): string {
  if (!value) return '—'
  return value.replace(/-/g, '/')
}

/** 2026-01-02T09:37:28 → 2026/01/02 09:37 */
export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const raw = value.replace('T', ' ')
  return raw.slice(0, 16).replace(/-/g, '/')
}

/** 只要月日时分：09/23 09:37（时间线里省地方） */
export function formatShortDateTime(value?: string | null): string {
  if (!value) return '—'
  const raw = value.replace('T', ' ')
  return raw.slice(5, 16).replace(/-/g, '/')
}

/** 相对时间：刚刚 / 3 分钟前 / 2 天前 / 2026/01/02 */
export function relativeTime(value?: string | null): string {
  if (!value) return '—'
  const then = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(then.getTime())) return value
  const diff = Date.now() - then.getTime()
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  return formatDate(value.slice(0, 10))
}

export function orDash(value?: string | number | null): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

export function warrantyText(asset: { warranty_until: string | null; warranty_days_left: number | null }): {
  text: string
  tone: 'ok' | 'warn' | 'expired' | 'none'
} {
  if (!asset.warranty_until) return { text: '未登记', tone: 'none' }
  const days = asset.warranty_days_left ?? 0
  if (days < 0) return { text: `已过保 ${Math.abs(days)} 天`, tone: 'expired' }
  if (days <= 90) return { text: `还剩 ${days} 天`, tone: 'warn' }
  return { text: `还剩 ${days} 天`, tone: 'ok' }
}

export function downloadImage(src: string, filename: string): void {
  const link = document.createElement('a')
  link.href = src
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 降级到 execCommand */
  }
  try {
    const input = document.createElement('textarea')
    input.value = text
    input.style.position = 'fixed'
    input.style.opacity = '0'
    document.body.appendChild(input)
    input.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(input)
    return ok
  } catch {
    return false
  }
}
