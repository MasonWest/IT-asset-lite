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
  /**
   * 已确认的图标标识（`utils/icons.ts::ICON_PATHS` 的 key），如 `tower`。
   *
   * **为空 = 用户还没确认过图标**，界面据此按名称给一个**建议**图标
   * （只显示、不写库）。判断"该不该显示未确认标记"也靠它。
   */
  icon: string | null
  asset_count: number
}

export interface DeviceTypeBrief {
  id: number
  name: string
  category: DeviceCategory
  /** 同 `DeviceType.icon`：可能为空（未确认） */
  icon?: string | null
}

/** 配对关系里用的精简设备结构 */
export interface AssetBrief {
  id: number
  asset_code: string
  device_type_id: number
  device_type_name: string
  /**
   * 已确认的图标标识（`utils/icons.ts::ICON_PATHS` 的 key）。
   * 为空 = 用户还没确认过，界面按名称给一个**建议**图标（只显示、不写库）。
   */
  device_type_icon?: string | null
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
  /**
   * 已确认的图标标识（`utils/icons.ts::ICON_PATHS` 的 key）。
   * 为空 = 用户还没确认过，界面按名称给一个**建议**图标（只显示、不写库）。
   */
  device_type_icon?: string | null
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
  /** 点位迁移（整点位业务迁移 / 多台资产换点位）—— 与通用 update 分开，履历上一眼可辨 */
  | 'relocate'
  /** 关系差异被确认为例外 / 例外被撤销（设备级才有） */
  | 'relation_exception'
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
  /**
   * 图标标识（`utils/icons.ts::ICON_PATHS` 的 key），**不是 emoji**。
   * 由后端按动作枚举统一给出，覆盖全部 38 个审计动作。
   */
  action_icon_name: string
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
  /**
   * 点位上挂了几台资产 —— 后端派生，不落库。
   *
   * ⚠️ v2.9.6 删掉了 `light_direct` / `light_via_assets`：它们是"地图角标"的数据源，
   * 角标已退役。轻资产要看的是**具体哪一条**（聚合后身份就丢了），
   * 入口是点位侧栏的四组，不是点位上的一个数字。
   */
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
  /** **有人**的点位数。斜杠 `/` 不算有人（与后端 `services/ownership.py` 同口径）。 */
  assigned: number
  /**
   * **一台设备都没挂**的点位数 —— 资产轴。
   *
   * ⚠️ 2026-10-08 由后端的 `empty` 改名而来：那个名字**看起来**像"空闲点位"，
   * 而本文件里另有一个 `stats.free` 才是"没人用"（`total - assigned`）——
   * **两个都叫"空闲"，指的却是两件事**，同一页上对不上且不报错。
   * 现在名字自己说清是什么，不用再靠读文档区分。
   */
  without_asset: number
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
  /**
   * 已确认的图标标识（`utils/icons.ts::ICON_PATHS` 的 key）。
   * 为空 = 用户还没确认过，界面按名称给一个**建议**图标（只显示、不写库）。
   */
  device_type_icon?: string | null
  brand: string | null
  model: string | null
  status: AssetStatus
  status_label: string
  result: InventoryResult
  result_label: string
  operator: string | null
  /** 盘点备注。与列表页/导出的 note 是同一列，地图侧栏「标记异常」采集后回显同一份 */
  note: string | null
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

// --------------------------------------------------------------------------- //
// 关系核对（点位使用人 ↔ 设备使用人）
// --------------------------------------------------------------------------- //
/**
 * 核对结论的四态。
 *
 * ⚠️ `mismatch` 是**待核对**，不是错误：它只说明"两边写的人不一样"，
 * 至于哪边对、要不要改，由人判断。系统永远不会自己改一边。
 */
export type RelationCheckStatus = 'consistent' | 'mismatch' | 'unregistered' | 'undetermined'
/** 展示权重：`review` 只是"值得看一眼"，没有任何 error 语义 */
export type RelationSeverity = 'ok' | 'info' | 'review'
/** 问题挂靠粒度：单台设备可以单独确认例外 */
export type RelationScope = 'station' | 'asset'

export interface RelationSubject {
  kind: 'workstation' | 'asset'
  id: number
  code: string
  user_name: string | null
  label: string
}

/**
 * 一条**例外确认** —— 业务决定，不是问题状态。
 *
 * `valid=false` 表示确认之后关系又变了，这条确认**已经失效**
 * （核对结果会重新冒出来）。判断依据是后端存下的关系指纹。
 */
export interface RelationException {
  id: number
  rule: string
  workstation_id: number
  /** 0 表示点位级例外 */
  asset_id: number
  asset_scope: boolean
  reason: string
  valid: boolean
  created_by: string | null
  created_at: string
  checked_at: string | null
}

export interface RelationIssue {
  rule: string
  rule_label: string
  status: RelationCheckStatus
  status_label: string
  severity: RelationSeverity
  severity_label: string
  scope: RelationScope
  asset_id: number | null
  title: string
  detail: string
  suggestion: string
  /** 关系指纹。确认例外时原样回传，后端据此判断"你确认的是不是当前这条"。 */
  fingerprint: string
  subjects: RelationSubject[]
  exception: RelationException | null
  /** 因有效例外被压住 —— 不必再提示，但用户看得到是被谁确认掉的 */
  suppressed: boolean
}

export interface RelationAssetCheck {
  asset_id: number
  asset_code: string
  asset_user: string | null
  state: RelationCheckStatus
  state_label: string
}

/** 一个点位的关系核对结果。**每次现算，不落库。** */
export interface StationRelationCheck {
  workstation_id: number
  code: string
  user_name: string | null
  /** 有没有可核对的对象（没挂设备时为 false） */
  applicable: boolean
  verdict: RelationCheckStatus
  verdict_label: string
  /** 需要人看一眼的条数（mismatch，且未被有效例外压住） */
  review_count: number
  issues: RelationIssue[]
  asset_checks: RelationAssetCheck[]
  checked_at: string
}

export interface RelationExceptionPayload {
  rule: string
  asset_id?: number | null
  fingerprint: string
  reason: string
  operator?: string | null
}

// --------------------------------------------------------------------------- //
// 点位迁移
// --------------------------------------------------------------------------- //
/** `assets` = 资产换点位；`station` = 整个点位业务迁移。**都不碰坐标。** */
export type RelocationMode = 'assets' | 'station'

export interface AssetMove {
  asset_id: number
  asset_code: string
  asset_user: string | null
  from_workstation_id: number | null
  from_workstation_code: string | null
  to_workstation_id: number | null
  to_workstation_code: string | null
  /** 迁移**前**这台设备与它当前点位的关系状态 */
  relation_state: string
}

export interface RelocationStationRef {
  id: number
  code: string
  user_name: string | null
  active: boolean
}

/** 迁移预览。**绝不写数据。** */
export interface RelocationPreview {
  mode: RelocationMode
  mode_label: string
  ok: boolean
  /** 阻止执行的问题 */
  conflicts: string[]
  /** 需要用户显式点头的差异 / 影响 */
  confirmations: string[]
  /** 只说明情况、不要求点头的提示（如"源点位没有归属可搬"） */
  notes: string[]
  moves: AssetMove[]
  noop_count: number
  station_changes: string[]
  /**
   * mode=station：归属**到底会不会跟着走**。
   * 源点位没有登记归属（空值 / `/` / `／`）时恒为 false —— 没有东西可搬。
   */
  user_move: boolean
  /** 迁移前后的归属原值（照实给，含 null 与 `/`）。展示走 `userLabel()`。 */
  source_user_before: string | null
  source_user_after: string | null
  target_user_before: string | null
  target_user_after: string | null
  source: RelocationStationRef | null
  target: RelocationStationRef | null
  /** 预览令牌。执行时带回，不一致说明数据被改过 → 409。 */
  preview_token: string
  affected_asset_count: number
}

export interface RelocationPreviewPayload {
  mode: RelocationMode
  asset_ids?: number[]
  source_workstation_id?: number | null
  target_workstation_id?: number | null
  move_assets?: boolean
  move_user?: boolean
  retire_source?: boolean
}

export interface RelocationApplyPayload extends RelocationPreviewPayload {
  preview_token: string
  acknowledge_warnings?: boolean
  operator?: string | null
}

export interface RelocationApplyResult {
  ok: boolean
  mode: RelocationMode
  moved: number
  moved_codes: string[]
  station_changes: string[]
  /** 说明性提示，与预览同源 */
  notes: string[]
  /** 执行后重新核对的结论（源 + 目标点位） */
  relation_checks: StationRelationCheck[]
  summary: string
}

// --------------------------------------------------------------------------- //
// 问题中心
//
// ⚠️ `key` 与 `fingerprint` 是两件事，**别混用**：
//   · `key`        只随**对象身份**变 —— 去重 / 定位 / 跨刷新一致靠它；
//   · `fingerprint` 随**关系状态**变 —— 只用来判"例外还算不算数"。
// 拿 fingerprint 当标识，用户改一下使用人同一条问题就换了身份。
// --------------------------------------------------------------------------- //
export type InsightSeverity = 'ok' | 'info' | 'review'
export type InsightSource = 'relation_check' | 'data_integrity'
export type InsightRule =
  | 'station_vs_asset_user'
  | 'asset_user_unregistered'
  | 'asset_unbound'
  | 'asset_on_deleted_station'
  | 'relation_exception_stale'

export interface InsightSubject {
  kind: 'workstation' | 'asset'
  id: number
  code: string
  user_name: string | null
  label: string
}

export interface InsightRuleGroup {
  rule: InsightRule
  label: string
  severity: InsightSeverity
  severity_label: string
  source: InsightSource
  source_label: string
  /** 一句"为什么值得关注" —— 后端下发，前端**一个字都不拼** */
  reason: string
  /** 该规则的**全部**问题数（含已被有效例外盖住的） */
  count: number
  /** 其中已被有效例外盖住的 */
  handled_count: number
  /** 还需要看一眼的条数 */
  pending: number
  /** 只有当该规则下待办问题落点一致时才有；否则为空，界面只提供"展开逐条" */
  action_label: string
  action_url: string | null
}

export interface InsightProblem {
  /** 稳定标识（只随对象身份变） */
  key: string
  rule: InsightRule
  rule_label: string
  severity: InsightSeverity
  severity_label: string
  source: InsightSource
  source_label: string
  title: string
  detail: string
  reason: string
  subjects: InsightSubject[]
  action_label: string
  /** 后端保证是白名单里的完整路径，前端直接跳 */
  action_url: string
  fingerprint: string
  /** 已被有效例外盖住 → 不计入待办，但仍可查看 / 可撤销 */
  handled: boolean
  exception_id: number | null
  exception_reason: string | null
}

export interface InsightChecked {
  rules: string[]
  stations: number
  assets: number
}

export interface InsightsPage {
  generated_at: string
  total: number
  counts: Record<string, number>
  checked: InsightChecked
  by_rule: InsightRuleGroup[]
  items: InsightProblem[]
  page: number
  page_size: number
  pages: number
}

export interface InsightQuery {
  rule?: InsightRule
  severity?: InsightSeverity
  page?: number
  page_size?: number
}

/* -------------------------------------------------------------------------- */
/* 关联操作建议（v2.7.0）                                                        */
/* -------------------------------------------------------------------------- */

/** 单台资产的关联建议。后端逐台算好，前端**一个字都不拼** */
export interface LinkSuggestion {
  asset_id: number
  asset_code: string
  device_type_name: string
  /** 当前使用人。没归属（空 / `/`）时为 null —— 界面显示「未登记」 */
  current_user: string | null
  suggestion: 'fill' | 'conflict' | 'keep'
  label: string
  hint: string
  /** 默认是否勾选（只有 fill 默认勾） */
  checked: boolean
}

export interface LinkSuggestionsResult {
  station_id: number
  station_code: string
  new_user: string
  suggestions: LinkSuggestion[]
  /** 已是新使用人、无需提示的台数 */
  matched_count: number
  /** 该点位资产总数（对账用） */
  total_assets: number
}

export interface LinkageApplyResult {
  updated: number
  asset_codes: string[]
  skipped: { asset_id: number; asset_code?: string; current_user?: string | null; reason: string }[]
  matched_count: number
}

/** 清人方向（第二期）：点位使用人 有人 → 无人 */
export interface ClearSuggestion {
  asset_id: number
  asset_code: string
  device_type_name: string
  /** 当前使用人（= 被清的那位） */
  current_user: string | null
  suggestion: 'clear'
  label: string
  hint: string
  /** 恒为 false —— 清人默认一个都不勾（勾了才清，不勾 = 留给下一任） */
  checked: boolean
}

export interface ClearSuggestionsResult {
  station_id: number
  station_code: string
  /** 被清空的点位使用人 */
  old_user: string
  suggestions: ClearSuggestion[]
  /** 使用人不是他 / 本来没登记的设备数（导语里说"另有 N 台不受影响"） */
  other_count: number
  total_assets: number
}

export interface UnlinkApplyResult {
  cleared: number
  asset_codes: string[]
  skipped: { asset_id: number; asset_code?: string; current_user?: string | null; reason: string }[]
}

// --------------------------------------------------------------------------- //
// 轻资产（不纳入正式台账的 IT 配件）
// --------------------------------------------------------------------------- //
/**
 * ⚠️ 这里的每个数字都来自后端（`light_holdings` 当前分配记录的聚合），前端不做任何加减。
 *
 * 这一版**取消了**追踪粒度（`tracking` 数量 / 单件双模式）与整套「单件档案」，
 * 统一按「品类 + 数量」管理；当前状态由 `light_holdings` 承载，历史由 `light_movements`。
 */
/** 手工登记允许的来源。没有 'import' —— 历史导入只能走导入接口 */
export type LightOrigin = 'manual' | 'opening' | 'import'

export type LightHolderKind = 'stock' | 'user' | 'asset' | 'workstation' | 'none'

/** 物料移动的操作类型。`replace`（更换）不在其中 —— 它一次写两条流水，走专用入口 */
export type LightOp =
  | 'stock_in'
  | 'allocate'
  | 'return'
  | 'attach'
  | 'detach'
  | 'transfer'
  | 'scrap'
  | 'adjust'

/** 一处引用 / 一个持有位置（流水的 from / to，或一条当前分配记录指向的位置） */
export interface LightHolder {
  kind: LightHolderKind | null
  kind_label: string
  ref_id: number | null
  ref_code: string | null
  ref_label: string | null
  user: string | null
  /** 现成的展示文案（"库存" / "张三" / "B022-03-00001"） */
  display: string
  /** 引用的宿主是否还在（资产/点位被删后为 false） */
  alive: boolean
  /** 一行简短说明（"随 B022-03-00001" / "在某人手上，未记录固定位置"…） */
  hint: string
  /** 以下只在 kind='asset' 时填：宿主**当前**的使用人 / 点位 / 状态（现读，不落库） */
  host_user: string | null
  host_workstation_code: string | null
  host_status: string | null
  host_status_label: string | null
  /** 仅 kind='stock'：存放位置描述 + 它恰好是某个活点位的编码时的 id（可点击） */
  stock_location: string | null
  stock_location_station_id: number | null
  /**
   * 仅 kind='user'：**推断**的候选点位（按"该使用人名下的点位"算）。
   *
   * 0 个 = 推不出来；1 个 = 唯一候选；N 个 = 有歧义，N 处都是候选。
   * ⚠️ 这是推断不是关系 —— 展示时必须与 `direct` / `asset` 的确定位置区分，
   * 也不能拿它参与任何"确定有多少件"的汇总。
   */
  inferred_station_ids: number[]
  inferred_station_codes: string[]
}

/** 一条**当前分配记录**："现在在哪、有几件" */
export interface LightHolding extends LightHolder {
  id: number
  category_id: number
  /**
   * 品类信息。⚠️ **必须有** —— `LightHolder` 那半边只回答"在哪"（`display` 是宿主，
   * 资产页上就是那台机器自己的编号）。少了这几个字段，界面就只能显示
   * "装在 B022-03-00038 上 ×1"，看不出装的是什么东西。
   */
  category_name: string
  category_brand: string | null
  category_spec: string | null
  category_unit: string
  /** 「名称 · 品牌 规格」单行版 —— 只有一行位置的地方用它，别自己再拼一遍 */
  category_label: string
  /**
   * 品类图标标识（为空 = 未确认）。
   * ⚠️ **必须有** —— 点位侧栏与资产详情页的关联配件面板都靠它显示品类图标；
   * 少了它，同一品类在这些地方就会退回"猜出来的图标"，与列表页不一致。
   */
  category_icon: string | null
  quantity: number
  since: string | null
  note: string | null
}

export interface LightHoldingPage {
  items: LightHolding[]
  total: number
  page: number
  page_size: number
}

export interface LightCategoryStat {
  stock: number
  allocated: number
  net: number
  imported_count: number
  /** true = 有历史流水、当前账却是空的（典型：只导入了分配、没做期初确认）→ 界面说"未登记" */
  pending_opening: boolean
  /** 当前分配记录（只读 `light_holdings`，结构上不可能和库存 / 已分配对不上） */
  holdings: LightHolding[]
}

export interface LightCategory {
  id: number
  name: string
  brand: string | null
  spec: string | null
  unit: string
  notes: string | null
  /**
   * 已确认的图标标识（`utils/icons.ts::ICON_PATHS` 的 key），如 `keyboardMouse`。
   *
   * **为空 = 用户还没确认过图标**，界面据此按品类名给一个**建议**图标
   * （只显示、不写库）。判断"该不该显示未确认标记"也靠它。
   */
  icon: string | null
  active: boolean
  created_at: string
  updated_at: string
  /** 做过期初确认没有 —— **只是提示用**，不阻止再次登记（期初允许补录 / 重做） */
  opening_done: boolean
  stat: LightCategoryStat
}

export interface LightCategoryPage {
  items: LightCategory[]
  total: number
  page: number
  page_size: number
}

export interface LightMovement {
  id: number
  category_id: number
  category_name: string
  op: LightOp
  op_label: string
  quantity: number
  from_holder: LightHolder
  to_holder: LightHolder
  operator: string | null
  occurred_at: string
  created_at: string
  note: string | null
  origin: LightOrigin
  origin_label: string
  /** 是否计入当前状态（历史导入为 false） */
  effective: boolean
  import_batch: string | null
  corrects_id: number | null
  /** 这条流水能不能机械冲正。后端按 `LightOp.reverse_op()` 算好 —— 「报损」「安装（外部→X）」
   *  这类没有机械反向的操作，界面上不该出现一个点下去必然报错的按钮。 */
  can_reverse: boolean
}

export interface LightMovementPage {
  items: LightMovement[]
  total: number
  page: number
  page_size: number
}

/** 登记一次业务动作的响应：新流水（**更换是两条**）+ 这次操作后的当前状态 */
export interface LightMovementResult {
  movements: LightMovement[]
  stat: LightCategoryStat
}

/** 某资产 / 某点位的关联配件 */
export interface AssetLightHoldings {
  asset_id: number
  asset_code: string
  items: LightHolding[]
  total: number
}

/** 点位关联配件 —— **两组分开返回**，前端不做拼装判断 */
export interface StationLightHoldings {
  workstation_id: number
  code: string
  /** 固定在此工位（`holder_kind='workstation'`） */
  direct: LightHolding[]
  /** 随主机上的（`holder_kind='asset'` 且该资产当前就在本点位） */
  via_assets: LightHolding[]
  /** 库存就存在这个位置（`holder_kind='stock'` 且 `stock_location` 是本点位编码） */
  stock: LightHolding[]
  /** **推断**：记在某人手上，而这个人名下有本点位（他还有别的点位时那边也会出现同一条） */
  via_users: LightHolding[]
}

/** 期初确认预览：每个持有位置的「改前 → 改后」 */
export interface OpeningPreviewRow {
  holder: LightHolder
  before: number
  delta: number
  after: number
}

export interface OpeningPreview {
  category_id: number
  unit: string
  rows: OpeningPreviewRow[]
  stock: { before: number; after: number }
  allocated: { before: number; after: number }
  net: { before: number; after: number }
  opening_done: boolean
}

export interface LightSummary {
  category_count: number
  /** 流水总条数（含历史导入） */
  movement_count: number
  imported_movement_count: number
  /** 有历史流水、当前账却是空的品类数 —— 页头给一条中性提示，引导做期初确认 */
  pending_opening_count: number
  /** 当前分配记录条数 */
  holding_count: number
  stock_total: number
  allocated_total: number
}

/** 流转对话框里选「正式资产 / 点位」时的一个候选（/light/holders 搜索结果） */
export interface LightHolderCandidate {
  id: number
  /** 资产编码 / 点位编码（快照进流水用的就是这个） */
  code: string
  /** 主文案：设备类型名 / 点位编码 */
  label: string
  /** 副提示：当前使用人 / 房间备注（现读，仅展示） */
  hint: string
  kind: 'asset' | 'workstation'
}

export interface LightFilterOptions {
  categories: { id: number; name: string; unit: string }[]
  users: string[]
  ops: { value: LightOp; label: string }[]
  /** 已经用过的库存存放位置（自由文本候选） */
  stock_locations: string[]
}

export interface LightCategoryPayload {
  name: string
  brand?: string | null
  spec?: string | null
  unit?: string
  notes?: string | null
  /** 图标标识。传 null / '' = 清掉（回到"按名称给建议图标"）；不传 = 不动它 */
  icon?: string | null
  operator?: string | null
}

/** 一处持有位置的请求体（kind='none' = 系统外部 / 注销） */
export interface LightHolderRefPayload {
  kind: LightHolderKind
  ref_id?: number | null
  user?: string | null
  /** 仅 kind='stock'：存放位置（"库房A" / 某个点位编码…） */
  stock_location?: string | null
}

export interface LightMovementPayload {
  category_id: number
  op: LightOp
  from_ref: LightHolderRefPayload
  to_ref: LightHolderRefPayload
  quantity?: number
  occurred_at?: string | null
  note?: string | null
  operator?: string | null
  origin?: LightOrigin
  /** 前端每次提交带一个 UUID，防重复提交 */
  request_id?: string | null
}

/** 更换配件：一条业务动作 = 报损 + 安装两条流水（同事务） */
export interface LightReplacePayload {
  category_id: number
  asset_id: number
  quantity?: number
  occurred_at?: string | null
  note?: string | null
  operator?: string | null
  request_id?: string | null
}

export interface OpeningEntryPayload {
  holder: LightHolderRefPayload
  quantity: number
}

export interface OpeningPayload {
  category_id: number
  entries: OpeningEntryPayload[]
  operator?: string | null
  note?: string | null
  occurred_at?: string | null
}

/** 历史导入的一行判定结果 */
export interface LightImportRow {
  row: number
  action: 'import' | 'duplicate' | 'error'
  action_label: string
  message: string
  summary: string
}

export interface LightImportPreview {
  filename: string
  total: number
  will_import: number
  duplicate: number
  error: number
  rows: LightImportRow[]
  rows_truncated: boolean
  /** 文件层面的问题（表头缺列 / 空文件 / 解不开）—— 非空时整份文件都不落库 */
  schema_note: string | null
}
