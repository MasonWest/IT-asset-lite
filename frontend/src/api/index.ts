import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import type {
  Asset,
  AssetGroupPage,
  AssetOperationPayload,
  AssetOperationResult,
  AssetPage,
  AssetPayload,
  AssetQuery,
  AuditMeta,
  AuditPage,
  AuditQuery,
  DeviceCategory,
  DeviceType,
  ExportOptions,
  FilterOptions,
  ImportMode,
  ImportPreview,
  ImportResult,
  InsightQuery,
  InsightsPage,
  InventoryContext,
  InventoryResult,
  InventoryScope,
  InventoryTask,
  InventoryTaskDetail,
  AssetLightHoldings,
  LightCategory,
  LightCategoryPage,
  LightCategoryPayload,
  LightFilterOptions,
  LightHolding,
  LightHoldingPage,
  LightHolderCandidate,
  LightImportPreview,
  LightMovement,
  LightMovementPage,
  LightMovementPayload,
  LightMovementResult,
  LightReplacePayload,
  LightSummary,
  OpeningPayload,
  OpeningPreview,
  StationLightHoldings,
  PairingBoard,
  Relation,
  RelationExceptionPayload,
  RelationHistoryPage,
  MapInventoryView,
  RelocationApplyPayload,
  RelocationApplyResult,
  RelocationPreview,
  RelocationPreviewPayload,
  StationRelationCheck,
  SystemInfo,
  TimelinePage,
  Workstation,
  WorkstationCreatePayload,
  WorkstationLayoutItem,
  WorkstationLayoutResult,
  WorkstationMap,
  WorkstationUpdatePayload,
  FloorMap,
  FloorMapList,
  FloorMapSavePayload,
  WorkstationBulkPayload,
  WorkstationBulkResult,
  LinkSuggestionsResult,
  LinkageApplyResult,
  ClearSuggestionsResult,
  UnlinkApplyResult,
} from '@/types'

const http = axios.create({
  baseURL: '/api',
  timeout: 20000,
})

function pickMessage(error: unknown): string {
  const err = error as {
    response?: { data?: { detail?: unknown } }
    message?: string
  }
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string }
    if (first?.msg) return first.msg
  }
  return err?.message || '请求失败，请检查后端服务是否已启动'
}

http.interceptors.response.use(
  (response) => response,
  (error) => {
    // 静默模式：用于探测类请求，不弹提示
    const cfg = error?.config as AxiosRequestConfig & { silent?: boolean }
    if (!cfg?.silent) {
      ElMessage.error(pickMessage(error))
    }
    return Promise.reject(error)
  },
)

/** 从 axios 错误里取后端那句人话，业务代码要自己弹提示时用 */
export function apiMessage(error: unknown): string {
  return pickMessage(error)
}

async function get<T>(url: string, params?: unknown, silent = false): Promise<T> {
  const res = await http.get<T>(url, { params: params as never, silent } as never)
  return res.data
}

async function post<T>(url: string, body?: unknown, params?: unknown): Promise<T> {
  const res = await http.post<T>(url, body, { params: params as never })
  return res.data
}

async function put<T>(url: string, body?: unknown, params?: unknown): Promise<T> {
  const res = await http.put<T>(url, body, { params: params as never })
  return res.data
}

async function del<T>(url: string, params?: unknown): Promise<T> {
  const res = await http.delete<T>(url, { params: params as never })
  return res.data
}

async function patch<T>(url: string, body?: unknown, params?: unknown): Promise<T> {
  const res = await http.patch<T>(url, body, { params: params as never })
  return res.data
}

export const systemApi = {
  info: () => get<SystemInfo>('/system/info'),
}

export const assetApi = {
  list: (query: AssetQuery = {}) => get<AssetPage>('/assets', query),
  /**
   * 套装聚合视图：把「主机 + 它挂着的显示器」合并成一行再分页。
   * 筛选口径与 list 完全一致（服务端共用同一套条件），只是返回的行结构不同。
   */
  grouped: (query: AssetQuery = {}) => get<AssetGroupPage>('/assets/grouped', query),
  detail: (id: number) => get<Asset>(`/assets/${id}`),
  create: (payload: AssetPayload) => post<Asset>('/assets', payload),
  update: (id: number, payload: Partial<AssetPayload>) => put<Asset>(`/assets/${id}`, payload),
  // operator 走 query：删除请求没有 body，但审计要记「谁删的」
  remove: (id: number, operator?: string | null) =>
    del<{ ok: boolean; asset_code: string }>(
      `/assets/${id}`,
      operator ? { operator } : undefined,
    ),
  timeline: (id: number, limit = 200) => get<TimelinePage>(`/assets/${id}/timeline`, { limit }),
  relations: (id: number) => get<RelationHistoryPage>(`/assets/${id}/relations`),
  operate: (id: number, payload: AssetOperationPayload) =>
    post<AssetOperationResult>(`/assets/${id}/operations`, payload),
}

export const deviceTypeApi = {
  list: () => get<DeviceType[]>('/device-types'),
  // operator 走 query：只影响审计日志里的「操作人」，不参与业务
  create: (
    payload: { name: string; sort_order?: number; category?: DeviceCategory; icon?: string | null },
    operator?: string | null,
  ) => post<DeviceType>('/device-types', payload, operator ? { operator } : undefined),
  update: (
    id: number,
    // icon 传 null / '' = 清掉图标（回到"按名称给建议图标"）；
    // 不传这个 key = 不动它（后端用 exclude_unset 区分）
    payload: { name?: string; sort_order?: number; category?: DeviceCategory; icon?: string | null },
    operator?: string | null,
  ) => put<DeviceType>(`/device-types/${id}`, payload, operator ? { operator } : undefined),
  remove: (id: number, operator?: string | null) =>
    del<{ ok: boolean }>(`/device-types/${id}`, operator ? { operator } : undefined),
}

export const metaApi = {
  filters: () => get<FilterOptions>('/meta/filters'),
}

export const pairingApi = {
  board: () => get<PairingBoard>('/pairings/board'),
  history: (params: { asset_id?: number; active_only?: boolean; page?: number; page_size?: number } = {}) =>
    get<RelationHistoryPage>('/pairings/history', params),
  bind: (payload: { monitor_id: number; host_id: number; operator?: string | null; note?: string | null }) =>
    post<Relation>('/pairings', payload),
  rebind: (payload: { monitor_id: number; host_id: number; operator?: string | null; note?: string | null }) =>
    post<Relation>('/pairings/rebind', payload),
  release: (relationId: number, payload: { operator?: string | null; note?: string | null }) =>
    post<Relation>(`/pairings/${relationId}/release`, payload),
}

export const inventoryApi = {
  tasks: (params: { status?: string } = {}) => get<InventoryTask[]>('/inventory/tasks', params),
  task: (id: number, params: { result?: InventoryResult } = {}) =>
    get<InventoryTaskDetail>(`/inventory/tasks/${id}`, params),
  create: (payload: {
    name: string
    note?: string | null
    created_by?: string | null
    scope: InventoryScope
  }) => post<InventoryTask>('/inventory/tasks', payload),
  mark: (taskId: number, payload: { asset_id: number; result: InventoryResult; operator?: string | null; note?: string | null }) =>
    post<{ item: unknown; task: InventoryTask }>(`/inventory/tasks/${taskId}/mark`, payload),
  addItem: (taskId: number, assetId: number) =>
    post<unknown>(`/inventory/tasks/${taskId}/items`, null, { asset_id: assetId }),
  // 结束 / 重新开启的 operator 是 query 参数（后端按操作日志处理，不进请求体）
  close: (taskId: number, operator?: string | null) =>
    post<InventoryTask>(`/inventory/tasks/${taskId}/close`, null, { operator: operator || undefined }),
  reopen: (taskId: number, operator?: string | null) =>
    post<InventoryTask>(`/inventory/tasks/${taskId}/reopen`, null, { operator: operator || undefined }),
  remove: (taskId: number, operator?: string | null) =>
    del<{ ok: boolean }>(`/inventory/tasks/${taskId}`, operator ? { operator } : undefined),
  context: (assetId: number) =>
    get<InventoryContext | null>(`/inventory/context/${assetId}`, undefined, true),
}

/** 导出走浏览器直接下载（中文文件名由后端 Content-Disposition 提供） */
export function inventoryExportUrl(
  taskId: number,
  opts: { fmt?: 'xlsx' | 'csv'; onlyDiff?: boolean; result?: InventoryResult } = {},
): string {
  const params = new URLSearchParams()
  params.set('fmt', opts.fmt ?? 'xlsx')
  if (opts.onlyDiff) params.set('only_diff', 'true')
  if (opts.result) params.set('result', opts.result)
  return `/api/inventory/tasks/${taskId}/export?${params.toString()}`
}

// --------------------------------------------------------------------------- //
// 批量导入 / 导出
// --------------------------------------------------------------------------- //
/** 把对象拼成查询串，空值一律丢掉 —— 用于拼下载链接（<a href> 那种，必须是字符串） */
function queryString(params: Record<string, unknown>): string {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    sp.set(key, String(value))
  }
  return sp.toString()
}

/**
 * 交给 axios 当 params 用的对象。
 *
 * 注意不能直接给 axios 传拼好的查询串 —— axios 1.20 起 `params` 必须是对象，
 * 传字符串会在内部 toFormData 那里直接抛 "target must be an object"，
 * 而且抛在请求发出去之前，后端日志里一点痕迹都没有，特别难查。
 */
function pruneParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    out[key] = value
  }
  return out
}

/** 资产列表的筛选条件 → 查询参数（导出和列表共用一套口径） */
function assetQueryParams(query: AssetQuery = {}): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const kw = query.keyword?.trim()
  if (kw) out.keyword = kw
  if (query.device_type_id !== undefined) out.device_type_id = query.device_type_id
  if (query.status) out.status = query.status
  if (query.user_name) out.user_name = query.user_name
  if (query.location) out.location = query.location
  if (query.paired) out.paired = query.paired
  if (query.workstation) out.workstation = query.workstation
  return out
}

export const transferApi = {
  templateUrl: (fmt: 'xlsx' | 'csv' = 'xlsx', samples = true) =>
    `/api/transfer/template?${queryString({ fmt, samples })}`,

  /**
   * 导出资产。`scope: 'all'` 时忽略筛选条件导全部。
   * `operator` 只用于审计留痕，不进筛选。
   */
  exportUrl: (
    query: AssetQuery,
    opts: ExportOptions & { operator?: string | null } = { fmt: 'xlsx' },
  ) => {
    const scope = opts.scope ?? 'filtered'
    const params = {
      ...(scope === 'filtered' ? assetQueryParams(query) : {}),
      fmt: opts.fmt,
      with_pairing: opts.withPairing ?? false,
      operator: opts.operator ?? '',
    }
    return `/api/transfer/export?${queryString(params)}`
  },

  /** 预览：dry_run=true，服务端只解析校验、不写库 */
  preview: (
    file: File,
    opts: { mode: ImportMode; autoCreateType: boolean; operator?: string | null },
  ) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return post<ImportPreview>(
      '/transfer/import',
      form,
      pruneParams({
        dry_run: true,
        mode: opts.mode,
        auto_create_type: opts.autoCreateType,
        operator: opts.operator ?? '',
      }),
    )
  },

  /** 正式导入：dry_run=false，和预览跑的是同一段校验代码 */
  commit: (
    file: File,
    opts: { mode: ImportMode; autoCreateType: boolean; operator?: string | null },
  ) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return post<ImportResult>(
      '/transfer/import',
      form,
      pruneParams({
        dry_run: false,
        mode: opts.mode,
        auto_create_type: opts.autoCreateType,
        operator: opts.operator ?? '',
      }),
    )
  },
}

// --------------------------------------------------------------------------- //
// 点位（资产空间地图）
// --------------------------------------------------------------------------- //
/**
 * 点位相关的接口。
 *
 * 注意「点位上挂的资产」走的是 `/workstations/{id}/assets`，
 * 那查的是 `assets.workstation_id`（单一真相），**不是**点位表里存的资产列表 ——
 * 点位表里根本不存资产列表。
 */
export const workstationApi = {
  /** 地图渲染用：点位列表 + 画布尺寸。画布尺寸由后端给，前端不再硬编码 */
  map: () => get<WorkstationMap>('/workstations'),

  detail: (id: number) => get<Workstation>(`/workstations/${id}`),

  /** 侧栏「资产清单」：真实资产，按 asset_code 排序 */
  assets: (id: number) => get<Asset[]>(`/workstations/${id}/assets`),

  /** 新增。code 留空由**后端**生成 —— 前端算会并发撞号 */
  create: (payload: WorkstationCreatePayload) => post<Workstation>('/workstations', payload),

  update: (id: number, payload: WorkstationUpdatePayload) =>
    patch<Workstation>(`/workstations/${id}`, payload),

  /**
   * 拖动后**攒批**提交坐标。
   * 逐次提交会让审计被几十次布局微调刷屏 —— 一次布局调整 = 1 条审计。
   */
  saveLayout: (items: WorkstationLayoutItem[], operator?: string | null) =>
    patch<WorkstationLayoutResult>('/workstations/layout', { items, operator }),

  /** 把一批资产挂到这个点位上 */
  bindAssets: (id: number, assetIds: number[], operator?: string | null) =>
    post<{ moved: number; asset_codes: string[] }>(`/workstations/${id}/assets`, {
      asset_ids: assetIds,
      operator,
    }),

  /** 从点位上摘下来（**资产留着**，只是没有点位了） */
  unbindAsset: (id: number, assetId: number, operator?: string | null) =>
    del<{ ok: boolean; asset_code: string }>(
      `/workstations/${id}/assets/${assetId}`,
      operator ? { operator } : undefined,
    ),

  /**
   * 删除点位（软删除）。
   * @param force 点位上还有资产时，是否一并清空这些资产的点位归属（**不删资产**）
   */
  remove: (id: number, opts: { operator?: string | null; force?: boolean } = {}) =>
    del<{ ok: boolean; code: string; detached_assets: string[]; note: string }>(
      `/workstations/${id}`,
      pruneParams({ operator: opts.operator ?? '', force: opts.force ? 'true' : '' }),
    ),

  /**
   * 批量生成点位（行列排布）。
   *
   * ⚠️ **必须走这个接口，不要循环调 `create()`** ——
   * 一次批量操作 = 1 条审计（全系统口径）。循环 24 次会往审计页灌 24 条，
   * 把真正的资产操作淹掉。
   */
  bulk: (payload: WorkstationBulkPayload) =>
    post<WorkstationBulkResult>('/workstations/bulk', payload),

  /**
   * 关联操作建议（预检，干跑）：给无归属点位新增使用人前，
   * 先看该点位下资产的关联建议。只读、不写库。
   */
  linkSuggestions: (id: number, userName: string) =>
    get<LinkSuggestionsResult>(`/workstations/${id}/link-suggestions`, {
      user_name: userName,
    }),

  /** 执行关联建议：只改用户勾选的资产使用人（后端执行前会重新核对） */
  applyLinkage: (id: number, payload: { user_name: string; asset_ids: number[]; operator?: string | null }) =>
    post<LinkageApplyResult>(`/workstations/${id}/apply-linkage`, payload),

  /**
   * 清人建议（预检，干跑）：点位使用人要从有人 → 无人前，
   * 看该点位下还有哪些设备挂在他名下。只读。
   */
  unlinkSuggestions: (id: number) => get<ClearSuggestionsResult>(`/workstations/${id}/unlink-suggestions`),

  /** 执行清人建议：只解除用户勾选的设备使用人（后端执行前会重新核对） */
  applyUnlink: (id: number, payload: { asset_ids: number[]; operator?: string | null }) =>
    post<UnlinkApplyResult>(`/workstations/${id}/apply-unlink`, payload),
}

/**
 * 平面图。**当前只有一张** —— 所以没有 `maps[]`、没有选择器、没有 `?map=`。
 *
 * 唯一用得到的入口是 `default()`（地图/编辑器打开时读）与 `save()`（编辑器提交）。
 * `list()` 存在只是为了让"契约上有列表"这件事可查，**UI 不许用它渲染多个图**。
 */
export const floorMapApi = {
  list: () => get<FloorMapList>('/floor-maps'),

  /** 默认（也是唯一）那张图。地图页与编辑器都读它。 */
  default: () => get<FloorMap>('/floor-maps/default'),

  detail: (id: number) => get<FloorMap>(`/floor-maps/${id}`),

  /**
   * 保存整张图（覆盖 layout）。
   *
   * ⚠️ 必须带 `expected_updated_at`（乐观锁）：两个人同时开着编辑器时，
   * 后保存的那个会拿到 409 而不是静默覆盖。不带 = 跳过检查，只有脚本该这么用。
   */
  save: (id: number, payload: FloorMapSavePayload) => put<FloorMap>(`/floor-maps/${id}`, payload),
}

export const mapInventoryApi = {
  /**
   * 地图盘点视图：**按 task_id 显式取**，一次拿全图状态。
   *
   * 不复用 `/inventory/context/{asset_id}` —— 那个是单任务假设
   * （取"该设备所属的最近一个进行中任务"），在资产详情页合理，
   * 但地图问的是"这一片点位整体盘到哪了"，必须明确聚合的是哪一次。
   */
  view: (taskId: number) => get<MapInventoryView>(`/inventory/tasks/${taskId}/map-view`),
}

// --------------------------------------------------------------------------- //
// 关系核对（点位使用人 ↔ 设备使用人）
// --------------------------------------------------------------------------- //
/**
 * 关系核对。
 *
 * ⚠️ 这里的 GET **每次都是现算**的派生结果，后端没有"问题表"——
 * 所以没有"刷新问题列表"这种动作，也没有 updateIssue 之类的方法。
 * 唯一写操作是「例外确认」：它记的是**人的决定**，不是问题的状态。
 */
export const relationCheckApi = {
  workstation: (stationId: number) =>
    get<StationRelationCheck>(`/relation-checks/workstation/${stationId}`),

  /** 确认一条差异为例外。**理由必填**（没理由的例外 = 把提示关掉）。 */
  confirm: (stationId: number, payload: RelationExceptionPayload) =>
    post<StationRelationCheck>(`/relation-checks/workstation/${stationId}/exceptions`, payload),

  /** 撤销例外（软删，记录留着）。 */
  revoke: (exceptionId: number, operator?: string | null) =>
    del<StationRelationCheck>(
      `/relation-checks/exceptions/${exceptionId}`,
      operator ? { operator } : undefined,
    ),
}

// --------------------------------------------------------------------------- //
// 点位迁移
// --------------------------------------------------------------------------- //
/**
 * 点位迁移。**与 `/api/transfer`（导入导出）是两回事** ——
 * 那边改"台账里有哪些设备"，这里改"设备摆在哪 / 这个位置归谁"。
 *
 * `preview` 不写任何数据；`apply` 必须带上 preview 拿到的令牌，
 * 令牌对不上后端会 409（挡住"预览后被别人改了"和"手抖点了两次"）。
 */
export const relocationApi = {
  preview: (payload: RelocationPreviewPayload) =>
    post<RelocationPreview>('/relocations/preview', payload),
  apply: (payload: RelocationApplyPayload) =>
    post<RelocationApplyResult>('/relocations/apply', payload),
}

// --------------------------------------------------------------------------- //
// 轻资产（不纳入正式台账的 IT 配件）
// --------------------------------------------------------------------------- //
/**
 * ⚠️ 库存 / 已分配**全部由后端从 `light_holdings` 聚合**（GET 返回值里的 `stat`），
 * 前端一个数都不自己加 —— 本地加减必然和库里的真相打架。
 *
 * 登记业务动作（`movements`）是**唯一写入口**，每次提交带 `request_id`（UUID），
 * 重复提交后端 409，不会二次入账。没有"直接改库存"的接口。
 */
export const lightApi = {
  summary: () => get<LightSummary>('/light/summary'),
  filters: () => get<LightFilterOptions>('/light/filters'),

  categories: (
    params: { keyword?: string; active?: string; page?: number; page_size?: number } = {},
  ) =>
    get<LightCategoryPage>(
      '/light/categories',
      pruneParams({
        keyword: params.keyword ?? '',
        active: params.active ?? '',
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      }),
    ),
  category: (id: number) => get<LightCategory>(`/light/categories/${id}`),
  createCategory: (payload: LightCategoryPayload) =>
    post<LightCategory>('/light/categories', payload),
  updateCategory: (id: number, payload: Partial<LightCategoryPayload>) =>
    put<LightCategory>(`/light/categories/${id}`, payload),
  /** 停用（软删）。force = 还有配件在外也停（不删任何数据） */
  deactivateCategory: (id: number, opts: { operator?: string | null; force?: boolean } = {}) =>
    del<LightCategory>(
      `/light/categories/${id}`,
      pruneParams({ operator: opts.operator ?? '', force: opts.force ? 'true' : '' }),
    ),

  /** 当前分配记录（列表页 / 详情页） */
  holdings: (
    params: {
      category_id?: number
      holder_kind?: string
      keyword?: string
      page?: number
      page_size?: number
    } = {},
  ) =>
    get<LightHoldingPage>(
      '/light/holdings',
      pruneParams({
        category_id: params.category_id,
        holder_kind: params.holder_kind ?? '',
        keyword: params.keyword ?? '',
        page: params.page ?? 1,
        page_size: params.page_size ?? 100,
      }),
    ),

  /** 某资产的关联配件（资产详情页「关联轻资产」区块） */
  byAsset: (assetId: number) => get<AssetLightHoldings>(`/light/by-asset/${assetId}`),
  /** 某点位的关联配件 —— 返回 `direct` / `via_assets` 两组 */
  byWorkstation: (stationId: number) =>
    get<StationLightHoldings>(`/light/by-workstation/${stationId}`),

  movements: (
    params: {
      category_id?: number
      op?: string
      origin?: string
      keyword?: string
      start?: string
      end?: string
      page?: number
      page_size?: number
    } = {},
  ) =>
    get<LightMovementPage>(
      '/light/movements',
      pruneParams({
        category_id: params.category_id,
        op: params.op ?? '',
        origin: params.origin ?? '',
        keyword: params.keyword ?? '',
        start: params.start ?? '',
        end: params.end ?? '',
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      }),
    ),

  /** 登记一次业务动作。唯一写入口 —— op 决定语义，from/to 决定物料移动 */
  createMovement: (payload: LightMovementPayload) =>
    post<LightMovementResult>('/light/movements', payload),

  /** **更换配件**：一条业务动作 = 报损 + 安装两条流水（同事务） */
  replacePart: (payload: LightReplacePayload) =>
    post<LightMovementResult>('/light/replace', payload),

  /** 冲正 = 追加一条反向流水，原记录一个字不改 */
  reverseMovement: (
    id: number,
    opts: { note?: string | null; operator?: string | null; request_id?: string | null } = {},
  ) =>
    post<LightMovementResult>(
      `/light/movements/${id}/reverse`,
      null,
      pruneParams({
        note: opts.note ?? '',
        operator: opts.operator ?? '',
        request_id: opts.request_id ?? '',
      }),
    ),

  /** 期初确认预览（**不写任何数据**）：每个持有位置的「改前 → 改后」 */
  openingPreview: (payload: OpeningPayload) =>
    post<OpeningPreview>('/light/opening/preview', payload),
  /** 登记期初确认。允许补录 / 重做，不永久封锁 */
  registerOpening: (payload: OpeningPayload) =>
    post<LightMovementResult>('/light/opening', payload),

  /** 历史导入预览（只补历史，不动当前账） */
  importPreview: (file: File) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return post<LightImportPreview>('/light/import/preview', form)
  },
  /** 历史导入落库：只写流水，**当前持有不变** */
  importHistory: (file: File, operator?: string | null) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return post<LightImportPreview>(
      '/light/import',
      form,
      pruneParams({ operator: operator ?? '' }),
    )
  },

  /** 导出轻资产台账（当前分配 + 流转流水）。给 `<a href>` 用 */
  exportUrl: (categoryId?: number | null) =>
    `/api/light/export?${queryString(pruneParams({ category_id: categoryId ?? '' }))}`,

  /**
   * 搜索可关联的正式资产 / 点位（流转对话框选宿专用）。
   *
   * `limit = 0`（默认）= **不截断**，一次返回全部候选：点位 ~70、资产 ~150，
   * 打开下拉就能一直往下滚。早先写死 20，下拉只露前 20 条、用户以为"就这么点"。
   * 搜关键词时也只返回匹配项，所以"全量"只在没输入时发生。
   */
  holders: (kind: 'asset' | 'workstation', keyword = '', limit = 0) =>
    get<LightHolderCandidate[]>('/light/holders', pruneParams({ kind, keyword, limit })),
}

// --------------------------------------------------------------------------- //
// 操作审计
// --------------------------------------------------------------------------- //
function auditQueryParams(query: AuditQuery = {}): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (query.start) out.start = query.start
  if (query.end) out.end = query.end
  if (query.action) out.action = query.action
  if (query.operator) out.operator = query.operator
  if (query.ip) out.ip = query.ip
  if (query.status) out.status = query.status
  const kw = query.keyword?.trim()
  if (kw) out.keyword = kw
  return out
}

export const auditApi = {
  list: (query: AuditQuery = {}) => get<AuditPage>('/audit', query),
  meta: () => get<AuditMeta>('/audit/meta'),
  exportUrl: (query: AuditQuery, fmt: 'xlsx' | 'csv' = 'xlsx', actor?: string | null) =>
    `/api/audit/export?${queryString({ ...auditQueryParams(query), fmt, actor: actor ?? '' })}`,
}

/** 触发浏览器下载。中文文件名由后端的 Content-Disposition 决定。 */
export function downloadUrl(url: string): void {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

// --------------------------------------------------------------------------- //
// 问题中心
// --------------------------------------------------------------------------- //
export const insightsApi = {
  /**
   * 全库问题清单（**现算，不落库**）。
   *
   * `silent`：导航栏那个角标用它 —— 拿不到就静静不显示，
   * 不该因为一个角标在别的页面上弹错误提示。
   */
  issues: (query: InsightQuery = {}, silent = false) =>
    get<InsightsPage>('/insights/issues', pruneParams(query as Record<string, unknown>), silent),
}

export default http
