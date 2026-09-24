<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { assetApi, metaApi } from '@/api'
import { assetDetailUrl, loadSystemInfo, qrImageSrc } from '@/stores/app'
import type { Asset, DeviceType } from '@/types'
import { orDash, statusMeta, typeIcon } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const allAssets = ref<Asset[]>([])
const selectedIds = ref<number[]>([])
const search = ref('')

/**
 * 补打标签时最烦的是"在一百多台里一台台翻"，所以除了搜索框，
 * 再给两个能一眼缩范围的筛选：类型、存放位置。
 * 数据全量在前端（page_size=1000），筛选纯前端做，不用打接口。
 *
 * ⚠️ 候选值和已选值必须用不同的 ref 名字。
 * 曾经把候选取名叫 `locations`，又在 loadMeta 里写 `locations.value = res.locations` ——
 * 顺手就把 14 个候选位置全变成了"已选中"，界面一打开位置筛选就是全选状态，
 * 而且因为"全选所有位置"恰好等价于不筛，数量断言还看不出来。
 */
const deviceTypes = ref<DeviceType[]>([])
const locationOptions = ref<string[]>([])
const typeIds = ref<number[]>([])
const locations = ref<string[]>([])

const hasFilter = computed(
  () => Boolean(search.value.trim()) || typeIds.value.length > 0 || locations.value.length > 0,
)

const columns = ref(3)
const labelSize = ref<'sm' | 'md' | 'lg'>('md')
const showSn = ref(true)
const showUser = ref(true)
const showLocation = ref(true)
const showStatus = ref(true)

const DEFAULT_SELECT = 30

const SIZE_MAP = {
  sm: { qr: 88, code: 13, line: 10.5, pad: 8 },
  md: { qr: 120, code: 15, line: 11.5, pad: 10 },
  lg: { qr: 164, code: 17, line: 12.5, pad: 12 },
} as const

const filteredAssets = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return allAssets.value.filter((a) => {
    if (typeIds.value.length && !typeIds.value.includes(a.device_type_id)) return false
    if (locations.value.length && !(a.location && locations.value.includes(a.location))) return false
    if (!kw) return true
    return [a.asset_code, a.serial_number, a.brand, a.model, a.device_type_name, a.user_name, a.location]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(kw))
  })
})

function clearFilters() {
  search.value = ''
  typeIds.value = []
  locations.value = []
}

const selectedAssets = computed(() => {
  const order = new Map(selectedIds.value.map((id, index) => [id, index]))
  return allAssets.value
    .filter((a) => order.has(a.id))
    .sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0))
})

const sheetStyle = computed(() => ({
  gridTemplateColumns: `repeat(${columns.value}, minmax(0, 1fr))`,
}))

const sizeConf = computed(() => SIZE_MAP[labelSize.value])

function isChecked(id: number): boolean {
  return selectedIds.value.includes(id)
}

function toggle(id: number) {
  if (isChecked(id)) selectedIds.value = selectedIds.value.filter((x) => x !== id)
  else selectedIds.value = [...selectedIds.value, id]
}

function selectAllVisible() {
  const ids = new Set(selectedIds.value)
  filteredAssets.value.forEach((a) => ids.add(a.id))
  selectedIds.value = [...ids]
}

function clearAll() {
  selectedIds.value = []
}

function invertVisible() {
  const visible = new Set(filteredAssets.value.map((a) => a.id))
  const kept = selectedIds.value.filter((id) => !visible.has(id))
  const added = filteredAssets.value.filter((a) => !isChecked(a.id)).map((a) => a.id)
  selectedIds.value = [...kept, ...added]
}

async function loadMeta() {
  try {
    const res = await metaApi.filters()
    deviceTypes.value = res.device_types
    locationOptions.value = res.locations
  } catch {
    /* 筛选候选拿不到不影响打标签，静默失败即可 */
  }
}

async function load() {
  loading.value = true
  try {
    const res = await assetApi.list({ page: 1, page_size: 1000 })
    allAssets.value = res.items
    applyRouteSelection()
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function applyRouteSelection() {
  const raw = route.query.ids
  const text = Array.isArray(raw) ? raw.join(',') : (raw ?? '')
  if (text) {
    const ids = String(text)
      .split(',')
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isFinite(n) && allAssets.value.some((a) => a.id === n))
    selectedIds.value = ids
  } else if (!selectedIds.value.length) {
    selectedIds.value = allAssets.value.slice(0, DEFAULT_SELECT).map((a) => a.id)
  }
}

function qrFor(asset: Asset, px: number): string {
  return qrImageSrc(assetDetailUrl(asset.id), px)
}

function doPrint() {
  if (!selectedAssets.value.length) {
    ElMessage.warning('先选几台设备再打印')
    return
  }
  window.print()
}

function goBack() {
  void router.push('/')
}

onMounted(async () => {
  await Promise.all([loadSystemInfo(), loadMeta()])
  await load()
})

watch(
  () => route.query.ids,
  () => applyRouteSelection(),
)
</script>

<template>
  <div>
    <div class="page-head no-print">
      <div>
        <h1>二维码标签打印</h1>
        <p class="sub">
          每张标签上的二维码都指向该设备的详情页，贴到机器上，手机一扫就能查到。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="goBack">返回台账</el-button>
        <el-button type="primary" @click="doPrint">打印（{{ selectedAssets.length }} 张）</el-button>
      </div>
    </div>

    <div class="labels-layout">
      <!-- 选择面板 -->
      <aside class="panel picker no-print">
        <div class="picker-head">
          <el-input v-model="search" placeholder="搜索编号 / SN / 使用人" clearable size="small" />
          <div class="picker-filters">
            <el-select
              v-model="typeIds"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="1"
              placeholder="全部类型"
              clearable
              size="small"
            >
              <el-option
                v-for="t in deviceTypes"
                :key="t.id"
                :label="`${t.name}（${t.asset_count}）`"
                :value="t.id"
              />
            </el-select>
            <el-select
              v-model="locations"
              multiple
              collapse-tags
              collapse-tags-tooltip
              :max-collapse-tags="1"
              placeholder="全部位置"
              clearable
              filterable
              size="small"
            >
              <el-option v-for="l in locationOptions" :key="l" :label="l" :value="l" />
            </el-select>
          </div>
          <div v-if="hasFilter" class="picker-filter-tip">
            筛出 {{ filteredAssets.length }} 台
            <a @click="clearFilters">清空筛选</a>
          </div>
          <div class="picker-tools">
            <el-button size="small" @click="selectAllVisible">全选（{{ filteredAssets.length }}）</el-button>
            <el-button size="small" @click="invertVisible">反选</el-button>
            <el-button size="small" @click="clearAll">清空</el-button>
          </div>
        </div>
        <div v-loading="loading" class="picker-list">
          <div
            v-for="a in filteredAssets"
            :key="a.id"
            class="picker-item"
            @click="toggle(a.id)"
          >
            <el-checkbox :model-value="isChecked(a.id)" @click.stop @change="toggle(a.id)" />
            <span class="type-badge" style="width: 28px; height: 28px; font-size: 14px">
              {{ typeIcon(a.device_type_name) }}
            </span>
            <div class="info">
              <div class="c1">{{ a.asset_code }}</div>
              <div class="c2">{{ a.device_type_name }} · {{ orDash(a.user_name) }}</div>
            </div>
          </div>
          <div v-if="!loading && !filteredAssets.length" class="label-empty">没有匹配的设备</div>
        </div>
      </aside>

      <!-- 预览 + 打印区 -->
      <div>
        <div class="label-toolbar no-print">
          <div class="field">
            每行
            <el-radio-group v-model="columns" size="small">
              <el-radio-button :value="1">1</el-radio-button>
              <el-radio-button :value="2">2</el-radio-button>
              <el-radio-button :value="3">3</el-radio-button>
              <el-radio-button :value="4">4</el-radio-button>
            </el-radio-group>
          </div>

          <div class="sep" />

          <div class="field">
            大小
            <el-radio-group v-model="labelSize" size="small">
              <el-radio-button value="sm">小</el-radio-button>
              <el-radio-button value="md">中</el-radio-button>
              <el-radio-button value="lg">大</el-radio-button>
            </el-radio-group>
          </div>

          <div class="sep" />

          <el-checkbox v-model="showStatus" size="small">状态</el-checkbox>
          <el-checkbox v-model="showSn" size="small">SN</el-checkbox>
          <el-checkbox v-model="showUser" size="small">使用人</el-checkbox>
          <el-checkbox v-model="showLocation" size="small">位置</el-checkbox>
        </div>

        <el-alert
          v-if="selectedAssets.length > 60"
          class="no-print"
          type="info"
          :closable="false"
          show-icon
          :title="`已选 ${selectedAssets.length} 张标签，一次打印这么多会影响速度，建议分批打。`"
          style="margin-bottom: 12px"
        />

        <div v-loading="loading" class="label-sheet" :style="sheetStyle">
          <div v-if="!selectedAssets.length" class="label-empty">
            左侧勾选设备后，这里会生成对应的二维码标签
          </div>

          <div
            v-for="a in selectedAssets"
            :key="a.id"
            class="label-card"
            :style="{ padding: `${sizeConf.pad}px` }"
          >
            <div class="qr">
              <img
                :src="qrFor(a, sizeConf.qr * 2)"
                :width="sizeConf.qr"
                :height="sizeConf.qr"
                :alt="a.asset_code"
              />
            </div>
            <div class="body">
              <div class="brand-line">IT 资产</div>
              <div class="code" :style="{ fontSize: `${sizeConf.code}px` }">{{ a.asset_code }}</div>
              <div class="line strong" :style="{ fontSize: `${sizeConf.line}px` }">
                {{ a.device_type_name }}
                <template v-if="a.brand || a.model"> · {{ orDash(a.brand) }} {{ a.model || '' }}</template>
              </div>
              <div v-if="showSn" class="line" :style="{ fontSize: `${sizeConf.line}px` }">
                SN {{ a.serial_number || '未登记' }}
              </div>
              <div v-if="showUser" class="line" :style="{ fontSize: `${sizeConf.line}px` }">
                {{ a.user_name || '未分配' }}
              </div>
              <div v-if="showLocation" class="line" :style="{ fontSize: `${sizeConf.line}px` }">
                {{ a.location || '位置未登记' }}
              </div>
              <div
                v-if="showStatus"
                class="line"
                :style="{ fontSize: `${sizeConf.line}px`, color: statusMeta(a.status).color }"
              >
                {{ a.status_label }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
