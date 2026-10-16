<script setup lang="ts">
/**
 * 轻资产品类的新建 / 编辑。
 *
 * ⚠️ 这里**没有**「追踪方式」了 —— 上一版那套「按数量 / 按单件」双模式已经取消：
 * 所有品类统一按「品类 + 数量」管理，SSD / 内存也像键鼠一样记"几块、装在哪台机器上"。
 * 界面不再问用户一个他自己也答不上来的问题（"这东西要不要逐件追？"）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightCategory } from '@/types'

const props = defineProps<{
  modelValue: boolean
  /** null = 新建 */
  category: LightCategory | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved', category: LightCategory): void
}>()

const form = ref({
  name: '',
  brand: '',
  spec: '',
  unit: '件',
  notes: '',
})
const saving = ref(false)

const isEdit = computed(() => props.category !== null)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const c = props.category
    form.value = {
      name: c?.name ?? '',
      brand: c?.brand ?? '',
      spec: c?.spec ?? '',
      unit: c?.unit ?? '件',
      notes: c?.notes ?? '',
    }
  },
)

async function save() {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('请填写品类名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name,
      brand: form.value.brand.trim() || null,
      spec: form.value.spec.trim() || null,
      unit: form.value.unit.trim() || '件',
      notes: form.value.notes.trim() || null,
      operator: appState.operator || null,
    }
    const saved = isEdit.value
      ? await lightApi.updateCategory(props.category!.id, payload)
      : await lightApi.createCategory(payload)
    ElMessage.success(isEdit.value ? `已保存「${saved.name}」` : `已新增品类「${saved.name}」`)
    emit('saved', saved)
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEdit ? `编辑品类：${category?.name}` : '新增轻资产品类'"
    width="min(560px, 94vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form label-width="72px" label-position="top">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" maxlength="80" placeholder="如：罗技 MK270 键鼠套装" />
      </el-form-item>
      <div class="grid-2">
        <el-form-item label="品牌">
          <el-input v-model="form.brand" maxlength="64" placeholder="选填，如：罗技" />
        </el-form-item>
        <el-form-item label="规格">
          <el-input v-model="form.spec" maxlength="128" placeholder="选填，如：2.4G 无线" />
        </el-form-item>
      </div>
      <el-form-item label="计量单位">
        <el-input v-model="form.unit" maxlength="16" placeholder="套 / 个 / 块 / 条" />
      </el-form-item>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
        <template #title>统一按「品类 + 数量」管理</template>
        <div style="font-size: 12.5px; line-height: 1.7; margin-top: 2px">
          不区分"按数量 / 按单件"—— 记"还有几套、装在哪台机器上、发给谁"就够了，
          不需要给每一件配一个身份。单位只是给人看的（套 / 条 / 块），不参与计算。
        </div>
      </el-alert>
      <el-form-item label="备注">
        <el-input v-model="form.notes" type="textarea" :rows="2" maxlength="500" placeholder="选填" />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">
        {{ isEdit ? '保存' : '创建' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 12px;
}

@media (max-width: 640px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>
