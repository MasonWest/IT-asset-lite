<script setup lang="ts">
/**
 * 设备类型管理。
 *
 * 除了增删改名，这里还要能设「类别」—— 主机类 / 显示类 / 其他。
 * 配对功能全靠这个类别判断哪台设备能当主机、哪台能当显示器，
 * 而不是靠猜类型名字（用户完全可以把「主机」改名成「工控机」）。
 */
import { ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, deviceTypeApi } from '@/api'
import { appState } from '@/stores/app'
import type { DeviceCategory, DeviceType } from '@/types'
import { categoryMeta } from '@/utils/format'

const props = defineProps<{
  modelValue: boolean
  deviceTypes: DeviceType[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'changed'): void
}>()

const CATEGORIES: { value: DeviceCategory; label: string; hint: string }[] = [
  { value: 'host', label: '主机类', hint: '可以挂显示器（主机 / 台式机 / 笔记本 / 服务器）' },
  { value: 'display', label: '显示类', hint: '可以被挂到主机上（显示器 / 屏幕）' },
  { value: 'other', label: '其他', hint: '键盘鼠标打印机这类，不参与配对' },
]

const newName = ref('')
const newCategory = ref<DeviceCategory>('other')
const adding = ref(false)
const editingId = ref<number | null>(null)
const editingName = ref('')
const busy = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      newName.value = ''
      newCategory.value = 'other'
      editingId.value = null
    }
  },
)

/** 新建时按名字给个默认类别，猜不中就是「其他」，用户随时能改 */
function guessCategory(name: string): DeviceCategory {
  const n = name.trim()
  if (/显示器|屏幕/.test(n)) return 'display'
  if (/主机|台式|笔记本|服务器|工作站/.test(n)) return 'host'
  return 'other'
}

function onNewNameChange() {
  newCategory.value = guessCategory(newName.value)
}

async function addType() {
  const name = newName.value.trim()
  if (!name) {
    ElMessage.warning('请输入设备类型名称')
    return
  }
  adding.value = true
  try {
    await deviceTypeApi.create(
      {
        name,
        sort_order: (props.deviceTypes.at(-1)?.sort_order ?? 0) + 10,
        category: newCategory.value,
      },
      appState.operator,
    )
    newName.value = ''
    newCategory.value = 'other'
    ElMessage.success(`已新增设备类型「${name}」`)
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    adding.value = false
  }
}

function startEdit(t: DeviceType) {
  editingId.value = t.id
  editingName.value = t.name
}

async function commitEdit(t: DeviceType) {
  const name = editingName.value.trim()
  if (!name || name === t.name) {
    editingId.value = null
    return
  }
  busy.value = true
  try {
    await deviceTypeApi.update(t.id, { name }, appState.operator)
    editingId.value = null
    ElMessage.success('已修改')
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    busy.value = false
  }
}

async function changeCategory(t: DeviceType, category: DeviceCategory) {
  if (category === t.category) return
  busy.value = true
  try {
    await deviceTypeApi.update(t.id, { category }, appState.operator)
    ElMessage.success(`「${t.name}」已设为${categoryMeta(category).label}`)
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    busy.value = false
  }
}

async function removeType(t: DeviceType) {
  if (t.asset_count > 0) {
    ElMessage.warning(`「${t.name}」下还有 ${t.asset_count} 台设备，先把这些设备改成别的类型`)
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除设备类型「${t.name}」？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deviceTypeApi.remove(t.id, appState.operator)
    ElMessage.success('已删除')
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="设备类型与类别"
    width="min(620px, 94vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
      <template #title>「类别」决定设备能不能参与主机 / 显示器配对</template>
      <div style="font-size: 12.5px; line-height: 1.8; margin-top: 2px">
        主机类 = 可以挂显示器；显示类 = 可以被挂到主机上；其他 = 键盘鼠标打印机这类，不参与配对。
        改成显示类之后，它就会出现在「配对管理」里。
      </div>
    </el-alert>

    <div class="add-row">
      <el-input
        v-model="newName"
        placeholder="新增类型，如 服务器、交换机、平板"
        maxlength="20"
        style="flex: 1"
        @change="onNewNameChange"
        @keyup.enter="addType"
      />
      <el-select v-model="newCategory" style="width: 130px">
        <el-option v-for="c in CATEGORIES" :key="c.value" :label="c.label" :value="c.value" />
      </el-select>
      <el-button type="primary" :loading="adding" @click="addType">添加</el-button>
    </div>

    <div class="type-list">
      <div v-for="t in deviceTypes" :key="t.id" class="type-row">
        <template v-if="editingId === t.id">
          <el-input
            v-model="editingName"
            size="small"
            maxlength="20"
            style="flex: 1"
            @keyup.enter="commitEdit(t)"
            @blur="editingId = null"
          />
          <el-button size="small" type="primary" @click="commitEdit(t)">保存</el-button>
        </template>
        <template v-else>
          <span class="tname">{{ t.name }}</span>
          <span class="tcount">{{ t.asset_count }} 台</span>
          <el-select
            :model-value="t.category"
            size="small"
            class="cat-select"
            :disabled="busy"
            @change="(v: DeviceCategory) => changeCategory(t, v)"
          >
            <el-option v-for="c in CATEGORIES" :key="c.value" :label="c.label" :value="c.value">
              <span class="opt">
                <b>{{ c.label }}</b>
                <small>{{ c.hint }}</small>
              </span>
            </el-option>
          </el-select>
          <el-button link size="small" @click="startEdit(t)">重命名</el-button>
          <el-button link size="small" @click="removeType(t)">删除</el-button>
        </template>
      </div>
      <div v-if="!deviceTypes.length" class="label-empty">还没有设备类型</div>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.add-row {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.type-list {
  max-height: 44vh;
  overflow-y: auto;
}

.type-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 4px;
  border-bottom: 1px dashed var(--border);
}

.type-row .tname {
  flex: 1;
  font-size: 14px;
  min-width: 0;
}

.type-row .tcount {
  font-size: 12px;
  color: var(--text-3);
  margin-right: 4px;
  white-space: nowrap;
}

.cat-select {
  width: 104px;
  flex-shrink: 0;
}

.opt {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  padding: 2px 0;
}

.opt small {
  color: var(--text-3);
  font-size: 11px;
}

@media (max-width: 640px) {
  .add-row {
    flex-wrap: wrap;
  }

  .type-row {
    flex-wrap: wrap;
  }
}
</style>
