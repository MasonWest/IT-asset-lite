/**
 * 平面图（floor map）的纯函数与常量。
 *
 * ## 这一层的唯一职责：**layout 的解析入口**
 *
 * 后端把 `layout` 以「已解析对象」的形式给出来，理论上前端不用再 JSON.parse。
 * 但编辑器保存时要把 shape 数组再拼回文档、渲染时要按 kind 分流 ——
 * 这些规则集中在这里，组件里不许出现零散的结构判断。
 *
 * ⚠️ **单图心智模型**：这里没有任何"当前选中哪张图"的概念。
 * 后端接口恒返回一张，前端也就只认一张。不要在这里加 `currentMapId`。
 */

/** 当前支持的 layout 版本。读取时用它做兼容判断。 */
export const CURRENT_LAYOUT_VERSION = 1

/**
 * 八种图元。
 *
 * ⚠️ `wall`（竖墙）与 `divider`（横分隔条）是**两个 kind**，不是同一个加参数。
 * 原型里把横条也写成了竖墙的 class、靠 inline `width` 覆盖，接进来时
 * 就是因为这个丢了 2 条分隔条。拆开之后，"墙还是隔断"一眼可辨。
 */
export type ShapeKind =
  | 'zone' // 地台（点位区的底板）
  | 'corr' // 中央走道
  | 'corrtext' // 走道上的文字
  | 'room' // 房间（会议/办公/机房/前台）
  | 'wall' // 竖墙（宽度固定 6）
  | 'divider' // 横分隔条（高度固定 6）
  | 'lobby' // 前厅
  | 'entry' // 大楼入口

/** 几何字段。文本图元（corrtext）只有 x/y + text。 */
export interface FloorShape {
  id: string
  kind: ShapeKind
  x: number
  y: number
  w?: number
  h?: number
  /** corrtext 用 */
  text?: string
  /** room 用：配色分组（exec / meet / live / front） */
  room?: string
  /** room 用：房间名 */
  name?: string
  /** room 用：副标题 */
  sub?: string
}

export interface FloorLayout {
  schema_version: number
  shapes: FloorShape[]
}

/** 空图。默认值不许写成 `{}` —— 那样每个读的人都要判空。 */
export function emptyLayout(): FloorLayout {
  return { schema_version: CURRENT_LAYOUT_VERSION, shapes: [] }
}

/**
 * 解析 layout。**宽容读**：读到坏数据退化成空图，绝不抛异常。
 *
 * 一张读不出来的图不该让整个地图页白屏 —— 用户看到"底图没画出来"
 * 比看到整页报错好得多，而且他重新保存一次就能修好。
 */
export function parseLayout(raw: unknown): FloorLayout {
  if (!raw) return emptyLayout()

  let data: unknown = raw
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return emptyLayout()
    try {
      data = JSON.parse(text)
    } catch {
      return emptyLayout()
    }
  }

  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    return emptyLayout()
  }

  const doc = data as Record<string, unknown>
  const rawShapes = Array.isArray(doc.shapes) ? doc.shapes : []

  const shapes: FloorShape[] = []
  for (const item of rawShapes) {
    if (typeof item !== 'object' || item === null) continue
    const s = item as Record<string, unknown>
    if (!s.id || !s.kind) continue // 画不出来的直接丢

    const shape: FloorShape = {
      id: String(s.id),
      kind: String(s.kind) as ShapeKind,
      x: toInt(s.x),
      y: toInt(s.y),
    }
    if (s.w !== undefined) shape.w = toInt(s.w)
    if (s.h !== undefined) shape.h = toInt(s.h)
    if (s.text !== undefined) shape.text = String(s.text)
    if (s.room !== undefined) shape.room = String(s.room)
    if (s.name !== undefined) shape.name = String(s.name)
    if (s.sub !== undefined) shape.sub = String(s.sub)
    shapes.push(shape)
  }

  const version =
    typeof doc.schema_version === 'number' && doc.schema_version >= 1
      ? doc.schema_version
      : CURRENT_LAYOUT_VERSION

  return { schema_version: version, shapes }
}

function toInt(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n) : 0
}

/** 图元 id 列表，按文档顺序。对账与断言用。 */
export function layoutShapeIds(layout: FloorLayout): string[] {
  return layout.shapes.map((s) => s.id)
}

/**
 * 每种图元的中文名，用于编辑器图元列表 / 图例。
 * 顺序与「新增图元」下拉一致。
 */
export const SHAPE_KIND_LABEL: Record<ShapeKind, string> = {
  zone: '地台',
  corr: '走道',
  corrtext: '走道文字',
  room: '房间',
  wall: '竖墙',
  divider: '横隔断',
  lobby: '前厅',
  entry: '大楼入口',
}

/** 房间配色分组 → 主色。与原型一致。 */
export const ROOM_COLORS: Record<string, string> = {
  exec: '#8b5cf6',
  meet: '#0284c7',
  live: '#9333ea',
  front: '#d97706',
}

/** 图元在 DOM 上用的 class（不含 `room` 的配色后缀）。 */
export const SHAPE_KIND_CLASS: Record<ShapeKind, string> = {
  zone: 'zone',
  corr: 'corr',
  corrtext: 'corr-t',
  room: 'room',
  wall: 'wall-v',
  divider: 'divider-h',
  lobby: 'lobby',
  entry: 'entry',
}

/** 图元是不是"只有位置没有尺寸"（文字类）。 */
export function isTextShape(kind: ShapeKind): boolean {
  return kind === 'corrtext'
}
