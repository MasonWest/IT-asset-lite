<script setup lang="ts">
/**
 * 问题中心 —— 回答三个问题：现在有什么值得关注 / 为什么值得关注 / 我能直接做什么。
 *
 * ## 页面为什么按「规则」组织而不是铺一张长清单
 *
 * 真实库一上手就是 28 条"没挂点位"、4 条"没登记使用人"。平铺出来就是一堵墙，
 * 而墙的结局是被无视。所以：
 *
 *   首屏 = 汇总（几条待办、按规则分组）→ 展开某条规则才拉它的明细。
 *
 * ## 数据全部来自接口，前端一个字都不拼
 *
 * 每张卡的"为什么值得关注"是后端下发的 `reason`；动作按钮的落点是后端下发的
 * `action_url`（后端有白名单，保证是可跳的完整路径）。这里只负责**摆出来**。
 *
 * ## 没有「已解决」这个状态
 *
 * 问题每次现算：用户点了「去核对」做完真实操作，下次进这个页面（或点刷新）
 * 它就不出现了。界面上也**没有**任何"标记已处理"的按钮 —— 有那个按钮，
 * 就意味着背后有一个会过期的状态字段。
 */
import { computed, onMounted, ref } from 'vue'
import AppIcon from '@/components/AppIcon.vue'
import { useRouter } from 'vue-router'

import { apiMessage, insightsApi } from '@/api'
import type { InsightProblem, InsightRule, InsightRuleGroup, InsightsPage } from '@/types'
import { hasOwner, userLabel } from '@/utils/format'
import { ruleIcon, severityMeta } from '@/utils/issueMeta'
// 后端文案里的 `**重点**` 要渲染成粗体，不能把星号直接显示出来
// （v2.5.0 在关系核对卡片上踩过这个坑）—— 复用同一个实现
import { richText } from '@/utils/richText'
import { useInsightBadge } from '@/composables/useInsightBadge'

const router = useRouter()
const badge = useInsightBadge()

/**
 * `page_size: 1` = **首屏只要汇总**。
 *
 * 汇总（`counts` / `by_rule` / `checked` / `total`）本来就是全库口径、不受分页影响，
 * 而明细是按规则展开时才拉的（见 `toggleRule`）。所以这里不为用不到的 20 条买单。
 */
const SUMMARY_ONLY = 1

const summary = ref<InsightsPage | null>(null)
const loading = ref(false)
const loadError = ref('')

/** 展开了哪几条规则（默认全收起 —— 首屏是一张"该关注什么"的名片，不是清单） */
const expanded = ref<string[]>([])
type RuleBucket = {
  loading: boolean
  error: string
  items: InsightProblem[]
  page: number
  pages: number
  total: number
}
const buckets = ref<Record<string, RuleBucket>>({})

const counts = computed(() => summary.value?.counts ?? {})
const reviewCount = computed(() => counts.value.review ?? 0)
const infoCount = computed(() => counts.value.info ?? 0)
const total = computed(() => summary.value?.total ?? 0)
const rules = computed(() => summary.value?.by_rule ?? [])
const checked = computed(() => summary.value?.checked ?? { rules: [], stations: 0, assets: 0 })

/** 一句话结论 —— 页面第一行就是它，人不用读表格 */
const headline = computed(() => {
  if (!summary.value) return '正在检查…'
  if (total.value === 0) return '现在没有需要处理的关系问题'
  const parts: string[] = []
  if (reviewCount.value) parts.push(`${reviewCount.value} 件事需要核对`)
  if (infoCount.value) parts.push(`${infoCount.value} 条提示`)
  return parts.join('，')
})

async function loadSummary() {
  loading.value = true
  loadError.value = ''
  try {
    summary.value = await insightsApi.issues({ page_size: SUMMARY_ONLY })
    badge.refresh()
  } catch (error) {
    loadError.value = apiMessage(error)
  } finally {
    loading.value = false
  }
}

async function loadRule(rule: InsightRule, page = 1) {
  const bucket = buckets.value[rule] ?? {
    loading: false,
    error: '',
    items: [],
    page: 1,
    pages: 0,
    total: 0,
  }
  bucket.loading = true
  bucket.error = ''
  buckets.value = { ...buckets.value, [rule]: bucket }
  try {
    const data = await insightsApi.issues({ rule, page, page_size: 20 })
    buckets.value = {
      ...buckets.value,
      [rule]: {
        loading: false,
        error: '',
        items: data.items,
        page: data.page,
        pages: data.pages,
        total: data.items.length,
      },
    }
  } catch (error) {
    buckets.value = { ...buckets.value, [rule]: { ...bucket, loading: false, error: apiMessage(error) } }
  }
}

async function toggleRule(group: InsightRuleGroup) {
  if (hasExpanded(group.rule)) {
    expanded.value = expanded.value.filter((r) => r !== group.rule)
    return
  }
  expanded.value = [...expanded.value, group.rule]
  if (!buckets.value[group.rule]) await loadRule(group.rule)
}

function hasExpanded(rule: string): boolean {
  return expanded.value.includes(rule)
}

function bucketOf(rule: string): RuleBucket {
  return (
    buckets.value[rule] ?? { loading: false, error: '', items: [], page: 1, pages: 0, total: 0 }
  )
}

/** 动作一律跳到后端给的落点 —— 那是真实页面（地图 / 资产列表 / 设备详情） */
function go(url: string) {
  void router.push(url)
}

function subjectText(p: InsightProblem): string {
  return p.subjects
    .map((s) => (s.kind === 'workstation' ? `点位 ${s.code}` : `设备 ${s.code}`))
    .join(' · ')
}

function ownerName(name: string | null | undefined): string {
  return hasOwner(name) ? String(name).trim() : userLabel(name)
}

onMounted(loadSummary)
</script>

<template>
  <div class="insights">
    <!-- ① 现在有什么值得关注 -->
    <header class="head">
      <div class="head-main">
        <h2 class="headline">{{ headline }}</h2>
        <!-- 零问题时下面那块空状态已经把"查了什么"说全了，这里不再重复第二遍 -->
        <p v-if="summary && total > 0" class="head-sub">
          问题由检查引擎<b>每次现算</b>，不落库 —— 做完真实操作后它自己就会消失。
        </p>
      </div>
      <el-button :loading="loading" @click="loadSummary">刷新</el-button>
    </header>

    <el-alert
      v-if="loadError"
      class="load-err"
      type="warning"
      :closable="false"
      show-icon
      :title="loadError"
    />

    <el-skeleton v-if="!summary && loading" :rows="5" animated />

    <!-- ② 为什么值得关注：按规则分组 -->
    <section v-if="summary && rules.length" class="rules">
      <article
        v-for="group in rules"
        :key="group.rule"
        class="rule"
        :class="[group.pending ? group.severity : 'cleared', { open: hasExpanded(group.rule) }]"
      >
        <div class="rule-top">
          <span class="ic"><AppIcon :name="ruleIcon(group.rule)" :size="17" /></span>
          <div class="rule-title-wrap">
            <div class="rule-title">
              {{ group.label }}
              <span class="sev" :style="{ color: severityMeta(group.severity).color }">
                {{ severityMeta(group.severity).label }}
              </span>
            </div>
            <div class="rule-reason" v-html="richText(group.reason)" />
          </div>
          <div class="rule-counts">
            <span class="num">{{ group.pending }}</span>
            <span class="unit">条待办</span>
            <span v-if="group.handled_count" class="handled-tip">
              其中 {{ group.handled_count }} 处已确认为例外
            </span>
          </div>
        </div>

        <div class="rule-acts">
          <el-button
            v-if="group.action_url"
            type="primary"
            plain
            size="small"
            @click="go(group.action_url)"
          >
            {{ group.action_label }}
          </el-button>
          <el-button size="small" @click="toggleRule(group)">
            {{ hasExpanded(group.rule) ? '收起' : `展开 ${group.count} 条` }}
          </el-button>
          <!-- 待办为 0 但还有已确认的例外 → 说清楚为什么它还在这个页面上 -->
          <span v-if="!group.pending && group.handled_count" class="all-handled">
            待办已清零，下面是当初确认过的例外（可以查看或撤销）
          </span>
        </div>

        <!-- ③ 我能直接做什么：逐条 -->
        <div v-if="hasExpanded(group.rule)" class="items">
          <div v-if="bucketOf(group.rule).loading" class="items-loading">拉取明细…</div>
          <el-alert
            v-else-if="bucketOf(group.rule).error"
            type="warning"
            :closable="false"
            show-icon
            :title="bucketOf(group.rule).error"
          />
          <p v-else-if="!bucketOf(group.rule).items.length" class="items-empty">
            这一类下暂时没有条目。
          </p>
          <template v-else>
            <div
              v-for="p in bucketOf(group.rule).items"
              :key="p.key"
              class="item"
              :class="{ handled: p.handled }"
              :data-key="p.key"
            >
              <div class="item-main">
                <div class="item-title">
                  {{ p.title }}
                  <span v-if="p.handled" class="chip-handled">已确认为例外</span>
                </div>
                <div class="item-detail" v-html="richText(p.detail)" />
                <div class="item-subjects">
                  <span v-for="(s, i) in p.subjects" :key="i" class="subj">
                    {{ s.kind === 'workstation' ? '点位' : '设备' }} {{ s.code }}
                    <em v-if="s.user_name">· {{ ownerName(s.user_name) }}</em>
                  </span>
                </div>
                <div v-if="p.handled && p.exception_reason" class="item-exc">
                  确认理由：{{ p.exception_reason }} —— 关系变了它会自动失效，不需要你手动清
                </div>
              </div>
              <el-button size="small" @click="go(p.action_url)">{{ p.action_label }}</el-button>
            </div>

            <div v-if="bucketOf(group.rule).pages > 1" class="pager">
              <el-button
                size="small"
                :disabled="bucketOf(group.rule).page <= 1"
                @click="loadRule(group.rule, bucketOf(group.rule).page - 1)"
              >
                上一页
              </el-button>
              <span class="pager-txt">
                第 {{ bucketOf(group.rule).page }} / {{ bucketOf(group.rule).pages }} 页
              </span>
              <el-button
                size="small"
                :disabled="bucketOf(group.rule).page >= bucketOf(group.rule).pages"
                @click="loadRule(group.rule, bucketOf(group.rule).page + 1)"
              >
                下一页
              </el-button>
            </div>
          </template>
        </div>
      </article>
    </section>

    <!-- 空状态：不是一片空白，要说清"这是被查出来的" -->
    <section v-else-if="summary && !rules.length" class="empty">
      <div class="empty-ic"><AppIcon name="check" :size="26" /></div>
      <h3>没有需要处理的关系问题</h3>
      <p class="empty-line">
        检查了 <b>{{ checked.stations }}</b> 个点位、<b>{{ checked.assets }}</b> 台设备。
      </p>
      <div class="empty-rules">
        <span class="empty-rules-title">本轮检查的规则：</span>
        <span v-for="r in checked.rules" :key="r" class="empty-rule">{{ r }}</span>
      </div>
      <p class="empty-note">
        这里不列出"点位没人 + 挂着有主的设备"那类情况 —— 公共设备、备用机、会议室机器
        本来就长这样，它们<b>是正常的</b>，不是问题。
      </p>
    </section>
  </div>
</template>

<style scoped>
.insights {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.headline {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  color: var(--text-1, #1f2329);
}
.head-sub {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--text-3, #8a9099);
  line-height: 1.7;
}
.load-err {
  margin-bottom: 0;
}

.rules {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

/* 规则卡：review 走琥珀、info 走灰蓝 —— **不用红色**（异常不等于错误） */
.rule {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 13px 15px;
  background: #fff;
}
.rule.review {
  border-color: #fde68a;
  background: #fffdf5;
}
.rule.info {
  border-color: #e2e8f0;
  background: #fcfdfe;
}
/*
  待办清零、只剩"已确认的例外"的规则卡：**收成中性色**。
  它仍然值得摆在页面上（那 2 处确认过的事需要能被看见、被撤销），
  但用琥珀去强调一件"没人需要动手"的事，等于教用户忽略琥珀。
*/
.rule.cleared {
  border-color: #e2e8f0;
  background: #fcfdfe;
}
.rule.open {
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}

.rule-top {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.ic {
  font-size: 17px;
  line-height: 1.3;
}
.rule-title-wrap {
  flex: 1;
  min-width: 0;
}
.rule-title {
  font-size: 13.5px;
  font-weight: 700;
  color: #0f172a;
  display: flex;
  align-items: center;
  gap: 8px;
}
.sev {
  font-size: 11px;
  font-weight: 600;
}
.rule-reason {
  margin-top: 5px;
  font-size: 12px;
  color: #475569;
  line-height: 1.75;
}
.rule-counts {
  text-align: right;
  white-space: nowrap;
}
.num {
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}
.unit {
  font-size: 11.5px;
  color: #94a3b8;
  margin-left: 3px;
}
.handled-tip {
  display: block;
  margin-top: 3px;
  font-size: 11px;
  color: #94a3b8;
}

.rule-acts {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.all-handled {
  font-size: 11.5px;
  color: #94a3b8;
}

.items {
  margin-top: 11px;
  border-top: 1px dashed #e2e8f0;
  padding-top: 10px;
}
.items-loading,
.items-empty {
  font-size: 12px;
  color: #94a3b8;
  margin: 4px 0;
}

.item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px dashed #f1f5f9;
}
.item:last-of-type {
  border-bottom: none;
}
.item-main {
  flex: 1;
  min-width: 0;
}
.item-title {
  font-size: 12.5px;
  font-weight: 600;
  color: #1f2329;
  display: flex;
  align-items: center;
  gap: 7px;
}
.item-detail {
  margin-top: 3px;
  font-size: 11.5px;
  color: #64748b;
  line-height: 1.75;
}
.item-subjects {
  margin-top: 5px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.subj {
  font-size: 11px;
  color: #475569;
  background: #f1f5f9;
  border-radius: 5px;
  padding: 1px 7px;
}
.subj em {
  font-style: normal;
  color: #94a3b8;
}
.item-exc {
  margin-top: 5px;
  font-size: 11px;
  color: #94a3b8;
}
/* 已确认为例外：收成中性色，不再吸引眼球，但**仍在**（可查看 / 可撤销） */
.item.handled .item-title,
.item.handled .item-detail {
  color: #94a3b8;
}
.chip-handled {
  font-size: 10.5px;
  font-weight: 600;
  color: #64748b;
  background: #f1f5f9;
  border-radius: 5px;
  padding: 1px 6px;
}

.pager {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 9px;
}
.pager-txt {
  font-size: 11.5px;
  color: #94a3b8;
}

.empty {
  border: 1px dashed #cbd5e1;
  border-radius: 12px;
  padding: 28px 22px;
  background: #fcfdfe;
  text-align: center;
}
.empty-ic {
  font-size: 26px;
  color: #0f7b3f;
}
.empty h3 {
  margin: 8px 0 0;
  font-size: 15px;
  color: #1f2329;
}
.empty-line {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: #475569;
}
.empty-rules {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  align-items: center;
  justify-content: center;
}
.empty-rules-title {
  font-size: 11.5px;
  color: #94a3b8;
}
.empty-rule {
  font-size: 11.5px;
  color: #475569;
  background: #f1f5f9;
  border-radius: 6px;
  padding: 2px 8px;
}
.empty-note {
  margin: 14px auto 0;
  max-width: 560px;
  font-size: 11.5px;
  color: #94a3b8;
  line-height: 1.8;
}
</style>
