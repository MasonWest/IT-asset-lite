<script setup lang="ts">
/**
 * 主机 ←→ 显示器 配对管理。
 *
 * 解决的核心问题是「显示器比主机多 / 到底哪台挂在哪台上说不清」，
 * 所以这一页只干一件事：让每一台显示器的归属一眼可见、一步可改。
 *   · 左边是待配对池 —— 没主的显示器都在这
 *   · 右边是主机列表 —— 每台主机下面挂着它的显示器，点 × 就解绑
 *   · 从别的主机上把显示器拿走，走的是同一套选人弹窗（后端按换绑处理，一次事务做完）
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, pairingApi } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState, loadSystemInfo, saveOperator } from '@/stores/app'
import type { AssetBrief, PairingBoard, Relation } from '@/types'
import { formatDate, orDash, statusMeta, typeIcon } from '@/utils/format'

const router = useRouter()
/** 从这一页点进设备详情，返回时要回到配对页而不是台账 */
const { withBack } = useBackNav('/pairings')

const loading = ref(false)
const board = ref<PairingBoard | null>(null)
const keyword = ref('')
const operatorInput = ref('')
const acting = ref(false)

const pickerOpen = ref(false)
const pickerTarget = ref<AssetBrief | null>(null)
const pickedMonitorId = ref<number | undefined>(undefined)
const pickerNote = ref('')

const history = ref<Relation[]>([])
const historyLoading = ref(false)
const showHistory = ref(false)

/** 主机列：只看还没挂任何显示器的主机。默认全看 */
const onlyUnpairedHosts = ref(false)

/**
 * 使用人里代表「无归属」的占位符。
 *
 * 项目里没有人员字典表，使用人是自由文本，于是「这台没人用」被写成了各种样子 ——
 * 现在库里 15 台写的是斜杠 `/`（13 台主机，全在办公室大厅 / 资料室这类公共区域），
 * 而且这 13 台全都没有显示器，正好淹在「只看没配显示器的」那个筛选里，
 * 想配真正要配的那几台得先做一遍排除法。
 *
 * 半角、全角斜杠都认 —— 中文输入法下打出 `／` 太正常了。
 */
const NO_USER_MARKS = ['/', '／']

function isNoUser(asset: AssetBrief): boolean {
  const name = asset.user_name?.trim()
  return !!name && NO_USER_MARKS.includes(name)
}

/** 主机列：默认把「没使用人」的藏掉 */
const hideNoUserHosts = ref(true)

/** 被这条规则挡住的主机。数组留全，界面上要能把「藏了几台、怎么放出来」讲清楚 */
const noUserHosts = computed(() => (board.value?.hosts ?? []).filter((h) => isNoUser(h.host)))

/**
 * 主机侧的基础集合 = 全部主机 − 被藏掉的没使用人的。
 *
 * **只作用于主机侧，显示器不筛。** 显示器写 `/` 也得有人给它找主机去挂，
 * 藏起来只会漏配；主机写 `/` 则是「这台不归谁」，混在工作列表里才是纯干扰。
 */
const hostPool = computed(() =>
  (board.value?.hosts ?? []).filter((h) => !hideNoUserHosts.value || !isNoUser(h.host)),
)

/**
 * 没配显示器的主机数。按**过滤之后**算 —— 拿后端全局统计去当勾选框上的数字，
 * 会出现「写着 21 台，勾上只看得到 8 台」这种对不上的情况。
 */
const unpairedHostCount = computed(() => hostPool.value.filter((h) => !h.monitor_count).length)

/** 「加显示器」弹窗：默认只列还没配出去的，勾上才显示已挂机的 */
const pickerShowBound = ref(false)
const pickerKeyword = ref('')

const stats = computed(() => board.value?.stats ?? {})
const hints = computed(() => board.value?.hints ?? [])

/** 所有显示器（含已挂机的），用来在「给主机加显示器」时把已挂在别处的也列出来 */
const allMonitors = computed(() => {
  const rows: AssetBrief[] = []
  board.value?.hosts.forEach((h) => rows.push(...h.monitors))
  rows.push(...(board.value?.unpaired_monitors ?? []))
  return rows
})

/** 显示器 ID → 当前挂在哪台主机上（没挂就是 undefined） */
const hostOf = computed(() => {
  const map = new Map<number, AssetBrief>()
  board.value?.hosts.forEach((h) => h.monitors.forEach((m) => map.set(m.id, h.host)))
  return map
})

/** 已经挂在别处的显示器 —— 单独拎出来，弹窗里默认把它们藏掉 */
const boundMonitors = computed(() => allMonitors.value.filter((m) => hostOf.value.has(m.id)))

const freeMonitorCount = computed(() => allMonitors.value.length - boundMonitors.value.length)

/**
 * 「加显示器」弹窗里真正列出来的东西。
 *
 * 默认**只列没配出去的** —— 一台台主机配显示器时，满屏「当前挂在 XXX」的候选
 * 纯属干扰，眼睛得先做完排除法才能选中要的那台。要换绑再勾开关把它们放出来。
 */
const pickerOptions = computed(() => {
  const base = pickerShowBound.value ? allMonitors.value : allMonitors.value.filter((m) => !hostOf.value.has(m.id))
  const kw = pickerKeyword.value.trim().toLowerCase()
  if (!kw) return base
  return base.filter((m) =>
    [m.asset_code, m.brand, m.model, m.user_name, m.location]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(kw)),
  )
})

function matchHost(code: string, user: string | null, name: string): boolean {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return true
  return [code, user, name].filter(Boolean).some((v) => String(v).toLowerCase().includes(kw))
}

/**
 * 关键字命中的主机（不带上「只看未配对」那个开关）—— 待配对池的「挂到主机」下拉
 * 也用这一个集合，所以它跟左边主机列看到的范围永远一致。
 */
const matchedHosts = computed(() =>
  hostPool.value.filter((h) =>
    matchHost(h.host.asset_code, h.host.user_name, h.monitors.map((m) => m.asset_code).join(' ')),
  ),
)

const visibleHosts = computed(() =>
  matchedHosts.value.filter((h) => !onlyUnpairedHosts.value || !h.monitor_count),
)

const visibleUnpaired = computed(() =>
  (board.value?.unpaired_monitors ?? []).filter((m) =>
    matchHost(m.asset_code, m.user_name, `${m.brand ?? ''} ${m.model ?? ''}`),
  ),
)

async function load() {
  loading.value = true
  try {
    board.value = await pairingApi.board()
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await pairingApi.history({ page_size: 100 })
    history.value = res.items
  } catch {
    /* 拦截器已提示 */
  } finally {
    historyLoading.value = false
  }
}

function ensureOperator(): string | null {
  const op = appState.operator.trim()
  if (!op) {
    ElMessage.warning('请先在右上角「操作人」里填一下名字，配对记录要记谁操作的')
    return null
  }
  return op
}

function openBindPicker(host: AssetBrief) {
  pickerTarget.value = host
  pickedMonitorId.value = undefined
  pickerNote.value = ''
  // 每次打开都回到「只看未配对」的默认态：这是多数场景，别把上次的临时选择带过来
  pickerShowBound.value = false
  pickerKeyword.value = ''
  pickerOpen.value = true
}

async function submitBind() {
  if (!pickerTarget.value || !pickedMonitorId.value) {
    ElMessage.warning('先选一台显示器')
    return
  }
  const op = ensureOperator()
  if (!op) return

  const from = hostOf.value.get(pickedMonitorId.value)
  acting.value = true
  try {
    if (from) {
      // 已经挂在别的主机上 —— 后端会用「换绑」，一个事务里解绑 + 绑定
      await pairingApi.rebind({
        monitor_id: pickedMonitorId.value,
        host_id: pickerTarget.value.id,
        operator: op,
        note: pickerNote.value.trim() || `从 ${from.asset_code} 换绑过来`,
      })
      ElMessage.success('已换绑到本机')
    } else {
      await pairingApi.bind({
        monitor_id: pickedMonitorId.value,
        host_id: pickerTarget.value.id,
        operator: op,
        note: pickerNote.value.trim() || null,
      })
      ElMessage.success('已绑定')
    }
    pickerOpen.value = false
    await Promise.all([load(), showHistory.value ? loadHistory() : Promise.resolve()])
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

async function unbind(host: AssetBrief, monitor: AssetBrief) {
  const op = ensureOperator()
  if (!op) return
  try {
    await ElMessageBox.confirm(
      `把「${monitor.asset_code}」从「${host.asset_code}」上解绑？解绑记录会留在两台设备的变更历史里。`,
      '解绑确认',
      { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  acting.value = true
  try {
    const found = await pairingApi.history({ asset_id: monitor.id, active_only: true })
    const relation = found.items[0]
    if (!relation) {
      ElMessage.warning('这条配对关系已经不存在了，刷新一下看看')
      await load()
      return
    }
    await pairingApi.release(relation.id, { operator: op, note: '从配对管理页解绑' })
    ElMessage.success('已解绑')
    await Promise.all([load(), showHistory.value ? loadHistory() : Promise.resolve()])
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

function openAsset(id: number) {
  void router.push({ path: `/asset/${id}`, query: withBack() })
}

function saveOperatorName() {
  saveOperator(operatorInput.value)
  if (appState.operator) ElMessage.success(`操作人已设为「${appState.operator}」`)
}

async function toggleHistory() {
  showHistory.value = !showHistory.value
  if (showHistory.value && !history.value.length) await loadHistory()
}

onMounted(async () => {
  await loadSystemInfo()
  operatorInput.value = appState.operator
  await load()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>配对管理</h1>
        <p class="sub">
          主机和显示器的绑定关系。一台主机可以带多台显示器，一台显示器同一时刻只能挂在一台主机上。
        </p>
      </div>
      <div class="head-actions">
        <el-input
          v-model="operatorInput"
          placeholder="操作人姓名"
          size="default"
          style="width: 140px"
          @change="saveOperatorName"
        />
        <el-button @click="toggleHistory">{{ showHistory ? '收起记录' : '配对记录' }}</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <div class="stat-row">
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>🖥️</span>主机</div>
        <div class="v">{{ stats.hosts ?? 0 }}<small>台</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>🖥</span>显示器</div>
        <div class="v">{{ stats.monitors ?? 0 }}<small>台</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>🔗</span>已配对</div>
        <div class="v">{{ stats.paired_monitors ?? 0 }}<small>台</small></div>
      </div>
      <div class="stat-card" style="cursor: default">
        <div class="k"><span>📦</span>待配对</div>
        <div class="v">{{ stats.unpaired_monitors ?? 0 }}<small>台</small></div>
      </div>
      <div
        class="stat-card"
        style="cursor: default"
        title="全库统计，不受上方「只看没配显示器的」和过滤开关影响"
      >
        <div class="k"><span>⚠️</span>没配显示器的主机</div>
        <div class="v">{{ stats.hosts_without_monitor ?? 0 }}<small>台</small></div>
      </div>
    </div>

    <el-alert
      v-for="(hint, i) in hints"
      :key="i"
      type="info"
      :closable="false"
      show-icon
      :title="hint"
      style="margin-bottom: 10px"
    />

    <div class="filter-bar">
      <el-input
        v-model="keyword"
        class="grow"
        placeholder="搜索资产编号 / 使用人 / 品牌型号"
        clearable
      />
      <el-checkbox v-model="hideNoUserHosts" class="no-user-toggle">
        隐藏使用人「/」的主机（{{ noUserHosts.length }}）
      </el-checkbox>
      <span class="count-note">
        {{ visibleHosts.length }} 台主机 · {{ visibleUnpaired.length }} 台待配对显示器
      </span>
    </div>

    <div v-if="showHistory" class="panel history-panel">
      <div class="section-title">配对记录（含已解绑）</div>
      <div v-loading="historyLoading" class="history-list">
        <div v-for="r in history" :key="r.id" class="history-row">
          <span class="badge" :class="r.active ? 'on' : 'off'">{{ r.active ? '生效中' : '已解绑' }}</span>
          <span class="pair">
            <a @click="openAsset(r.host.id)">{{ r.host.asset_code }}</a>
            <span class="arrow">↔</span>
            <a @click="openAsset(r.monitor.id)">{{ r.monitor.asset_code }}</a>
          </span>
          <span class="note">{{ r.active ? (r.note || '—') : (r.release_note || '—') }}</span>
          <span class="who">
            {{ r.active ? (r.created_by || '—') : (r.released_by || '—') }}
          </span>
          <span class="time">
            {{ formatDate(r.created_at.slice(0, 10)) }}
            <template v-if="!r.active"> → {{ formatDate(r.released_at?.slice(0, 10)) }}</template>
          </span>
        </div>
        <div v-if="!historyLoading && !history.length" class="empty-note">还没有任何配对记录</div>
      </div>
    </div>

    <div v-loading="loading" class="pair-layout">
      <!-- 主机侧 -->
      <div class="col">
        <div class="col-head">
          <span>主机（{{ visibleHosts.length }}）</span>
          <el-checkbox v-model="onlyUnpairedHosts" size="small">
            只看没配显示器的（{{ unpairedHostCount }}）
          </el-checkbox>
        </div>
        <div v-for="h in visibleHosts" :key="h.host.id" class="host-card panel">
          <div class="host-row">
            <span class="type-badge">{{ typeIcon(h.host.device_type_name) }}</span>
            <div class="info" @click="openAsset(h.host.id)">
              <div class="c1">
                {{ h.host.asset_code }}
                <span class="type">{{ h.host.device_type_name }}</span>
              </div>
              <div class="c2">
                {{ orDash(h.host.brand) }} {{ h.host.model || '' }}
                <template v-if="h.host.user_name"> · 使用人 {{ h.host.user_name }}</template>
              </div>
              <div class="c2">{{ orDash(h.host.location) }}</div>
            </div>
            <div class="host-actions">
              <span class="monitor-count" :class="{ zero: !h.monitor_count }">
                {{ h.monitor_count }} 台显示器
              </span>
              <el-button size="small" :disabled="acting" @click="openBindPicker(h.host)">
                + 加显示器
              </el-button>
            </div>
          </div>

          <div v-if="h.monitors.length" class="monitor-chips">
            <span
              v-for="m in h.monitors"
              :key="m.id"
              class="chip"
              :style="{ borderColor: statusMeta(m.status).border }"
            >
              <span class="ic">{{ typeIcon(m.device_type_name) }}</span>
              <a @click="openAsset(m.id)">{{ m.asset_code }}</a>
              <span class="model">{{ orDash(m.brand) }} {{ m.model || '' }}</span>
              <button class="x" title="解绑" :disabled="acting" @click="unbind(h.host, m)">×</button>
            </span>
          </div>
          <div v-else class="no-monitor">这台主机还没配显示器</div>
        </div>

        <div v-if="!loading && !visibleHosts.length" class="empty-state">
          <div class="big">🖥️</div>
          <template v-if="onlyUnpairedHosts && matchedHosts.length">
            <div class="title">所有主机都配上显示器了</div>
            <div>取消勾选「只看没配显示器的」，就能看到全部 {{ matchedHosts.length }} 台主机。</div>
          </template>
          <template v-else-if="hideNoUserHosts && noUserHosts.length && !keyword.trim()">
            <div class="title">能显示的主机都被过滤掉了</div>
            <div>
              取消勾选上方那个过滤开关，{{ noUserHosts.length }} 台公共区域的主机就会回来。
            </div>
          </template>
          <template v-else>
            <div class="title">没有匹配的主机</div>
            <div>换个关键字，或者到「资产台账 → 设备类型」里把主机类型的类别设成主机类。</div>
          </template>
        </div>
      </div>

      <!-- 待配对池 -->
      <div class="col">
        <div class="col-head">
          <span>待配对显示器（{{ visibleUnpaired.length }}）</span>
        </div>
        <div
          v-for="m in visibleUnpaired"
          :key="m.id"
          class="monitor-card panel"
        >
          <span class="type-badge">{{ typeIcon(m.device_type_name) }}</span>
          <div class="info" @click="openAsset(m.id)">
            <div class="c1">{{ m.asset_code }}</div>
            <div class="c2">{{ orDash(m.brand) }} {{ m.model || '' }}</div>
            <div class="c2">
              {{ m.user_name ? `使用人 ${m.user_name} · ` : '' }}{{ orDash(m.location) }}
            </div>
          </div>
          <el-dropdown
            trigger="click"
            :disabled="acting || !matchedHosts.length"
            @command="(host: AssetBrief) => { openBindPicker(host); pickedMonitorId = m.id }"
          >
            <el-button size="small">挂到主机 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  v-for="h in matchedHosts"
                  :key="h.host.id"
                  :command="h.host"
                >
                  {{ h.host.asset_code }}
                  <span style="color: var(--text-3)">
                    {{ h.host.user_name ? `（${h.host.user_name}）` : '' }}
                  </span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div v-if="!loading && !visibleUnpaired.length" class="empty-state">
          <div class="big">📦</div>
          <div class="title">没有待配对的显示器</div>
          <div>所有显示器都有归属了，或者还没有录入显示类设备。</div>
        </div>
      </div>
    </div>

    <!-- 选择显示器 -->
    <el-dialog
      v-model="pickerOpen"
      :title="pickerTarget ? `给 ${pickerTarget.asset_code} 加显示器` : '加显示器'"
      width="min(560px, 92vw)"
    >
      <div v-if="!allMonitors.length" class="empty-state">
        <div class="big">🖥</div>
        <div class="title">还没有任何显示类设备</div>
        <div>先到「资产台账」录入显示器，并确认它的设备类型类别是显示类。</div>
      </div>

      <template v-else>
        <p class="picker-tip">
          默认只列<strong>还没配出去</strong>的显示器，省得满屏都是「当前挂在 XXX」干扰视线。
          要把某台已经挂在别的主机上的换过来，勾上下面「显示已挂机的」——
          选中它按换绑处理，系统一次做完「解绑旧的 + 绑到本机」，两台设备的变更历史里都会留下记录。
        </p>

        <div class="picker-filters">
          <el-input
            v-model="pickerKeyword"
            size="small"
            placeholder="搜索编号 / 品牌型号 / 使用人"
            clearable
          />
          <el-checkbox v-model="pickerShowBound" size="small">
            显示已挂机的（{{ boundMonitors.length }} 台）
          </el-checkbox>
        </div>

        <div class="picker-count">
          列出 {{ pickerOptions.length }} 台<template v-if="pickerShowBound">（未配对 {{ freeMonitorCount }} + 已挂机 {{ boundMonitors.length }}）</template>
        </div>

        <el-radio-group v-model="pickedMonitorId" class="picker-grid">
          <el-radio
            v-for="m in pickerOptions"
            :key="m.id"
            :value="m.id"
            border
            class="picker-radio"
            :disabled="hostOf.get(m.id)?.id === pickerTarget?.id"
          >
            <span class="picker-head-row">
              <span class="picker-code">{{ m.asset_code }}</span>
              <!-- 使用人做成独立标签：同一个人的几台设备一眼归堆，不用去记编号 -->
              <span class="picker-user" :class="{ none: !m.user_name }">
                {{ m.user_name || '未填使用人' }}
              </span>
            </span>
            <span class="picker-sub">
              {{ orDash(m.brand) }} {{ m.model || '' }}
              <template v-if="m.location"> · {{ m.location }}</template>
              <template v-if="hostOf.get(m.id)">
                ·
                <span class="warn-text">当前挂在 {{ hostOf.get(m.id)?.asset_code }}（换绑）</span>
              </template>
              <template v-else>· 未配对</template>
            </span>
          </el-radio>
        </el-radio-group>

        <div v-if="!pickerOptions.length" class="empty-note">
          <template v-if="!pickerShowBound && boundMonitors.length && !pickerKeyword.trim()">
            所有显示器都已经配出去了。要换绑就把上面「显示已挂机的」勾上。
          </template>
          <template v-else>没有匹配的显示器，换个关键字或者勾上「显示已挂机的」试试。</template>
        </div>

        <el-input
          v-model="pickerNote"
          placeholder="备注（可选），例如 新同事入职加一台"
          clearable
          style="margin-top: 14px"
        />
      </template>

      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <el-button @click="pickerOpen = false">取消</el-button>
          <el-button
            type="primary"
            :loading="acting"
            :disabled="!pickerOptions.length"
            @click="submitBind"
          >
            确定
          </el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.count-note {
  font-size: 13px;
  color: var(--text-2);
  white-space: nowrap;
}

.no-user-toggle {
  flex-shrink: 0;
}

.no-user-toggle :deep(.el-checkbox__label) {
  font-size: 12.5px;
  white-space: nowrap;
}

.pair-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  margin-top: 4px;
}

.col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 28px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-2);
  padding: 0 2px;
}

.col-head :deep(.el-checkbox__label) {
  font-size: 12.5px;
  font-weight: 400;
  white-space: nowrap;
}

.host-card {
  padding: 14px 16px;
}

.host-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.host-row .info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.host-row .info:hover .c1 {
  color: var(--primary);
}

.host-row .info .c1 {
  font-size: 14.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.host-row .info .c1 .type {
  font-size: 11.5px;
  font-weight: 400;
  color: var(--text-2);
  background: #f2f3f5;
  border-radius: 999px;
  padding: 1px 8px;
}

.host-row .info .c2 {
  font-size: 12.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.host-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.monitor-count {
  font-size: 12.5px;
  color: #0f7b3f;
  background: #e8f7ee;
  border-radius: 999px;
  padding: 2px 10px;
  white-space: nowrap;
}

.monitor-count.zero {
  color: #a15c00;
  background: #fff4e6;
}

.monitor-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fafbfc;
  font-size: 12.5px;
  max-width: 100%;
}

.chip .ic {
  font-size: 13px;
  line-height: 1;
}

.chip a {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.chip .model {
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 130px;
}

.chip .x {
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 50%;
  background: #e9ebef;
  color: var(--text-2);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  font-family: inherit;
  flex-shrink: 0;
}

.chip .x:hover {
  background: #fdecea;
  color: #d54941;
}

.no-monitor {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  font-size: 12.5px;
  color: var(--text-3);
}

.monitor-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
}

.monitor-card .info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.monitor-card .info:hover .c1 {
  color: var(--primary);
}

.monitor-card .info .c1 {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.monitor-card .info .c2 {
  font-size: 12.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-panel {
  margin-bottom: 16px;
}

.history-list {
  padding: 0 20px 16px;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--border);
  font-size: 12.5px;
}

.history-row:last-child {
  border-bottom: 0;
}

.history-row .badge {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11.5px;
}

.history-row .badge.on {
  background: #e8f7ee;
  color: #0f7b3f;
}

.history-row .badge.off {
  background: #f2f3f5;
  color: #7a7f87;
}

.history-row .pair {
  display: flex;
  align-items: center;
  gap: 6px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.history-row .pair a {
  cursor: pointer;
}

.history-row .arrow {
  color: var(--text-3);
}

.history-row .note {
  flex: 1;
  min-width: 0;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-row .who {
  width: 64px;
  flex-shrink: 0;
  color: var(--text-3);
}

.history-row .time {
  width: 160px;
  flex-shrink: 0;
  text-align: right;
  color: var(--text-3);
  white-space: nowrap;
}

.empty-note {
  padding: 20px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}

.picker-tip {
  margin: 0 0 12px;
  font-size: 12.5px;
  color: var(--text-2);
  line-height: 1.7;
  padding: 9px 11px;
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.picker-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.picker-filters .el-input {
  flex: 1;
  min-width: 0;
}

.picker-filters :deep(.el-checkbox__label) {
  font-size: 12.5px;
  white-space: nowrap;
}

.picker-count {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 8px;
}

.picker-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  max-height: 48vh;
  overflow-y: auto;
}

.picker-radio {
  width: 100%;
  height: auto;
  margin: 0 !important;
  padding: 10px 12px;
  align-items: flex-start;
}

.picker-radio :deep(.el-radio__label) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.picker-code {
  font-size: 13.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}

/* 编号 + 使用人排一行。使用人做成标签是为了扫视 —— 同一使用人的几台设备自然会归堆 */
.picker-head-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.picker-user {
  font-size: 11.5px;
  line-height: 1.6;
  color: #3370ff;
  background: #eef3ff;
  border-radius: 999px;
  padding: 0 8px;
  white-space: nowrap;
}

.picker-user.none {
  color: var(--text-3);
  background: #f2f3f5;
}

.picker-sub {
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.6;
  white-space: normal;
}

.warn-text {
  color: #a15c00;
}

@media (max-width: 900px) {
  .pair-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .head-actions {
    flex-wrap: wrap;
  }

  .host-actions {
    flex-direction: column;
    align-items: flex-end;
  }

  .history-row {
    flex-wrap: wrap;
  }

  .history-row .time {
    width: auto;
  }
}
</style>
