<script setup lang="ts">
/**
 * **历史导入**：把以前 Excel 里的轻资产流转补进系统 —— **只补历史，不动当前账**。
 *
 * ## 它和"期初确认"完全是两件事
 *
 * Excel 里通常只有"某年某月把这套键鼠给了张三"，**没有对应的入库记录**。
 * 如果把这些直接当当前状态，会得到负库存或凭空出现的持有量（上一轮实测出来过）。
 * 所以导入后当前持有仍是空的 —— 台账上显示"未登记"而不是猜一个数字。
 * **这是诚实，不是缺陷。**
 *
 * ## 预览与落库跑同一段解析
 *
 * 不存在"预览说没事、确认就报错"。同一份文件导两遍，第二遍 0 新增（按自然键去重）。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightImportPreview } from '@/types'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'imported'): void
}>()

const file = ref<File | null>(null)
const preview = ref<LightImportPreview | null>(null)
const parsing = ref(false)
const committing = ref(false)

const HEADERS = '品类,操作,数量,来源类型,来源,去向类型,去向,发生日期,备注'

const canCommit = computed(
  () => !!preview.value && !preview.value.schema_note && preview.value.will_import > 0,
)

function reset() {
  file.value = null
  preview.value = null
}

function onPick(e: Event) {
  const target = e.target as HTMLInputElement
  const picked = target.files?.[0] ?? null
  file.value = picked
  preview.value = null
  if (picked) void runPreview(picked)
}

async function runPreview(picked: File) {
  parsing.value = true
  try {
    preview.value = await lightApi.importPreview(picked)
  } catch (error) {
    ElMessage.error(apiMessage(error))
    preview.value = null
  } finally {
    parsing.value = false
  }
}

async function commit() {
  if (!file.value) return
  committing.value = true
  try {
    const result = await lightApi.importHistory(file.value, appState.operator || null)
    ElMessage.success(
      `导入完成：新增 ${result.will_import} 条历史流水，已存在 ${result.duplicate} 条（「当前持有」不变）`,
    )
    emit('imported')
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    committing.value = false
  }
}

const ACTION_META: Record<string, { label: string; tone: string }> = {
  import: { label: '将导入', tone: 'ok' },
  duplicate: { label: '已存在', tone: 'mute' },
  error: { label: '有问题', tone: 'bad' },
}

function actionMeta(action: string) {
  return ACTION_META[action] ?? { label: action, tone: 'mute' }
}

function onChange() {
  reset()
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="导入轻资产历史流水"
    width="min(760px, 95vw)"
    @update:model-value="emit('update:modelValue', false)"
  >
    <div class="infobox">
      只补<b>过去发生过</b>的流转记录。<b>不会</b>改变当前库存与持有 ——
      Excel 里通常没有对应的入库记录，拿来当现状会算出负库存或凭空多出的数量。
      导入后请在台账里按实际盘点结果登记一次「期初确认」，把真实基线建起来。
    </div>

    <div class="upload-row">
      <label class="file-btn">
        <AppIcon name="download" :size="14" style="margin-right: 5px" />
        {{ file ? file.name : '选择 CSV / Excel 文件' }}
        <input type="file" accept=".csv,.xlsx,.xlsm" @change="onPick" />
      </label>
      <el-button v-if="file" size="small" @click="onChange">清除</el-button>
      <span class="hint">表头：{{ HEADERS }}</span>
    </div>

    <div v-if="parsing" class="loading">正在解析…</div>

    <template v-if="preview">
      <el-alert v-if="preview.schema_note" type="error" :closable="false" show-icon>
        <template #title>{{ preview.schema_note }}</template>
      </el-alert>

      <template v-else>
        <div class="buckets">
          <span class="bucket ok">将导入 {{ preview.will_import }}</span>
          <span class="bucket mute">已存在（跳过）{{ preview.duplicate }}</span>
          <span class="bucket bad">有问题 {{ preview.error }}</span>
          <span class="bucket">共 {{ preview.total }} 行</span>
        </div>

        <div class="rows">
          <div v-for="(r, i) in preview.rows" :key="i" class="row">
            <span class="rn">第 {{ r.row }} 行</span>
            <span class="chip" :class="actionMeta(r.action).tone">{{ actionMeta(r.action).label }}</span>
            <span class="sum">{{ r.summary || r.message }}</span>
            <span v-if="r.message && r.summary" class="msg">{{ r.message }}</span>
          </div>
          <div v-if="preview.rows_truncated" class="more">… 只显示前 {{ preview.rows.length }} 行</div>
        </div>
      </template>
    </template>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="committing" :disabled="!canCommit" @click="commit">
        导入 {{ preview?.will_import ?? 0 }} 条历史流水
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.infobox {
  margin-bottom: 14px;
  padding: 11px 13px;
  border-radius: var(--radius-sm);
  background: #f2f6ff;
  border: 1px solid #d6e2ff;
  color: #33507e;
  font-size: 12.5px;
  line-height: 1.7;
}
.infobox b { color: #24406e; }

.upload-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.file-btn {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--border-strong, #dcdfe6);
  border-radius: var(--el-border-radius-base, 4px);
  background: var(--panel, #fff);
  font-size: 13px;
  cursor: pointer;
}
.file-btn:hover { border-color: var(--el-color-primary); color: var(--el-color-primary); }
.file-btn input { display: none; }
.hint { font-size: 11.5px; color: var(--text-3); }

.loading { padding: 18px 0; text-align: center; color: var(--text-3); font-size: 13px; }

.buckets { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.bucket {
  padding: 2px 10px; border-radius: 999px; font-size: 12px;
  background: #f2f3f5; color: var(--text-2); border: 1px solid var(--border);
}
.bucket.ok { background: #e8f8e5; color: #1f8a17; border-color: #c8ebc2; }
.bucket.bad { background: #ffeceb; color: #d13b36; border-color: #ffd0ce; }
.bucket.mute { color: var(--text-3); }

.rows { max-height: 320px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.row {
  display: flex; align-items: baseline; gap: 8px;
  padding: 6px 10px; font-size: 12.5px; border-bottom: 1px solid var(--border);
}
.row:last-child { border-bottom: 0; }
.rn { color: var(--text-3); flex: none; min-width: 62px; }
.chip {
  flex: none; padding: 0 7px; border-radius: 999px; font-size: 11px; border: 1px solid;
  background: #f2f3f5; color: var(--text-3); border-color: var(--border);
}
.chip.ok { background: #e8f8e5; color: #1f8a17; border-color: #c8ebc2; }
.chip.bad { background: #ffeceb; color: #d13b36; border-color: #ffd0ce; }
.sum { flex: 1; color: var(--text-1); }
.msg { color: var(--text-3); }
.more { padding: 8px 10px; color: var(--text-3); font-size: 12px; }
</style>
