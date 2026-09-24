<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { auditApi, downloadUrl } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState } from '@/stores/app'
import type { AuditLog, AuditMeta, AuditQuery } from '@/types'
import { auditVisual, formatDateTime, relativeTime, toIsoDate } from '@/utils/format'

const router = useRouter()
/** 从审计点进设备 / 盘点任务，返回时要回到审计页（带着当时的筛选） */
const { withBack } = useBackNav('/audit')

const loading = ref(false)
const items = ref<AuditLog[]>([])
const total = ref(0)
const meta = ref<AuditMeta | null>(null)
const expanded = ref<number[]>([])

const DEFAULT_DAYS = 7

const filters = reactive<{
  range: [string, string] | null
  action: string[]
  operator?: string
  ip?: string
  status?: 'success' | 'failed'
  keyword: string
}>({
  range: null,
  action: [],
  operator: undefined,
  ip: undefined,
  status: undefined,
  keyword: '',
})

const page = ref(1)
const pageSize = ref(30)

function defaultRange(): [string, string] {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - (DEFAULT_DAYS - 1))
  return [toIsoDate(start), toIsoDate(end)]
}

filters.range = defaultRange()

const rangeText = computed(() => {
  if (!total.value) return '共 0 条记录'
  const from = (page.value - 1) * pageSize.value + 1
  const to = Math.min(page.value * pageSize.value, total.value)
  return `共 ${total.value} 条记录，当前显示 ${from}-${to}`
})

/** 操作类型 -> 中文名，用于把选中的多选项显示成人话 */
const actionLabelMap = computed(() => {
  const map: Record<string, string> = {}
  for (const group of meta.value?.groups ?? []) {
    for (const option of group.options) map[option.value] = option.label
  }
  return map
})

function buildQuery(): AuditQuery {
  const query: AuditQuery = { page: page.value, page_size: pageSize.value }
  if (filters.range && filters.range[0] && filters.range[1]) {
    query.start = filters.range[0]
    query.end = filters.range[1]
  }
  if (filters.action.length) query.action = filters.action.join(',')
  if (filters.operator) query.operator = filters.operator
  if (filters.ip) query.ip = filters.ip
  if (filters.status) query.status = filters.status
  const kw = filters.keyword.trim()
  if (kw) query.keyword = kw
  return query
}

function hasFilter(): boolean {
  return Boolean(
    filters.action.length ||
      filters.operator ||
      filters.ip ||
      filters.status ||
      filters.keyword.trim(),
  )
}

async function load() {
  loading.value = true
  try {
    const res = await auditApi.list(buildQuery())
    items.value = res.items
    total.value = res.total
  } catch {
    /* 拦截器已统一提示 */
  } finally {
    loading.value = false
  }
}

async function loadMeta() {
  try {
    meta.value = await auditApi.meta()
  } catch {
    /* 忽略 */
  }
}

async function refresh() {
  await Promise.all([loadMeta(), load()])
}

let keywordTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => filters.keyword,
  () => {
    if (keywordTimer) clearTimeout(keywordTimer)
    keywordTimer = setTimeout(() => {
      page.value = 1
      void load()
    }, 320)
  },
)

watch(
  () => [filters.range, filters.action, filters.operator, filters.ip, filters.status],
  () => {
    page.value = 1
    void load()
  },
  { deep: true },
)

function resetFilters() {
  filters.range = defaultRange()
  filters.action = []
  filters.operator = undefined
  filters.ip = undefined
  filters.status = undefined
  filters.keyword = ''
  page.value = 1
  void load()
}

/** 点统计卡直接筛选 */
function quickToday() {
  const today = toIsoDate(new Date())
  filters.range = [today, today]
}

function quickFailed() {
  filters.status = filters.status === 'failed' ? undefined : 'failed'
}

function toggleDetail(id: number) {
  const idx = expanded.value.indexOf(id)
  if (idx >= 0) expanded.value.splice(idx, 1)
  else expanded.value.push(id)
}

/** 审计对象的可跳转目标：设备去详情页，盘点任务去盘点详情 */
function targetLink(entry: AuditLog): string | null {
  if (entry.target_id === null) return null
  if (entry.target_type === 'asset') return `/asset/${entry.target_id}`
  if (entry.target_type === 'inventory') return `/inventory/${entry.target_id}`
  return null
}

function openTarget(entry: AuditLog) {
  const link = targetLink(entry)
  if (link) void router.push({ path: link, query: withBack() })
}

function doExport(fmt: 'xlsx' | 'csv') {
  if (!total.value) {
    ElMessage.warning('当前筛选条件下没有记录可导出')
    return
  }
  downloadUrl(auditApi.exportUrl(buildQuery(), fmt, appState.operator))
  // 导出本身也是一条审计，稍后刷新就能看到，这里不抢在请求前刷新
  setTimeout(() => void refresh(), 1200)
  ElMessage.success(`正在导出 ${total.value} 条记录（${fmt.toUpperCase()}）`)
}

function exportAll(fmt: 'xlsx' | 'csv') {
  downloadUrl(auditApi.exportUrl({}, fmt, appState.operator))
  setTimeout(() => void refresh(), 1200)
  ElMessage.success(`正在导出全部日志（${fmt.toUpperCase()}）`)
}

function detailRows(entry: AuditLog): { key: string; label: string; value: string }[] {
  return Object.entries(entry.detail ?? {}).map(([key, value]) => ({
    key,
    label: detailLabel(key),
    value: detailValue(key, value),
  }))
}

const DETAIL_LABELS: Record<string, string> = {
  asset_code: 'IT 资产编号',
  device_type: '设备类型',
  status: '状态',
  source: '来源',
  changes: '变更明细',
  fields: '涉及字段',
  action: '动作',
  action_label: '动作',
  from_status: '原状态',
  to_status: '新状态',
  user_name: '使用人',
  location: '存放位置',
  host_code: '主机编号',
  monitor_code: '显示器编号',
  from_host_code: '原主机',
  to_host_code: '新主机',
  relation_id: '配对记录',
  task_id: '盘点任务',
  task_name: '盘点任务',
  result: '盘点结果',
  kind: '类型',
  scope_text: '盘点范围',
  total: '应盘台数',
  checked: '已盘',
  pending: '未盘',
  abnormal: '异常',
  created: '新增',
  updated: '更新',
  unchanged: '无变化',
  failed: '失败',
  created_types: '自动创建的类型',
  mode: '模式',
  mode_label: '模式',
  auto_create_type: '自动创建类型',
  format: '格式',
  rows: '行数',
  with_pairing: '含配对信息',
  filters: '筛选条件',
  truncated: '是否截断',
  method: '请求方法',
  path: '请求路径',
  status_code: 'HTTP 状态码',
  name: '名称',
  category: '类别',
  before: '变更前',
  after_name: '变更后名称',
  auto_unbound: '自动解绑条数',
}

function detailLabel(key: string): string {
  return DETAIL_LABELS[key] ?? key
}

function detailValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  const text = String(value)
  if (key === 'status' || key === 'from_status' || key === 'to_status') {
    return { in_use: '在用', idle: '在库', repair: '维修中', scrapped: '已报废' }[text] ?? text
  }
  return text
}

onMounted(async () => {
  await refresh()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>操作审计</h1>
        <p class="sub">
          {{ rangeText }} · 只读日志，系统不提供修改或删除入口
        </p>
      </div>
      <div class="head-actions">
        <el-dropdown trigger="click" @command="doExport">
          <el-button>导出当前筛选 ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="xlsx">导出 Excel（.xlsx）</el-dropdown-item>
              <el-dropdown-item command="csv">导出 CSV（.csv）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-dropdown trigger="click" @command="exportAll">
          <el-button>导出全部 ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="xlsx">导出 Excel（.xlsx）</el-dropdown-item>
              <el-dropdown-item command="csv">导出 CSV（.csv）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button @click="refresh">刷新</el-button>
      </div>
    </div>

    <div class="stat-row">
      <div class="stat-card" :class="{ active: !hasFilter() }" @click="resetFilters">
        <div class="k"><span>📜</span>全部记录</div>
        <div class="v">{{ meta?.total ?? 0 }}<small>条</small></div>
      </div>
      <div class="stat-card" @click="quickToday">
        <div class="k"><span>📅</span>今日操作</div>
        <div class="v">{{ meta?.today ?? 0 }}<small>条</small></div>
      </div>
      <div class="stat-card" :class="{ active: filters.status === 'failed' }" @click="quickFailed">
        <div class="k"><span>⚠️</span>失败操作</div>
        <div class="v">{{ meta?.failed ?? 0 }}<small>条</small></div>
      </div>
    </div>

    <div class="filter-bar">
      <el-date-picker
        v-model="filters.range"
        type="daterange"
        unlink-panels
        range-separator="至"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        value-format="YYYY-MM-DD"
        style="width: 260px"
      />
      <el-select v-model="filters.action" multiple collapse-tags collapse-tags-tooltip
                 placeholder="全部操作类型" clearable style="width: 210px">
        <el-option-group v-for="g in meta?.groups ?? []" :key="g.name" :label="g.name">
          <el-option v-for="o in g.options" :key="o.value" :label="o.label" :value="o.value" />
        </el-option-group>
      </el-select>
      <el-select v-model="filters.operator" placeholder="全部操作人" clearable
                 filterable style="width: 150px">
        <el-option v-for="o in meta?.operators ?? []" :key="o" :label="o" :value="o" />
      </el-select>
      <el-select v-model="filters.ip" placeholder="全部 IP" clearable filterable style="width: 160px">
        <el-option v-for="i in meta?.ips ?? []" :key="i" :label="i" :value="i" />
      </el-select>
      <el-select v-model="filters.status" placeholder="全部结果" clearable style="width: 120px">
        <el-option label="成功" value="success" />
        <el-option label="失败" value="failed" />
      </el-select>
      <el-input v-model="filters.keyword" class="grow"
                placeholder="搜索操作对象 / 操作内容 / 操作人 / IP" clearable />
      <el-button v-if="hasFilter()" @click="resetFilters">重置</el-button>
    </div>

    <div v-loading="loading" class="audit-panel">
      <ol v-if="items.length" class="audit-feed">
        <li v-for="entry in items" :key="entry.id" class="audit-item" :class="{ failed: !entry.ok }">
          <div class="mark" :style="{ background: auditVisual(entry.action).bg, color: auditVisual(entry.action).color }">
            {{ auditVisual(entry.action).icon }}
          </div>
          <div class="body">
            <div class="line-1">
              <span class="action">{{ entry.action_label }}</span>
              <button
                v-if="targetLink(entry)"
                class="object link"
                @click="openTarget(entry)"
              >{{ entry.target_label || '—' }}</button>
              <span v-else class="object">{{ entry.target_label || '—' }}</span>
              <span class="spacer" />
              <span class="when" :title="formatDateTime(entry.created_at)">
                {{ relativeTime(entry.created_at) }}
              </span>
            </div>

            <div v-if="entry.summary" class="summary">{{ entry.summary }}</div>

            <div class="line-3">
              <span class="chip" :class="{ muted: !entry.operator }">
                👤 {{ entry.operator || '未填写操作人' }}
              </span>
              <span class="chip mono">🌐 {{ entry.ip || '—' }}</span>
              <span class="chip">{{ formatDateTime(entry.created_at) }}</span>
              <span v-if="!entry.ok" class="chip bad">{{ entry.status_label }}</span>
              <span class="spacer" />
              <button v-if="entry.detail" class="more" @click="toggleDetail(entry.id)">
                {{ expanded.includes(entry.id) ? '收起' : '详情' }}
              </button>
            </div>

            <dl v-if="entry.detail && expanded.includes(entry.id)" class="detail">
              <template v-for="row in detailRows(entry)" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </template>
            </dl>
          </div>
        </li>
      </ol>

      <div v-else-if="!loading" class="empty-state">
        <div class="big">📜</div>
        <div class="title">{{ hasFilter() ? '这段时间没有符合条件的操作' : '还没有任何操作记录' }}</div>
        <div>
          {{
            hasFilter()
              ? '换个时间范围或筛选项试试'
              : '新增设备、改状态、配对、盘点、导入导出都会自动记在这里'
          }}
        </div>
        <div v-if="hasFilter()" style="margin-top: 14px">
          <el-button @click="resetFilters">重置筛选</el-button>
        </div>
      </div>
    </div>

    <div v-if="total > pageSize" class="pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="prev, pager, next, jumper"
        background
        @current-change="load"
      />
    </div>

    <p class="readonly-note">
      审计日志只追加、不修改、不删除 —— 界面上没有编辑和删除入口，后端也没有对应接口。
      要长期归档请定期用 <code>backup.bat</code> 备份整个数据库文件。
      操作人由用户在右上角自己填（系统没有登录体系），留空只表示当时没设名字，不代表无人操作。
    </p>
  </div>
</template>

<style scoped>
.audit-panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 6px 18px;
  margin-top: 4px;
}

.audit-feed {
  list-style: none;
  margin: 0;
  padding: 0;
}

.audit-item {
  display: flex;
  gap: 12px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}

.audit-item:last-child {
  border-bottom: none;
}

.audit-item.failed .mark {
  box-shadow: inset 0 0 0 1px #f7c5c1;
}

.mark {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}

.body {
  flex: 1;
  min-width: 0;
}

.line-1 {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.action {
  font-weight: 600;
  color: var(--text-1);
}

.object {
  font-size: 13px;
  color: var(--text-2);
  word-break: break-all;
}

.object.link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 13px;
  color: var(--primary);
  cursor: pointer;
  text-align: left;
}

.object.link:hover {
  text-decoration: underline;
}

.spacer {
  flex: 1;
}

.when {
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}

.summary {
  margin-top: 4px;
  font-size: 13px;
  color: var(--text-1);
  line-height: 1.65;
  word-break: break-word;
}

.line-3 {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.chip {
  font-size: 12px;
  color: var(--text-2);
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 9px;
  white-space: nowrap;
}

.chip.muted {
  color: var(--text-3);
}

.chip.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.chip.bad {
  color: #d54941;
  background: #fdecea;
  border-color: #f7c5c1;
}

.more {
  border: none;
  background: none;
  color: var(--primary);
  font-size: 12px;
  cursor: pointer;
  padding: 0 2px;
}

.detail {
  margin: 10px 0 0;
  padding: 10px 12px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  display: grid;
  grid-template-columns: 108px 1fr;
  gap: 4px 12px;
  font-size: 12px;
}

.detail dt {
  color: var(--text-3);
}

.detail dd {
  margin: 0;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* .readonly-note 已提到 main.css 全局，多个页面共用 */

@media (max-width: 720px) {
  .audit-panel {
    padding: 4px 12px;
  }

  .detail {
    grid-template-columns: 1fr;
  }

  .detail dt {
    margin-top: 6px;
  }
}
</style>
