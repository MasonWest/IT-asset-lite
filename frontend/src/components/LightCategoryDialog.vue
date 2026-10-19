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

import AppIcon from '@/components/AppIcon.vue'
import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightCategory } from '@/types'
import { hasConfirmedLightIcon, lightCategoryIcon, suggestLightCategoryIcon } from '@/utils/light'
import { LIGHT_CATEGORY_ICON_GROUPS } from '@/utils/icons'
import type { IconName } from '@/utils/icons'

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
  /** null = 没选（保存后库里是 NULL，界面显示建议图标） */
  icon: null as IconName | null,
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
      icon: (c?.icon as IconName | null) ?? null,
    }
  },
)

/** 当前该显示哪个图标：已选 > 建议 > 兜底（与全站同一套分层） */
const previewIcon = computed<IconName>(() => {
  if (form.value.icon) return form.value.icon
  return suggestLightCategoryIcon(form.value.name, form.value.spec)
})

/** 这个预览是"建议值"还是"已选定" —— 只有前者需要提示 */
const isSuggested = computed(() => !form.value.icon)

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
      // 没选就传 null（= 库里留空，界面继续显示建议图标）。
      // **不把建议值当成用户的选择提交** —— 见 utils/light.ts::lightCategoryIcon 的说明。
      icon: form.value.icon,
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
    width="min(600px, 94vw)"
    top="2vh"
    class="light-cat-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form label-width="72px" label-position="top">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" maxlength="80" placeholder="如：罗技 MK270 键鼠套装" />
      </el-form-item>
      <div class="grid-3">
        <el-form-item label="品牌">
          <el-input v-model="form.brand" maxlength="64" placeholder="选填，如：罗技" />
        </el-form-item>
        <el-form-item label="规格">
          <el-input v-model="form.spec" maxlength="128" placeholder="选填，如：2.4G 无线" />
        </el-form-item>
        <el-form-item label="计量单位">
          <el-input v-model="form.unit" maxlength="16" placeholder="套 / 个 / 块" />
        </el-form-item>
      </div>

      <!--
        图标：按用途分组的选择器。
        「建议」标记只在**没选过**时出现 —— 它与设备类型弹窗同一套口径：
        推断值只用于显示，不写库；想落成配置得用户自己点一下。
      -->
      <el-form-item>
        <template #label>
          图标
          <span v-if="isSuggested" class="sug">建议值 · 未确认</span>
        </template>
        <div class="icon-pick">
          <div class="cur" :class="{ suggested: isSuggested }">
            <AppIcon :name="previewIcon" :size="20" />
          </div>
          <div class="groups">
            <div v-for="g in LIGHT_CATEGORY_ICON_GROUPS" :key="g.label" class="grow">
              <span class="gl">{{ g.label }}</span>
              <button
                v-for="i in g.icons"
                :key="i.name"
                type="button"
                class="pi"
                :class="{ on: form.icon === i.name }"
                :title="i.label"
                @click="form.icon = form.icon === i.name ? null : i.name"
              >
                <AppIcon :name="i.name" :size="16" />
              </button>
            </div>
          </div>
        </div>
        <div class="pick-hint">
          不选也能保存 —— 系统会按品类名给一个建议图标（仅显示，不算已确认）。
          再点一次已选中的图标可以取消选择。
        </div>
      </el-form-item>
      <div class="tip-line">
        统一按「品类 + 数量」管理：记"还有几套、装在哪台机器上"就够了，单位不参与计算。
      </div>
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
.grid-3 {
  display: grid;
  grid-template-columns: 1fr 1fr 0.8fr;
  gap: 0 12px;
}

/* ---- 图标选择器 ---- */
.sug {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  background: #fff4e6;
  color: #a15c00;
  font-size: 11px;
  font-weight: 400;
}

.icon-pick {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  width: 100%;
}

/* 预览框只跟第一行（键鼠）对齐就够了 —— 它是"当前选中"的示意，不是整列占位 */

.icon-pick .cur {
  width: 44px;
  height: 44px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: #f4f6fa;
  color: #1f5fd0;
}

/* 建议值：虚线边框，一眼看出"这不是你定的" */
.icon-pick .cur.suggested {
  border-style: dashed;
  border-color: #d0b06a;
  background: #fffdf5;
  color: #a16207;
}

.icon-pick .groups {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.icon-pick .grow {
  display: flex;
  align-items: center;
  gap: 4px;
}

.icon-pick .gl {
  width: 64px;
  white-space: nowrap;
  flex: none;
  font-size: 11px;
  color: var(--text-3);
}

.icon-pick .pi {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 7px;
  background: #f4f6fa;
  color: #4b5563;
  cursor: pointer;
}

.icon-pick .pi:hover {
  border-color: #1f5fd0;
  color: #1f5fd0;
}

.icon-pick .pi.on {
  border-color: #1f5fd0;
  background: #eaf2ff;
  color: #1f5fd0;
}

/* 窄屏 / 小窗口兜底：内容区自己滚，别把页脚顶出视口 */
.light-cat-dialog :deep(.el-dialog__body) {
  max-height: calc(100vh - 150px);
  overflow-y: auto;
}

.tip-line {
  margin: 0 0 12px;
  padding: 7px 10px;
  border-radius: 7px;
  background: #f8fafc;
  border: 1px solid #e8edf3;
  font-size: 12px;
  line-height: 1.6;
  color: #64748b;
}

.pick-hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-3);
  line-height: 1.6;
}

@media (max-width: 640px) {
  .grid-3 {
    grid-template-columns: 1fr;
  }
}
</style>
