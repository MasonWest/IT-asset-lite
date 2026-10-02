<script setup lang="ts">
/**
 * 工位详情侧栏。
 *
 * 从地图视图里拆出来，因为它是**唯一会写数据的地方** ——
 * 改使用人、改编码、挂/摘资产、单台盘点都在这里。
 * 把这些写操作集中在一个组件里，地图那边就只剩渲染和拖拽了。
 *
 * ## 三处必须说清的口径
 *
 * ### 1. 侧栏的资产清单是**真实资产**，不是工位自己存的列表
 * 数据来自 `GET /api/workstations/{id}/assets`，查的是 `assets.workstation_id`。
 * 工位表里**不存资产列表** —— 那会变成同一件事存两处，迟早对不上。
 *
 * ### 2. 「使用人」是工位归属，不是资产使用人的副本
 * 「W23 这个位置归王嘉怡」和「这台主机现在归王嘉怡」是两个不同的事实，
 * 现实中**可以不一致**，而不一致本身正是现场盘点要找的信号。
 * 所以这里**不做**"改工位使用人就顺手改资产使用人" —— 那会把差异抹掉。
 *
 * ### 3. 空位 / 不在本次范围不是「已盘」
 * 它们显示为灰色「空位」「不在本次范围」，与绿色「已盘」明确区分。
 * 详见 `utils/workstationMap.ts` 的 STATE_META。
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, workstationApi } from '@/api'
import { useFreeTextOptions } from '@/composables/useFreeTextOptions'
import { appState } from '@/stores/app'
import type { Asset, MapInventoryWorkstation, Workstation } from '@/types'
import { hasOwner, orDash, statusMeta, typeIcon, userLabel } from '@/utils/format'
import { brandModel, facingLabel, isNeutral, positionText, stateMeta } from '@/utils/workstationMap'

const props = defineProps<{
  station: Workstation | null
  /** 该工位在「当前选中的盘点任务」里的状态（没开盘点模式时为 null） */
  mapRow: MapInventoryWorkstation | null
  assets: Asset[]
  users: string[]
  auditMode: boolean
  assetsLoading: boolean
  markingIds: number[]
  /** 当前选中的盘点任务 id。用于「去资产详情」时把回程地址带全 */
  taskId: number | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', station: Workstation): void
  (e: 'removed', station: Workstation): void
  (e: 'mark', assetId: number, result: 'checked' | 'abnormal' | 'pending'): void
  (e: 'mark-station', workstationId: number, result: 'checked' | 'abnormal'): void
  (e: 'assets-changed', stationId: number): void
  (e: 'add-asset', stationId: number): void
}>()

const router = useRouter()

const busy = computed(() => Boolean(props.station))
const saving = ref(false)

/* ---- 表单：改使用人 / 编码 / 房间 ---- */
const codeInput = ref('')
const roomInput = ref('')

/**
 * 工位使用人下拉。
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
    roomInput.value = s?.room ?? ''
    resetUserQuery()
  },
  { immediate: true },
)

/** 盘点模式下的状态（没开盘点时为 null） */
const state = computed(() => props.mapRow?.state ?? null)
const neutral = computed(() => (state.value ? isNeutral(state.value) : false))

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

/* ---- 写操作 ---- */

async function saveBasics() {
  const s = props.station
  if (!s) return
  const payload: Record<string, string | null> = {}
  const code = codeInput.value.trim()
  const room = roomInput.value.trim()
  if (code && code !== s.code) payload.code = code
  if (room !== (s.room ?? '')) payload.room = room || null
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
 * 改工位使用人。
 *
 * **只改工位，不动资产。** 工位归属与设备归属是两个事实，
 * 强行联动会把"这位置上写的是 A、但机器记在 B 名下"这种差异抹掉 ——
 * 而那正是盘点要找的东西。
 */
async function changeUser(value: string | null) {
  const s = props.station
  if (!s) return
  const next = (value ?? '').trim() || null
  if (next === (s.user_name ?? null)) return
  saving.value = true
  try {
    const updated = await workstationApi.update(s.id, {
      user_name: next,
      operator: appState.operator || null,
    })
    emit('saved', updated)
    ElMessage.success(next ? `工位使用人已改为 ${next}` : '已清空工位使用人')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

/** 从工位上摘下一台设备（**资产留着**，只是不再属于这个工位） */
async function unbind(asset: Asset) {
  const s = props.station
  if (!s) return
  try {
    await ElMessageBox.confirm(
      `把 ${asset.asset_code} 从工位 ${s.code} 摘下来？设备仍保留在台账里。`,
      '摘除设备',
      { confirmButtonText: '摘下来', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await workstationApi.unbindAsset(s.id, asset.id, appState.operator || null)
    emit('assets-changed', s.id)
    ElMessage.success(`已把 ${asset.asset_code} 从工位摘下来`)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

/** 把这个工位上的设备全部标为「已盘」 */
function markAllPassed() {
  const s = props.station
  if (!s) return
  emit('mark-station', s.id, 'checked')
}

/**
 * 跳去资产台账。**带上当前工位的使用人作为筛选** —— 这样从刘盼盼的工位点进去，
 * 看到的就是"刘盼盼的设备"，少一步手动筛选。
 *
 * 不带筛选的两种情况，都是有意的：
 *   - 工位**没有使用人**（空闲工位）：没有可筛的名字，只能给全量台账
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
        <div class="t">{{ auditMode ? '现场工位盘点' : '工位详情' }}</div>
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
      <div class="h">选择一个工位</div>
      <div class="p">
        点击地图上的工位，查看这个位置上的<b>真实设备</b>，<br />
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
            {{ station!.room ? station!.room + ' · ' : '' }}坐标
            {{ positionText(station!.x, station!.y) }}
          </div>
        </div>
      </div>

      <!-- 三个指标。盘点状态用的是**当前选中任务**的结果 -->
      <div class="metrics">
        <div class="metric">
          <div class="v">{{ station!.user_name ? userLabel(station!.user_name) : '—' }}</div>
          <div class="k">工位使用人</div>
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
          <span>⚡ 工位快捷盘点</span>
          <span class="st">{{ state ? stateMeta(state).label : '' }}</span>
        </div>
        <button class="pass-all" :disabled="!mapRow?.assets.length" @click="markAllPassed">
          🟢 把这个工位的设备全部标为已盘
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
            <div class="asset-icon">🖥️</div>
            <div class="asset-info">
              <div class="asset-title">{{ row.asset_code }}</div>
              <div class="asset-sub">
                {{ dash(row.device_type_name) }} · {{ assetSpec({ brand: row.brand, model: row.model } as Asset) }}
              </div>
              <div class="asset-sub">
                账面：{{ row.result_label }}
                <template v-if="row.operator"> · {{ row.operator }}</template>
              </div>
              <!--
                盘点之后账面又被改过 —— 这是盘点最有价值的信号，后端已经算好了。
                「账上写着是谁用的」和「现在是谁用的」不一致，正是要找的东西。
              -->
              <div v-if="row.changed_since" class="changed">
                ⚠️ 盘点后账面已变更（原为 {{ dash(row.snapshot_user) }}）
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
              🟢 正常在位
            </button>
            <button
              class="abtn danger"
              :class="{ active: row.result === 'abnormal' }"
              :disabled="isMarking(row.asset_id)"
              @click="emit('mark', row.asset_id, 'abnormal')"
            >
              🔴 标记缺失
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
          「挂设备到工位」走的是「新增一台设备并直接指定工位」这条路。
          反过来做（给工位挑一台已有设备）就得在弹窗里塞一个带筛选分页的设备选择器，
          等于把台账再写一遍 —— 所以宁可多一个按钮。
          已经有设备的人用不着它，去台账编辑那台设备即可。
        -->
        <button class="add-asset-btn" @click="emit('add-asset', station!.id)">
          ＋ 给这个工位挂一台新设备
        </button>

        <div v-if="assets.length" class="asset-plain-list">
          <div v-for="asset in assets" :key="asset.id" class="plain-row">
            <span class="ic">{{ typeIcon(asset.device_type_name) }}</span>
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
            </div>
            <button class="mini" title="从工位摘下来（设备保留）" @click="unbind(asset)">摘下</button>
          </div>
        </div>

        <div v-else-if="!assetsLoading" class="none">
          该工位暂无绑定资产。<br />
          到<a @click="openLedger">资产台账</a>里编辑设备，把「所在工位」选上它就会出现在这里。
        </div>
      </div>

      <!-- 编辑区 -->
      <div class="sect">
        <div class="sh"><span>工位设置</span></div>

        <div class="field">
          <label>工位使用人</label>
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
          <label>工位编码</label>
          <el-input v-model="codeInput" maxlength="32" placeholder="例如 W23" />
        </div>

        <div class="field">
          <label>房间备注</label>
          <el-input v-model="roomInput" maxlength="64" placeholder="例如 前台 / 总经理办公室" clearable />
        </div>

        <div class="hint-row">
          朝向：{{ facingLabel(station!.facing) }} · 位置调整请用顶部「调整位置」拖动后保存布局
        </div>

        <div class="acts">
          <el-button type="primary" :loading="saving" @click="saveBasics">保存编码 / 房间</el-button>
          <el-button type="danger" plain @click="emit('removed', station!)">删除工位</el-button>
        </div>
      </div>
    </div>
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
