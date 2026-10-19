<script setup lang="ts">
/**
 * 极简线性图标（内联 SVG，不引任何图标库）。
 *
 * ## 为什么不用 emoji
 *
 * 全站曾有三套图标来源：`utils/format.ts::typeIcon()` 返回 emoji、各组件写死的 emoji、
 * 以及少量内联 `<svg>`。emoji 有三类真实问题：
 * ① **大部分设备类 emoji 码点默认是"文本呈现"**（`Emoji_Presentation=No`），
 *    要靠不可见的 `FE0F` 变体选择符才彩色显示，字体回退一变就成豆腐块 □；
 * ② 靠系统字体渲染，headless / 精简版 Windows 上直接缺失；
 * ③ 彩色、字重不齐，在统计卡这种"数字旁边的说明符号"位置上观感差，
 *    容易把克制的信息卡读成表情包（轻资产第一版的统计卡就是这样，见 CHANGELOG）。
 *
 * 现在全站统一用这一套描边图标：
 * - 本地 SVG，随前端一起打包，**不依赖系统 emoji 字体**；
 * - 不请求第三方图标 API，也不引入完整图标库（只存路径字符串）；
 * - 只用 `currentColor`，颜色由外层 CSS 决定（语义色由卡片类控制）。
 *
 * ## 路径表在哪
 *
 * 在 `utils/icons.ts::ICON_PATHS`（**全站唯一来源**）。图标选择器也从同一份派生候选，
 * 所以"界面上能选到的图标"与"这里渲染得出来的图标"必然一致。
 */
import { ICON_PATHS, FALLBACK_ICON } from '@/utils/icons'
import type { IconName } from '@/utils/icons'

const props = withDefaults(
  defineProps<{
    /** 图标名。传未登记的值时退成兜底盒子，不会渲染成空白 */
    name: string
    size?: number
  }>(),
  { size: 16 },
)

/** 认不出的名字一律给盒子：界面不该因为数据多了一种可能就空一块 */
function pathOf(name: string): string {
  const key = name as IconName
  return ICON_PATHS[key] ?? ICON_PATHS[FALLBACK_ICON]
}

/**
 * 描边宽度（用户单位）。**必须随尺寸补偿，不能固定 1.7。**
 *
 * 24×24 视口按 1.7 画出来，缩到 16px 时实际只有 `1.7 × 16/24 ≈ 1.13` 物理像素 ——
 * 在普通屏上会渲染成发虚的浅灰（实测：小尺寸那一排整体"发飘"）。
 * 所以尺寸越小，用户单位下的描边越要粗，落到屏幕上才是稳定的 ~1.5px：
 *
 * | size | stroke-width | 实际像素 |
 * | --- | --- | --- |
 * | 14 | 2.6 | 1.52 |
 * | 16 | 2.3 | 1.53 |
 * | 20 | 1.9 | 1.58 |
 * | ≥24 | 1.7 | ≥1.7 |
 */
function strokeOf(size: number): number {
  if (size <= 14) return 2.6
  if (size <= 16) return 2.3
  if (size <= 20) return 1.9
  return 1.7
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
    :stroke-width="strokeOf(props.size)"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="pathOf(name)" />
  </svg>
</template>

<style scoped>
.app-icon {
  flex: none;
  display: block;
}
</style>
