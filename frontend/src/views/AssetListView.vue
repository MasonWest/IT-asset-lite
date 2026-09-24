<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { LocationQuery } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AssetFormDialog from '@/components/AssetFormDialog.vue'
import DeviceTypeDialog from '@/components/DeviceTypeDialog.vue'
import { assetApi, downloadUrl, metaApi, transferApi } from '@/api'
import { appState, loadSystemInfo } from '@/stores/app'
import type { Asset, AssetGroup, AssetQuery, FilterOptions } from '@/types'
import { formatDate, orDash, statusMeta, typeIcon } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
/**
 * 列表**永远**渲染成「行」。
 *
 * 明细模式把每台设备包成一个只有它自己的 single 行，聚合模式直接吃后端的分组结果 ——
 * 这样模板和筛选/选中/标签/分页逻辑只写一套，不会出现"卡片视图支持了聚合、
 * 表格视图忘了改"这种慢慢长歪的情况。
 */
const groups = ref<AssetGroup[]>([])
const total = ref(0)
/** 聚合视图专用：命中筛选的明细台数 / 展开后的总台数 */
const matchedTotal = ref(0)
const expandedTotal = ref(0)

const viewMode = ref<'card' | 'table'>('card')

const GROUPED_KEY = 'it-asset.grouped-view'

function readGroupedPref(): boolean {
  try {
    return localStorage.getItem(GROUPED_KEY) === '1'
  } catch {
    return false
  }
}

/** 套装聚合展示：默认关，勾了之后拉后端的分组接口。用户偏好记在浏览器里 */
const groupedMode = ref(readGroupedPref())

watch(groupedMode, (on) => {
  try {
    localStorage.setItem(GROUPED_KEY, on ? '1' : '0')
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略 */
  }
  page.value = 1
  selected.value = []
  void load()
})

/**
 * 筛选条件。
 *
 * 类型 / 位置 / 使用人都是**多选**（数组），拼查询参数时 join(',') 丢给后端 ——
 * 后端那三个条件本来就收逗号分隔的多值，单值只是"只含一个元素的列表"，
 * 所以这个改动不用动接口，导出的查询串也自动跟着对。
 *
 * 「状态」故意保持单选：上面那排统计卡点一下就切状态，卡片是单选语义，
 * 做成多选会和卡片的表现对不上。
 */
const filters = reactive<{
  keyword: string
  device_type_ids: number[]
  status?: string
  user_names: string[]
  locations: string[]
  paired?: 'paired' | 'unpaired'
}>({
  keyword: '',
  device_type_ids: [],
  status: undefined,
  user_names: [],
  locations: [],
  paired: undefined,
})

const page = ref(1)
const pageSize = ref(24)

/**
 * URL query ↔ 界面状态。
 *
 * 意义在于「点进设备详情再返回，筛选还在」：路由是懒加载、没有 keep-alive，
 * 列表组件一卸载，所有 ref 就没了。把状态挂在 URL 上，返回时重新挂载就能原样恢复，
 * 顺带白拿三件事 —— 刷新不丢、能直接分享「带着筛选的链接」、浏览器前进后退也对。
 *
 * 同步方向是**单向**的（界面 → URL）：只负责写出去，不监听 URL 回灌，免得两边互相
 * 触发打成一团。挂载时读一次就够，因为列表页自己的 URL 永远是自己最后一次写的那份。
 */
const hydrated = ref(false)

function readStateFromQuery(q: LocationQuery) {
  const one = (v: unknown) => (typeof v === 'string' ? v : '')
  const many = (v: unknown) =>
    one(v)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)

  filters.keyword = one(q.keyword)
  filters.device_type_ids = many(q.device_type_id)
    .map(Number)
    .filter((n) => Number.isInteger(n))
  filters.status = one(q.status) || undefined
  const paired = one(q.paired)
  filters.paired = paired === 'paired' || paired === 'unpaired' ? paired : undefined
  filters.user_names = many(q.user_name)
  filters.locations = many(q.location)
  viewMode.value = one(q.view) === 'table' ? 'table' : 'card'
  const p = Number(one(q.page))
  page.value = Number.isInteger(p) && p > 0 ? p : 1
}

/** 界面状态 → URL query。key 刻意和接口 / 导出用的查询参数同名，肉眼就能核对 */
function currentUrlQuery(): Record<string, string> {
  const q: Record<string, string> = {}
  for (const [k, v] of Object.entries(currentQuery())) {
    if (v !== undefined && v !== null && String(v) !== '') q[k] = String(v)
  }
  if (viewMode.value !== 'card') q.view = viewMode.value
  if (page.value > 1) q.page = String(page.value)
  return q
}

/**
 * 用 replace 而不是 push —— push 的话每敲一个字、每翻一页都会往浏览器历史里塞一条，
 * 用户点几次「返回」还在列表页里打转，比丢筛选更烦人。
 */
function syncUrl() {
  if (!hydrated.value) return
  void router.replace({ path: '/', query: currentUrlQuery() })
}

/** 当前列表的地址（含筛选 / 页码），跳详情时交给对方当返回目标 */
function backPath(): string {
  return router.resolve({ path: '/', query: currentUrlQuery() }).fullPath
}

const meta = ref<FilterOptions | null>(null)

const formOpen = ref(false)
const editing = ref<Asset | null>(null)
const presetTypeId = ref<number | null>(null)
const typeDialogOpen = ref(false)

const selected = ref<AssetGroup[]>([])

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
  if (!total.value) return groupedMode.value ? '共 0 行' : '共 0 台设备'
  const from = (page.value - 1) * pageSize.value + 1
  const to = Math.min(page.value * pageSize.value, total.value)
  if (groupedMode.value) {
    return `共 ${total.value} 行 · 覆盖 ${expandedTotal.value} 台设备，当前显示 ${from}-${to}`
  }
  return `共 ${total.value} 台设备，当前显示 ${from}-${to}`
})

/**
 * 聚合视图下被「整组带出来」的配套设备台数。
 *
 * 筛选命中的是设备明细，但套装是按整机展示的：筛「主机」时，那台主机的显示器也会
 * 跟着出现（否则会显示成「未配显示器」，是错误信息）。两者不相等就要跟用户说清楚。
 */
const carriedAlong = computed(() => Math.max(0, expandedTotal.value - matchedTotal.value))

const hasFilter = computed(
  () =>
    Boolean(filters.keyword.trim()) ||
    filters.device_type_ids.length > 0 ||
    filters.status !== undefined ||
    filters.user_names.length > 0 ||
    filters.locations.length > 0 ||
    filters.paired !== undefined,
)

/** 选中行展开成「设备明细 id」—— 打印标签永远按明细走，套装只是列表的一种看法 */
const selectedAssetIds = computed(() => {
  const ids = new Set<number>()
  for (const g of selected.value) {
    for (const id of g.asset_ids) ids.add(id)
  }
  return [...ids]
})

/** 明细模式下把一台设备包成「只有它自己的一行」 */
function assetToGroup(a: Asset): AssetGroup {
  return {
    group_key: `single-${a.id}`,
    kind: 'single',
    primary: a,
    extra_hosts: [],
    monitors: [],
    monitor_count: a.monitor_count,
    asset_ids: [a.id],
    asset_count: 1,
    sort_id: a.id,
    flags: { multi_host: false, multi_monitor: false, status_mismatch: false },
  }
}

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
    if (groupedMode.value) {
      const res = await assetApi.grouped(query)
      groups.value = res.items
      total.value = res.total
      matchedTotal.value = res.matched_total
      expandedTotal.value = res.expanded_total
    } else {
      const res = await assetApi.list(query)
      groups.value = res.items.map(assetToGroup)
      total.value = res.total
      // 明细口径下"命中"和"覆盖"本来就是同一批设备
      matchedTotal.value = res.total
      expandedTotal.value = res.total
    }
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
    if (!hydrated.value) return
    if (keywordTimer) clearTimeout(keywordTimer)
    keywordTimer = setTimeout(() => {
      page.value = 1
      syncUrl()
      void load()
    }, 320)
  },
)

onBeforeUnmount(() => {
  if (keywordTimer) clearTimeout(keywordTimer)
})

/**
 * 非关键字的筛选条件变了就重查。
 *
 * getter 最后 join 成一个字符串再返回 —— 返回数组字面量的话，数组每次都是新引用，
 * watch 会认为"变了"从而多打一次接口。返回字符串才是按内容比较。
 */
watch(
  () =>
    [
      filters.device_type_ids.join('|'),
      filters.user_names.join('|'),
      filters.locations.join('|'),
      filters.status ?? '',
      filters.paired ?? '',
    ].join('~'),
  () => {
    if (!hydrated.value) return
    page.value = 1
    syncUrl()
    void load()
  },
)

/** 翻页和切视图不改查询，但同样得进 URL —— 否则返回后又回到第 1 页 / 卡片视图 */
watch(page, () => syncUrl())
watch(viewMode, () => syncUrl())

function pickStatus(key: string) {
  filters.status = key === '' ? undefined : key
}

function resetFilters() {
  filters.keyword = ''
  filters.device_type_ids = []
  filters.status = undefined
  filters.user_names = []
  filters.locations = []
  filters.paired = undefined
  page.value = 1
  syncUrl()
  void load()
}

function openCreate() {
  editing.value = null
  // 只在「恰好筛了一个类型」时把类型带进新增表单 —— 筛了好几个就不知道
  // 用户想新建哪一种了，带错比不带更烦人
  presetTypeId.value = filters.device_type_ids.length === 1 ? filters.device_type_ids[0] : null
  formOpen.value = true
}

function openEdit(asset: Asset) {
  editing.value = asset
  presetTypeId.value = null
  formOpen.value = true
}

function openDetail(asset: Asset) {
  // 把当前列表地址（含筛选 / 页码）一起带过去，详情页的「返回」才能原样回来
  void router.push({ path: `/asset/${asset.id}`, query: { back: backPath() } })
}

/** 表格行点击 → 打开这一行的门面设备（套装就是主机） */
function onRowClick(row: AssetGroup) {
  openDetail(row.primary)
}

function rowKeyOf(row: AssetGroup): string {
  return row.group_key
}

/** 打印某一行对应的标签。套装行会展开成成员设备 —— 出的是 N 张，不是 1 张 */
function labelOne(g: AssetGroup) {
  void router.push({ path: '/labels', query: { ids: g.asset_ids.join(','), back: backPath() } })
}

function goLabels() {
  const ids = selectedAssetIds.value
  void router.push({
    path: '/labels',
    query: ids.length ? { ids: ids.join(','), back: backPath() } : { back: backPath() },
  })
}

function goImport() {
  void router.push({ path: '/import', query: { back: backPath() } })
}

async function onSaved(asset: Asset, mode: 'create' | 'update') {
  await refreshAll()
  if (mode === 'create') {
    await router.push({ path: `/asset/${asset.id}`, query: { back: backPath() } })
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

function onSelectionChange(rows: AssetGroup[]) {
  selected.value = rows
}

function statusStyle(a: Asset) {
  const m = statusMeta(a.status)
  return { color: m.color, background: m.bg, borderColor: m.border }
}

/** 这一行整体算不算"有配对信息"——用来决定配对那行要不要压暗 */
function hasPairing(g: AssetGroup): boolean {
  return g.kind === 'bundle' || Boolean(g.primary.monitor_count || g.primary.host_code)
}

/** 列表里一句话说清配对状态：套装挂了几台 / 显示器挂在谁身上 */
function pairingText(g: AssetGroup): string {
  if (g.kind === 'bundle') {
    return `挂着 ${g.monitor_count} 台显示器`
  }
  const a = g.primary
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
  // 多选值 join 成逗号串 —— 后端三个条件都按多值解析
  if (filters.device_type_ids.length) query.device_type_id = filters.device_type_ids.join(',')
  if (filters.status) query.status = filters.status
  if (filters.user_names.length) query.user_name = filters.user_names.join(',')
  if (filters.locations.length) query.location = filters.locations.join(',')
  if (filters.paired) query.paired = filters.paired
  return query
}

/**
 * 导出交给后端：列定义和「批量导入模板」共用同一份 ASSET_COLUMNS，
 * 才能保证「导出 → 改一改 → 再导入」这条闭环不会因为列名对不上而断掉。
 *
 * 注意 `currentQuery()` 里**没有**聚合开关 —— 导出永远出设备明细，
 * 和列表当前是哪种视图无关。聚合行在导出的表格里没有任何对应结构。
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
  // 先按 URL 还原上一次的筛选 / 页码 / 视图（从详情页返回时走的就是这条路）
  readStateFromQuery(route.query)
  // watch 是 flush:'pre'（在微任务里跑），等它们都跑完再开闸门 —— 否则「恢复状态」
  // 这个动作本身会被当成用户在操作，白打一轮接口
  await nextTick()
  hydrated.value = true
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
        <el-button @click="goImport">批量导入</el-button>
        <el-button type="primary" @click="openCreate">+ 新增资产</el-button>
      </div>
    </div>

    <!-- 状态统计：永远按设备明细算，点一下就是筛选（不受聚合视图影响） -->
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
      <el-select
        v-model="filters.device_type_ids"
        multiple
        collapse-tags
        collapse-tags-tooltip
        :max-collapse-tags="1"
        placeholder="全部类型"
        clearable
        style="width: 200px"
      >
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
      <el-select
        v-model="filters.user_names"
        multiple
        collapse-tags
        collapse-tags-tooltip
        :max-collapse-tags="1"
        placeholder="全部使用人"
        clearable
        filterable
        style="width: 180px"
      >
        <el-option v-for="u in meta?.users ?? []" :key="u" :label="u" :value="u" />
      </el-select>
      <el-select
        v-model="filters.locations"
        multiple
        collapse-tags
        collapse-tags-tooltip
        :max-collapse-tags="1"
        placeholder="全部位置"
        clearable
        filterable
        style="width: 210px"
      >
        <el-option v-for="l in meta?.locations ?? []" :key="l" :label="l" :value="l" />
      </el-select>
      <el-button v-if="hasFilter" @click="resetFilters">重置</el-button>

      <div class="spacer" />

      <el-tooltip placement="bottom" :show-after="200" :hide-after="0">
        <template #content>
          <div style="max-width: 320px; line-height: 1.7">
            把「主机 + 它挂着的显示器」合并成一行展示。<br />
            底层设备一台都没变：编号、状态、保修、使用人都各自独立，配对关系也不动。<br />
            筛选命中的是设备明细，展示时整组一起出现。<br />
            <b>导出和打印标签永远按设备明细走，不受这个开关影响。</b>
          </div>
        </template>
        <label class="group-toggle">
          <el-switch v-model="groupedMode" size="small" />
          <span>套装聚合展示</span>
        </label>
      </el-tooltip>

      <div class="view-toggle">
        <button :class="{ on: viewMode === 'card' }" @click="viewMode = 'card'">卡片</button>
        <button :class="{ on: viewMode === 'table' }" @click="viewMode = 'table'">表格</button>
      </div>
    </div>

    <!-- 聚合视图：说明"多出来的那几台"是怎么来的 -->
    <div v-if="groupedMode && carriedAlong" class="carry-tip">
      当前筛选命中 <b>{{ matchedTotal }}</b> 台设备。套装视图按整机展示，
      额外带出了 <b>{{ carriedAlong }}</b> 台配套设备，本页共覆盖
      <b>{{ expandedTotal }}</b> 台。
    </div>

    <!-- 卡片视图 -->
    <div v-if="viewMode === 'card'" v-loading="loading" class="asset-grid">
      <article
        v-for="g in groups"
        :key="g.group_key"
        class="asset-card"
        :class="{ 'is-bundle': g.kind === 'bundle' }"
        @click="openDetail(g.primary)"
      >
        <div class="top">
          <div class="type-badge">
            {{ g.kind === 'bundle' ? '🧩' : typeIcon(g.primary.device_type_name) }}
          </div>
          <div class="id-block">
            <div class="code">
              {{ g.primary.asset_code }}
              <span v-if="g.kind === 'bundle'" class="bundle-tag">
                套装 {{ g.asset_count }} 台
              </span>
            </div>
            <div class="model">
              {{ orDash(g.primary.brand) }} {{ g.primary.model || '' }}
              <span v-if="!g.primary.brand && !g.primary.model">未填写品牌型号</span>
            </div>
          </div>
          <span class="status-pill" :style="statusStyle(g.primary)">
            {{ g.primary.status_label }}
          </span>
        </div>

        <div class="meta">
          <div class="row">
            <span class="ic">👤</span>
            <span class="val">{{ orDash(g.primary.user_name) }}</span>
          </div>
          <div class="row">
            <span class="ic">📍</span>
            <span class="val">{{ orDash(g.primary.location) }}</span>
          </div>
          <div class="row">
            <span class="ic">🏷️</span>
            <span class="sn-chip">{{ g.primary.serial_number || 'SN 未登记' }}</span>
          </div>
          <div class="row">
            <span class="ic">🛡️</span>
            <span class="val">
              保修 {{ g.primary.warranty_until ? formatDate(g.primary.warranty_until) : '未登记' }}
            </span>
          </div>
          <div class="row">
            <span class="ic">🔗</span>
            <span class="val" :class="{ muted: !hasPairing(g) }">{{ pairingText(g) }}</span>
            <!-- 表格视图的状态列也有同一个提示，两种视图口径必须一致 -->
            <el-tooltip
              v-if="g.flags.status_mismatch"
              placement="top"
              content="套装内设备状态不一致（主机与显示器状态不同），仅提示，不会自动改状态"
            >
              <span class="warn-dot">⚠️</span>
            </el-tooltip>
          </div>
        </div>

        <!-- 套装成员：点一下就进那台设备自己的详情 -->
        <div v-if="g.kind === 'bundle'" class="bundle-members" @click.stop>
          <span
            v-for="m in g.monitors"
            :key="m.id"
            class="member-chip"
            :class="{ off: m.status !== g.primary.status }"
            :title="`${m.device_type_name} · ${m.status_label} · ${orDash(m.location)}`"
            @click="openDetail(m)"
          >
            🖥️ {{ m.asset_code }}
            <em v-if="m.status !== g.primary.status">{{ m.status_label }}</em>
          </span>
          <span v-if="!g.monitors.length" class="member-chip muted">这一组暂无显示器成员</span>
        </div>

        <div class="foot" @click.stop>
          <el-button size="small" @click="openDetail(g.primary)">详情</el-button>
          <el-button size="small" @click="openEdit(g.primary)">编辑</el-button>
          <el-button size="small" @click="labelOne(g)">
            标签{{ g.asset_count > 1 ? `（${g.asset_count} 张）` : '' }}
          </el-button>
          <div class="spacer" />
          <el-button size="small" link @click="removeAsset(g.primary)">删除</el-button>
        </div>
      </article>
    </div>

    <!-- 表格视图 -->
    <div v-else class="panel" style="padding: 4px 4px 0">
      <el-table
        v-loading="loading"
        :data="groups"
        :row-key="rowKeyOf"
        @selection-change="onSelectionChange"
        @row-click="onRowClick"
      >
        <!-- 展开看套装里每台显示器的明细。只在聚合视图下有意义 -->
        <el-table-column v-if="groupedMode" type="expand" width="40">
          <template #default="{ row }">
            <div class="bundle-panel">
              <div class="bundle-panel-head">
                <b>{{ row.primary.asset_code }}</b>
                <span>{{ row.primary.device_type_name }}</span>
                <span class="muted">
                  {{ orDash(row.primary.brand) }} {{ row.primary.model || '' }} ·
                  {{ orDash(row.primary.serial_number) }}
                </span>
                <span class="muted">挂 {{ row.monitor_count }} 台显示器</span>
              </div>
              <div
                v-for="m in row.monitors"
                :key="m.id"
                class="bundle-panel-row"
                @click.stop="openDetail(m)"
              >
                <span class="code">{{ m.asset_code }}</span>
                <span>{{ m.device_type_name }}</span>
                <span>{{ orDash(m.brand) }} {{ m.model || '' }}</span>
                <span class="sn">{{ orDash(m.serial_number) }}</span>
                <el-tag size="small" effect="light" :style="statusStyle(m)">
                  {{ m.status_label }}
                </el-tag>
                <span class="loc">{{ orDash(m.location) }}</span>
                <el-button link size="small" @click.stop="openDetail(m)">详情</el-button>
              </div>
              <div v-if="!row.monitors.length" class="bundle-panel-row muted">没有显示器成员</div>
            </div>
          </template>
        </el-table-column>

        <el-table-column type="selection" width="42" />
        <el-table-column label="IT 资产编号" min-width="190" sortable prop="primary.asset_code">
          <template #default="{ row }">
            <span class="code-cell">{{ row.primary.asset_code }}</span>
            <span v-if="row.kind === 'bundle'" class="bundle-tag">套装 {{ row.asset_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="96">
          <template #default="{ row }">{{ row.primary.device_type_name }}</template>
        </el-table-column>
        <el-table-column label="品牌 / 型号" min-width="170">
          <template #default="{ row }">
            {{ orDash(row.primary.brand) }} {{ row.primary.model || '' }}
          </template>
        </el-table-column>
        <el-table-column label="序列号 SN" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ orDash(row.primary.serial_number) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :style="statusStyle(row.primary)">
              {{ row.primary.status_label }}
            </el-tag>
            <el-tooltip
              v-if="row.flags.status_mismatch"
              placement="top"
              content="套装内设备状态不一致（主机与显示器状态不同），仅提示，不会自动改状态"
            >
              <span class="warn-dot">⚠️</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="使用人" width="100">
          <template #default="{ row }">{{ orDash(row.primary.user_name) }}</template>
        </el-table-column>
        <el-table-column label="配对" width="170">
          <template #default="{ row }">
            <span :style="{ color: hasPairing(row) ? '#7a4bd0' : 'var(--text-3)' }">
              {{ pairingText(row) }}
            </span>
            <el-tag v-if="row.flags.multi_monitor" size="small" effect="plain" type="warning">
              多显示器
            </el-tag>
            <el-tag v-if="row.flags.multi_host" size="small" effect="plain" type="danger">
              多主机
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="存放位置" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">{{ orDash(row.primary.location) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" @click.stop="openDetail(row.primary)">详情</el-button>
            <el-button link size="small" @click.stop="openEdit(row.primary)">编辑</el-button>
            <el-button link size="small" @click.stop="removeAsset(row.primary)">删除</el-button>
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
        <span style="font-size: 13px; color: var(--text-2)">
          已选 {{ selected.length }} 行 · 共 {{ selectedAssetIds.length }} 台设备
        </span>
        <el-button size="small" type="primary" @click="goLabels">
          打印选中标签（{{ selectedAssetIds.length }} 张）
        </el-button>
        <el-button size="small" @click="selected = []">清空选择</el-button>
      </div>
    </div>

    <div v-if="!loading && !groups.length" class="empty-state" style="margin-top: 16px">
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
        导出的列与「批量导入」的模板完全一致，可以直接改完再导回来。<br />
        每台设备各占一行 —— 与列表的套装聚合视图无关。
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

/* 聚合开关：放在视图切换左边，视觉权重别抢过筛选框 */
.group-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
}

.carry-tip {
  margin: 0 0 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f2f6ff;
  border: 1px solid #d6e2ff;
  color: #33507e;
  font-size: 13px;
  line-height: 1.6;
}

/* 「套装 N 台」小标 —— 卡片和表格共用 */
.bundle-tag {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 999px;
  background: #eef1ff;
  border: 1px solid #d3daff;
  color: #4a5bd0;
  font-size: 11px;
  font-weight: 400;
  line-height: 18px;
  white-space: nowrap;
}

/* 套装卡片：左侧一道彩条，扫一眼就能和普通明细行区分 */
.asset-card.is-bundle {
  border-left: 3px solid #7a4bd0;
}

.bundle-members {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 14px 10px;
}

.member-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f4f5f7;
  border: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
}

.member-chip:hover {
  border-color: #7a4bd0;
  color: #7a4bd0;
}

.member-chip.muted {
  cursor: default;
  color: var(--text-3);
}

/* 成员状态和主机不一致时标红，但不阻断任何操作 */
.member-chip.off {
  background: #fff6f0;
  border-color: #ffd9c2;
  color: #b25b16;
}

.member-chip em {
  font-style: normal;
  font-size: 11px;
  opacity: 0.85;
}

.warn-dot {
  margin-left: 4px;
  cursor: help;
  font-size: 12px;
}

.code-cell {
  cursor: pointer;
  color: var(--text-1);
}

/* 展开的套装明细 */
.bundle-panel {
  padding: 8px 12px 12px 24px;
  background: #fafbfc;
}

.bundle-panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-bottom: 8px;
  font-size: 13px;
  color: var(--text-2);
}

.bundle-panel-head .muted,
.bundle-panel-row.muted {
  color: var(--text-3);
}

.bundle-panel-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-2);
  cursor: pointer;
}

.bundle-panel-row:hover {
  background: #f0f3f8;
}

.bundle-panel-row .code {
  min-width: 170px;
  color: var(--text-1);
}

.bundle-panel-row .sn {
  color: var(--text-3);
}

.bundle-panel-row .loc {
  margin-left: auto;
  color: var(--text-3);
}

@media (max-width: 640px) {
  .bundle-panel {
    padding-left: 10px;
  }

  .bundle-panel-row {
    flex-wrap: wrap;
    gap: 6px;
  }

  .bundle-panel-row .code {
    min-width: 0;
  }

  .bundle-panel-row .loc {
    margin-left: 0;
  }
}
</style>
