<script setup lang="ts">
/**
 * 盘点详情：进度 / 逐台标记 / 差异 / 导出。
 *
 * 标记有三种入口，对应三种真实场景：
 *   1. 手机扫设备上的二维码 → 落到设备详情页，顶部自动出现盘点条（InventoryContextBar）
 *   2. USB 扫码枪/手动输入编号 → 用页头那个「扫码或输入编号」框，回车即标记已盘
 *   3. 在列表里对着某一行点按钮
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, inventoryApi, inventoryExportUrl } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState, loadSystemInfo, saveOperator } from '@/stores/app'
import type { InventoryItem, InventoryResult, InventoryTaskDetail } from '@/types'
import {
  formatDateTime,
  inventoryResultMeta,
  orDash,
  statusMeta,
  typeIcon,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
/** 点进某台设备后要能退回这个盘点任务 */
const { withBack, goBack } = useBackNav('/inventory')

const loading = ref(false)
const task = ref<InventoryTaskDetail | null>(null)
const notFound = ref(false)
const resultFilter = ref<InventoryResult | ''>('')
const search = ref('')
const operatorInput = ref('')
const acting = ref(false)
const scanInput = ref('')

const taskId = computed(() => Number(route.params.id))

const items = computed(() => task.value?.items ?? [])

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return items.value.filter((i) => {
    if (resultFilter.value && i.result !== resultFilter.value) return false
    if (!kw) return true
    const a = i.asset
    return [a.asset_code, a.serial_number, a.user_name, a.location, a.brand, a.model]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(kw))
  })
})

/** 输入框里是「编号」还是「设备详情页 URL」（扫码枪/微信扫到的二维码内容是 URL） */
function extractCode(raw: string): string {
  const text = raw.trim()
  const match = text.match(/\/asset\/(\d+)/)
  if (match) return `#${match[1]}`
  return text
}

async function load() {
  loading.value = true
  notFound.value = false
  try {
    task.value = await inventoryApi.task(taskId.value)
  } catch {
    task.value = null
    notFound.value = true
  } finally {
    loading.value = false
  }
}

function ensureOperator(): string | null {
  const op = (appState.operator || operatorInput.value).trim()
  if (!op) {
    ElMessage.warning('先填一下操作人，盘点结果要记录盘点人')
    return null
  }
  saveOperator(op)
  operatorInput.value = op
  return op
}

async function mark(item: InventoryItem, result: InventoryResult, note?: string | null) {
  const op = ensureOperator()
  if (!op) return

  acting.value = true
  try {
    await inventoryApi.mark(taskId.value, {
      asset_id: item.asset_id,
      result,
      operator: op,
      note: note ?? null,
    })
    await load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

async function markAbnormal(item: InventoryItem) {
  try {
    const res = await ElMessageBox.prompt(
      `${item.asset.asset_code}：哪里不对？`,
      '标记异常',
      {
        confirmButtonText: '确认异常',
        cancelButtonText: '取消',
        inputPlaceholder: '例如「位置不对，实际在 4F」「SN 对不上」「外壳破损」',
        inputValue: '',
      },
    )
    await mark(item, 'abnormal', res.value?.trim() || null)
    ElMessage.success('已标记异常')
  } catch {
    /* 用户取消 */
  }
}

async function quickMark() {
  const raw = scanInput.value.trim()
  if (!raw) return
  const code = extractCode(raw)
  const op = ensureOperator()
  if (!op) return

  acting.value = true
  try {
    // 支持 #12 这种按 ID 直接定位（详情页 URL 扫码的场景）
    let assetId: number | null = null
    if (code.startsWith('#')) {
      assetId = Number(code.slice(1))
    } else {
      const res = await assetApi.list({ keyword: code, page_size: 5 })
      const hit = res.items.find((a) => a.asset_code.toLowerCase() === code.toLowerCase()) ?? res.items[0]
      assetId = hit?.id ?? null
    }

    if (!assetId) {
      ElMessage.warning(`没找到编号「${code}」对应的设备`)
      return
    }

    const item = items.value.find((i) => i.asset_id === assetId)
    if (!item) {
      ElMessage.warning(`设备 #${assetId} 不在这轮盘点的范围里`)
      return
    }

    await inventoryApi.mark(taskId.value, { asset_id: assetId, result: 'checked', operator: op })
    scanInput.value = ''
    await load()
    ElMessage.success(`${item.asset.asset_code} 已标记为已盘`)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

async function closeTask() {
  try {
    await ElMessageBox.confirm(
      '结束这轮盘点？结束后不能再标记，但随时可以重新开启，导出也不受影响。',
      '结束盘点',
      { type: 'warning', confirmButtonText: '结束盘点', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await inventoryApi.close(taskId.value, appState.operator || null)
    ElMessage.success('已结束')
    await load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

async function reopenTask() {
  try {
    await inventoryApi.reopen(taskId.value, appState.operator || null)
    ElMessage.success('已重新开启')
    await load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

async function removeTask() {
  try {
    await ElMessageBox.confirm('删除这个盘点任务？已标记的结果会一起删掉，不可恢复。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await inventoryApi.remove(taskId.value, appState.operator || null)
    ElMessage.success('已删除')
    goBack()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

function exportFile(fmt: 'xlsx' | 'csv', onlyDiff = false) {
  const link = document.createElement('a')
  link.href = inventoryExportUrl(taskId.value, { fmt, onlyDiff })
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function openAsset(id: number) {
  void router.push({ path: `/asset/${id}`, query: withBack() })
}

const resultCards = computed(() => {
  const t = task.value
  return [
    { key: '' as const, label: '全部', value: t?.total ?? 0, icon: '📦' },
    { key: 'checked' as const, label: '已盘', value: t?.checked ?? 0, icon: '✅' },
    { key: 'abnormal' as const, label: '异常', value: t?.abnormal ?? 0, icon: '⚠️' },
    { key: 'pending' as const, label: '未盘', value: t?.pending ?? 0, icon: '⏳' },
  ]
})

onMounted(async () => {
  await loadSystemInfo()
  operatorInput.value = appState.operator
  await load()
})

watch(() => route.params.id, load)
</script>

<template>
  <div v-loading="loading">
    <div style="margin-bottom: 12px">
      <el-button link @click="goBack">← 返回盘点列表</el-button>
    </div>

    <div v-if="notFound && !loading" class="empty-state">
      <div class="big">🔍</div>
      <div class="title">没有这个盘点任务</div>
      <div>它可能已经被删除了，或者链接不对。</div>
      <div style="margin-top: 16px">
        <el-button type="primary" @click="goBack">返回盘点列表</el-button>
      </div>
    </div>

    <template v-else-if="task">
      <!-- 头部 -->
      <div class="inv-hero">
        <div class="lead">
          <div class="name-row">
            <h1>{{ task.name }}</h1>
            <span class="status-pill" :class="task.status">
              {{ task.status_label }}
            </span>
          </div>
          <p class="sub">
            {{ task.scope_text }} ·
            {{ task.created_by || '—' }} 发起于 {{ formatDateTime(task.created_at) }}
            <template v-if="task.closed_at"> · {{ formatDateTime(task.closed_at) }} 结束</template>
          </p>
          <p v-if="task.note" class="note">{{ task.note }}</p>
        </div>

        <div class="progress-ring">
          <div class="pct">{{ task.progress }}<small>%</small></div>
          <div class="txt">{{ task.checked }} / {{ task.total }} 已盘</div>
        </div>
      </div>

      <div class="head-actions" style="margin-bottom: 16px">
        <el-button size="small" @click="exportFile('xlsx')">导出 Excel</el-button>
        <el-button size="small" @click="exportFile('csv')">导出 CSV</el-button>
        <el-button size="small" @click="exportFile('xlsx', true)">只导出差异</el-button>
        <el-button v-if="task.status === 'open'" size="small" @click="closeTask">结束盘点</el-button>
        <el-button v-else size="small" @click="reopenTask">重新开启</el-button>
        <el-button size="small" link @click="removeTask">删除任务</el-button>
      </div>

      <!-- 扫码标记 -->
      <div v-if="task.status === 'open'" class="panel scan-panel">
        <div class="scan-head">
          <span class="ic">📷</span>
          <div>
            <div class="t1">扫码标记</div>
            <div class="t2">
              手机连同一个 Wi-Fi，用相机或微信扫设备上的二维码 → 打开详情页后顶部会直接出现盘点按钮。
              在电脑上也可以用 USB 扫码枪，或者手动敲编号，回车即标记为已盘。
            </div>
          </div>
        </div>
        <div class="scan-input">
          <el-input
            v-model="scanInput"
            size="large"
            placeholder="扫码枪扫描 / 输入资产编号 / 粘贴详情页链接，然后回车"
            clearable
            @keyup.enter="quickMark"
          />
          <el-button size="large" type="primary" :loading="acting" @click="quickMark">标记已盘</el-button>
          <el-input
            v-if="!appState.operator"
            v-model="operatorInput"
            size="large"
            class="who"
            placeholder="盘点人"
            @change="saveOperator(operatorInput)"
          />
          <span v-else class="who-chip">盘点人 {{ appState.operator }}</span>
        </div>
      </div>

      <!-- 统计 + 差异 -->
      <div class="stat-row">
        <div
          v-for="c in resultCards"
          :key="c.key"
          class="stat-card"
          :class="{ active: resultFilter === c.key }"
          @click="resultFilter = c.key"
        >
          <div class="k"><span>{{ c.icon }}</span>{{ c.label }}</div>
          <div class="v">{{ c.value }}<small>台</small></div>
        </div>
      </div>

      <el-alert
        v-if="task.diff.length"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 14px"
      >
        <template #title>
          差异 {{ task.diff.length }} 台：未盘 {{ task.pending }} 台、异常 {{ task.abnormal }} 台
        </template>
        <div class="diff-lines">
          <div v-for="d in task.diff" :key="d.id" class="diff-line">
            <span class="code" @click="openAsset(d.asset_id)">{{ d.asset.asset_code }}</span>
            <span
              class="tag"
              :style="{
                color: inventoryResultMeta(d.result).color,
                background: inventoryResultMeta(d.result).bg,
              }"
            >
              {{ d.result_label }}
            </span>
            <span class="where">{{ orDash(d.snapshot_location) }}</span>
            <span v-if="d.note" class="note">{{ d.note }}</span>
          </div>
        </div>
        <div style="margin-top: 8px">
          <el-button size="small" @click="exportFile('xlsx', true)">导出差异报表</el-button>
        </div>
      </el-alert>

      <div v-else-if="task.status === 'closed'" class="panel" style="padding: 14px 18px; margin-bottom: 14px">
        <span style="font-size: 13px; color: #0f7b3f">
          ✅ 这轮盘点全部盘到，没有差异。
        </span>
      </div>

      <!-- 筛选 -->
      <div class="filter-bar">
        <el-input
          v-model="search"
          class="grow"
          placeholder="搜索编号 / SN / 使用人 / 位置"
          clearable
        />
        <el-button v-if="resultFilter || search" @click="resultFilter = ''; search = ''">重置</el-button>
        <div class="spacer" />
        <span style="font-size: 13px; color: var(--text-2)">显示 {{ filtered.length }} 台</span>
      </div>

      <!-- 明细 -->
      <div class="panel" style="padding: 0; overflow: hidden">
        <div class="inv-table-head">
          <span class="c1">设备</span>
          <span class="c2">账面信息</span>
          <span class="c3">盘点结果</span>
          <span class="c4">操作</span>
        </div>

        <div v-for="i in filtered" :key="i.id" class="inv-row">
          <div class="c1">
            <span class="type-badge sm">{{ typeIcon(i.asset.device_type_name) }}</span>
            <div class="info" @click="openAsset(i.asset_id)">
              <div class="code">{{ i.asset.asset_code }}</div>
              <div class="model">{{ orDash(i.asset.brand) }} {{ i.asset.model || '' }}</div>
              <div class="sn">SN {{ orDash(i.asset.serial_number) }}</div>
            </div>
          </div>

          <div class="c2">
            <div class="line">
              使用人 {{ orDash(i.asset.user_name) }}
              <span v-if="i.snapshot_user !== i.asset.user_name" class="drift">
                （盘点时 {{ orDash(i.snapshot_user) }}）
              </span>
            </div>
            <div class="line">
              {{ orDash(i.asset.location) }}
              <span v-if="i.snapshot_location !== i.asset.location" class="drift">
                （盘点时 {{ orDash(i.snapshot_location) }}）
              </span>
            </div>
            <div class="line">
              <span
                class="mini-status"
                :style="{
                  color: statusMeta(i.asset.status).color,
                  background: statusMeta(i.asset.status).bg,
                }"
              >
                {{ i.asset.status_label }}
              </span>
              <span v-if="i.changed_since" class="drift">标记之后账面被改过</span>
            </div>
          </div>

          <div class="c3">
            <span
              class="result-chip"
              :style="{
                color: inventoryResultMeta(i.result).color,
                background: inventoryResultMeta(i.result).bg,
                borderColor: inventoryResultMeta(i.result).border,
              }"
            >
              {{ inventoryResultMeta(i.result).icon }} {{ i.result_label }}
            </span>
            <div v-if="i.operator" class="meta">by {{ i.operator }}</div>
            <div v-if="i.checked_at" class="meta">{{ formatDateTime(i.checked_at) }}</div>
            <div v-if="i.note" class="note">{{ i.note }}</div>
          </div>

          <div class="c4">
            <template v-if="task.status === 'open'">
              <el-button
                v-if="i.result !== 'checked'"
                size="small"
                type="success"
                :loading="acting"
                @click="mark(i, 'checked')"
              >
                已盘
              </el-button>
              <el-button
                v-if="i.result !== 'abnormal'"
                size="small"
                :loading="acting"
                @click="markAbnormal(i)"
              >
                异常
              </el-button>
              <el-button
                v-if="i.result !== 'pending'"
                size="small"
                link
                :loading="acting"
                @click="mark(i, 'pending')"
              >
                撤销
              </el-button>
            </template>
            <span v-else class="meta">已结束</span>
          </div>
        </div>

        <div v-if="!filtered.length" class="empty-note">
          {{ search || resultFilter ? '没有符合条件的设备' : '这个任务里还没有设备' }}
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.inv-hero {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  padding: 20px;
  margin-bottom: 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.inv-hero .lead {
  flex: 1;
  min-width: 0;
}

.inv-hero .name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.inv-hero h1 {
  font-size: 21px;
}

.inv-hero .sub {
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--text-2);
}

.inv-hero .note {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--text-3);
}

.inv-hero .status-pill.open {
  color: #0b6bcb;
  background: #eaf2ff;
  border-color: #bed6ff;
}

.inv-hero .status-pill.closed {
  color: #7a7f87;
  background: #f2f3f5;
  border-color: #dcdee3;
}

.progress-ring {
  flex-shrink: 0;
  text-align: right;
}

.progress-ring .pct {
  font-size: 30px;
  font-weight: 600;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  color: var(--primary);
}

.progress-ring .pct small {
  font-size: 14px;
  font-weight: 400;
}

.progress-ring .txt {
  font-size: 12px;
  color: var(--text-3);
}

.scan-panel {
  padding: 16px 18px;
  margin-bottom: 16px;
}

.scan-head {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}

.scan-head .ic {
  font-size: 20px;
  line-height: 1.2;
}

.scan-head .t1 {
  font-size: 14px;
  font-weight: 600;
}

.scan-head .t2 {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-2);
  line-height: 1.7;
}

.scan-input {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.scan-input .who {
  width: 130px;
  flex-shrink: 0;
}

.scan-input .who-chip {
  font-size: 12.5px;
  color: var(--text-2);
  background: #f2f3f5;
  border-radius: 999px;
  padding: 5px 12px;
  white-space: nowrap;
}

.diff-lines {
  margin-top: 6px;
}

.diff-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  line-height: 2;
  flex-wrap: wrap;
}

.diff-line .code {
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--text-1);
  cursor: pointer;
}

.diff-line .code:hover {
  color: var(--primary);
}

.diff-line .tag {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 11.5px;
}

.diff-line .where,
.diff-line .note {
  color: var(--text-3);
}

.inv-table-head {
  display: flex;
  gap: 12px;
  padding: 11px 16px;
  background: #fafbfc;
  border-bottom: 1px solid var(--border);
  font-size: 12.5px;
  color: var(--text-2);
  font-weight: 500;
}

.inv-table-head .c1 {
  width: 240px;
  flex-shrink: 0;
}

.inv-table-head .c2 {
  flex: 1.2;
  min-width: 0;
}

.inv-table-head .c3 {
  width: 170px;
  flex-shrink: 0;
}

.inv-table-head .c4 {
  width: 180px;
  flex-shrink: 0;
}

.inv-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.inv-row:last-child {
  border-bottom: 0;
}

.inv-row .c1 {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  min-width: 0;
}

.type-badge.sm {
  width: 32px;
  height: 32px;
  font-size: 15px;
  border-radius: 8px;
  flex-shrink: 0;
}

.inv-row .c1 .info {
  min-width: 0;
  cursor: pointer;
}

.inv-row .c1 .info:hover .code {
  color: var(--primary);
}

.inv-row .c1 .code {
  font-size: 13.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.inv-row .c1 .model,
.inv-row .c1 .sn {
  font-size: 11.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inv-row .c2 {
  flex: 1.2;
  min-width: 0;
}

.inv-row .c2 .line {
  font-size: 12.5px;
  color: var(--text-2);
  line-height: 1.9;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.drift {
  font-size: 11.5px;
  color: #a15c00;
}

.mini-status {
  font-size: 11.5px;
  padding: 0 7px;
  border-radius: 999px;
}

.inv-row .c3 {
  width: 170px;
  flex-shrink: 0;
}

.result-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 12px;
}

.inv-row .c3 .meta {
  font-size: 11.5px;
  color: var(--text-3);
  line-height: 1.7;
}

.inv-row .c3 .note {
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--text-2);
  word-break: break-word;
}

.inv-row .c4 {
  width: 180px;
  flex-shrink: 0;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.inv-row .c4 .meta {
  font-size: 12px;
  color: var(--text-3);
}

.empty-note {
  padding: 32px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}

@media (max-width: 900px) {
  .inv-table-head {
    display: none;
  }

  .inv-row {
    flex-wrap: wrap;
  }

  .inv-row .c1,
  .inv-row .c2,
  .inv-row .c3,
  .inv-row .c4 {
    width: 100%;
    flex: none;
  }

  .inv-row .c4 {
    gap: 8px;
  }

  .inv-hero {
    flex-wrap: wrap;
  }

  .progress-ring {
    text-align: left;
  }
}
</style>
