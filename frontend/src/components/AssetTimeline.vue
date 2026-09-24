<script setup lang="ts">
/**
 * 设备变更历史时间线。
 * 数据全部来自后端 asset_events —— 每台设备的「录入 / 信息变更 / 状态流转 /
 * 配对解绑 / 盘点标记」都会在这里按时间倒序排开。
 */
import { computed, ref, watch } from 'vue'

import { assetApi } from '@/api'
import type { AssetEvent } from '@/types'
import { eventMeta, formatShortDateTime, relativeTime } from '@/utils/format'

const props = defineProps<{
  assetId: number
  /** 外部改了资产后 +1，触发重新拉取 */
  refreshKey?: number
}>()

const loading = ref(false)
const items = ref<AssetEvent[]>([])
const total = ref(0)
const filter = ref<string>('')

/** 时间线里的关键节点：把这类先挑出来，其余折叠 */
const HIGHLIGHT = new Set(['create', 'pair', 'unpair', 'checkout', 'return', 'repair', 'repair_done', 'scrap', 'restore', 'status'])

const shown = computed(() =>
  filter.value ? items.value.filter((e) => e.event_type === filter.value) : items.value,
)

const chips = computed(() => {
  const counts = new Map<string, number>()
  items.value.forEach((e) => counts.set(e.event_type, (counts.get(e.event_type) ?? 0) + 1))
  return [...counts.entries()].map(([type, count]) => ({
    type,
    count,
    label: items.value.find((e) => e.event_type === type)?.event_label ?? type,
  }))
})

function changeLine(event: AssetEvent): string {
  if (event.from_status_label && event.to_status_label && event.from_status_label !== event.to_status_label) {
    return `${event.from_status_label} → ${event.to_status_label}`
  }
  return ''
}

function detailLines(event: AssetEvent): string[] {
  const detail = event.detail
  if (!detail) return []

  const changes = detail['changes'] as Record<string, { from: string; to: string }> | undefined
  if (changes) {
    return Object.entries(changes).map(([key, value]) => `${FIELD_LABELS[key] ?? key}：${value.from} → ${value.to}`)
  }

  const counterpart = (detail['role'] === 'host' ? detail['monitor_code'] : detail['host_code']) as string | undefined
  if (counterpart) {
    return [detail['role'] === 'host' ? `显示器 ${counterpart}` : `主机 ${counterpart}`]
  }
  return []
}

const FIELD_LABELS: Record<string, string> = {
  asset_code: '资产编号',
  finance_code: '财务编码',
  device_type_id: '设备类型',
  brand: '品牌',
  model: '型号',
  serial_number: 'SN',
  status: '状态',
  user_name: '使用人',
  location: '存放位置',
  purchase_date: '购入日期',
  warranty_until: '保修到期',
  notes: '备注',
}

async function load() {
  if (!props.assetId) return
  loading.value = true
  try {
    const res = await assetApi.timeline(props.assetId)
    items.value = res.items
    total.value = res.total
  } catch {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

watch(() => [props.assetId, props.refreshKey], load, { immediate: true })
</script>

<template>
  <div class="timeline-wrap" v-loading="loading">
    <div v-if="chips.length > 1" class="timeline-chips">
      <button :class="{ on: filter === '' }" @click="filter = ''">全部 {{ total }}</button>
      <button
        v-for="c in chips"
        :key="c.type"
        :class="{ on: filter === c.type }"
        @click="filter = c.type"
      >
        {{ eventMeta(c.type).icon }} {{ c.label }} {{ c.count }}
      </button>
    </div>

    <div v-if="!loading && !shown.length" class="timeline-empty">
      {{ filter ? '这个类型下还没有记录' : '还没有任何变更记录' }}
    </div>

    <ul class="timeline">
      <li v-for="e in shown" :key="e.id" :class="{ major: HIGHLIGHT.has(e.event_type) }">
        <span
          class="node"
          :style="{ background: eventMeta(e.event_type).bg, color: eventMeta(e.event_type).color }"
        >
          {{ eventMeta(e.event_type).icon }}
        </span>
        <div class="body">
          <div class="head">
            <span class="title">{{ e.event_label }}</span>
            <span v-if="changeLine(e)" class="delta">{{ changeLine(e) }}</span>
            <span class="time" :title="e.created_at">{{ relativeTime(e.created_at) }}</span>
          </div>
          <div v-if="e.note" class="note">{{ e.note }}</div>
          <ul v-if="detailLines(e).length" class="detail">
            <li v-for="(line, i) in detailLines(e)" :key="i">{{ line }}</li>
          </ul>
          <div class="meta">
            <span v-if="e.operator">操作人：{{ e.operator }}</span>
            <span>{{ formatShortDateTime(e.created_at) }}</span>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.timeline-wrap {
  min-height: 60px;
}

.timeline-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 20px 12px;
}

.timeline-chips button {
  border: 1px solid var(--border);
  background: #fff;
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--text-2);
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}

.timeline-chips button:hover {
  border-color: var(--border-strong);
}

.timeline-chips button.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}

.timeline-empty {
  padding: 28px 20px 32px;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}

.timeline {
  list-style: none;
  margin: 0;
  padding: 0 20px 8px;
}

.timeline li {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 0 0 18px;
}

/* 竖向连线：最后一条不画，免得尾巴多出一截 */
.timeline li:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 13px;
  top: 30px;
  bottom: 0;
  width: 1px;
  background: var(--border);
}

.timeline .node {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
}

.timeline .body {
  flex: 1;
  min-width: 0;
  padding-top: 3px;
}

.timeline .head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.timeline .title {
  font-size: 13.5px;
  font-weight: 500;
  color: var(--text-1);
}

.timeline .delta {
  font-size: 11.5px;
  padding: 1px 7px;
  border-radius: 999px;
  background: #f2f3f5;
  color: var(--text-2);
}

.timeline .time {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}

.timeline .note {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-2);
  line-height: 1.6;
  word-break: break-word;
}

.timeline .detail {
  margin: 5px 0 0;
  padding: 0;
  list-style: none;
}

.timeline .detail li {
  display: block;
  padding: 0;
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.8;
}

.timeline .detail li::before {
  content: '·';
  margin-right: 6px;
}

.timeline .meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-3);
}
</style>
