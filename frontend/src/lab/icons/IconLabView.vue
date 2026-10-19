<script setup lang="ts">
/**
 * 图标预览（T1 验收工具）。
 *
 * 用途：把 `ICON_PATHS` 里全部图标逐个渲染出来，用真实浏览器核对
 * ① 形状是否符合语义、② 小尺寸下是否糊、③ 有没有整块空白（path 写错）。
 *
 * 75 个图标靠肉眼看页面是看不全的，所以这里给三个尺寸并排：
 * 16 / 20 / 30 —— 分别对应行内、卡片缩略图、空态大图。
 *
 * ⚠️ 这是**开发期工具页**，不是业务页面。图标改造完成后可以整目录删掉。
 */
import AppIcon from '@/components/AppIcon.vue'
import { ICON_PATHS, DEVICE_TYPE_ICON_GROUPS, LIGHT_CATEGORY_ICON_GROUPS } from '@/utils/icons'

const allNames = Object.keys(ICON_PATHS) as (keyof typeof ICON_PATHS)[]
</script>

<template>
  <div class="wrap">
    <h1>图标预览（{{ allNames.length }} 个）</h1>

    <div class="grid">
      <div v-for="n in allNames" :key="n" class="cell">
        <div class="row">
          <AppIcon :name="n" :size="30" />
        </div>
        <div class="nm">{{ n }}</div>
      </div>
    </div>

    <h2>小尺寸检查（同一图标的 16 / 20 px 两档）</h2>
    <div class="grid small">
      <div v-for="n in allNames" :key="n" class="cell">
        <div class="row">
          <AppIcon :name="n" :size="16" />
          <AppIcon :name="n" :size="20" />
        </div>
        <div class="nm">{{ n }}</div>
      </div>
    </div>

    <h2>设备类型选择器候选</h2>
    <div v-for="g in DEVICE_TYPE_ICON_GROUPS" :key="g.label" class="group">
      <div class="gl">{{ g.label }}</div>
      <div class="grow">
        <span v-for="i in g.icons" :key="i.name" class="gitem">
          <AppIcon :name="i.name" :size="20" />
          <span>{{ i.label }}</span>
        </span>
      </div>
    </div>

    <h2>轻资产品类选择器候选</h2>
    <div v-for="g in LIGHT_CATEGORY_ICON_GROUPS" :key="g.label" class="group">
      <div class="gl">{{ g.label }}</div>
      <div class="grow">
        <span v-for="i in g.icons" :key="i.name" class="gitem">
          <AppIcon :name="i.name" :size="20" />
          <span>{{ i.label }}</span>
        </span>
      </div>
    </div>

    <h2>兜底测试</h2>
    <div class="unset">
      <span>未知名字 → 应显示盒子：</span>
      <AppIcon name="__not_a_real_icon__" :size="20" />
      <AppIcon name="" :size="20" />
    </div>
  </div>
</template>

<style scoped>
.wrap {
  padding: 20px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif;
  color: #1f2329;
}
h1 { font-size: 18px; margin: 0 0 14px; }
h2 { font-size: 15px; margin: 24px 0 10px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.cell {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px 8px;
  text-align: center;
}
.row {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 8px;
  height: 34px;
  color: #1f5fd0;
}
.nm { font-size: 11px; color: #6b7280; margin-top: 6px; word-break: break-all; }
.group { margin-bottom: 10px; }
.gl { font-size: 12px; color: #6b7280; margin-bottom: 4px; }
.grow { display: flex; flex-wrap: wrap; gap: 10px; }
.gitem {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 5px 9px;
  font-size: 12px;
  color: #374151;
}
.unset { display: flex; align-items: center; gap: 10px; color: #1f5fd0; font-size: 13px; }
</style>
