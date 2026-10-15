<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AssetFormDialog from '@/components/AssetFormDialog.vue'
import AssetOperationDialog from '@/components/AssetOperationDialog.vue'
import AssetRelationsPanel from '@/components/AssetRelationsPanel.vue'
import AssetTimeline from '@/components/AssetTimeline.vue'
import InventoryContextBar from '@/components/InventoryContextBar.vue'
import { assetApi, metaApi, workstationApi } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState, assetDetailUrl, loadSystemInfo, qrImageSrc } from '@/stores/app'
import type { Asset, AssetOperation, FilterOptions, Workstation } from '@/types'
import {
  copyText,
  downloadImage,
  formatDate,
  orDash,
  operationMeta,
  statusMeta,
  typeIcon,
  userLabel,
  warrantyText,
} from '@/utils/format'

const props = defineProps<{ id: string }>()
const router = useRouter()
/** 「返回」的目标由 URL 上的 `back` 决定 —— 从台账筛着筛着点进来的，退出去筛选还在 */
const { backTarget, withBack, goBack } = useBackNav('/')

/**
 * 返回按钮的文案跟着来路变。
 * 从配对页 / 审计页点进来的，返回的并不是台账 —— 那时候还写「返回资产台账」就是在骗人。
 */
const backText = computed(() => {
  const p = backTarget.value
  if (p === '/' || p.startsWith('/?')) return '← 返回资产台账'
  if (p.startsWith('/pairings')) return '← 返回配对管理'
  if (p.startsWith('/inventory')) return '← 返回盘点列表'
  if (p.startsWith('/audit')) return '← 返回审计日志'
  if (p.startsWith('/labels')) return '← 返回标签打印'
  if (p.startsWith('/asset/')) return '← 返回上一台设备'
  return '← 返回'
})

const loading = ref(true)
const notFound = ref(false)
const asset = ref<Asset | null>(null)
const meta = ref<FilterOptions | null>(null)
/**
 * 这台设备所在点位（`assets.workstation_id` 指向的那一格）。
 *
 * **为什么详情页也要显示它**：点位是后加的一层，早期详情页里看不到，
 * 只能进「编辑」才看得见 —— 同一份数据在"看"的界面缺失、只在"改"的界面出现，
 * 用户会以为它是个可选字段，而它其实是台账的空间维度（地图就是按它摆的）。
 *
 * 拿到的是**同一个** `GET /api/workstations/{id}`，与地图页用的是同一份数据 ——
 * 详情页不做任何本地拼装，免得两处口径漂（本项目在"两套数据两处存"上吃过亏）。
 */
const station = ref<Workstation | null>(null)
const formOpen = ref(false)
const opOpen = ref(false)
const initialAction = ref<AssetOperation | null>(null)
/** 详情页里做了任何操作后 +1，让时间线重新拉数据 */
const refreshKey = ref(0)

const assetId = computed(() => Number(props.id))

const detailUrl = computed(() => (asset.value ? assetDetailUrl(asset.value.id) : ''))
const qrSrc = computed(() => (detailUrl.value ? qrImageSrc(detailUrl.value, 440) : ''))

const warranty = computed(() =>
  asset.value ? warrantyText(asset.value) : { text: '—', tone: 'none' as const },
)

const warrantyColor = computed(() => {
  switch (warranty.value.tone) {
    case 'expired':
      return '#d54941'
    case 'warn':
      return '#e37318'
    case 'ok':
      return '#0f7b3f'
    default:
      return 'var(--text-1)'
  }
})

async function load() {
  loading.value = true
  notFound.value = false
  try {
    asset.value = await assetApi.detail(assetId.value)
    await loadStation()
  } catch {
    asset.value = null
    notFound.value = true
  } finally {
    loading.value = false
  }
}

/**
 * 拉点位详情（只为显示"这格归谁"）。
 *
 * ⚠️ **失败不报错、不阻塞**：点位可能刚被删掉（`workstation_id` 还留着旧值），
 * 那时详情页照常显示资产本身，只是点位那一行退回「已删除的点位」——
 * 为了一个附属信息把整页打成错误页，是主次颠倒。
 */
async function loadStation() {
  const id = asset.value?.workstation_id
  if (!id) {
    station.value = null
    return
  }
  try {
    station.value = await workstationApi.detail(id)
  } catch {
    station.value = null
  }
}

/** 跳到空间地图并选中这一格（`?station=<code>` 深链，与问题中心「去核对」同一个落点） */
function openStation() {
  const code = asset.value?.workstation_code
  if (!code) return
  void router.push(`/map?station=${encodeURIComponent(code)}`)
}

async function loadMeta() {
  try {
    meta.value = await metaApi.filters()
  } catch {
    /* 忽略：表单下拉留空也能用 */
  }
}

onMounted(async () => {
  await Promise.all([loadSystemInfo(), loadMeta()])
  await load()
})

watch(
  () => props.id,
  () => {
    void load()
  },
)

async function copyLink() {
  const ok = await copyText(detailUrl.value)
  if (ok) ElMessage.success('详情页链接已复制')
  else ElMessage.warning('复制失败，请手动选择链接复制')
}

function downloadQr() {
  if (!asset.value) return
  downloadImage(qrImageSrc(detailUrl.value, 800), `${asset.value.asset_code}-二维码.png`)
}

function printLabel() {
  if (!asset.value) return
  void router.push(`/labels?ids=${asset.value.id}`)
}

async function removeAsset() {
  if (!asset.value) return
  const code = asset.value.asset_code
  try {
    await ElMessageBox.confirm(`确定删除资产「${code}」？删除后不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await assetApi.remove(assetId.value, appState.operator)
  ElMessage.success('已删除')
  await router.push('/')
}

function openOperation(action: AssetOperation | null) {
  initialAction.value = action
  opOpen.value = true
}

async function onSaved(updated: Asset) {
  asset.value = updated
  refreshKey.value += 1
  await loadMeta()
  // 编辑里可能改了「所在点位」—— 点位那一行要跟着变，否则界面显示的还是旧格子
  await loadStation()
}

async function onOperated(updated: Asset) {
  asset.value = updated
  refreshKey.value += 1
  await loadMeta()
}

async function onRelationsChanged() {
  await load()
  refreshKey.value += 1
}
</script>

<template>
  <div v-loading="loading">
    <div style="margin-bottom: 12px">
      <el-button link @click="goBack">{{ backText }}</el-button>
    </div>

    <div v-if="notFound && !loading" class="empty-state">
      <div class="big">🔍</div>
      <div class="title">没有找到这台设备</div>
      <div>它可能已经被删除了，或者链接不对。</div>
      <div style="margin-top: 16px">
        <el-button type="primary" @click="goBack">返回列表</el-button>
      </div>
    </div>

    <template v-else-if="asset">
      <!-- 手机扫码进详情页时，如果这轮盘点正盯着这台设备，这里会直接给出标记按钮 -->
      <InventoryContextBar
        :asset-id="asset.id"
        :asset-code="asset.asset_code"
        :refresh-key="refreshKey"
        @marked="refreshKey += 1"
      />

      <div class="detail-hero">
        <div class="type-badge">{{ typeIcon(asset.device_type_name) }}</div>
        <div style="flex: 1; min-width: 0">
          <div class="code">
            {{ asset.asset_code }}
            <span
              class="status-pill"
              :style="{
                color: statusMeta(asset.status).color,
                background: statusMeta(asset.status).bg,
                borderColor: statusMeta(asset.status).border,
              }"
            >
              {{ asset.status_label }}
            </span>
            <span v-if="asset.monitor_count" class="status-pill linked">
              🖥 {{ asset.monitor_count }} 台显示器
            </span>
            <span v-else-if="asset.host_code" class="status-pill linked">
              🔗 挂在 {{ asset.host_code }}
            </span>
          </div>
          <p class="sub">
            {{ asset.device_type_name }} · {{ orDash(asset.brand) }}
            {{ asset.model || '' }}
            <span v-if="!asset.brand && !asset.model">· 品牌型号未登记</span>
          </p>
        </div>
        <div class="head-actions" style="align-items: flex-start">
          <el-dropdown
            v-if="asset.actions.length"
            trigger="click"
            @command="(a: AssetOperation) => openOperation(a)"
          >
            <el-button type="primary">
              状态操作 ▾
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-for="a in asset.actions" :key="a" :command="a">
                  {{ operationMeta(a).icon }} {{ operationMeta(a).label }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button @click="printLabel">打印标签</el-button>
          <el-button @click="formOpen = true">编辑</el-button>
          <el-button @click="removeAsset">删除</el-button>
        </div>
      </div>

      <div class="detail-wrap">
        <div>
          <section class="panel">
            <div class="section-title">基本信息</div>
            <div class="field-list">
              <div class="item">
                <div class="k">IT 资产编号</div>
                <div class="v mono">{{ asset.asset_code }}</div>
              </div>
              <div class="item">
                <div class="k">财务编码</div>
                <div class="v mono">{{ orDash(asset.finance_code) }}</div>
              </div>
              <div class="item">
                <div class="k">设备类型</div>
                <div class="v">{{ asset.device_type_name }}</div>
              </div>
              <div class="item">
                <div class="k">状态</div>
                <div class="v">{{ asset.status_label }}</div>
              </div>
              <div class="item">
                <div class="k">品牌</div>
                <div class="v">{{ orDash(asset.brand) }}</div>
              </div>
              <div class="item">
                <div class="k">型号</div>
                <div class="v">{{ orDash(asset.model) }}</div>
              </div>
              <div class="item">
                <div class="k">序列号 SN</div>
                <div class="v mono">{{ orDash(asset.serial_number) }}</div>
              </div>
              <div class="item">
                <div class="k">使用人</div>
                <div class="v">{{ orDash(asset.user_name) }}</div>
              </div>
            </div>
          </section>

          <AssetRelationsPanel
            :asset="asset"
            style="margin-top: 16px"
            @changed="onRelationsChanged"
          />

          <section class="panel" style="margin-top: 16px">
            <div class="section-title">归属与维保</div>
            <div class="field-list">
              <!--
                所在点位 —— 台账的空间维度，与地图上那一格是**同一条数据**（assets.workstation_id）。
                以前只在「编辑」窗口里能看见/能改，详情页看不到，容易被当成可选字段。
                点编码跳空间地图并选中这一格（`?station=<code>`，与问题中心「去核对」同一个落点）。
              -->
              <div class="item">
                <div class="k">所在点位</div>
                <div class="v">
                  <a v-if="asset.workstation_code" class="ws-link" @click="openStation">
                    {{ asset.workstation_code }}
                  </a>
                  <span v-else class="ws-none">未挂点位</span>
                </div>
                <div class="ws-hint">
                  <template v-if="asset.workstation_code">
                    地图上的格子，点它去地图看这一格的位置与邻居<template v-if="station">
                      · 点位使用人 {{ userLabel(station.user_name) }}</template
                    >
                  </template>
                  <template v-else>
                    这台设备还没有落在任何一个点位上（地图上看不到它）。在「编辑」里选「所在点位」即可挂靠
                  </template>
                </div>
              </div>
              <div class="item">
                <div class="k">存放位置</div>
                <div class="v">{{ orDash(asset.location) }}</div>
              </div>
              <div class="item">
                <div class="k">购入日期</div>
                <div class="v">{{ formatDate(asset.purchase_date) }}</div>
              </div>
              <div class="item">
                <div class="k">保修到期日</div>
                <div class="v">{{ formatDate(asset.warranty_until) }}</div>
              </div>
              <div class="item">
                <div class="k">保修状态</div>
                <div class="v" :style="{ color: warrantyColor }">{{ warranty.text }}</div>
              </div>
              <div class="item full">
                <div class="k">备注</div>
                <div class="v">{{ asset.notes || '—' }}</div>
              </div>
            </div>
          </section>

          <section class="panel" style="margin-top: 16px">
            <div class="section-title">
              <span>变更历史</span>
              <span class="section-hint">录入 / 状态流转 / 配对 / 盘点 都会记在这里</span>
            </div>
            <AssetTimeline :asset-id="asset.id" :refresh-key="refreshKey" />
          </section>

          <section class="panel" style="margin-top: 16px">
            <div class="section-title">记录</div>
            <div class="field-list">
              <div class="item">
                <div class="k">录入时间</div>
                <div class="v">{{ formatDate(asset.created_at?.slice(0, 10)) }}</div>
              </div>
              <div class="item">
                <div class="k">最后修改</div>
                <div class="v">{{ formatDate(asset.updated_at?.slice(0, 10)) }}</div>
              </div>
            </div>
          </section>
        </div>

        <aside class="panel qr-panel">
          <div class="qr-box">
            <img :src="qrSrc" :alt="`${asset.asset_code} 二维码`" />
          </div>
          <p class="hint">
            手机连上<b>同一个 Wi-Fi</b>，用相机或微信扫这个码，<br />就能直接打开这台设备的详情页。
          </p>
          <div class="url-box">{{ detailUrl }}</div>
          <div class="qr-actions">
            <el-button size="small" @click="copyLink">复制链接</el-button>
            <el-button size="small" @click="downloadQr">下载二维码</el-button>
            <el-button size="small" type="primary" @click="printLabel">打印标签</el-button>
          </div>
        </aside>
      </div>

      <AssetFormDialog
        v-model="formOpen"
        :asset="asset"
        :device-types="meta?.device_types ?? []"
        :users="meta?.users ?? []"
        :locations="meta?.locations ?? []"
        @saved="onSaved"
      />

      <AssetOperationDialog
        v-model="opOpen"
        :asset="asset"
        :users="meta?.users ?? []"
        :locations="meta?.locations ?? []"
        :initial-action="initialAction"
        @done="onOperated"
      />
    </template>
  </div>
</template>

<style scoped>
.status-pill.linked {
  color: #7a4bd0;
  background: #f2ecff;
  border-color: #dcc9ff;
}

.section-hint {
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-3);
}

/* 所在点位：编码做成可点的跳转（去地图定位这一格） */
.ws-link {
  color: var(--brand, #2563eb);
  font-weight: 600;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.ws-link:hover { text-decoration: underline; }
.ws-none { color: var(--text-3); }
/* 一行小字把「点位」与「存放位置」的区别说清 —— 两个字段都在这一块里，
   不说清用户会把点位当成存放位置的重复字段。 */
.ws-hint {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-3);
}
</style>
