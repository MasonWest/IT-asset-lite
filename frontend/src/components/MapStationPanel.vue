<script setup lang="ts">
/**
 * 点位详情侧栏。
 *
 * 从地图视图里拆出来，因为它是**唯一会写数据的地方** ——
 * 改使用人、改编码、挂/摘资产、单台盘点都在这里。
 * 把这些写操作集中在一个组件里，地图那边就只剩渲染和拖拽了。
 *
 * ## 三处必须说清的口径
 *
 * ### 1. 侧栏的资产清单是**真实资产**，不是点位自己存的列表
 * 数据来自 `GET /api/workstations/{id}/assets`，查的是 `assets.workstation_id`。
 * 点位表里**不存资产列表** —— 那会变成同一件事存两处，迟早对不上。
 *
 * ### 2. 「使用人」是点位归属，不是资产使用人的副本
 * 「W23 这个位置归林晓薇」和「这台主机现在归林晓薇」是两个不同的事实，
 * 现实中**可以不一致**，而不一致本身正是现场盘点要找的信号。
 * 所以这里**不做**"改点位使用人就顺手改资产使用人" —— 那会把差异抹掉。
 *
 * ### 3. 空位 / 不在本次范围不是「已盘」
 * 它们显示为灰色「空位」「不在本次范围」，与绿色「已盘」明确区分。
 * 详见 `utils/workstationMap.ts` 的 STATE_META。
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, lightApi, workstationApi } from '@/api'
import { useFreeTextOptions } from '@/composables/useFreeTextOptions'
import { appState } from '@/stores/app'
import type {
  Asset,
  ClearSuggestionsResult,
  LinkSuggestionsResult,
  LightHolding,
  MapInventoryAsset,
  MapInventoryWorkstation,
  StationLightHoldings,
  StationRelationCheck,
  Workstation,
} from '@/types'
import AppIcon from '@/components/AppIcon.vue'
import { hasOwner, iconOf, orDash, statusMeta, typeIcon, userLabel } from '@/utils/format'
import { holdingCategoryIcon } from '@/utils/light'
import RelationCheckCard from '@/components/RelationCheckCard.vue'
import LinkSuggestDialog from '@/components/LinkSuggestDialog.vue'
import {
  brandModel,
  facingLabel,
  isNeutral,
  positionText,
  RELATION_META,
  relationOf,
  stateMeta,
} from '@/utils/workstationMap'

const props = defineProps<{
  station: Workstation | null
  /** 该点位在「当前选中的盘点任务」里的状态（没开盘点模式时为 null） */
  mapRow: MapInventoryWorkstation | null
  assets: Asset[]
  users: string[]
  auditMode: boolean
  assetsLoading: boolean
  markingIds: number[]
  /** 当前选中的盘点任务 id。用于「去资产详情」时把回程地址带全 */
  taskId: number | null
  /** 关系核对结果。**现算的派生结果，不落库**（每次选中点位时拉一次） */
  relationCheck: StationRelationCheck | null
  relationLoading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', station: Workstation): void
  (e: 'removed', station: Workstation): void
  (e: 'mark', assetId: number, result: 'checked' | 'abnormal' | 'pending', note?: string | null): void
  (e: 'mark-station', workstationId: number, result: 'checked' | 'abnormal'): void
  (e: 'assets-changed', stationId: number): void
  (e: 'add-asset', stationId: number): void
  /** 关系核对结果变了（确认 / 撤销例外后后端返回的新结果） */
  (e: 'relation-updated', check: StationRelationCheck): void
  /** 让父组件重新拉一次核对结果（令牌过期等） */
  (e: 'relation-refresh'): void
  /** 打开迁移对话框。assetId 有值 = 单台资产换点位，无值 = 整个点位迁移 */
  (e: 'relocate', payload: { mode: 'assets' | 'station'; assetId?: number }): void
}>()

const router = useRouter()

const busy = computed(() => Boolean(props.station))
const saving = ref(false)

/* ---- 表单：改使用人 / 编码 ---- */
const codeInput = ref('')

/**
 * 点位使用人下拉。
 *
 * ⚠️ 这里**曾经只写了一半**：注释写着"与资产表单同一套拼音筛选 + 新建项垫底"，
 * 实际只搬了拼音筛选、没搬那个"新建"候选项 —— 于是 `filterable` 让框里能打字，
 * 但库里没有的名字**存不进去**（没有候选能对上，回车什么都不发生、`change` 不触发）。
 * 而这恰恰是最需要它的场景：这个位置**有人在用，但他用的是自己的笔记本**，
 * 台账里根本没有他名下那台资产，人名的候选里自然也就没有他。
 *
 * 现在两边共用 `useFreeTextOptions`，「同一套」不再只是一句注释。
 * （注意它是**必须解构**的 —— 不拆开的话模板里 `userOptions` 拿到的是 ref 本身，
 *  `v-for` 会把它当对象迭代，下拉里渲染出一堆垃圾。实测踩过。）
 */
const {
  options: userOptions,
  createLabel: userCreateLabel,
  onFilter: filterUsers,
  reset: resetUserQuery,
  onVisibleChange: onUserVisibleChange,
} = useFreeTextOptions(() => props.users)

watch(
  () => props.station,
  (s) => {
    codeInput.value = s?.code ?? ''
    resetUserQuery()
  },
  { immediate: true },
)

/* ------------------------------------------------------------------ */
/* 轻资产：点位详情里的**两组**关联配件（设计 §5.4）                     */
/* ------------------------------------------------------------------ */
/**
 * ⚠️ 两组**只是展示分组，不创建任何额外持有记录**：
 * - `direct`：`holder_kind='workstation'` —— 东西就固定放在这个工位上；
 * - `via_assets`：`holder_kind='asset'` 且那台主机**当前**就在这个点位 —— 查询时算出来的，
 *   主机换点位这一组自动跟着变，配件上**不另存点位**（这就是"不重复位置关系"）。
 *
 * 数据由后端分组返回（`/light/by-workstation/{id}`），前端不做拼装判断。
 */
const lightHoldings = ref<StationLightHoldings | null>(null)
const lightLoading = ref(false)

async function loadLightHoldings() {
  const s = props.station
  if (!s) {
    lightHoldings.value = null
    return
  }
  lightLoading.value = true
  try {
    lightHoldings.value = await lightApi.byWorkstation(s.id)
  } catch {
    // 轻资产只是附加信息，取不到不该把整个侧栏打红
    lightHoldings.value = null
  } finally {
    lightLoading.value = false
  }
}

watch(
  () => props.station?.id,
  () => {
    void loadLightHoldings()
  },
  { immediate: true },
)

/** 两组相加 = 这个点位上"看得到的"配件总量（仅用于界面提示，不写进任何统计表） */
/**
 * 侧栏标题上的件数。
 *
 * ⚠️ **`via_users` 不能折进"共 N 件"**：那一组是"这个人名下有本点位"推断出来的，
 * 同一件东西在他另外的点位上也会出现 —— 加进去等于把 N 个候选当成 N 份实物。
 * 所以分成两段说：`确定 3 件 · 另有 1 件可能在`。
 */
const lightSummary = computed(() => {
  const g = lightHoldings.value
  if (!g) return ''
  const certain =
    g.direct.reduce((sum, h) => sum + h.quantity, 0) +
    g.via_assets.reduce((sum, h) => sum + h.quantity, 0) +
    (g.stock || []).reduce((sum, h) => sum + h.quantity, 0)
  const inferred = (g.via_users || []).reduce((sum, h) => sum + h.quantity, 0)
  if (certain && inferred) return `确定 ${certain} 件 · 另有 ${inferred} 件可能在`
  if (certain) return `共 ${certain} 件`
  if (inferred) return `${inferred} 件可能是`
  return ''
})

/** 这四组里有没有任何一条（空态判断用，别在模板里写四个 `||`） */
const hasAnyLight = computed(() => {
  const g = lightHoldings.value
  if (!g) return false
  return Boolean(
    g.direct.length || g.via_assets.length || (g.stock || []).length || (g.via_users || []).length,
  )
})

/**
 * 副行文本：`品牌 规格`。
 *
 * ⚠️ **主行必须是品类名（`category_name`），不是 `display`**。`display` 是**宿主**：
 * 在这两组里它分别等于点位编码和主机编号 —— 也就是"东西放在哪"，不是"这是什么东西"。
 * 2026-10-16 用户实测报障：原来两行都渲染 `display`，界面上只剩
 * 「B022-03-00038 ×1」，根本看不出装的是块固态硬盘。
 */
function catSpec(h: LightHolding): string {
  return [h.category_brand, h.category_spec].filter(Boolean).join(' ')
}

/** 「随主机」那一组的副行：`品牌 规格 · 装在 B022-03-00038 上`（前半段没有就不留分隔点） */
function viaSub(h: LightHolding): string {
  return [catSpec(h), `装在 ${h.display} 上`].filter(Boolean).join(' · ')
}

/** 盘点模式下的状态（没开盘点时为 null） */
const state = computed(() => props.mapRow?.state ?? null)
const neutral = computed(() => (state.value ? isNeutral(state.value) : false))

/**
 * 这个点位属于哪种关系（有人 × 有资产）。
 *
 * 与地图上是**同一个函数**（`utils/workstationMap.ts::relationOf`）——
 * 图上染成琥珀虚线、侧栏却写"正常"，那是两套事实，比不做还糟。
 * 盘点模式下不显示：那时的重点是盘点结论，不是空间使用情况。
 */
const relation = computed(() =>
  props.station ? RELATION_META[relationOf(props.station)] : null,
)

/**
 * 打包成一句人话的资产规格。走公共的 `brandModel`（含品牌去重）。
 *
 * 侧栏不拿 SN 当主标识 —— 真实库里 142 台资产的 `serial_number` 全是空的，
 * 显示「SN: —」等于没显示。所以主标识用 `asset_code`。
 */
function assetSpec(asset: Asset): string {
  return brandModel(asset.brand, asset.model)
}

/** 某台设备在这个盘点任务里的结果 */
function resultOf(assetId: number): 'pending' | 'checked' | 'abnormal' | null {
  const row = props.mapRow?.assets.find((a) => a.asset_id === assetId)
  return row ? (row.result as 'pending' | 'checked' | 'abnormal') : null
}

function isMarking(assetId: number): boolean {
  return props.markingIds.includes(assetId)
}

/** 关系核对的四种结论 → 短文案。与后端 `CheckStatus.LABELS` 同口径 */
const CHECK_LABELS: Record<string, string> = {
  mismatch: '待核对',
  unregistered: '未登记',
  undetermined: '无法判断',
}

/**
 * 某台设备的关系核对结论 —— **只返回需要看一眼的**。
 *
 * 一致的不加任何装饰：每个资产行都挂一个绿色「一致」标签，等于给界面加噪，
 * 用户会很快学会无视这些标签（那时真正的「待核对」也一起被无视了）。
 */
function relStateOf(assetId: number): string | null {
  const state = props.relationCheck?.asset_checks.find((a) => a.asset_id === assetId)?.state
  return state && state !== 'consistent' ? state : null
}

/* ---- 写操作 ---- */

/* ---- 关联操作建议（v2.7.0 填人 / v2.7.1 清人）----
 * 两个方向**各干各的**，互不触发：
 *   · 无归属 → 有人：问"要不要顺手把设备使用人对上"（mode=link）
 *   · 有人 → 无人：问"这些还挂在他名下的设备，一并清除还是留给下一任"（mode=unlink）
 * 换人（有人→有人）、绑定、迁移三个方向刻意不做（见评估结论）：
 *   迁移有自己的预览与令牌，本组件不碰它。 */
const linkResult = ref<LinkSuggestionsResult | ClearSuggestionsResult | null>(null)
const linkMode = ref<'link' | 'unlink'>('link')
const linkLoading = ref(false)
/** 待保存的点位使用人（面板确认后才真正写库） */
let pendingUser: string | null = null

async function saveBasics() {
  const s = props.station
  if (!s) return
  const payload: Record<string, string | null> = {}
  const code = codeInput.value.trim()
  if (code && code !== s.code) payload.code = code
  if (!Object.keys(payload).length) {
    ElMessage.info('没有改动')
    return
  }
  saving.value = true
  try {
    const updated = await workstationApi.update(s.id, {
      ...payload,
      operator: appState.operator || null,
    })
    emit('saved', updated)
    ElMessage.success('已保存')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

/**
 * 改点位使用人。
 *
 * **只改点位，不动资产** —— 这个原则没变；但两个方向各多了一步**建议**（不是联动）：
 *   · 无归属 → 有人：预检该点位下的资产，给出逐台建议，用户逐项勾选；
 *   · 有人 → 无人：预检"还有哪些设备挂在他名下"，默认都不勾（清人 = 丢归属方向）。
 * 冲突 / 清除项都不替用户决定。换人（有→有）不做建议 —— 评估结论：噪音最高、收益最低。
 */
async function changeUser(value: string | null) {
  const s = props.station
  if (!s) return
  const next = (value ?? '').trim() || null
  if (next === (s.user_name ?? null)) return

  // 无归属 → 有人：填人方向预检
  if (next && !hasOwner(s.user_name)) {
    pendingUser = next
    linkLoading.value = true
    try {
      const res = await workstationApi.linkSuggestions(s.id, next)
      if (res.suggestions.length === 0) {
        await saveUser(next)
      } else {
        linkMode.value = 'link'
        linkResult.value = res
      }
    } catch (error) {
      ElMessage.error(apiMessage(error))
    } finally {
      linkLoading.value = false
    }
    return
  }

  // 有人 → 无人：清人方向预检（只在"这个点位下确实有设备挂在他名下"时才弹）
  if (!next && hasOwner(s.user_name)) {
    pendingUser = null
    linkLoading.value = true
    try {
      const res = await workstationApi.unlinkSuggestions(s.id)
      if (res.suggestions.length === 0) {
        await saveUser(null)
      } else {
        linkMode.value = 'unlink'
        linkResult.value = res
      }
    } catch (error) {
      ElMessage.error(apiMessage(error))
    } finally {
      linkLoading.value = false
    }
    return
  }

  await saveUser(next)
}

/** 只保存点位使用人（不含资产联动）。关联同步在面板确认时已单独执行 */
async function saveUser(next: string | null) {
  const s = props.station
  if (!s) return
  saving.value = true
  try {
    const updated = await workstationApi.update(s.id, {
      user_name: next,
      operator: appState.operator || null,
    })
    emit('saved', updated)
    ElMessage.success(next ? `点位使用人已改为 ${next}` : '已清空点位使用人')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

/** 面板确认（含「暂不处理」/「都保留」）→ 保存点位使用人。资产侧已在面板里执行完 */
function onLinkConfirm() {
  linkResult.value = null
  // 填人方向：pendingUser = 新使用人；清人方向：pendingUser = null（= 清空点位使用人）
  void saveUser(pendingUser)
  pendingUser = null
}

/** 面板被直接关掉（×）：什么都不做，下拉会因 model-value 未变而保持原值 */
function onLinkClose() {
  linkResult.value = null
  pendingUser = null
}

/** 从点位上摘下一台设备（**资产留着**，只是不再属于这个点位） */
async function unbind(asset: Asset) {
  const s = props.station
  if (!s) return
  try {
    await ElMessageBox.confirm(
      `把 ${asset.asset_code} 从点位 ${s.code} 摘下来？设备仍保留在台账里。`,
      '摘除设备',
      { confirmButtonText: '摘下来', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await workstationApi.unbindAsset(s.id, asset.id, appState.operator || null)
    emit('assets-changed', s.id)
    ElMessage.success(`已把 ${asset.asset_code} 从点位摘下来`)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

/** 把这个点位上的设备全部标为「已盘」 */
function markAllPassed() {
  const s = props.station
  if (!s) return
  emit('mark-station', s.id, 'checked')
}

/**
 * 标记单台设备异常。**必须采集备注** —— 与盘点列表页（`InventoryDetailView.markAbnormal`）
 * 用的是同一句话术和同一个字段，两处口径不许漂。
 *
 * 这个入口曾经是**唯一一个不采集备注的**：地图上点「标记缺失」直接就把 result 写进库，
 * `inventory_items.note` 永远为空 → 导出的「盘点备注」列有列无值。
 * 备注是异常里最有用的那一半信息（"位置不对 / SN 对不上 / 外壳破损"），丢了等于白盘。
 *
 * 已经标过异常时把上次的备注带出来：重标是**在改**，不是从零写。
 */
async function markAbnormal(row: MapInventoryAsset) {
  let note: string | null = null
  try {
    const res = await ElMessageBox.prompt(`${row.asset_code}：哪里不对？`, '标记异常', {
      confirmButtonText: '确认异常',
      cancelButtonText: '取消',
      inputPlaceholder: '例如「位置不对，实际在 4F」「SN 对不上」「外壳破损」',
      inputValue: row.note ?? '',
    })
    note = res.value?.trim() || null
  } catch {
    return // 用户取消
  }
  emit('mark', row.asset_id, 'abnormal', note)
}

/**
 * 跳去资产台账。**带上当前点位的使用人作为筛选** —— 这样从某个使用人的点位点进去，
 * 看到的就是"那个人的设备"，少一步手动筛选。
 *
 * 不带筛选的两种情况，都是有意的：
 *   - 点位**没有使用人**（空闲点位）：没有可筛的名字，只能给全量台账
 *   - 使用人写的是 `/`、`公用` 这类**占位符**：它出现在台账里会筛出一堆
 *     不属于任何人的设备，反而误导。用 `hasOwner()` 统一判断（`/` 和空值都算没归属）
 *
 * 注意筛选值走 URL 的 `user_name`，与台账页自己的筛选参数**同一个 key**
 * （见 `AssetListView.readStateFromQuery`），所以进去之后筛选框里会正常显示这个条件，
 * 用户可以直接 × 掉或改成别人 —— 不是一个只读的隐藏过滤。
 */
function openLedger() {
  const q = new URLSearchParams()
  const owner = (props.station?.user_name || '').trim()
  if (owner && hasOwner(owner)) q.set('user_name', owner)
  if (props.taskId) q.set('task', String(props.taskId))
  // ⚠️ back 的值**不要在这里 encodeURIComponent**：URLSearchParams.toString()
  // 已经会把它整体编码一次，再编一次会变成 back=%252Fmap，
  // 台账页解出来是字面的 "%2Fmap" 而不是 "/map"，返回按钮就废了。
  q.set('back', `/map${props.taskId ? `?task=${props.taskId}` : ''}`)
  router.push(`/?${q.toString()}`)
}

/** 跳去轻资产台账（登记 / 查看配件用）。地图上不画轻资产对象，这里只是入口 */
function openLightLedger() {
  router.push('/light')
}

/** 跳去某台设备的详情页 */
function openAsset(asset: Asset) {
  const q = props.taskId ? `?task=${props.taskId}` : ''
  const back = encodeURIComponent(`/map${q}`)
  router.push(`/asset/${asset.id}?back=${back}`)
}

/** 资产编号是侧栏的主标识 —— 真实库的 SN 是空的，不能拿它当主标识 */
function dash(v: string | null | undefined): string {
  return v && v.trim() ? v : '—'
}
</script>

<template>
  <aside class="sb">
    <div class="sb-head">
      <div>
        <div class="t">{{ auditMode ? '现场点位盘点' : '点位详情' }}</div>
        <div class="d">WORKSTATION</div>
      </div>
      <button class="sb-close" title="关闭" @click="emit('close')">×</button>
    </div>

    <!-- 未选中 -->
    <div v-if="!busy" class="empty-st">
      <!--
        用内联 SVG 而不是 emoji：原型这里就是 SVG。
        emoji 依赖系统字体（headless 环境 / 精简版 Windows 里会渲染成豆腐块 □），
        一个 76px 的大图标变成方块很显眼；SVG 到哪都一样。
      -->
      <div class="ring">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
          <path d="M3 10h18" />
          <path d="M4 10v9a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-9" />
          <path d="M6 10V7a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3" />
        </svg>
      </div>
      <div class="h">选择一个点位</div>
      <div class="p">
        点击地图上的点位，查看这个位置上的<b>真实设备</b>，<br />
        并在图上直接完成盘点。
      </div>
    </div>

    <div v-else class="sb-body">
      <!-- 抬头：编码 + 位置 -->
      <div class="dw-head">
        <div class="avatar" :class="{ idle: !station!.user_name }">
          {{ station!.user_name ? station!.user_name.slice(0, 1) : '空' }}
        </div>
        <div class="info">
          <div class="c">{{ station!.code }}</div>
          <div class="m">
            坐标 {{ positionText(station!.x, station!.y) }}
          </div>
          <!--
            点位关系概况。**是概况不是状态** —— 它由「有没有归属的人」和
            「挂了几台设备」现算出来，资产一挂一摘就变，所以没有落库字段（红线 2）。
            盘点模式下不显示：那时候用户要的是盘点的结论。
          -->
          <div v-if="!auditMode && relation" class="rel-chip" :class="relationOf(station!)">
            <b>{{ relation.glyph }} {{ relation.label }}</b>
            <span>{{ relation.hint }}</span>
          </div>
        </div>
      </div>

      <!-- 三个指标。盘点状态用的是**当前选中任务**的结果 -->
      <div class="metrics">
        <div class="metric">
          <div class="v">{{ station!.user_name ? userLabel(station!.user_name) : '—' }}</div>
          <div class="k">点位使用人</div>
        </div>
        <div class="metric">
          <div class="v">{{ station!.asset_count }} 台</div>
          <div class="k">绑定资产</div>
        </div>
        <div class="metric">
          <div class="v" :class="{ neutral: neutral }">
            {{ state ? stateMeta(state).label : '—' }}
          </div>
          <div class="k">盘点状态</div>
        </div>
      </div>

      <!--
        关系核对（点位使用人 ↔ 设备使用人）。
        **放在资产清单之前** —— 先说"这个点位的人/设备对不对得上"，
        再往下才是"具体挂了哪几台"，读起来是自顶向下的。
        结论是派生结果（后端每次现算），所以这里不存任何状态。
      -->
      <RelationCheckCard
        :check="relationCheck"
        :loading="relationLoading"
        :relocation-enabled="!auditMode"
        @updated="(c) => emit('relation-updated', c)"
        @refresh="emit('relation-refresh')"
        @relocate="(p) => emit('relocate', p)"
      />

      <!--
        两种中性态要在这里说清楚，因为它们的"看起来像没盘"最容易引起误解。
        空位是"没有东西可盘"，不在范围是"本次不盘" —— 都不是"漏盘"。
      -->
      <div v-if="auditMode && neutral" class="neutral-note">
        <template v-if="state === 'empty'">
          这个位置上没有设备 —— <b>不是"盘过了"，也不是"漏盘"</b>，本就不参与本次盘点的分母。
        </template>
        <template v-else>
          这个位置有 {{ station!.asset_count }} 台设备，但<b>不属于当前选中的这次盘点</b>
          —— 本次不盘，不是漏盘。换一个盘点任务就能看到它们的状态。
        </template>
      </div>

      <!-- 盘点快捷条 -->
      <div v-if="auditMode && station!.asset_count > 0 && !neutral" class="audit-box">
        <div class="audit-title">
          <span><AppIcon name="zap" :size="14" /> 点位快捷盘点</span>
          <span class="st">{{ state ? stateMeta(state).label : '' }}</span>
        </div>
        <button class="pass-all" :disabled="!mapRow?.assets.length" @click="markAllPassed">
          <AppIcon name="circleCheck" :size="14" /> 把这个点位的设备全部标为已盘
        </button>
      </div>

      <!-- 盘点模式下的单台明细 -->
      <div v-if="auditMode && mapRow?.assets.length" class="sect">
        <div class="sh"><span>单台资产盘点明细</span></div>
        <div
          v-for="row in mapRow.assets"
          :key="row.asset_id"
          class="asset-item"
          :class="{ ok: row.result === 'checked', bad: row.result === 'abnormal' }"
        >
          <div class="asset-top">
            <div class="asset-icon">
              <AppIcon :name="typeIcon({ name: row.device_type_name, icon: row.device_type_icon })" :size="17" />
            </div>
            <div class="asset-info">
              <div class="asset-title">{{ row.asset_code }}</div>
              <div class="asset-sub">
                {{ dash(row.device_type_name) }} · {{ assetSpec({ brand: row.brand, model: row.model } as Asset) }}
              </div>
              <div class="asset-sub">
                账面：{{ row.result_label }}
                <template v-if="row.operator"> · {{ row.operator }}</template>
              </div>
              <!-- 备注要回显出来：地图上填的、列表里填的，都是同一份 -->
              <div v-if="row.note" class="asset-note">备注：{{ row.note }}</div>
              <!--
                盘点之后账面又被改过 —— 这是盘点最有价值的信号，后端已经算好了。
                「账上写着是谁用的」和「现在是谁用的」不一致，正是要找的东西。
              -->
              <div v-if="row.changed_since" class="changed">
                <AppIcon name="circleAlert" :size="12" /> 盘点后账面已变更（原为 {{ dash(row.snapshot_user) }}）
              </div>
            </div>
          </div>
          <div class="asset-btns">
            <button
              class="abtn pass"
              :class="{ active: row.result === 'checked' }"
              :disabled="isMarking(row.asset_id)"
              @click="emit('mark', row.asset_id, 'checked')"
            >
              <AppIcon name="circleCheck" :size="14" /> 正常在位
            </button>
            <button
              class="abtn danger"
              :class="{ active: row.result === 'abnormal' }"
              :disabled="isMarking(row.asset_id)"
              @click="markAbnormal(row)"
            >
              <AppIcon name="circleAlert" :size="14" /> 标记异常
            </button>
          </div>
        </div>
      </div>

      <!-- 资产清单（任何模式都显示真实资产） -->
      <div class="sect">
        <div class="sh">
          <span>绑定资产（{{ assets.length }}）</span>
          <span v-if="assetsLoading" class="loading">加载中…</span>
        </div>

        <!--
          「挂设备到点位」走的是「新增一台设备并直接指定点位」这条路。
          反过来做（给点位挑一台已有设备）就得在弹窗里塞一个带筛选分页的设备选择器，
          等于把台账再写一遍 —— 所以宁可多一个按钮。
          已经有设备的人用不着它，去台账编辑那台设备即可。
        -->
        <button class="add-asset-btn" @click="emit('add-asset', station!.id)">
          ＋ 给这个点位挂一台新设备
        </button>

        <div v-if="assets.length" class="asset-plain-list">
          <div v-for="asset in assets" :key="asset.id" class="plain-row">
            <span class="ic"><AppIcon :name="typeIcon(iconOf(asset))" :size="15" /></span>
            <div class="txt">
              <a class="code" @click="openAsset(asset)">{{ asset.asset_code }}</a>
              <div class="sub">
                {{ dash(asset.device_type_name) }} · {{ assetSpec(asset) }}
                <span class="tag" :style="{ color: statusMeta(asset.status).color }">
                  {{ statusMeta(asset.status).label }}
                </span>
              </div>
              <div v-if="auditMode && resultOf(asset.id)" class="sub">
                本次盘点：{{ resultOf(asset.id) === 'checked' ? '已盘' : resultOf(asset.id) === 'abnormal' ? '异常' : '待盘' }}
              </div>
              <!--
                关系核对在**资产行上就地呈现**（要求：不要求用户跳转到别的模块）。
                只标出需要看一眼的，一致的不加装饰。
              -->
              <div v-if="relStateOf(asset.id)" class="rel-inline">
                <span class="rel-chip-mini" :class="relStateOf(asset.id)">
                  {{ CHECK_LABELS[relStateOf(asset.id) as string] }}
                </span>
                <span class="rel-inline-txt">
                  与点位使用人
                  {{ relStateOf(asset.id) === 'mismatch' ? '不一致' : '无法对照' }}
                </span>
              </div>
            </div>
            <!--
              两个按钮职责必须分得清（这个项目在"混淆操作语义"上吃过事故）：
                · 换点位 = 改这台设备的**归属**（搬去别的点位）
                · 摘下   = 让它**没有点位**（仍在台账里）
              都不是"挪桌子"—— 那属于地图上的「调整位置」。
            -->
            <button
              class="mini"
              title="把这台设备迁到另一个点位（改归属，不动地图坐标）"
              @click="emit('relocate', { mode: 'assets', assetId: asset.id })"
            >
              换点位
            </button>
            <button class="mini" title="从点位摘下来（设备保留）" @click="unbind(asset)">摘下</button>
          </div>
        </div>

        <div v-else-if="!assetsLoading" class="none">
          该点位暂无绑定资产。<br />
          到<a @click="openLedger">资产台账</a>里编辑设备，把「所在点位」选上它就会出现在这里。
        </div>
      </div>

      <!--
        轻资产（设计 §5.4）：**不把每个配件画成地图对象**，只在这里分组列出来。
        四组分开显示，因为它们的含义完全不同 —— 不分开，用户会以为
        "随主机上的配件"也是自己放在这个工位上的、"按人推断的"更是已经确认在这儿的。

        前三组是**确定在这里**（工位 / 主机 / 库存位置写明），
        最后一组是**推断**（记在某人手上，而此人名下有本点位）—— 它必须长得不一样。
      -->
      <div class="sect">
        <div class="sh">
          <span>轻资产{{ lightSummary ? `（${lightSummary}）` : '' }}</span>
          <span v-if="lightLoading" class="loading">加载中…</span>
        </div>

        <template v-if="hasAnyLight">
          <div v-if="lightHoldings!.direct.length" class="light-group">
            <div class="light-group-title">固定在此工位（直接关联）</div>
            <div v-for="h in lightHoldings!.direct" :key="h.id" class="light-row">
              <span class="light-ic"><AppIcon :name="holdingCategoryIcon(h)" :size="14" /></span>
              <div class="light-info">
                <div class="light-name">{{ h.category_name }}</div>
                <div class="light-sub">
                  <template v-if="catSpec(h)">{{ catSpec(h) }}</template>
                  <template v-else>就放在这个工位上</template>
                </div>
              </div>
              <span class="light-qty">×{{ h.quantity }}</span>
            </div>
          </div>

          <div v-if="lightHoldings!.via_assets.length" class="light-group">
            <div class="light-group-title">随主机上的（间接关联）</div>
            <div v-for="h in lightHoldings!.via_assets" :key="h.id" class="light-row">
              <span class="light-ic"><AppIcon :name="holdingCategoryIcon(h)" :size="14" /></span>
              <div class="light-info">
                <div class="light-name">{{ h.category_name }}</div>
                <div class="light-sub">{{ viaSub(h) }}</div>
              </div>
              <span class="light-qty">×{{ h.quantity }}</span>
            </div>
          </div>

          <!-- 库存位置写的就是本点位编码：东西确实在这儿，但还没分配出去 -->
          <div v-if="lightHoldings!.stock.length" class="light-group">
            <div class="light-group-title">库存存放在此</div>
            <div v-for="h in lightHoldings!.stock" :key="h.id" class="light-row">
              <span class="light-ic"><AppIcon :name="holdingCategoryIcon(h)" :size="14" /></span>
              <div class="light-info">
                <div class="light-name">{{ h.category_name }}</div>
                <div class="light-sub">
                  <template v-if="catSpec(h)">{{ catSpec(h) }} · </template>库存未分配
                </div>
              </div>
              <span class="light-qty">×{{ h.quantity }}</span>
            </div>
          </div>

          <!--
            ⚠️ 推断组：**必须说清"可能"**。
            这个人名下有 N 个点位，东西到底在哪一处系统不知道；
            写成"在这里"就是撒谎，用户按它去找找不到时没有任何线索。
          -->
          <div v-if="lightHoldings!.via_users.length" class="light-group inferred">
            <div class="light-group-title">
              按使用人推断（可能在这里）
            </div>
            <div v-for="h in lightHoldings!.via_users" :key="h.id" class="light-row">
              <span class="light-ic"><AppIcon :name="holdingCategoryIcon(h)" :size="14" /></span>
              <div class="light-info">
                <div class="light-name">{{ h.category_name }}</div>
                <div class="light-sub">
                  <template v-if="catSpec(h)">{{ catSpec(h) }} · </template>
                  {{ h.user || '某人' }} 持有，按他的点位推断{{ h.inferred_station_codes.length > 1
                    ? `（共 ${h.inferred_station_codes.length} 个候选：${h.inferred_station_codes.join('、')}）`
                    : '' }}
                </div>
              </div>
              <span class="light-qty">×{{ h.quantity }}</span>
            </div>
          </div>
        </template>

        <div v-else-if="!lightLoading" class="none">
          这个点位上看不到任何轻资产。<br />
          要给某个工位配键鼠、或给某台主机加内存，到<a @click="openLightLedger">轻资产</a>里登记一次流转即可。
        </div>
      </div>

      <!-- 编辑区 -->
      <div class="sect">
        <div class="sh"><span>点位设置</span></div>

        <div class="field">
          <label>点位使用人</label>
          <!--
            使用人**是自由文本**，不是从人员表里挑 —— 所以最后那一项 `createLabel` 必须有：
            有这个位置的人在座、但他用的是自己的笔记本（台账里没有他名下资产），
            这种名字只可能手打。少了它，框里能打字但存不进去。
            拼音筛选 + 新建项垫底的完整理由见 `composables/useFreeTextOptions.ts`。
          -->
          <el-select
            :model-value="station!.user_name ?? ''"
            placeholder="可直接输入新的人名，或打拼音首字母"
            filterable
            default-first-option
            clearable
            style="width: 100%"
            :filter-method="filterUsers"
            :reserve-keyword="false"
            :disabled="saving"
            @change="changeUser"
            @visible-change="onUserVisibleChange"
          >
            <el-option v-for="u in userOptions" :key="u" :label="u" :value="u" />
            <el-option
              v-if="userCreateLabel"
              :key="`__new__${userCreateLabel}`"
              :label="userCreateLabel"
              :value="userCreateLabel"
            />
          </el-select>
          <div class="hint">这是「这个位置归谁」，不是设备使用人 —— 两者可以不一致，不一致正是盘点要看的</div>
        </div>

        <div class="field">
          <label>点位编码</label>
          <el-input v-model="codeInput" maxlength="32" placeholder="例如 W23" />
        </div>

        <div class="hint-row">
          朝向：{{ facingLabel(station!.facing) }} · 位置调整请用顶部「调整位置」拖动后保存布局
        </div>

        <div class="acts">
          <el-button type="primary" :loading="saving" @click="saveBasics">保存编码</el-button>
          <el-button type="danger" plain @click="emit('removed', station!)">删除点位</el-button>
        </div>
      </div>
    </div>

    <!-- 关联操作建议：填人（无→有）/ 清人（有→无）两个方向共用面板，语义分开 -->
    <LinkSuggestDialog
      :result="linkResult"
      :mode="linkMode"
      :loading="linkLoading"
      @confirm="onLinkConfirm"
      @close="onLinkClose"
    />
  </aside>
</template>

<style scoped>
.sb {
  width: 390px;
  flex: none;
  background: #fff;
  border-left: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  height: 100%;
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.03);
  z-index: 20;
}
.sb-head {
  padding: 16px 22px 12px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  border-bottom: 1px solid #e2e8f0;
  flex: none;
}
.sb-head .t { font-size: 15px; font-weight: 700; letter-spacing: -0.2px; color: #0f172a; }
.sb-head .d {
  font-size: 9.5px; color: #94a3b8; margin-top: 2px; letter-spacing: 1.5px; font-weight: 700;
}
.sb-close {
  width: 30px; height: 30px; border-radius: 8px; border: none; background: transparent;
  color: #94a3b8; cursor: pointer; font-size: 20px; line-height: 1;
}
.sb-close:hover { background: #f1f5f9; color: #0f172a; }

.sb-body { flex: 1; overflow-y: auto; padding: 16px 22px 28px; }
.sb-body::-webkit-scrollbar { width: 6px; }
.sb-body::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }

.empty-st {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; padding: 0 24px 60px;
}
.empty-st .ring {
  width: 76px; height: 76px; border-radius: 24px; margin-bottom: 20px;
  background: linear-gradient(150deg, #f4f7fb, #e8eef7); display: flex; align-items: center;
  justify-content: center; box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.04);
}
.empty-st .ring svg { width: 30px; height: 30px; color: #b6c2d1; flex: none; }
.empty-st .h { font-size: 14px; font-weight: 600; color: #475569; margin-bottom: 7px; }
.empty-st .p { font-size: 12px; color: #94a3b8; line-height: 1.9; }
.empty-st .p b { color: #2563eb; font-weight: 600; }

.dw-head {
  display: flex; align-items: center; gap: 14px; padding-bottom: 16px;
  border-bottom: 1px solid #e2e8f0;
}
.avatar {
  width: 48px; height: 48px; border-radius: 14px; flex: none; display: flex;
  align-items: center; justify-content: center; font-size: 18px; font-weight: 700; color: #fff;
  background: linear-gradient(135deg, #60a5fa, #1d4ed8);
}
.avatar.idle { background: linear-gradient(135deg, #e2e8f0, #cbd5e1); color: #64748b; }
.dw-head .info { flex: 1; min-width: 0; }
.dw-head .c { font-size: 22px; font-weight: 800; color: #0f172a; }
.dw-head .m { font-size: 11.5px; color: #94a3b8; margin-top: 4px; font-weight: 500; }

/* 点位关系概况标签。配色与地图上的 .ws.rel-* 一一对应 —— 两处不同色
   就等于给了两条互相矛盾的信息。 */
.rel-chip {
  display: inline-flex; align-items: baseline; gap: 7px; flex-wrap: wrap;
  margin-top: 8px; padding: 3px 9px; border-radius: 999px;
  font-size: 11px; line-height: 1.5;
  border: 1px solid var(--rel-bd, #e2e8f0);
  background: var(--rel-bg, #f8fafc);
  color: var(--rel-fg, #475569);
}
.rel-chip b { font-weight: 700; color: var(--rel-fg, #475569); }
.rel-chip span { color: #94a3b8; }
.rel-chip.person_asset { --rel-bg: #eff6ff; --rel-bd: #bfdbfe; --rel-fg: #1d4ed8; }
.rel-chip.person_none { --rel-bg: #fffbeb; --rel-bd: #fde68a; --rel-fg: #b45309; }
.rel-chip.none_asset { --rel-bg: #f1f5f9; --rel-bd: #cbd5e1; --rel-fg: #475569; }
.rel-chip.none_none { --rel-bg: #fafafa; --rel-bd: #e5e7eb; --rel-fg: #94a3b8; }

.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 16px 0 18px; }
.metric { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }
.metric .v {
  font-size: 13.5px; font-weight: 700; color: #0f172a;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.metric .v.neutral { color: #64748b; }
.metric .k { font-size: 10.5px; color: #94a3b8; margin-top: 2px; }

.neutral-note {
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #94a3b8;
  border-radius: 8px; padding: 10px 12px; font-size: 11.5px; color: #475569;
  line-height: 1.75; margin-bottom: 16px;
}
.neutral-note b { color: #0f172a; }

.audit-box {
  background: #fffbeb; border: 1px solid #fde68a; border-radius: 12px;
  padding: 12px; margin-bottom: 16px;
}
.audit-title {
  font-size: 12px; font-weight: 700; color: #78350f; margin-bottom: 10px;
  display: flex; justify-content: space-between; align-items: center;
}
.audit-title .st { font-size: 11px; color: #d97706; font-weight: 600; }
.pass-all {
  width: 100%; height: 36px; border-radius: 9px; font-size: 12.5px; font-weight: 700;
  border: none; background: #10b981; color: #fff; cursor: pointer;
  box-shadow: 0 4px 12px -3px rgba(16, 185, 129, 0.4); transition: all 0.16s;
}
.pass-all:hover:not(:disabled) { background: #059669; }
.pass-all:disabled { opacity: 0.5; cursor: not-allowed; }

.sect { margin-top: 16px; padding-top: 14px; border-top: 1px solid #e2e8f0; }
.sect .sh {
  font-size: 11px; font-weight: 700; color: #94a3b8; letter-spacing: 1.2px;
  margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;
}
.sect .sh .loading { font-size: 10.5px; color: #94a3b8; letter-spacing: 0; }

.asset-item {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 11px;
  margin-bottom: 9px; display: flex; flex-direction: column; gap: 8px;
}
.asset-item.ok { border-color: #a7f3d0; background: #ecfdf5; }
.asset-item.bad { border-color: #fca5a5; background: #fef2f2; }
.asset-top { display: flex; align-items: flex-start; gap: 10px; }
.asset-icon {
  width: 32px; height: 32px; border-radius: 8px; background: #eff6ff;
  display: flex; align-items: center; justify-content: center; flex: none; font-size: 15px;
}
.asset-info { flex: 1; min-width: 0; }
.asset-title { font-size: 12px; font-weight: 700; color: #0f172a; }
.asset-sub { font-size: 10.5px; color: #94a3b8; margin-top: 2px; }
/* 备注：比"账面"重一点，因为它是人写的信息，不是系统字段 */
.asset-note {
  font-size: 10.5px; color: #475569; margin-top: 3px; line-height: 1.6;
  word-break: break-word;
}
.changed { font-size: 10.5px; color: #b45309; margin-top: 4px; font-weight: 600; }
.asset-btns { display: flex; gap: 6px; }
.abtn {
  flex: 1; height: 28px; border-radius: 6px; font-size: 11px; font-weight: 700;
  border: 1px solid #cbd5e1; background: #fff; color: #475569; cursor: pointer;
  transition: all 0.14s;
}
.abtn:disabled { opacity: 0.5; cursor: not-allowed; }
.abtn.pass.active { background: #10b981; border-color: #10b981; color: #fff; }
.abtn.danger.active { background: #ef4444; border-color: #ef4444; color: #fff; }

.add-asset-btn {
  width: 100%; height: 34px; margin-bottom: 10px; border-radius: 9px;
  border: 1px dashed #cbd5e1; background: #f8fafc; color: #475569;
  font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 0.16s;
}
.add-asset-btn:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; }

.asset-plain-list { display: flex; flex-direction: column; gap: 8px; }
.plain-row {
  display: flex; align-items: flex-start; gap: 10px; padding: 9px 10px;
  background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 9px;
}
.plain-row .ic { font-size: 15px; flex: none; line-height: 1.4; }
.plain-row .txt { flex: 1; min-width: 0; }
.plain-row .code {
  font-size: 12px; font-weight: 700; color: #2563eb; cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.plain-row .code:hover { text-decoration: underline; }
.plain-row .sub { font-size: 10.5px; color: #94a3b8; margin-top: 2px; }
.plain-row .tag { font-weight: 700; margin-left: 4px; }
/* 关系核对在资产行上的就地标记。只用琥珀（待核对）与灰（无法对照）两档 —— */
/* 与卡片配色一致，且刻意不用红：差异是"待核对"，不是"错误"。 */
.rel-inline { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.rel-chip-mini {
  font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 999px; line-height: 1.6;
}
.rel-chip-mini.mismatch { background: #fef3c7; color: #b45309; }
.rel-chip-mini.unregistered,
.rel-chip-mini.undetermined { background: #f1f5f9; color: #64748b; }
.rel-inline-txt { font-size: 10.5px; color: #94a3b8; }
.mini {
  flex: none; height: 24px; padding: 0 9px; border-radius: 6px; font-size: 11px;
  border: 1px solid #cbd5e1; background: #fff; color: #475569; cursor: pointer;
}
.mini:hover { border-color: #94a3b8; color: #0f172a; }

.none {
  background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 10px; padding: 16px;
  text-align: center; font-size: 12px; color: #94a3b8; line-height: 1.8;
}
.none a { color: #2563eb; cursor: pointer; }

/* ---------- 点位详情里的轻资产两组 ---------- */
.light-group { margin-bottom: 10px; }
.light-group-title {
  font-size: 11px; color: #94a3b8; letter-spacing: 0.2px; margin-bottom: 5px;
}
/* 推断组：与地图上的"虚线框"同一套语言（橙 = 这条命中需要人看一眼）。
   行本身也用虚线描边 —— 不能让它和"确定在这里"的三组长得一样。 */
.light-group.inferred .light-group-title { color: #b45309; }
.light-group.inferred .light-row {
  background: #fffbeb; border-style: dashed; border-color: #fcd34d;
}
.light-row {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 9px; margin-bottom: 4px;
  background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
  font-size: 12.5px; color: #1e293b;
}
/* 两行：主行 = 品类名（**这是什么东西**），副行 = 品牌规格 + 装在谁身上。
   宿主（点位 / 主机）只出现在副行 —— 它在标题里已经说过一遍了。 */
.light-ic { flex: none; display: flex; align-items: center; color: #64748b; }
.light-info { flex: 1; min-width: 0; }
.light-name { font-weight: 600; overflow-wrap: anywhere; }
.light-sub { font-size: 11px; color: #94a3b8; margin-top: 1px; overflow-wrap: anywhere; }
.light-qty { flex: none; color: #64748b; font-variant-numeric: tabular-nums; }

.field { margin-bottom: 13px; }
.field label {
  display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 6px;
}
.field .hint { font-size: 11px; color: #94a3b8; line-height: 1.7; margin-top: 5px; }
.hint-row { font-size: 11px; color: #94a3b8; margin: 4px 0 12px; line-height: 1.7; }
.acts { display: flex; gap: 8px; }
.acts .el-button { flex: 1; }

/* ==========================================================================
   窄屏（≤900px）：本组件在手机上不再是"右侧常驻栏"，而是浮在地图上的底部抽屉。

   外壳尺寸由 SpaceMapView 的 `.sheet-host` 决定（那边才知道画布和抽屉的比例），
   这里只负责**别再用固定 390px 宽**——那正好等于手机屏宽，是"地图被挤没"的元凶。

   断点必须和 SpaceMapView 里的 900px 以及 JS 的 `compact` 保持一致。
   ========================================================================== */
@media (max-width: 900px) {
  .sb {
    width: 100%;
    height: 100%;
    border-left: none;
    border-radius: 16px 16px 0 0;
    /* 圆角外的阴影由外壳给，这里再来一层会在圆角处露出发灰的边 */
    box-shadow: none;
    z-index: auto;
  }

  /* 抽屉顶部加一道"抓手"，视觉上说明这东西是浮层、可以关掉 */
  .sb-head { padding: 12px 16px 10px; position: relative; }
  .sb-head::before {
    content: '';
    position: absolute;
    left: 50%;
    top: 6px;
    width: 36px;
    height: 4px;
    margin-left: -18px;
    border-radius: 2px;
    background: #e2e8f0;
  }

  .sb-body { padding: 12px 16px 20px; }

  /* 三个指标在窄屏保持横排（挤成一列太浪费抽屉那点高度），只把留白和字号收一点 */
  .metrics { gap: 6px; margin: 10px 0 12px; }
  .metric { padding: 8px 9px; }
  .metrics .metric .v { font-size: 12px; }

  /* 抽屉本身高度有限，里面的空态别再抢滚动权 */
  .empty-st { padding-bottom: 20px; }
}
</style>
