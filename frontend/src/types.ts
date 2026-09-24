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
