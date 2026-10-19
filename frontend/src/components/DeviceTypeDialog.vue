<script setup lang="ts">
/**
 * 设备类型管理。
 *
 * 除了增删改名，这里还要能设两样东西：
 * - **类别**（主机类 / 显示类 / 其他）—— 决定能不能参与主机 / 显示器配对；
 * - **图标** —— 台账、详情、配对、地图、标签打印各处都用它。
 *
 * 两者都**不按名字自动判断**：用户完全可以把「主机」改名成「工控机」，
 * 靠名字猜的逻辑一改名就废。名字只用来给一个**建议值**（见 `suggestTypeIcon`）。
 *
 * ## 图标的三态（这是本弹窗最需要说清的一件事）
 *
 * | 状态 | 库里的值 | 显示 | 本弹窗的行为 |
 * | --- | --- | --- | --- |
 * | 已确认 | 图标标识 | 该图标 | 正常显示，可改 |
 * | 未确认（可推断） | NULL | 推断出的建议图标 | 打「未确认」标记，提示可套用 |
 * | 未确认（猜不出） | NULL | 通用盒子 | 同上 |
 *
 * ⚠️ **推断结果只用于显示，绝不自动写库** —— 那等于替用户做了他没做过的决定。
 * 想批量落成配置，得用户自己点「套用建议图标」。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import { apiMessage, deviceTypeApi } from '@/api'
import { appState } from '@/stores/app'
import type { DeviceCategory, DeviceType } from '@/types'
import { categoryMeta, hasConfirmedIcon, suggestTypeIcon, typeIcon } from '@/utils/format'
import { DEVICE_TYPE_ICON_GROUPS } from '@/utils/icons'
import type { IconName } from '@/utils/icons'

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
const newIcon = ref<IconName | null>(null)
const adding = ref(false)
const editingId = ref<number | null>(null)
const editingName = ref('')
const busy = ref(false)
/** 正在展开图标选择器的那个类型（null = 都不展开） */
const pickerId = ref<number | null>(null)
/** 正在展开图标选择器的新增行 */
const newPickerOpen = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      newName.value = ''
      newCategory.value = 'other'
      newIcon.value = null
      editingId.value = null
      pickerId.value = null
      newPickerOpen.value = false
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

/**
 * 名字变了，顺手把"还没选过图标"的默认值也跟着变。
 *
 * ⚠️ 只在用户**没手动选过**时跟着走（`newIcon` 为空）。用户一旦自己挑了图标，
 * 再改名字就不该覆盖他的选择。
 */
function onNewNameChange() {
  const n = newName.value.trim()
  newCategory.value = guessCategory(n)
  if (!newIcon.value) newIcon.value = suggestTypeIcon(n)
}

/** 未确认的类型有多少个（决定要不要显示「套用建议图标」按钮） */
const unconfirmed = computed(() => props.deviceTypes.filter((t) => !hasConfirmedIcon(t)))

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
        icon: newIcon.value,
      },
      appState.operator,
    )
    newName.value = ''
    newCategory.value = 'other'
    newIcon.value = null
    newPickerOpen.value = false
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

/** 选一个图标并立刻保存（与「类别」下拉同一套交互：改了就落库） */
async function pickIcon(t: DeviceType, icon: IconName) {
  pickerId.value = null
  if (t.icon === icon) return
  busy.value = true
  try {
    await deviceTypeApi.update(t.id, { icon }, appState.operator)
    ElMessage.success(`「${t.name}」的图标已更新`)
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    busy.value = false
  }
}

/** 清掉已确认的图标 —— 回到"显示建议图标"的状态（不删类型本身） */
async function clearIcon(t: DeviceType) {
  pickerId.value = null
  busy.value = true
  try {
    await deviceTypeApi.update(t.id, { icon: null }, appState.operator)
    ElMessage.success(`「${t.name}」的图标已清除，暂时显示建议图标`)
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    busy.value = false
  }
}

/**
 * 把"建议图标"批量落成配置。
 *
 * ⚠️ 这是**唯一**会把推断结果写进库的入口，而且必须用户主动点 ——
 * 不做启动回填、不在保存别的字段时顺带写入，理由见 `utils/format.ts::typeIcon`。
 */
async function applySuggestions() {
  const targets = unconfirmed.value
  if (!targets.length) return
  try {
    await ElMessageBox.confirm(
      `为 ${targets.length} 个还没确认图标的类型套用建议图标？` +
        '建议是按名称推断的，套用后会成为正式配置（之后可以逐个改）。',
      '套用建议图标',
      { type: 'info', confirmButtonText: '套用', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busy.value = true
  let ok = 0
  let failed = 0
  for (const t of targets) {
    try {
      await deviceTypeApi.update(t.id, { icon: typeIcon(t) }, appState.operator)
      ok += 1
    } catch {
      failed += 1
    }
  }
  busy.value = false
  if (failed) ElMessage.warning(`已套用 ${ok} 个，${failed} 个失败`)
  else ElMessage.success(`已为 ${ok} 个类型套用建议图标`)
  emit('changed')
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
    width="min(760px, 94vw)"
    top="4vh"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
      <template #title>「类别」决定设备能不能参与配对；「图标」决定它在各页面长什么样</template>
      <div style="font-size: 12.5px; line-height: 1.8; margin-top: 2px">
        主机类 = 可以挂显示器；显示类 = 可以被挂到主机上；其他 = 键盘鼠标打印机这类，不参与配对。
        改成显示类之后，它就会出现在「配对管理」里。<br />
        图标在这台设备的**所有页面**通用（台账、详情、配对、空间地图、标签打印）。
        标着「未确认」的是按名称给的建议值，点它可以换成别的，也可以一键套用。
      </div>
    </el-alert>

    <!-- 新增行：名称 + 类别 + 图标 + 添加 -->
    <div class="add-row">
      <el-input
        v-model="newName"
        placeholder="新增类型，如 服务器、交换机、平板"
        maxlength="20"
        style="flex: 1"
        @change="onNewNameChange"
        @keyup.enter="addType"
      />
      <el-select v-model="newIcon" style="width: 148px" clearable placeholder="图标">
        <!-- 选中后在下拉框里直接显示图标 + 名称，而不是只有文字 -->
        <template #prefix>
          <AppIcon v-if="newIcon" :name="newIcon" :size="15" />
        </template>
        <el-option
          v-for="i in DEVICE_TYPE_ICON_GROUPS.flatMap((g) => g.icons)"
          :key="i.name"
          :label="i.label"
          :value="i.name"
        >
          <span class="opt-icon">
            <AppIcon :name="i.name" :size="16" />
            <span>{{ i.label }}</span>
          </span>
        </el-option>
      </el-select>
      <el-select v-model="newCategory" style="width: 118px">
        <el-option v-for="c in CATEGORIES" :key="c.value" :label="c.label" :value="c.value" />
      </el-select>
      <el-button type="primary" :loading="adding" @click="addType">添加</el-button>
    </div>

    <div v-if="unconfirmed.length" class="suggest-bar">
      <span>
        有 <b>{{ unconfirmed.length }}</b> 个类型还没确认图标，现在显示的是按名称推断的建议值。
      </span>
      <el-button link type="primary" :disabled="busy" @click="applySuggestions">
        套用建议图标
      </el-button>
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
          <!--
            图标按钮：点开选择器。
            「未确认」小标记只在这里出现 —— 列表 / 详情 / 地图那些日常页面不显示，
            免得把一个技术状态泄漏成用户可见的噪音（展示上建议图标与已确认图标长一样）。
          -->
          <el-popover
            :visible="pickerId === t.id"
            placement="bottom-start"
            :width="300"
            trigger="click"
            @hide="pickerId = null"
          >
            <template #reference>
              <button
                class="icon-btn"
                :class="{ unconfirmed: !hasConfirmedIcon(t) }"
                :title="hasConfirmedIcon(t) ? '点击更换图标' : '当前是按名称推断的建议图标，点击确认或更换'"
                :disabled="busy"
                @click="pickerId = pickerId === t.id ? null : t.id"
              >
                <AppIcon :name="typeIcon(t)" :size="18" />
              </button>
            </template>
            <div class="picker">
              <div v-for="g in DEVICE_TYPE_ICON_GROUPS" :key="g.label" class="pg">
                <div class="pgl">{{ g.label }}</div>
                <div class="pgr">
                  <button
                    v-for="i in g.icons"
                    :key="i.name"
                    class="pi"
                    :class="{ on: t.icon === i.name }"
                    :title="i.label"
                    @click="pickIcon(t, i.name)"
                  >
                    <AppIcon :name="i.name" :size="17" />
                  </button>
                </div>
              </div>
              <div class="pfoot">
                <span v-if="!hasConfirmedIcon(t)" class="tip">当前是建议图标</span>
                <el-button
                  v-if="hasConfirmedIcon(t)"
                  link
                  size="small"
                  @click="clearIcon(t)"
                >
                  清除图标
                </el-button>
              </div>
            </div>
          </el-popover>

          <span class="tname">
            {{ t.name }}
            <span v-if="!hasConfirmedIcon(t)" class="badge">未确认</span>
          </span>
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
  /* 图标改造后弹窗多了一行提示条 + 每行多了图标按钮，原来是 44vh 会把弹窗顶出视口。
     这里改成"跟着视口走"，并留出标题、说明、新增行、提示条与页脚的高度。 */
  max-height: min(38vh, 320px);
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

/* ---- 图标按钮与选择器 ---- */
.icon-btn {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: #f4f6fa;
  color: var(--primary, #1f5fd0);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.icon-btn:hover {
  border-color: var(--primary, #1f5fd0);
  background: #eaf2ff;
}

/* 未确认：虚线边框 + 柔和底色 —— 一眼看出"这是建议值，不是定稿" */
.icon-btn.unconfirmed {
  border-style: dashed;
  border-color: #d0b06a;
  background: #fffdf5;
  color: #a16207;
}

.badge {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  background: #fff4e6;
  color: #a15c00;
  font-size: 11px;
  line-height: 16px;
  vertical-align: 1px;
}

.suggest-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fffdf5;
  border: 1px solid #fde68a;
  font-size: 12.5px;
  color: #7c5800;
}

.picker .pg {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 7px;
}

.picker .pgl {
  width: 46px;
  flex-shrink: 0;
  padding-top: 5px;
  font-size: 11px;
  color: var(--text-3);
}

.picker .pgr {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.picker .pi {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: 7px;
  background: #f4f6fa;
  color: #4b5563;
  cursor: pointer;
}

.picker .pi:hover {
  border-color: var(--primary, #1f5fd0);
  color: var(--primary, #1f5fd0);
}

.picker .pi.on {
  border-color: var(--primary, #1f5fd0);
  background: #eaf2ff;
  color: var(--primary, #1f5fd0);
}

.picker .pfoot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--border);
  min-height: 20px;
}

.picker .pfoot .tip {
  font-size: 11.5px;
  color: #a16207;
}

.opt-icon {
  display: inline-flex;
  align-items: center;
  gap: 7px;
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
