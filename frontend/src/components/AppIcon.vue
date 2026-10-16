<script setup lang="ts">
/**
 * 极简线性图标（内联 SVG，不引任何图标库）。
 *
 * 为什么不用 emoji：全站的设备类型图标走 `utils/format.ts::typeIcon()` 返回 emoji，
 * 但 emoji 在**统计卡**这种"数字旁边的说明符号"位置上观感很差 —— 彩色、字重不齐、
 * 跨平台渲染不一致，容易把克制的信息卡读成表情包（轻资产第一版的统计卡就是这样）。
 * 所以这里用一套统一描边的线性图标。
 *
 * 只用 `currentColor`、`stroke-width` 固定 —— 颜色由外层 CSS 决定（语义色由卡片类控制）。
 */
withDefaults(
  defineProps<{
    name: string
    size?: number
  }>(),
  { size: 16 },
)

// 图标路径表。key 就是用法里的 name。
const PATHS: Record<string, string> = {
  // 品类 / 设备
  box: 'M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z M3.5 7.5 12 12l8.5-4.5 M12 12v9',
  keyboard: 'M2.5 6.5h19v11h-19z M6 10h.01 M9.5 10h.01 M13 10h.01 M16.5 10h.01 M8 13.5h8',
  chip: 'M7 7h10v10H7z M10 3v3 M14 3v3 M10 18v3 M14 18v3 M3 10h3 M3 14h3 M18 10h3 M18 14h3',
  // 人 / 归属
  user: 'M12 4.6a3.4 3.4 0 1 1 0 6.8 3.4 3.4 0 0 1 0-6.8z M4.8 20c.6-3.9 3.6-6 7.2-6s6.6 2.1 7.2 6',
  users: 'M9 5.5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z M2.8 19.5c.5-3.4 3-5.4 6.2-5.4s5.7 2 6.2 5.4 M16.5 6.2a3 3 0 0 1 0 5.6 M17.5 14.6c2.1.5 3.5 2.2 3.8 4.9',
  // 位置
  pin: 'M12 21s6.5-5.6 6.5-10a6.5 6.5 0 1 0-13 0C5.5 15.4 12 21 12 21z M12 8.8a2.2 2.2 0 1 1 0 4.4 2.2 2.2 0 0 1 0-4.4z',
  screen: 'M2.5 4.5h19v12h-19z M9 20h6 M12 16.5V20',
  // 动作
  plus: 'M12 5v14 M5 12h14',
  arrowIn: 'M12 3.5v10 M8 10l4 3.5 4-3.5 M4.5 17v2.5h15V17',
  arrowOut: 'M12 14.5v-10 M8 8l4-3.5L16 8 M4.5 17v2.5h15V17',
  swap: 'M4 8.5h13 M13.5 5l3.5 3.5-3.5 3.5 M20 15.5H7 M10.5 12 7 15.5 10.5 19',
  wrench: 'M15.5 4.5a5 5 0 0 0-6.1 6.6L3.5 17v3.5H7l5.9-5.9a5 5 0 0 0 6.6-6.1l-3 3-2.5-2.5z',
  trash: 'M4 7h16 M9.5 7V4.8h5V7 M6.5 7l1 13h9l1-13',
  undo: 'M4 10h11a5 5 0 0 1 0 10H8 M7.5 6.5 4 10l3.5 3.5',
  edit: 'M4 20h4l10-10-4-4L4 16z M14 6l4 4',
  close: 'M6 6l12 12 M18 6 6 18',
  check: 'M4.5 12.5 9.5 17.5 19.5 6.5',
  // 统计
  list: 'M8 6h13 M8 12h13 M8 18h13 M3.5 6h.01 M3.5 12h.01 M3.5 18h.01',
  grid: 'M3.5 3.5h7v7h-7z M13.5 3.5h7v7h-7z M3.5 13.5h7v7h-7z M13.5 13.5h7v7h-7z',
  alert: 'M12 3.6 21 19.5H3z M12 10v4 M12 17h.01',
  doc: 'M6.5 3.5h7l5 5v12h-12z M13.5 3.5v5h5',
  download: 'M12 3.5v11 M7.5 10 12 14.5 16.5 10 M4.5 18.5h15',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5 M12 8h.01',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7.5V12l3 2',
  sum: 'M17.5 4.5h-11l5.5 7.5-5.5 7.5h11',
}
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="PATHS[name] ?? PATHS.box" />
  </svg>
</template>

<style scoped>
.app-icon {
  flex: none;
  display: block;
}
</style>
