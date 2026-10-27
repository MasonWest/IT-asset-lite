<script setup lang="ts">
/**
 * 点位迁移对话框 —— 资产换点位 / 整个点位业务迁移。
 *
 * ## 为什么必须有"预览 → 确认 → 执行"这三步
 *
 * 迁移是这个系统里**影响面最大的一次性操作**（可能一次动十几台设备 + 两个点位）。
 * 所以流程被刻意拆开：
 *   1. 选对象 → 点「预览」：**只读**，后端一个字段都不写；
 *   2. 看清单（搬哪几台、点位归属怎么变、有哪些差异要认）；
 *   3. 点「执行」：带上预览令牌。期间数据被人动过 → 后端 409，逼你重新看一遍。
 *
 * 令牌那一步是为两件事准备的：**并发改动**（别人抢先挪了设备）和**重复提交**
 * （手抖点了两次）。禁用按钮挡不住这两个，令牌能。
 *
 * ## 与坐标移动的边界
 *
 * 这里**从头到尾不碰 x / y**。mover 桌子是「编辑点位」那个模式的事，
 * 两者混在一起正是这个项目踩过的事故（CS 决策 67）。所以对话框里明写着这句。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { apiMessage, relocationApi } from '@/api'
import { appState } from '@/stores/app'
import type {
  Asset,
  AssetMove,
  RelocationApplyResult,
  RelocationPreview,
  RelocationMode,
  Workstation,
} from '@/types'
import { hasOwner, userLabel } from '@/utils/format'
import { compareStationCode } from '@/utils/workstationMap'

const props = defineProps<{
  modelValue: boolean
  mode: RelocationMode
  /** 迁移的源点位（进入本对话框的上下文） */
  source: Workstation | null
  /** 源点位上的资产（mode=assets 时用来勾选） */
  sourceAssets: Asset[]
  /** 从某台设备进来的，预勾选它 */
  presetAssetIds: number[]
  /** 全部点位，作为目标候选 */
  stations: Workstation[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'applied', result: RelocationApplyResult): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const mode = ref<RelocationMode>(props.mode)
const selectedAssetIds = ref<number[]>([])
const targetId = ref<number | null>(null)

// 点位业务迁移的三个开关
const moveAssets = ref(true)
const moveUser = ref(false)
const retireSource = ref(false)

const preview = ref<RelocationPreview | null>(null)
const previewing = ref(false)
const applying = ref(false)
const acknowledged = ref(false)
const expanded = ref<string[]>([])

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    mode.value = props.mode
    selectedAssetIds.value = [...props.presetAssetIds]
    targetId.value = null
    preview.value = null
    acknowledged.value = false
    moveAssets.value = true
    moveUser.value = false
    retireSource.value = false
  },
)

// 任何输入变化都让旧预览作废 —— 免得用户改了对象却照着旧清单点执行
watch([mode, selectedAssetIds, targetId, moveAssets, moveUser, retireSource], () => {
  preview.value = null
  acknowledged.value = false
}, { deep: true })

/**
 * 目标点位候选 —— **必须按编码排序，不能沿用接口给的顺序**。
 *
 * `GET /api/workstations` 的顺序是 `y, x, id`，那是**地图渲染**的顺序
 * （同一排的挨着给），本身没问题；但用在一个"从 68 个点位里挑一个"的下拉里，
 * 用户看到的就是 W48、W49、W5、W50… 这种顺序 —— 选起来极其费劲。
 *
 * 排序实现复用 `utils/workstationMap.ts::compareStationCode`
 * （`AssetFormDialog` 的点位下拉已经在用它）。**不要在这里再写一份**
 * —— `W10` 排在 `W2` 前面正是那个函数存在的原因。
 */
const targetCandidates = computed(() =>
  props.stations
    .filter((s) => s.active && s.id !== props.source?.id)
    .sort((a, b) => compareStationCode(a.code, b.code)),
)

const canPreview = computed(() => {
  if (!props.source || targetId.value === null) return false
  if (mode.value === 'assets') return selectedAssetIds.value.length > 0
  return moveAssets.value || moveUser.value
})

const targetStation = computed(() =>
  targetCandidates.value.find((s) => s.id === targetId.value) ?? null,
)

/** 目标点位使用人与将被搬过去的设备使用人是否会打架 —— 预览里后端也会说，这里先给个提醒 */
const targetOwnerLabel = computed(() => userLabel(targetStation.value?.user_name ?? null))

/** 归属原值 → 给用户看的文案（空值、`/`、`／` 一律显示成「未填使用人」） */
function ownerLabel(v: string | null | undefined): string {
  return userLabel(v ?? null)
}

/**
 * 这一侧的归属迁移前后变了没有。
 *
 * ⚠️ 比的是**显示文案**而不是原值：`null` 与 `/` 是两个不同的值、却是同一句话
 * （都显示「未填使用人」），按原值比会高亮出一个用户根本看不见的差别。
 */
function ownerChanged(before: string | null, after: string | null): boolean {
  return ownerLabel(before) !== ownerLabel(after)
}

/** 归属块的两种"侧"：源点位、目标点位。顺序固定，界面才不会跳。 */
const ownerSides = computed(() => {
  const p = preview.value
  if (!p) return []
  return [
    { key: 'source', code: p.source?.code ?? '源点位', before: p.source_user_before, after: p.source_user_after },
    { key: 'target', code: p.target?.code ?? '目标点位', before: p.target_user_before, after: p.target_user_after },
  ]
})

async function runPreview() {
  if (!props.source || targetId.value === null) return
  previewing.value = true
  try {
    preview.value = await relocationApi.preview({
      mode: mode.value,
      asset_ids: mode.value === 'assets' ? selectedAssetIds.value : [],
      source_workstation_id: mode.value === 'station' ? props.source.id : null,
      target_workstation_id: targetId.value,
      move_assets: moveAssets.value,
      move_user: moveUser.value,
      retire_source: retireSource.value,
    })
    acknowledged.value = false
    expanded.value = []
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    previewing.value = false
  }
}

async function runApply() {
  const p = preview.value
  if (!p || !p.ok) return
  if (p.confirmations.length && !acknowledged.value) {
    ElMessage.warning('先勾选「我已确认以上差异」再执行')
    return
  }
  applying.value = true
  try {
    const result = await relocationApi.apply({
      mode: p.mode,
      asset_ids: p.mode === 'assets' ? selectedAssetIds.value : [],
      source_workstation_id: p.mode === 'station' ? props.source!.id : null,
      target_workstation_id: targetId.value,
      move_assets: moveAssets.value,
      move_user: moveUser.value,
      retire_source: retireSource.value,
      preview_token: p.preview_token,
      acknowledge_warnings: acknowledged.value,
      operator: appState.operator || null,
    })
    ElMessage.success(result.summary || `已迁移 ${result.moved} 台设备`)
    emit('applied', result)
    visible.value = false
  } catch (error) {
    // 409 的两种情形（令牌过期 / 有未确认差异）后端给了人话，原样弹出来
    ElMessage.error(apiMessage(error))
  } finally {
    applying.value = false
  }
}

function moveLabel(move: AssetMove): string {
  const from = move.from_workstation_code || '未挂点位'
  const to = move.to_workstation_code || '未挂点位'
  return `${from} → ${to}`
}

const RELATION_LABEL: Record<string, string> = {
  consistent: '一致',
  mismatch: '待核对',
  unregistered: '未登记',
  undetermined: '无法判断',
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="source ? `迁移设备 / 点位 · ${source.code}` : '迁移'"
    width="640px"
    top="6vh"
    append-to-body
    class="reloc-dialog"
  >
    <div class="note">
      这里只改「设备摆在哪个点位」和「点位归谁」—— <b>不动地图坐标</b>。
      想把桌子挪个位置，请用地图上的「编辑点位」。
    </div>

    <!-- ① 选对象 -->
    <div class="block">
      <div class="blk-title">迁移类型</div>
      <el-radio-group v-model="mode" size="default">
        <el-radio-button value="assets">资产换点位</el-radio-button>
        <el-radio-button value="station">点位业务迁移</el-radio-button>
      </el-radio-group>
      <div class="blk-hint">
        <template v-if="mode === 'assets'">
          把选中的设备搬到另一个点位。<b>不改设备使用人</b>。
        </template>
        <template v-else>
          把一个点位的归属（使用人）和 / 或它上面的设备整体搬到另一个点位。
        </template>
      </div>
    </div>

    <!-- mode = assets：挑设备 -->
    <div v-if="mode === 'assets'" class="block">
      <div class="blk-title">要迁移的设备（{{ selectedAssetIds.length }} 台）</div>
      <div v-if="sourceAssets.length" class="asset-picker">
        <el-checkbox-group v-model="selectedAssetIds">
          <label v-for="a in sourceAssets" :key="a.id" class="pick-row">
            <el-checkbox :value="a.id">
              <span class="pick-code">{{ a.asset_code }}</span>
              <span class="pick-sub">
                {{ a.device_type_name || '—' }} · 使用人
                {{ hasOwner(a.user_name) ? a.user_name : '未填' }}
              </span>
            </el-checkbox>
          </label>
        </el-checkbox-group>
      </div>
      <div v-else class="blk-hint">这个点位上没有设备。</div>
    </div>

    <!-- mode = station：三个开关 -->
    <div v-else class="block">
      <div class="blk-title">迁移内容</div>
      <el-checkbox v-model="moveAssets">
        一并迁移设备
        <span class="sub">（只搬设备；源点位 {{ source?.code }} 上现有 {{ sourceAssets.length }} 台）</span>
      </el-checkbox>
      <el-checkbox v-model="moveUser">
        迁移点位使用人
        <span class="sub">
          （源点位 {{ source?.code }} 现为 {{ ownerLabel(source?.user_name) }} → 目标点位；
          <b>源点位使用人会被清空</b>。不勾就两侧都不动）
        </span>
      </el-checkbox>
      <el-checkbox v-model="retireSource">
        迁移后退役源点位
        <span class="sub">（软删除，编码不释放 —— 审计里两条记录仍指同一位置）</span>
      </el-checkbox>
    </div>

    <!-- 目标点位 -->
    <div class="block">
      <div class="blk-title">目标点位</div>
      <el-select v-model="targetId" placeholder="选择一个点位" filterable style="width: 100%">
        <el-option
          v-for="s in targetCandidates"
          :key="s.id"
          :label="`${s.code} · ${userLabel(s.user_name)}`"
          :value="s.id"
        />
      </el-select>
      <div v-if="targetStation" class="blk-hint">
        目标点位使用人：<b>{{ targetOwnerLabel }}</b>
      </div>
    </div>

    <div class="acts">
      <el-button :disabled="!canPreview" :loading="previewing" @click="runPreview">
        {{ preview ? '重新预览' : '预览影响范围' }}
      </el-button>
    </div>

    <!-- ② 预览结果 -->
    <div v-if="preview" class="preview">
      <div v-if="preview.conflicts.length" class="pane conflict">
        <div class="pane-title">无法执行</div>
        <ul>
          <li v-for="(c, i) in preview.conflicts" :key="i">{{ c }}</li>
        </ul>
      </div>

      <template v-if="preview.ok">
        <!--
          点位归属：**两侧都把"迁移前 → 迁移后"摆出来，哪怕这一侧根本不变。**
          这一块存在的理由就是"不变的也要说" —— 曾经它只说会变的，
          于是用户点了「点位业务迁移 + 一并迁移设备」，执行完发现使用人没动，
          只能靠事后翻审计日志才知道"那项没勾，所以没搬"。
        -->
        <div v-if="preview.mode === 'station'" class="pane owner">
          <div class="pane-title">
            点位归属
            <span v-if="preview.user_move">（跟着走）</span>
            <span v-else>（不跟着走）</span>
          </div>
          <div v-for="side in ownerSides" :key="side.key" class="owner-row">
            <span class="ocode">{{ side.code }}</span>
            <span class="obefore">{{ ownerLabel(side.before) }}</span>
            <span class="oarrow">→</span>
            <span class="oafter" :class="{ changed: ownerChanged(side.before, side.after) }">
              {{ ownerLabel(side.after) }}
            </span>
            <span v-if="!ownerChanged(side.before, side.after)" class="okeep">不变</span>
          </div>
          <div v-if="!preview.user_move" class="owner-hint">
            <template v-if="hasOwner(preview.source_user_before)">
              要把归属一起搬过去，请勾上「迁移点位使用人」。
            </template>
            <template v-else>
              源点位本来就没有登记使用人，没有归属可搬；目标点位现有的人不会被覆盖。
            </template>
          </div>
        </div>

        <div class="pane">
          <div class="pane-title">
            将迁移 {{ preview.moves.length }} 台设备
            <span v-if="preview.noop_count">（{{ preview.noop_count }} 台已在目标点位，跳过）</span>
          </div>
          <div class="moves">
            <div v-for="m in preview.moves" :key="m.asset_id" class="move-row">
              <span class="mcode">{{ m.asset_code }}</span>
              <span class="mgo">{{ moveLabel(m) }}</span>
              <span class="muser">{{ hasOwner(m.asset_user) ? m.asset_user : '未填使用人' }}</span>
              <span class="mstate" :class="m.relation_state">
                {{ RELATION_LABEL[m.relation_state] ?? m.relation_state }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="preview.station_changes.length" class="pane">
          <div class="pane-title">点位侧的变更</div>
          <ul>
            <li v-for="(s, i) in preview.station_changes" :key="i">{{ s }}</li>
          </ul>
        </div>

        <div v-if="preview.notes.length" class="pane">
          <div class="pane-title">说明</div>
          <ul>
            <li v-for="(n, i) in preview.notes" :key="i">{{ n }}</li>
          </ul>
        </div>

        <div v-if="preview.confirmations.length" class="pane warn">
          <div class="pane-title">需要你确认的差异</div>
          <ul>
            <li v-for="(w, i) in preview.confirmations" :key="i">{{ w }}</li>
          </ul>
          <el-checkbox v-model="acknowledged" class="ack">
            我已看过以上差异，确认继续（系统不会自动改任何一侧的使用人）
          </el-checkbox>
        </div>
      </template>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :disabled="!preview || !preview.ok || (preview.confirmations.length > 0 && !acknowledged)"
        :loading="applying"
        @click="runApply"
      >
        执行迁移
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.note {
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #94a3b8;
  border-radius: 8px; padding: 9px 12px; font-size: 12px; color: #475569;
  line-height: 1.7; margin-bottom: 16px;
}
.note b { color: #0f172a; }

.block { margin-bottom: 16px; }
.blk-title { font-size: 12.5px; font-weight: 700; color: #334155; margin-bottom: 8px; }
.blk-hint { font-size: 11.5px; color: #94a3b8; line-height: 1.7; margin-top: 6px; }
.blk-hint b { color: #475569; }
.block .el-checkbox { display: flex; margin-bottom: 6px; }
.block .el-checkbox .sub { color: #94a3b8; font-size: 11.5px; margin-left: 6px; }

.asset-picker {
  max-height: 190px; overflow-y: auto; border: 1px solid #e2e8f0; border-radius: 9px; padding: 8px 10px;
}
.pick-row { display: block; margin-bottom: 2px; }
.pick-code { font-weight: 700; color: #0f172a; font-variant-numeric: tabular-nums; }
.pick-sub { color: #94a3b8; font-size: 11.5px; margin-left: 8px; }

.acts { margin: 4px 0 2px; }

.preview { margin-top: 8px; }
.pane {
  border: 1px solid #e2e8f0; border-radius: 10px; padding: 11px 13px; margin-bottom: 10px;
}
.pane-title { font-size: 12px; font-weight: 700; color: #334155; margin-bottom: 7px; }
.pane-title span { font-weight: 500; color: #94a3b8; }
.pane ul { margin: 0; padding-left: 18px; }
.pane li { font-size: 11.5px; color: #475569; line-height: 1.8; }
.pane.conflict { background: #fef2f2; border-color: #fca5a5; }
.pane.conflict .pane-title { color: #b91c1c; }
.pane.conflict li { color: #991b1b; }
.pane.warn { background: #fffbeb; border-color: #fde68a; }
.pane.warn .pane-title { color: #b45309; }
.pane.warn li { color: #92400e; }
/* 归属块：信息性，用蓝不用红 —— 它不是警告，是"告诉你事实" */
.pane.owner { background: #f8fafc; }
.owner-row {
  display: flex; align-items: center; gap: 9px; padding: 4px 0;
  font-size: 12px; font-variant-numeric: tabular-nums;
}
.ocode { font-weight: 700; color: #0f172a; min-width: 48px; }
.obefore { color: #64748b; }
.oarrow { color: #cbd5e1; }
.oafter { color: #475569; font-weight: 600; }
.oafter.changed { color: #b45309; font-weight: 700; }
.okeep { font-size: 10.5px; color: #94a3b8; }
.owner-hint { font-size: 11px; color: #64748b; line-height: 1.7; margin-top: 7px; }
.ack { margin-top: 9px; font-size: 12px; }

.moves { max-height: 220px; overflow-y: auto; }
.move-row {
  display: flex; align-items: center; gap: 10px; padding: 5px 0;
  border-bottom: 1px dashed #f1f5f9; font-size: 11.5px;
}
.move-row:last-child { border-bottom: none; }
.mcode { font-weight: 700; color: #0f172a; font-variant-numeric: tabular-nums; min-width: 92px; }
.mgo { color: #2563eb; font-weight: 600; min-width: 150px; }
.muser { color: #64748b; flex: 1; }
.mstate { font-size: 10.5px; color: #64748b; }
.mstate.mismatch { color: #b45309; font-weight: 700; }
</style>
