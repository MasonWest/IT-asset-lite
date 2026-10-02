import type { FloorLayout } from '@/utils/floorMap'

export type AssetStatus = 'in_use' | 'idle' | 'repair' | 'scrapped'
/** 设备类别 —— 决定能不能参与主机/显示器配对 */
export type DeviceCategory = 'host' | 'display' | 'other'

/** 状态流转动作 */
export type AssetOperation =
  | 'checkout'
  | 'return'
  | 'repair'
  | 'repair_done'
  | 'scrap'
  | 'restore'

/** 盘点明细结果 */
export type InventoryResult = 'pending' | 'checked' | 'abnormal'
export type InventoryTaskStatus = 'open' | 'closed'

export interface DeviceType {
  id: number
  name: string
  sort_order: number
  category: DeviceCategory
  category_label: string
  asset_count: number
}

export interface DeviceTypeBrief {
  id: number
  name: string
  category: DeviceCategory
}

/** 配对关系里用的精简设备结构 */
export interface AssetBrief {
  id: number
  asset_code: string
  device_type_id: number
  device_type_name: string
  brand: string | null
  model: string | null
  serial_number: string | null
  status: AssetStatus
  status_label: string
  user_name: string | null
  location: string | null
}

export interface Asset {
  id: number
  asset_code: string
  finance_code: string | null
  device_type_id: number
  device_type_name: string
  device_category: DeviceCategory
  device_type: DeviceTypeBrief | null
  brand: string | null
  model: string | null
  serial_number: string | null
  status: AssetStatus
  status_label: string
  user_name: string | null
  location: string | null
  /** 挂在哪个点位上。绝大多数资产为 null（不在点位上） */
  workstation_id: number | null
  /** 点位编码，便于列表直接显示，不用再查一次 */
  workstation_code: string | null
  purchase_date: string | null
  warranty_until: string | null
  warranty_days_left: number | null
  notes: string | null
  created_at: string
  updated_at: string
  /** 作为主机，当前挂了几台显示器 */
  monitor_count: number
  /** 作为显示器，当前挂在哪台主机上 */
  host_id: number | null
  host_code: string | null
  /** 详情接口才会填：主机挂着的显示器列表 */
  monitors: AssetBrief[]
  /** 详情接口才会填：显示器挂在哪台主机上 */
  host: AssetBrief | null
  /** 当前状态下能做的操作 */
  actions: AssetOperation[]
}

export interface AssetPage {
  items: Asset[]
  total: number
  page: number
  page_size: number
}

/** 套装聚合行需要「轻提示」的几种情况 —— 只提示，不阻断、不自动改数据 */
export interface AssetGroupFlags {
  /** 一台显示器挂了多台主机（当前数据模型下不可能，防御性字段） */
  multi_host: boolean
  /** 一台主机挂了 ≥2 台显示器 */
  multi_monitor: boolean
  /** 组内成员状态不一致（例如主机「在用」而显示器「在库」） */
  status_mismatch: boolean
}

/**
 * 套装聚合视图的一行。
 * - `kind === 'bundle'`：一台主机 + N 台显示器
 * - `kind === 'single'`：没参与配对的散设备
 */
export interface AssetGroup {
  group_key: string
  kind: 'bundle' | 'single'
  /** 这一行的门面：套装是主机，散设备是它自己 */
  primary: Asset
  extra_hosts: Asset[]
  monitors: Asset[]
  monitor_count: number
  /** 组内全部资产 id —— 打印标签时展开成明细用（标签永远按设备明细打印） */
  asset_ids: number[]
  asset_count: number
  sort_id: number
  flags: AssetGroupFlags
}

export interface AssetGroupPage {
  items: AssetGroup[]
  /** 聚合后的行数（分页按它算） */
  total: number
  page: number
  page_size: number
  /** 命中筛选的明细台数，等于同一条件下 /api/assets 的 total */
  matched_total: number
  /** 聚合后展开的总台数，因「整组出现」可能大于 matched_total */
  expanded_total: number
}

export interface StatusOption {
  value: AssetStatus
  label: string
}

export interface FilterOptions {
  device_types: DeviceType[]
  statuses: StatusOption[]
  users: string[]
  locations: string[]
  total: number
  status_counts: Record<string, number>
  device_type_counts: Record<string, number>
  /** 没有落到任何点位点位的资产数（台账页「无点位资产」统计卡） */
  no_workstation_count: number
  pair_stats: Record<string, number>
}

export interface AssetPayload {
  asset_code?: string | null
  finance_code?: string | null
  device_type_id: number
  brand?: string | null
  model?: string | null
  serial_number?: string | null
  status: AssetStatus
  user_name?: string | null
  location?: string | null
  /**
   * 所在点位。
   *
   * **`null` 与"不传"在语义上不同**：不传 = 不动；传 null = 从点位上摘下来。
   * 后端靠 `model_dump(exclude_unset=True)` 区分这两者，所以表单提交时
   * 这个键**必须显式带上**，不能因为是 null 就省略。
   */
  workstation_id?: number | null
  purchase_date?: string | null
  warranty_until?: string | null
  notes?: string | null
  /** 经办人，写进变更历史 */
  operator?: string | null
}

export interface SystemInfo {
  app_name: string
  version: string
  port: number
  lan_ip: string
  base_url: string
  database_file: string
  /** 备份目录（backup.bat 往这里写） */
  backup_dir: string
  asset_count: number
}

export interface AssetQuery {
  keyword?: string
  device_type_id?: string | number
  status?: string
  user_name?: string
  location?: string
  paired?: 'paired' | 'unpaired'
  /**
   * 点位轴：`none` = 只看没落到任何点位点位的资产。
   *
   * 与 `status` **正交**（一台设备可以既「在用」又「没点位」），所以不是枚举选择，
   * 而是"要不要加这个条件" —— 传别的值等于不筛。
   */
  workstation?: 'none'
  page?: number
  page_size?: number
}

// --------------------------------------------------------------------------- //
// 变更历史
// --------------------------------------------------------------------------- //
export type AssetEventType =
  | 'create'
  | 'update'
  | 'status'
  | 'pair'
  | 'unpair'
  | 'inventory'
  | AssetOperation

export interface AssetEvent {
  id: number
  asset_id: number
  event_type: AssetEventType
  event_label: string
  from_status: AssetStatus | null
  from_status_label: string | null
  to_status: AssetStatus | null
  to_status_label: string | null
  operator: string | null
  note: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

export interface TimelinePage {
  asset_id: number
  asset_code: string
  total: number
  items: AssetEvent[]
}

// --------------------------------------------------------------------------- //
// 状态流转
// --------------------------------------------------------------------------- //
export interface AssetOperationPayload {
  action: AssetOperation
  operator?: string | null
  user_name?: string | null
  location?: string | null
  note?: string | null
}

export interface AssetOperationResult {
  asset: Asset
  event: AssetEvent
}

// --------------------------------------------------------------------------- //
// 配对
// --------------------------------------------------------------------------- //
export interface Relation {
  id: number
  active: boolean
  host_id: number
  host: AssetBrief
  monitor_id: number
  monitor: AssetBrief
  note: string | null
  created_by: string | null
  created_at: string
  released_by: string | null
  released_at: string | null
  release_note: string | null
}

export interface HostPairing {
  host: AssetBrief
  monitors: AssetBrief[]
  monitor_count: number
}

export interface PairingBoard {
  hosts: HostPairing[]
  unpaired_monitors: AssetBrief[]
  stats: Record<string, number>
  hints: string[]
}

export interface RelationHistoryPage {
  total: number
  items: Relation[]
}

// --------------------------------------------------------------------------- //
// 盘点
// --------------------------------------------------------------------------- //
export interface InventoryScope {
  device_type_id?: string | null
  status?: string | null
  user_name?: string | null
  location?: string | null
  keyword?: string | null
  asset_ids?: number[] | null
}

export interface InventoryTask {
  id: number
  name: string
  status: InventoryTaskStatus
  status_label: string
  note: string | null
  scope: InventoryScope | null
  scope_text: string
  created_by: string | null
  created_at: string
  closed_by: string | null
  closed_at: string | null
  total: number
  checked: number
  pending: number
  abnormal: number
  progress: number
}

export interface InventoryItem {
  id: number
  task_id: number
  asset_id: number
  asset: Asset
  result: InventoryResult
  result_label: string
  snapshot_user: string | null
  snapshot_location: string | null
  snapshot_status: AssetStatus | null
  operator: string | null
  note: string | null
  checked_at: string | null
  /** 标记之后账面值又被改过 */
  changed_since: boolean
}

export interface InventoryTaskDetail extends InventoryTask {
  items: InventoryItem[]
  diff: InventoryItem[]
}

export interface InventoryContext {
  task_id: number
  task_name: string
  task_status: InventoryTaskStatus
  item_id: number
  result: InventoryResult
  result_label: string
  operator: string | null
  note: string | null
  checked_at: string | null
}

// --------------------------------------------------------------------------- //
// 批量导入 / 导出
// --------------------------------------------------------------------------- //
export type ImportMode = 'create_only' | 'upsert'
export type ImportRowAction = 'create' | 'update' | 'unchanged' | 'error'

export interface ImportRow {
  row: number
  asset_code: string
  action: ImportRowAction
  action_label: string
  errors: string[]
  warnings: string[]
  /** 「字段 旧 → 新」的人话列表 */
  changes: string[]
  /** 库里已有那条的设备描述 */
  existing_label: string
}

export interface ImportPreview {
  filename: string
  mode: ImportMode
  mode_label: string
  total: number
  create: number
  update: number
  unchanged: number
  error: number
  will_write: number
  can_commit: boolean
  auto_types: string[]
  unknown_headers: string[]
  missing_headers: string[]
  missing_required: string[]
  empty: boolean
  rows: ImportRow[]
  rows_truncated: boolean
}

export interface ImportResult {
  filename: string
  mode: ImportMode
  mode_label: string
  created: number
  updated: number
  unchanged: number
  failed: number
  created_types: string[]
  failed_rows: ImportRow[]
  asset_codes: string[]
}

export interface ExportOptions {
  fmt: 'xlsx' | 'csv'
  withPairing?: boolean
  /** 导出范围：当前筛选 / 全部 */
  scope?: 'filtered' | 'all'
}

// --------------------------------------------------------------------------- //
// 操作审计
// --------------------------------------------------------------------------- //
export type AuditLogStatus = 'success' | 'failed'
export type AuditTargetType = 'asset' | 'inventory' | 'device_type' | 'file' | 'system'

export interface AuditLog {
  id: number
  created_at: string
  operator: string | null
  ip: string | null
  action: string
  action_label: string
  action_icon: string
  target_type: AuditTargetType
  target_type_label: string
  target_id: number | null
  target_label: string | null
  summary: string | null
  status: AuditLogStatus
  status_label: string
  ok: boolean
  detail: Record<string, unknown> | null
}

export interface AuditPage {
  items: AuditLog[]
  total: number
  page: number
  page_size: number
}

export interface AuditActionOption {
  value: string
  label: string
  icon: string
}

export interface AuditGroup {
  name: string
  options: AuditActionOption[]
}

export interface AuditMeta {
  groups: AuditGroup[]
  operators: string[]
  ips: string[]
  total: number
  today: number
  failed: number
}

export interface AuditQuery {
  start?: string
  end?: string
  action?: string
  operator?: string
  ip?: string
  status?: AuditLogStatus
  keyword?: string
  page?: number
  page_size?: number
}

// --------------------------------------------------------------------------- //
// 点位（资产空间地图）
// --------------------------------------------------------------------------- //
/** 点位朝向。桌子朝哪边摆 —— 是家具属性，与它在画布第几行无关 */
export type Facing = 'up' | 'down'

/**
 * 点位级盘点状态。前三种来自后端 `inventory_items.result` 的聚合，
 * 后两种是**视图层专有**的中性态（后端也算好了，只是不落库）：
 *   - `empty`        这位置一台资产都没有 —— 不是"盘过了"，也不是"漏盘"
 *   - `not_in_scope` 有资产，但不属于当前选中的这次盘点（本次不盘）
 */
export type MapState = 'checked' | 'abnormal' | 'pending' | 'empty' | 'not_in_scope'

export interface Workstation {
  id: number
  code: string
  x: number
  y: number
  facing: Facing
  user_name: string | null
  room: string | null
  active: boolean
  created_at: string
  updated_at: string
  /** 点位上挂了几台资产 —— 后端派生，不落库 */
  asset_count: number
}

export interface WorkstationMapCanvas {
  width: number
  height: number
  /** 与前端视觉网格必须一致：视觉 20px 而吸附 4px 的话，用户肉眼看不出"对齐了" */
  grid: number
}

/** 单张平面图。`layout` 是后端解析后的对象，不是字符串。 */
export interface FloorMap {
  id: number
  name: string
  canvas: WorkstationMapCanvas
  layout: FloorLayout
  is_default: boolean
  created_at: string | null
  updated_at: string | null
}

export interface FloorMapList {
  total: number
  /** ⚠️ 当前恒为一张。前端只取 `items[0]`，禁止做选择器/多图 UI。 */
  items: FloorMap[]
}

export interface FloorMapSavePayload {
  name?: string | null
  layout?: FloorLayout | null
  /** 乐观锁：打开页面时看到的 updated_at。不带则跳过检查。 */
  expected_updated_at?: string | null
  operator?: string | null
}

/** 批量生成点位的参数。与后端 `WorkstationBulkGenerate` 一一对应。 */
export interface WorkstationBulkPayload {
  /** 生成总数（不是列数） */
  count: number
  origin_x: number
  origin_y: number
  step_x?: number
  step_y?: number
  /** 每行几个 */
  cols?: number
  /** `down` / `up` / `alternate`（一列朝下一列朝上，面对面） */
  facing?: string
  code_prefix?: string | null
  /** 起始编码建议（如 W63）。为空则后端按现有最大值续号。 */
  start_code?: string | null
  room?: string | null
  operator?: string | null
}

export interface WorkstationBulkResult {
  created: number
  codes: string[]
  first_code: string | null
  last_code: string | null
  items: Workstation[]
}

export interface WorkstationMap {
  canvas: WorkstationMapCanvas
  /**
   * 平面图的建筑图元（地台/走道/房间/墙体/分隔条/前厅/入口）。
   * 由后端从默认平面图带出 —— 前端不再写死。没有图时是空对象。
   * 解析一律走 `utils/floorMap.ts::parseLayout`，别在这里直接摸 shapes。
   */
  layout: FloorLayout
  floor_map_id: number | null
  total: number
  assigned: number
  empty: number
  items: Workstation[]
}

export interface WorkstationCreatePayload {
  code?: string | null
  x: number
  y: number
  facing?: Facing | null
  user_name?: string | null
  room?: string | null
  operator?: string | null
}

export interface WorkstationUpdatePayload {
  code?: string
  x?: number
  y?: number
  facing?: Facing
  user_name?: string | null
  room?: string | null
  operator?: string | null
}

export interface WorkstationLayoutItem {
  id: number
  x: number
  y: number
}

export interface WorkstationLayoutResult {
  updated: number
  unchanged: number
  changes: Array<{ id: number; code: string; from: [number, number]; to: [number, number] }>
}

// --------------------------------------------------------------------------- //
// 地图盘点视图
// --------------------------------------------------------------------------- //
export interface MapInventoryAsset {
  asset_id: number
  asset_code: string
  device_type_name: string
  brand: string | null
  model: string | null
  status: AssetStatus
  status_label: string
  result: InventoryResult
  result_label: string
  operator: string | null
  checked_at: string | null
  /** 盘点之后账面又被改过 —— 后端拿「盘点时刻快照」比出来的 */
  changed_since: boolean
  snapshot_user: string | null
  snapshot_location: string | null
}

export interface MapInventoryWorkstation {
  workstation_id: number
  code: string
  x: number
  y: number
  facing: Facing
  user_name: string | null
  room: string | null
  state: MapState
  state_label: string
  /** 属于本次任务的资产明细（empty / not_in_scope 时为空） */
  assets: MapInventoryAsset[]
  /** 该点位下资产总数（含不属于本次任务的）—— 用来区分 empty 与 not_in_scope */
  asset_count: number
}

export interface MapInventoryView {
  task: InventoryTask
  canvas: WorkstationMapCanvas
  workstations: MapInventoryWorkstation[]
  counts: Record<string, number>
  /** 没有被任何点位认领的资产数（没位置 = 没人管） */
  unassigned: number
  /** 有点位、但不属于本次任务的资产数（这些设备本次不盘） */
  out_of_scope: number
}
