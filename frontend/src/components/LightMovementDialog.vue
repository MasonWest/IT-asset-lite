<script setup lang="ts">
/**
 * 登记一次业务动作 —— 轻资产模块的**唯一写入口**。
 *
 * ## 先选"业务动作"，不是"从哪来 / 到哪去"
 *
 * 第一版第一屏就要用户选「来源类型 / 去向类型」(`stock` / `user` / `asset` …)——
 * 那是在要求用户先在脑子里把"这套键鼠发给李四"翻译成数据模型里的一次物料移动。
 * 对 IT 管理员来说这不是易用，是让他理解实现。
 *
 * 现在改成两步：**先选人话动作**（发给某人 / 装到机器 / 换手 / 收回 / 报损 / 更换），
 * 界面自己把它翻成 `(op, from_kind, to_kind)`。用户只回答"给谁""哪台机器"。
 * 这样也**不给用户机会组合出"使用人 + 点位"这种冲突状态**（设计 §4.3）。
 *
 * ## 来源只能从"真的持有它的地方"里选
 *
 * 来源候选直接来自 `category.stat.holdings`（当前分配记录），数量都对得上 ——
 * 没有的不会出现在这里。库存分放多处时，每一处是独立的一条候选（含它的存放位置）。
 *
 * ## 每次提交带 request_id（UUID）
 *
 * 手抖连点、刷新重发，后端 409 挡第二次，不会二次入账。
 *
 * ## 底部给出「操作前 → 操作后」的确认条
 *
 * **不变的那行显式标"不变"** —— 用户要能一眼看出这次操作会改什么、不会改什么。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import { apiMessage, lightApi, metaApi } from '@/api'
import { useFreeTextOptions } from '@/composables/useFreeTextOptions'
import { appState } from '@/stores/app'
import type {
  LightCategory,
  LightHolderCandidate,
  LightHolderKind,
  LightHolding,
  LightMovementResult,
  LightOp,
} from '@/types'
import { HOLDER_KIND_META, flowAllows, newRequestId, opMeta } from '@/utils/light'
import { localDateString } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  category: LightCategory
  /** 从某条分配记录发起（收回 / 换手 / 报损 / 更换）时预选的那一条 */
  presetHoldingId?: number | null
  /** 从某台资产详情页发起：直接跳到某个动作、并把去向预选成那台资产（设计 §5.3） */
  presetActionKey?: string | null
  presetTargetKind?: 'asset' | 'workstation' | null
  presetTargetRefId?: number | null
  presetTargetCode?: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved', result: LightMovementResult): void
}>()

const unit = computed(() => props.category.unit)
const holdings = computed<LightHolding[]>(() => props.category.stat.holdings)

/* ---------------- 表单状态 ---------------- */
const op = ref<LightOp>('stock_in')
const fromKind = ref<LightHolderKind | null>(null)
const fromRefId = ref<number | null>(null)
const fromUser = ref('')
const fromLocation = ref('')
const toKind = ref<LightHolderKind | null>('stock')
const toRefId = ref<number | null>(null)
const toUser = ref('')
const toLocation = ref('')

const quantity = ref(1)
const occurredAt = ref('')
const note = ref('')
const operator = ref('')
const origin = ref<'manual' | 'opening'>('manual')
const saving = ref(false)
const requestId = ref(newRequestId())

/** 使用人下拉的候选：轻资产自己记过的 + 正式资产台账里的 */
const userSuggestions = ref<string[]>([])
/** 库存存放位置候选（自由文本，不强制规范化） */
const locationSuggestions = ref<string[]>([])

/**
 * 「发给谁 / 换给谁」的输入行为 —— 与资产表单的「使用人」**同一份实现**。
 *
 * ⚠️ 这里原来是 `<el-input list="…">` 配一个 `<datalist>`，两个后果：
 *   1. 弹的是**浏览器原生**的候选框（不是本项目的下拉），跟别处长得不一样；
 *   2. 原生 datalist 只会**按字面前缀**匹配 —— 打拼音首字母（`yjl` → 殷金岭）
 *      一个候选都出不来，而这恰恰是本项目选人的主要方式。
 *
 * 候选值可能在别处被改（刚存了一个新人名），所以传 **getter** 而不是快照。
 * 完整理由（`createLabel` 必须垫底、别加 `allow-create`）见 `composables/useFreeTextOptions.ts`。
 */
const {
  options: userOptions,
  createLabel: userCreateLabel,
  onFilter: filterUsers,
  reset: resetUserQuery,
  onVisibleChange: onUserVisibleChange,
} = useFreeTextOptions(() => userSuggestions.value)

/** 资产 / 点位候选的远程搜索 */
const holderCandidates = ref<LightHolderCandidate[]>([])
const holderLoading = ref(false)
let holderTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 从资产详情页带进来的那个候选 —— 搜索是远程的、会整个替换候选列表，
 * 不把它钉住的话，`el-select` 会因为找不到选项而把编码显示成裸 id。
 *
 * ⚠️ 必须声明在 `watch(modelValue, ..., { immediate: true })` **之前**：
 * 那个回调是**同步**跑的，而 `applyPresetTarget()` 会写它 —— 声明在下面就吃 TDZ 报错。
 */
const pinnedCandidates = ref<LightHolderCandidate[]>([])

const flowOk = computed(() => flowAllows(op.value, fromKind.value, toKind.value))

const flowHint = computed(() => {
  if (flowOk.value) return ''
  const f = fromKind.value ? HOLDER_KIND_META[fromKind.value].label : '系统外部'
  const t = toKind.value ? HOLDER_KIND_META[toKind.value].label : '系统外部'
  return `「${opMeta(op.value).label}」不能从「${f}」到「${t}」。请调整来源或去向。`
})

/* ---------------- 业务动作表 ----------------
   每个动作 = 一个业务意图。用户不需要知道它对应哪个 op / from / to。 */
interface BizAction {
  key: string
  title: string
  desc: string
  icon: string
  tone: 'g' | '' | 'v' | 'a' | 'r'
  op: LightOp
  /** 来源从哪一类持有记录里挑（null = 系统外部，不需要挑） */
  fromKinds: (LightHolderKind | null)[]
  /** 去向：固定给死，还是让用户填 */
  to: 'stock' | 'stock-with-location' | 'user' | 'asset-or-station'
}

const ACTIONS: BizAction[] = [
  {
    key: 'stock_in',
    title: '入库 / 建期初库存',
    desc: '新采购的，或按盘点结果把真实库存建起来',
    icon: 'arrowIn',
    tone: 'g',
    op: 'stock_in',
    fromKinds: [null],
    to: 'stock-with-location',
  },
  {
    key: 'give',
    title: '发给某个人',
    desc: '从库存里发给某人用（东西跟着人走）',
    icon: 'user',
    tone: '',
    op: 'allocate',
    fromKinds: ['stock'],
    to: 'user',
  },
  {
    key: 'mount',
    title: '装到某台机器 / 放到某个点位',
    desc: '比如把内存装进主机，或把键鼠固定放在某个工位',
    icon: 'wrench',
    tone: 'v',
    op: 'attach',
    fromKinds: ['stock'],
    to: 'asset-or-station',
  },
  {
    key: 'move',
    title: '换个人 / 换个地方',
    desc: '东西没回库，直接换手或挪位，总量不变',
    icon: 'swap',
    tone: 'v',
    op: 'transfer',
    fromKinds: ['user', 'asset', 'workstation'],
    to: 'user',
  },
  {
    key: 'back',
    title: '收回库存',
    desc: '从人手上或机器上退回来，回库房',
    icon: 'arrowOut',
    tone: 'a',
    op: 'return',
    fromKinds: ['user', 'asset', 'workstation'],
    to: 'stock-with-location',
  },
  {
    key: 'scrap',
    title: '报损',
    desc: '彻底退出管理，不再占库存、也没有持有方',
    icon: 'trash',
    tone: 'r',
    op: 'scrap',
    fromKinds: ['stock', 'user', 'asset', 'workstation'],
    to: 'stock' as never, // 实际去向是"外部"，只用 to='stock' 占位，见 applyAction
  },
  {
    key: 'replace',
    title: '更换配件',
    desc: '这台机器上的旧件报废、新件顶上；一次操作两条流水',
    icon: 'swap',
    tone: 'a',
    op: 'attach', // 占位：更换走专用入口，不用 op 字段
    fromKinds: ['asset'],
    to: 'stock' as never,
  },
]

const pickedKey = ref<string | null>(null)
const picked = computed(() => ACTIONS.find((a) => a.key === pickedKey.value) ?? null)
const step = ref<'pick' | 'form'>('pick')

/** 更换配件走专用入口，不套 (from,to) 那套 */
const isReplace = computed(() => pickedKey.value === 'replace')

/** 需要用户挑"从谁那里"的动作 */
const needsSource = computed(() => (picked.value?.fromKinds.length ?? 0) > 0 && !(picked.value?.fromKinds.includes(null) && picked.value.fromKinds.length === 1))

/** 当前动作可选的来源候选（来自当前分配记录，数量都对得上） */
const sourceOptions = computed(() => {
  const a = picked.value
  if (!a) return []
  if (isReplace.value) return holdings.value.filter((h) => h.kind === 'asset')
  return holdings.value.filter((h) => a.fromKinds.includes(h.kind as LightHolderKind))
})

function holdingKey(h: LightHolding): string {
  return `${h.kind}:${h.id}`
}

const sourceKey = ref<string>('')
const sourceHolding = computed(
  () => sourceOptions.value.find((h) => holdingKey(h) === sourceKey.value) ?? null,
)

/**
 * 「这条来源实际有几件」的提示 —— 只提示，**绝不写进默认值**。
 *
 * 数量默认恒为 1（见 `chooseAction`）。但来源是"手上/机器上有 3 套"这类多条记录时，
 * 不吭声会让人以为一次就处理干净了。信息给到，决定权留给人。
 */
const sourceQtyHint = computed(() => {
  const h = sourceHolding.value
  if (!h || h.quantity <= 1) return ''
  return `来源共 ${h.quantity} ${unit.value}，默认只登记 1 ${unit.value}；整条处理请改成 ${h.quantity}。`
})

const targetStation = ref(false)
/** 「换个人 / 换个地方」的三种去向（后端的 transfer 支持 人/机器/点位 互转） */
const moveMode = ref<'user' | 'asset' | 'workstation'>('user')

/** 去向需要"选一个对象"（而不是自由文本） */
const targetNeedsPicker = computed(
  () => toKind.value === 'asset' || toKind.value === 'workstation',
)
const targetLabel = computed(() => (toKind.value === 'workstation' ? '哪个点位' : '哪台机器'))

function applyAction() {
  const a = picked.value
  if (!a) return
  if (isReplace.value) {
    // 更换：来源是那台机器，去向也是它（后端算两条流水）
    const src = sourceHolding.value
    op.value = 'attach'
    fromKind.value = src?.kind ?? 'asset'
    fromRefId.value = src?.ref_id ?? null
    fromUser.value = src?.user ?? ''
    fromLocation.value = src?.stock_location ?? ''
    toKind.value = src?.kind ?? 'asset'
    toRefId.value = src?.ref_id ?? null
    toUser.value = ''
    toLocation.value = ''
    return
  }
  op.value = a.op
  // 来源
  if (a.fromKinds.length === 1 && a.fromKinds[0] === null) {
    fromKind.value = null
    fromRefId.value = null
    fromUser.value = ''
    fromLocation.value = ''
  } else {
    const src = sourceHolding.value
    fromKind.value = src ? (src.kind as LightHolderKind) : null
    fromRefId.value = src?.ref_id ?? null
    fromUser.value = src?.user ?? ''
    fromLocation.value = src?.stock_location ?? ''
  }
  // 「换个人 / 换个地方」：去向由 moveMode 决定（人 / 机器 / 点位）
  if (pickedKey.value === 'move') {
    toKind.value = moveMode.value
    toRefId.value = null
    toUser.value = ''
    toLocation.value = ''
    return
  }
  // 去向
  switch (a.to) {
    case 'stock':
    case 'stock-with-location':
      toKind.value = 'stock'
      toRefId.value = null
      toUser.value = ''
      if (a.to === 'stock') toLocation.value = ''
      break
    case 'user':
      toKind.value = 'user'
      toRefId.value = null
      break
    case 'asset-or-station':
      toKind.value = targetStation.value ? 'workstation' : 'asset'
      toUser.value = ''
      toLocation.value = ''
      break
    default:
      toKind.value = 'stock'
  }
  if (pickedKey.value === 'scrap') {
    // 报损的去向是"系统外部 / 注销"
    toKind.value = null
    toRefId.value = null
    toUser.value = ''
    toLocation.value = ''
  }
}

function chooseAction(a: BizAction) {
  pickedKey.value = a.key
  step.value = 'form'
  sourceKey.value = ''
  targetStation.value = false
  moveMode.value = 'user'
  quantity.value = 1
  origin.value = 'manual'
  // 预选来源：从某条分配记录点进来时，直接选它
  if (props.presetHoldingId != null) {
    const hit = sourceOptions.value.find((h) => h.id === props.presetHoldingId)
    if (hit) {
      sourceKey.value = holdingKey(hit)
      // 数量**恒为 1**，不跟着那一行走。
      //
      // 早先是"从 3 套那张卡点「收回库存」就默认 3 套"，本意是别让用户以为收干净了、
      // 实际只收回一件。但那是个矫枉过正：绝大多数登记就是一件一件地走，
      // 而"默认 N"会让只想收回一件的人**必须手动改小**，改错方向正好相反。
      // 现在改成界面把这条记录实际有几件**说出来**（见 sourceQtyHint），
      // 让人自己决定 —— 信息给到，默认值不替人做决定。
    }
  }
  if (!sourceKey.value && sourceOptions.value.length === 1) {
    sourceKey.value = holdingKey(sourceOptions.value[0])
  }
  applyAction()
}

function backToPick() {
  step.value = 'pick'
  pickedKey.value = null
  op.value = 'stock_in'
  fromKind.value = null
  toKind.value = 'stock'
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    pinnedCandidates.value = []
    holderCandidates.value = []
    requestId.value = newRequestId()
    operator.value = appState.operator
    note.value = ''
    origin.value = 'manual'
    occurredAt.value = localDateString()
    quantity.value = 1
    toLocation.value = ''
    toUser.value = ''
    toRefId.value = null
    backToPick()
    // 带动作进来（行上的按钮 / 资产详情页"登记配件变动"）：**同步**落到表单。
    // 之前这段在 `loadSuggestions().then()` 里，要等两个候选接口回来才切屏 ——
    // 于是"直达表单"会先闪一下"选业务动作"那一屏，肉眼看得到。
    // `chooseAction` 不依赖候选（候选只喂自由文本的 datalist），没理由等它。
    if (props.presetActionKey) {
      const action = ACTIONS.find((a) => a.key === props.presetActionKey)
      if (action) {
        chooseAction(action)
        applyPresetTarget()
      }
    }
    void loadSuggestions()
  },
  // ⚠️ `immediate` 不是可选项：资产详情页那条入口是**挂载时 `modelValue` 就已经是 true**
  // （`confirmPick()` 里 `dialogCategory` 与 `dialogOpen` 同一拍置位，组件随即挂载），
  // 所以只监听"变化"的话回调永远不触发 —— 预置的动作和去向全部被吞掉，
  // 用户点「登记配件变动」还是落在"选业务动作"那一屏。这正是「入口承诺了动作，行为却不认」。
  { immediate: true },
)

/**
 * 资产详情页带进来的去向（那台机器 / 那个点位）—— 与"选了哪个动作"无关，所以单独一段。
 *
 * ⚠️ 顺序有意义：`chooseAction` 会按动作把 `toKind` 定死（比如 `mount` 是 asset-or-station），
 * 这里再覆盖 `targetStation` / `toRefId`。`toRefId` 不会被后面的 `applyAction()` 清掉。
 */
function applyPresetTarget() {
  if (!props.presetTargetKind) return
  targetStation.value = props.presetTargetKind === 'workstation'
  toRefId.value = props.presetTargetRefId ?? null
  if (props.presetTargetRefId == null) return
  const code = props.presetTargetCode || `#${props.presetTargetRefId}`
  const candidate: LightHolderCandidate = {
    id: props.presetTargetRefId,
    code,
    label: code,
    hint: '',
    kind: props.presetTargetKind,
  }
  // 钉住它，见 pinnedCandidates 的说明
  pinnedCandidates.value = [candidate]
  holderCandidates.value = [candidate]
}

/**
 * 「装到某台机器 ↔ 放到某个点位」的切换 —— **顺手把已选对象清掉**。
 *
 * `toRefId` 是资产表与点位表**共用**的一个 id 空间，两边 id 会撞车：
 * 选好一台机器（asset#12）再切到「放到某个点位」，`toRefId` 还留着 12，
 * 而候选已经整批换成点位 —— `el-select` 找不到对应 option，就把 id 原样
 * 显示成 `12`；更糟的是**点「登记」会照着 12 号点位提交**：不报错，但对象是错的。
 *
 * ⚠️ 清空只能放在**这里**（用户手动切换的那一下），不能塞进 `applyAction()`：
 * 那条路径同时也被 `applyPresetTarget()` 走（资产详情页把对象带进来），
 * 在 `applyAction()` 里清会把预置的去向一起抹掉。
 */
function switchTargetStation(station: boolean) {
  if (targetStation.value === station) return
  targetStation.value = station
  toRefId.value = null
}

watch(sourceKey, () => {
  if (step.value === 'form') applyAction()
})
watch(targetStation, () => {
  if (step.value === 'form') applyAction()
})
watch(moveMode, () => {
  if (step.value === 'form') applyAction()
})

async function loadSuggestions() {
  try {
    const [lightFilters, assetFilters] = await Promise.all([lightApi.filters(), metaApi.filters()])
    const merged = new Set<string>([...lightFilters.users, ...assetFilters.users])
    merged.delete('')
    userSuggestions.value = [...merged].sort((a, b) => a.localeCompare(b, 'zh'))
    locationSuggestions.value = lightFilters.stock_locations
  } catch {
    /* 候选拿不到不拦着登记，只是没有下拉提示 */
  }
}

async function searchHolders(kind: 'asset' | 'workstation', keyword: string) {
  if (holderTimer) clearTimeout(holderTimer)
  holderTimer = setTimeout(async () => {
    holderLoading.value = true
    try {
      const list = await lightApi.holders(kind, keyword)
      const pinned = pinnedCandidates.value.filter(
        (p) => p.kind === kind && !list.some((c) => c.id === p.id),
      )
      holderCandidates.value = [...pinned, ...list]
    } catch {
      holderCandidates.value = [...pinnedCandidates.value.filter((p) => p.kind === kind)]
    } finally {
      holderLoading.value = false
    }
  }, 250)
}

// 去向变成"机器 / 点位"时才需要拉候选（mount 与 move 共用这一段）
watch(toKind, (kind) => {
  if (kind === 'asset' || kind === 'workstation') {
    void searchHolders(kind === 'workstation' ? 'workstation' : 'asset', '')
  }
})

onBeforeUnmount(() => {
  if (holderTimer) clearTimeout(holderTimer)
})

function holderLabel(c: LightHolderCandidate): string {
  return c.hint ? `${c.code} · ${c.label} · ${c.hint}` : `${c.code} · ${c.label}`
}

/* ---------------- 确认条：操作前 → 操作后 ---------------- */
interface PreviewRow {
  label: string
  from: string
  to: string
  changed: boolean
}

/** 找"某个去向位置"当前有多少（用来看它会不会变） */
function quantityAt(kind: LightHolderKind | null, refId: number | null, user: string, loc: string): number {
  if (!kind) return 0
  const hit = holdings.value.find((h) => {
    if (h.kind !== kind) return false
    if (kind === 'user') return (h.user ?? '') === user.trim()
    if (kind === 'stock') return (h.stock_location ?? '') === (loc.trim() || '')
    return h.ref_id === refId
  })
  return hit?.quantity ?? 0
}

const preview = computed<PreviewRow[]>(() => {
  const q = quantity.value
  const rows: PreviewRow[] = []
  const fmt = (n: number) => `${n} ${unit.value}`
  const mark = (label: string, before: number, after: number) => ({
    label,
    from: fmt(before),
    to: after <= 0 ? '无' : fmt(after),
    changed: after !== before,
  })

  const st = props.category.stat

  if (isReplace.value) {
    // 更换 = 旧件报损（-q 净存在）+ 新件从库存装上（库存 -q、该机器不变）
    rows.push(mark('库存', st.stock, st.stock - q))
    rows.push(mark('已分配', st.allocated, st.allocated))
    rows.push(mark('净存在', st.net, st.net - q))
    rows.push(mark('这台机器上', quantityAt('asset', fromRefId.value, '', ''), quantityAt('asset', fromRefId.value, '', '')))
    return rows
  }

  const HELD: LightHolderKind[] = ['user', 'asset', 'workstation']
  const stockDelta =
    (toKind.value === 'stock' ? q : 0) - (fromKind.value === 'stock' ? q : 0)
  const allocatedDelta =
    (toKind.value && HELD.includes(toKind.value) ? q : 0) -
    (fromKind.value && HELD.includes(fromKind.value as LightHolderKind) ? q : 0)
  const netDelta =
    (fromKind.value === null && toKind.value !== null ? q : 0) +
    (fromKind.value !== null && toKind.value === null ? -q : 0)

  rows.push(mark('库存', st.stock, st.stock + stockDelta))
  rows.push(mark('已分配', st.allocated, st.allocated + allocatedDelta))
  rows.push(mark('净存在', st.net, st.net + netDelta))

  // 具体位置：来源减、目标加
  if (fromKind.value) {
    const before = sourceHolding.value?.quantity ?? quantityAt(fromKind.value, fromRefId.value, fromUser.value, fromLocation.value)
    rows.unshift(mark(sourceHolding.value?.display ?? HOLDER_KIND_META[fromKind.value].label, before, before - q))
  }
  if (toKind.value && toKind.value !== 'stock') {
    const before = quantityAt(toKind.value, toRefId.value, toUser.value, '')
    const label =
      toKind.value === 'user'
        ? toUser.value.trim() || '（未填使用人）'
        : holderCandidates.value.find((c) => c.id === toRefId.value)?.code ?? '（未选）'
    rows.unshift(mark(label, before, before + q))
  } else if (toKind.value === 'stock' && toLocation.value.trim()) {
    const before = quantityAt('stock', null, '', toLocation.value)
    rows.unshift(mark(`库存（${toLocation.value.trim()}）`, before, before + q))
  }
  return rows
})

async function submit() {
  if (!flowOk.value) {
    ElMessage.warning(flowHint.value)
    return
  }
  if (needsSource.value && !sourceHolding.value) {
    ElMessage.warning('请选择从哪一条持有记录发起')
    return
  }
  if (!isReplace.value) {
    if (toKind.value === 'user' && !toUser.value.trim()) {
      ElMessage.warning('请填一下发给谁')
      return
    }
    if ((toKind.value === 'asset' || toKind.value === 'workstation') && toRefId.value === null) {
      ElMessage.warning(toKind.value === 'asset' ? '请选择装到哪台机器' : '请选择放到哪个点位')
      return
    }
    if (origin.value === 'opening' && !operator.value.trim()) {
      ElMessage.warning('期初确认必须填写操作人（是谁确认的这份基线）')
      return
    }
  }
  saving.value = true
  try {
    /*
      ⚠️ **不要 `new Date(...).toISOString()`**。它输出的是 UTC（带 Z），
      后端会把它当成本地时间存进去 —— 东八区就慢 8 小时，轻资产「起始于」
      和审计日志对不上（v2.9.8 事故）。
      这里直接拼本地 naive 字符串：日期来自选择器（YYYY-MM-DD），时间取当前时分。
    */
    const occurred = occurredAt.value ? `${occurredAt.value}T${timeNow()}:00` : null
    let result: LightMovementResult
    if (isReplace.value) {
      if (fromRefId.value === null) {
        ElMessage.warning('请选择是哪台机器上的配件')
        return
      }
      result = await lightApi.replacePart({
        category_id: props.category.id,
        asset_id: fromRefId.value,
        quantity: quantity.value,
        occurred_at: occurred,
        note: note.value.trim() || null,
        operator: operator.value.trim() || null,
        request_id: requestId.value,
      })
      ElMessage.success('已登记「更换配件」：旧件报损 + 新件装上（两条流水）')
    } else {
      result = await lightApi.createMovement({
        category_id: props.category.id,
        op: op.value,
        from_ref: {
          kind: fromKind.value ?? 'none',
          ref_id: fromRefId.value,
          user: fromUser.value.trim() || null,
          stock_location: fromKind.value === 'stock' ? fromLocation.value.trim() || null : null,
        },
        to_ref: {
          kind: toKind.value ?? 'none',
          ref_id: toRefId.value,
          user: toUser.value.trim() || null,
          stock_location: toKind.value === 'stock' ? toLocation.value.trim() || null : null,
        },
        quantity: quantity.value,
        occurred_at: occurred,
        note: note.value.trim() || null,
        operator: operator.value.trim() || null,
        origin: origin.value,
        request_id: requestId.value,
      })
      ElMessage.success(`已登记「${result.movements[0]?.op_label ?? opMeta(op.value).label}」`)
    }
    emit('saved', result)
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

/** 日期输入只给到天，时间补当前时分，让"补登昨天的账"有个合理钟点 */
function timeNow(): string {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`登记流转 · ${category.name}`"
    width="min(680px, 94vw)"
    @update:model-value="emit('update:modelValue', false)"
  >
    <!-- 第一步：这次要做什么 -->
    <div v-if="step === 'pick'">
      <div class="pick-title">这次要做什么？</div>
      <div class="act-grid">
        <button v-for="a in ACTIONS" :key="a.key" class="act" @click="chooseAction(a)">
          <span class="ico" :class="a.tone"><AppIcon :name="a.icon" :size="17" /></span>
          <span class="txt">
            <span class="t">{{ a.title }}</span>
            <span class="d">{{ a.desc }}</span>
          </span>
        </button>
      </div>
      <div class="infobox">
        看不出该点哪个？记住一句话：<b>先想"东西要变成什么样"，再想"它现在在哪"</b> ——
        界面会替你把它翻成系统认识的记录，不需要理解背后的数据模型。
      </div>
    </div>

    <!-- 第二步：只问这件事需要的信息 -->
    <div v-else-if="picked">
      <button class="back-link" @click="backToPick">
        <AppIcon name="close" :size="13" /> 换个操作
      </button>

      <div class="act-header">
        <span class="ico" :class="picked.tone"><AppIcon :name="picked.icon" :size="17" /></span>
        <div>
          <div class="ah-t">{{ picked.title }}</div>
          <div class="ah-d">{{ picked.desc }}</div>
        </div>
      </div>

      <!-- 从哪一条持有记录发起（数量都对得上，没有的不会出现在这里） -->
      <div v-if="needsSource" class="field">
        <label class="lbl">
          {{ isReplace ? '哪台机器上的配件' : '从哪里发起' }} <span class="req">*</span>
        </label>
        <el-select v-model="sourceKey" placeholder="选一个真的持有它的地方" style="width: 100%">
          <el-option
            v-for="h in sourceOptions"
            :key="holdingKey(h)"
            :value="holdingKey(h)"
            :label="`${h.display}（${h.quantity} ${category.unit}）`"
          />
        </el-select>
        <div class="tip">只列出真的持有这个品类的地方 —— 数量都对得上。</div>
        <div v-if="!sourceOptions.length" class="warnbox">
          这个品类现在没有任何符合条件的持有记录，所以没有可以"{{ picked.title }}"的对象。
          <template v-if="isReplace">（更换要求这台机器上确实装着这个品类，且库房里有新件。）</template>
        </div>
      </div>

      <!-- 换个人 / 换个地方：先选"换成哪种"（后端 transfer 支持 人↔机器↔点位 互转） -->
      <div v-if="!isReplace && picked.key === 'move'">
        <div class="mode-pick three">
          <button :class="{ on: moveMode === 'user' }" @click="moveMode = 'user'">换给某个人</button>
          <button :class="{ on: moveMode === 'asset' }" @click="moveMode = 'asset'">换到某台机器</button>
          <button :class="{ on: moveMode === 'workstation' }" @click="moveMode = 'workstation'">换到某个点位</button>
        </div>
      </div>

      <!-- 装到机器 / 放到点位 -->
      <div v-if="!isReplace && picked.key === 'mount'">
        <div class="mode-pick">
          <button :class="{ on: !targetStation }" @click="switchTargetStation(false)">装到某台机器</button>
          <button :class="{ on: targetStation }" @click="switchTargetStation(true)">放到某个点位</button>
        </div>
      </div>

      <!-- 发给某人（give 恒为人；move 选到"人"时才出现） -->
      <div v-if="!isReplace && toKind === 'user' && picked.key !== 'mount'" class="field">
        <label class="lbl">
          {{ picked.key === 'give' ? '发给谁' : '换给谁' }} <span class="req">*</span>
        </label>
        <!--
          与资产表单的「使用人」**同一套参数**（`filterable` + `default-first-option` +
          自带拼音筛选 + 新建项垫底）。**别再退回原生 datalist**：
          它不认拼音首字母，而打首字母正是这里选人的常规操作。
        -->
        <el-select
          v-model="toUser"
          placeholder="可直接输入新的人名，或打拼音首字母"
          filterable
          default-first-option
          clearable
          style="width: 100%"
          :filter-method="filterUsers"
          :reserve-keyword="false"
          @change="resetUserQuery"
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
      </div>

      <!-- 去向是机器 / 点位（mount 与 move 共用） -->
      <div v-if="!isReplace && targetNeedsPicker" class="field">
        <label class="lbl">{{ targetLabel }} <span class="req">*</span></label>
        <el-select
          v-model="toRefId"
          filterable
          remote
          :remote-method="(kw: string) => searchHolders(toKind === 'workstation' ? 'workstation' : 'asset', kw)"
          :loading="holderLoading"
          placeholder="搜编码 / 品牌型号 / 使用人"
          style="width: 100%"
        >
          <el-option v-for="c in holderCandidates" :key="c.id" :value="c.id" :label="holderLabel(c)" />
        </el-select>
        <div class="tip">输入资产编号或使用人姓名都能搜到。</div>
      </div>

      <!-- 去往库存时的存放位置（可留空 = 未填位置） -->
      <div
        v-if="!isReplace && (picked.key === 'stock_in' || picked.key === 'back')"
        class="field"
      >
        <label class="lbl">存放到哪里（选填）</label>
        <el-input
          v-model="toLocation"
          maxlength="128"
          placeholder="选填，如：3 楼库房A柜 / 前台抽屉 / W01"
          list="light-location-suggestions"
        />
        <div class="tip">
          可以是自由描述，也可以填某个点位的编码。留空就是"没记录存放位置"的库存。
        </div>
      </div>

      <!-- 入库：日常 / 期初 -->
      <div v-if="picked.key === 'stock_in'" class="field">
        <label class="lbl">这是怎么进来的 <span class="req">*</span></label>
        <el-radio-group v-model="origin">
          <el-radio-button value="manual">日常采购入库</el-radio-button>
          <el-radio-button value="opening">期初确认（盘点基线）</el-radio-button>
        </el-radio-group>
        <div class="tip">
          期初确认用于建立真实基线，必须填操作人。数量多、位置分散时，
          更推荐在品类详情页用「按实际盘点结果登记期初」一次登记清楚。
        </div>
      </div>

      <div v-if="picked.key === 'scrap'" class="warnbox">
        报损 = 彻底退出管理，之后不能再走「入库」复活。
        如果是误报损、实物还在，请改用<b>「调整（盘盈）」</b>重新登记进库存。
      </div>
      <div v-if="isReplace" class="infobox">
        更换会一次写<b>两条</b>流水：旧件报损（退出管理）+ 新件从库存装上。
        同一个事务，中间不会出现"这台机器少一根"的假象。
      </div>

      <div class="two">
        <div class="field">
          <label class="lbl">数量</label>
          <el-input-number v-model="quantity" :min="1" :max="100000" style="width: 100%" />
          <div v-if="sourceQtyHint" class="tip">{{ sourceQtyHint }}</div>
        </div>
        <div class="field">
          <label class="lbl">发生日期</label>
          <el-date-picker
            v-model="occurredAt"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="默认今天"
            style="width: 100%"
          />
        </div>
        <div class="field">
          <label class="lbl">操作人</label>
          <el-input v-model="operator" maxlength="64" placeholder="谁登记的这一笔" />
        </div>
        <div class="field">
          <label class="lbl">备注</label>
          <el-input v-model="note" maxlength="255" placeholder="选填，如：替换下来的旧件放回仓库" />
        </div>
      </div>

      <div v-if="!flowOk" class="warnbox">{{ flowHint }}</div>

      <!-- 确认条 -->
      <div v-if="flowOk" class="preview">
        <div class="ttl">确认一下这次会发生什么</div>
        <table>
          <tbody>
            <tr v-for="(r, i) in preview" :key="i">
              <td class="k">{{ r.label }}</td>
              <td class="v">
                <span class="was">{{ r.from }}</span>
                <span class="arr">→</span>
                <span :class="r.changed ? 'chg' : 'same'">{{ r.to }}</span>
                <span v-if="!r.changed" class="same">（不变）</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <datalist id="light-location-suggestions">
      <option v-for="l in locationSuggestions" :key="l" :value="l" />
    </datalist>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button
        v-if="step === 'form'"
        type="primary"
        :loading="saving"
        :disabled="!flowOk || (needsSource && !sourceOptions.length)"
        @click="submit"
      >
        登记
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pick-title {
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 12px;
}

.act-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
@media (max-width: 640px) {
  .act-grid {
    grid-template-columns: 1fr;
  }
}
.act {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  text-align: left;
  padding: 13px 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.act:hover {
  border-color: var(--el-color-primary);
  background: #fbfcff;
  box-shadow: var(--shadow-sm);
}
.ico {
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.ico.g { background: #e8f8e5; color: #1f8a17; }
.ico.a { background: #fff4e6; color: #d2661d; }
.ico.v { background: #f1ebfb; color: #7a4bd0; }
.ico.r { background: #ffeceb; color: #d13b36; }
.act .txt {
  display: flex;
  flex-direction: column;
}
.act .t {
  display: block;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-1);
}
.act .d {
  display: block;
  font-size: 12px;
  color: var(--text-2);
  margin-top: 3px;
  line-height: 1.55;
}

.infobox {
  margin-top: 14px;
  padding: 11px 13px;
  border-radius: var(--radius-sm);
  background: #f2f6ff;
  border: 1px solid #d6e2ff;
  color: #33507e;
  font-size: 12.5px;
  line-height: 1.7;
}
.infobox b { color: #24406e; }

.warnbox {
  margin-top: 12px;
  padding: 11px 13px;
  border-radius: var(--radius-sm);
  background: #fff4e6;
  border: 1px solid #ffdba8;
  color: #8a4700;
  font-size: 12.5px;
  line-height: 1.7;
}
.warnbox b { color: #6d3800; }

.back-link {
  /* 图标 + 文字：`AppIcon` 是 block svg，靠 flex 排一行（别再写 vertical-align 补丁） */
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
  padding: 0 0 12px;
  cursor: pointer;
}
.back-link:hover { color: var(--el-color-primary); }

.act-header {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  margin-bottom: 16px;
}
.ah-t { font-size: 14px; font-weight: 600; color: var(--text-1); }
.ah-d { font-size: 12.5px; color: var(--text-2); margin-top: 2px; }

.field {
  margin-bottom: 14px;
}
.field .lbl {
  display: block;
  font-size: 13px;
  color: var(--text-2);
  margin-bottom: 5px;
}
.field .lbl .req { color: var(--el-color-danger); }
.field .tip {
  font-size: 11.5px;
  color: var(--text-3);
  margin-top: 4px;
  line-height: 1.6;
}

.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 14px;
}
@media (max-width: 640px) {
  .two { grid-template-columns: 1fr; }
}

.mode-pick {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
  padding: 3px;
  background: #f2f3f5;
  border-radius: var(--radius-sm);
}
.mode-pick button {
  flex: 1;
  border: 0;
  background: transparent;
  padding: 7px 10px;
  border-radius: 6px;
  font-size: 12.5px;
  color: var(--text-2);
  cursor: pointer;
}
.mode-pick button.on {
  background: var(--panel);
  color: var(--text-1);
  box-shadow: var(--shadow-sm);
  font-weight: 500;
}
.mode-pick.three button { font-size: 12px; padding: 7px 6px; }

.preview {
  margin-top: 16px;
  padding: 12px 14px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.preview .ttl {
  font-size: 12.5px;
  color: var(--text-2);
  margin-bottom: 9px;
}
.preview table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.preview td {
  padding: 4px 0;
  vertical-align: top;
}
.preview td.k {
  color: var(--text-2);
  width: 40%;
}
.preview td.v {
  color: var(--text-1);
  font-weight: 500;
}
.preview .was { color: var(--text-3); font-weight: 400; }
.preview .arr { color: var(--text-3); margin: 0 5px; }
/* 变的用琥珀色强调；不变的用普通深色 + "（不变）" —— 别让"没变化"读成警示 */
.preview .chg { color: var(--el-color-warning); font-weight: 600; }
.preview .same { color: var(--text-1); }
.preview td.v .same { color: var(--text-3); font-weight: 400; }
</style>
