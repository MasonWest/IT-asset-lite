import axios, { type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

import type {
  Asset,
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
  SystemInfo,
  TimelinePage,
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

export const systemApi = {
  info: () => get<SystemInfo>('/system/info'),
}

export const assetApi = {
  list: (query: AssetQuery = {}) => get<AssetPage>('/assets', query),
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
