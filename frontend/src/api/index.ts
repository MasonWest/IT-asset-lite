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
  InventoryContext,
  InventoryResult,
  InventoryScope,
  InventoryTask,
  InventoryTaskDetail,
  PairingBoard,
  Relation,
  RelationHistoryPage,
  MapInventoryView,
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
    payload: { name: string; sort_order?: number; category?: DeviceCategory },
    operator?: string | null,
  ) => post<DeviceType>('/device-types', payload, operator ? { operator } : undefined),
  update: (
    id: number,
    payload: { name?: string; sort_order?: number; category?: DeviceCategory },
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

export default http
