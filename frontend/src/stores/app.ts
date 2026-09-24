import { reactive } from 'vue'

import { systemApi } from '@/api'
import type { SystemInfo } from '@/types'

const OVERRIDE_KEY = 'it-asset.base-url'
const OPERATOR_KEY = 'it-asset.operator'

export const appState = reactive({
  ready: false,
  baseUrl: '',
  lanIp: '',
  port: 0,
  version: '',
  databaseFile: '',
  backupDir: '',
  assetCount: 0,
  override: '',
  /** 当前操作人 —— 领用/归还/盘点都要写进历史，记一次省得每次手打 */
  operator: '',
})

/** 操作人的名字存在浏览器里。系统没有账号体系（约束里也不做审批流），
 *  所以「操作人」只能靠用户自己填；存下来只是为了不用反复输入。 */
export function loadOperator(): string {
  try {
    return localStorage.getItem(OPERATOR_KEY) || ''
  } catch {
    return ''
  }
}

export function saveOperator(value: string): void {
  const normalized = value.trim()
  appState.operator = normalized
  try {
    if (normalized) localStorage.setItem(OPERATOR_KEY, normalized)
    else localStorage.removeItem(OPERATOR_KEY)
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略 */
  }
}

/** 扫描地址的手动覆盖值（多网卡 / 有 VPN / WSL / VMware 时自动探测可能不对） */
export function loadOverride(): string {
  try {
    return localStorage.getItem(OVERRIDE_KEY) || ''
  } catch {
    return ''
  }
}

export function saveOverride(value: string): void {
  const normalized = value.trim().replace(/\/+$/, '')
  appState.override = normalized
  try {
    if (normalized) localStorage.setItem(OVERRIDE_KEY, normalized)
    else localStorage.removeItem(OVERRIDE_KEY)
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略 */
  }
}

/** 扫码/分享用的真实对外地址 */
export function effectiveBaseUrl(): string {
  return appState.override || appState.baseUrl
}

/**
 * 下载链接里带的「操作人」。
 *
 * 为什么下载也要带：导出不是只读动作 —— 有数据离开系统。
 * 但下载是浏览器直接发起的请求（不走 axios），没法加请求头，
 * 所以只能把它挂成 query 参数交给后端写进审计。
 */
export function operatorParam(): string {
  return appState.operator ? `&operator=${encodeURIComponent(appState.operator)}` : ''
}

export function assetDetailUrl(id: number): string {
  const base = effectiveBaseUrl()
  return base ? `${base}/asset/${id}` : `/asset/${id}`
}

/** 后端出图，这样二维码内容一定是服务端认定的绝对地址 */
export function qrImageSrc(text: string, size = 240): string {
  return `/api/qrcode.png?text=${encodeURIComponent(text)}&size=${size}`
}

export async function loadSystemInfo(force = false): Promise<void> {
  if (appState.ready && !force) return
  appState.override = loadOverride()
  appState.operator = loadOperator()
  try {
    const info: SystemInfo = await systemApi.info()
    appState.baseUrl = info.base_url
    appState.lanIp = info.lan_ip
    appState.port = info.port
    appState.version = info.version
    appState.databaseFile = info.database_file
    appState.backupDir = info.backup_dir || ''
    appState.assetCount = info.asset_count
    appState.ready = true
  } catch {
    appState.ready = true
  }
}
