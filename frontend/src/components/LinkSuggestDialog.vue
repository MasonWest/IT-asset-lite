<script setup lang="ts">
/**
 * 关联操作建议面板 —— 两个方向共用一套 UI，但**语义严格分开**：
 *
 * | mode | 触发 | 默认勾选 | 执行接口 |
 * | --- | --- | --- | --- |
 * | `link` | 无归属点位 → 有人（填人） | `fill` 默认勾 | `apply-linkage` |
 * | `unlink` | 有人点位 → 清空（清人） | **一个都不勾** | `apply-unlink` |
 *
 * 三条设计约束（与落地评审口径一致）：
 * 1. **只提示有意义的变化** —— 不相干的资产后端就不下发，这里不会出现。
 * 2. **不替用户决定** —— `conflict` / `keep` / `clear` 默认不勾，勾了才动。
 * 3. **建议不落库** —— 没有"已处理/忽略"状态，关掉就是暂不处理。
 *
 * ⚠️ **两个方向互不触发**：清人只由「用户在点位侧栏清空使用人」产生；
 * 点位迁移（`move_user`）走它自己的预览与令牌，两者不互相调用（用户口径"各干各的"）。
 *
 * ⚠️ 数据纪律：每行文案（label / hint）由后端下发，本组件**一个字都不拼**；
 * 执行时后端会在写库前**重新核对**（期间被改过的会被跳过并回包说明）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { apiMessage, workstationApi } from '@/api'
import { appState } from '@/stores/app'
import type { ClearSuggestionsResult, LinkSuggestionsResult } from '@/types'

type Row =
  | LinkSuggestionsResult['suggestions'][number]
  | ClearSuggestionsResult['suggestions'][number]

const props = defineProps<{
  /** 预检结果。两个方向的回包结构兼容（suggestions / total_assets）。null = 面板关闭 */
  result: LinkSuggestionsResult | ClearSuggestionsResult | null
  /** 方向：link = 填人，unlink = 清人 */
  mode: 'link' | 'unlink'
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  /** 用户确认。ids = 勾选的资产 id；空数组 = 暂不处理 / 都保留（只保存点位） */
  (e: 'confirm', ids: number[]): void
}>()

const isUnlink = computed(() => props.mode === 'unlink')
const title = computed(
  () => `${isUnlink.value ? '清人建议' : '关联建议'} · ${props.result?.station_code ?? ''}`,
)

/** 勾选状态：asset_id → boolean。每次打开按后端给的 checked 初始化 */
const checkedMap = ref<Record<number, boolean>>({})

watch(
  () => props.result,
  (r) => {
    const map: Record<number, boolean> = {}
    for (const s of r?.suggestions ?? []) map[s.asset_id] = s.checked
    checkedMap.value = map
  },
  { immediate: true },
)

const rows = computed<Row[]>(() => (props.result?.suggestions ?? []) as Row[])
const checkedCount = computed(() => rows.value.filter((r) => checkedMap.value[r.asset_id]).length)
const nothingActionable = computed(() => rows.value.length === 0)

/** 建议 → 行样式。link: fill 蓝 / keep 灰 / conflict 琥珀 ｜ unlink: clear 琥珀。
 *  刻意不用红：这是建议不是告警（问题中心同一条理念：差异不是错误）。 */
const ROW_CLASS: Record<string, string> = {
  fill: 'is-fill',
  keep: 'is-keep',
  conflict: 'is-conflict',
  clear: 'is-clear',
}

function selectAll() {
  for (const r of rows.value) checkedMap.value[r.asset_id] = true
}

function selectNone() {
  for (const r of rows.value) checkedMap.value[r.asset_id] = false
}

async function onConfirm() {
  const r = props.result
  if (!r) return
  const ids = rows.value.filter((row) => checkedMap.value[row.asset_id]).map((row) => row.asset_id)
  if (!ids.length) {
    emit('confirm', [])
    return
  }
  try {
    if (isUnlink.value) {
      const res = await workstationApi.applyUnlink(r.station_id, {
        asset_ids: ids,
        operator: appState.operator || null,
      })
      report(res.skipped.map((s) => s.asset_code ?? `#${s.asset_id}`), res.cleared, '解除')
    } else {
      const res = await workstationApi.applyLinkage(r.station_id, {
        user_name: (r as LinkSuggestionsResult).new_user,
        asset_ids: ids,
        operator: appState.operator || null,
      })
      report(res.skipped.map((s) => s.asset_code ?? `#${s.asset_id}`), res.updated, '同步')
    }
    emit('confirm', ids)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

/** 面板打开期间数据变了 → 后端拦下了，如实告知，不静默 */
function report(names: string[], done: number, verb: string) {
  if (names.length) {
    ElMessage.warning(`已${verb} ${done} 台；${names.join('、')} 在此期间已被修改，已跳过`)
  } else {
    ElMessage.success(`已${verb} ${done} 台设备的使用人`)
  }
}
</script>

<template>
  <el-dialog
    :model-value="result !== null"
    :title="title"
    width="520px"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="(v: boolean) => !v && emit('close')"
  >
    <div v-loading="loading" class="ls-body">
      <!-- 填人方向：新使用人接管点位 -->
      <p v-if="!isUnlink" class="ls-lead">
        <b>{{ (result as LinkSuggestionsResult)?.new_user }}</b> 接管了
        <b>{{ result?.station_code }}</b
        >，该点位下有 {{ result?.total_assets }} 台资产，其中
        <b>{{ rows.length }}</b> 台需要你看一眼<template
          v-if="(result as LinkSuggestionsResult)?.matched_count > 0"
        >
          （另有 {{ (result as LinkSuggestionsResult).matched_count }} 台已登记为本人，无需调整）</template
        >：
      </p>

      <!-- 清人方向：使用人已清空，还有设备挂在他名下 -->
      <p v-else class="ls-lead">
        <b>{{ result?.station_code }}</b> 的使用人 <b>{{ (result as ClearSuggestionsResult)?.old_user }}</b>
        已清空，该点位下有 {{ result?.total_assets }} 台资产，其中
        <b>{{ rows.length }}</b> 台仍登记为 {{ (result as ClearSuggestionsResult)?.old_user }}<template
          v-if="(result as ClearSuggestionsResult)?.other_count"
        >
          （另有 {{ (result as ClearSuggestionsResult).other_count }} 台不受影响）</template
        >：
      </p>

      <div class="ls-rows">
        <label
          v-for="row in rows"
          :key="row.asset_id"
          class="ls-row"
          :class="[ROW_CLASS[row.suggestion], { off: !checkedMap[row.asset_id] }]"
        >
          <input v-model="checkedMap[row.asset_id]" type="checkbox" class="ls-check" />
          <span class="ls-main">
            <span class="ls-code">{{ row.asset_code }}</span>
            <span class="ls-spec">{{ row.device_type_name || '—' }}</span>
          </span>
          <span class="ls-side">
            <span class="ls-tag" :class="ROW_CLASS[row.suggestion]">{{ row.label }}</span>
            <span class="ls-hint">
              {{ row.current_user ? `当前：${row.current_user}` : row.hint }}
            </span>
          </span>
        </label>
      </div>

      <div class="ls-foot-hint">
        <template v-if="!isUnlink">
          只同步你勾选的；冲突项（<b>当前：别人</b>）勾选即覆盖，请逐台确认。
          暂不处理也没关系 —— 差异会留在问题中心，随时能找到。
        </template>
        <template v-else>
          只清除你勾选的；<b>不勾的设备保留原使用人</b>，适合"人走了、设备留给下一任"。
          清空之后再留下的名字，问题中心<b>不会</b>替你盯着（它只看点位有没有人），所以这里值得看一眼。
        </template>
      </div>
    </div>

    <template #footer>
      <div class="ls-actions">
        <el-button text :disabled="nothingActionable" @click="selectAll">全选</el-button>
        <el-button text :disabled="checkedCount === 0" @click="selectNone">全不选</el-button>
        <span class="spacer" />
        <el-button @click="emit('confirm', [])">{{ isUnlink ? '都保留' : '暂不处理' }}</el-button>
        <el-button type="primary" :loading="loading" @click="onConfirm">
          {{ isUnlink ? '清除' : '同步' }}{{ checkedCount ? ` ${checkedCount} 台` : '' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.ls-lead { font-size: 13px; color: #334155; line-height: 1.8; margin: 0 0 12px; }
.ls-lead b { color: #0f172a; }

.ls-rows { display: flex; flex-direction: column; gap: 8px; max-height: 320px; overflow-y: auto; }

.ls-row {
  display: flex; align-items: center; gap: 10px; padding: 9px 11px;
  border: 1px solid #e2e8f0; border-radius: 9px; cursor: pointer;
  transition: border-color 0.14s, background 0.14s;
}
.ls-row.is-fill { background: #f8fbff; border-color: #bfdbfe; }
.ls-row.is-conflict,
.ls-row.is-clear { background: #fffdf7; border-color: #fde68a; }
.ls-row.off { opacity: 0.62; }
.ls-check { width: 15px; height: 15px; flex: none; accent-color: #2563eb; }

.ls-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ls-code { font-size: 12.5px; font-weight: 700; color: #0f172a; }
.ls-spec { font-size: 10.5px; color: #94a3b8; margin-top: 1px; }

.ls-side { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.ls-tag { font-size: 10.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; line-height: 1.6; }
.ls-tag.is-fill { background: #eff6ff; color: #1d4ed8; }
.ls-tag.is-keep { background: #f1f5f9; color: #64748b; }
.ls-tag.is-conflict,
.ls-tag.is-clear { background: #fef3c7; color: #b45309; }
.ls-hint { font-size: 10.5px; color: #94a3b8; }

.ls-foot-hint {
  margin-top: 12px; font-size: 11px; color: #94a3b8; line-height: 1.7;
  border-top: 1px dashed #e2e8f0; padding-top: 9px;
}
.ls-foot-hint b { color: #64748b; }

.ls-actions { display: flex; align-items: center; gap: 6px; width: 100%; }
.ls-actions .spacer { flex: 1; }
</style>
