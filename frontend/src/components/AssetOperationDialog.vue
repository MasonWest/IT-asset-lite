<script setup lang="ts">
/**
 * 状态流转操作弹窗：领用 / 归还 / 送修 / 维修完成 / 报废 / 恢复入库。
 *
 * 只列出「当前状态允许的动作」，其余按钮根本不渲染 ——
 * 让用户先点再被后端 409 挡回来，是很差的体验。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi } from '@/api'
import { appState, saveOperator } from '@/stores/app'
import type { Asset, AssetOperation, AssetStatus } from '@/types'
import { OPERATION_META, operationMeta, statusMeta } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  asset: Asset | null
  users: string[]
  locations: string[]
  /** 从详情页的下拉菜单指定要执行哪个动作；不在可用列表里就退回默认 */
  initialAction?: AssetOperation | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'done', asset: Asset): void
}>()

const saving = ref(false)
const form = reactive({
  action: 'checkout' as AssetOperation,
  operator: '',
  user_name: '',
  location: '',
  note: '',
})

const available = computed<AssetOperation[]>(() => props.asset?.actions ?? [])

const current = computed(() => operationMeta(form.action))

/** 归还时顺手把「放回哪个位置」填上，默认给个库房提示 */
const locationPlaceholder = computed(() =>
  form.action === 'return' ? '放回哪个位置，例如 总部 2F 库房 货架 C2' : '可选，填了会同时更新存放位置',
)

function reset() {
  const asset = props.asset
  form.operator = appState.operator
  form.user_name = asset?.user_name ?? ''
  form.location = asset?.location ?? ''
  form.note = ''
  const actions = asset?.actions ?? []
  if (props.initialAction && actions.includes(props.initialAction)) {
    form.action = props.initialAction
  } else {
    form.action = actions.includes('checkout') ? 'checkout' : (actions[0] ?? 'checkout')
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) reset()
  },
)

// 切动作时把「使用人」清掉：领用才需要，其它动作带个使用人过去很容易误存
watch(
  () => form.action,
  (action) => {
    if (!operationMeta(action).needUser) form.user_name = ''
  },
)

const title = computed(() => {
  const code = props.asset?.asset_code ?? ''
  return `${current.value.label} ${code}`
})

async function submit() {
  if (!props.asset) return
  const operator = form.operator.trim()
  if (!operator) {
    ElMessage.warning('请先填写操作人，会记录到设备的变更历史里')
    return
  }
  if (current.value.needUser && !form.user_name.trim()) {
    ElMessage.warning(`「${current.value.label}」需要指定使用人`)
    return
  }

  if (current.value.confirm) {
    try {
      await ElMessageBox.confirm(
        `确定把「${props.asset.asset_code}」标记为${current.value.label}？${current.value.hint}`,
        '请确认',
        { type: 'warning', confirmButtonText: `确认${current.value.label}`, cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  saving.value = true
  try {
    const res = await assetApi.operate(props.asset.id, {
      action: form.action,
      operator,
      user_name: form.user_name.trim() || null,
      location: form.location.trim() || null,
      note: form.note.trim() || null,
    })
    saveOperator(operator)
    ElMessage.success(`已${current.value.label}`)
    emit('done', res.asset)
    emit('update:modelValue', false)
  } catch (error) {
    // 状态机在服务端，冲突时把后端那句话原样抛给用户
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

const statusLabel = computed(() =>
  props.asset ? statusMeta(props.asset.status).label : '',
)
const OPERATIONS = OPERATION_META
const STATUS = statusMeta
const CHAIN: AssetStatus[] = ['idle', 'in_use', 'repair', 'scrapped']
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="min(560px, 94vw)"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="asset" class="op-body">
      <div class="state-chain">
        <span class="lbl">当前状态</span>
        <span class="chain">
          <span
            v-for="s in CHAIN"
            :key="s"
            class="pill"
            :class="{ on: s === asset.status }"
            :style="s === asset.status
              ? { color: STATUS(s).color, background: STATUS(s).bg, borderColor: STATUS(s).border }
              : {}"
          >
            {{ STATUS(s).label }}
          </span>
        </span>
      </div>

      <div class="op-picker">
        <button
          v-for="a in available"
          :key="a"
          class="op-card"
          :class="[`tone-${OPERATIONS[a].tone}`, { on: form.action === a }]"
          @click="form.action = a"
        >
          <span class="ic">{{ OPERATIONS[a].icon }}</span>
          <span class="nm">{{ OPERATIONS[a].label }}</span>
        </button>
      </div>
      <p class="hint">{{ current.hint }}</p>

      <el-form label-position="top">
        <div class="op-grid">
          <el-form-item label="操作人" required>
            <el-input v-model="form.operator" placeholder="你的名字，会写进变更历史" clearable />
          </el-form-item>

          <el-form-item v-if="current.needUser" label="使用人" required>
            <el-select
              v-model="form.user_name"
              placeholder="可直接输入新的人名"
              filterable
              allow-create
              default-first-option
              clearable
              style="width: 100%"
            >
              <el-option v-for="u in users" :key="u" :label="u" :value="u" />
            </el-select>
          </el-form-item>

          <el-form-item label="存放位置" :class="{ full: !current.needUser }">
            <el-select
              v-model="form.location"
              :placeholder="locationPlaceholder"
              filterable
              allow-create
              default-first-option
              clearable
              style="width: 100%"
            >
              <el-option v-for="l in locations" :key="l" :label="l" :value="l" />
            </el-select>
          </el-form-item>

          <el-form-item label="备注" class="full">
            <el-input
              v-model="form.note"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              :placeholder="`例如：${current.label}原因、配件情况等，可留空`"
            />
          </el-form-item>
        </div>
      </el-form>

      <div class="preview">
        <span class="lbl">完成后</span>
        <span class="arrow">{{ statusLabel }} → {{ OPERATIONS[form.action]?.label }}</span>
      </div>
    </div>

    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <el-button @click="close">取消</el-button>
        <el-button
          :type="current.tone === 'danger' ? 'danger' : 'primary'"
          :loading="saving"
          @click="submit"
        >
          确认{{ current.label }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.op-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.state-chain,
.preview {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.state-chain .lbl,
.preview .lbl {
  color: var(--text-3);
  font-size: 12px;
  flex-shrink: 0;
}

.state-chain .chain {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.state-chain .pill {
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: #fafbfc;
  color: var(--text-3);
  font-size: 12px;
}

.state-chain .pill.on {
  font-weight: 500;
}

.op-picker {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
  gap: 8px;
}

.op-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 10px 6px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}

.op-card .ic {
  font-size: 18px;
  line-height: 1;
}

.op-card .nm {
  font-size: 12.5px;
  color: var(--text-2);
}

.op-card:hover {
  border-color: var(--border-strong);
  background: #fafbfc;
}

.op-card.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  box-shadow: 0 0 0 2px var(--primary-soft);
}

.op-card.on .nm {
  color: var(--primary);
  font-weight: 500;
}

.op-card.tone-danger.on {
  border-color: #d54941;
  background: #fdecea;
  box-shadow: 0 0 0 2px #fdecea;
}

.op-card.tone-danger.on .nm {
  color: #d54941;
}

.op-card.tone-warning.on {
  border-color: #e37318;
  background: #fff4e6;
  box-shadow: 0 0 0 2px #fff4e6;
}

.op-card.tone-warning.on .nm {
  color: #a15c00;
}

.hint {
  margin: -4px 0 0;
  font-size: 12.5px;
  color: var(--text-2);
  line-height: 1.6;
  padding: 8px 10px;
  background: #fafbfc;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.op-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.op-grid .full {
  grid-column: 1 / -1;
}

.preview .arrow {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-1);
}

@media (max-width: 640px) {
  .op-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
