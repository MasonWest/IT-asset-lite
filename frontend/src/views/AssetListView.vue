<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AssetFormDialog from '@/components/AssetFormDialog.vue'
import DeviceTypeDialog from '@/components/DeviceTypeDialog.vue'
import { assetApi, downloadUrl, metaApi, transferApi } from '@/api'
import { appState, loadSystemInfo } from '@/stores/app'
import type { Asset, AssetQuery, FilterOptions } from '@/types'
import { formatDate, orDash, statusMeta, typeIcon } from '@/utils/format'

const router = useRouter()

const loading = ref(false)
const items = ref<Asset[]>([])
const total = ref(0)
const viewMode = ref<'card' | 'table'>('card')

const filters = reactive<{
  keyword: string
  device_type_id?: number
  status?: string
  user_name?: string
  location?: string
  paired?: 'paired' | 'unpaired'
}>({
  keyword: '',
  device_type_id: undefined,
  status: undefined,
  user_name: undefined,
  location: undefined,
  paired: undefined,
})

const page = ref(1)
const pageSize = ref(24)

const meta = ref<FilterOptions | null>(null)

const formOpen = ref(false)
const editing = ref<Asset | null>(null)
const presetTypeId = ref<number | null>(null)
const typeDialogOpen = ref(false)

const selected = ref<Asset[]>([])

const statusCards = [
  { key: '', label: '全部设备', icon: '📦' },
  { key: 'in_use', label: '在用', icon: '✅' },
  { key: 'idle', label: '在库', icon: '🗄️' },
  { key: 'repair', label: '维修中', icon: '🔧' },
  { key: 'scrapped', label: '已报废', icon: '🗑️' },
]

const pairedOptions = [
  { value: 'paired' as const, label: '已配对' },
  { value: 'unpaired' as const, label: '未配对' },
]

const statusOptions = computed(
  () => meta.value?.statuses ?? [
    { value: 'in_use' as const, label: '在用' },
    { value: 'idle' as const, label: '闲置' },
    { value: 'repair' as const, label: '维修中' },
    { value: 'scrapped' as const, label: '已报废' },
  ],
)

const rangeText = computed(() => {
  if (!total.value) return '共 0 台设备'
  const from = (page.value - 1) * pageSize.value + 1
  const to = Math.min(page.value * pageSize.value, total.value)
  return `共 ${total.value} 台设备，当前显示 ${from}-${to}`
})

const hasFilter = computed(
  () =>
    Boolean(filters.keyword.trim()) ||
    filters.device_type_id !== undefined ||
    filters.status !== undefined ||
    filters.user_name !== undefined ||
    filters.location !== undefined ||
    filters.paired !== undefined,
)

async function loadMeta() {
  meta.value = await metaApi.filters()
}

async function load() {
  loading.value = true
  try {
    const query: AssetQuery = {
      ...currentQuery(),
      page: page.value,
      page_size: pageSize.value,
    }
    const res = await assetApi.list(query)
    items.value = res.items
    total.value = res.total
  } catch {
    /* 错误提示已由拦截器统一处理 */
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await Promise.all([loadMeta(), load()])
}

/** 关键字输入做防抖，避免每敲一个字都打一次接口 */
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

onBeforeUnmount(() => {
  if (keywordTimer) clearTimeout(keywordTimer)
})

watch(
  () => [filters.device_type_id, filters.status, filters.user_name, filters.location, filters.paired],
  () => {
    page.value = 1
    void load()
  },
)

function pickStatus(key: string) {
  filters.status = key === '' ? undefined : key
}

function resetFilters() {
  filters.keyword = ''
  filters.device_type_id = undefined
  filters.status = undefined
  filters.user_name = undefined
  filters.location = undefined
  filters.paired = undefined
  page.value = 1
  void load()
}

function openCreate() {
  editing.value = null
  presetTypeId.value = filters.device_type_id ?? null
  formOpen.value = true
}

function openEdit(asset: Asset) {
  editing.value = asset
  presetTypeId.value = null
  formOpen.value = true
}

function openDetail(asset: Asset) {
  void router.push(`/asset/${asset.id}`)
}

function goLabels() {
  if (selected.value.length) {
    void router.push(`/labels?ids=${selected.value.map((a) => a.id).join(',')}`)
  } else {
    void router.push('/labels')
  }
}

async function onSaved(asset: Asset, mode: 'create' | 'update') {
  await refreshAll()
  if (mode === 'create') {
    await router.push(`/asset/${asset.id}`)
  }
}

async function removeAsset(asset: Asset) {
  try {
    await ElMessageBox.confirm(
      `确定删除资产「${asset.asset_code}」？删除后不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await assetApi.remove(asset.id, appState.operator)
  ElMessage.success('已删除')
  await refreshAll()
}

function onSelectionChange(rows: Asset[]) {
  selected.value = rows
}

/** 列表里一句话说清配对状态：主机挂了几台 / 显示器挂在谁身上 */
function pairingText(a: Asset): string {
  if (a.device_category === 'host') {
    return a.monitor_count ? `挂了 ${a.monitor_count} 台显示器` : '未配显示器'
  }
  if (a.device_category === 'display') {
    return a.host_code ? `挂在 ${a.host_code}` : '未挂机'
  }
  return '不参与配对'
}

function currentQuery(): AssetQuery {
  const query: AssetQuery = {}
  const kw = filters.keyword.trim()
  if (kw) query.keyword = kw
  if (filters.device_type_id !== undefined) query.device_type_id = filters.device_type_id
  if (filters.status) query.status = filters.status
  if (filters.user_name) query.user_name = filters.user_name
  if (filters.location) query.location = filters.location
  if (filters.paired) query.paired = filters.paired
  return query
}

/**
 * 导出交给后端：列定义和「批量导入模板」共用同一份 ASSET_COLUMNS，
 * 才能保证「导出 → 改一改 → 再导入」这条闭环不会因为列名对不上而断掉。
 */
const exportOpen = ref(false)
const exportScope = ref<'filtered' | 'all'>('filtered')
const exportFmt = ref<'xlsx' | 'csv'>('xlsx')
const exportWithPairing = ref(true)

function openExport() {
  exportScope.value = hasFilter.value ? 'filtered' : 'all'
  exportOpen.value = true
}

function runExport() {
  const url = transferApi.exportUrl(currentQuery(), {
    scope: exportScope.value,
    fmt: exportFmt.value,
    withPairing: exportWithPairing.value,
    operator: appState.operator,
  })
  downloadUrl(url)
  exportOpen.value = false
  ElMessage.success(
    exportScope.value === 'filtered' ? '正在导出当前筛选结果' : '正在导出全部资产',
  )
}

onMounted(async () => {
  await loadSystemInfo()
  await refreshAll()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>IT 资产台账</h1>
        <p class="sub">{{ rangeText }} · 扫码即可查看任意设备的详情</p>
      </div>
      <div class="head-actions">
        <el-button @click="typeDialogOpen = true">设备类型</el-button>
        <el-button @click="goLabels">打印标签</el-button>
        <el-button @click="openExport">导出</el-button>
        <el-button @click="$router.push('/import')">批量导入</el-button>
        <el-button type="primary" @click="openCreate">+ 新增资产</el-button>
      </div>
    </div>

    <!-- 状态统计，点一下就是筛选 -->
    <div class="stat-row">
      <div
        v-for="c in statusCards"
        :key="c.key"
        class="stat-card"
        :class="{ active: (filters.status ?? '') === c.key }"
        @click="pickStatus(c.key)"
      >
        <div class="k"><span>{{ c.icon }}</span>{{ c.label }}</div>
        <div class="v">
          {{ c.key === '' ? (meta?.total ?? 0) : (meta?.status_counts?.[c.key] ?? 0) }}
          <small>台</small>
        </div>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input
        v-model="filters.keyword"
        class="grow"
        placeholder="搜索编号 / 品牌 / 型号 / SN / 使用人 / 位置"
        clearable
      />
      <el-select v-model="filters.device_type_id" placeholder="全部类型" clearable>
        <el-option
          v-for="t in meta?.device_types ?? []"
          :key="t.id"
          :label="`${t.name}（${t.asset_count}）`"
          :value="t.id"
        />
      </el-select>
      <el-select v-model="filters.status" placeholder="全部状态" clearable>
        <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-select v-model="filters.paired" placeholder="配对：全部" clearable style="width: 130px">
        <el-option v-for="p in pairedOptions" :key="p.value" :label="p.label" :value="p.value" />
      </el-select>
      <el-select v-model="filters.user_name" placeholder="全部使用人" clearable filterable>
        <el-option v-for="u in meta?.users ?? []" :key="u" :label="u" :value="u" />
      </el-select>
      <el-select v-model="filters.location" placeholder="全部位置" clearable filterable>
        <el-option v-for="l in meta?.locations ?? []" :key="l" :label="l" :value="l" />
      </el-select>
      <el-button v-if="hasFilter" @click="resetFilters">重置</el-button>

      <div class="spacer" />

      <div class="view-toggle">
        <button :class="{ on: viewMode === 'card' }" @click="viewMode = 'card'">卡片</button>
        <button :class="{ on: viewMode === 'table' }" @click="viewMode = 'table'">表格</button>
      </div>
    </div>

    <!-- 卡片视图 -->
    <div v-if="viewMode === 'card'" v-loading="loading" class="asset-grid">
      <article
        v-for="a in items"
        :key="a.id"
        class="asset-card"
        @click="openDetail(a)"
      >
        <div class="top">
          <div class="type-badge">{{ typeIcon(a.device_type_name) }}</div>
          <div class="id-block">
            <div class="code">{{ a.asset_code }}</div>
            <div class="model">
              {{ orDash(a.brand) }} {{ a.model || '' }}
              <span v-if="!a.brand && !a.model">未填写品牌型号</span>
            </div>
          </div>
          <span
            class="status-pill"
            :style="{
              color: statusMeta(a.status).color,
              background: statusMeta(a.status).bg,
              borderColor: statusMeta(a.status).border,
            }"
          >
            {{ a.status_label }}
          </span>
        </div>

        <div class="meta">
          <div class="row">
            <span class="ic">👤</span>
            <span class="val">{{ orDash(a.user_name) }}</span>
          </div>
          <div class="row">
            <span class="ic">📍</span>
            <span class="val">{{ orDash(a.location) }}</span>
          </div>
          <div class="row">
            <span class="ic">🏷️</span>
            <span class="sn-chip">{{ a.serial_number || 'SN 未登记' }}</span>
          </div>
          <div class="row">
            <span class="ic">🛡️</span>
            <span class="val">
              保修 {{ a.warranty_until ? formatDate(a.warranty_until) : '未登记' }}
            </span>
          </div>
          <div class="row">
            <span class="ic">🔗</span>
            <span class="val" :class="{ muted: !a.monitor_count && !a.host_code }">
              {{ pairingText(a) }}
            </span>
          </div>
        </div>

        <div class="foot" @click.stop>
          <el-button size="small" @click="openDetail(a)">详情</el-button>
          <el-button size="small" @click="openEdit(a)">编辑</el-button>
          <el-button size="small" @click="router.push(`/labels?ids=${a.id}`)">标签</el-button>
          <div class="spacer" />
          <el-button size="small" link @click="removeAsset(a)">删除</el-button>
        </div>
      </article>
    </div>

    <!-- 表格视图 -->
    <div v-else class="panel" style="padding: 4px 4px 0">
      <el-table
        v-loading="loading"
        :data="items"
        row-key="id"
        @selection-change="onSelectionChange"
        @row-click="openDetail"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column label="IT 资产编号" prop="asset_code" min-width="140" sortable />
        <el-table-column label="类型" prop="device_type_name" width="92" />
        <el-table-column label="品牌 / 型号" min-width="180">
          <template #default="{ row }">
            {{ orDash(row.brand) }} {{ row.model || '' }}
          </template>
        </el-table-column>
        <el-table-column label="序列号 SN" prop="serial_number" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ orDash(row.serial_number) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              :style="{
                color: statusMeta(row.status).color,
                background: statusMeta(row.status).bg,
                borderColor: statusMeta(row.status).border,
              }"
            >
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="使用人" width="100">
          <template #default="{ row }">{{ orDash(row.user_name) }}</template>
        </el-table-column>
        <el-table-column label="配对" width="150">
          <template #default="{ row }">
            <span :style="{ color: row.monitor_count || row.host_code ? '#7a4bd0' : 'var(--text-3)' }">
              {{ pairingText(row) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="存放位置" prop="location" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ orDash(row.location) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" @click.stop="openDetail(row)">详情</el-button>
            <el-button link size="small" @click.stop="openEdit(row)">编辑</el-button>
            <el-button link size="small" @click.stop="removeAsset(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div
        v-if="selected.length"
        style="
          padding: 10px 12px;
          display: flex;
          align-items: center;
          gap: 10px;
          border-top: 1px solid var(--border);
        "
      >
        <span style="font-size: 13px; color: var(--text-2)">已选 {{ selected.length }} 台</span>
        <el-button size="small" type="primary" @click="goLabels">打印选中标签</el-button>
        <el-button size="small" @click="selected = []">清空选择</el-button>
      </div>
    </div>

    <div v-if="!loading && !items.length" class="empty-state" style="margin-top: 16px">
      <div class="big">📦</div>
      <div class="title">{{ hasFilter ? '没有符合条件的设备' : '还没有录入任何设备' }}</div>
      <div>
        {{ hasFilter ? '换个筛选条件试试，或者点右上角「重置」' : '点右上角「+ 新增资产」开始建立台账' }}
      </div>
      <div style="margin-top: 16px">
        <el-button v-if="hasFilter" @click="resetFilters">重置筛选</el-button>
        <el-button v-else type="primary" @click="openCreate">+ 新增资产</el-button>
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

    <AssetFormDialog
      v-model="formOpen"
      :asset="editing"
      :device-types="meta?.device_types ?? []"
      :users="meta?.users ?? []"
      :locations="meta?.locations ?? []"
      :preset-type-id="presetTypeId"
      @saved="onSaved"
    />

    <DeviceTypeDialog
      v-model="typeDialogOpen"
      :device-types="meta?.device_types ?? []"
      @changed="refreshAll"
    />

    <el-dialog v-model="exportOpen" title="导出资产" width="min(480px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="导出范围">
          <el-radio-group v-model="exportScope">
            <el-radio-button value="filtered" :disabled="!hasFilter">
              当前筛选结果
            </el-radio-button>
            <el-radio-button value="all">全部资产（{{ meta?.total ?? 0 }} 台）</el-radio-button>
          </el-radio-group>
          <div v-if="!hasFilter" style="font-size: 12px; color: var(--text-3); margin-top: 6px">
            当前没有设置筛选条件，导出的就是全部资产。
          </div>
        </el-form-item>

        <el-form-item label="文件格式">
          <el-radio-group v-model="exportFmt">
            <el-radio-button value="xlsx">Excel（.xlsx）</el-radio-button>
            <el-radio-button value="csv">CSV（.csv）</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="附加内容">
          <el-switch v-model="exportWithPairing" />
          <span style="margin-left: 10px; font-size: 13px; color: var(--text-2)">
            附带配对的显示器信息
          </span>
        </el-form-item>
      </el-form>

      <div class="readonly-note" style="margin-top: 0">
        导出的列与「批量导入」的模板完全一致，可以直接改完再导回来。
      </div>

      <template #footer>
        <el-button @click="exportOpen = false">取消</el-button>
        <el-button type="primary" @click="runExport">导出</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 没有配对关系时压暗一点，避免「不参与配对」这种信息抢视线 */
.asset-card .meta .row .val.muted {
  color: var(--text-3);
}
</style>
