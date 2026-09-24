<script setup lang="ts">
/**
 * 盘点任务列表 + 发起盘点。
 *
 * 「发起盘点」的范围直接复用资产台账那套筛选口径（类型 / 状态 / 使用人 / 位置 / 关键字），
 * 边调条件边实时显示「这次会盘到几台」，避免手一抖把整库圈进来。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, inventoryApi, inventoryExportUrl, metaApi } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState, loadSystemInfo, saveOperator } from '@/stores/app'
import type { FilterOptions, InventoryResult, InventoryScope, InventoryTask } from '@/types'
import { formatDate, formatDateTime, orDash } from '@/utils/format'

const router = useRouter()
/** 点进盘点任务后返回，要回到这一页 */
const { withBack } = useBackNav('/inventory')

const loading = ref(false)
const tasks = ref<InventoryTask[]>([])
const meta = ref<FilterOptions | null>(null)
const tab = ref<'open' | 'closed' | 'all'>('open')

const createOpen = ref(false)
const creating = ref(false)
const previewCount = ref<number | null>(null)
const previewLoading = ref(false)
const operatorInput = ref('')

const form = reactive({
  name: '',
  note: '',
  device_type_id: undefined as number | undefined,
  statuses: [] as string[],
  user_name: '',
  location: '',
  keyword: '',
})

const statusOptions = computed(() => meta.value?.statuses ?? [])

const scope = computed<InventoryScope>(() => ({
  device_type_id: form.device_type_id ? String(form.device_type_id) : null,
  status: form.statuses.length ? form.statuses.join(',') : null,
  user_name: form.user_name || null,
  location: form.location || null,
  keyword: form.keyword.trim() || null,
}))

const scopeSummary = computed(() => {
  const parts: string[] = []
  if (form.device_type_id) {
    const t = meta.value?.device_types.find((x) => x.id === form.device_type_id)
    if (t) parts.push(`类型 ${t.name}`)
  }
  if (form.statuses.length) {
    parts.push(
      '状态 ' + form.statuses.map((s) => statusOptions.value.find((o) => o.value === s)?.label ?? s).join('/'),
    )
  }
  if (form.user_name) parts.push(`使用人 ${form.user_name}`)
  if (form.location) parts.push(`位置 ${form.location}`)
  if (form.keyword.trim()) parts.push(`关键字 ${form.keyword.trim()}`)
  return parts.length ? parts.join(' · ') : '全部设备'
})

const counts = computed(() => {
  const open = tasks.value.filter((t) => t.status === 'open').length
  const closed = tasks.value.filter((t) => t.status === 'closed').length
  const devices = tasks.value.reduce((sum, t) => sum + t.total, 0)
  const abnormal = tasks.value.reduce((sum, t) => sum + t.abnormal, 0)
  return { open, closed, devices, abnormal }
})

const shown = computed(() =>
  tasks.value.filter((t) => (tab.value === 'all' ? true : t.status === tab.value)),
)

function defaultName(): string {
  const now = new Date()
  return `${now.getFullYear()}年${now.getMonth() + 1}月资产盘点`
}

async function load() {
  loading.value = true
  try {
    const [list, filters] = await Promise.all([inventoryApi.tasks({}), metaApi.filters()])
    tasks.value = list
    meta.value = filters
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function refreshPreview() {
  previewLoading.value = true
  try {
    const res = await assetApi.list({
      keyword: scope.value.keyword ?? undefined,
      device_type_id: scope.value.device_type_id ?? undefined,
      status: scope.value.status ?? undefined,
      user_name: scope.value.user_name ?? undefined,
      location: scope.value.location ?? undefined,
      page: 1,
      page_size: 1,
    })
    previewCount.value = res.total
  } catch {
    previewCount.value = null
  } finally {
    previewLoading.value = false
  }
}

let previewTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => [form.device_type_id, form.statuses, form.user_name, form.location, form.keyword],
  () => {
    if (!createOpen.value) return
    if (previewTimer) clearTimeout(previewTimer)
    previewTimer = setTimeout(() => void refreshPreview(), 260)
  },
  { deep: true },
)

function openCreate() {
  form.name = defaultName()
  form.note = ''
  form.device_type_id = undefined
  form.statuses = []
  form.user_name = ''
  form.location = ''
  form.keyword = ''
  operatorInput.value = appState.operator
  previewCount.value = null
  createOpen.value = true
  void refreshPreview()
}

function resetScope() {
  form.device_type_id = undefined
  form.statuses = []
  form.user_name = ''
  form.location = ''
  form.keyword = ''
}

async function submitCreate() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('给这次盘点起个名字')
    return
  }
  const operator = (appState.operator || operatorInput.value).trim()
  saveOperator(operator)

  creating.value = true
  try {
    const task = await inventoryApi.create({
      name,
      note: form.note.trim() || null,
      created_by: operator || null,
      scope: scope.value,
    })
    ElMessage.success(`盘点已发起，共 ${task.total} 台设备`)
    createOpen.value = false
    await load()
    void router.push({ path: `/inventory/${task.id}`, query: withBack() })
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    creating.value = false
  }
}

function exportTask(task: InventoryTask, fmt: 'xlsx' | 'csv', onlyDiff = false) {
  const link = document.createElement('a')
  link.href = inventoryExportUrl(task.id, { fmt, onlyDiff })
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

async function closeTask(task: InventoryTask) {
  try {
    await ElMessageBox.confirm(
      `结束「${task.name}」？结束后不能再标记，但可以重新开启，导出也不受影响。`,
      '结束盘点',
      { type: 'warning', confirmButtonText: '结束盘点', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await inventoryApi.close(task.id, appState.operator || null)
    ElMessage.success('已结束')
    await load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

async function removeTask(task: InventoryTask) {
  try {
    await ElMessageBox.confirm(
      `删除盘点任务「${task.name}」？已标记的盘点结果会一起删掉，不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await inventoryApi.remove(task.id, appState.operator || null)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

async function copyScope(task: InventoryTask) {
  form.name = `${task.name}（复盘）`
  form.note = `基于「${task.name}」的范围重新盘点`
  const s = task.scope ?? {}
  form.device_type_id = s.device_type_id
    ? Number(String(s.device_type_id).split(',')[0])
    : undefined
  form.statuses = s.status ? String(s.status).split(',').filter(Boolean) : []
  form.user_name = s.user_name ?? ''
  form.location = s.location ?? ''
  form.keyword = s.keyword ?? ''
  operatorInput.value = appState.operator
  createOpen.value = true
  await refreshPreview()
}

function openTask(task: InventoryTask) {
  void router.push({ path: `/inventory/${task.id}`, query: withBack() })
}

function statusChip(task: InventoryTask) {
  return task.status === 'open'
    ? { color: '#0b6bcb', bg: '#eaf2ff', border: '#bed6ff', label: '进行中' }
    : { color: '#7a7f87', bg: '#f2f3f5', border: '#dcdee3', label: '已结束' }
}

const RESULT_LABEL: Record<InventoryResult, string> = {
  pending: '未盘',
  checked: '已盘',
  abnormal: '异常',
}

onMounted(async () => {
  await loadSystemInfo()
  await load()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>盘点</h1>
        <p class="sub">
          发起一次盘点，用手机扫设备上的二维码就能逐台标记，最后导出差异报表。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">+ 发起盘点</el-button>
      </div>
    </div>

    <div class="stat-row">
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>🔄</span>进行中</div>
        <div class="v">{{ counts.open }}<small>个</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>✅</span>已结束</div>
        <div class="v">{{ counts.closed }}<small>个</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>📦</span>累计盘点设备</div>
        <div class="v">{{ counts.devices }}<small>台次</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>⚠️</span>累计异常</div>
        <div class="v">{{ counts.abnormal }}<small>台</small></div>
      </div>
    </div>

    <div class="filter-bar">
      <div class="view-toggle">
        <button :class="{ on: tab === 'open' }" @click="tab = 'open'">进行中</button>
        <button :class="{ on: tab === 'closed' }" @click="tab = 'closed'">已结束</button>
        <button :class="{ on: tab === 'all' }" @click="tab = 'all'">全部</button>
      </div>
      <div class="spacer" />
      <span style="font-size: 13px; color: var(--text-2)">{{ shown.length }} 个任务</span>
    </div>

    <div v-loading="loading" class="task-list">
      <article v-for="t in shown" :key="t.id" class="task-card panel" @click="openTask(t)">
        <div class="head">
          <span
            class="status-pill"
            :style="{ color: statusChip(t).color, background: statusChip(t).bg, borderColor: statusChip(t).border }"
          >
            {{ statusChip(t).label }}
          </span>
          <div class="title-block">
            <div class="name">{{ t.name }}</div>
            <div class="scope">{{ t.scope_text }}</div>
          </div>
          <div class="progress-num">
            <span class="big">{{ t.progress }}<small>%</small></span>
            <span class="sub">{{ t.checked }}/{{ t.total }} 已盘</span>
          </div>
        </div>

        <div class="bar">
          <span class="fill checked" :style="{ width: `${t.total ? (t.checked / t.total) * 100 : 0}%` }" />
          <span
            class="fill abnormal"
            :style="{ width: `${t.total ? (t.abnormal / t.total) * 100 : 0}%`, left: `${t.total ? (t.checked / t.total) * 100 : 0}%` }"
          />
        </div>

        <div class="metrics">
          <span class="m"><b>{{ t.total }}</b> 应盘</span>
          <span class="m ok"><b>{{ t.checked }}</b> 已盘</span>
          <span class="m bad"><b>{{ t.abnormal }}</b> 异常</span>
          <span class="m mute"><b>{{ t.pending }}</b> 未盘</span>
        </div>

        <div class="foot" @click.stop>
          <span class="who">
            {{ t.created_by || '—' }} 发起于 {{ formatDate(t.created_at.slice(0, 10)) }}
            <template v-if="t.closed_at"> · {{ formatDateTime(t.closed_at) }} 结束</template>
          </span>
          <div class="spacer" />
          <el-button size="small" type="primary" @click="openTask(t)">
            {{ t.status === 'open' ? '去盘点' : '查看结果' }}
          </el-button>
          <el-button size="small" @click="exportTask(t, 'xlsx')">导出 Excel</el-button>
          <el-dropdown trigger="click">
            <el-button size="small">更多 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="exportTask(t, 'csv')">导出 CSV</el-dropdown-item>
                <el-dropdown-item @click="exportTask(t, 'xlsx', true)">只导出差异（Excel）</el-dropdown-item>
                <el-dropdown-item @click="exportTask(t, 'csv', true)">只导出差异（CSV）</el-dropdown-item>
                <el-dropdown-item divided @click="copyScope(t)">按这个范围再盘一次</el-dropdown-item>
                <el-dropdown-item v-if="t.status === 'open'" @click="closeTask(t)">结束盘点</el-dropdown-item>
                <el-dropdown-item divided @click="removeTask(t)">删除任务</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </article>

      <div v-if="!loading && !shown.length" class="empty-state">
        <div class="big">📋</div>
        <div class="title">{{ tab === 'open' ? '没有进行中的盘点' : '还没有盘点记录' }}</div>
        <div>点右上角「+ 发起盘点」，选好范围就能开始盘了。</div>
        <div style="margin-top: 16px">
          <el-button type="primary" @click="openCreate">+ 发起盘点</el-button>
        </div>
      </div>
    </div>

    <!-- 发起盘点 -->
    <el-dialog v-model="createOpen" title="发起盘点" width="min(680px, 94vw)" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="盘点名称" required>
          <el-input v-model="form.name" placeholder="例如 2026年9月资产盘点" clearable />
        </el-form-item>

        <div class="scope-head">
          <span>盘点范围</span>
          <el-button link size="small" @click="resetScope">重置为全部设备</el-button>
        </div>

        <div class="scope-grid">
          <el-form-item label="设备类型">
            <el-select v-model="form.device_type_id" placeholder="全部类型" clearable style="width: 100%">
              <el-option
                v-for="t in meta?.device_types ?? []"
                :key="t.id"
                :label="`${t.name}（${t.asset_count}）`"
                :value="t.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="状态">
            <el-select
              v-model="form.statuses"
              multiple
              collapse-tags
              collapse-tags-tooltip
              placeholder="全部状态"
              style="width: 100%"
            >
              <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </el-form-item>

          <el-form-item label="使用人">
            <el-select v-model="form.user_name" placeholder="全部使用人" clearable filterable style="width: 100%">
              <el-option v-for="u in meta?.users ?? []" :key="u" :label="u" :value="u" />
            </el-select>
          </el-form-item>

          <el-form-item label="存放位置">
            <el-select v-model="form.location" placeholder="全部位置" clearable filterable style="width: 100%">
              <el-option v-for="l in meta?.locations ?? []" :key="l" :label="l" :value="l" />
            </el-select>
          </el-form-item>

          <el-form-item label="关键字" class="full">
            <el-input v-model="form.keyword" placeholder="编号 / 品牌 / 型号 / SN / 备注" clearable />
          </el-form-item>

          <el-form-item label="备注" class="full">
            <el-input
              v-model="form.note"
              type="textarea"
              :rows="2"
              maxlength="500"
              placeholder="可选，例如「Q3 季度盘点，IT 部主导」"
            />
          </el-form-item>

          <el-form-item label="发起人" class="full">
            <el-input
              v-model="operatorInput"
              placeholder="你的名字"
              clearable
              @change="saveOperator(operatorInput)"
            />
          </el-form-item>
        </div>
      </el-form>

      <div class="preview-box" :class="{ warn: previewCount === 0 }">
        <template v-if="previewCount === null">
          <span>正在统计范围内的设备…</span>
        </template>
        <template v-else-if="previewCount === 0">
          <span>⚠️ 这个条件下没有设备，换个范围再发起</span>
        </template>
        <template v-else>
          <span class="num">{{ previewCount }}</span>
          <span>台设备会被圈进这次盘点</span>
          <span class="scope-text">（{{ scopeSummary }}）</span>
        </template>
      </div>

      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <el-button @click="createOpen = false">取消</el-button>
          <el-button
            type="primary"
            :loading="creating || previewLoading"
            :disabled="previewCount === 0"
            @click="submitCreate"
          >
            发起盘点
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.task-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-card {
  padding: 16px 18px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}

.task-card:hover {
  border-color: #c9d3e8;
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.task-card .head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.task-card .title-block {
  flex: 1;
  min-width: 0;
}

.task-card .name {
  font-size: 15px;
  font-weight: 600;
}

.task-card .scope {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--text-2);
}

.task-card .progress-num {
  text-align: right;
  flex-shrink: 0;
}

.task-card .progress-num .big {
  font-size: 21px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.task-card .progress-num .big small {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
}

.task-card .progress-num .sub {
  display: block;
  font-size: 11.5px;
  color: var(--text-3);
}

.task-card .bar {
  position: relative;
  height: 8px;
  margin-top: 12px;
  border-radius: 999px;
  background: #f0f1f3;
  overflow: hidden;
}

.task-card .bar .fill {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 999px;
  transition: width 0.3s;
}

.task-card .bar .fill.checked {
  left: 0;
  background: linear-gradient(90deg, #34c759, #4cd964);
}

.task-card .bar .fill.abnormal {
  background: linear-gradient(90deg, #ff7a70, #d54941);
}

.task-card .metrics {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 12.5px;
  color: var(--text-2);
}

.task-card .metrics .m b {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-right: 2px;
}

.task-card .metrics .m.ok b {
  color: #0f7b3f;
}

.task-card .metrics .m.bad b {
  color: #d54941;
}

.task-card .metrics .m.mute b {
  color: var(--text-3);
}

.task-card .foot {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}

.task-card .foot .who {
  font-size: 12px;
  color: var(--text-3);
}

.task-card .foot .spacer {
  flex: 1;
}

.scope-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 4px 0 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-1);
}

.scope-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.scope-grid .full {
  grid-column: 1 / -1;
}

.preview-box {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 11px 13px;
  border: 1px solid #bed6ff;
  background: #eaf2ff;
  border-radius: 10px;
  font-size: 13px;
  color: #0b6bcb;
}

.preview-box.warn {
  border-color: #f7c5c1;
  background: #fdecea;
  color: #d54941;
}

.preview-box .num {
  font-size: 19px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.preview-box .scope-text {
  color: var(--text-2);
  font-size: 12px;
}

@media (max-width: 640px) {
  .scope-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .task-card .foot .spacer {
    display: none;
  }
}
</style>
