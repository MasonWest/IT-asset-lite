<script setup lang="ts">
/**
 * 轻资产品类详情。
 *
 * 两个页签对应两种"看同一个品类"的方式：
 * - 当前分配：**现在在哪、各多少**（读 `light_holdings` 快照，不重放历史）；
 * - 流转流水：全部来龙去脉（含历史导入 —— 那部分不计入当前账）。
 *
 * ⚠️ 「单件清单」页签已随单件追踪一起取消：统一按「品类 + 数量」管理。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import LightCategoryDialog from '@/components/LightCategoryDialog.vue'
import LightMovementDialog from '@/components/LightMovementDialog.vue'
import LightOpeningDialog from '@/components/LightOpeningDialog.vue'
import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightCategory, LightHolding, LightMovement, LightMovementPage, LightOrigin } from '@/types'
import { formatDateTime, orDash } from '@/utils/format'
import { holderIconName } from '@/utils/light'

const route = useRoute()
const router = useRouter()

const categoryId = computed(() => Number(route.params.categoryId))

const loading = ref(false)
const category = ref<LightCategory | null>(null)

/* ---------------- 流水 ---------------- */
const movements = ref<LightMovement[]>([])
const movementsTotal = ref(0)
const movementsLoading = ref(false)
const movementFilters = reactive({ op: '', origin: '', keyword: '' })
const movementPage = ref(1)
const movementPageSize = ref(20)
const activeTab = ref<'hold' | 'flow'>('hold')
/**
 * 「当前分配」的两种排法，同一份 `stat.holdings` 换个排法而已（**不重新请求**）。
 *
 * 卡片看"每条是谁、能对它做什么"；列表看"一共几条、谁排在前面、数量对不对得上"——
 * 分配记录一多，卡片要横向扫一大片才数得清。默认仍是卡片（现状不变）。
 */
const holdViewMode = ref<'card' | 'table'>('card')

/* ---------------- 对话框 ---------------- */
const movementDialogOpen = ref(false)
/** 从某条分配记录发起（收回 / 换手）时预选的那一条 */
const movementPresetHoldingId = ref<number | null>(null)
/** 连同动作一起预选 —— 行上的按钮要**直达表单**，不该再让人在"选业务动作"那一屏重选一次 */
const movementPresetActionKey = ref<string | null>(null)
const categoryDialogOpen = ref(false)
const openingDialogOpen = ref(false)

async function loadCategory(): Promise<LightCategory | null> {
  loading.value = true
  try {
    category.value = await lightApi.category(categoryId.value)
    return category.value
  } catch (error) {
    ElMessage.error(apiMessage(error))
    return null
  } finally {
    loading.value = false
  }
}

async function loadMovements() {
  movementsLoading.value = true
  try {
    const res: LightMovementPage = await lightApi.movements({
      category_id: categoryId.value,
      op: movementFilters.op || undefined,
      origin: movementFilters.origin || undefined,
      keyword: movementFilters.keyword.trim() || undefined,
      page: movementPage.value,
      page_size: movementPageSize.value,
    })
    movements.value = res.items
    movementsTotal.value = res.total
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    movementsLoading.value = false
  }
}

function reloadAll() {
  // 切品类时先清掉上一个品类的内容，否则新品类还没回来时会短暂显示旧数据
  category.value = null
  movements.value = []
  movementsTotal.value = 0
  void loadCategory()
  void loadMovements()
}

watch(categoryId, reloadAll)
onMounted(reloadAll)

/* ---------------- 操作 ---------------- */
function openMovement(presetHoldingId: number | null = null, presetActionKey: string | null = null) {
  movementPresetHoldingId.value = presetHoldingId
  movementPresetActionKey.value = presetActionKey
  movementDialogOpen.value = true
}

function onMovementSaved() {
  void loadCategory()
  void loadMovements()
}

async function reverse(movement: LightMovement) {
  if (!movement.can_reverse) {
    // 后端没给机械反向（报损 / 安装自外部），说明这里该由人决定走哪条路。
    ElMessage.info(
      `「${movement.op_label}」没有机械冲正 —— 请用对应的操作来更正（例如误报损可以用「调整（盘盈）」把实物重新登记进库存）`,
    )
    return
  }
  let reason: string | null = null
  try {
    const { value } = await ElMessageBox.prompt(
      `冲正流水 #${movement.id}（${movement.op_label}）？系统会「追加一条反向记录」，原记录一个字不改。请填写冲正原因：`,
      '冲正确认',
      { confirmButtonText: '冲正', cancelButtonText: '取消', inputPlaceholder: '如：登记错了，实际发给员工C' },
    )
    reason = value
  } catch {
    return
  }
  try {
    const result = await lightApi.reverseMovement(movement.id, {
      note: reason,
      operator: appState.operator || null,
    })
    ElMessage.success(`已冲正，追加了一条「${result.movements[0]?.op_label ?? '反向'}」`)
    reloadAll()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

const originMeta: Record<LightOrigin, { label: string; type: 'success' | 'warning' | 'info' }> = {
  manual: { label: '日常', type: 'success' },
  opening: { label: '期初', type: 'warning' },
  import: { label: '历史导入', type: 'info' },
}

function originTag(origin: string): { label: string; type: 'success' | 'warning' | 'info' } {
  return originMeta[origin as LightOrigin] ?? { label: origin, type: 'info' }
}

/** 一条分配记录的副标题：位置本身的说明 + 起点时间 */
function holdingSub(h: LightHolding): string {
  const parts: string[] = [h.kind_label]
  if (h.kind === 'asset' && h.host_user) parts.push(`主机使用人 ${h.host_user}`)
  if (h.kind === 'workstation' && h.host_workstation_code) parts.push(`点位 ${h.host_workstation_code}`)
  if (h.kind === 'stock' && h.stock_location) parts.push(`存放于 ${h.stock_location}`)
  if (h.hint) parts.push(h.hint)
  return parts.join(' · ')
}

/**
 * 每条分配记录上该放哪些按钮 —— **按持有类型分工**。
 *
 * ## 上一版这里是错的
 *
 * 原来两条入口（「收回库存」「换成别的持有方」）调的是同一个 `openMovement(row.id)`，
 * 点进去都落在"选业务动作"那一屏：文案承诺了两件不同的事，行为一模一样。
 *
 * 更糟的是**库存行也挂着这两个按钮**（`current_holdings()` 把 `holder_kind='stock'` 排在最前，
 * 而这里没有按 kind 过滤）：「收回库存」对应的 `return` 根本不接受 `stock` 来源，
 * 传进去的行 id 在对话框里匹配不上、被静默丢弃；品类恰好只有一条借出记录时，
 * 它还会走"只有一条就自动选中"的兜底 —— 从**库存卡**点进去，动的却是**别人的**那条。
 *
 * ## 现在
 *
 * 每个按钮都带 `action`（= 对话框的 `presetActionKey`）**直达表单**，来源由行 id 预选，
 * 并且严格按 kind 给：「库存」上没有「收回库存」（它就在库存里），
 * 人手上 / 机器上 / 点位上没有「发给某人」（先收回，或用「转移」直接换手）。
 * 顺带补齐了两个此前**没有任何行内入口**的动作：报损、更换配件。
 */
interface RowAction {
  key: string
  label: string
  /** 对应 LightMovementDialog 里 ACTIONS 的 key */
  action: string
  /** 破坏性动作，界面上给个克制的红 */
  danger?: boolean
}

const ROW_ACTIONS: Record<string, RowAction[]> = {
  stock: [
    { key: 'give', label: '发给某人', action: 'give' },
    { key: 'mount', label: '装到机器 / 放到点位', action: 'mount' },
    { key: 'scrap', label: '报损', action: 'scrap', danger: true },
  ],
  user: [
    { key: 'back', label: '收回库存', action: 'back' },
    { key: 'move', label: '换个人 / 换个地方', action: 'move' },
    { key: 'scrap', label: '报损', action: 'scrap', danger: true },
  ],
  asset: [
    { key: 'back', label: '收回库存', action: 'back' },
    // 措辞与对话框里的默认去向一致：转移默认落在"换给人"，机器/点位是第二个选项
    { key: 'move', label: '换个人 / 换台机器', action: 'move' },
    { key: 'replace', label: '更换配件', action: 'replace' },
    { key: 'scrap', label: '报损', action: 'scrap', danger: true },
  ],
  workstation: [
    { key: 'back', label: '收回库存', action: 'back' },
    { key: 'move', label: '换个人 / 换个点位', action: 'move' },
    { key: 'scrap', label: '报损', action: 'scrap', danger: true },
  ],
}

/** 拿不到 kind（或未来的新 kind）时**宁可不显示按钮**，也不给一个点下去必然报错的入口 */
function rowActions(row: LightHolding): RowAction[] {
  if (!row.kind) return []
  return ROW_ACTIONS[row.kind] ?? []
}

/** 流水的圆形节点样式：按 op 的语义分色，扫一眼就能看出"进 / 出 / 换手 / 报损"。 */
function flowPipClass(op: string): string {
  if (op === 'stock_in' || op === 'adjust') return 'in'
  if (op === 'allocate' || op === 'return') return 'out'
  if (op === 'transfer') return 'move'
  if (op === 'scrap') return 'bad'
  return ''
}

function flowIconName(op: string): string {
  switch (op) {
    case 'stock_in':
    case 'adjust':
      return 'arrowIn'
    case 'allocate':
      return 'user'
    case 'attach':
      return 'wrench'
    case 'detach':
    case 'return':
      return 'arrowOut'
    case 'transfer':
      return 'swap'
    case 'scrap':
      return 'trash'
    default:
      return 'box'
  }
}
</script>

<template>
  <div v-if="category" v-loading="loading">
    <div class="page-head">
      <div>
        <div class="crumb">
          <router-link to="/light" class="link">轻资产</router-link>
          <span class="sep">/</span>
          <span>{{ category.name }}</span>
        </div>
        <h1>
          {{ category.name }}
          <span class="tag" :class="category.active ? 'ok' : 'off'">
            {{ category.active ? '启用中' : '已停用' }}
          </span>
        </h1>
        <p class="sub">
          {{ [category.brand, category.spec].filter(Boolean).join(' · ') || '未填品牌规格' }}
          · 单位：{{ category.unit }}
          · <router-link to="/light-help" class="sub-link">口径说明</router-link>
          <template v-if="category.notes"> · {{ category.notes }}</template>
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="categoryDialogOpen = true">编辑品类</el-button>
        <el-button type="primary" :disabled="!category.active" @click="openMovement()">
          <AppIcon name="plus" :size="15" style="margin-right: 5px" />登记流转
        </el-button>
      </div>
    </div>

    <div class="stat-row light-stat-row">
      <div class="stat-card light-stat tone-blue">
        <span class="bar" />
        <span class="k"><AppIcon name="box" :size="15" />库存</span>
        <span class="v">{{ category.stat.pending_opening ? '未登记' : category.stat.stock
          }}<small v-if="!category.stat.pending_opening">{{ category.unit }}</small></span>
        <span class="hint">还在库里、没分配出去</span>
      </div>
      <div class="stat-card light-stat tone-green">
        <span class="bar" />
        <span class="k"><AppIcon name="users" :size="15" />已分配</span>
        <span class="v">{{ category.stat.allocated }}<small>{{ category.unit }}</small></span>
        <span class="hint">在人手上 / 装在机器上 / 放在点位上</span>
      </div>
      <div class="stat-card light-stat tone-violet">
        <span class="bar" />
        <span class="k"><AppIcon name="sum" :size="15" />净存在</span>
        <span class="v">{{ category.stat.net }}<small>{{ category.unit }}</small></span>
        <span class="hint">库存 + 已分配</span>
      </div>
      <div class="stat-card light-stat tone-amber">
        <span class="bar" />
        <span class="k"><AppIcon name="list" :size="15" />历史流水</span>
        <span class="v">{{ category.stat.imported_count }}<small>条</small></span>
        <span class="hint">只追溯、不计当前账</span>
      </div>
    </div>

    <el-alert
      v-if="category.stat.pending_opening"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 14px"
    >
      <template #title>有历史流水，但当前账是空的</template>
      <div style="font-size: 12.5px; line-height: 1.7; margin-top: 2px">
        历史导入只补"过去发生过什么"，<b>不会</b>变成现在的账（Excel 里通常没有对应的入库记录，
        拿来当现状会算出负库存）。请按实际盘点结果登记一次「期初确认」，把真实基线建起来。
        <div style="margin-top: 8px">
          <el-button size="small" type="primary" :disabled="!category.active" @click="openingDialogOpen = true">
            按实际盘点结果登记期初
          </el-button>
        </div>
      </div>
    </el-alert>

    <el-tabs v-model="activeTab">
      <!-- 当前分配：直接读 light_holdings 的快照 -->
      <el-tab-pane label="当前分配" name="hold">
        <!-- 同一份 holdings 的两种排法，切换不重新请求 -->
        <div v-if="category.stat.holdings.length" class="hold-toolbar">
          <span class="hold-count">共 {{ category.stat.holdings.length }} 条持有记录</span>
          <div class="view-toggle">
            <button :class="{ on: holdViewMode === 'card' }" @click="holdViewMode = 'card'">
              <AppIcon name="grid" :size="14" />卡片
            </button>
            <button :class="{ on: holdViewMode === 'table' }" @click="holdViewMode = 'table'">
              <AppIcon name="list" :size="14" />列表
            </button>
          </div>
        </div>

        <div
          v-if="category.stat.holdings.length && holdViewMode === 'card'"
          class="hold-grid"
        >
          <div v-for="row in category.stat.holdings" :key="row.id" class="hold-card">
            <span class="av">
              <AppIcon :name="holderIconName(row.kind)" :size="17" />
            </span>
            <div class="body">
              <div class="who">
                {{ row.display }}
                <el-tag v-if="!row.alive" type="danger" size="small" effect="plain">宿主已删除</el-tag>
              </div>
              <div class="sub">
                {{ holdingSub(row) }}
                <template v-if="row.since"> · 起始于 {{ formatDateTime(row.since) }}</template>
              </div>
              <div v-if="category.active" class="acts">
                <button
                  v-for="a in rowActions(row)"
                  :key="a.key"
                  class="lk"
                  :class="{ danger: a.danger }"
                  @click="openMovement(row.id, a.action)"
                >
                  {{ a.label }}
                </button>
              </div>
            </div>
            <div class="cnt">{{ row.quantity }}<small>{{ category.unit }}</small></div>
          </div>
        </div>

        <!--
          列表视图：与「轻资产台账」「正式资产台账」的表格同一套语言 ——
          无外框 / 无竖线 / 无斑马纹，只留横向细线，外面包一层白面板。
          它就是上面那堆卡片的另一种排法（**不重新请求**），
          分配记录一多，卡片要横向扫一大片才数得清"一共几条"。
        -->
        <div v-else class="panel table-panel">
          <el-table :data="category.stat.holdings" row-key="id">
            <el-table-column label="持有方" min-width="200">
              <template #default="{ row }">
                <div class="hold-cell">
                  <span class="ic"><AppIcon :name="holderIconName(row.kind)" :size="16" /></span>
                  <span class="nm">{{ row.display }}</span>
                  <el-tag v-if="!row.alive" type="danger" size="small" effect="plain">
                    宿主已删除
                  </el-tag>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="位置说明" min-width="230">
              <template #default="{ row }">
                <span class="sub-t">{{ holdingSub(row) }}</span>
              </template>
            </el-table-column>

            <el-table-column label="起始时间" width="150">
              <template #default="{ row }">
                <span v-if="row.since" class="date">{{ formatDateTime(row.since) }}</span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>

            <el-table-column label="数量" width="100" align="right">
              <template #default="{ row }">
                <span class="num">{{ row.quantity }}<small>{{ category.unit }}</small></span>
              </template>
            </el-table-column>

            <el-table-column label="操作" min-width="230" align="right">
              <template #default="{ row }">
                <div v-if="category.active" class="row-acts">
                  <button
                    v-for="a in rowActions(row)"
                    :key="a.key"
                    class="lk"
                    :class="{ danger: a.danger }"
                    @click="openMovement(row.id, a.action)"
                  >
                    {{ a.label }}
                  </button>
                </div>
                <span v-else class="muted">品类已停用</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <!--
          已经有分配记录了，所以那条"去做期初"的提示会收起来（设计 §6.1）——
          但**期初允许补录 / 重做**（漏录了品类、盘点发现数量不对），
          所以这里留一个低调的入口，别让"允许补录"只停留在接口层面。
        -->
        <div v-if="category.stat.holdings.length && category.opening_done" class="hold-foot">
          <span>漏录了、或盘点发现数量不对？</span>
          <button class="lk" :disabled="!category.active" @click="openingDialogOpen = true">
            补登期初（按实际盘点结果）
          </button>
          <span class="sep">·</span>
          <span>只想改数量请用「登记流转 → 调整（盘盈 / 盘亏）」，那样会留审计。</span>
        </div>

        <!-- 空态：给出路，不是只解释。
             ⚠️ 条件必须显式写 `!holdings.length` —— 原来这里是个光秃秃的 `v-else`，
             绑在上面「hold-foot」那条 `v-if="holdings.length && opening_done"` 上，
             于是**有分配记录、但没做过期初确认**（比如第一笔就是"日常采购入库"）时，
             界面会一边列着持有方、一边弹「现在没有任何分配记录」。
             `opening_done` 只是"提示用"的标记（见 types.ts），当不了空态判据。 -->
        <div v-else-if="!category.stat.holdings.length" class="empty-box">
          <div class="icon"><AppIcon name="box" :size="23" /></div>
          <h3>现在没有任何分配记录</h3>
          <p>
            <template v-if="category.stat.pending_opening">
              有历史流水，但当前账是空的 —— 这是诚实的空态（历史导入不代表现状）。
              按实际盘点结果登记一次 <b>期初确认</b>，把真实库存建起来。
            </template>
            <template v-else>
              这个品类建好之后一次都没动过。登记第一笔「入库」或「期初确认」，账就开始走了。
            </template>
          </p>
          <div class="row">
            <el-button type="primary" :disabled="!category.active" @click="openMovement()">
              登记流转
            </el-button>
            <el-button :disabled="!category.active" @click="openingDialogOpen = true">
              按实际盘点结果登记期初
            </el-button>
            <el-button @click="activeTab = 'flow'">查看流转流水</el-button>
            <el-button link type="primary" @click="router.push('/light-help')">为什么不能直接改库存？</el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- 流转流水 -->
      <el-tab-pane label="流转流水">
        <div class="filter-bar">
          <el-select
            v-model="movementFilters.op"
            placeholder="全部操作"
            clearable
            style="width: 140px"
            @change="movementPage = 1; loadMovements()"
          >
            <el-option label="入库" value="stock_in" />
            <el-option label="分配" value="allocate" />
            <el-option label="安装" value="attach" />
            <el-option label="转移" value="transfer" />
            <el-option label="拆卸" value="detach" />
            <el-option label="归还" value="return" />
            <el-option label="调整" value="adjust" />
            <el-option label="报损" value="scrap" />
          </el-select>
          <el-select
            v-model="movementFilters.origin"
            placeholder="全部来源"
            clearable
            style="width: 140px"
            @change="movementPage = 1; loadMovements()"
          >
            <el-option label="日常登记" value="manual" />
            <el-option label="期初确认" value="opening" />
            <el-option label="历史导入" value="import" />
          </el-select>
          <el-input
            v-model="movementFilters.keyword"
            class="grow"
            placeholder="搜索备注 / 操作人 / 使用人"
            clearable
            style="max-width: 280px"
            @change="movementPage = 1; loadMovements()"
            @clear="movementPage = 1; loadMovements()"
          />
        </div>
        <!-- 时间线：比密集表格更读得出"东西按什么顺序去过哪" -->
        <div v-if="movements.length" class="flow" v-loading="movementsLoading">
          <div v-for="row in movements" :key="row.id" class="flow-item">
            <div class="rail">
              <span class="pip" :class="flowPipClass(row.op)">
                <AppIcon :name="flowIconName(row.op)" :size="15" />
              </span>
            </div>
            <div class="flow-body">
              <div class="line1">
                <span class="op">{{ row.op_label }}</span>
                <span class="pill" :class="row.origin">{{ originTag(row.origin).label }}</span>
                <span v-if="row.corrects_id" class="pill fix">冲正 #{{ row.corrects_id }}</span>
                <span class="when">{{ formatDateTime(row.occurred_at) }} · {{ orDash(row.operator) }}</span>
              </div>
              <div class="path">
                <span class="frm" :class="{ dim: !row.from_holder.display }">
                  {{ row.from_holder.display || '系统外部' }}
                </span>
                <span class="arr">→</span>
                <span class="to">{{ row.to_holder.display || '已注销 / 报损' }}</span>
                <span class="qty">· {{ row.quantity }} {{ category.unit }}</span>
              </div>
              <!-- 没有机械反向的流水（报损 / 安装自外部）不给死按钮，直接说明该怎么做 -->
              <div class="note">
                <span class="note-text">{{ row.note }}</span>
                <span v-if="!row.can_reverse" class="no-rev">
                  {{
                    row.origin === 'import'
                      ? '历史导入只用于追溯，不计入当前库存与持有，不需要冲正。'
                      : '这条没有机械冲正 —— 如果记错了，请用对应的操作更正（误报损可用「调整（盘盈）」把实物重新登记进库存）。'
                  }}
                </span>
                <button v-if="row.can_reverse" class="lk" @click="reverse(row)">
                  <AppIcon name="undo" :size="13" />冲正
                </button>
              </div>
            </div>
          </div>
        </div>
        <div v-else-if="!movementsLoading" class="empty-box">
          <div class="icon"><AppIcon name="list" :size="23" /></div>
          <h3>还没有任何流水</h3>
          <p>这个品类建好之后一次都没动过。登记第一笔「入库」或「期初确认」，账就开始走了。</p>
          <div class="row">
            <el-button type="primary" :disabled="!category.active" @click="openMovement()">登记第一笔</el-button>
            <el-button link type="primary" @click="router.push('/light-help')">看一下怎么用</el-button>
          </div>
        </div>
        <div v-else class="flow-loading">正在读取…</div>
        <div class="pager">
          <el-pagination
            v-model:current-page="movementPage"
            v-model:page-size="movementPageSize"
            :total="movementsTotal"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            background
            @current-change="loadMovements"
            @size-change="movementPage = 1; loadMovements()"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <LightMovementDialog
      v-model="movementDialogOpen"
      :category="category"
      :preset-holding-id="movementPresetHoldingId"
      :preset-action-key="movementPresetActionKey"
      @saved="onMovementSaved"
    />

    <LightOpeningDialog v-model="openingDialogOpen" :category="category" @saved="onMovementSaved" />

    <LightCategoryDialog v-model="categoryDialogOpen" :category="category" @saved="loadCategory" />
  </div>
</template>

<style scoped>
.crumb {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 4px;
}
.crumb .link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.crumb .link:hover {
  text-decoration: underline;
}
.crumb .sep {
  margin: 0 6px;
}

h1 {
  display: flex;
  align-items: center;
  gap: 10px;
}

.muted {
  color: var(--text-3);
}
.dim {
  color: var(--text-3);
}

.sub-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.sub-link:hover {
  text-decoration: underline;
}

/* 状态标签 */
.tag {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
  border: 1px solid;
  vertical-align: 2px;
}
.tag.ok {
  background: #e8f8e5;
  color: #1f8a17;
  border-color: #c8ebc2;
}
.tag.off {
  background: #f2f3f5;
  color: var(--text-3);
  border-color: var(--border);
}

/* ---------- 统计卡：线性图标 + 左侧语义色条 ---------- */
.light-stat-row {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.light-stat {
  position: relative;
  overflow: hidden;
}
.light-stat .bar {
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: currentColor;
}
.light-stat .k {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
}
.light-stat .v {
  display: block;
  color: var(--text-1);
}
.light-stat .v small {
  color: var(--text-3);
}
.light-stat .hint {
  display: block;
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--text-3);
}
.light-stat.tone-blue { color: var(--el-color-primary); }
.light-stat.tone-violet { color: #7a4bd0; }
.light-stat.tone-amber { color: var(--el-color-warning); }
.light-stat.tone-green { color: #34c724; }

/* ---------- 当前分配：工具条（卡片 / 列表 切换） ---------- */
.hold-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.hold-count {
  font-size: 12.5px;
  color: var(--text-3);
}
/*
 * 卡片 / 列表 切换 —— 与流转弹窗的 `.mode-pick` 同一套分段控件语言
 * （灰底槽 + 白色活动块），这两处都是"同一份数据换个排法"，长得该一样。
 *
 * ⚠️ 按钮必须 `inline-flex`：`AppIcon` 渲染的是 `display:block` 的 svg，
 * 放进普通 `<button>` 会独占一行、文字被顶到第二行（决策 88）。
 */
.view-toggle {
  display: flex;
  gap: 2px;
  padding: 3px;
  background: #f2f3f5;
  border-radius: var(--radius-sm);
}
.view-toggle button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  padding: 4px 10px;
  border-radius: 6px;
  font-family: inherit;
  font-size: 12.5px;
  line-height: 18px;
  color: var(--text-2);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.view-toggle button.on {
  background: var(--panel);
  color: var(--text-1);
  box-shadow: var(--shadow-sm);
  font-weight: 500;
}

/* ---------- 分配卡片 ---------- */
.hold-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: 12px;
}
.hold-card {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  padding: 12px 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.hold-card .av {
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-2);
}
.hold-card .body {
  flex: 1;
  min-width: 0;
}
.hold-card .who {
  font-size: 13.5px;
  font-weight: 600;
}
.hold-card .sub {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 3px;
  line-height: 1.6;
}
.hold-card .acts {
  margin-top: 7px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.hold-card .acts .lk {
  font-size: 12px;
  padding: 2px 7px;
}
.hold-card .cnt {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.hold-card .cnt small {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
  margin-left: 2px;
}

/* 已经有分配记录时，期初「补录」的低调入口 */
.hold-foot {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--text-3);
}
.hold-foot .sep {
  color: var(--border-strong);
}
.hold-foot .lk:disabled {
  color: var(--text-3);
  cursor: not-allowed;
}

/* ---------- 当前分配的列表视图 ----------
   与「轻资产台账」「正式资产台账」的表格同一套语言：无外框 / 无竖线 / 无斑马纹
   （`border stripe` 是上一版的做法，满屏网格 + 条纹），只留横向细线，
   外面包一层白色面板。同一份 holdings，只是换个排法。 */
.table-panel {
  padding: 4px 4px 0;
}
/* 行高比 Element 默认舒展一点：一行就是一条持有记录，别挤成一团 */
.table-panel :deep(.el-table__cell) {
  padding: 11px 0;
}

.hold-cell {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}
.hold-cell .ic {
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-2);
}
.hold-cell .nm {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-1);
}
.sub-t {
  font-size: 12.5px;
  color: var(--text-2);
}
.date {
  font-size: 12.5px;
  color: var(--text-2);
  font-variant-numeric: tabular-nums;
}
.num {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.num small {
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-3);
  margin-left: 2px;
}
/* 操作列右对齐 + 允许换行（`asset` 类有四个动作，窄屏会折成两行） */
.row-acts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: flex-end;
}

/* ---------- 空态：给出路，不是只解释 ---------- */
.empty-box {
  padding: 32px 20px;
  text-align: center;
  background: var(--panel);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius);
}
.empty-box .icon {
  width: 46px;
  height: 46px;
  margin: 0 auto 12px;
  border-radius: 13px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  display: grid;
  place-items: center;
}
.empty-box h3 {
  margin: 0 0 6px;
  font-size: 15px;
  color: var(--text-1);
}
.empty-box p {
  margin: 0 auto 16px;
  max-width: 500px;
  color: var(--text-2);
  font-size: 13px;
  line-height: 1.8;
}
.empty-box p b {
  color: var(--text-1);
}
.empty-box .row {
  display: flex;
  gap: 8px;
  justify-content: center;
  flex-wrap: wrap;
}

/* ---------- 流水时间线 ---------- */
.flow {
  position: relative;
}
.flow-item {
  display: flex;
  gap: 12px;
  padding-bottom: 13px;
}
.flow-item .rail {
  position: relative;
  width: 30px;
  flex: none;
  display: flex;
  justify-content: center;
}
.flow-item .rail::before {
  content: '';
  position: absolute;
  top: 30px;
  bottom: -13px;
  width: 1px;
  background: var(--border);
}
.flow-item:last-child .rail::before {
  display: none;
}
.flow-item .pip {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--panel);
  border: 1px solid var(--border);
  color: var(--text-2);
  z-index: 1;
}
.flow-item .pip.in { border-color: #cfe9ff; background: #eef7ff; color: #1d7fd6; }
.flow-item .pip.out { border-color: #ffd9c7; background: #fff3ec; color: #d2661d; }
.flow-item .pip.move { border-color: #ded0f5; background: #f1ebfb; color: #7a4bd0; }
.flow-item .pip.bad { border-color: #ffd0ce; background: #ffeceb; color: #d13b36; }

.flow-body {
  flex: 1;
  min-width: 0;
  padding: 11px 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.flow-body .line1 {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.flow-body .op {
  font-size: 13.5px;
  font-weight: 600;
}
.flow-body .when {
  margin-left: auto;
  color: var(--text-3);
  font-size: 12.5px;
  white-space: nowrap;
}
.flow-body .path {
  margin-top: 6px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}
.flow-body .path .arr {
  color: var(--text-3);
}
.flow-body .path .frm {
  color: var(--text-2);
}
.flow-body .path .frm.dim {
  color: var(--text-3);
}
.flow-body .path .to {
  font-weight: 600;
}
.flow-body .path .qty {
  color: var(--text-3);
  font-size: 12.5px;
}
.flow-body .note {
  margin-top: 7px;
  padding-top: 7px;
  border-top: 1px dashed var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.flow-body .note-text {
  flex: 1;
  min-width: 120px;
  color: var(--text-2);
  font-size: 12.5px;
}
.flow-body .no-rev {
  flex: 1;
  min-width: 200px;
  color: var(--text-3);
  font-size: 12.5px;
}
.flow-loading {
  padding: 26px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}

.pill {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  border: 1px solid;
  white-space: nowrap;
}
.pill.manual { background: #e8f8e5; color: #1f8a17; border-color: #c8ebc2; }
.pill.opening { background: #fff4e6; color: #b65c00; border-color: #ffdba8; }
.pill.import { background: #f2f3f5; color: var(--text-2); border-color: var(--border); }
.pill.fix { background: #fff4e6; color: #b65c00; border-color: #ffdba8; }

.lk {
  /* 图标 + 文字（冲正）：`AppIcon` 是 block svg，靠 flex 排一行 */
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: var(--el-color-primary);
  font-size: 12.5px;
  padding: 3px 7px;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.lk:hover {
  background: var(--el-color-primary-light-9);
}
/* 报损这类破坏性动作：给个克制的红 —— 卡片与列表两处共用同一份 */
.lk.danger {
  color: var(--el-color-danger);
}
.lk.danger:hover {
  background: var(--el-color-danger-light-9);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
