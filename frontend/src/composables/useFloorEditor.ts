/**
 * 平面图编辑器的核心状态：图元文档 + 撤销栈 + 脏标记。
 *
 * ## 为什么单独成一个组合式函数
 *
 * 「编辑一张图」的规则能脱离 Vue 讲清楚：改了什么、能不能撤销、有没有未保存。
 * 塞进 `.vue` 里会跟画布手势、吸附、缩放糊在一起，改一处要读整个文件。
 *
 * ## 撤销栈的两条口径（来自方案评审）
 *
 * 1. **可撤销的操作不拦，不可撤销的才拦。**
 *    图元删除**不弹确认** —— 它在内存文档里，`Ctrl+Z` 能回来。
 *    只有「离开页面且有未保存改动」和「删除整张平面图」才确认（那是真的回不来）。
 * 2. 栈深固定（默认 50）。无限栈在长编辑会话里会吃内存，而用户几乎不会
 *    一次撤销 100 步以上。
 *
 * ## 不在这里做的事
 *
 * 不做吸附、不做坐标换算 —— 那是 `utils/workstationMap.ts` 的纯函数，
 * 编辑器只是"按一下算一次"。
 */

import { computed, ref, type Ref } from 'vue'

import {
  emptyLayout,
  type FloorLayout,
  type FloorShape,
  type ShapeKind,
} from '@/utils/floorMap'

/** 撤销栈深度。够用且不失控。 */
const HISTORY_LIMIT = 50

export interface UseFloorEditorOptions {
  /** 画布尺寸，用于图元越界收边 */
  canvas: Ref<{ width: number; height: number }>
}

export function useFloorEditor(options: UseFloorEditorOptions) {
  const layout = ref<FloorLayout>(emptyLayout())

  /** 栈里存**快照**而不是 diff：图元文档小（19 个），快照实现简单且不会算错。 */
  const past = ref<FloorLayout[]>([])
  const future = ref<FloorLayout[]>([])

  /** 打开页面时的原始快照，用来判断"有没有改过"。 */
  const baseline = ref<string>(JSON.stringify(emptyLayout()))

  const dirty = computed(() => JSON.stringify(layout.value) !== baseline.value)
  const canUndo = computed(() => past.value.length > 0)
  const canRedo = computed(() => future.value.length > 0)

  /** 每次改动前先落一份快照进 past。 */
  function pushHistory() {
    past.value.push(clone(layout.value))
    if (past.value.length > HISTORY_LIMIT) past.value.shift()
    future.value = []
  }

  function clone(l: FloorLayout): FloorLayout {
    return { schema_version: l.schema_version, shapes: l.shapes.map((s) => ({ ...s })) }
  }

  /** 载入一张图（打开编辑器 / 放弃修改后重载）。清空历史。 */
  function load(next: FloorLayout) {
    layout.value = clone(next)
    baseline.value = JSON.stringify(layout.value)
    past.value = []
    future.value = []
  }

  /**
   * 直接替换整份文档并记一步历史。
   * 拖拽过程中的中间帧**不要**调这个 —— 那会把栈灌满。
   * 拖拽应该：拖动时只改 `layout.value`（用 mutateWithoutHistory），
   * 松手时才调一次 `commit()`。
   */
  function mutate(mutator: (draft: FloorLayout) => void) {
    pushHistory()
    mutator(layout.value)
  }

  /** 拖拽中间帧用：改而不记历史。松手后再调 `commitSnapshot()` 补一步。 */
  function mutateWithoutHistory(mutator: (draft: FloorLayout) => void) {
    mutator(layout.value)
  }

  /**
   * 拖拽结束时调一次：把"开始拖之前"的那份快照补进历史。
   * `before` 是拖拽开始时的深拷贝，由调用方在 pointerdown 时存好。
   */
  function commitSnapshot(before: FloorLayout) {
    past.value.push(before)
    if (past.value.length > HISTORY_LIMIT) past.value.shift()
    future.value = []
  }

  function undo() {
    const prev = past.value.pop()
    if (!prev) return
    future.value.push(clone(layout.value))
    layout.value = prev
  }

  function redo() {
    const next = future.value.pop()
    if (!next) return
    past.value.push(clone(layout.value))
    layout.value = next
  }

  // ── 图元增删改 ────────────────────────────────────────────────────────

  function addShape(shape: FloorShape) {
    mutate((draft) => {
      draft.shapes.push(shape)
    })
  }

  /** 删除图元。**不弹确认** —— 它是可撤销的。 */
  function removeShape(id: string) {
    mutate((draft) => {
      draft.shapes = draft.shapes.filter((s) => s.id !== id)
    })
  }

  /** 更新图元字段（选中面板里改名字/尺寸用）。 */
  function updateShape(id: string, patch: Partial<FloorShape>) {
    mutate((draft) => {
      const target = draft.shapes.find((s) => s.id === id)
      if (target) Object.assign(target, patch)
    })
  }

  /** 生成新图元 id：`k` + 递增序号，保证不与现有冲突。 */
  function nextShapeId(kind: ShapeKind): string {
    const prefix = kind.charAt(0)
    let n = 1
    const used = new Set(layout.value.shapes.map((s) => s.id))
    while (used.has(`${prefix}${n}`)) n += 1
    return `${prefix}${n}`
  }

  // ── 序列化 ────────────────────────────────────────────────────────────

  /** 给后端的 payload（PUT /api/floor-maps/{id} 的 layout 字段）。 */
  function payload(): FloorLayout {
    return clone(layout.value)
  }

  /** 放弃修改：回到 baseline。 */
  function reset() {
    layout.value = JSON.parse(baseline.value) as FloorLayout
    past.value = []
    future.value = []
  }

  return {
    layout,
    dirty,
    canUndo,
    canRedo,
    load,
    mutate,
    mutateWithoutHistory,
    commitSnapshot,
    undo,
    redo,
    addShape,
    removeShape,
    updateShape,
    nextShapeId,
    payload,
    reset,
  }
}
