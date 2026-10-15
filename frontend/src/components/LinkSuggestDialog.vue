<script setup lang="ts">
/**
 * 关联操作建议面板（v2.7.0 第一期）。
 *
 * 触发时机：给**原本无归属**的点位新增使用人时，如果该点位下存在
 * 使用人为空或与新使用人不一致的资产，先在这里让用户挑一遍。
 *
 * 三条设计约束（与落地评审口径一致）：
 * 1. **只提示有意义的变化** —— 已是新使用人的资产后端就不下发，这里不会出现。
 * 2. **冲突不替用户决定** —— `conflict` / `keep` 默认不勾，勾选才覆盖。
 * 3. **建议不落库** —— 本组件没有"已处理/忽略"状态，关掉就是暂不处理；
 *    点位保存后问题中心会按最新数据重新计算。
 *
 * ⚠️ 数据纪律：每行的文案（label / hint）由后端下发，本组件**一个字都不拼**；
 * 执行后由父组件调 `apply-linkage`，后端在写库前还会**重新核对**一遍
 * （弹窗打开期间资产被改过的话会被跳过并回包说明），本组件不做本地兜底判断。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { apiMessage, workstationApi } from '@/api'
import { appState } from '@/stores/app'
import type { LinkSuggestion, LinkSuggestionsResult } from '@/types'

const props = defineProps<{
  /** 预检结果。null = 面板关闭 */
  result: LinkSuggestionsResult | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  /** 用户确认。ids = 勾选的资产 id；空数组 = 全部暂不处理（只保存点位） */
  (e: 'confirm', ids: number[]): void
}>()

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

const rows = computed(() => props.result?.suggestions ?? [])
const checkedCount = computed(() => rows.value.filter((r) => checkedMap.value[r.asset_id]).length)
const allFill = computed(() => rows.value.every((r) => r.suggestion !== 'fill'))

/** 建议 → 行样式。fill 蓝（顺理成章）、keep 灰（可不动）、conflict 琥珀（要人拍板）。
 *  刻意不用红：这是建议不是告警，与问题中心"差异不是错误"同一个理念。 */
const ROW_CLASS: Record<LinkSuggestion['suggestion'], string> = {
  fill: 'is-fill',
  keep: 'is-keep',
  conflict: 'is-conflict',
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
    const res = await workstationApi.applyLinkage(r.station_id, {
      user_name: r.new_user,
      asset_ids: ids,
      operator: appState.operator || null,
    })
    if (res.skipped.length) {
      // 弹窗打开期间数据变了 —— 后端拦下了，如实告知，不静默
      const names = res.skipped
        .map((s) => s.asset_code ?? `#${s.asset_id}`)
        .join('、')
      ElMessage.warning(`已同步 ${res.updated} 台；${names} 在此期间已被修改，已跳过`)
    } else {
      ElMessage.success(`已同步 ${res.updated} 台设备的使用人`)
    }
    emit('confirm', ids)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}
</script>

<template>
  <el-dialog
    :model-value="result !== null"
    :title="`关联建议 · ${result?.station_code ?? ''}`"
    width="520px"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="(v: boolean) => !v && emit('close')"
  >
    <div v-loading="loading" class="ls-body">
      <p class="ls-lead">
        <b>{{ result?.new_user }}</b> 接管了 <b>{{ result?.station_code }}</b
        >，该点位下有 {{ result?.total_assets }} 台资产，其中
        <b>{{ rows.length }}</b> 台需要你看一眼<template v-if="result && result.matched_count > 0">
          （另有 {{ result.matched_count }} 台已登记为本人，无需调整）</template
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
        只同步你勾选的；冲突项（<b>当前：别人</b>）勾选即覆盖，请逐台确认。
        暂不处理也没关系 —— 差异会留在问题中心，随时能找到。
      </div>
    </div>

    <template #footer>
      <div class="ls-actions">
        <el-button text :disabled="allFill" @click="selectAll">全选</el-button>
        <el-button text :disabled="checkedCount === 0" @click="selectNone">全不选</el-button>
        <span class="spacer" />
        <el-button @click="emit('confirm', [])">暂不处理</el-button>
        <el-button type="primary" :loading="loading" @click="onConfirm">
          同步{{ checkedCount ? ` ${checkedCount} 台` : '' }}
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
.ls-row.is-conflict { background: #fffdf7; border-color: #fde68a; }
.ls-row.off { opacity: 0.62; }
.ls-check { width: 15px; height: 15px; flex: none; accent-color: #2563eb; }

.ls-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ls-code { font-size: 12.5px; font-weight: 700; color: #0f172a; }
.ls-spec { font-size: 10.5px; color: #94a3b8; margin-top: 1px; }

.ls-side { flex: none; display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.ls-tag { font-size: 10.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; line-height: 1.6; }
.ls-tag.is-fill { background: #eff6ff; color: #1d4ed8; }
.ls-tag.is-keep { background: #f1f5f9; color: #64748b; }
.ls-tag.is-conflict { background: #fef3c7; color: #b45309; }
.ls-hint { font-size: 10.5px; color: #94a3b8; }

.ls-foot-hint {
  margin-top: 12px; font-size: 11px; color: #94a3b8; line-height: 1.7;
  border-top: 1px dashed #e2e8f0; padding-top: 9px;
}
.ls-foot-hint b { color: #64748b; }

.ls-actions { display: flex; align-items: center; gap: 6px; width: 100%; }
.ls-actions .spacer { flex: 1; }
</style>
