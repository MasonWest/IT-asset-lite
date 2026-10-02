<!--
  平面图编辑器。

  ## 为什么是独立页而不是 /map 上开个模式
  `/map` 在手机端是满铺布局，顶部固定区一叠加就只剩很小的绘图区；编辑器要三栏
  （图元列表 / 画布 / 属性面板）+ 工具条，塞不下。独立页也让"编辑"与"看地图"
  这两件事的心智分开 —— 用户不会在只想看看地图时误触到改坐标。

  ## 与 /map 的关系
  共用同一套坐标语义（960×1320，网格 20）与同一个 `floor_maps.layout` 文档。
  但**不复用 `.map-page` 的一整套样式** —— 那是只读地图的视觉，编辑器有自己
  的三栏布局需求。唯一共享的是 `utils/floorMap.ts` 的解析与常量。
-->
<template>
  <div class="editor-page">
    <!-- ── 顶部：返回 + 图名 + 撤销/重做 + 保存 ── -->
    <div class="ed-topbar">
      <button class="ed-btn ghost" @click="goBack">
        <span class="ic">←</span> 返回空间地图
      </button>

      <div class="ed-title">
        <input
          v-model="mapName"
          class="name-input"
          maxlength="128"
          placeholder="平面图名称"
          @change="markDirtyByRename"
        />
        <span v-if="hasUnsaved" class="dirty-dot" title="有未保存的修改">未保存</span>
      </div>

      <div class="ed-spacer" />

      <button class="ed-btn ghost" :disabled="!editor.canUndo.value" title="撤销 (Ctrl+Z)" @click="editor.undo()">
        <span class="ic">↶</span> 撤销
      </button>
      <button class="ed-btn ghost" :disabled="!editor.canRedo.value" title="重做 (Ctrl+Shift+Z)" @click="editor.redo()">
        <span class="ic">↷</span> 重做
      </button>
      <button class="ed-btn primary" :disabled="saving || !hasUnsaved" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </div>

    <div class="ed-body">
      <!-- ── 左栏：新增图元 + 批量生成点位 + 图元列表 ── -->
      <aside class="ed-pane left">
        <div class="pane-head">新增图元</div>
        <div class="shape-palette">
          <button
            v-for="kind in paletteKinds"
            :key="kind"
            class="palette-btn"
            :title="`在画布中央放一个「${SHAPE_KIND_LABEL[kind]}」`"
            @click="addShape(kind)"
          >
            {{ SHAPE_KIND_LABEL[kind] }}
          </button>
        </div>

        <!--
          ⚠️ 「批量生成点位」曾经在这里 —— 已挪到地图页的「调整位置」模式。
          理由（用户提出，说得对）：**底图是底图，点位是点位。**
          （术语注：用户原话是「底图是底图，工位是工位」；v2.1.0 起「工位」在界面与文档里统一叫「点位」，
           见 CURRENT_STATE.md 术语表。）
           - 直觉上，人不会想到"要加一片点位，先去编辑底图"；
           - 更要命的是这一栏与「新增图元」并排、长得一样，但语义**相反** ——
             图元进草稿（不保存就白搭），点位立刻写库。真实事故就是这么来的。
          正确的位置是**点位自己的家**：地图页的「调整位置」，那里本来就有
          单个新增 / 删除 / 拖动。职责单一，界面上不再混放两种写库语义。
        -->

        <div class="pane-head">
          图元（{{ editor.layout.value.shapes.length }}）
          <span class="hint">点选后可在右侧改属性，Delete 删除</span>
        </div>
        <ul class="shape-list">
          <li
            v-for="shape in editor.layout.value.shapes"
            :key="shape.id"
            class="shape-row"
            :class="{ active: selectedId === shape.id }"
            @click="selectShape(shape.id)"
          >
            <span class="kind-tag">{{ SHAPE_KIND_LABEL[shape.kind] || shape.kind }}</span>
            <span class="shape-name">{{ shapeLabel(shape) }}</span>
            <span class="shape-pos">{{ shape.x }},{{ shape.y }}</span>
          </li>
        </ul>
      </aside>

      <!-- ── 中栏：画布 ── -->
      <div
        ref="viewportRef"
        class="ed-viewport"
        :class="{ 'is-panning': panning }"
        @pointerdown="onCanvasDown"
        @pointermove="onCanvasMove"
        @pointerup="onCanvasUp"
        @pointercancel="onCanvasUp"
        @wheel.prevent="onWheel"
      >
        <div
          class="ed-canvas"
          :style="{ transform: `translate(${tx}px, ${ty}px) scale(${scale})` }"
        >
          <div class="ed-plate" :style="{ width: `${canvas.width}px`, height: `${canvas.height}px` }" />
          <div
            class="ed-grid"
            :style="{
              width: `${canvas.width}px`,
              height: `${canvas.height}px`,
              backgroundSize: `${canvas.grid}px ${canvas.grid}px`,
            }"
          />

          <!-- 图元。与 /map 渲染同一个 layout 文档，class 也照搬（视觉一致） -->
          <template v-for="shape in editor.layout.value.shapes" :key="shape.id">
            <div
              v-if="shape.kind === 'corrtext'"
              class="corr-t"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px` }"
            >
              {{ shape.text }}
            </div>

            <div
              v-else-if="shape.kind === 'wall'"
              class="wall-v"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px`, height: `${shape.h ?? 0}px` }"
            />

            <div
              v-else-if="shape.kind === 'divider'"
              class="divider-h"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px`, width: `${shape.w ?? 0}px` }"
            />

            <div
              v-else-if="shape.kind === 'entry'"
              class="entry"
              :style="{ left: `${shape.x}px`, top: `${shape.y}px` }"
            >
              <svg viewBox="0 0 64 38" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="M3 36V8a4 4 0 0 1 4-4h19v32" />
                <path d="M61 36V8a4 4 0 0 0-4-4H38v32" />
              </svg>
              <span>大楼入口</span>
            </div>

            <div
              v-else-if="shape.kind === 'room'"
              class="room"
              :class="[shape.room || 'meet', { picked: selectedId === shape.id }]"
              :style="shapeBox(shape)"
              @pointerdown.stop="onShapeDown($event, shape)"
            >
              <div class="rname">
                <i :style="{ background: ROOM_COLORS[shape.room || 'meet'] || ROOM_COLORS.meet }" />{{ shape.name }}
              </div>
              <div v-if="shape.sub" class="rsub">{{ shape.sub }}</div>
              <!-- 选中时给四个角加手柄（纯视觉，缩放用属性面板的数字） -->
              <template v-if="selectedId === shape.id">
                <span class="handle tl" /><span class="handle tr" />
                <span class="handle bl" /><span class="handle br" />
              </template>
            </div>

            <div
              v-else
              :class="[SHAPE_KIND_CLASS[shape.kind] || shape.kind, { picked: selectedId === shape.id }]"
              :style="shapeBox(shape)"
              @pointerdown.stop="onShapeDown($event, shape)"
            />
          </template>
        </div>

        <!-- 画布角标：缩放 + 提示 -->
        <div class="ed-hud">
          <span>缩放 {{ Math.round(scale * 100) }}%</span>
          <span class="sep">·</span>
          <span>拖动空白平移，拖动图元移动</span>
          <button class="hud-btn" @click="fitCanvas">适应窗口</button>
        </div>
      </div>

      <!-- ── 右栏：属性 ── -->
      <aside class="ed-pane right">
        <div class="pane-head">属性</div>
        <div v-if="!selectedShape" class="empty-hint">在画布或左侧列表里选一个图元</div>
        <div v-else class="prop-form">
          <div class="prop-row">
            <label>类型</label>
            <span class="ro">{{ SHAPE_KIND_LABEL[selectedShape.kind] || selectedShape.kind }}</span>
          </div>
          <div class="prop-row">
            <label>ID</label>
            <span class="ro mono">{{ selectedShape.id }}</span>
          </div>

          <div class="prop-grid">
            <div class="prop-row"><label>X</label><input v-model.number="editX" type="number" step="10" @change="applyGeometry" /></div>
            <div class="prop-row"><label>Y</label><input v-model.number="editY" type="number" step="10" @change="applyGeometry" /></div>
          </div>

          <div v-if="hasSize" class="prop-grid">
            <div class="prop-row"><label>宽</label><input v-model.number="editW" type="number" step="10" @change="applyGeometry" /></div>
            <div class="prop-row"><label>高</label><input v-model.number="editH" type="number" step="10" @change="applyGeometry" /></div>
          </div>

          <template v-if="selectedShape.kind === 'room'">
            <div class="prop-row"><label>名称</label><input v-model="editName" @change="applyText" /></div>
            <div class="prop-row"><label>副标题</label><input v-model="editSub" @change="applyText" /></div>
            <div class="prop-row">
              <label>配色</label>
              <select v-model="editRoom" @change="applyText">
                <option v-for="(_, key) in ROOM_COLORS" :key="key" :value="key">{{ key }}</option>
              </select>
            </div>
          </template>

          <div v-if="selectedShape.kind === 'corrtext'" class="prop-row">
            <label>文字</label><input v-model="editText" @change="applyText" />
          </div>

          <button class="ed-btn danger full" @click="deleteSelected">删除该图元</button>
          <p class="del-hint">删除不弹确认 —— 它是可撤销的，按 Ctrl+Z 就能回来。</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, floorMapApi } from '@/api'
import { appState } from '@/stores/app'
import {
  ROOM_COLORS,
  SHAPE_KIND_CLASS,
  SHAPE_KIND_LABEL,
  parseLayout,
  type FloorShape,
  type ShapeKind,
} from '@/utils/floorMap'
import { useFloorEditor } from '@/composables/useFloorEditor'
import { clampToCanvas } from '@/utils/workstationMap'

const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const floorId = ref<number | null>(null)
/** 乐观锁版本：打开页面时那次 GET 拿到的 updated_at */
const serverVersion = ref<string | null>(null)

const mapName = ref('')
const canvas = ref({ width: 960, height: 1320, grid: 20 })

/**
 * 编辑器**只管底图**，不碰点位 —— 点位的增删在「地图页 /map」的
 * 「调整位置」模式里（那里才是点位的家）。
 *
 * 曾经这里也有一份点位列表 + 批量生成面板，已移除：
 * 「底图是底图，点位是点位」，把它们混在一页会让用户以为
 * "加一片点位"要先来编辑底图（真实事故），而且两者写库语义相反。
 */

// ── 画布视图变换（与 /map 同一套语义：translate + scale，原点在左上）─────
const viewportRef = ref<HTMLElement | null>(null)
const scale = ref(0.7)
const tx = ref(0)
const ty = ref(0)
const panning = ref(false)

const editor = useFloorEditor({ canvas })

/** 图名不进 layout 文档，所以单独跟踪是否改过名字。 */
const serverName = ref('')
const nameDirty = computed(() => mapName.value.trim() !== serverName.value)
const hasUnsaved = computed(() => editor.dirty.value || nameDirty.value)

const selectedId = ref<string | null>(null)
const selectedShape = computed(
  () => editor.layout.value.shapes.find((s) => s.id === selectedId.value) ?? null,
)

/** 可新增的图元种类。`corrtext` 与 `entry` 属于"整图唯一"的固定件，不给新增入口。 */
const paletteKinds: ShapeKind[] = ['zone', 'corr', 'room', 'wall', 'divider', 'lobby']

const hasSize = computed(() => selectedShape.value?.kind !== 'corrtext' && selectedShape.value?.kind !== 'entry')

// 属性面板的本地副本（改完 change 才写回文档，避免每敲一个字符进一次撤销栈）
const editX = ref(0)
const editY = ref(0)
const editW = ref(0)
const editH = ref(0)
const editName = ref('')
const editSub = ref('')
const editText = ref('')
const editRoom = ref('meet')

watch(selectedShape, (shape) => {
  if (!shape) return
  editX.value = shape.x
  editY.value = shape.y
  editW.value = shape.w ?? 0
  editH.value = shape.h ?? 0
  editName.value = shape.name ?? ''
  editSub.value = shape.sub ?? ''
  editText.value = shape.text ?? ''
  editRoom.value = shape.room ?? 'meet'
})

// ── 载入 ────────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  try {
    const floor = await floorMapApi.default()
    floorId.value = floor.id
    serverVersion.value = floor.updated_at
    mapName.value = floor.name
    serverName.value = floor.name
    canvas.value = { width: floor.canvas.width, height: floor.canvas.height, grid: floor.canvas.grid }
    editor.load(parseLayout(floor.layout))
    selectedId.value = null
    await nextTick()
    fitCanvas()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    loading.value = false
  }
}

// ── 画布视图 ────────────────────────────────────────────────────────────
function fitCanvas() {
  const el = viewportRef.value
  if (!el) return
  const pad = 48
  const availableW = el.clientWidth - pad
  const availableH = el.clientHeight - pad
  scale.value = Math.min(1, Math.min(availableW / canvas.value.width, availableH / canvas.value.height))
  tx.value = (el.clientWidth - canvas.value.width * scale.value) / 2
  ty.value = (el.clientHeight - canvas.value.height * scale.value) / 2
}

function onWheel(event: WheelEvent) {
  const el = viewportRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  // 锚点换算成画布坐标再定基准 —— 拿屏幕坐标算会变成"以画布原点缩放"
  const cx = (px - tx.value) / scale.value
  const cy = (py - ty.value) / scale.value
  const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1
  const next = Math.max(0.2, Math.min(2, scale.value * factor))
  scale.value = next
  tx.value = px - cx * next
  ty.value = py - cy * next
}

function shapeBox(shape: FloorShape): Record<string, string> {
  const style: Record<string, string> = { left: `${shape.x}px`, top: `${shape.y}px` }
  if (shape.w !== undefined) style.width = `${shape.w}px`
  if (shape.h !== undefined) style.height = `${shape.h}px`
  return style
}

function shapeLabel(shape: FloorShape): string {
  if (shape.kind === 'room') return shape.name || '未命名房间'
  if (shape.kind === 'corrtext') return shape.text || '走道文字'
  return SHAPE_KIND_LABEL[shape.kind] || shape.kind
}

function selectShape(id: string) {
  selectedId.value = id
}

// ── 拖拽图元 / 平移画布 ─────────────────────────────────────────────────
interface DragState {
  kind: 'shape' | 'pan'
  shapeId?: string
  startX: number
  startY: number
  originX: number
  originY: number
  before?: ReturnType<typeof editor.payload>
  moved: boolean
}
let drag: DragState | null = null

function canvasPoint(event: PointerEvent) {
  const el = viewportRef.value
  if (!el) return { x: 0, y: 0 }
  const rect = el.getBoundingClientRect()
  return {
    x: (event.clientX - rect.left - tx.value) / scale.value,
    y: (event.clientY - rect.top - ty.value) / scale.value,
  }
}

function onShapeDown(event: PointerEvent, shape: FloorShape) {
  if (event.button !== 0) return
  selectedId.value = shape.id
  const point = canvasPoint(event)
  drag = {
    kind: 'shape',
    shapeId: shape.id,
    startX: point.x,
    startY: point.y,
    originX: shape.x,
    originY: shape.y,
    // 拖拽开始时的快照：松手时才补进撤销栈（中间帧不进，否则栈被灌满）
    before: editor.payload(),
    moved: false,
  }
  ;(event.target as HTMLElement).setPointerCapture?.(event.pointerId)
}

function onCanvasDown(event: PointerEvent) {
  if (event.button !== 0) return
  const el = viewportRef.value
  if (!el) return
  drag = {
    kind: 'pan',
    startX: event.clientX,
    startY: event.clientY,
    originX: tx.value,
    originY: ty.value,
    moved: false,
  }
  panning.value = true
  el.setPointerCapture(event.pointerId)
}

function onCanvasMove(event: PointerEvent) {
  if (!drag) return
  if (drag.kind === 'pan') {
    tx.value = drag.originX + (event.clientX - drag.startX)
    ty.value = drag.originY + (event.clientY - drag.startY)
    drag.moved = true
    return
  }

  const point = canvasPoint(event)
  const dx = point.x - drag.startX
  const dy = point.y - drag.startY
  if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) drag.moved = true

  const id = drag.shapeId!
  const originX = drag.originX
  const originY = drag.originY
  const shape = editor.layout.value.shapes.find((s) => s.id === id)
  if (!shape) return
  // 吸附到网格 + 收进画布（复用只读地图那套纯函数）
  const clamped = clampToCanvas(originX + dx, originY + dy, canvas.value)
  editor.mutateWithoutHistory((draft) => {
    const target = draft.shapes.find((s) => s.id === id)
    if (!target) return
    target.x = Math.round(clamped.x / canvas.value.grid) * canvas.value.grid
    target.y = Math.round(clamped.y / canvas.value.grid) * canvas.value.grid
  })
  // 属性面板跟着动
  editX.value = shape.x
  editY.value = shape.y
}

function onCanvasUp() {
  if (!drag) return
  if (drag.kind === 'shape' && drag.moved && drag.before) {
    // 松手补一步历史 —— 整个拖拽算一次撤销
    editor.commitSnapshot(drag.before)
  }
  panning.value = false
  drag = null
}

// ── 图元操作 ────────────────────────────────────────────────────────────
function addShape(kind: ShapeKind) {
  const id = editor.nextShapeId(kind)
  // 放在画布中央（粗略），用户再拖
  const cx = Math.round(canvas.value.width / 2 / canvas.value.grid) * canvas.value.grid
  const cy = Math.round(canvas.value.height / 2 / canvas.value.grid) * canvas.value.grid
  const base: FloorShape = { id, kind, x: cx - 100, y: cy - 60 }
  if (kind === 'room') {
    Object.assign(base, { w: 200, h: 120, room: 'meet', name: '新房间' })
  } else if (kind === 'wall') {
    base.h = 120
  } else if (kind === 'divider') {
    base.w = 200
  } else if (kind === 'zone' || kind === 'corr' || kind === 'lobby') {
    Object.assign(base, { w: 240, h: 200 })
  }
  editor.addShape(base)
  selectedId.value = id
}

function deleteSelected() {
  if (!selectedId.value) return
  const id = selectedId.value
  editor.removeShape(id) // 不弹确认：可撤销
  selectedId.value = null
  ElMessage.success({ message: '已删除（Ctrl+Z 可撤销）', duration: 1600 })
}

function applyGeometry() {
  if (!selectedShape.value) return
  const patch: Partial<FloorShape> = { x: Math.round(editX.value) || 0, y: Math.round(editY.value) || 0 }
  if (hasSize.value) {
    patch.w = Math.max(6, Math.round(editW.value) || 0)
    patch.h = Math.max(6, Math.round(editH.value) || 0)
  }
  editor.updateShape(selectedShape.value.id, patch)
}

function applyText() {
  if (!selectedShape.value) return
  const patch: Partial<FloorShape> = {}
  if (selectedShape.value.kind === 'room') {
    patch.name = editName.value
    patch.sub = editSub.value
    patch.room = editRoom.value
  } else if (selectedShape.value.kind === 'corrtext') {
    patch.text = editText.value
  }
  editor.updateShape(selectedShape.value.id, patch)
}

function markDirtyByRename() {
  // 图名不在 layout 文档里，改名的脏标记由 `nameDirty` 计算属性负责。
  // 这里留个空实现是为了让模板上的 @change 有落点（将来要埋点也在这）。
}

// ── 保存 ────────────────────────────────────────────────────────────────
async function save() {
  if (floorId.value === null) return
  saving.value = true
  try {
    const saved = await floorMapApi.save(floorId.value, {
      name: mapName.value,
      layout: editor.payload(),
      expected_updated_at: serverVersion.value,
      operator: appState.operator,
    })
    serverVersion.value = saved.updated_at
    serverName.value = saved.name
    editor.load(parseLayout(saved.layout))
    ElMessage.success('平面图已保存')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}

// ── 离开保护：**有未保存改动才确认**（可撤销的不拦，不可撤销的才拦）─────
onBeforeRouteLeave(async () => {
  if (!hasUnsaved.value) return true
  try {
    await ElMessageBox.confirm('平面图有未保存的修改，离开会丢失。要放弃这些修改吗？', '未保存的修改', {
      confirmButtonText: '放弃修改并离开',
      cancelButtonText: '留下继续编辑',
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
})

// ── 键盘 ────────────────────────────────────────────────────────────────
function onKeydown(event: KeyboardEvent) {
  const target = event.target as HTMLElement | null
  const typing =
    target &&
    (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  if (typing) return

  const meta = event.ctrlKey || event.metaKey
  if (meta && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    if (event.shiftKey) editor.redo()
    else editor.undo()
    return
  }
  if ((event.key === 'Delete' || event.key === 'Backspace') && selectedId.value) {
    event.preventDefault()
    deleteSelected()
  }
}

function goBack() {
  router.push('/map')
}

/** 视图尺寸变化时如果还没手动缩放，重新适应（简单起见每次都 fit） */
function onResize() {
  fitCanvas()
}

onMounted(() => {
  load()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onResize)
})
</script>

<style scoped>
.editor-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--appbar-h));
  height: calc(100dvh - var(--appbar-h));
  min-height: 520px;
  background: #f1f5f9;
  overflow: hidden;
}

/* ── 顶部 ── */
.ed-topbar {
  height: 56px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  background: #fff;
  border-bottom: 1px solid #e2e8f0;
  z-index: 20;
}
.ed-title { display: flex; align-items: center; gap: 10px; }
.name-input {
  font-size: 15px;
  font-weight: 700;
  color: #0f172a;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 5px 8px;
  width: 220px;
  background: transparent;
}
.name-input:hover { border-color: #e2e8f0; }
.name-input:focus { outline: none; border-color: #2563eb; background: #fff; }
.dirty-dot {
  font-size: 11px; color: #b45309; background: #fef3c7;
  border-radius: 10px; padding: 2px 8px; font-weight: 600;
}
.ed-spacer { flex: 1; }

.ed-btn {
  display: inline-flex; align-items: center; gap: 6px;
  height: 34px; padding: 0 14px; border-radius: 8px;
  font-size: 13px; font-weight: 600; cursor: pointer;
  border: 1px solid #e2e8f0; background: #fff; color: #334155;
  transition: all 0.15s;
}
.ed-btn:hover:not(:disabled) { background: #f8fafc; border-color: #cbd5e1; }
.ed-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.ed-btn.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
.ed-btn.primary:hover:not(:disabled) { background: #1d4ed8; }
.ed-btn.danger { background: #fef2f2; border-color: #fecaca; color: #dc2626; }
.ed-btn.danger:hover { background: #fee2e2; }
.ed-btn.full { width: 100%; justify-content: center; margin-top: 16px; }
.ic { font-size: 15px; line-height: 1; }

/* ── 三栏主体 ── */
.ed-body { flex: 1; display: flex; min-height: 0; }

.ed-pane {
  width: 248px; flex: none; background: #fff;
  display: flex; flex-direction: column;
  overflow-y: auto;
}
.ed-pane.left { border-right: 1px solid #e2e8f0; width: 272px; }
.ed-pane.right { border-left: 1px solid #e2e8f0; width: 268px; }

.pane-head {
  font-size: 12px; font-weight: 700; color: #64748b;
  letter-spacing: 0.4px; padding: 14px 14px 8px;
  position: sticky; top: 0; background: #fff; z-index: 2;
}
.pane-head .hint { display: block; font-weight: 500; font-size: 10.5px; color: #94a3b8; margin-top: 3px; }

.shape-palette { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; padding: 0 12px 10px; }
.palette-btn {
  height: 32px; border-radius: 7px; border: 1px dashed #cbd5e1;
  background: #f8fafc; color: #475569; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: all 0.15s;
}
.palette-btn:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; border-style: solid; }

.shape-list { list-style: none; margin: 0; padding: 0 8px 16px; }
.shape-row {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 9px; border-radius: 7px; cursor: pointer;
  font-size: 12.5px; color: #334155;
}
.shape-row:hover { background: #f8fafc; }
.shape-row.active { background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.kind-tag {
  flex: none; font-size: 10px; padding: 1px 6px; border-radius: 4px;
  background: #f1f5f9; color: #64748b;
}
.shape-row.active .kind-tag { background: #dbeafe; color: #1d4ed8; }
.shape-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shape-pos { flex: none; font-size: 10.5px; color: #94a3b8; font-variant-numeric: tabular-nums; }

/* ── 画布 ── */
.ed-viewport {
  flex: 1; position: relative; overflow: hidden; min-width: 0;
  background-color: #eaeff6;
  background-image: radial-gradient(circle, #cbd5e1 1.2px, transparent 1.2px);
  background-size: 24px 24px; background-position: -12px -12px;
  cursor: grab;
  /* ⚠️ 不给容器加 user-select: none —— 那会连触摸合成的 click 一起吞掉 */
  touch-action: none;
}
.ed-viewport.is-panning { cursor: grabbing; }
.ed-canvas { position: absolute; left: 0; top: 0; transform-origin: 0 0; }
.ed-plate {
  position: absolute; left: 0; top: 0;
  background: #f8fafc; border-radius: 16px;
  box-shadow: 0 24px 60px -16px rgba(15, 23, 42, 0.18), inset 0 0 0 1.5px #cbd5e1;
}
.ed-grid {
  position: absolute; left: 0; top: 0; border-radius: 16px; pointer-events: none;
  background-image: linear-gradient(to right, rgba(37, 99, 235, 0.09) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(37, 99, 235, 0.09) 1px, transparent 1px);
}

.ed-hud {
  position: absolute; left: 14px; bottom: 14px;
  display: flex; align-items: center; gap: 8px;
  background: rgba(255, 255, 255, 0.94); border: 1px solid #e2e8f0;
  border-radius: 9px; padding: 7px 12px;
  font-size: 11.5px; color: #475569; box-shadow: 0 4px 12px -2px rgba(15, 23, 42, 0.08);
}
.ed-hud .sep { color: #cbd5e1; }
.hud-btn {
  margin-left: 4px; border: none; background: #eff6ff; color: #1d4ed8;
  border-radius: 6px; padding: 4px 9px; font-size: 11.5px; font-weight: 600; cursor: pointer;
}
.hud-btn:hover { background: #dbeafe; }

/* ── 图元视觉（与 /map 保持一致，编辑器里加选中态）── */
.zone, .corr, .lobby, .room { position: absolute; }
.zone { border-radius: 14px; background: #fff; box-shadow: inset 0 0 0 1px #e2e8f0; }
.corr { border-radius: 12px; background: #f1f5f9; box-shadow: inset 0 0 0 1px #cbd5e1; }
.lobby { border-radius: 12px; background: #fffbeb; box-shadow: inset 0 0 0 1px #fef3c7; }
.corr-t {
  position: absolute; font-size: 10px; letter-spacing: 8px; font-weight: 700; color: #94a3b8;
  writing-mode: vertical-rl; text-orientation: upright; pointer-events: none; z-index: 2;
}
.wall-v {
  position: absolute; width: 6px; border-radius: 3px; background: #94a3b8;
  box-shadow: 0 0 0 1px #cbd5e1; z-index: 6; cursor: move;
}
.divider-h {
  position: absolute; height: 6px; border-radius: 3px; background: #cbd5e1;
  box-shadow: inset 0 0 0 1px #94a3b8; z-index: 4; cursor: move;
}
.entry {
  position: absolute; display: flex; flex-direction: column; align-items: center;
  gap: 5px; pointer-events: none;
}
.entry svg { width: 64px; height: 36px; color: #d97706; }
.entry span { font-size: 10px; color: #b45309; letter-spacing: 3px; font-weight: 700; }

.room {
  border-radius: 12px; padding: 10px 14px; overflow: hidden; cursor: move;
  background: #fff; border: 1px solid #cbd5e1; box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
}
.room.meet { background: #f0f9ff; border-color: #bae6fd; }
.room.live { background: #faf5ff; border-color: #e9d5ff; }
.room.exec { background: #f5f3ff; border-color: #ddd6fe; }
.room.front { background: #fffbf0; border-color: #fef08a; }
.room .rname {
  font-size: 12.5px; font-weight: 700; color: #1e293b; letter-spacing: 0.2px;
  display: flex; align-items: center; gap: 8px;
}
.room .rname i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.room .rsub { font-size: 9.5px; color: #64748b; letter-spacing: 0.3px; padding-left: 15px; margin-top: 3px; font-weight: 500; }

/* 选中态 + 四角手柄 */
.picked { outline: 2px solid #2563eb; outline-offset: 1px; }
.handle {
  position: absolute; width: 8px; height: 8px; background: #fff;
  border: 1.5px solid #2563eb; border-radius: 2px; z-index: 8;
}
.handle.tl { left: -4px; top: -4px; }
.handle.tr { right: -4px; top: -4px; }
.handle.bl { left: -4px; bottom: -4px; }
.handle.br { right: -4px; bottom: -4px; }

/* ── 属性面板 ── */
.empty-hint { padding: 20px 16px; font-size: 12.5px; color: #94a3b8; line-height: 1.7; }
.prop-form { padding: 4px 14px 24px; }
.prop-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.prop-row label { width: 52px; flex: none; font-size: 12px; color: #64748b; }
.prop-row input, .prop-row select {
  flex: 1; min-width: 0; height: 30px; border: 1px solid #e2e8f0; border-radius: 6px;
  padding: 0 8px; font-size: 12.5px; color: #1e293b; background: #fff;
}
.prop-row input:focus, .prop-row select:focus { outline: none; border-color: #2563eb; }
.prop-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.prop-grid .prop-row { margin-bottom: 8px; }
.prop-grid .prop-row label { width: 28px; }
.ro { font-size: 12.5px; color: #334155; }
.ro.mono { font-family: ui-monospace, monospace; color: #64748b; }
.del-hint { font-size: 11px; color: #94a3b8; margin: 8px 0 0; line-height: 1.6; }

@media (max-width: 900px) {
  .ed-pane { width: 200px; }
  .ed-pane.right { width: 220px; }
}
</style>
