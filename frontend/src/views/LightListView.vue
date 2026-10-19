<script setup lang="ts">
/**
 * 轻资产台账（品类视角）。
 *
 * 与正式资产台账**平级**：两边统计口径刻意分开 —— 轻资产不进固定资产口径，
 * 正式资产的数量也不包含键鼠套装。混在一张表里，"150 台设备"这种数字永远说不清。
 *
 * 页面上的库存 / 已分配**全是后端从流水现算**的（GET 返回值里带来），
 * 前端一个数都不自己加。
 *
 * ## 为什么是卡片网格而不是表格
 *
 * 第一版这里是裸 `el-table`（列宽写死、文字被省略号截断、三个链接挤到换行），
 * 与全站"不要传统后台那种密集表格感"的约束直接冲突 —— 它是全站唯一一个纯表格列表页。
 * 现在改成与「资产台账」一致的卡片网格，统计卡补上线性图标与语义配色。
 *
 * ## 列表视图（可选）也走同一套语言
 *
 * 「列表」是给习惯横排扫读的人留的另一种排法，但它一度是全站唯一的
 * `el-table border stripe` —— 满屏网格线 + 斑马纹，跟卡片视图切来切去像两个系统。
 * 现在对齐「正式资产台账」的表格视图：**无外框、无竖线、无斑马纹**，
 * 只留横向细线；品类列带图标与状态标，库存/已分配是等宽数字，
 * 当前分配与卡片视图共用同一批 chip，行可点进详情。
 * 两个视图只是同一份数据的两种排法，视觉语言必须是一套。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import LightCategoryDialog from '@/components/LightCategoryDialog.vue'
import LightImportDialog from '@/components/LightImportDialog.vue'
import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightCategory, LightSummary } from '@/types'
import { holderIconName, lightCategoryIcon } from '@/utils/light'

const router = useRouter()

const loading = ref(false)
const rows = ref<LightCategory[]>([])
const total = ref(0)
const summary = ref<LightSummary | null>(null)
const viewMode = ref<'card' | 'table'>('card')

/** 导出台账（当前分配 + 流水）。走后端只读导出，会用 `<a href>` 直接下载 */
const exportUrl = computed(() => lightApi.exportUrl(null))

const filters = reactive<{ keyword: string; active: string }>({
  keyword: '',
  active: 'true',
})

const page = ref(1)
const pageSize = ref(20)

const dialogOpen = ref(false)
const editing = ref<LightCategory | null>(null)
const importOpen = ref(false)

async function load() {
  loading.value = true
  try {
    const [pageRes, sumRes] = await Promise.all([
      lightApi.categories({
        keyword: filters.keyword.trim() || undefined,
        active: filters.active || undefined,
        page: page.value,
        page_size: pageSize.value,
      }),
      lightApi.summary(),
    ])
    rows.value = pageRes.items
    total.value = pageRes.total
    summary.value = sumRes
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  void load()
}

function openCreate() {
  editing.value = null
  dialogOpen.value = true
}

function openEdit(row: LightCategory) {
  editing.value = row
  dialogOpen.value = true
}

/** 卡片和列表两种排法的"点进去"必须是同一个去处 */
function openDetail(row: LightCategory) {
  void router.push(`/light/${row.id}`)
}

async function deactivate(row: LightCategory) {
  const busy = row.stat.allocated > 0
  const warn = busy
    ? `「${row.name}」当前还有库存 ${row.stat.stock} ${row.unit}、已分配 ${row.stat.allocated} ${row.unit}。` +
      '停用只是不再出现在新建下拉里，数据全部保留。'
    : `停用「${row.name}」？数据全部保留，只是不再出现在新建流转的下拉里。`
  try {
    await ElMessageBox.confirm(warn, '停用品类', {
      type: 'warning',
      confirmButtonText: '停用',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await lightApi.deactivateCategory(row.id, { operator: appState.operator || null, force: busy })
    ElMessage.success(`已停用「${row.name}」`)
    void load()
  } catch (error) {
    ElMessage.error(apiMessage(error))
  }
}

/** 品类图标统一走 `utils/light.ts::lightCategoryIcon()` —— 本地不再各写一份判断 */

/** 库存展示："有历史流水却没做过期初"时显示「未登记」而不是 0（不推断真实库存） */
function stockText(row: LightCategory): string {
  return row.stat.pending_opening ? '未登记' : String(row.stat.stock)
}

const statCards = computed(() => [
  {
    key: 'category_count',
    label: '启用品类',
    unit: '个',
    icon: 'box',
    tone: 'blue',
    hint: '还在管、没停用的',
  },
  {
    key: 'holding_count',
    label: '当前分配记录',
    unit: '条',
    icon: 'list',
    tone: 'violet',
    hint: '库存 / 人 / 机器 / 点位各一条',
  },
  {
    key: 'pending_opening_count',
    label: '待期初确认',
    unit: '个',
    icon: 'alert',
    tone: 'amber',
    hint: '有历史流水、当前账为空',
  },
  {
    key: 'movement_count',
    label: '流水条数',
    unit: '条',
    icon: 'sum',
    tone: 'green',
    hint: '每次流转各一条',
  },
])

function cardValue(key: string): number {
  if (!summary.value) return 0
  return (summary.value as unknown as Record<string, number>)[key] ?? 0
}

/** 点统计卡 = 快捷筛选（与资产台账同一套交互）。 */
const activeStat = ref('category_count')
function clickStat(key: string) {
  if (key !== 'category_count') {
    // 其余三张卡没有对应的列表筛选，跳不到；只提示，不做假筛选。
    ElMessage.info(
      key === 'pending_opening_count'
        ? '「待期初确认」的品类会在下方卡片上标出「未登记」'
        : '这一项是页头汇总，不能当作列表筛选',
    )
    return
  }
  activeStat.value = activeStat.value === key ? '' : key
  onFilterChange()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>轻资产</h1>
        <p class="sub">
          不纳入正式台账的 IT 配件：键鼠套装、SSD、内存、转接头。
          当前分配记录在库里，流转历史在流水里。
          <router-link to="/light-help" class="sub-link">怎么用？</router-link>
        </p>
      </div>
      <div class="head-actions">
        <a class="plain-link" :href="exportUrl">
          <AppIcon name="download" :size="15" style="margin-right: 5px" />导出台账
        </a>
        <el-button @click="importOpen = true">导入历史流水</el-button>
        <el-button type="primary" @click="openCreate">
          <AppIcon name="plus" :size="15" style="margin-right: 5px" />新增品类
        </el-button>
      </div>
    </div>

    <div class="stat-row light-stat-row">
      <button
        v-for="c in statCards"
        :key="c.key"
        class="stat-card light-stat"
        :class="[`tone-${c.tone}`, { on: activeStat === c.key && c.key === 'category_count' }]"
        @click="clickStat(c.key)"
      >
        <span class="bar" />
        <span class="k"><AppIcon :name="c.icon" :size="15" />{{ c.label }}</span>
        <span class="v">{{ cardValue(c.key) }}<small>{{ c.unit }}</small></span>
        <span class="hint">{{ c.hint }}</span>
      </button>
    </div>

    <el-alert
      v-if="summary && summary.pending_opening_count > 0"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 14px"
    >
      <template #title>
        有 {{ summary.pending_opening_count }} 个品类「有历史流水、但当前账是空的」
      </template>
      <div style="font-size: 12.5px; line-height: 1.7; margin-top: 2px">
        这通常是历史数据只导入了分配流水、没做期初确认。系统<b>不会据历史推断真实库存</b>——
        请进品类详情，按实际盘点结果登记「期初确认」，把真实基线建起来。
      </div>
    </el-alert>

    <div class="filter-bar">
      <el-input
        v-model="filters.keyword"
        class="grow"
        placeholder="搜索品类名 / 品牌 / 规格"
        clearable
        @change="onFilterChange"
        @clear="onFilterChange"
      />
      <el-select
        v-model="filters.active"
        placeholder="状态"
        style="width: 120px"
        @change="onFilterChange"
      >
        <el-option label="启用中" value="true" />
        <el-option label="已停用" value="false" />
        <el-option label="全部" value="all" />
      </el-select>
      <div class="view-toggle">
        <button :class="{ on: viewMode === 'card' }" @click="viewMode = 'card'">
          <AppIcon name="grid" :size="14" />卡片
        </button>
        <button :class="{ on: viewMode === 'table' }" @click="viewMode = 'table'">
          <AppIcon name="list" :size="14" />列表
        </button>
      </div>
    </div>

    <!-- 卡片网格（默认），与「资产台账」同一套视觉语言 -->
    <div v-if="viewMode === 'card'" v-loading="loading" class="light-grid">
      <article
        v-for="row in rows"
        :key="row.id"
        class="light-card"
        @click="openDetail(row)"
      >
        <div class="top">
          <span class="thumb">
            <AppIcon :name="lightCategoryIcon(row)" :size="20" />
          </span>
          <div class="title">
            <div class="name">{{ row.name }}</div>
            <div class="spec">{{ [row.brand, row.spec].filter(Boolean).join(' · ') || '未填品牌规格' }}</div>
          </div>
          <span class="tag" :class="row.active ? 'ok' : 'off'">
            {{ row.active ? '启用中' : '已停用' }}
          </span>
        </div>

        <div class="nums">
          <div class="cell">
            <div class="lbl">库存</div>
            <div
              class="num"
              :class="{
                amber: row.stat.pending_opening,
                zero: !row.stat.pending_opening && row.stat.stock === 0,
              }"
            >
              {{ stockText(row) }}<small v-if="!row.stat.pending_opening">{{ row.unit }}</small>
            </div>
          </div>
          <div class="cell">
            <div class="lbl">已分配</div>
            <div class="num" :class="{ zero: row.stat.allocated === 0 }">
              {{ row.stat.allocated }}<small>{{ row.unit }}</small>
            </div>
          </div>
        </div>

        <div class="who">
          <div class="lbl">当前分配</div>
          <div v-if="row.stat.holdings.length" class="chips">
            <span v-for="h in row.stat.holdings.slice(0, 3)" :key="h.id" class="who-chip">
              <AppIcon :name="holderIconName(h.kind)" :size="12" />
              {{ h.display }}<span v-if="h.quantity > 1" class="q">×{{ h.quantity }}</span>
            </span>
            <span v-if="row.stat.holdings.length > 3" class="more">
              +{{ row.stat.holdings.length - 3 }}
            </span>
          </div>
          <div v-else class="who-empty">
            {{ row.stat.pending_opening ? '有历史流水，还没做期初确认' : '还没有分配出去' }}
          </div>
        </div>

        <div class="foot">
          <button
            v-if="row.active"
            class="lk"
            @click.stop="openDetail(row)"
          >
            登记流转
          </button>
          <span class="sp" />
          <button class="lk" @click.stop="openEdit(row)">编辑</button>
          <button v-if="row.active" class="lk danger" @click.stop="deactivate(row)">停用</button>
        </div>
      </article>

      <button class="light-card add-card" @click="openCreate">
        <AppIcon name="plus" :size="22" />
        <span>新增品类</span>
      </button>

      <div v-if="!loading && !rows.length" class="empty-grid">
        <p>还没有轻资产品类</p>
        <el-button type="primary" size="small" @click="openCreate">新增第一个品类</el-button>
      </div>
    </div>

    <!--
      列表视图（可选）：与「正式资产台账」的表格视图**同一套语言** ——
      无外框、无竖线、无斑马纹，只留横向细线；行可点，点了进品类详情。
      它和上面的卡片视图是同一份数据的两种排法，来回切不该像两个系统。
    -->
    <div v-else class="panel table-panel">
      <el-table v-loading="loading" :data="rows" row-key="id" @row-click="openDetail">
        <el-table-column label="品类" min-width="250">
          <template #default="{ row }">
            <div class="cat-cell">
              <span class="ic"><AppIcon :name="lightCategoryIcon(row)" :size="17" /></span>
              <div class="ct">
                <div class="nm">
                  <span class="nm-t">{{ row.name }}</span>
                  <span class="tag" :class="row.active ? 'ok' : 'off'">
                    {{ row.active ? '启用中' : '已停用' }}
                  </span>
                </div>
                <div class="sb">
                  {{ [row.brand, row.spec].filter(Boolean).join(' · ') || '未填品牌规格' }}
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="库存" width="122" align="right">
          <template #default="{ row }">
            <span v-if="row.stat.pending_opening" class="pill amber">未登记</span>
            <span v-else class="num" :class="{ zero: row.stat.stock === 0 }">
              {{ row.stat.stock }}<small>{{ row.unit }}</small>
            </span>
          </template>
        </el-table-column>

        <el-table-column label="已分配" width="112" align="right">
          <template #default="{ row }">
            <span class="num" :class="{ zero: row.stat.allocated === 0 }">
              {{ row.stat.allocated }}<small>{{ row.unit }}</small>
            </span>
          </template>
        </el-table-column>

        <el-table-column label="当前分配" min-width="280">
          <template #default="{ row }">
            <div v-if="row.stat.holdings.length" class="chips">
              <span v-for="h in row.stat.holdings.slice(0, 3)" :key="h.id" class="who-chip">
                <AppIcon :name="holderIconName(h.kind)" :size="12" />
                {{ h.display }}<span v-if="h.quantity > 1" class="q">×{{ h.quantity }}</span>
              </span>
              <span v-if="row.stat.holdings.length > 3" class="more">
                +{{ row.stat.holdings.length - 3 }}
              </span>
            </div>
            <span v-else class="muted">
              {{ row.stat.pending_opening ? '有历史流水，还没做期初确认' : '还没有分配出去' }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="188" align="right" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.active"
              link
              type="primary"
              size="small"
              @click.stop="openDetail(row)"
            >
              登记流转
            </el-button>
            <el-button link type="primary" size="small" @click.stop="openEdit(row)">编辑</el-button>
            <el-button
              v-if="row.active"
              link
              type="danger"
              size="small"
              @click.stop="deactivate(row)"
            >
              停用
            </el-button>
          </template>
        </el-table-column>

        <template #empty>
          <div class="empty-state">
            <div class="big"><AppIcon name="box" :size="44" /></div>
            <div class="title">还没有轻资产品类</div>
            <div>键鼠套装、SSD、内存这类不纳入正式台账的配件，建成品类就能开始记账。</div>
            <div style="margin-top: 16px">
              <el-button type="primary" @click="openCreate">+ 新增品类</el-button>
            </div>
          </div>
        </template>
      </el-table>
    </div>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="load"
        @size-change="onFilterChange"
      />
    </div>

    <LightCategoryDialog v-model="dialogOpen" :category="editing" @saved="load" />

    <LightImportDialog v-model="importOpen" @imported="load" />
  </div>
</template>

<style scoped>
.sub-link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.sub-link:hover {
  text-decoration: underline;
}

/* ---------- 统计卡：线性图标 + 左侧语义色条 ---------- */
.light-stat-row {
  grid-template-columns: repeat(auto-fit, minmax(172px, 1fr));
}
.light-stat {
  position: relative;
  display: block;
  text-align: left;
  overflow: hidden;
}
.light-stat .bar {
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: currentColor;
}
.light-stat .k {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
}
.light-stat .v {
  display: block;
}
.light-stat .hint {
  display: block;
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--text-3);
}
.light-stat.tone-blue { color: var(--el-color-primary); }
.light-stat.tone-violet { color: #7a4bd0; }
.light-stat.tone-amber { color: var(--el-color-warning); }
.light-stat.tone-green { color: #34c724; }
.light-stat .v {
  color: var(--text-1);
}

/* ---------- 卡片网格 ---------- */
.light-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
@media (max-width: 1180px) {
  .light-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .light-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 620px) {
  .light-grid { grid-template-columns: 1fr; }
}

.light-card {
  display: flex;
  flex-direction: column;
  padding: 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s;
}
.light-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.light-card .top {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 40px;
}
.light-card .thumb {
  width: 38px;
  height: 38px;
  flex: none;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.light-card .title {
  min-width: 0;
  flex: 1;
}
/* 名称限两行、规格限一行：否则一条长规格会把这张卡撑高，同排卡片的数字块就错位了 */
.light-card .name {
  font-size: 14.5px;
  font-weight: 600;
  line-height: 1.35;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.light-card .spec {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.light-card .nums {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 13px 0 11px;
  padding: 11px 12px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.light-card .nums .lbl {
  font-size: 11.5px;
  color: var(--text-2);
}
.light-card .nums .num {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.light-card .nums .num small {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
  margin-left: 3px;
}
.light-card .nums .num.amber {
  color: var(--el-color-warning);
  font-size: 17px;
}
.light-card .nums .num.zero {
  color: var(--text-3);
}

.light-card .who {
  flex: 1;
}
.light-card .who .lbl {
  font-size: 11.5px;
  color: var(--text-3);
  margin-bottom: 4px;
}
.light-card .chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.who-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-1);
}
.who-chip .q {
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
.light-card .more {
  align-self: center;
  font-size: 12px;
  color: var(--text-3);
}
.who-empty {
  font-size: 12px;
  color: var(--text-3);
}

.light-card .foot {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 11px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.light-card .foot .sp {
  flex: 1;
}
.lk {
  border: 0;
  background: transparent;
  color: var(--el-color-primary);
  font-size: 12.5px;
  padding: 3px 7px;
  border-radius: 6px;
  cursor: pointer;
}
.lk:hover {
  background: var(--el-color-primary-light-9);
}
.lk.danger {
  color: var(--el-color-danger);
}
.lk.danger:hover {
  background: var(--el-color-danger-light-9);
}

.add-card {
  border-style: dashed;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  color: var(--text-3);
  gap: 6px;
  font-size: 13px;
}
.add-card:hover {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.empty-grid {
  grid-column: 1 / -1;
  padding: 30px 0;
  text-align: center;
  color: var(--text-3);
}
.empty-grid p {
  margin: 0 0 10px;
}

/* 状态标签 */
.tag {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
  border: 1px solid;
}
.tag.ok {
  background: #e8f8e5;
  color: #1f8a17;
  border-color: #c8ebc2;
}
.tag.off {
  background: #f2f3f5;
  color: var(--text-3);
  border-color: var(--border);
}

/* 导出台账：与旁边的 el-button 视觉对齐（它只是一个下载链接） */
.plain-link {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  margin-right: 10px;
  border: 1px solid var(--border-strong, #dcdfe6);
  border-radius: var(--el-border-radius-base, 4px);
  background: var(--panel, #fff);
  color: var(--text-1);
  font-size: 14px;
  text-decoration: none;
}
.plain-link:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

/* ---------- 列表视图：与「正式资产台账」的表格同一套语言 ----------
   无外框 / 无竖线 / 无斑马纹（`border stripe` 是上一版，满屏网格 + 条纹，
   跟全站"不要密集后台表格感"的约束对着干），只留横向细线，外面包一层白色面板。 */
.table-panel {
  padding: 4px 4px 0;
}

/* 行可点进详情 —— 光标是唯一的提示，一定要给 */
.table-panel :deep(.el-table__row) {
  cursor: pointer;
}
/* 行高比 Element 默认舒展一点：这里一行就是一个品类，别挤成一团 */
.table-panel :deep(.el-table__cell) {
  padding: 11px 0;
}

.cat-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.cat-cell .ic {
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.cat-cell .ct {
  min-width: 0;
}
.cat-cell .nm {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
}
/* 名字可以很长，但状态标不能被挤出去 —— 省略的是名字 */
.cat-cell .nm-t {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cat-cell .sb {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 数字列：等宽数字，一列扫下来位数对得齐 */
.table-panel .num {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}
.table-panel .num small {
  margin-left: 3px;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
}
.table-panel .num.zero {
  color: var(--text-3);
}

/* 「未登记」是个状态、不是数字 0 —— 用琥珀色胶囊把它和真实的 0 分开 */
.table-panel .pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 18px;
  border: 1px solid;
}
.table-panel .pill.amber {
  background: #fff4e6;
  color: #b65c00;
  border-color: #ffdba8;
}

/* 当前分配：与卡片视图共用同一批 chip，两边切换不该让人重新认一遍 */
.table-panel .chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
}
.table-panel .more {
  font-size: 12px;
  color: var(--text-3);
}

.muted {
  color: var(--text-3);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
</style>
