<script setup lang="ts">
/**
 * 资产空间地图。
 *
 * ## 它解决的问题
 * 台账是**按设备编号**查东西的，回答不了「这一片点位现在什么情况」。
 * 这一页把点位按真实平面图摆出来，点一下就能看到那个位置上有什么设备，
 * 并且能在图上直接盘点 —— 不用抱着平板在点位间来回跑。
 *
 * ## 一句话设计口径
 * **这页只是"看"的另一副眼镜，不是第二套数据。**
 *   · 点位来自 `workstations` 表（`assets.workstation_id` 反查资产）
 *   · 资产来自现有 `assets` 表 —— 一台都不另存
 *   · 盘点直接调现有 `inventory` 的标记接口，读现有 `inventory_items.result`
 * 所以在地图上标记一台设备，退出地图去原盘点页，看到的是**同一条记录**。
 *
 * ## 三处必须解释清楚的设计
 *
 * ### 1. 盘点任务选择器（不是装饰）
 * `/api/inventory/context/{asset_id}` 是**单任务假设**：取该设备所属的、
 * 最近一个进行中任务。这在资产详情页合理（问的是"这台设备归哪个盘点"），
 * 但地图问的是「**这一片点位整体盘到哪了**」—— 聚合视图必须明确聚合的是哪一次。
 * 两个盘点任务同时进行时（比如"整层盘点"和"采购部专项"），
 * 不做选择器就会拿其中一个的状态去渲染整张图，大部分格子显示"待盘"，
 * 而那批设备其实在另一个任务里早就盘完了 —— 这不是显示错，
 * 是**显示了一个用户没问过的任务的答案**。所以顶部永远显示当前任务名。
 *
 * ### 2. 五种点位状态，其中两种是中性态
 * `checked` / `abnormal` / `pending` 由该点位下**属于本次任务**的资产聚合
 * （优先级 异常 > 待盘 > 已盘 —— 有一台异常就报异常，不能被"其他都正常"掩盖）。
 * `empty`（这位置没有资产）与 `not_in_scope`（有资产但本次不盘）是中性态：
 * **都不进盘点计数、都不进进度分母**，而且文案分开 ——
 * 把"本次不盘"误读成"漏盘"是盘点里最需要避免的错误结论。
 *
 * ### 3. 进度分母用**资产数**，不是点位数
 * 盘点的对象是资产：一个点位可能挂 2 台（主机 + 显示器），也可能 0 台。
 * 拿点位数当分母，在任何点位挂 2 台时百分比都会失准。
 * 而 `task.total / checked` 本来就是后端按 `inventory_items` 数出来的资产维度，
 * **直接用同一份数字**，地图与原盘点页的进度天然相等。
 */
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, inventoryApi, lightApi, mapInventoryApi, metaApi, relationCheckApi, workstationApi } from '@/api'
import { appState } from '@/stores/app'
import type {
  Asset,
  InventoryTask,
  LightHolding,
  MapInventoryView,
  MapInventoryWorkstation,
  MapState,
  RelocationApplyResult,
  RelocationMode,
  StationRelationCheck,
  Workstation,
} from '@/types'
import { hasOwner, userLabel } from '@/utils/format'
import {
  emptyLayout,
  parseLayout,
  ROOM_COLORS,
  SHAPE_KIND_CLASS,
  type FloorShape,
} from '@/utils/floorMap'
import { matchByPinyin } from '@/utils/pinyin'
import {
  buildExportPayload,
  clampToCanvas,
  compareStationCode,
  DESK_H,
  DESK_W,
  FALLBACK_CANVAS,
  GRID,
  isNeutral,
  RELATION_META,
  RELATION_ORDER,
  relationOf,
  snapAxis,
  stateMeta,
  type RelationKey,
} from '@/utils/workstationMap'
import MapStationPanel from '@/components/MapStationPanel.vue'
import RelocationDialog from '@/components/RelocationDialog.vue'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const canvas = ref({ ...FALLBACK_CANVAS })
const stations = ref<Workstation[]>([])

/**
 * 平面图图元。**唯一来源是 `GET /api/workstations` 的 `layout` 字段**。
 *
 * 这里不再有写死的建筑图元 —— 那是这次数据化的全部意义。
 * 要改形状去编辑器改，别往模板里加 div。
 */
const floorShapes = ref<FloorShape[]>(emptyLayout().shapes)

/** 图元的定位样式。房间/地台要宽高，墙体靠 class 里的固定边宽。 */
function shapeBox(shape: FloorShape): Record<string, string> {
  const style: Record<string, string> = {
    left: `${shape.x}px`,
    top: `${shape.y}px`,
  }
  if (shape.w !== undefined) style.width = `${shape.w}px`
  if (shape.h !== undefined) style.height = `${shape.h}px`
  return style
}

const selectedId = ref<number | null>(null)

/** 侧栏资产。按点位缓存，避免来回点同一格反复请求 */
const assetCache = ref<Record<number, Asset[]>>({})
const assetsLoading = ref(false)

const users = ref<string[]>([])

/* ---- 视图状态（缩放 / 平移）---- */
const vp = ref<HTMLElement | null>(null)
/** 点位详情抽屉的外壳。窄屏下它浮在画布上，需要量它的高度来让位 */
const sheetEl = ref<HTMLElement | null>(null)
const tx = ref(0)
const ty = ref(0)
const scale = ref(1)
const spacePressed = ref(false)
const panning = ref(false)
const panStart = { x: 0, y: 0, tx: 0, ty: 0 }

/** 缩放的上下限。**只有这一处**：以前 0.2/3 散在 fit / zoomBy / onWheel 三处，
 *  这次加双指时差点只改了其中一处 —— 收成常量免得再漂。 */
const MIN_SCALE = 0.2
const MAX_SCALE = 3
function clampScale(v: number) {
  return Math.max(MIN_SCALE, Math.min(MAX_SCALE, v))
}

/**
 * 缩放步长。**故意是两个不同的值，别"统一"成一个**（2026-10-08）。
 *
 * 步长大小取决于**输入设备的物理特性**，不是取决于代码整洁：
 * - 滚轮一格一格地"滚"，是**离散且要连续滚动**的操作 → 步长小（10%）才够细腻；
 *   太大一点滚轮就"啪"一下放大到头，找不回来。
 * - 工具条 ± 是**点一下就完事**，用户盯着结果 → 步长大（15%）才不让人连点七八下。
 *
 * 这两个值以前散在模板两处 + `onWheel` 一处，改的时候要改三处 —— 正是决策 75
 * 里说的那种"同一口径散在多处"的典型漂移风险，所以收成具名常量。
 *
 * ⚠️ 双指捏合**不用这个** —— 它按两指距离的实时比例算，天然是连续的
 * （见 `pinchMetrics`）。
 */
const ZOOM_STEP_BUTTON = 1.15 // 工具条 ±
const ZOOM_STEP_WHEEL = 1.1 // 滚轮一格

/* ---- 模式 ---- */
const editMode = ref(false) // 「调整位置」：可拖动
const adding = ref(false) // 「新增」：点空白处放新点位
const auditMode = ref(false) // 「盘点模式」

/* ---- 批量生成点位 ----
 *
 * ## 为什么它在地图页而不是「编辑底图」里
 * 用户的原话：「底图是底图呀，不一样呀，和工位不要在一起弄。」
 * （原话说的是「工位」；v2.1.0 起界面统一叫「点位」，下同。）
 * 说得对 —— 人不会想到"要加一片点位，先去编辑底图"。
 * 而且那个按钮和「新增图元」并排、长得一样，语义却**相反**
 * （图元进草稿要保存，点位立刻写库），真实事故就是这么来的。
 * 点位的家就在这一页的「调整位置」模式里，单个增删也在这 —— 它当然也该在这。
 */
const bulkOpen = ref(false)
const bulkBusy = ref(false)
const bulk = ref({
  count: 12,
  origin_x: 60,
  origin_y: 220,
  step_x: 90,
  step_y: 66,
  cols: 6,
  facing: 'alternate' as 'down' | 'up' | 'alternate',
  start_code: '',
})

/** 刚生成的那一批，用于「撤销生成」。只记最近一次 —— 后悔药给一步就够了。 */
const lastBulk = ref<{ codes: string[]; items: { id: number }[] } | null>(null)
const bulkUndoBusy = ref(false)

const bulkValid = computed(
  () => bulk.value.count >= 1 && bulk.value.count <= 200 && bulk.value.cols >= 1,
)

/**
 * 编码预览。**只是给用户的预期，不是真相** ——
 * 真正的分配在后端事务里做（复用 `_allocate_codes`），撞号会 409 而不跳号。
 */
const bulkPreview = computed(() => {
  const explicit = (bulk.value.start_code || '').trim()
  if (/^[A-Za-z]+\d+$/.test(explicit)) {
    const m = explicit.match(/^([A-Za-z]+)(\d+)$/)!
    const start = Number(m[2])
    const end = start + bulk.value.count - 1
    return `${m[1]}${String(start).padStart(2, '0')} ~ ${m[1]}${String(end).padStart(2, '0')}`
  }
  let biggest = 0
  for (const s of stations.value) {
    const m = /^([A-Za-z]*)(\d+)$/.exec((s.code || '').trim())
    if (m && (!m[1] || m[1].toUpperCase() === 'W')) biggest = Math.max(biggest, Number(m[2]))
  }
  const start = biggest + 1
  const end = start + bulk.value.count - 1
  return `W${String(start).padStart(2, '0')} ~ W${String(end).padStart(2, '0')}`
})

/* ---- 搜索 ---- */

/**
 * 空间地图的搜索框：**一个框、四个维度**（点位 / 人员 / 资产 / 轻资产）。
 *
 * ## 三条口径（2026-10-19 重构）
 * 1. **不搜 `room`**。系统里只有「点位 / 资产 / 人员」，没有"房间"这一维；
 *    真实库 69 个点位里 `room` 有值的只有 2 个（前台、总经理办公室），
 *    留着它只会让用户搜到一个"别处都搜不到"的词。
 * 2. **不跟任何图层开关联动**。关系层决定"画不画符号"，搜索决定"找什么"；
 *    绑在一起就会出现「开了关系层搜人名却 0 命中」这种看起来像坏了的现场。
 *    （v2.9.6 起轻资产层开关已删，这条约束只剩关系层一处。）
 * 3. **索引懒加载**：进页面不请求，第一次聚焦搜索框才拉资产与轻资产各一次
 *    （不搜索的人一个多余请求都不用发）。
 *
 * 命中集合是**纯派生**的：不落库、不写任何状态，清空搜索词即恢复原样。
 */
const searchQuery = ref('')
const searchBoxEl = ref<HTMLElement | null>(null)
const panelEl = ref<HTMLElement | null>(null)
const panelOpen = ref(false)
/** 浮层位置。用 fixed + 计算坐标，**不能靠 CSS 相对定位** ——
 *  `.map-topbar` 是 `overflow-x: auto`，绝对定位的子元素会被裁掉。 */
const panelPos = ref({ left: 0, top: 0, width: 380 })

type SearchDim = 'station' | 'person' | 'asset' | 'light'

interface SearchReason {
  dim: SearchDim
  /** 「这块为什么亮」的一句话，直接显示在结果行里 */
  label: string
  /**
   * 这条理由是**推断**出来的（"配件在某人手上，而此人有这个点位"），不是确定关系。
   * 只靠推断亮起的点位要画虚线边框 —— 不然用户会以为东西确实在这儿。
   */
  inferred?: boolean
}

interface StationHit {
  station: Workstation
  reasons: SearchReason[]
}

/** 搜到了、但**不在地图上**的东西。不给出口的话，搜索结果就是在说谎。 */
interface OffMapHit {
  key: string
  label: string
  /** 为什么不在图上（未挂点位 / 在某人手上 / 库存） */
  hint: string
  to: string
}

/**
 * 两个索引的条数上限 —— 是**接口的硬限制**（资产 `le=1000`、holdings `le=200`）。
 * 一旦 `total` 超出，面板必须把"只搜了一部分"说出来，否则资产涨到一千多台时
 * 搜索会静默漏结果，而界面一切正常（典型假绿）。
 */
const ASSET_INDEX_LIMIT = 1000
const LIGHT_INDEX_LIMIT = 200

const searchIndex = ref<{
  assets: Asset[]
  light: LightHolding[]
  assetTotal: number
  lightTotal: number
} | null>(null)
const searchLoading = ref(false)
const searchLoadError = ref('')

async function ensureSearchIndex() {
  if (searchIndex.value || searchLoading.value) return
  searchLoading.value = true
  searchLoadError.value = ''
  try {
    const [assets, light] = await Promise.all([
      assetApi.list({ page_size: ASSET_INDEX_LIMIT }),
      lightApi.holdings({ page_size: LIGHT_INDEX_LIMIT }),
    ])
    searchIndex.value = {
      assets: assets.items,
      light: light.items,
      assetTotal: assets.total,
      lightTotal: light.total,
    }
  } catch (error) {
    // 索引挂了不等于搜索挂了：点位 / 人员两个维度仍然可用，只是少了资产与轻资产。
    searchLoadError.value = apiMessage(error)
  } finally {
    searchLoading.value = false
  }
}

/**
 * 丢弃索引。**迁移 / 换人之后必须调用** ——
 * 索引里存的是 `assets.workstation_id`，迁移一改，缓存就指向旧点位了。
 */
function resetSearchIndex() {
  searchIndex.value = null
}

/** 点位编码 → 点位 id。轻资产「装在主机里」那一支只给宿主编码，靠它换算成点位。 */
const stationIdByCode = computed<Record<string, number>>(() => {
  const out: Record<string, number> = {}
  for (const s of stations.value) out[s.code.toUpperCase()] = s.id
  return out
})

/** 轻资产是否被搜索词命中：品类名 / 品牌 / 规格（"ssd"、"256GB" 都该命中） */
function lightHoldingMatches(h: LightHolding, q: string, lower: string): boolean {
  if (matchByPinyin(h.category_name || '', q)) return true
  if (matchByPinyin(h.category_brand || '', q)) return true
  return (h.category_spec || '').toLowerCase().includes(lower)
}

/**
 * 一条配件可能落在哪些点位（**一个数组**，不是单个）。
 *
 * 三种确定来源：
 *   · `kind='workstation'`：东西就固定在这个工位 → `ref_id` 就是点位 id；
 *   · `kind='asset'`：装在主机里 → 主机**当前**所在点位（后端给 `host_workstation_code`）；
 *   · `kind='stock'`：库房且存放位置文本恰好是点位编码 → 后端已解析成 `stock_location_station_id`。
 *
 * 第四种是**推断**：
 *   · `kind='user'`：东西在人手上，本来没有位置；如果这个人名下只有一个点位，
 *     那几乎是它；如果有 N 个，**N 个都算候选**（用户口径：宁可多点亮两处，
 *     也不要因为"定不了"就什么都不显示）。后端已经把候选算好（`inferred_station_ids`），
 *     这里不再自己按人名做一遍匹配 —— 那是两套实现漂移的起点。
 *
 * ⚠️ 候选是"可能"，不是"就是"。所以调用方必须在文案里写清「按使用人推断」——
 * 用户照着虚线点位去找东西、找不到时，得有个解释。
 */
function lightHoldingStationIds(h: LightHolding): number[] {
  if (h.kind === 'workstation') return h.ref_id ? [h.ref_id] : []
  if (h.kind === 'asset') {
    const code = (h.host_workstation_code || '').toUpperCase()
    const id = code ? stationIdByCode.value[code] : undefined
    return id ? [id] : []
  }
  if (h.kind === 'stock') return h.stock_location_station_id ? [h.stock_location_station_id] : []
  if (h.kind === 'user') return h.inferred_station_ids ?? []
  return []
}

/**
 * 一次遍历算出两件事：**哪些点位该亮（带原因）** 与 **哪些命中不在图上**。
 *
 * 合成一个 computed 而不是分两个：资产 / 轻资产要跑同一套拼音匹配，
 * 分开算等于让 150 台资产每按键匹配两遍，还会给两处结果漂移的机会。
 */
const searchResult = computed<{ reasons: Map<number, SearchReason[]>; offMap: OffMapHit[] }>(() => {
  const reasons = new Map<number, SearchReason[]>()
  const offMap: OffMapHit[] = []
  const q = searchQuery.value.trim()
  if (!q) return { reasons, offMap }
  const lower = q.toLowerCase()

  const push = (id: number, reason: SearchReason) => {
    const list = reasons.get(id)
    if (!list) {
      reasons.set(id, [reason])
      return
    }
    // 同一个点位可能同时因"使用人"和"装了这台机器"命中，两条都留着 —— 那是两句话
    if (!list.some((r) => r.dim === reason.dim && r.label === reason.label)) list.push(reason)
  }

  // ① 点位编码 + 点位使用人。使用人走拼音（与台账筛选、资产表单同一个函数），
  //    编码走普通子串（w23 / W2 都该命中）。
  for (const s of stations.value) {
    if (s.code.toLowerCase().includes(lower)) push(s.id, { dim: 'station', label: `点位 ${s.code}` })
    if (matchByPinyin(s.user_name || '', q)) push(s.id, { dim: 'person', label: `使用人 ${s.user_name}` })
  }

  // ② 资产：**只按编号与品牌**（用户口径：其他字段不要）。
  //    资产编号 → 它当前挂在哪个点位。没挂点位的进「不在图上」，绝不猜。
  for (const a of searchIndex.value?.assets ?? []) {
    const codeHit = a.asset_code.toLowerCase().includes(lower)
    const brandHit = matchByPinyin(a.brand || '', q)
    if (!codeHit && !brandHit) continue
    const label = a.brand ? `${a.asset_code} · ${a.brand}` : a.asset_code
    if (a.workstation_id === null) {
      offMap.push({
        key: `asset-${a.id}`,
        label,
        hint: '未挂点位',
        // 台账筛选后的画面：把编号当 keyword 带过去，用户看到的就是"这一台"。
        // ⚠️ 台账列表的路由是 `/`（name='assets'），不是 `/assets` —— 后者只有详情页的 `/:id`。
        to: `/?keyword=${encodeURIComponent(a.asset_code)}`,
      })
      continue
    }
    push(a.workstation_id, { dim: 'asset', label })
  }

  // ③ 轻资产：品类命中后，位置由后端给的归属换算（见 lightHoldingStationIds）。
  for (const h of searchIndex.value?.light ?? []) {
    if (!lightHoldingMatches(h, q, lower)) continue
    const label = `${h.category_label || h.category_name} ×${h.quantity}`
    const ids = lightHoldingStationIds(h)
    if (!ids.length) {
      offMap.push({
        key: `light-${h.id}`,
        label,
        hint:
          h.kind === 'user'
            ? `${h.user || '某人'} 持有，且不在任何点位上`
            : h.stock_location
              ? `库存 · ${h.stock_location}`
              : '库存（未记录位置）',
        // 轻资产没有单件详情，落到品类详情页 —— 那里能看到这条持有记录的全貌
        to: `/light/${h.category_id}`,
      })
      continue
    }
    // 人持有的候选可能有多个（他名下有 N 个点位）：**每处都点亮**，
    // 但每一行都注明是推断 —— 用户要的是"可能在哪"，不是"系统替他选一个"。
    const isUser = h.kind === 'user'
    const candidates = (h.inferred_station_codes || []).join('、')
    const reasonLabel = isUser
      ? `${label} · ${h.user || '某人'} 持有，按使用人的 ${ids.length} 个点位推断（${candidates}）`
      : label
    for (const id of ids) push(id, { dim: 'light', label: reasonLabel, inferred: isUser })
  }

  return { reasons, offMap }
})

/** 命中原因表；模板里按点位 id 查 */
const searchReasons = computed(() => searchResult.value.reasons)

/** 仍然"亮"的点位 id。空集 = 有搜索词但一个都没命中 */
const matchedIds = computed(() => new Set(searchReasons.value.keys()))

/**
 * **只靠推断**才亮的点位（它的每一条命中理由都是 `inferred`）。
 *
 * 一个点位可能同时因"编码命中"和"推断的配件命中"亮起 —— 那它本身是确定的，
 * 不该画虚线。只有一条确定理由都没有时，才需要提醒用户"这块是按人推出来的"。
 */
const inferredOnlyIds = computed<Set<number>>(() => {
  const out = new Set<number>()
  for (const [id, reasons] of searchReasons.value) {
    if (reasons.length && reasons.every((r) => r.inferred)) out.add(id)
  }
  return out
})

/** 命中点位（按编码排，用项目里既有口径，别自己 localeCompare） */
const stationHits = computed<StationHit[]>(() => {
  const out: StationHit[] = []
  for (const [id, reasons] of searchReasons.value) {
    const station = stations.value.find((s) => s.id === id)
    if (station) out.push({ station, reasons })
  }
  return out.sort((a, b) => compareStationCode(a.station.code, b.station.code))
})

const offMapHits = computed(() => searchResult.value.offMap)

/** 索引没拉全时的实话实说 */
const truncationNote = computed(() => {
  const idx = searchIndex.value
  if (!idx) return ''
  const parts: string[] = []
  if (idx.assetTotal > idx.assets.length)
    parts.push(`资产共 ${idx.assetTotal} 台，仅搜了前 ${idx.assets.length} 台`)
  if (idx.lightTotal > idx.light.length)
    parts.push(`轻资产共 ${idx.lightTotal} 条，仅搜了前 ${idx.light.length} 条`)
  return parts.join('；')
})

/** 有没有正在生效的搜索词。用来区分"没搜"和"搜了但没命中" */
const hasQuery = computed(() => searchQuery.value.trim().length > 0)

/** 结果浮层贴住搜索框。宽度在窄屏上自动收，避免贴边溢出。 */
function placePanel() {
  const el = searchBoxEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const width = Math.min(420, window.innerWidth - 24)
  panelPos.value = {
    left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
    top: Math.round(rect.bottom + 6),
    width,
  }
}

function openPanel() {
  panelOpen.value = true
  void nextTick(placePanel)
}

function onSearchFocus() {
  void ensureSearchIndex()
  if (hasQuery.value) openPanel()
}

function onSearchInput() {
  void ensureSearchIndex()
  openPanel()
}

function clearSearch() {
  searchQuery.value = ''
  panelOpen.value = false
}

function closePanel() {
  panelOpen.value = false
}

/** 点到别处就收起浮层。用 document 上的 mousedown，而不是 input 的 blur ——
 *  blur 会先于结果行的 click 触发，点结果会点空。 */
function onDocMouseDown(event: MouseEvent) {
  if (!panelOpen.value) return
  const target = event.target as Node | null
  if (!target) return
  if (panelEl.value?.contains(target) || searchBoxEl.value?.contains(target)) return
  closePanel()
}

watch(panelOpen, (open) => {
  if (open) {
    window.addEventListener('resize', placePanel)
    // capture = true：顶栏自己横向滚动时也要重算，否则浮层会跟搜索框错位
    window.addEventListener('scroll', placePanel, true)
  } else {
    window.removeEventListener('resize', placePanel)
    window.removeEventListener('scroll', placePanel, true)
  }
})

/** 点命中行：跳到那个点位并打开详情。**不自动居中** —— 只有用户点了才移动视图。 */
function gotoStation(station: Workstation) {
  closePanel()
  centerOnStation(station)
  void select(station)
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    clearSearch()
    return
  }
  if (event.key === 'Enter' && stationHits.value.length) {
    event.preventDefault()
    gotoStation(stationHits.value[0].station)
  }
}

/* ---- 点位关系层（有人 × 有资产）---- */

/**
 * 关系层开关。
 *
 * **宽屏默认开着**：它就是这页的默认视角（管理员进来第一眼就该看到"哪片区域空着"），
 * 而不是藏在某个开关后面。留这个开关是因为它确实会让格子变花 ——
 * 用户看腻了想回到"只看有人/没人"的清爽两态时，得有路可退。
 *
 * ⚠️ **窄屏默认关着**，在 onMounted 里按断点改，不是在这里。
 * 四个关系格（开关 + 全部 + 四态）在 390px 上必然折两行，工具条实测被撑到 117px、
 * 固定区占比破 20%（验收 C1/C2 卡的就是这两条）。
 * 手机上这一页的主要用途本来就是"拿着走到点位盘点"，格局统计是次要的；
 * 开关仍然留着，想看格局的人点一下就能开（代价是多占一行，知情可选）。
 * ⚠️ 别把这句写回 `ref(true)` —— 那样又会被无脑改成常开。
 */
const relLayer = ref(true)

/** 关系筛选：'all' 或四选一。点了就只亮那一类 */
const relFilter = ref<'all' | RelationKey>('all')

/**
 * 四种关系的**点位计数**。
 *
 * 前端现算，不走后端 —— `stations` 里已经有 `asset_count`（后端派生下发的），
 * 两个布尔值一次遍历就够。为此再加一个接口就等于把同一份数据第二次端出来。
 */
const relCounts = computed<Record<RelationKey, number>>(() => {
  const out: Record<RelationKey, number> = {
    person_asset: 0,
    person_none: 0,
    none_asset: 0,
    none_none: 0,
  }
  for (const s of stations.value) out[relationOf(s)] += 1
  return out
})

/** 工作位 id → 关系。模板里逐个格子查，省得每次渲染重算 */
const relById = computed<Record<number, RelationKey>>(() => {
  const out: Record<number, RelationKey> = {}
  for (const s of stations.value) out[s.id] = relationOf(s)
  return out
})

/**
 * 某个点位在关系筛选下是否该变暗。
 *
 * 与搜索筛选（`isSearchDimmed`）、盘点筛选（`isDimmed`）是**三个独立的轴**，
 * 可以叠加：搜一个人 + 选"有人·无资产"，就是要看"这个人名下还没配设备的点位"。
 *
 * ⚠️ 盘点模式下**不让关系筛选生效**：那时的颜色是盘点结论，
 * 再叠一层筛选只会让人分不清是被筛掉的还是没盘过的。两个维度各管各的。
 */
function isRelDimmed(stationId: number): boolean {
  if (!relLayer.value || auditMode.value) return false
  if (relFilter.value === 'all') return false
  return relById.value[stationId] !== relFilter.value
}

/** 顶栏四格统计：数字直接取 relCounts，别在前端重数一遍 */
const relStats = computed(() =>
  RELATION_ORDER.map((key) => ({
    key,
    ...RELATION_META[key],
    count: relCounts.value[key],
  })),
)

/* ---- 盘点 ---- */
const openTasks = ref<InventoryTask[]>([])
const taskId = ref<number | null>(null)
const mapView = ref<MapInventoryView | null>(null)
const mapLoading = ref(false)
const auditFilter = ref<'all' | MapState>('all')
const markingIds = ref<number[]>([])

/* ---- 拖动 ---- */
const dragId = ref<number | null>(null)
const dragOff = { dx: 0, dy: 0 }
const movedDuringDrag = ref(false)
const dirtyIds = ref<Set<number>>(new Set())
const guides = ref<{ x: number | null; y: number | null }>({ x: null, y: null })

const selected = computed(() => stations.value.find((s) => s.id === selectedId.value) ?? null)
const selectedMap = computed<MapInventoryWorkstation | null>(() => {
  if (selectedId.value === null || !mapView.value) return null
  return mapView.value.workstations.find((w) => w.workstation_id === selectedId.value) ?? null
})
const selectedAssets = computed<Asset[]>(() =>
  selectedId.value === null ? [] : assetCache.value[selectedId.value] ?? [],
)

/** 盘点态查表：workstation_id → state */
const stateById = computed<Record<number, MapState>>(() => {
  const out: Record<number, MapState> = {}
  for (const w of mapView.value?.workstations ?? []) out[w.workstation_id] = w.state
  return out
})

/**
 * 顶部进度。
 *
 * ⚠️ **口径必须与原盘点页完全一致：`已盘 / 应盘`，异常单独算。**
 *
 * 一开始这里写的是 `(checked + abnormal) / total`，看着更"合理"（异常也算处理过了），
 * 但实测两页数字对不上：地图 40% vs 盘点页 20% —— 而"两页结果一致"正是这一页的验收标准。
 * 原盘点页的 progress 来自后端 `serialize_task` 的 `checked / total`，
 * 异常只出现在「差异」里，不计入进度。地图要做的是一模一样的口径，
 * 而不是自己发明一个更好看的算法 —— 那正是"又做了套平行系统"。
 */
const progress = computed(() => {
  const t = mapView.value?.task
  if (!t) return { total: 0, checked: 0, abnormal: 0, pct: 0 }
  const total = t.total || 0
  const checked = t.checked || 0
  return {
    total,
    checked,
    abnormal: t.abnormal || 0,
    pct: total ? Math.round((checked / total) * 100) : 0,
  }
})

/** 各状态的**点位**计数（含两种中性态）。直接取后端算好的，不在前端重算 */
const stateCounts = computed<Record<string, number>>(() => {
  const base: Record<string, number> = {
    all: stations.value.length,
    checked: 0,
    abnormal: 0,
    pending: 0,
    empty: 0,
    not_in_scope: 0,
  }
  const fromApi = mapView.value?.counts
  if (fromApi) {
    for (const key of Object.keys(base)) {
      if (key !== 'all' && typeof fromApi[key] === 'number') base[key] = fromApi[key]
    }
  }
  return base
})

/**
 * 顶栏那三个数字（**关系层关掉时**才显示）。
 *
 * ⚠️ **`free` 是「没有人」，不是「没有设备」** —— 它 = `total - assigned`，人员轴。
 * 后端那个"一台设备都没挂"的计数曾经叫 `empty`（2026-10-08 改名 `without_asset`），
 * **两个都叫"空闲"，指的却是两件事**，同一页上对不上且不报错。
 * 现在后端那个名字自带语义，这里这个也写清楚了，两边不会再互相串。
 *
 * 前端自己算而不是抄接口：`hasOwner()` 就在手边，而它才是这套口径的权威。
 */
const stats = computed(() => {
  const total = stations.value.length
  const assigned = stations.value.filter((s) => hasOwner(s.user_name)).length
  return { total, assigned, free: total - assigned }
})

/**
 * 是不是「全新用户」：底图一个图元都没有 **且** 一个点位都没有。
 *
 * 这两个条件缺一不可：
 *   - 只看图元 → 用户画了底图但还没加点位时，引导会一直杵在那儿，挡着画布；
 *   - 只看点位 → 用户没画底图但已经从别处导入了点位，此时该说的是"还没画底图"。
 * 所以分成两层文案（见 `blankHint`），别用一条万能提示糊过去。
 *
 * ⚠️ **刻意不把 `editMode` / `auditMode` 掺进条件。** 引导说的是"这里还什么都没有"，
 * 不是"你现在没在做什么模式"。用户点「开始建点位」进了调整位置模式、
 * 但一个点位都还没建 —— 引导**应该留着**（他才刚开始，正是最需要指引的时候），
 * 等落下第一个点位它自然消失。
 * 这样"引导何时消失"只有一个判据：**他有没有真的建出东西**。
 * 掺进模式会带来"一点就消失"这种反直觉行为（我第一版就写错了，还写进了注释）。
 */
const isBrandNew = computed(
  () => floorShapes.value.length === 0 && stations.value.length === 0,
)

/** 只有底图、没点位（反之亦然）：给更贴切的半句提示 */
const blankHint = computed(() => {
  if (!isBrandNew.value) return null
  const noShape = floorShapes.value.length === 0
  const noStation = stations.value.length === 0
  if (noShape && noStation) return { title: '这里还是一张空白画布', steps: '建点位 · 画底图' }
  if (noStation) return { title: '底图画好了，还没有点位', steps: '加点位' }
  if (noShape) return { title: '这些点位还没有底图', steps: '画底图' }
  return null
})

const canvasStyle = computed(() => ({
  width: `${canvas.value.width}px`,
  height: `${canvas.value.height}px`,
  transform: `translate(${tx.value}px, ${ty.value}px) scale(${scale.value})`,
}))

/* ========================================================================== */
/* 数据加载                                                                    */
/* ========================================================================== */

/** 去编辑底图（独立页）。点位位置在这页调，建筑图元在编辑器里调。 */
function openEditor() {
  router.push('/map/editor')
}

/**
 * 空画布引导的「开始建点位」：**一步到位**。
 *
 * 只调 `toggleEditMode()` 是不够的 —— 那样用户进到调整位置模式，
 * 但点画布不会加东西，还得自己再点一次「新增」。**两个按钮就够问了**。
 */
function startFromBlank() {
  editMode.value = true
}

async function loadMap() {
  loading.value = true
  try {
    const data = await workstationApi.map()
    canvas.value = data.canvas
    stations.value = data.items
    // 建筑图元来自后端（默认平面图）。解析走 parseLayout —— 它是唯一入口，
    // 组件里不做结构判断（读坏数据退化成空图，不白屏）。
    floorShapes.value = parseLayout(data.layout).shapes
    // 选中的点位被删掉了 → 清空侧栏，免得显示一个不存在的东西
    if (selectedId.value !== null && !data.items.some((s) => s.id === selectedId.value)) {
      selectedId.value = null
    }
  } catch (error) {
    if (import.meta.env.DEV) console.error(error)
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  try {
    const filters = await metaApi.filters()
    users.value = filters.users
  } catch {
    // 拿不到候选就退化成"只能手输"，不阻断地图
    users.value = []
  }
}

/**
 * 把 URL 里的 `?station=<code>` 对回去 —— 问题中心「去核对」深链的落点。
 *
 * ⚠️ 用 **code** 而不是 id：问题是按编码展示给人的，链接里写 id 谁也不知道指的是哪一格；
 * 而且点位编码软删除后**不释放**，链接长期有效。
 * ⚠️ 只对**一次**。对完之后用户在地图上点别的格子，URL 由 `select()` 接着维护，
 * 不需要（也不该）再被这个入口拽回去。
 */
let stationQueryApplied = false
async function applyStationFromQuery() {
  if (stationQueryApplied) return
  const want = String(route.query.station ?? '').trim()
  if (!want) return
  stationQueryApplied = true
  const hit = stations.value.find((s) => s.code === want)
  if (!hit) {
    // 点位可能刚被删掉 / 改了编码。说出来，别让人对着一个空侧栏猜
    ElMessage.info(`没找到点位 ${want} —— 它可能已被删除或改了编码`)
    return
  }
  await select(hit)
}

/** 打开进行中的盘点任务，并把 URL 里的 ?task= 对回去 */
async function loadTasks() {
  try {
    openTasks.value = await inventoryApi.tasks({ status: 'open' })
  } catch {
    openTasks.value = []
  }
  const wanted = Number(route.query.task)
  const exists = openTasks.value.some((t) => t.id === wanted)
  if (exists) {
    taskId.value = wanted
  } else if (openTasks.value.length) {
    // 默认选最新那个（接口按 id desc 给），符合"没选过时看最近一次"的直觉
    taskId.value = openTasks.value[0].id
  } else {
    taskId.value = null
  }
}

async function loadMapView() {
  if (taskId.value === null) {
    mapView.value = null
    return
  }
  mapLoading.value = true
  try {
    mapView.value = await mapInventoryApi.view(taskId.value)
  } catch (error) {
    mapView.value = null
    if (import.meta.env.DEV) console.error(error)
  } finally {
    mapLoading.value = false
  }
}

async function loadAssets(stationId: number, force = false) {
  if (!force && assetCache.value[stationId]) return
  assetsLoading.value = true
  try {
    const list = await workstationApi.assets(stationId)
    assetCache.value = { ...assetCache.value, [stationId]: list }
  } catch (error) {
    if (import.meta.env.DEV) console.error(error)
  } finally {
    assetsLoading.value = false
  }
}

function invalidateAssets(stationId: number) {
  const next = { ...assetCache.value }
  delete next[stationId]
  assetCache.value = next
}

/* ========================================================================== */
/* 选择与侧栏                                                                  */
/* ========================================================================== */

async function select(station: Workstation) {
  selectedId.value = station.id
  /**
   * 让 URL 里的 `?station=` 跟着走。
   *
   * 这一页的 query 约定是早就有的（`?task=`），这里只是补上"当前选中哪一格"。
   * 好处是**问题中心点「去核对」跳过来能直接定位到 W61**（见 `applyStationFromQuery`），
   * 而且刷新 / 把链接发给人，落到的是同一格。
   *
   * 用 `replace` 而不是 `push`：点几个格子就在历史里塞几条记录，会把"返回上一页"变得没法用。
   */
  if (String(route.query.station ?? '') !== station.code) {
    void router.replace({ query: { ...route.query, station: station.code } })
  }
  // 资产清单与关系核对**并行拉**：两者互不依赖，串行会让侧栏分两次"跳"出来
  await Promise.all([loadAssets(station.id), loadRelationCheck(station.id)])
  // 新增模式下点已有点位：**这个位置已经有主了**。说出来 ——
  // 否则用户"想在这儿加一个"点下去只看到侧栏变了，会以为新增又失败了
  // （2026-10-12 真实用户踩坑，`.ws` 上有 `@click.stop`，
  //  所以在 `onStageClick` 里加提示是无效的 —— 事件根本冒泡不上来）。
  if (adding.value) {
    ElMessage.info(`这个位置已经有点位 ${station.code} 了。想加新的，请点空白处`)
  }
}

function closeSidebar() {
  selectedId.value = null
  relationCheck.value = null
}

/* ========================================================================== */
/* 关系核对（点位使用人 ↔ 设备使用人）                                          */
/* ========================================================================== */

/**
 * 关系核对结果。
 *
 * ⚠️ **它是派生结果，后端每次现算，前端也只当快照用** ——
 * 没有任何"本地改一改"的路径，确认 / 撤销例外一律由后端返回新结果覆盖。
 * 这样"界面上显示的结论"永远等于"后端按当前数据算出来的结论"。
 */
const relationCheck = ref<StationRelationCheck | null>(null)
const relationLoading = ref(false)

async function loadRelationCheck(stationId: number) {
  relationLoading.value = true
  try {
    relationCheck.value = await relationCheckApi.workstation(stationId)
  } catch (error) {
    relationCheck.value = null
    if (import.meta.env.DEV) console.error(error)
  } finally {
    relationLoading.value = false
  }
}

/** 卡片里确认 / 撤销例外之后，后端会把**刷新后的整份结论**回来，直接覆盖即可 */
function onRelationUpdated(check: StationRelationCheck) {
  relationCheck.value = check
}

function refreshRelationCheck() {
  if (selectedId.value !== null) loadRelationCheck(selectedId.value)
}

/* ========================================================================== */
/* 迁移                                                                       */
/* ========================================================================== */

const relocationOpen = ref(false)
const relocationMode = ref<RelocationMode>('assets')
const relocationPreset = ref<number[]>([])

/** 打开迁移对话框。带 assetId = 单台资产换点位；不带 = 整个点位业务迁移。 */
function openRelocation(payload: { mode: 'assets' | 'station'; assetId?: number }) {
  if (selectedId.value === null) return
  relocationMode.value = payload.mode
  relocationPreset.value = payload.assetId ? [payload.assetId] : []
  relocationOpen.value = true
}

/**
 * 迁移完成后的刷新。
 *
 * 迁移同时动了两样东西：`assets.workstation_id`（设备归属）和
 * 可能的点位使用人 / 点位退役。所以地图、侧栏资产缓存、关系核对**都要重来** ——
 * 少刷一个就会出现"地图上设备还在老位置"这种自相矛盾的画面。
 */
async function onRelocationApplied(result: RelocationApplyResult) {
  await loadMap()
  // 资产的 `workstation_id` 变了 → 搜索索引里那份归属已经过期，丢掉重拉
  resetSearchIndex()
  if (selectedId.value !== null) {
    await Promise.all([loadAssets(selectedId.value, true), loadRelationCheck(selectedId.value)])
    const mine = result.relation_checks.find((c) => c.workstation_id === selectedId.value)
    if (mine) relationCheck.value = mine
  }
  if (auditMode.value) await loadMapView()
}

/* ========================================================================== */
/* 视图：缩放 / 平移                                                           */
/* ========================================================================== */

/**
 * 窄屏（手机 / 竖屏平板）判定。
 *
 * 和 CSS 里的 `@media (max-width: 900px)` **必须是同一个断点** ——
 * JS 这边管"画布怎么摆"，CSS 那边管"抽屉长什么样"，两边对不上就会出现
 * "按桌面布局算的居中 + 按手机布局画的抽屉"叠在一起。
 */
const compact = ref(false)
let compactMq: MediaQueryList | null = null

/**
 * 窄屏下"点位详情"抽屉**浮在画布上**，画布的可视高度没变、但下半截被盖住了。
 * 取它的实际高度，让 fit() / 定位把这一截让出来，否则居中的画布有一半藏在抽屉后面，
 * 用户看着就是"地图没居中 / 被裁了"。
 *
 * 用 offsetParent 判可见（`display:none` 时它是 null）—— 没选中点位时抽屉不渲染，
 * 这时候不应该让出任何高度。
 */
function sheetInset(): number {
  if (!compact.value) return 0
  const el = sheetEl.value
  if (!el || el.offsetParent === null) return 0
  return el.offsetHeight
}

/**
 * 上一次算出的"可用尺寸"。用来避免无谓的重算：
 * 手机地址栏收起 / 软键盘弹出都会触发 window resize，但画布尺寸其实没变，
 * 这时候重跑 fit() 等于把用户刚缩放好的视图清掉。
 */
let lastFitKey = ''

function fit() {
  const el = vp.value
  if (!el) return
  const inset = sheetInset()
  const availableW = el.clientWidth - 40
  const availableH = el.clientHeight - inset - 40
  lastFitKey = `${el.clientWidth}x${el.clientHeight}x${inset}`
  // 适应视图时最多放到 1.5 —— 再大就只剩几块点位，不如留点全局感
  scale.value = clampScale(
    Math.min(1.5, Math.min(availableW / canvas.value.width, availableH / canvas.value.height)),
  )
  tx.value = (el.clientWidth - canvas.value.width * scale.value) / 2
  ty.value = (el.clientHeight - inset - canvas.value.height * scale.value) / 2
}

/** window resize 的入口：尺寸真变了才重算，否则只是地址栏收放，别动用户的视图 */
function onWindowResize() {
  const el = vp.value
  if (!el) return
  const key = `${el.clientWidth}x${el.clientHeight}x${sheetInset()}`
  if (key === lastFitKey) return
  fit()
}

/**
 * 窄屏点击点位后：**保缩放、只平移**，把选中的点位挪到抽屉上方那块可见区的中央。
 *
 * 这里刻意**不调 fit()**。抽屉占掉近六成高度，按剩余高度去重新 fit 会把缩放压到
 * 0.2 上下，点位缩成十几像素、手指根本点不中 —— 恰好把"按空间点位盘点"这件事做废。
 * 缩放保持不动、只把画布平移过去，用户手里的地图还是原来那个大小，指哪打哪。
 */
function centerOnStation(s: Workstation) {
  const el = vp.value
  if (!el) return
  const visibleH = el.clientHeight - sheetInset()
  // 点位左上角 → 中心（.ws 是 80×56）
  const cx = (s.x + 40) * scale.value
  const cy = (s.y + 28) * scale.value
  let nextTx = el.clientWidth / 2 - cx
  let nextTy = visibleH / 2 - cy

  // 别把画布整体推出视野：画布比可视区大时，四周各留 60px 边距就停
  const cw = canvas.value.width * scale.value
  const chh = canvas.value.height * scale.value
  if (cw > el.clientWidth) {
    nextTx = Math.min(60, Math.max(el.clientWidth - cw - 60, nextTx))
  }
  if (chh > visibleH) {
    nextTy = Math.min(60, Math.max(visibleH - chh - 60, nextTy))
  }
  tx.value = nextTx
  ty.value = nextTy
}

function zoomBy(factor: number) {
  scale.value = clampScale(scale.value * factor)
}

function onWheel(event: WheelEvent) {
  event.preventDefault()
  const el = vp.value
  if (!el) return
  const old = scale.value
  const next = clampScale(old * (event.deltaY < 0 ? ZOOM_STEP_WHEEL : 1 / ZOOM_STEP_WHEEL))
  const rect = el.getBoundingClientRect()
  const cx = event.clientX - rect.left
  const cy = event.clientY - rect.top
  // 以鼠标为锚点缩放：光标下的那个点保持不动
  tx.value = cx - (cx - tx.value) * (next / old)
  ty.value = cy - (cy - ty.value) * (next / old)
  scale.value = next
}

/* --------------------------------------------------------------------------
   手势：单指拖动平移 + **双指捏合缩放**（触屏 / 鼠标都走 pointer 事件）

   平移这块之前是**空的** —— `spacePressed` 只换了个 `cursor: grab` 光标，没有任何
   真正移动画布的代码，等于"空格临时平移"只是句注释。桌面宽屏下画布本来就
   fit 得下、看不出问题；手机上一缩到 0.36 就非常致命：只能看，不能移，
   而"按点位盘点"恰恰需要凑近那个点位去点。

   双指缩放是后补的：第一版只做了平移，手机上的表现就是"只能一根手指拖，捏不动"。
   想放大只能去点工具条上的 ± 按钮，一次 15%、还得连点七八下，纯残废。
   根因是原来的 `onPanStart` 把**所有** pointerdown 都当成同一根手指处理：第二根
   指头落下只会把平移起点覆盖掉，两指同向滑就变成了缓慢平移，反方向滑就原地打架。

   现在改成维护一张"当前按在视口上的指针表"（pointerId → 坐标）：
   - 1 根手指  → 平移，和以前一模一样
   - 2 根手指  → 捏合缩放，以**两指中点**为锚点（中点下的画布位置全程不动）
   - ≥3 根      → 抬起一根后重新取基准，不让中点瞬移
   用 pointer 事件而不是 mouse + touch 两套：一套代码同时吃鼠标 / 触摸 / 触控笔，
   配合 `.viewport { touch-action: none }` 才能把触摸手势从浏览器的滚动里抢过来。
   配合 setPointerCapture，手指滑出视口边界也不会丢事件（和拖点位用 document
   级 mouseup 是同一个理由，见 onMounted 里的注释）。
   -------------------------------------------------------------------------- */

/** 当前按在视口上的指针：pointerId → 视口坐标。多指手势全靠这张表 */
const pointers = new Map<number, { x: number; y: number; pan: boolean }>()

/** 双指缩放的起手快照。`ax/ay` 是**起手中点下的画布坐标**，缩放全程它都钉在手指中点下面 */
const pinch = { active: false, dist: 0, scale: 1, ax: 0, ay: 0, ox: 0, oy: 0 }

/** 这一轮手势里发生过捏合。两个用途：挡掉抬手时浏览器补发的 click（新增模式下会凭空
 *  多一个点位），以及让"捏合后剩下的那根手指"照样能平移 —— 见 onPanEnd。
 *  一轮手势 = 从第一根手指落下到全部抬起，所以它在 onPanStart 里（size===0 时）清空。 */
let gesturePinched = false

/** 两指间距与中点（屏幕坐标；间距是差值，不受视口位置影响） */
function pinchMetrics() {
  const [a, b] = [...pointers.values()]
  return {
    dist: Math.hypot(a.x - b.x, a.y - b.y),
    mx: (a.x + b.x) / 2,
    my: (a.y + b.y) / 2,
  }
}

/**
 * 这一轮手势里指针**真的移动过**（超过阈值就算）。
 *
 * 存在的唯一理由：新增模式现在允许平移（见 `canPan`），而"拖一下松手"
 * 浏览器可能补一个 click —— 那一下会把点位建在松手位置，用户却只是想挪视野。
 * 所以这里记账，`onStageClick` 见到它就当这轮不是"想加点位"。
 *
 * **阈值 6px**：低于它算"手抖的轻点"（仍算新增，用户就是要点），
 * 高于它就是"我在拖地图"。和 `dragId` 那个拖动阈值同一个量级。
 */
let movedDuringPan = false
/** 超过这个位移就当"在拖"，不再是"点" */
const PAN_MOVE_THRESHOLD = 6

/**
 * 这根指针是否被允许用来平移。
 *
 * ⚠️ **新增模式下也允许平移**（2026-10-12 修，真实用户踩坑）：
 * 以前这里是 `if (adding.value) return false` 一刀切，结果**新增模式下地图完全拖不动** ——
 * 用户想加一个**视野外**的点位，必须先退出新增、把地图拖过去、再进新增、再点，
 * 三四步才能加一个点位。这就是"新增很困难"的另一半原因（另一半是 `.room` 拦截）。
 *
 * 允许平移不会误加点位：拖动距离大时浏览器**不补发 click**
 * （见 MEMORY 里那条双击坑：实测 0ms 必吞、200ms 起恢复），
 * 真正还会触发 click 的只有"几乎没动"的轻点 —— 而那正是用户想要的。
 *
 * 落在已有点位上依旧不给平移：那是选中 / 拖点位的事（`onStationMouseDown`）。
 */
function canPan(event: PointerEvent) {
  if (spacePressed.value) return true
  if ((event.target as HTMLElement).closest('.ws')) return false
  if (adding.value) return true
  return !editMode.value
}

function beginPinch() {
  const el = vp.value
  if (!el) return
  const { dist, mx, my } = pinchMetrics()
  // 两指几乎重合时方向量不准，比值会疯跳 —— 等它们分开再定基准
  if (dist < 16) return
  const rect = el.getBoundingClientRect()
  pinch.active = true
  pinch.dist = dist
  pinch.scale = scale.value
  pinch.ax = (mx - rect.left - tx.value) / scale.value
  pinch.ay = (my - rect.top - ty.value) / scale.value
  // 手势期间视口不会动，rect 缓存下来 —— 每次 move 都 getBoundingClientRect 会强制重排
  pinch.ox = rect.left
  pinch.oy = rect.top
  panning.value = false // 两颗手指了，已经不是平移
  gesturePinched = true
  for (const id of pointers.keys()) el.setPointerCapture?.(id)
}

function applyPinch() {
  const { dist, mx, my } = pinchMetrics()
  if (pinch.dist < 16) return
  const next = clampScale(pinch.scale * (dist / pinch.dist))
  const sx = mx - pinch.ox
  const sy = my - pinch.oy
  scale.value = next
  // 锚点不动 = 手指中点下面那块地图"长"在手指上
  tx.value = sx - pinch.ax * next
  ty.value = sy - pinch.ay * next
}

function onPanStart(event: PointerEvent) {
  // 只认左键 / 触摸 / 触控笔；右键留给浏览器菜单（也不许混进捏合的那张表里）
  if (event.pointerType === 'mouse' && event.button !== 0) return

  const el = event.currentTarget as HTMLElement
  // 全部手指都抬起了 = 上一轮手势结束，把标记清掉
  if (pointers.size === 0) {
    gesturePinched = false
    movedDuringPan = false
  }

  pointers.set(event.pointerId, {
    x: event.clientX,
    y: event.clientY,
    pan: canPan(event),
  })

  // 第二根手指落下：立刻从"平移"切成"捏合"
  if (pointers.size === 2) {
    beginPinch()
    return
  }
  if (pointers.size > 2) return // 三指以上不参与计算，交给 onPanEnd 重新取基准
  if (!pointers.get(event.pointerId)!.pan) return

  panning.value = true
  panStart.x = event.clientX
  panStart.y = event.clientY
  panStart.tx = tx.value
  panStart.ty = ty.value
  // 捕获指针：手指滑出视口也能继续收到 move，松手不会"粘"在平移态
  el.setPointerCapture?.(event.pointerId)
}

function onPanMove(event: PointerEvent) {
  const current = pointers.get(event.pointerId)
  if (current) {
    current.x = event.clientX
    current.y = event.clientY
  }

  // 单指平移时记账"这一轮真的移动过"—— 给 onStageClick 用来区分
  // "用户想加点位"和"用户只是想挪一下视野"（见 movedDuringPan 的注释）
  if (panning.value && pointers.size === 1) {
    const dx = event.clientX - panStart.x
    const dy = event.clientY - panStart.y
    if (Math.hypot(dx, dy) > PAN_MOVE_THRESHOLD) movedDuringPan = true
  }

  // 双指（或更多）：缩放优先，不再平移
  if (pointers.size >= 2) {
    if (!pinch.active) beginPinch()
    if (pinch.active) applyPinch()
    return
  }

  if (!panning.value) return
  tx.value = panStart.tx + (event.clientX - panStart.x)
  ty.value = panStart.ty + (event.clientY - panStart.y)
}

function onPanEnd(event: PointerEvent) {
  const el = event.currentTarget as HTMLElement
  pointers.delete(event.pointerId)
  if (el?.hasPointerCapture?.(event.pointerId)) el.releasePointerCapture(event.pointerId)

  // 三指里抬起一根：缩放结果保留，但基准要重取，否则中点会瞬移一下
  if (pointers.size >= 2) {
    pinch.active = false
    beginPinch()
    return
  }
  pinch.active = false

  if (pointers.size === 0) {
    panning.value = false
    return
  }

  // 双指变单指：剩下这根手指接着拖。
  //
  // 这里比单指起手**放宽**了：只要这一轮手势捏合过，剩下的手指一律能平移 ——
  // 哪怕它起手时落在点位上。理由是点位几乎铺满画布，捏合放大地图后随手松一根
  // 想接着拖的概率很高，按单指那套"落在点位上就不给平移"会让手指变成死的
  // （实测第一版就是这样：26 个用例里唯独这条 FAIL）。
  // 不会因此误拖点位：搬点位走的是 mousedown，触摸在移动过程中浏览器不会补发它。
  const [id, rest] = [...pointers.entries()][0]
  if (rest.pan || gesturePinched) {
    panning.value = true
    panStart.x = rest.x
    panStart.y = rest.y
    panStart.tx = tx.value
    panStart.ty = ty.value
    el.setPointerCapture?.(id)
  } else {
    panning.value = false
  }
}

function onStationMouseDown(event: MouseEvent, station: Workstation) {
  if (adding.value || spacePressed.value || event.button !== 0 || !editMode.value) return
  event.preventDefault()
  const el = vp.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  // 坐标换算必须扣掉平移、再除以缩放，否则缩放/平移后拖拽会错位
  dragOff.dx = (event.clientX - rect.left - tx.value) / scale.value - station.x
  dragOff.dy = (event.clientY - rect.top - ty.value) / scale.value - station.y
  dragId.value = station.id
  movedDuringDrag.value = false
  void select(station)
}

function onMouseMove(event: MouseEvent) {
  if (dragId.value === null || !editMode.value) return
  const el = vp.value
  if (!el) return
  const station = stations.value.find((s) => s.id === dragId.value)
  if (!station) return

  const rect = el.getBoundingClientRect()
  const rawX = (event.clientX - rect.left - tx.value - dragOff.dx * scale.value) / scale.value
  const rawY = (event.clientY - rect.top - ty.value - dragOff.dy * scale.value) / scale.value

  // 对齐基准：其他点位的真实坐标（不是只吸网格 —— 用户要的是"和同一排/列对齐"）
  const anchorsX: number[] = []
  const anchorsY: number[] = []
  for (const s of stations.value) {
    if (s.id === dragId.value) continue
    anchorsX.push(s.x)
    anchorsY.push(s.y)
  }

  const sx = snapAxis(rawX, anchorsX)
  const sy = snapAxis(rawY, anchorsY)
  const clamped = clampToCanvas(sx.v, sy.v, canvas.value)

  if (Math.abs(clamped.x - station.x) > 1 || Math.abs(clamped.y - station.y) > 1) {
    movedDuringDrag.value = true
  }
  station.x = clamped.x
  station.y = clamped.y
  guides.value = { x: sx.hit, y: sy.hit }
}

function onMouseUp() {
  if (dragId.value === null) return
  const id = dragId.value
  dragId.value = null
  guides.value = { x: null, y: null }
  if (movedDuringDrag.value) {
    dirtyIds.value = new Set(dirtyIds.value).add(id)
  }
  movedDuringDrag.value = false
}

/**
 * 保存布局。**一次提交 = 1 条审计**。
 *
 * 「调整位置」是拖着反复微调，一次排版几十次松手；逐次提交会让审计页被布局记录刷屏，
 * 真正的资产操作被淹掉。所以这里只提交**真的动过**的点位，且只在用户点保存时发一次请求。
 */
async function saveLayout() {
  if (!dirtyIds.value.size) {
    ElMessage.info('布局没有变化')
    return
  }
  const items = stations.value
    .filter((s) => dirtyIds.value.has(s.id))
    .map((s) => ({ id: s.id, x: s.x, y: s.y }))
  saving.value = true
  try {
    const result = await workstationApi.saveLayout(items, appState.operator || null)
    dirtyIds.value = new Set()
    ElMessage.success(`已保存 ${result.updated} 个点位的位置`)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

function toggleEditMode() {
  if (editMode.value && dirtyIds.value.size) {
    ElMessage.warning('有未保存的位置调整，先点「保存布局」')
    return
  }
  editMode.value = !editMode.value
}

function setAdding(on: boolean) {
  adding.value = on
}

/**
 * 手机端「更多」下拉的分发。
 *
 * 每一项都直接调**宽屏那几个按钮调的同一个函数**，没有第二套实现 ——
 * 否则"手机上加的点位和桌面加的点位行为不一样"这种 bug 迟早会出现。
 */
function onTopbarCommand(cmd: string) {
  if (cmd === 'add') setAdding(!adding.value)
  else if (cmd === 'remove') {
    if (selected.value) void removeStation(selected.value)
  } else if (cmd === 'bulk') toggleBulkPanel()
  else if (cmd === 'export') exportJson()
  else if (cmd === 'editor') openEditor()
  else if (cmd === 'edit') toggleEditMode()
  else if (cmd === 'save') void saveLayout()
}

function onStageClick(event: MouseEvent) {
  if (!adding.value) return
  // 这一轮手势里捏合过：浏览器还会补一个 click，别让它凭空放出一个点位
  if (gesturePinched) {
    gesturePinched = false
    return
  }
  // ⚠️ 这一轮**平移过**就不当它是新增（2026-10-12 加）。
  // 新增模式现在允许平移了（见 canPan 注释），所以"拖一下地图松手"这类操作
  // 也可能被浏览器补一个 click 过来 —— 那一下会把点位建在**松手的位置**，
  // 而用户明明只是想挪个视野。用"这一轮 pointer 有没有真的移动过"挡掉。
  if (movedDuringPan) {
    movedDuringPan = false
    return
  }
  const target = event.target as HTMLElement

  // ⚠️ **房间不再拦截新增**（2026-10-12 修，真实用户踩坑）：
  // 以前这里是 `if (target.closest('.ws') || target.closest('.room')) return`，
  // 而 `.room` 是纯装饰的房间矩形 —— 它没有任何点击行为，排除它**没有任何好处**。
  // 坏处却极大：真实库 8 个房间盖住画布一大半，**用户在房间里点新增 = 什么都没发生、
  // 连个提示都没有**。实测采样 40 个点，20 个（正好一半）落在这两类元素上，
  // 用户连点三五次以为系统坏了，其实他只是点在房间里。
  // **房间就是桌子所在的地方** —— 那里恰恰是最该加点位的位置。
  //
  // ⚠️ 这里**不再判断 `.ws`**：点位自己有 `@click.stop="select(station)"`，
  // 事件压根冒泡不到这里（提示挪到 `select()` 里去了）。
  const el = vp.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const px = (event.clientX - rect.left - tx.value) / scale.value
  const py = (event.clientY - rect.top - ty.value) / scale.value
  const pos = clampToCanvas(px, py, canvas.value)
  void createStation(pos.x, pos.y)
}

/**
 * 新增点位。**编码由后端生成** —— 前端按数量+1 算会并发撞号。
 *
 * 提示里带一个「撤销」按钮：这一步是**立刻写库**的（没有草稿态 / 没有保存按钮），
 * 点歪了只能手动删回来，而"删回来"要过确认框、还可能撞上资产归属。
 * 与「批量生成」同一口径：**立即生效的操作，都要给一步后悔药**。
 */
async function createStation(x: number, y: number) {
  try {
    const created = await workstationApi.create({
      x,
      y,
      facing: 'down',
      operator: appState.operator || null,
    })
    stations.value = [...stations.value, created]
    adding.value = false
    await select(created)
    ElMessage({
      type: 'success',
      duration: 6000,
      // 刚建出来的点位必然没有资产，撤销就是干净地软删除 —— 不用再问一遍
      message: h('span', { class: 'undo-msg' }, [
        `已新增点位 ${created.code}`,
        h(
          'button',
          {
            class: 'undo-inline',
            onClick: async () => {
              const cur = stations.value.find((s) => s.id === created.id)
              if (!cur) return
              const ok = await deleteStation(cur)
              if (ok) ElMessage.success(`已撤销新增（${created.code} 已删除）`)
            },
          },
          '撤销',
        ),
      ]),
    })
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

/**
 * 删除点位。点位上还有资产时后端会 409 并列出设备 ——
 * 这里把那句话转达成人话，并给一个"同时清空归属"的选项。
 *
 * ⚠️ 「同时清空」也**不会删资产**，只是把那些设备的点位置空。
 * 点位只是资产的位置标签，为了删标签而删掉被标记的东西，因果是反的。
 */
async function removeStation(station: Workstation) {
  const attached = station.asset_count
  try {
    if (attached > 0) {
      const label = hasOwner(station.user_name) ? userLabel(station.user_name) : ''
      await ElMessageBox.confirm(
        `点位 ${station.code}${label ? `（${label}）` : ''} 上还挂着 ${attached} 台设备。` +
          `清空归属后设备仍保留在台账里，只是不再属于任何点位。`,
        '这个点位上有设备',
        { confirmButtonText: '清空归属并删除', cancelButtonText: '取消', type: 'warning' },
      )
    } else {
      await ElMessageBox.confirm(`删除点位 ${station.code}？`, '确认删除', {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      })
    }
  } catch {
    return // 用户取消
  }
  await deleteStation(station)
}

/**
 * 真正删（软删除），**不弹确认** —— 确认是调用方的事。
 *
 * 拆出来是因为「删除单个」与「撤销批量生成」要做同一件事，
 * 但后者**不能**再逐条弹确认（撤销 24 个就是 24 个弹窗，用户会把窗口关掉）。
 * 两处共用这一个实现，避免"以后改了一处漏了另一处"。
 */
async function deleteStation(station: Workstation): Promise<boolean> {
  const attached = station.asset_count
  try {
    const result = await workstationApi.remove(station.id, {
      operator: appState.operator || null,
      force: attached > 0,
    })
    stations.value = stations.value.filter((s) => s.id !== station.id)
    if (selectedId.value === station.id) selectedId.value = null
    invalidateAssets(station.id)
    if (result.detached_assets?.length) {
      ElMessage.success(`已删除 ${station.code}，${result.detached_assets.length} 台设备保留在台账中`)
    } else {
      ElMessage.success(`已删除点位 ${station.code}`)
    }
    if (auditMode.value) await loadMapView()
    return true
  } catch (error) {
    ElMessage.error(apiMessage(error))
    return false
  }
}

/* ========================================================================== */
/* 批量生成点位（在「调整位置」模式里）                                       */
/* ========================================================================== */

/**
 * 批量生成。**一次操作 = 1 条审计**（后端 `POST /api/workstations/bulk` 里合批记）。
 *
 * 三层防护，按顺序：
 *  ① 面板顶部常驻一条「会立刻写库」的提示 —— 它不是草稿，不点保存也生效；
 *  ② 点「生成」弹确认框，把号段 / 排布 / 数量摊开给用户看一遍；
 *  ③ 生成后出现「撤销生成」，走软删除撤回。
 *
 * 为什么这么啰嗦：这个功能曾经在「编辑底图」页，紧挨着「新增图元」
 * （草稿语义、不保存就白搭），长得一样但语义相反。用户"就点了试试"，
 * 24 个点位直接落库、退出时连确认框都没弹（layout 没变 → 不算脏）。
 * 那次的教训见 CURRENT_STATE 决策 67。
 */
async function runBulk() {
  if (!bulkValid.value || bulkBusy.value) return

  const range = bulkPreview.value
  const rows = Math.ceil(bulk.value.count / bulk.value.cols)
  try {
    await ElMessageBox.confirm(
      `将立刻往数据库里写入 ${bulk.value.count} 个点位（${range}）。\n\n` +
        `排布：${rows} 行 × ${bulk.value.cols} 列，起点 (${bulk.value.origin_x}, ${bulk.value.origin_y})，` +
        `横距 ${bulk.value.step_x} / 纵距 ${bulk.value.step_y}。\n\n` +
        '生成后可以在面板里点「撤销生成」撤回。',
      '批量生成点位',
      { confirmButtonText: `生成 ${bulk.value.count} 个`, cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return // 用户取消
  }

  bulkBusy.value = true
  try {
    const result = await workstationApi.bulk({
      ...bulk.value,
      start_code: (bulk.value.start_code || '').trim() || null,
      operator: appState.operator || null,
    })
    stations.value = [...stations.value, ...result.items]
    lastBulk.value = { codes: result.codes, items: result.items.map((i) => ({ id: i.id })) }
    ElMessage.success(`已生成 ${result.created} 个点位（${result.first_code} ~ ${result.last_code}）`)
    // 新点位可能落在当前视野外，重新 fit 一次让用户直接看到
    await nextTick()
    fit()
  } catch (error) {
    // 撞码 / 格式错都会走到这里，后端给的人话直接显示
    ElMessage.error(apiMessage(error))
  } finally {
    bulkBusy.value = false
  }
}

/** 撤销生成：逐个软删除。部分失败继续删剩下的，最后如实报告。 */
async function undoBulk() {
  if (!lastBulk.value || bulkUndoBusy.value) return
  const batch = lastBulk.value
  try {
    await ElMessageBox.confirm(
      `把刚生成的 ${batch.codes.length} 个点位（${batch.codes[0]} ~ ${batch.codes[batch.codes.length - 1]}）删掉？\n` +
        '是软删除：编码会被它们继续占用，不会释放给别人。',
      '撤销生成',
      { confirmButtonText: '撤销生成', cancelButtonText: '算了', type: 'warning' },
    )
  } catch {
    return
  }

  bulkUndoBusy.value = true
  const failed: number[] = []
  const removed = new Set<number>()
  for (const item of batch.items) {
    // 复用 deleteStation（已拆出无确认版本），保证与单个删除同一套行为
    const target = stations.value.find((s) => s.id === item.id)
    if (!target) {
      removed.add(item.id) // 已经不在列表里 = 不存在了
      continue
    }
    if (await deleteStation(target)) removed.add(item.id)
    else failed.push(item.id)
  }

  if (failed.length === 0) {
    lastBulk.value = null
    ElMessage.success(`已撤销 ${removed.size} 个点位`)
  } else {
    lastBulk.value = {
      codes: batch.codes.slice(batch.codes.length - failed.length),
      items: batch.items.filter((i) => failed.includes(i.id)),
    }
    ElMessage.warning(`已撤销 ${removed.size} 个，还有 ${failed.length} 个没删掉，可以再试一次`)
  }
  bulkUndoBusy.value = false
}

/** 打开/关闭批量生成面板。关的时候把面板收干净，别让上次的撤销条赖着。 */
function toggleBulkPanel() {
  bulkOpen.value = !bulkOpen.value
  if (!bulkOpen.value) lastBulk.value = null
}

/**
 * 「给这个点位挂一台新设备」→ 跳到台账并打开新增表单，预选好这个点位。
 *
 * 为什么不是地图弹一个"挑设备"的窗：那需要在弹窗里塞一个设备选择器，
 * 而设备有一百多台、带筛选和分页，等于把台账再写一遍。
 * 借道 URL（`/?new=1&workstation=23`）交给台账页处理，两边都只有一条路径。
 */function openCreateOnStation(stationId: number) {
  const q = taskId.value ? `&task=${taskId.value}` : ''
  const back = encodeURIComponent(`/map${taskId.value ? `?task=${taskId.value}` : ''}`)
  router.push(`/?new=1&workstation=${stationId}&back=${back}`)
}

/* ========================================================================== */
/* 盘点                                                                        */
/* ========================================================================== */

async function onTaskChange(id: number) {
  taskId.value = id
  // 任务写进 URL：刷新、收藏、发链接都不丢（与台账筛选进 URL 同一口径）
  router.replace({ query: { ...route.query, task: String(id) } })
  await loadMapView()
}

/** 打开 / 关闭盘点模式。关闭时清掉筛选，免得下次打开还带着上次的过滤 */
function toggleAuditMode() {
  auditMode.value = !auditMode.value
  if (!auditMode.value) auditFilter.value = 'all'
}

/**
 * 标记一台设备。**走的是现有 inventory 的 mark 接口**，不是地图自己的接口 ——
 * 所以这一步产生的 `inventory_items.result`、`asset_events`、审计记录
 * 与原盘点页标记完全一样。这正是"两页结果一致"的根据。
 *
 * `note` 是「标记异常」时采集的备注：**必须透传**，否则地图这条路写进
 * `inventory_items.note` 的永远是空值，导出的「盘点备注」列自然也就空着。
 * 备注的采集在 `MapStationPanel.markAbnormal`（点按钮那一步），这里只负责送出去。
 */
async function markAsset(
  assetId: number,
  result: 'checked' | 'abnormal' | 'pending',
  note?: string | null,
) {
  if (taskId.value === null) return
  markingIds.value = [...markingIds.value, assetId]
  try {
    await inventoryApi.mark(taskId.value, {
      asset_id: assetId,
      result,
      operator: appState.operator || null,
      note: note ?? null,
    })
    await loadMapView()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    markingIds.value = markingIds.value.filter((id) => id !== assetId)
  }
}

/**
 * 一个点位上"属于本次任务"的设备全部标为同一状态（逐台调同一个 mark 接口）。
 *
 * 这条路径**不采集备注**（一键标「已盘」没有可写的东西）；「标记异常」不走这里，
 * 它由面板逐台弹框采集备注。所以这里的 `note` 永远是空 —— 语义上是对的。
 */
async function markStation(workstationId: number, result: 'checked' | 'abnormal') {
  const row = mapView.value?.workstations.find((w) => w.workstation_id === workstationId)
  if (!row?.assets.length) return
  const pending = row.assets.filter((a) => a.result !== result)
  if (!pending.length) return
  for (const asset of pending) {
    await markAsset(asset.asset_id, result)
  }
}

function setAuditFilter(key: 'all' | MapState) {
  auditFilter.value = key
}

/** 关系筛选。再点同一格 = 取消筛选（"全部"是显式项，但取消更符合直觉） */
function setRelFilter(key: 'all' | RelationKey) {
  relFilter.value = relFilter.value === key && key !== 'all' ? 'all' : key
}

/** 某个点位在当前筛选下是否应该变暗 */
function isDimmed(stationId: number): boolean {
  if (!auditMode.value) return false
  if (auditFilter.value !== 'all') {
    const state = stateById.value[stationId]
    // 空位 / 不在范围有各自的筛选按钮，不再"顺带"落进别的分类里
    if (state !== auditFilter.value) return true
  }
  if (matchedIds.value.size) return !matchedIds.value.has(stationId)
  return false
}

/**
 * 某个点位是否该变暗（搜索维度）。
 *
 * 与 `isDimmed` 分开：那个管**盘点筛选**（只在盘点模式下生效），
 * 这个管**搜索**（任何时候都生效）。
 *
 * ⚠️ 用 `hasQuery` 而不是 `matchedIds.size > 0` 判断：
 * 否则搜一个不存在的词时集合是空的、判定成"没有搜索"，结果**什么都不变暗、也没有高亮** ——
 * 用户以为搜索坏了。搜了但没命中的正确反馈是"全部变暗"，一眼就知道没这个人。
 */
function isSearchDimmed(stationId: number): boolean {
  if (!hasQuery.value) return false
  return !matchedIds.value.has(stationId)
}

/* ========================================================================== */
/* 导出                                                                        */
/* ========================================================================== */

/**
 * 导出点位 JSON。
 *
 * 格式仍是 `asset-space-map/workstations@1`（后端导入按这个 schema 卡闸门），
 * 且**刻意不带 assets** —— 点位文件只描述点位本身，
 * 真实资产归属由 `assets.workstation_id` 反着指向点位。详见 `utils/workstationMap.ts`。
 */
function exportJson() {
  const payload = buildExportPayload(stations.value, canvas.value)
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: 'application/json;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const link = document.createElement('a')
  link.href = url
  link.download =
    `workstations-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}.json`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
  ElMessage.success(`已导出 ${stations.value.length} 个点位（不含资产，资产归属以系统为准）`)
}

/* ========================================================================== */
/* 键盘：空格临时平移 / ESC 取消新增 / Delete 删除选中                          */
/* ========================================================================== */

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return !!el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)
}

function onKeyDown(event: KeyboardEvent) {
  if (isTyping(event.target)) return
  if (event.code === 'Space') {
    if (!spacePressed.value) {
      spacePressed.value = true
      event.preventDefault()
    }
    return
  }
  if (event.key === 'Escape') {
    if (adding.value) {
      adding.value = false
      ElMessage.info('已退出新增模式')
    } else if (editMode.value) {
      editMode.value = false
    }
    return
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selected.value) {
    event.preventDefault()
    void removeStation(selected.value)
  }
}

function onKeyUp(event: KeyboardEvent) {
  if (event.code === 'Space') spacePressed.value = false
}

/** 窗口失焦时清掉空格平移态，免得用户切回来发现画布还"粘"在平移模式 */
function onBlur() {
  spacePressed.value = false
}

/* ========================================================================== */
/* 生命周期                                                                    */
/* ========================================================================== */

onMounted(async () => {
  await Promise.all([loadMap(), loadUsers(), loadTasks()])
  await loadMapView()
  // 问题中心的「去核对」深链：把 ?station= 选中。放在 fit() 之前 ——
  // 侧栏一出现就会改变可视高度，晚一步会先按没侧栏的尺寸 fit 一次。
  await applyStationFromQuery()

  /**
   * 断点判定必须在第一次 fit() 之前落地。
   * 反过来（先按桌面布局 fit、再切到窄屏）会在手机上闪一下"巨大的画布"，
   * 而且 `compact` 变化时的 fit 要等一次 nextTick 才生效。
   */
  compactMq = window.matchMedia('(max-width: 900px)')
  compact.value = compactMq.matches
  // 关系层在窄屏默认关（见 relLayer 的注释）。必须在这里做、且在第一次 fit() 之前 ——
  // 关系格会改变顶栏高度，晚一步会先按旧高度 fit 出画布再被顶栏挤掉一截。
  if (compact.value) relLayer.value = false
  compactMq.addEventListener('change', onCompactChange)

  await nextTick()
  fit()
  window.addEventListener('resize', onWindowResize)
  document.addEventListener('keydown', onKeyDown)
  document.addEventListener('keyup', onKeyUp)
  window.addEventListener('blur', onBlur)
  /**
   * mouseup 挂在 **document** 上，不是视口上。
   *
   * 挂视口的话，用户把点位拖到视口边缘之外松手（拖到侧栏上方、或拖出窗口），
   * 拖动就永远不结束 —— `dragId` 一直是那个点位，位置变更也不会被记进待保存集合。
   * 原型踩过同一类坑（它自己的注释里写着"此前缺少 mousemove/mouseup 监听"），
   * 这里跟着用 document 才符合拖拽的常规做法。
   */
  document.addEventListener('mouseup', onMouseUp)
  // 搜索浮层「点到别处就收起」。用 mousedown 而不是 input 的 blur，理由见 onDocMouseDown
  document.addEventListener('mousedown', onDocMouseDown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize)
  compactMq?.removeEventListener('change', onCompactChange)
  document.removeEventListener('keydown', onKeyDown)
  document.removeEventListener('keyup', onKeyUp)
  window.removeEventListener('blur', onBlur)
  document.removeEventListener('mouseup', onMouseUp)
  document.removeEventListener('mousedown', onDocMouseDown)
  window.removeEventListener('resize', placePanel)
  window.removeEventListener('scroll', placePanel, true)
})

/** 跨过 900px 断点：布局从"右侧常驻栏"变成"底部浮层抽屉"，画布占比变了，得重算 */
function onCompactChange(event: MediaQueryListEvent) {
  compact.value = event.matches
  void nextTick(() => fit())
}

/**
 * 选中点位变化时做两件事：
 *
 * 1. **窄屏**：把画布挪到它身上（保缩放）—— 抽屉是浮层，选中后会被它盖住；
 *    点关闭（`id === null`）时也走一遍，但**不重算缩放**，用户只是收起了详情，
 *    不该把它的视图重置回适应大小。
 * 2. 侧栏要显示这个点位上的资产，没缓存过就去拉。
 */
watch(selectedId, (id) => {
  if (id !== null && !assetCache.value[id]) void loadAssets(id)
  if (!compact.value) return
  void nextTick(() => {
    const s = stations.value.find((x) => x.id === id)
    if (s) centerOnStation(s)
  })
})

</script>

<template>
  <div class="map-page" :class="{ 'audit-on': auditMode, 'edit-on': editMode }">
    <!-- ───────────────────────── 顶部工具条 ───────────────────────── -->
    <div class="map-topbar">
      <!--
        点位关系层：**图例与筛选是同一个东西**。
        四个格子既是"这一片现在什么情况"的答案，也是筛选器 ——
        管理员一眼看到 45 / 1 / 1 / 15，再点其中一格把不关心的变暗，不用去别处找开关。
        关掉开关就回到原来的"有人 / 空闲"两态（原来的三个统计卡）。
      -->
      <div class="stats rel-stats">
        <button
          class="rel-toggle"
          :class="{ on: relLayer }"
          title="点位关系层：按「有人 × 有资产」两个维度给点位着色（只是概况，不是状态）"
          @click="relLayer = !relLayer"
        >
          关系层
        </button>

        <template v-if="relLayer">
          <button
            class="rstat all"
            :class="{ active: relFilter === 'all' }"
            @click="setRelFilter('all')"
          >
            全部 {{ stats.total }}
          </button>
          <button
            v-for="r in relStats"
            :key="r.key"
            class="rstat"
            :class="[r.key, { active: relFilter === r.key }]"
            :title="`${r.label} —— ${r.hint}`"
            @click="setRelFilter(r.key)"
          >
            <span class="rg">{{ r.glyph }}</span>
            <span class="rk">{{ r.short }}</span>
            <span class="rv">{{ r.count }}</span>
          </button>
        </template>

        <template v-else>
          <div class="stat">
            <div class="v">{{ stats.total }}</div>
            <div class="k">总点位</div>
          </div>
          <div class="stat used">
            <div class="v">{{ stats.assigned }}</div>
            <div class="k">已分配</div>
          </div>
          <div class="stat free">
            <div class="v">{{ stats.free }}</div>
            <div class="k">空闲点位</div>
          </div>
        </template>
      </div>

      <div class="vline" />

      <div ref="searchBoxEl" class="search-box">
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="compact ? '搜索…' : '姓名 / 点位 / 资产编号 / 轻资产'"
          title="搜索点位编码、使用人、资产编号 / 品牌、轻资产品类（品牌 / 规格）"
          spellcheck="false"
          @focus="onSearchFocus"
          @input="onSearchInput"
          @keydown="onSearchKeydown"
        />
        <button v-if="searchQuery" class="clear" title="清空" @click="clearSearch">×</button>
      </div>

      <!--
        搜索结果浮层。

        ⚠️ **必须 Teleport 到 body**：`.map-topbar` 是 `overflow-x: auto` 的横向滚动条，
        绝对定位的子元素会被它裁掉（哪怕 top 写得再对）。所以这里用 fixed + 计算坐标，
        且滚动顶栏时要整体重算（见 placePanel / 滚动监听）。

        内容分两段：**图上的点位**（点一下跳过去）与**不在图上**（给出口）。
        后者是这次重构的重点：轻资产有「在某人手上」这种不落点位的情况，
        24 台资产也没挂点位 —— 只点亮地图的话，用户搜"键鼠"会看到 0 命中，
        然后合理推断"系统里没有键鼠"。那是功能在说谎，不是体验问题。
      -->
      <Teleport to="body">
        <div
          v-if="panelOpen && hasQuery"
          ref="panelEl"
          class="search-panel"
          :style="{ left: panelPos.left + 'px', top: panelPos.top + 'px', width: panelPos.width + 'px' }"
        >
          <div class="sp-head">
            <span>
              命中 <b>{{ stationHits.length }}</b> 个点位
              <template v-if="offMapHits.length"> · 另有 <b>{{ offMapHits.length }}</b> 条不在图上</template>
            </span>
            <button class="sp-close" title="收起" @click="closePanel">×</button>
          </div>

          <div class="sp-body">
            <button
              v-for="hit in stationHits"
              :key="hit.station.id"
              class="sp-row"
              @click="gotoStation(hit.station)"
            >
              <span class="sp-code">{{ hit.station.code }}</span>
              <span class="sp-user">
                {{ hasOwner(hit.station.user_name) ? userLabel(hit.station.user_name) : '空闲' }}
              </span>
              <span class="sp-why">
                <span v-for="r in hit.reasons" :key="r.dim + r.label" class="sp-tag" :class="`d-${r.dim}`">
                  {{ r.label }}
                </span>
              </span>
            </button>

            <div v-if="!stationHits.length && !offMapHits.length && !searchLoading" class="sp-empty">
              没有匹配，图上无结果
            </div>

            <template v-if="offMapHits.length">
              <div class="sp-sep">不在图上（{{ offMapHits.length }}）</div>
              <RouterLink
                v-for="hit in offMapHits"
                :key="hit.key"
                class="sp-row off"
                :to="hit.to"
                @click="closePanel"
              >
                <span class="sp-why"><span class="sp-tag d-off">{{ hit.label }}</span></span>
                <span class="sp-hint">{{ hit.hint }}</span>
              </RouterLink>
            </template>
          </div>

          <div v-if="searchLoading || searchLoadError || truncationNote" class="sp-foot">
            <span v-if="searchLoading">正在建立索引（资产 / 轻资产）…</span>
            <span v-else-if="searchLoadError">资产 / 轻资产索引没加载成功：{{ searchLoadError }}</span>
            <span v-else>{{ truncationNote }}</span>
          </div>
        </div>
      </Teleport>

      <div class="btn-group">
        <button class="btn" :class="{ on: auditMode }" @click="toggleAuditMode()">
          {{ auditMode ? '退出盘点' : '盘点模式' }}
        </button>

        <!--
          ⚠️ v2.9.6 删掉了「轻资产层」开关与点位角标。理由不是"没人用"，而是**它表达不了语义**：
          角标只能给 `Σquantity`，而轻资产是混合池 —— 「轻×3」分不清是 3 套键鼠还是 1 SSD + 2 键鼠，
          聚合后每条记录的身份就丢了。要看"哪一条、什么规格、在谁手里"，入口是搜索（四维结果浮层）
          和点位侧栏的四组明细。**别再把它加回来**：一个纯数字换不来任何可行动的信息。
        -->

        <!--
          编辑类操作（新增 / 删除 / 导出 / 调整位置）。
          宽屏：整组用 `display: contents` 摊平，按钮直接当 `.btn-group` 的子项，和以前一模一样。
          手机：整组隐藏，收进右边的「更多」下拉 —— 六个按钮在 390px 里会折成 4 行、吃掉 178px
          （实测数字，见 CURRENT_STATE 第八节），比"点两下才找到"糟得多。
        -->
        <span class="tb-extra">
          <button class="btn primary" :class="{ on: adding }" @click="setAdding(!adding)">
            {{ adding ? '取消新增' : '新增' }}
          </button>
          <button class="btn ghost-danger" :disabled="!selected" @click="selected && removeStation(selected)">
            删除
          </button>
          <!--
            批量生成点位。**放在这里而不是「编辑底图」里** —— 见 runBulk 的注释。
            只在「调整位置」模式里出现：它属于"摆点位"，不属于"看地图"。
          -->
          <button v-if="editMode" class="btn teal" :class="{ on: bulkOpen }" @click="toggleBulkPanel">
            批量生成
          </button>
          <button class="btn" @click="exportJson">导出 JSON</button>
          <button class="btn teal" :class="{ on: editMode }" @click="toggleEditMode">调整位置</button>
          <button class="btn primary" :disabled="saving || !dirtyIds.size" v-if="editMode" @click="saveLayout">
            保存布局{{ dirtyIds.size ? `（${dirtyIds.size}）` : '' }}
          </button>
          <button class="btn" title="编辑底图（地台/走道/房间/墙体）" @click="openEditor">编辑底图</button>
        </span>

        <!-- 手机端专用入口。宽屏下整块 display:none，不进布局。 -->
        <el-dropdown class="tb-more" trigger="click" placement="bottom-end" @command="onTopbarCommand">
          <button class="btn">
            更多<span class="caret">▾</span>
            <span v-if="dirtyIds.size" class="dot" />
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="add">{{ adding ? '取消新增' : '新增点位' }}</el-dropdown-item>
              <el-dropdown-item command="remove" :disabled="!selected">删除选中点位</el-dropdown-item>
              <el-dropdown-item v-if="editMode" command="bulk">
                {{ bulkOpen ? '收起批量生成' : '批量生成点位' }}
              </el-dropdown-item>
              <el-dropdown-item command="export">导出 JSON</el-dropdown-item>
              <el-dropdown-item command="editor">编辑底图</el-dropdown-item>
              <el-dropdown-item command="edit">
                {{ editMode ? '退出调整' : '调整位置' }}
              </el-dropdown-item>
              <el-dropdown-item v-if="editMode" command="save" :disabled="saving || !dirtyIds.size">
                保存布局{{ dirtyIds.size ? `（${dirtyIds.size}）` : '' }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <div class="vline" />

      <div class="zgroup">
        <button class="zbtn" title="缩小" @click="zoomBy(1 / ZOOM_STEP_BUTTON)">−</button>
        <button class="zbtn" title="放大" @click="zoomBy(ZOOM_STEP_BUTTON)">＋</button>
        <button class="zbtn wide" title="适应视图" @click="fit()">适应</button>
      </div>
    </div>

    <!-- ───────────────────────── 盘点条 ───────────────────────── -->
    <div v-if="auditMode" class="audit-bar">
      <div class="audit-info">
        <!--
          任务选择器。**不是装饰**：地图是聚合视图，必须明确"聚合的是哪一次"，
          否则两个任务同时进行时，图上会显示一个用户没问过的任务的答案。
          即使只有一个任务也显示 —— 要让"当前是哪一个"永远可见。
        -->
        <span class="lbl">当前盘点</span>
        <el-select
          v-if="openTasks.length"
          :model-value="taskId ?? undefined"
          size="small"
          style="width: 260px"
          @update:model-value="onTaskChange"
        >
          <el-option v-for="t in openTasks" :key="t.id" :label="t.name" :value="t.id" />
        </el-select>
        <span v-else class="no-task">当前没有进行中的盘点</span>

        <template v-if="mapView">
          <div class="track">
            <div class="fill" :style="{ width: progress.pct + '%' }" />
          </div>
          <!--
            口径与原盘点页**完全一致**：已盘 / 应盘，异常单独标出来。
            分母是**资产数**（task.total），不是点位数：一个点位可能挂 2 台，也可能 0 台。
            异常不计入进度 —— 原盘点页就是这么算的，两页数字必须能对上。
          -->
          <!--
            「台资产」三个字在手机上就是纯粹的宽度浪费 —— `.wide-only` / `.compact-only`
            是一对只切换文案、不切换逻辑的壳（见样式表末尾），宽屏读全称、窄屏读简称。
            数字和口径一个字都不差。
          -->
          <span class="pct">
            <span class="wide-only">已盘 {{ progress.checked }}/{{ progress.total }} 台资产（{{ progress.pct }}%）</span>
            <span class="compact-only">已盘 {{ progress.checked }}/{{ progress.total }}（{{ progress.pct }}%）</span>
            <template v-if="progress.abnormal">· 异常 {{ progress.abnormal }}</template>
          </span>
        </template>
      </div>

      <div v-if="mapView" class="chips">
        <button class="chip" :class="{ active: auditFilter === 'all' }" @click="setAuditFilter('all')">
          全部 ({{ stateCounts.all }})
        </button>
        <button class="chip pending" :class="{ active: auditFilter === 'pending' }" @click="setAuditFilter('pending')">
          待盘 ({{ stateCounts.pending }})
        </button>
        <button class="chip passed" :class="{ active: auditFilter === 'checked' }" @click="setAuditFilter('checked')">
          已盘 ({{ stateCounts.checked }})
        </button>
        <button class="chip danger" :class="{ active: auditFilter === 'abnormal' }" @click="setAuditFilter('abnormal')">
          异常 ({{ stateCounts.abnormal }})
        </button>
        <!--
          两种中性态各有一个按钮。不加的话这些格子在任何筛选下都"不属于任何一类"，
          点"全部"看得见、点任何一个具体筛选就消失 —— 用户会以为它们出问题了。
          而且它们**不进进度分母**：把"没有东西可盘"算成"盘过了"是错误信息。
        -->
        <button class="chip empty" :class="{ active: auditFilter === 'empty' }" @click="setAuditFilter('empty')">
          空位 ({{ stateCounts.empty }})
        </button>
        <button
          class="chip outscope"
          :class="{ active: auditFilter === 'not_in_scope' }"
          @click="setAuditFilter('not_in_scope')"
        >
          <span class="wide-only">不在本次范围</span><span class="compact-only">范围外</span>
          ({{ stateCounts.not_in_scope }})
        </button>
      </div>
    </div>

    <!-- ───────────────────────── 地图 + 侧栏 ───────────────────────── -->
    <div class="map-body">
      <!--
        批量生成点位面板。浮在画布左上角，只在「调整位置」模式下由按钮唤出。
        它**不在编辑器里** —— 「底图是底图，点位是点位」（见 runBulk 的注释）。
      -->
      <div v-if="bulkOpen && editMode" class="bulk-panel">
        <div class="bulk-panel-head">
          <span>批量生成点位</span>
          <button class="bulk-close" title="收起" @click="toggleBulkPanel">×</button>
        </div>
        <p class="bulk-warn">
          <AppIcon name="circleAlert" :size="13" />
          <span>会<b>立刻写进数据库</b>，不用点保存。生成后可撤销。</span>
        </p>
        <div class="bulk-form">
          <div class="bulk-row">
            <label>总数</label>
            <input v-model.number="bulk.count" type="number" min="1" max="200" />
          </div>
          <div class="bulk-grid">
            <div class="bulk-row"><label>起点 X</label><input v-model.number="bulk.origin_x" type="number" step="20" /></div>
            <div class="bulk-row"><label>起点 Y</label><input v-model.number="bulk.origin_y" type="number" step="20" /></div>
          </div>
          <div class="bulk-grid">
            <div class="bulk-row"><label>横距</label><input v-model.number="bulk.step_x" type="number" step="10" /></div>
            <div class="bulk-row"><label>纵距</label><input v-model.number="bulk.step_y" type="number" step="10" /></div>
          </div>
          <div class="bulk-grid">
            <div class="bulk-row"><label>每行</label><input v-model.number="bulk.cols" type="number" min="1" max="30" /></div>
            <div class="bulk-row">
              <label>朝向</label>
              <select v-model="bulk.facing">
                <option value="down">全朝下</option>
                <option value="up">全朝上</option>
                <option value="alternate">交替（对面）</option>
              </select>
            </div>
          </div>
          <div class="bulk-grid">
            <div class="bulk-row"><label>起始号</label><input v-model="bulk.start_code" placeholder="如 W63" /></div>
          </div>

          <!-- 编码预览：**只是建议**，真相在后端（会查全量 code 兜底） -->
          <div class="bulk-preview">
            将生成 <b>{{ bulk.count }}</b> 个：
            <span class="mono">{{ bulkPreview }}</span>
            <span class="preview-note">实际编码以后端为准；撞号会报错而不跳号</span>
          </div>

          <button class="bulk-go" :disabled="bulkBusy || !bulkValid" @click="runBulk">
            {{ bulkBusy ? '生成中…' : '生成点位' }}
          </button>

          <div v-if="lastBulk" class="bulk-undo">
            <span class="bulk-undo-text">
              刚生成 <b>{{ lastBulk.codes.length }}</b> 个（{{ lastBulk.codes[0] }} ~
              {{ lastBulk.codes[lastBulk.codes.length - 1] }}）
            </span>
            <button class="bulk-undo-btn" :disabled="bulkUndoBusy" @click="undoBulk">
              {{ bulkUndoBusy ? '撤销中…' : '撤销生成' }}
            </button>
          </div>
        </div>
      </div>

      <div
        ref="vp"
        class="viewport"
        :class="{
          adding,
          'space-hold': spacePressed,
          'edit-mode': editMode,
          dragging: dragId !== null,
          panning,
        }"
        @click="onStageClick"
        @pointerdown="onPanStart"
        @pointermove="onPanMove"
        @pointerup="onPanEnd"
        @pointercancel="onPanEnd"
        @mousemove="onMouseMove"
        @wheel="onWheel"
      >
        <div class="canvas" :style="canvasStyle">
          <div class="grid-backdrop" :style="{ backgroundSize: `${GRID}px ${GRID}px`, inset: '24px' }" />
          <div class="plate" :class="{ blank: !floorShapes.length }" />

          <!-- 对齐参考线：拖到某列/某排时画出来，让"吸附"看得见 -->
          <svg class="guides" :width="canvas.width" :height="canvas.height">
            <line
              v-if="guides.x !== null"
              :x1="guides.x"
              y1="0"
              :x2="guides.x"
              :y2="canvas.height"
              class="g-v"
            />
            <line
              v-if="guides.y !== null"
              x1="0"
              :y1="guides.y"
              :x2="canvas.width"
              :y2="guides.y"
              class="g-h"
            />
          </svg>

          <!--
            建筑图元：地台 / 走道 / 房间 / 墙体 / 分隔条 / 前厅 / 入口。
            全部来自默认平面图的 layout（后端带出），前端不再写死坐标。
            改动形状请去编辑器，不要往这里加 div。
          -->
          <template v-for="shape in floorShapes" :key="shape.id">
            <!-- 文字图元：走道标注 -->
            <div
              v-if="shape.kind === 'corrtext'"
              class="corr-t"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px` }"
            >
              {{ shape.text }}
            </div>

            <!-- 竖墙 / 横分隔条：两条边，无内容 -->
            <div
              v-else-if="shape.kind === 'wall'"
              class="wall-v"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px`, height: `${shape.h ?? 0}px` }"
            />
            <div
              v-else-if="shape.kind === 'divider'"
              class="divider-h"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px`, width: `${shape.w ?? 0}px` }"
            />

            <!-- 房间：带名字与副标题 -->
            <div
              v-else-if="shape.kind === 'room'"
              class="room"
              :class="shape.room || 'meet'"
              :style="shapeBox(shape)"
            >
              <div class="rname">
                <i :style="{ background: ROOM_COLORS[shape.room || 'meet'] || ROOM_COLORS.meet }" />{{ shape.name }}
              </div>
              <div v-if="shape.sub" class="rsub">{{ shape.sub }}</div>
            </div>

            <!-- 大楼入口：图标 + 文字 -->
            <div
              v-else-if="shape.kind === 'entry'"
              class="entry"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px` }"
            >
              <svg viewBox="0 0 64 38" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="M3 36V8a4 4 0 0 1 4-4h19v32" />
                <path d="M61 36V8a4 4 0 0 0-4-4H38v32" />
              </svg>
              <span>大楼入口</span>
            </div>

            <!-- 地台 / 走道 / 前厅：纯色块 -->
            <div
              v-else
              :class="SHAPE_KIND_CLASS[shape.kind] || shape.kind"
              :style="shapeBox(shape)"
            />
          </template>

          <!-- 点位 -->
          <div
            v-for="station in stations"
            :key="station.id"
            class="ws"
            :class="[
              hasOwner(station.user_name) ? 'occupied' : 'empty',
              `facing-${station.facing}`,
              {
                selected: selectedId === station.id,
                dragging: dragId === station.id,
                dimmed: isDimmed(station.id) || isSearchDimmed(station.id) || isRelDimmed(station.id),
                highlighted: matchedIds.has(station.id),
                // 只靠「按使用人推断」亮起的点位画虚线 —— 否则用户会以为东西确实在这儿
                inferred: inferredOnlyIds.has(station.id),
                // 关系层配色。盘点模式下**不加**这个类：那时的颜色是盘点结论，
                // 两个维度各管各的，混在一起会让人分不清格子到底在说什么。
                [`rel-${relById[station.id]}`]: relLayer && !auditMode,
                [`state-${stateById[station.id]}`]: auditMode && stateById[station.id],
              },
            ]"
            :style="{ left: station.x + 'px', top: station.y + 'px' }"
            @mousedown="onStationMouseDown($event, station)"
            @click.stop="select(station)"
          >
            <div class="chair" />
            <div class="desk">
              <div class="nm">{{ hasOwner(station.user_name) ? userLabel(station.user_name) : '空闲' }}</div>
              <div class="cd">{{ station.code }}</div>
            </div>
            <div v-if="auditMode && stateById[station.id]" class="audit-badge" :class="`b-${stateById[station.id]}`">
              {{ stateMeta(stateById[station.id]).badge }}
            </div>
            <!-- v2.9.6：删掉了「轻×N」角标。轻资产是混合池，一个件数说明不了"是什么、在哪一条"，
                 而聚合后的数字恰好把身份丢了。要看配件请用搜索或点位侧栏的四组明细。 -->
          </div>
        </div>

        <!--
          空画布引导（v2.3.0）。**只有全新用户的库才会出现** ——
          有图有点的老用户永远看不到它。
          它是浮层且 `pointer-events: none`（只有两个按钮可点），
          不挡底下的画布：万一用户已经开始操作了，引导不会拦住他。
        -->
          <div v-if="blankHint" class="blank-hint" :class="{ 'is-editing': editMode }">
            <div class="bh-in">
            <div class="bh-t">{{ blankHint.title }}</div>
            <p class="bh-p">
              这套系统不会替你猜办公室长什么样 —— 底图、点位都是你自己的。
            </p>
            <ol class="bh-steps">
              <li>
                <b>建点位</b>：顶栏「调整位置」→ 点「新增」，再点画布空白处；
                想排一片就用「批量生成」（能实时预览编号范围）
              </li>
              <li><b>画底图</b>：顶栏「编辑底图」→ 从零画地台 / 走道 / 房间 / 墙体</li>
              <li><b>挂资产</b>：台账里逐台填「所在点位」，或用「批量导入」</li>
            </ol>
            <div class="bh-btns">
              <button class="bh-btn primary" @click="startFromBlank()">开始建点位</button>
              <button class="bh-btn" @click="openEditor()">先画底图</button>
            </div>
            <div class="bh-foot">
              已经有点位文件了？用
              <code>python backend\import_workstations.py --file 你的.json</code> 导入
            </div>
          </div>
        </div>

        <div class="legend">
          <!--
            图例与顶栏那四个格子是同一份数据的两种呈现：
            顶栏管"筛选 + 数字"，这里管"这个符号长什么样"。
            两处都走 `relStats`，不各写一份文案 —— 否则改了标签忘了改这里，
            图上就会出现一种颜色配一句对不上的说明。
          -->
          <template v-if="relLayer && !auditMode">
            <div v-for="r in relStats" :key="r.key" class="it" :title="`${r.label} —— ${r.hint}`">
              <span class="sw" :class="`rel-${r.key}`" />
              <span class="lg">{{ r.glyph }} {{ r.short }}</span>
            </div>
          </template>
          <template v-else-if="stations.length">
            <div class="it"><span class="sw occ" />已分配</div>
            <div class="it"><span class="sw emp" />空闲</div>
          </template>
          <template v-if="auditMode">
            <div class="it"><span class="sw aud-ok" />已盘</div>
            <div class="it"><span class="sw aud-bad" />异常</div>
            <div class="it">
              <span class="sw aud-empty" /><span class="wide-only">空位 / 不在范围</span><span class="compact-only">空位/范围外</span>
            </div>
          </template>
          <div class="it hint">
            <template v-if="hasQuery && !matchedIds.size">没有匹配，图上无结果</template>
            <template v-else-if="hasQuery && inferredOnlyIds.size && inferredOnlyIds.size === matchedIds.size">
              虚线框 = 按使用人推断的位置，不一定是这里
            </template>
            <template v-else-if="relFilter !== 'all' && relLayer && !auditMode">点顶栏「全部」取消筛选</template>
            <template v-else-if="auditMode">点击点位可对单台设备盘点</template>
            <template v-else>点「调整位置」后可拖动点位</template>
          </div>
        </div>

        <div v-if="loading" class="map-loading">加载中…</div>
      </div>

      <!--
        点位详情抽屉。
        宽屏：`.sheet-host` 是 `display: contents`，这份包装完全不参与布局，
              侧栏还是老老实实一列 390px 的右栏（和之前一模一样）。
        窄屏：它变成浮在画布上的底部抽屉，且**没选中点位时整个不渲染** ——
              手机上那块"选择一个点位"的空态占满整屏，等于把地图废掉了。

        ref 是给 fit() / centerOnStation() 量高度用的：抽屉盖住的这截要让出来。
      -->
      <div
        ref="sheetEl"
        class="sheet-host"
        :class="{ 'sheet-host--hidden': !selected }"
      >
        <MapStationPanel
          :station="selected"
          :map-row="selectedMap"
          :assets="selectedAssets"
          :users="users"
          :audit-mode="auditMode"
          :assets-loading="assetsLoading"
          :marking-ids="markingIds"
          :task-id="taskId"
          :relation-check="relationCheck"
          :relation-loading="relationLoading"
          @close="closeSidebar"
          @saved="
            (s) => {
              const idx = stations.findIndex((x) => x.id === s.id)
              if (idx >= 0) stations[idx] = s
              // 改了使用人 → 关系核对的基准变了，必须重新算
              loadRelationCheck(s.id)
            }
          "
          @removed="removeStation"
          @mark="markAsset"
          @mark-station="markStation"
          @add-asset="openCreateOnStation"
          @assets-changed="
            (id) => {
              loadAssets(id, true)
              loadRelationCheck(id)
            }
          "
          @relation-updated="onRelationUpdated"
          @relation-refresh="refreshRelationCheck"
          @relocate="openRelocation"
        />
      </div>

      <!--
        迁移对话框。挂在画布容器里而不是侧栏里 —— 迁移成功后侧栏可能整个换人
        （源点位被退役时甚至连点位都没了），对话框不该跟着那个节点一起消失。
      -->
      <RelocationDialog
        v-model="relocationOpen"
        :mode="relocationMode"
        :source="selected"
        :source-assets="selectedAssets"
        :preset-asset-ids="relocationPreset"
        :stations="stations"
        @applied="onRelocationApplied"
      />
    </div>
  </div>
</template>

<style scoped>
/* ==========================================================================
   资产空间地图 —— 样式
   视觉全部沿用原型 V19（用户验收过的版本），因此这里**重新声明了一套局部变量**：
   原型用的是 --ink / --line / --accent 这一套，而全局 main.css 用的是
   --text-1 / --border / --primary。把原型的变量限定在 .map-page 内定义，
   下面的样式就能原样搬过来，不用逐条改值 —— 改值就是改视觉，那是回归。
   ========================================================================== */
.map-page {
  --bg: #f1f5f9;
  --panel: #ffffff;
  --ink: #0f172a;
  --ink-secondary: #475569;
  --ink-muted: #94a3b8;
  --line: #e2e8f0;
  --line-strong: #cbd5e1;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-soft: #eff6ff;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
  --teal: #0d9488;
  --teal-soft: #ccfbf1;
  --sh-sm: 0 1px 2px 0 rgba(15, 23, 42, 0.05);
  --sh-md: 0 4px 12px -2px rgba(15, 23, 42, 0.08), 0 2px 6px -1px rgba(15, 23, 42, 0.04);
  --sh-lg: 0 12px 28px -6px rgba(15, 23, 42, 0.12), 0 4px 12px -2px rgba(15, 23, 42, 0.06);
  --sh-plate: 0 24px 60px -16px rgba(15, 23, 42, 0.18), 0 4px 16px rgba(15, 23, 42, 0.04);
  --ease: cubic-bezier(0.16, 1, 0.3, 1);

  display: flex;
  flex-direction: column;
  /* 顶栏是全局的（App.vue），这里只占剩下的高度。
     这条能算准的前提是路由 meta.flush 生效 —— 地图页的 `main.page-host`
     不能有内边距，否则 56 + (100vh - 56) 会多出 padding 那么多、页面开始滚动。

     第二条 dvh 是给手机准备的：移动浏览器的 100vh 算的是"地址栏收起时"的高度，
     地址栏一出现底部就有一截被裁掉（画布下沿 / 图例会被切）。
     不支持的浏览器直接落在上面那条 vh 上，行为不变。 */
  height: calc(100vh - var(--appbar-h));
  height: calc(100dvh - var(--appbar-h));
  min-height: 520px;
  background: var(--bg);
  color: var(--ink);
  overflow: hidden;
}

/* ───────────────────────── 顶部工具条 ───────────────────────── */
.map-topbar {
  height: 60px;
  flex: none;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 20px;
  z-index: 30;
  box-shadow: var(--sh-sm);
  overflow-x: auto;
  scrollbar-width: none;
}
.map-topbar::-webkit-scrollbar { display: none; }

.stats { display: flex; align-items: center; gap: 18px; flex: none; }
.stat { display: flex; flex-direction: column; gap: 1px; }
.stat .v {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.stat .k { font-size: 10.5px; color: var(--ink-muted); font-weight: 600; letter-spacing: 0.2px; }
.stat.used .v { color: var(--accent); }
.stat.free .v { color: var(--warning); }

/* ───────────────── 关系层：图例 + 筛选（同一个东西）─────────────────
   一格 = 一个符号 + 名字 + 数字，点它就是把不关心的变暗。
   配色刻意只有蓝（有人）和灰（无人）两个色系，"有没有资产"用
   边框虚实与底色深浅表达 —— 四种一屏全是主色系才不会变成"彩色地图"。
   ------------------------------------------------------------------- */
.rel-stats { gap: 6px; }
.rel-toggle {
  height: 30px; padding: 0 10px; border-radius: 8px; cursor: pointer;
  font-size: 11.5px; font-weight: 700; white-space: nowrap;
  border: 1px dashed var(--line-strong); background: #fff; color: var(--ink-muted);
  transition: all 0.16s var(--ease); user-select: none; flex: none;
}
.rel-toggle:hover { border-color: #94a3b8; color: var(--ink-secondary); }
.rel-toggle.on {
  border-style: solid; border-color: var(--accent); background: var(--accent-soft);
  color: var(--accent-hover);
}
.rstat {
  display: flex; align-items: center; gap: 5px; flex: none;
  height: 30px; padding: 0 9px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--line); background: #fff;
  font-size: 11.5px; font-weight: 600; color: var(--ink-secondary); white-space: nowrap;
  transition: all 0.14s var(--ease); user-select: none;
}
.rstat:hover { border-color: #94a3b8; background: #f8fafc; }
.rstat .rg { font-size: 11px; line-height: 1; }
.rstat .rk { color: var(--ink-secondary); }
.rstat .rv {
  font-weight: 700; font-variant-numeric: tabular-nums; color: var(--ink);
  min-width: 14px; text-align: right;
}
.rstat.all { color: var(--ink-secondary); }
.rstat.all .rv { color: var(--ink); }
.rstat.active { border-color: currentColor; }
.rstat.active .rk, .rstat.active .rv { color: inherit; }

/* 四态的"色"（选中态底色 + 文字色）。虚实线在 .ws 那边，这里只管色。 */
.rstat.person_asset { color: #1d4ed8; }
.rstat.person_none { color: #b45309; }
.rstat.none_asset { color: #475569; }
.rstat.none_none { color: #94a3b8; }
.rstat.person_asset .rv { color: #1d4ed8; }
.rstat.person_none .rv { color: #b45309; }
.rstat.none_asset .rv { color: #475569; }
.rstat.none_none .rv { color: #94a3b8; }
.rstat.all { color: var(--ink-secondary); }
.rstat.active { background: #f1f5f9; }
.rstat.person_asset.active { background: #eff6ff; border-color: #93c5fd; }
.rstat.person_none.active { background: #fffbeb; border-color: #fcd34d; }
.rstat.none_asset.active { background: #f1f5f9; border-color: #cbd5e1; }
.rstat.none_none.active { background: #fafafa; border-color: #e5e7eb; }

.vline { width: 1px; height: 28px; background: var(--line); margin: 0 2px; flex: none; }

.search-box { position: relative; width: 180px; flex: none; transition: width 0.2s var(--ease); }
.search-box:focus-within { width: 220px; }
.search-box input {
  width: 100%; height: 36px; padding: 0 30px 0 12px; border-radius: 9px;
  border: 1px solid var(--line-strong); background: #f8fafc; font-size: 12.5px;
  color: var(--ink); outline: none; transition: all 0.16s var(--ease);
}
.search-box input:focus {
  background: #fff; border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}
.search-box .clear {
  position: absolute; right: 8px; top: 8px; width: 20px; height: 20px; border-radius: 50%;
  border: none; background: transparent; color: var(--ink-muted); cursor: pointer;
  line-height: 1; font-size: 15px;
}
.search-box .clear:hover { background: var(--line); color: var(--ink); }

/* ───────────────── 搜索结果浮层 ─────────────────
   Teleport 到 body，所以这一段不受 `.map-topbar` 的 overflow 裁切。
   位置由 placePanel() 算（fixed + left/top/width），这里只管长相。 */
.search-panel {
  position: fixed; z-index: 200;
  display: flex; flex-direction: column;
  max-height: min(52vh, 440px);
  background: var(--panel); border: 1px solid var(--line-strong); border-radius: 12px;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}
.sp-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 9px 12px; font-size: 11.5px; color: var(--ink-muted);
  border-bottom: 1px solid var(--line); background: #f8fafc;
}
.sp-head b { color: var(--ink); font-variant-numeric: tabular-nums; }
.sp-close {
  width: 20px; height: 20px; flex: none; border: none; border-radius: 50%;
  background: transparent; color: var(--ink-muted); cursor: pointer;
  line-height: 1; font-size: 15px;
}
.sp-close:hover { background: var(--line); color: var(--ink); }
.sp-body { overflow-y: auto; padding: 4px; }
.sp-row {
  display: grid; grid-template-columns: 46px 1fr; align-items: center; gap: 2px 8px;
  width: 100%; padding: 7px 8px; border: none; border-radius: 8px;
  background: transparent; text-align: left; cursor: pointer; font: inherit;
  text-decoration: none; color: var(--ink);
}
.sp-row:hover { background: #f1f5f9; }
.sp-code { font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums; }
.sp-user {
  font-size: 12px; color: var(--ink-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 命中原因是换行铺满整行的，不挤在某一列里 */
.sp-why { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
.sp-tag {
  font-size: 10.5px; line-height: 1.6; padding: 0 6px; border-radius: 5px;
  background: #f1f5f9; color: var(--ink-muted); border: 1px solid var(--line);
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 四个维度的标记色：点位中性、人员蓝、资产绿、轻资产琥珀 */
.sp-tag.d-station { background: #f8fafc; }
.sp-tag.d-person { background: #eff6ff; border-color: #dbeafe; color: #1d4ed8; }
.sp-tag.d-asset { background: #ecfdf5; border-color: #d1fae5; color: #047857; }
.sp-tag.d-light { background: #fffbeb; border-color: #fde68a; color: #b45309; }
/* 「不在图上」用中性灰而不是红色：它**不是错误**，只是"这条东西没有位置"。
   红色在这页上已经被盘点异常占用了，借过来会让人以为出了问题。 */
.sp-tag.d-off { background: #f8fafc; border-style: dashed; border-color: #cbd5e1; color: #475569; }
.sp-row.off { grid-template-columns: 1fr auto; }
.sp-row.off .sp-why { grid-column: auto; }
.sp-hint { font-size: 10.5px; color: var(--ink-muted); white-space: nowrap; }
.sp-sep {
  padding: 8px 10px 4px; margin-top: 4px; border-top: 1px solid var(--line);
  font-size: 10.5px; font-weight: 700; color: var(--ink-muted); letter-spacing: 0.3px;
}
.sp-empty { padding: 14px 12px; font-size: 12px; color: var(--ink-muted); text-align: center; }
.sp-foot {
  padding: 6px 12px; border-top: 1px solid var(--line); background: #f8fafc;
  font-size: 10.5px; color: var(--ink-muted);
}

.btn-group { display: flex; align-items: center; gap: 6px; flex: none; }

/**
 * 顶栏的「宽屏/手机」分叉。
 *
 * `display: contents` 让这层 span **不生成盒子** —— 里面的按钮直接当 `.btn-group`
 * 的 flex 子项，宽屏下的布局和"没有这层包装"时逐像素一致（同 `.sheet-host` 的手法）。
 * 手机端在末尾的媒体查询里把它整组关掉、换成 `.tb-more` 下拉。
 */
.tb-extra { display: contents; }

/* 手机专用入口，宽屏不占位 */
.tb-more { display: none; }
.tb-more .btn { position: relative; }
.tb-more .caret { font-size: 10px; opacity: 0.65; }
/* 有未保存的布局改动时点一下小圆点，免得收进菜单里就没人发现 */
.tb-more .dot {
  position: absolute; top: 3px; right: 3px;
  width: 7px; height: 7px; border-radius: 50%; background: var(--warning);
}
.btn {
  height: 36px; padding: 0 12px; border-radius: 9px; font-size: 12.5px; font-weight: 600;
  border: 1px solid var(--line-strong); background: #fff; color: var(--ink-secondary);
  cursor: pointer; display: inline-flex; align-items: center; gap: 5px; white-space: nowrap;
  transition: all 0.16s var(--ease); user-select: none;
}
.btn:hover:not(:disabled) { background: #f8fafc; border-color: #94a3b8; color: var(--ink); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-hover); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.primary:hover:not(:disabled) { background: var(--accent-hover); color: #fff; }
.btn.primary.on {
  background: var(--accent-hover); border-color: var(--accent); color: #fff;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
}
.btn.ghost-danger { color: #dc2626; border-color: #fca5a5; background: #fff; }
.btn.ghost-danger:hover:not(:disabled) { background: #fef2f2; }
.btn.teal { color: var(--teal); border-color: #99f6e4; background: #f0fdfa; }
.btn.teal.on {
  background: var(--teal-soft); border-color: var(--teal); color: #0f766e;
  box-shadow: 0 0 0 3px rgba(13, 148, 136, 0.14);
}

.zgroup {
  display: flex; gap: 3px; background: #f1f5f9; padding: 3px;
  border-radius: 9px; border: 1px solid var(--line); flex: none;
}
.zbtn {
  min-width: 28px; height: 28px; padding: 0 6px; border-radius: 6px; border: none;
  background: transparent; color: var(--ink-secondary); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; transition: all 0.14s var(--ease);
}
.zbtn.wide { font-size: 12px; }
.zbtn:hover { background: #fff; color: var(--ink); box-shadow: var(--sh-sm); }

/* ───────────────────────── 盘点条 ───────────────────────── */
.audit-bar {
  height: 44px; flex: none;
  background: linear-gradient(90deg, #fffbeb 0%, #fef3c7 100%);
  border-bottom: 1px solid #fde68a;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; z-index: 25; font-size: 12.5px; color: #92400e;
  overflow-x: auto; scrollbar-width: none; gap: 14px;
}
.audit-bar::-webkit-scrollbar { display: none; }
.audit-info { display: flex; align-items: center; gap: 12px; font-weight: 600; flex: none; }
.audit-info .lbl { color: #b45309; }
.audit-info .no-task { color: #b45309; font-weight: 500; }
.audit-info .track {
  width: 140px; height: 8px; background: #fef08a; border-radius: 999px;
  overflow: hidden; border: 1px solid #fde68a; flex: none;
}
.audit-info .fill {
  height: 100%;
  background: linear-gradient(90deg, #f59e0b, #10b981);
  border-radius: 999px; transition: width 0.3s var(--ease);
}
.audit-info .pct { font-variant-numeric: tabular-nums; }

.chips { display: flex; align-items: center; gap: 6px; flex: none; }
.chip {
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; cursor: pointer;
  border: 1px solid #fde68a; background: #fff; color: #78350f;
  transition: all 0.14s var(--ease); user-select: none; white-space: nowrap;
}
.chip.pending { color: #b45309; }
.chip.pending.active { background: #d97706; color: #fff; border-color: #d97706; }
.chip.passed { background: #dcfce7; border-color: #bbf7d0; color: #15803d; }
.chip.passed.active { background: #15803d; color: #fff; border-color: #15803d; }
.chip.danger { background: #fee2e2; border-color: #fecaca; color: #b91c1c; }
.chip.danger.active { background: #dc2626; color: #fff; border-color: #dc2626; }
.chip.all.active { background: #78350f; color: #fff; border-color: #78350f; }
/* 两种中性态：灰系，与"已盘"的绿明确区分 —— 空位不是"盘过了" */
.chip.empty { background: #f1f5f9; border-color: #e2e8f0; color: #64748b; }
.chip.empty.active { background: #64748b; color: #fff; border-color: #64748b; }
.chip.outscope { background: #fafafa; border-color: #e5e7eb; color: #71717a; }
.chip.outscope.active { background: #71717a; color: #fff; border-color: #71717a; }

/* ───────────────────────── 地图主体 ───────────────────────── */
.map-body { flex: 1; display: flex; min-height: 0; position: relative; }

/**
 * 点位详情抽屉的外壳。
 *
 * 宽屏下用 `display: contents` —— 它自己不生成盒子，里面的 `<aside class="sb">`
 * 直接当 `.map-body` 的 flex 子项，和"没有这层包装"时完全一样（不动原有布局）。
 * 窄屏才变成浮层，见文件末尾的媒体查询。
 */
.sheet-host { display: contents; }

.viewport {
  flex: 1; position: relative; overflow: hidden; background-color: #eaeff6;
  background-image: radial-gradient(circle, #cbd5e1 1.2px, transparent 1.2px);
  background-size: 24px 24px; background-position: -12px -12px;
  /* 空白处可以拖着走（见 onPanStart）—— 所以默认就是 grab 而不是 default */
  cursor: grab;
  /* 触摸手势不给浏览器：不写这条的话手指一划页面就开始滚，
     我们收不到 pointermove，平移在手机上直接失效。
     （touch-action 不是继承属性，但浏览器取的是从命中元素到根这条链上的**交集**，
     所以落在点位上的手指一样被这条镇住 —— 双指缩放能生效也靠它。） */
  touch-action: none;
}
.viewport.space-hold { cursor: grab; }
.viewport.adding { cursor: crosshair; }
.viewport.edit-mode { cursor: default; }
.viewport.edit-mode .ws { cursor: grab; }
.viewport.dragging { cursor: grabbing; }
.viewport.panning { cursor: grabbing; }

.canvas {
  position: absolute; left: 0; top: 0;
  transform-origin: 0 0;
  /* 拖动时不要加过渡，否则手感发飘；缩放由 zbtn 控制，也不需要过渡 */
}

.grid-backdrop {
  position: absolute; border-radius: 16px; pointer-events: none; opacity: 0;
  transition: opacity 0.3s var(--ease); z-index: 1;
  background-image: linear-gradient(to right, rgba(13, 148, 136, 0.08) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(13, 148, 136, 0.08) 1px, transparent 1px);
}
.map-page.edit-on .grid-backdrop { opacity: 1; }

/* ── 批量生成点位面板（浮在画布左上角，仅「调整位置」模式下出现）──────
   它从「编辑底图」页搬过来的：底图与点位分属两页，别再混在一起。
   面板本身是**覆盖层**，不占画布布局 —— 否则会让 fit() 算出的可用面积变小。 */
.bulk-panel {
  position: absolute;
  left: 16px;
  top: 16px;
  z-index: 30;
  /* 宽度要容得下「两列输入」：标签 min-width 40 + 输入框至少 60 + gap 7，两列约 220，
     再加左右 padding 24 与边框。246 会把「起点 Y」压没；300 又让「起始号」的
     占位符「如 W63」被裁一个字 —— 312 刚好。 */
  width: 312px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 28px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}
.bulk-panel-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; font-size: 12.5px; font-weight: 700; color: #1e293b;
  border-bottom: 1px solid var(--border);
}
.bulk-close {
  border: none; background: none; cursor: pointer; font-size: 17px; line-height: 1;
  color: #94a3b8; padding: 0 2px;
}
.bulk-close:hover { color: #475569; }
.bulk-warn {
  margin: 9px 12px 2px; padding: 6px 8px;
  font-size: 11px; line-height: 1.5; color: #92400e;
  background: #fffbeb; border: 1px solid #fde68a; border-radius: 7px;
  /* 图标 + 文字：`AppIcon` 是 block svg；文字包成一个 span，别让它被拆成多个 flex 项 */
  display: flex; align-items: flex-start; gap: 5px;
}
.bulk-warn > svg { flex: none; margin-top: 1.5px; }
.bulk-warn b { font-weight: 700; color: #b45309; }
.bulk-form { padding: 4px 12px 12px; }
.bulk-row { display: flex; align-items: center; gap: 7px; margin-bottom: 6px; }
.bulk-row label { font-size: 11.5px; color: #64748b; flex: none; min-width: 40px; }
.bulk-row input, .bulk-row select {
  flex: 1; min-width: 0; height: 28px; border: 1px solid var(--border);
  border-radius: 6px; padding: 0 7px; font-size: 12px; color: #1e293b; background: #fff;
}
.bulk-row input:focus, .bulk-row select:focus { outline: none; border-color: var(--primary); }
.bulk-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
.bulk-grid .bulk-row { margin-bottom: 6px; }
.bulk-grid .bulk-row label { min-width: 36px; }
/* 「起点 X / 起点 Y」这种两列布局在 292px 面板里刚好放得下，
   但标签是 2 个汉字（40px）+ 输入框，窄了就会把输入框压没 —— 见下面 360px 的兜底。 */
.bulk-preview {
  font-size: 11.5px; color: #475569; line-height: 1.6;
  background: #f8fafc; border: 1px solid var(--border); border-radius: 7px;
  padding: 8px 10px; margin: 8px 0 10px;
}
.bulk-preview b { color: #1d4ed8; }
.bulk-preview .mono { font-family: ui-monospace, monospace; color: #0f172a; font-weight: 600; }
.bulk-preview .preview-note { display: block; font-size: 10.5px; color: #94a3b8; margin-top: 3px; }
.bulk-go {
  width: 100%; height: 32px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--primary); background: var(--primary); color: #fff;
  font-size: 12.5px; font-weight: 600;
}
.bulk-go:disabled { opacity: 0.5; cursor: not-allowed; }
.bulk-go:hover:not(:disabled) { filter: brightness(1.06); }
.bulk-undo {
  margin-top: 8px; padding: 7px 9px; background: #f0f7ff;
  border: 1px solid #cfe0ff; border-radius: 8px;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.bulk-undo-text { font-size: 11px; color: #3350c4; flex: 1; min-width: 0; }
.bulk-undo-text b { font-weight: 700; }
.bulk-undo-btn {
  padding: 3px 9px; font-size: 11px; cursor: pointer; border-radius: 6px;
  color: #b91c1c; border: 1px solid #fecaca; background: #fff;
}
.bulk-undo-btn:hover:not(:disabled) { background: #fef2f2; border-color: #fca5a5; }
.bulk-undo-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* ElMessage 里的行内「撤销」（单个新增点位时用）。
   挂在消息体里而不是做全局 action，避免和其它消息抢位置。 */
.undo-msg { display: inline-flex; align-items: center; gap: 10px; }
.undo-inline {
  border: 1px solid rgba(255, 255, 255, 0.6);
  background: rgba(255, 255, 255, 0.16);
  color: inherit; font-size: 12px; font-weight: 600;
  padding: 1px 8px; border-radius: 6px; cursor: pointer; line-height: 1.6;
}
.undo-inline:hover { background: rgba(255, 255, 255, 0.3); }

.plate {
  position: absolute; left: 24px; top: 24px; width: 912px; height: 1272px;
  background: #f8fafc; border-radius: 16px;
  box-shadow: var(--sh-plate), inset 0 0 0 1.5px #cbd5e1;
}
.plate::after {
  content: ''; position: absolute; inset: 0; border-radius: 16px;
  box-shadow: inset 0 0 0 8px #ffffff; pointer-events: none;
}
/* 空白画布。**没有图元时它不该长得像"画坏了"** ——
   满屏的实线边框 + 阴影会让人以为渲染出错。改成虚线 + 极浅的底，
   一眼看出"这里待填"，而不是"这里炸了"。 */
.plate.blank {
  background: #fcfdfe;
  border: 1px dashed #dbe3ec;
  box-shadow: none;
}
.plate.blank::after { display: none; }

/* 建筑图元 */
.zone { position: absolute; border-radius: 14px; background: #fff; box-shadow: inset 0 0 0 1px #e2e8f0; }
.corr { position: absolute; border-radius: 12px; background: #f1f5f9; box-shadow: inset 0 0 0 1px #cbd5e1; }
.corr-t {
  position: absolute; font-size: 10px; letter-spacing: 8px; font-weight: 700; color: #94a3b8;
  writing-mode: vertical-rl; text-orientation: upright; pointer-events: none; z-index: 2;
}
.lobby { position: absolute; border-radius: 12px; background: #fffbeb; box-shadow: inset 0 0 0 1px #fef3c7; }
.wall-v {
  position: absolute; width: 6px; border-radius: 3px; background: #94a3b8;
  box-shadow: 0 0 0 1px #cbd5e1; z-index: 6; pointer-events: none;
}
/* 横分隔条。原型里把它写成了 .wall-v + inline width 来覆盖，
   接入时就是这个歧义吃掉了 2 条隔断 —— 所以这里给它独立的类。 */
.divider-h {
  position: absolute; height: 6px; border-radius: 3px; background: #cbd5e1;
  box-shadow: inset 0 0 0 1px #94a3b8; z-index: 4; pointer-events: none;
}
/* 大楼入口：图标 + 文字，原型的固定装饰 */
.entry {
  position: absolute; display: flex; flex-direction: column; align-items: center;
  gap: 5px; pointer-events: none;
}
.entry svg { width: 64px; height: 36px; color: #d97706; }
.entry span { font-size: 10px; color: #b45309; letter-spacing: 3px; font-weight: 700; }
.room {
  position: absolute; border-radius: 12px; padding: 10px 14px; overflow: hidden;
  background: #fff; border: 1px solid #cbd5e1; box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
}
.room.meet { background: #f0f9ff; border-color: #bae6fd; }
.room.live { background: #faf5ff; border-color: #e9d5ff; }
.room.exec { background: #f5f3ff; border-color: #ddd6fe; }
.room.front { background: #fffbf0; border-color: #fef08a; }
.room .rname {
  font-size: 12.5px; font-weight: 700; color: #1e293b; letter-spacing: 0.2px;
  display: flex; align-items: center; gap: 8px; position: relative; z-index: 3;
}
.room .rname i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.room .rsub {
  font-size: 9.5px; color: #64748b; letter-spacing: 0.3px; padding-left: 15px;
  position: relative; z-index: 3; margin-top: 3px; font-weight: 500;
}

.guides { position: absolute; inset: 0; pointer-events: none; z-index: 60; }
.guides .g-v,
.guides .g-h { stroke: #0d9488; stroke-width: 1; stroke-dasharray: 6 4; opacity: 0.9; }

/* ───────────────────────── 点位卡片 ───────────────────────── */
.ws {
  position: absolute; width: 80px; height: 56px; z-index: 5;
  transition: opacity 0.2s var(--ease);
}
.ws .chair {
  position: absolute; left: 50%; width: 32px; height: 15px; margin-left: -16px;
  background: #e2e8f0; border: 1px solid #cbd5e1; z-index: 1;
  transition: all 0.16s var(--ease);
  display: flex; justify-content: center; align-items: center;
}
/* 朝向 = 椅子在桌子的哪一侧。容器的 56px 高度**装不下**「椅15 + 桌38 + 椅15」= 68，
   所以朝上的点位让整张卡片在容器内上移 16px：桌面从 top:15 提到 top:-1，
   椅子紧贴桌面下沿（37~52）。代价是桌面溢出容器上边 1px（`.ws` 没设 overflow，正常显示）。

   为什么上移到 -1 而不是别的数：上一排（朝下）的桌面下沿在容器内 53，
   上一排容器 y 与本排 y 相差 62 → 两排桌面之间正好留 8px 缝 ——
   现实中就是一张长桌对拼，但**不能贴死**：0px 会让两排看起来是一张桌子，
   而且两张桌的边框会互相压边，反而显得脏。

   ⛔ 旧值是 `top: 41`（41~56），与桌面 15~53 重叠 12px，而 `.chair` 的 z-index 1
   低于 `.desk` 的 2 → 重叠部分被吃掉，朝上的椅子只剩 3px 细边（实测 20% 可见）。
   那时候朝上的点位看起来"没有椅子"，就是这个数值没和桌面下沿对齐。 */
.ws.facing-down .chair { top: 0; border-radius: 8px 8px 3px 3px; border-bottom: none; }
.ws.facing-up .desk { top: -1px; }
.ws.facing-up .chair { top: 37px; border-radius: 3px 3px 8px 8px; border-top: none; }
.ws .chair::after { content: ''; width: 14px; height: 3px; border-radius: 2px; background: #94a3b8; opacity: 0.6; }

.ws .desk {
  position: absolute; left: 0; top: 15px; width: 80px; height: 38px; border-radius: 9px;
  background: #fff; border: 1px solid #cbd5e1; box-shadow: var(--sh-sm); z-index: 2;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
  overflow: hidden; transition: all 0.16s var(--ease);
}
.ws .nm {
  font-size: 11.5px; font-weight: 700; line-height: 1.1; color: #64748b;
  white-space: nowrap; max-width: 72px; overflow: hidden; text-overflow: ellipsis;
}
.ws .cd {
  font-size: 8.5px; line-height: 1; color: #94a3b8;
  font-variant-numeric: tabular-nums; font-weight: 600;
}

.ws.occupied .desk {
  background: linear-gradient(180deg, #fff 0%, #eff6ff 100%);
  border-color: #93c5fd; box-shadow: 0 2px 6px -1px rgba(37, 99, 235, 0.12);
}
.ws.occupied .nm { color: #1d4ed8; }
.ws.occupied .cd { color: #60a5fa; }
.ws.occupied .chair { background: #dbeafe; border-color: #93c5fd; }
.ws.occupied .chair::after { background: #3b82f6; opacity: 0.8; }
.ws.empty .desk { background: #fafafa; border-style: dashed; border-color: #cbd5e1; box-shadow: none; }

/* ==========================================================================
   点位关系层（有人 × 有资产）的视觉
   --------------------------------------------------------------------------
   **只有蓝（有人）和灰（无人）两个色系**，"有没有资产"用边框虚实 + 底色深浅表达。
   这么定的理由：四个色相铺满一屏就是"彩色地图"，而这页的主角是**平面图**，
   点位只是叠在上面的记号。四个都抢眼的结果是没人看得出格局，只觉得花。

   与 `.occupied/.empty` 的关系：那两条规则是**关系层关掉时**的老样子，
   这里的四条是打开时的样子。两者互斥（模板里 rel-* 只在非盘点模式加），
   但仍然要放在它们**之后** —— 同特异度下后来者才赢。
   ========================================================================== */

/* ● 有人 · 有资产 —— 正常使用中。唯一"实心"的一态，主色蓝 */
.ws.rel-person_asset .desk {
  background: linear-gradient(180deg, #fff 0%, #eff6ff 100%);
  border-color: #60a5fa; border-style: solid;
  box-shadow: 0 1px 3px -1px rgba(37, 99, 235, 0.25);
}
.ws.rel-person_asset .nm { color: #1d4ed8; }
.ws.rel-person_asset .cd { color: #60a5fa; }
.ws.rel-person_asset .chair { background: #dbeafe; border-color: #93c5fd; }
.ws.rel-person_asset .chair::after { background: #3b82f6; opacity: 0.8; }

/* ◐ 有人 · 无资产 —— 人来了设备还没到。蓝色但**虚线**：颜色说"有人"，
   虚线说"这格还没落实"。⚠️ 不用红色：它绝大多数是"还没配"而不是"坏了"，
   刷一片红会让管理员以为出了事故。 */
.ws.rel-person_none .desk {
  background: #fff; border-color: #f59e0b; border-style: dashed;
  box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.18);
}
.ws.rel-person_none .nm { color: #b45309; }
.ws.rel-person_none .cd { color: #d97706; }
.ws.rel-person_none .chair { background: #fef3c7; border-color: #fcd34d; border-style: dashed; }
.ws.rel-person_none .chair::after { background: #f59e0b; opacity: 0.7; }

/* ○ 无人 · 有资产 —— 公共设备 / 备用机 / 机房。**这是正常态**，
   所以给"实线 + 浅灰"而不是警告色：一个没人坐但摆着机的位置是常态。 */
.ws.rel-none_asset .desk {
  background: #f8fafc; border-color: #cbd5e1; border-style: solid; box-shadow: none;
}
.ws.rel-none_asset .nm { color: #64748b; }
.ws.rel-none_asset .cd { color: #94a3b8; }

/* · 无人 · 无资产 —— 空置 / 预留。**合法现状，不是异常**：
   预先规划的办公桌、备用位、以后要放设备的位置都是它。
   所以给最轻的点线，不渲染成任何"该处理"的语气。
   ⚠️ "不刺眼"不等于"看不见"：`.nm` 用 #94a3b8 而不是跟着边框一起淡到 #cbd5e1 ——
   后者在 #fafafa 上对比度约 1.2:1，编码和人名基本读不出来，
   而这个页面的核心动作恰恰是"看清哪个位置空着"。 */
.ws.rel-none_none .desk {
  background: #fafafa; border-color: #e2e8f0; border-style: dotted; box-shadow: none;
}
.ws.rel-none_none .nm { color: #94a3b8; }
.ws.rel-none_none .cd { color: #cbd5e1; }
.ws.rel-none_none .chair { background: #f8fafc; border-color: #e2e8f0; }

.ws:hover { z-index: 30; }
.ws.selected { z-index: 40; }
.ws.selected .desk {
  border-color: var(--accent); box-shadow: 0 0 0 2.5px var(--accent), 0 8px 20px -4px rgba(37, 99, 235, 0.35);
  background: #fff;
}
.ws.dragging { z-index: 70; opacity: 0.9; }
.ws.dimmed { opacity: 0.18; filter: grayscale(40%); }
.ws.highlighted .desk {
  border-color: #f59e0b; box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.4);
}
/* 只靠"按使用人推断"亮起：虚线 + 弱一级的光晕。
   同一个橙色实线框表示"就是这里"，虚线表示"可能是这里"，肉眼能分出来就行 */
.ws.highlighted.inferred .desk {
  border-style: dashed;
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.22);
}
.map-page.edit-on .ws .desk { box-shadow: inset 0 0 0 1px var(--teal); border-color: var(--teal); }

.ws .audit-badge {
  position: absolute; right: -4px; top: 10px; z-index: 10; font-size: 8.5px; font-weight: 800;
  padding: 1px 5px; border-radius: 999px; line-height: 1.2; box-shadow: var(--sh-sm);
  color: #fff; white-space: nowrap;
}

/* 盘点状态配色。前三种来自后端 result 聚合，后两种是中性态（灰） */
.ws.state-checked .desk {
  background: linear-gradient(180deg, #fff 0%, #ecfdf5 100%);
  border-color: #10b981; box-shadow: 0 0 0 1px #10b981;
}
.ws.state-checked .chair { background: #d1fae5; border-color: #10b981; }
.badge, .audit-badge.b-checked { background: #10b981; }

.ws.state-abnormal .desk {
  background: linear-gradient(180deg, #fff 0%, #fef2f2 100%);
  border-color: #ef4444;
  animation: danger-pulse 1.4s infinite var(--ease);
}
@keyframes danger-pulse {
  0%, 100% { box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.6); transform: scale(1); }
  50% { box-shadow: 0 0 0 5px rgba(239, 68, 68, 0.85); transform: scale(1.05); }
}
.ws.state-abnormal .chair { background: #fee2e2; border-color: #ef4444; }
.audit-badge.b-abnormal { background: #ef4444; }

.ws.state-pending .desk { border-color: #f59e0b; border-style: dashed; }
.audit-badge.b-pending { background: #f59e0b; }

/* 空位 / 不在本次范围：**灰色、降饱和**，绝不用绿色 ——
   把"没有东西可盘"或"本次不盘"画成"已盘"是错误信息 */
.ws.state-empty .desk {
  border-color: #cbd5e1; border-style: dotted; background: #f8fafc; box-shadow: none;
}
.ws.state-empty .chair { background: #f1f5f9; border-color: #cbd5e1; }
.audit-badge.b-empty { background: #94a3b8; }

.ws.state-not_in_scope .desk {
  border-color: #e5e7eb; background: #fafafa; box-shadow: none;
}
.ws.state-not_in_scope .nm { color: #a1a1aa; }
.audit-badge.b-not_in_scope { background: #a1a1aa; }

/* ───────────────────────── 图例 / 载入 ───────────────────────── */
.legend {
  position: absolute; left: 20px; bottom: 20px; z-index: 50;
  background: rgba(255, 255, 255, 0.92); backdrop-filter: blur(12px);
  border: 1px solid var(--line); border-radius: 12px; padding: 10px 16px;
  display: flex; align-items: center; gap: 14px; box-shadow: var(--sh-md);
  font-size: 12px; color: var(--ink-secondary); flex-wrap: wrap; max-width: calc(100% - 40px);
}
.legend .it { display: flex; align-items: center; gap: 6px; }
.legend .sw { width: 14px; height: 11px; border-radius: 3.5px; flex: none; }
.legend .sw.occ { background: #eff6ff; border: 1px solid #93c5fd; }
.legend .sw.emp { background: #fafafa; border: 1px dashed #cbd5e1; }
.legend .sw.aud-ok { background: #ecfdf5; border: 1px solid #10b981; }
.legend .sw.aud-bad { background: #fef2f2; border: 1px solid #ef4444; }
.legend .sw.aud-empty { background: #f8fafc; border: 1px dotted #cbd5e1; }

/* 关系层图例色块。**必须与 .ws.rel-* 的四条一模一样**，
   否则图例上写着"空置"、图上是另一种灰，管理员只能靠猜。 */
.legend .sw.rel-person_asset { background: linear-gradient(180deg, #fff 0%, #eff6ff 100%); border: 1px solid #60a5fa; }
.legend .sw.rel-person_none { background: #fff; border: 1px dashed #f59e0b; }
.legend .sw.rel-none_asset { background: #f8fafc; border: 1px solid #cbd5e1; }
.legend .sw.rel-none_none { background: #fafafa; border: 1px dotted #e2e8f0; }
.legend .lg { white-space: nowrap; }
.legend .it.hint {
  margin-left: 4px; padding-left: 14px; border-left: 1px solid var(--line); color: var(--ink-muted);
}

/* ───────────────────────── 空画布引导（v2.3.0）─────────────────────────
   浮层 + `pointer-events: none`，只有里面两个按钮恢复点击 ——
   理由：它是"提示"，不是"必须先关掉的弹窗"。用户如果已经自己动手了
   （比如直接点了批量生成），引导不许挡他的路。
   ─────────────────────────────────────────────────────────────────────── */
.blank-hint {
  position: absolute; inset: 0; z-index: 45;
  display: flex; align-items: center; justify-content: center;
  pointer-events: none; padding: 20px;
}
.bh-in {
  pointer-events: auto;
  width: min(460px, 100%); background: #fff; border: 1px solid var(--line);
  border-radius: 14px; box-shadow: var(--sh-lg);
  padding: 20px 22px; text-align: left;
}
.bh-t { font-size: 16px; font-weight: 700; color: var(--ink); }
.bh-p { margin: 7px 0 0; font-size: 12.5px; color: var(--ink-secondary); line-height: 1.75; }
.bh-steps {
  margin: 13px 0 0; padding: 0 0 0 20px;
  font-size: 12.5px; color: var(--ink-secondary); line-height: 1.85;
}
.bh-steps li { margin-bottom: 4px; }
.bh-steps b { color: var(--ink); }
.bh-btns { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }
.bh-btn {
  height: 32px; padding: 0 14px; border-radius: 8px; cursor: pointer;
  font-size: 12.5px; font-weight: 600;
  border: 1px solid var(--line-strong); background: #fff; color: var(--ink-secondary);
  transition: all 0.14s var(--ease);
}
.bh-btn:hover { border-color: #94a3b8; background: #f8fafc; }
.bh-btn.primary {
  border-color: var(--accent); background: var(--accent); color: #fff;
}
.bh-btn.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }

/* 进了「调整位置」模式：让到画布上方去，别压着用户要点的地方。
   缩到一半宽、字号收一档 —— 这时候他是"在干活"，引导该退居二线而不是占据 C位。 */
.blank-hint.is-editing { align-items: flex-start; padding-top: 18px; }
.blank-hint.is-editing .bh-in {
  width: min(380px, 100%); padding: 14px 16px; opacity: 0.94;
  box-shadow: var(--sh-md);
}
.blank-hint.is-editing .bh-t { font-size: 14px; }
.blank-hint.is-editing .bh-p { display: none; }
.blank-hint.is-editing .bh-steps { font-size: 12px; margin-top: 8px; }
.blank-hint.is-editing .bh-foot { display: none; }
.bh-foot {
  margin-top: 13px; padding-top: 11px; border-top: 1px solid var(--line);
  font-size: 11.5px; color: var(--ink-muted); line-height: 1.8;
}
.bh-foot code {
  background: #f1f5f9; border-radius: 4px; padding: 1px 5px;
  font-size: 11px; color: #475569;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}

.map-loading {
  position: absolute; left: 50%; top: 16px; transform: translateX(-50%);
  background: rgba(15, 23, 42, 0.8); color: #fff; font-size: 12px;
  padding: 6px 14px; border-radius: 999px; z-index: 80;
}

/**
 * 一对"只切文案、不切逻辑"的壳。
 *
 * 「已盘 12/45 **台资产**（27%）」「**不在本次范围**」这类全称在 390px 上会把一行撑爆，
 * 只能折行或者被裁掉。但**不能在 JS 里按屏宽换文案** —— 那就等于同一句话维护两份，
 * 以后改一处漏一处。两个 span 都渲染、由 CSS 决定显示哪个：宽屏读全称、窄屏读简称。
 * **数字与口径一个字都不差**，只是省掉几个字。
 */
.compact-only { display: none; }

/* ───────────────────────── 窄屏 ───────────────────────── */
@media (max-width: 1100px) {
  .map-topbar { gap: 10px; padding: 0 12px; }
  .search-box { width: 140px; }
  .search-box:focus-within { width: 170px; }
  .stats { gap: 12px; }
}

/* ==========================================================================
   手机 / 窄屏（≤900px）
   --------------------------------------------------------------------------
   断点与 JS 里的 `compact`（window.matchMedia）**必须一致**，否则会出现
   "按桌面布局算的居中 + 按手机布局画的抽屉"叠在一起。

   这一档要解决的是原来最致命的问题：侧栏是 `width: 390px; flex: none` 的
   常驻右栏，而手机屏本身才 390px 宽 —— 画布被挤成 0 宽度，一进去什么都看不到。
   ========================================================================== */
@media (max-width: 900px) {
  /* ── 顶栏：从「4 行 / 178px」压下来 ──
     实测（390×844）原来是这样折的：统计独占一行 → 搜索独占一行 → 6 个按钮自己折 2 行
     → 缩放再一行。固定区吃掉 27%，一开盘点模式到 42%。这里三刀：
       ① 统计从"竖排占满一行"改成"横排小字"，缩到约 110px；
       ② 编辑类按钮（新增 / 删除 / 导出 / 调整位置）整组收进「更多」下拉；
       ③ 搜索与统计同行、吃掉剩余宽度。 */
  .map-topbar {
    height: auto;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 10px;
    overflow-x: visible;
  }

  .map-topbar .vline { display: none; }

  /* 统计：横排紧凑，不再独占一行 */
  .stats { width: auto; gap: 13px; flex: none; }
  .stat { flex-direction: row; align-items: baseline; gap: 3px; }
  .stat .v { font-size: 15px; }
  .stat .k { font-size: 10px; }

  /* 关系层：自己占满一行、允许折行。四个格子在 390px 里横排必然超宽，
     与其让它们被裁（用户根本不知道还有「无人·有资产」这一类），
     不如让这一行诚实地折成两行 —— 与盘点 chips 当初同一个理由。 */
  .rel-stats { flex: 1 1 100%; flex-wrap: wrap; gap: 4px; }
  .rel-toggle { height: 26px; padding: 0 8px; font-size: 11px; }
  .rstat { height: 26px; padding: 0 7px; font-size: 10.5px; gap: 4px; }
  .rstat .rg { font-size: 10px; }
  .rstat .rv { min-width: 10px; }

  /* 搜索：与统计同行。窄屏下它是唯一的自由项，所以给 min-width 兜底别被压没。
     ⚠️ **聚焦时不再变宽**（原来 `flex-basis: 150px`）：实测 390×844 上聚焦一次
     就把工具条从 87px 顶到 131px、折成 3 行，C1 当场失守 —— 而这个变宽换来的
     42px 一个字符都多显示不了几个，搜索结果本来就在下方浮层里。
     输入框内文本横向滚动即可，浮层才是"看结果"的地方。 */
  .search-box { flex: 1 1 108px; width: auto; min-width: 88px; }
  .search-box:focus-within { width: auto; flex-basis: 108px; }

  /* 按钮组瘦身；编辑操作换成「更多」入口 */
  .btn-group { flex: none; gap: 6px; }
  .btn { height: 34px; padding: 0 10px; }
  .tb-extra { display: none; }
  .tb-more { display: inline-flex; }

  /* 缩放三兄弟靠右收口，别和功能按钮混成一坨 */
  .zgroup { margin-left: auto; }

  /* ── 盘点模式：把全局统计也收掉 ──
     这时候要看的是"盘到哪了"，不是"总点位 / 有人 / 无人"；而它一占就是一行。
     小屏上这一行直接决定画布还剩多大 —— 用户的诉求就是"上面固定不动的占太多了"。
     退出盘点自动回来。
     ⚠️ 关系层在盘点模式下本来就不参与着色与筛选（`rel-*` 类不加、`isRelDimmed` 恒 false），
     所以这里连开关一起收掉不会留下"看不见的筛选还在生效"。 */
  .map-page.audit-on .stats { display: none; }

  /* ── 盘点条：从 128px 压到 ~70px ──
     原来：任务选择器 width:100% 独占一行、进度条一行、chips 又一行。 */
  .audit-bar {
    height: auto;
    flex-wrap: wrap;
    gap: 6px;
    padding: 6px 10px;
    overflow-x: visible;
  }

  /* 「当前盘点」四个字省掉 —— 右边那个选择器本身就是说明，手机上每个字都是成本 */
  .audit-info .lbl { display: none; }
  .audit-info { flex-wrap: nowrap; gap: 8px; flex: 1 1 auto; min-width: 0; }
  .audit-info .el-select { width: auto !important; flex: 0 1 138px; }
  .audit-info .no-task { white-space: nowrap; }

  /* 进度条在手机上删掉：进度已由「已盘 x/y 台资产（z%）」那句文字完整表达，
     留着就必须 width:100% 再占一行，纯亏。 */
  .audit-info .track { display: none; }
  .audit-info .pct {
    font-size: 11.5px; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* chips：六个圆片一行。用 `wrap` 而不是"横向滚动"—— 滚动的代价是最后一片被裁掉，
     用户根本不知道还有「范围外」这个筛选项（实测第一版就是这样）。宁可多一行也不能藏。 */
  .chips { flex: 1 1 100%; flex-wrap: wrap; gap: 4px; }
  .chip { padding: 3px 7px; font-size: 10.5px; flex: none; }

  /* 长文案换短版：省字不省数字 */
  .wide-only { display: none; }
  .compact-only { display: inline; }

  /* ── 侧栏 → 浮在画布上的底部抽屉 ──
     画布保持全屏（fit() 会用量到的高度把抽屉盖住的那截让出来），
     抽屉只盖住下沿，不再参与 flex 分配 —— 这正是"手机端画布被挤没"的根因。 */
  .sheet-host {
    display: block;
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: min(58%, 460px);
    z-index: 60;
    background: #fff;
    border-radius: 16px 16px 0 0;
    box-shadow: 0 -8px 28px rgba(15, 23, 42, 0.16);
    overflow: hidden;
  }

  /* 没选中点位时整块不渲染。
     原来那份"选择一个点位"的空态在手机上占满整屏 —— 恰好把地图废掉了，
     而手机点进来第一眼最该看到的就是地图。 */
  .sheet-host--hidden { display: none; }

  /* 图例挪到左上：右下角是抽屉的位置，会被盖住。
     它是**浮层**、不吃画布高度，但盖在图上一样是挡视线，所以跟着瘦一圈：
     内边距减半、字号收到 10.5px，五个色块（盘点态）刚好一行。 */
  .legend {
    left: 10px;
    bottom: auto;
    top: 10px;
    max-width: calc(100% - 20px);
    padding: 5px 9px;
    gap: 9px;
    font-size: 10.5px;
    border-radius: 9px;
  }

  /* 提示句独占一行 —— 宽屏那根分隔竖线是在"同行并排"时才有意义的，
     窄屏上它只会和色块抢宽度、把整块挤成三行。 */
  .legend .it.hint { flex: 1 1 100%; margin-left: 0; padding-left: 0; border-left: none; }

  /* 图上悬浮的"加载中"往下让一点，别和左上角的图例打架 */
  .map-loading { top: auto; bottom: calc(min(58%, 460px) + 12px); }
}
</style>
