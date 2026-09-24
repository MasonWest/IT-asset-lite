<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { ElMessage } from 'element-plus'

import { assetApi } from '@/api'
import { appState, saveOperator } from '@/stores/app'
import type { Asset, AssetPayload, AssetStatus, DeviceType } from '@/types'

const props = defineProps<{
  modelValue: boolean
  asset: Asset | null
  deviceTypes: DeviceType[]
  users: string[]
  locations: string[]
  presetTypeId?: number | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved', asset: Asset, mode: 'create' | 'update'): void
}>()

interface FormModel {
  asset_code: string
  finance_code: string
  device_type_id: number | undefined
  brand: string
  model: string
  serial_number: string
  status: AssetStatus
  user_name: string
  location: string
  purchase_date: string
  warranty_until: string
  notes: string
  operator: string
}

const formRef = ref<FormInstance>()
const saving = ref(false)

const form = reactive<FormModel>({
  asset_code: '',
  finance_code: '',
  device_type_id: undefined,
  brand: '',
  model: '',
  serial_number: '',
  status: 'in_use',
  user_name: '',
  location: '',
  purchase_date: '',
  warranty_until: '',
  notes: '',
  operator: '',
})

const isEdit = computed(() => Boolean(props.asset))
const title = computed(() => (isEdit.value ? `编辑资产 ${props.asset?.asset_code ?? ''}` : '新增资产'))

const statusOptions: { value: AssetStatus; label: string }[] = [
  { value: 'in_use', label: '在用' },
  { value: 'idle', label: '闲置' },
  { value: 'repair', label: '维修中' },
  { value: 'scrapped', label: '已报废' },
]

const rules: FormRules<FormModel> = {
  device_type_id: [{ required: true, message: '请选择设备类型', trigger: 'change' }],
  warranty_until: [
    {
      validator: (_rule, value: string, callback) => {
        const purchase = form.purchase_date
        if (value && purchase && value < purchase) {
          callback(new Error('保修到期日不能早于购入日期'))
          return
        }
        callback()
      },
      trigger: 'change',
    },
  ],
}

function resetForm() {
  const src = props.asset
  // 经办人永远取「当前操作人」，不要从旧记录里带 —— 大多数时候是换个人在改
  form.operator = appState.operator
  if (src) {
    form.asset_code = src.asset_code
    form.finance_code = src.finance_code ?? ''
    form.device_type_id = src.device_type_id
    form.brand = src.brand ?? ''
    form.model = src.model ?? ''
    form.serial_number = src.serial_number ?? ''
    form.status = src.status
    form.user_name = src.user_name ?? ''
    form.location = src.location ?? ''
    form.purchase_date = src.purchase_date ?? ''
    form.warranty_until = src.warranty_until ?? ''
    form.notes = src.notes ?? ''
  } else {
    form.asset_code = ''
    form.finance_code = ''
    form.device_type_id = props.presetTypeId ?? props.deviceTypes[0]?.id
    form.brand = ''
    form.model = ''
    form.serial_number = ''
    form.status = 'in_use'
    form.user_name = ''
    form.location = ''
    form.purchase_date = ''
    form.warranty_until = ''
    form.notes = ''
  }
  formRef.value?.clearValidate()
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) resetForm()
  },
)

/** 空字符串统一转 null，交给后端存 NULL */
function toPayload(): AssetPayload {
  const clean = (v: string) => {
    const t = (v ?? '').trim()
    return t ? t : null
  }
  return {
    asset_code: clean(form.asset_code),
    finance_code: clean(form.finance_code),
    device_type_id: form.device_type_id as number,
    brand: clean(form.brand),
    model: clean(form.model),
    serial_number: clean(form.serial_number),
    status: form.status,
    user_name: clean(form.user_name),
    location: clean(form.location),
    purchase_date: form.purchase_date || null,
    warranty_until: form.warranty_until || null,
    notes: clean(form.notes),
    operator: clean(form.operator),
  }
}

async function submit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  saveOperator(form.operator)

  saving.value = true
  try {
    if (isEdit.value && props.asset) {
      const updated = await assetApi.update(props.asset.id, toPayload())
      ElMessage.success('已保存修改')
      emit('saved', updated, 'update')
    } else {
      const created = await assetApi.create(toPayload())
      ElMessage.success(
        form.asset_code.trim() ? '已新增资产' : `已新增资产，编号自动生成为 ${created.asset_code}`,
      )
      emit('saved', created, 'create')
    }
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="min(640px, 94vw)"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <div class="form-grid">
        <el-form-item label="IT 资产编号" prop="asset_code">
          <el-input
            v-model="form.asset_code"
            :placeholder="isEdit ? '' : '留空自动生成，如 IT-2026-0011'"
            clearable
          />
        </el-form-item>

        <el-form-item label="财务编码" prop="finance_code">
          <el-input v-model="form.finance_code" placeholder="财务系统里的编码，可留空" clearable />
        </el-form-item>

        <el-form-item label="设备类型" prop="device_type_id" required>
          <el-select v-model="form.device_type_id" placeholder="请选择" style="width: 100%">
            <el-option v-for="t in deviceTypes" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="状态" prop="status">
          <el-select v-model="form.status" style="width: 100%">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
        </el-form-item>

        <el-form-item label="品牌" prop="brand">
          <el-input v-model="form.brand" placeholder="如 联想 / 戴尔" clearable />
        </el-form-item>

        <el-form-item label="型号" prop="model">
          <el-input v-model="form.model" placeholder="如 ThinkCentre M720t" clearable />
        </el-form-item>

        <el-form-item label="序列号 SN" prop="serial_number">
          <el-input v-model="form.serial_number" placeholder="设备背面/BIOS 里的 SN" clearable />
        </el-form-item>

        <el-form-item label="使用人" prop="user_name">
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

        <el-form-item label="存放位置" prop="location" class="full">
          <el-select
            v-model="form.location"
            placeholder="可直接输入新的位置，如 总部 3F 研发区 A-01"
            filterable
            allow-create
            default-first-option
            clearable
            style="width: 100%"
          >
            <el-option v-for="l in locations" :key="l" :label="l" :value="l" />
          </el-select>
        </el-form-item>

        <el-form-item label="购入日期" prop="purchase_date">
          <el-date-picker
            v-model="form.purchase_date"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="保修到期日" prop="warranty_until">
          <el-date-picker
            v-model="form.warranty_until"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="备注" prop="notes" class="full">
          <el-input
            v-model="form.notes"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="配置、配件、维修记录等，可留空"
          />
        </el-form-item>

        <el-form-item label="经办人" prop="operator" class="full">
          <el-input
            v-model="form.operator"
            placeholder="谁做的这次录入 / 修改，会记进设备的变更历史"
            clearable
          />
        </el-form-item>
      </div>
    </el-form>

    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 8px">
        <el-button @click="close">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">
          {{ isEdit ? '保存' : '创建' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.form-grid .full {
  grid-column: 1 / -1;
}

@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
