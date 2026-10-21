import type {
  AssetEventType,
  AssetOperation,
  AssetStatus,
  DeviceCategory,
  DeviceType,
  InventoryResult,
} from '@/types'
import { isIconName, FALLBACK_ICON } from '@/utils/icons'
import type { IconName } from '@/utils/icons'

/**
 * 设备类型 → **图标标识**（`utils/icons.ts::ICON_PATHS` 的 key）。
 *
 * ## 取值分三层（顺序不能变）
 *
 * 1. **库里已确认的图标**（`device_types.icon`）—— 用户选过就用它，这是唯一"配置"；
 * 2. **按名称推断的建议图标** —— 用户还没选过时给一个像样的默认值。
 *    ⚠️ **推断结果只用于显示，绝不写回数据库**：把推断写进库等于替用户做了
 *    他没做过的决定，而推断本身会错（"NAS 存储"能认出来，改名成"阵列柜"就废了）。
 *    维护界面据此打"未确认"标记，用户确认后才落到第 1 层。
 * 3. **兜底盒子** —— 名称里一个关键字都没命中。
 *
 * ## 为什么图标不再直接映射成 emoji
 *
 * 原来这里返回的是 emoji 字符，有三个真实问题（2026-09-24 实测记录）：
 * ① **大部分设备类 emoji 码点默认是"文本呈现"**（`Emoji_Presentation=No`），
 *   要靠不可见的 `FE0F` 变体选择符才显示成彩色，字体回退一变就成豆腐块 □；
 * ② emoji 靠系统字体渲染，headless / 精简版 Windows 上直接缺失（`AppIcon.vue` 的注释记过）；
 * ③ 按名字匹配意味着**用户改名等于丢图标**。
 * 现在统一返回 SVG 标识、由 `AppIcon` 渲染，并且优先读库里那个与名称解耦的配置。
 */
const TYPE_ICONS: Record<string, IconName> = {
  // —— 整机类 ——
  主机: 'tower',
  台式机: 'tower',
  笔记本: 'laptop',
  一体机: 'aio',
  服务器: 'server',
  // —— 显示类 ——
  显示器: 'monitor',
  电视: 'tv',
  投影仪: 'projector',
  // —— 外设 ——
  键盘: 'keyboard',
  鼠标: 'mouse',
  打印机: 'printer',
  扫描仪: 'scanner',
  摄像头: 'camera',
  音箱: 'speaker',
  // —— 网络设备 ——
  路由器: 'router',
  交换机: 'switch',
  // —— 移动设备 ——
  手机: 'smartphone',
  平板: 'tablet',
  // —— 其他 ——
  家具: 'chair',
  装饰摆件: 'frame',
}

/**
 * 类型名没精确命中时，按关键字兜底。
 *
 * 用户能自己建设备类型（「显示器（曲面）」「NAS 存储」…），一张精确表盖不住 ——
 * 但也不该全都退成盒子，那等于没图标。
 *
 * ⚠️ **这张表只是"建议图标"的来源**（第 2 层），不是最终配置。
 * 顺序有意义：先匹配到的赢，所以更具体的词要排在前面。
 */
const TYPE_KEYWORDS: [string[], IconName][] = [
  [['一体机', '一体', 'all-in-one'], 'aio'],
  [['笔记本', '手提', 'laptop', 'macbook'], 'laptop'],
  [['主机', '台式', '塔式', '工控机'], 'tower'],
  [['服务器', '存储', 'nas', '机架'], 'server'],
  [['显示', '屏幕', 'monitor', '曲面'], 'monitor'],
  [['电视', 'tv', '智慧屏'], 'tv'],
  [['投影'], 'projector'],
  [['打印'], 'printer'],
  [['扫描'], 'scanner'],
  [['键盘'], 'keyboard'],
  [['鼠标', '轨迹'], 'mouse'],
  [['路由', '网关', 'ap'], 'router'],
  [['交换', '防火墙'], 'switch'],
  [['摄像', '相机', '监控'], 'camera'],
  [['音', '扬声', '耳'], 'speaker'],
  [['手机', '电话'], 'smartphone'],
  [['平板'], 'tablet'],
  [['桌', '椅', '柜', '家具'], 'chair'],
  [['摆件', '装饰', '画'], 'frame'],
]

/**
 * 按名称猜一个**建议**图标（第 2 层）。
 *
 * 单独导出：维护界面要区分"显示的是建议还是已确认"，得知道自己拿到的是哪一层。
 */
export function suggestTypeIcon(name?: string | null): IconName {
  if (!name) return FALLBACK_ICON
  const exact = TYPE_ICONS[name]
  if (exact) return exact
  const lower = name.toLowerCase()
  for (const [words, icon] of TYPE_KEYWORDS) {
    if (words.some((w) => lower.includes(w))) return icon
  }
  return FALLBACK_ICON
}

/**
 * 取图标时真正需要的最小信息。
 *
 * 各接口给的是**扁平字段**（`device_type_name` / `device_type_icon`），
 * 不是嵌套的 `DeviceType` 对象，所以这里按结构收类型，不强制调用方去拼对象。
 */
export interface IconSource {
  name?: string | null
  icon?: string | null
}

/**
 * 取某一台设备类型该用的图标 —— **全站唯一入口**。
 *
 * 接受的三种形态：
 * - `DeviceType` / `{name, icon}` —— 有已确认图标就用它；
 * - 纯名称字符串 —— 只能走推断（老调用点的兜底）；
 * - 空 —— 兜底盒子。
 */
export function typeIcon(deviceType?: DeviceType | IconSource | string | null): IconName {
  if (!deviceType) return FALLBACK_ICON
  if (typeof deviceType === 'string') return suggestTypeIcon(deviceType)
  const saved = deviceType.icon
  if (saved && isIconName(saved)) return saved
  // 库里存了前端不认识的标识（旧版本写入、或手工改库）→ 退回名称推断。
  // 不直接给盒子：名称往往还猜得出来，那比空白更有用。
  return suggestTypeIcon(deviceType.name)
}

/**
 * 从任意"带设备类型字段"的行里取出图标源。
 *
 * 为什么要这个函数：各接口给的是**扁平字段**（`device_type_name` /
 * `device_type_icon`），字段名在 `Asset` / `AssetBrief` / `MapInventoryAsset`
 * 上是同一套，但每个页面各写一遍 `{ name: x.device_type_name, icon: x.device_type_icon }`
 * 迟早会有人漏掉 `icon` 那一半 —— 那样用户配好的图标就白配了（静默退回推断）。
 * 收敛到一处，模板里只写 `typeIcon(iconOf(row))`。
 */
export function iconOf(row?: { device_type_name?: string | null; device_type_icon?: string | null } | null): IconSource {
  return { name: row?.device_type_name, icon: row?.device_type_icon }
}

/**
 * 这台设备类型有没有"已确认的图标"。
 *
 * 维护界面用它决定要不要显示"未确认"标记；业务页面**不需要**关心这个 ——
 * 展示上"建议"和"已确认"长得一样，不该把技术状态泄漏成用户可见的噪音。
 */
export function hasConfirmedIcon(deviceType?: DeviceType | IconSource | null): boolean {
  return Boolean(deviceType?.icon && isIconName(deviceType.icon))
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
/**
 * 设备类别（host / display / other）的展示元数据。
 *
 * ⚠️ **这里刻意没有 icon**：类别是"能不能参与配对"的业务判定，
 * 不是给人挑图标的维度。图标挂在**设备类型**上（`DeviceType.icon`），
 * 类别只给配色与文案。（早先这里放过三个 emoji，但全站从来没人渲染 —— 已删。）
 */
export const CATEGORY_META: Record<DeviceCategory, { label: string; color: string; bg: string }> = {
  host: { label: '主机类', color: '#1f5fd0', bg: '#eaf2ff' },
  display: { label: '显示类', color: '#7a4bd0', bg: '#f2ecff' },
  other: { label: '其他', color: '#646a73', bg: '#f2f3f5' },
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
    icon: 'upload',
    tone: 'primary',
    needUser: true,
    confirm: false,
    hint: '把设备交给某人使用，状态变为「在用」，并更新使用人。',
  },
  return: {
    label: '归还',
    icon: 'download',
    tone: 'default',
    needUser: false,
    confirm: false,
    hint: '设备交回，状态变为「在库」，使用人清空。可以顺手改存放位置。',
  },
  repair: {
    label: '送修',
    icon: 'wrench',
    tone: 'warning',
    needUser: false,
    confirm: false,
    hint: '设备送修，状态变为「维修中」。',
  },
  repair_done: {
    label: '维修完成',
    icon: 'check',
    tone: 'primary',
    needUser: false,
    confirm: false,
    hint: '维修结束，状态回到「在库」，等待重新发放。',
  },
  scrap: {
    label: '报废',
    icon: 'trash',
    tone: 'danger',
    needUser: false,
    confirm: true,
    hint: '设备报废，状态变为「已报废」。报废后不能领用，但可以恢复。',
  },
  restore: {
    label: '恢复入库',
    icon: 'recycle',
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
  create: { icon: 'sparkles', color: '#1f5fd0', bg: '#eaf2ff' },
  update: { icon: 'edit', color: '#646a73', bg: '#f2f3f5' },
  status: { icon: 'refresh', color: '#a15c00', bg: '#fff4e6' },
  pair: { icon: 'link', color: '#7a4bd0', bg: '#f2ecff' },
  unpair: { icon: 'scissors', color: '#7a7f87', bg: '#f2f3f5' },
  inventory: { icon: 'clipboard', color: '#0f7b3f', bg: '#e8f7ee' },
  checkout: { icon: 'upload', color: '#0f7b3f', bg: '#e8f7ee' },
  return: { icon: 'download', color: '#0b6bcb', bg: '#eaf2ff' },
  repair: { icon: 'wrench', color: '#a15c00', bg: '#fff4e6' },
  repair_done: { icon: 'check', color: '#0f7b3f', bg: '#e8f7ee' },
  scrap: { icon: 'trash', color: '#d54941', bg: '#fdecea' },
  restore: { icon: 'recycle', color: '#0b6bcb', bg: '#eaf2ff' },
  // 点位迁移：与通用「信息变更」分开，一次搬十几台时履历上能一眼认出来
  relocate: { icon: 'shuffle', color: '#0b6bcb', bg: '#eaf2ff' },
  // 关系例外：这是"关于这台设备的一条业务决定"，不是设备被改了 —— 所以用中性灰蓝，不用红
  relation_exception: { icon: 'bandage', color: '#7a7f87', bg: '#f2f3f5' },
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
  'relocate',
  'relation_exception',
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
  pending: { label: '未盘', icon: 'clock', color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3' },
  checked: { label: '已盘', icon: 'check', color: '#0f7b3f', bg: '#e8f7ee', border: '#b7e6c9' },
  abnormal: { label: '异常', icon: 'circleAlert', color: '#d54941', bg: '#fdecea', border: '#f7c5c1' },
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
 * 审计动作 → **配色**。
 *
 * ⚠️ **图标不在这里**（2026-10-19 改）。图标标识由后端下发
 * （`AuditLogOut.action_icon_name` / `AuditActionOption.icon_name`，见
 * `backend/app/models.py::AuditAction.ICONS`）—— 一处定义覆盖全部 38 个动作。
 *
 * 以前这里另存了一份 18 项的图标映射，与后端的 38 项并存：
 * 多出来的 20 个动作（点位迁移、关联同步、平面图、轻资产…）全部掉到兜底 `•`，
 * 审计页有一整批记录根本没有图标。**同一件事存两处，必然漏。**
 *
 * 现在分工明确：
 * - **图标**是"这是什么动作"的语义 → 跟动作枚举一起定义，由接口给；
 * - **配色**是纯视觉关注点 → 留在前端，换一套配色不该动接口。
 */
const AUDIT_TONES: Record<string, { color: string; bg: string }> = {
  asset_create: { color: '#1f5fd0', bg: '#eaf2ff' },
  asset_update: { color: '#646a73', bg: '#f2f3f5' },
  asset_delete: { color: '#d54941', bg: '#fdecea' },
  asset_operate: { color: '#a15c00', bg: '#fff4e6' },
  asset_pair: { color: '#7a4bd0', bg: '#f2ecff' },
  asset_unpair: { color: '#7a7f87', bg: '#f2f3f5' },
  asset_rebind: { color: '#7a4bd0', bg: '#f2ecff' },
  asset_import: { color: '#0f7b3f', bg: '#e8f7ee' },
  asset_export: { color: '#0b6bcb', bg: '#eaf2ff' },
  inventory_create: { color: '#1f5fd0', bg: '#eaf2ff' },
  inventory_mark: { color: '#0f7b3f', bg: '#e8f7ee' },
  inventory_close: { color: '#646a73', bg: '#f2f3f5' },
  inventory_reopen: { color: '#a15c00', bg: '#fff4e6' },
  inventory_delete: { color: '#d54941', bg: '#fdecea' },
  type_create: { color: '#1f5fd0', bg: '#eaf2ff' },
  type_update: { color: '#646a73', bg: '#f2f3f5' },
  type_delete: { color: '#d54941', bg: '#fdecea' },
  audit_export: { color: '#0b6bcb', bg: '#eaf2ff' },
}

/**
 * 审计动作的配色。**图标请用接口给的 `action_icon_name`**，不要在这里找。
 */
export function auditTone(action: string): { color: string; bg: string } {
  return AUDIT_TONES[action] ?? { color: '#646a73', bg: '#f2f3f5' }
}

// --------------------------------------------------------------------------- //
// 批量导入
// --------------------------------------------------------------------------- //
export const IMPORT_ACTION_META: Record<
  string,
  { label: string; color: string; bg: string; border: string; icon: string }
> = {
  create: { label: '新增', icon: 'sparkles', color: '#0f7b3f', bg: '#e8f7ee', border: '#b7e6c9' },
  update: { label: '更新', icon: 'edit', color: '#0b6bcb', bg: '#eaf2ff', border: '#bed6ff' },
  unchanged: { label: '无变化', icon: 'minus', color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3' },
  error: { label: '错误', icon: 'circleAlert', color: '#d54941', bg: '#fdecea', border: '#f7c5c1' },
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
  // ⚠️ 后端给的是完整 datetime（2026-10-16T09:41:26.505245），不带时间会原样显示出来 ——
  // 以前这里只把连字符换成斜杠，于是"建档时间"列显示成一串带微秒的时间戳，
  // 靠列宽把它遮住而已。这里显式截到日期。
  return value.replace('T', ' ').slice(0, 10).replace(/-/g, '/')
}

/** 2026-01-02T09:37:28 → 2026/01/02 09:37 */
export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const raw = value.replace('T', ' ')
  return raw.slice(0, 16).replace(/-/g, '/')
}

/**
 * **业务时间**：只精确到天的记录只显示日期，真有分秒的才显示分秒。
 *
 * ## 为什么需要它（2026-10-21）
 *
 * 轻资产的登记入口只让用户选到「天」，后端把 `occurred_at` 的时分记成 `00:00`。
 * 若一律用 `formatDateTime`，界面上会出现 `2026/10/20 00:00` —— 一个不存在的
 * 半夜时刻，反而比只写日期更像"精确时间"。
 *
 * 判据就是"时分秒是否全 0"：`00:00` = 这条记录**没有业务时刻**，只到天。
 * ⚠️ 它不区分"真的是 0 点发生的"和"没录时分" —— 现实里没人凌晨 0 点整发放配件，
 * 而误判的代价只是少显示两个 0，不会改数据、也不会丢日期。
 * ⚠️ **不要反向使用**：不能用它判断"这条记录是不是历史补录"，
 * 补录在发生当天的记录看起来和正常登记一样。
 */
export function formatBusinessTime(value?: string | null): string {
  if (!value) return '—'
  const raw = value.replace('T', ' ')
  const time = raw.slice(11, 19)
  if (!time || time === '00:00:00' || time === '00:00') {
    return raw.slice(0, 10).replace(/-/g, '/')
  }
  return raw.slice(0, 16).replace(/-/g, '/')
}

/** 只比"哪一天"：2026-10-20T23:59:59 与 2026-10-20T00:00:00 视为同一天 */
export function sameCalendarDay(a?: string | null, b?: string | null): boolean {
  if (!a || !b) return false
  return a.replace('T', ' ').slice(0, 10) === b.replace('T', ' ').slice(0, 10)
}

/** 只要月日时分：09/23 09:37（时间线里省地方） */
export function formatShortDateTime(value?: string | null): string {
  if (!value) return '—'
  const raw = value.replace('T', ' ')
  return raw.slice(5, 16).replace(/-/g, '/')
}

/**
 * 本机"墙上时钟"的日期：2026-10-20。
 *
 * ⚠️ **别用 `new Date().toISOString().slice(0, 10)`**：`toISOString()` 输出的是 UTC，
 * 东八区晚上 8 点之后它会跳到"明天"（甚至把今天登记的事写进昨天/明天的日期里）。
 * 本系统库里存的全是本地 naive 时间（见后端 `models.py` 顶部说明），前端也要按本地口径拼。
 */
export function localDateString(now: Date = new Date()): string {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * 本机"墙上时钟"的整点时间戳：2026-10-20T14:18:00（**没有 Z，不是 UTC**）。
 *
 * 后端把这些时间当本地 naive datetime 直接入库；带上 `Z` 就会被当成 UTC，
 * 页面上比墙上时钟慢 8 小时，和审计日志对不上（v2.9.8 事故）。
 */
export function localDateTimeString(now: Date = new Date()): string {
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const ss = String(now.getSeconds()).padStart(2, '0')
  return `${localDateString(now)}T${hh}:${mm}:${ss}`
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

/**
 * 使用人里代表「无归属」的占位符。
 *
 * 项目没有人员字典表，使用人是自由文本，于是「这台不归谁」被写成了斜杠 ——
 * 现在库里 15 台写的是 `/`（13 台主机，全在办公室大厅 / 资料室这类公共区域）。
 * 半角、全角都认：中文输入法下打出 `／` 太正常了。
 */
export const NO_USER_MARKS = ['/', '／']

/**
 * 这个使用人算不算「没填」。
 *
 * **`/` 不是一个人名，是「没填」。** 凡是把使用人当**人名**展示的地方
 * （配对页的主机分组、选择弹窗里的人员标签）都要先过这一遍 ——
 * 否则界面上会冒出一个蓝色的「/」标签，看着像有这么个人。
 *
 * 注意别拿它当「空值判断」用：`undefined` / `''` 返回 false。
 * 要的是「这个字段有没有指向某个人」，两种情况合起来看才行。
 */
export function isNoUser(userName?: string | null): boolean {
  const name = userName?.trim()
  return !!name && NO_USER_MARKS.includes(name)
}

/**
 * 这台设备到底归不归某个人。**空值和斜杠都算「没归属」。**
 *
 * 跟 `isNoUser()` 的差别只在空值上：`isNoUser('')` 是 false（"没写斜杠"），
 * `hasOwner('')` 也是 false（"没人用"）。判断"要不要显示成人名"用这个，
 * 判断"写没写那个占位符"用上面那个。
 */
export function hasOwner(userName?: string | null): boolean {
  return Boolean(userName?.trim()) && !isNoUser(userName)
}

/** 人员标签上的文案：没归属的一律显成「未填使用人」，不显示成斜杠 */
export function userLabel(userName?: string | null): string {
  return hasOwner(userName) ? String(userName).trim() : '未填使用人'
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
