<script setup lang="ts">
/**
 * 手机扫码进详情页后，顶部的「盘点条」。
 *
 * 为什么不做一个摄像头扫码页：浏览器调摄像头必须 HTTPS 或 localhost，
 * 而本系统是局域网 http://192.168.x.x —— 手机上 getUserMedia 会被直接拦掉，
 * 引任何扫码库都救不回来。
 * 所以改成：用手机自带相机/微信扫设备上那张已有二维码 → 落到详情页 →
 * 这里查一下「这台设备是不是正在被某个盘点任务盯着」，是的话直接给标记按钮。
 * 不需要摄像头权限、不需要 HTTPS，微信内置浏览器里也能用。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, inventoryApi } from '@/api'
import { appState, saveOperator } from '@/stores/app'
import type { InventoryContext, InventoryResult } from '@/types'
import { inventoryResultMeta, relativeTime } from '@/utils/format'

const props = defineProps<{
  assetId: number
  assetCode: string
  /** 外部触发的刷新（比如在详情页做了别的操作） */
  refreshKey?: number
}>()

const emit = defineEmits<{
  (e: 'marked'): void
}>()

const context = ref<InventoryContext | null>(null)
const acting = ref(false)
const operatorInput = ref('')

const meta = computed(() => inventoryResultMeta(context.value?.result ?? 'pending'))
const marked = computed(() => context.value != null && context.value.result !== 'pending')

async function load() {
  if (!props.assetId) return
  operatorInput.value = appState.operator
  try {
    const res = await inventoryApi.context(props.assetId)
    context.value = res ?? null
  } catch {
    // 静默失败：没有进行中的盘点任务不该让详情页报错
    context.value = null
  }
}

async function mark(result: InventoryResult) {
  if (!context.value) return

  const operator = (appState.operator || operatorInput.value).trim()
  if (!operator) {
    ElMessage.warning('请先填一下你的名字，盘点结果要记录盘点人')
    return
  }
  saveOperator(operator)

  let note: string | null = null
  if (result === 'abnormal') {
    try {
      const res = await ElMessageBox.prompt(
        '哪里不对？例如「机器不在这个位置」「SN 对不上」',
        '标记异常',
        {
          confirmButtonText: '确认异常',
          cancelButtonText: '取消',
          inputPlaceholder: '选填，但强烈建议写一句',
          inputValue: '',
        },
      )
      note = res.value?.trim() || null
    } catch {
      return
    }
  }

  acting.value = true
  try {
    const res = await inventoryApi.mark(context.value.task_id, {
      asset_id: props.assetId,
      result,
      operator,
      note,
    })
    ElMessage.success(result === 'checked' ? '已标记为已盘' : result === 'abnormal' ? '已标记为异常' : '已撤销标记')
    await load()
    emit('marked')
    void res
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

watch(() => [props.assetId, props.refreshKey], load, { immediate: true })
</script>

<template>
  <div v-if="context" class="inv-bar" :style="{ borderColor: meta.border }">
    <div class="lead">
      <span class="ic" :style="{ background: meta.bg, color: meta.color }">📋</span>
      <div class="text">
        <div class="line1">
          盘点任务《{{ context.task_name }}》
          <span class="state" :style="{ color: meta.color, background: meta.bg, borderColor: meta.border }">
            {{ meta.icon }} {{ context.result_label }}
          </span>
        </div>
        <div class="line2">
          <template v-if="marked">
            盘点人 {{ context.operator || '—' }} · {{ relativeTime(context.checked_at) }}
            <template v-if="context.note"> · {{ context.note }}</template>
          </template>
          <template v-else>
            这台设备还没盘到，盘完点一下右边的按钮。
          </template>
        </div>
      </div>
    </div>

    <div class="actions">
      <el-input
        v-if="!appState.operator"
        v-model="operatorInput"
        size="small"
        placeholder="你的名字"
        class="who"
        @change="saveOperator(operatorInput)"
      />
      <span v-else class="who-chip">{{ appState.operator }}</span>

      <el-button
        v-if="context.result !== 'checked'"
        size="small"
        type="success"
        :loading="acting"
        @click="mark('checked')"
      >
        ✅ 标记已盘
      </el-button>
      <el-button
        v-if="context.result !== 'abnormal'"
        size="small"
        :loading="acting"
        class="abn"
        @click="mark('abnormal')"
      >
        ⚠️ 异常
      </el-button>
      <el-button
        v-if="marked"
        size="small"
        link
        :loading="acting"
        @click="mark('pending')"
      >
        撤销
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.inv-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 12px 16px;
  margin-bottom: 14px;
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: var(--shadow-sm);
}

.inv-bar .lead {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 220px;
}

.inv-bar .ic {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
}

.inv-bar .text {
  min-width: 0;
}

.inv-bar .line1 {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13.5px;
  font-weight: 500;
}

.inv-bar .state {
  font-size: 11.5px;
  font-weight: 400;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid;
}

.inv-bar .line2 {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-2);
}

.inv-bar .actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.inv-bar .who {
  width: 110px;
}

.inv-bar .who-chip {
  font-size: 12px;
  color: var(--text-2);
  background: #f2f3f5;
  border-radius: 999px;
  padding: 3px 10px;
}

.inv-bar .abn {
  border-color: #f7c5c1;
  color: #d54941;
}

@media (max-width: 640px) {
  .inv-bar {
    padding: 12px;
  }

  .inv-bar .actions {
    width: 100%;
  }

  .inv-bar .actions .el-button {
    flex: 1;
    margin-left: 0;
  }

  .inv-bar .actions .el-button + .el-button {
    margin-left: 0;
  }
}
</style>
