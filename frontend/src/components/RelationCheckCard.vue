<script setup lang="ts">
/**
 * 关系核对卡片 —— 在点位详情侧栏里**就地**呈现「点位使用人 ↔ 设备使用人」的核对结果。
 *
 * ## 三条设计口径（都对着要求写）
 *
 * ### 1. 静默优先，不弹窗
 * 结论一致、或没有可核对对象时，只留一行浅灰文字 —— **不弹任何提示**。
 * 只有出现 `mismatch`（待核对）才长出内容。普通的挂设备 / 改编码这些操作
 * 不该被一张核对卡片打断。
 *
 * ### 2. 差异是「待核对」，不是「错误」
 * 所以配色走琥珀（`review`），不走红。文案里明说"系统不会自动改任何一边"——
 * 用户看到红字的第一反应是"系统觉得我错了"，而这里根本不是那个意思。
 *
 * ### 3. 例外确认显示理由 + 当前是否还有效
 * 有效 → 显示绿勾 + 理由 + 「撤销」。
 * **已失效** → 显示琥珀警告"关系已变化，这条确认不再生效"，差异重新露出来
 *   —— 这正是要求里「旧例外确认不会继续掩盖新差异」在界面上的样子。
 *
 * 写操作（确认 / 撤销）在**本组件内**完成并向上 emit 新的核对结果：
 * 两个接口都会返回刷新后的整份核对结果，省掉一次往返，也不会出现
 * "点了确认、卡片还显示旧状态"的中间画面。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, relationCheckApi } from '@/api'
import { appState } from '@/stores/app'
import type { RelationIssue, StationRelationCheck } from '@/types'
import { hasOwner, userLabel } from '@/utils/format'
// 最小富文本渲染（`**重点**` → `<b>`）的唯一实现 —— 问题中心也用同一个
import { richText } from '@/utils/richText'

const props = defineProps<{
  check: StationRelationCheck | null
  loading: boolean
  /** 迁移入口是否可用（盘点模式下不给，避免和盘点流程抢注意力） */
  relocationEnabled: boolean
}>()

const emit = defineEmits<{
  (e: 'updated', check: StationRelationCheck): void
  /** 令牌过期 / 数据已变：请父组件重新拉一次核对结果 */
  (e: 'refresh'): void
  /** 打开迁移对话框 */
  (e: 'relocate', payload: { mode: 'station' }): void
}>()

const busy = ref(false)

/** 需要人看一眼、且没有被有效例外压住的差异条数 */
const openReviews = computed(() =>
  (props.check?.issues ?? []).filter((i) => i.status === 'mismatch' && !i.suppressed),
)

const hasExceptionNotice = computed(() =>
  (props.check?.issues ?? []).some((i) => i.exception && !i.exception.valid),
)

/** 已被**有效**例外确认盖住的差异条数（关系没变、确认仍成立） */
const settledCount = computed(
  () => (props.check?.issues ?? []).filter((i) => i.status === 'mismatch' && i.suppressed).length,
)

/**
 * 差异全被有效例外盖住了 —— 卡片要"落地"，不能再摆在琥珀色里当待办。
 *
 * ⚠️ 这里必须显式判定，**不能只看 `verdict`**：`verdict` 是从数据现算的
 * （`mismatch` 就是 `mismatch`，例外是叠在上面的业务决定，不参与它）。
 * 只看 verdict 会让"已确认的差异"继续显示成未处理。
 */
const fullySettled = computed(
  () => settledCount.value > 0 && openReviews.value.length === 0 && !hasExceptionNotice.value,
)

/** 这块要不要展开内容 */
const showDetail = computed(() => {
  const c = props.check
  if (!c || !c.applicable) return false
  return c.issues.length > 0 || c.asset_checks.some((a) => a.state !== 'consistent')
})

/** 概要文案 —— 一致时只有这一行，不打扰 */
const summary = computed(() => {
  const c = props.check
  if (!c) return ''
  if (!c.applicable) return '这个点位还没有挂载设备，暂无可核对的关系'
  if (openReviews.value.length) {
    return `${openReviews.value.length} 处点位使用人与设备使用人不一致，需要核对`
  }
  // ⚠️ 必须排在 verdict 的 switch 之前：此时 verdict 仍是 mismatch，
  // 落进下面的 default 会说出「两侧都没有登记使用人」这种与事实相反的话。
  if (settledCount.value) {
    return `${settledCount.value} 处差异已确认为例外，不再提示`
  }
  if (hasExceptionNotice.value) return '有例外确认因关系变化已失效，需要重新看一遍'
  switch (c.verdict) {
    case 'consistent':
      return '点位使用人与设备使用人一致'
    case 'unregistered':
      return '有一侧还没登记使用人（不是错误）'
    default:
      return '两侧都没有登记使用人，暂无可核对的关系'
  }
})

/**
 * 确认一条差异为例外
 */
async function confirmException(issue: RelationIssue) {
  const c = props.check
  if (!c) return
  let reason: string | null = null
  try {
    const res = await ElMessageBox.prompt(
      `${issue.detail}\n\n为什么这是正常的？（会记进审计，将来能查到）`,
      '确认例外',
      {
        confirmButtonText: '确认例外',
        cancelButtonText: '取消',
        inputPlaceholder: '例如「这个位置是公共工位，设备本来就共用」',
        inputValidator: (v: string) => (v && v.trim() ? true : '请填一句理由，不然下次没人说得清'),
        customClass: 'relation-exception-prompt',
      },
    )
    reason = res.value?.trim() || null
  } catch {
    return
  }
  if (!reason) return

  busy.value = true
  try {
    const next = await relationCheckApi.confirm(c.workstation_id, {
      rule: issue.rule,
      asset_id: issue.asset_id,
      fingerprint: issue.fingerprint,
      reason,
      operator: appState.operator || null,
    })
    emit('updated', next)
    ElMessage.success('已记为「已确认的例外」')
  } catch (error) {
    ElMessage.error(apiMessage(error))
    emit('refresh') // 令牌过期等情况：让父组件刷新核对
  } finally {
    busy.value = false
  }
}

/** 撤销例外 */
async function revokeException(issue: RelationIssue) {
  const exception = issue.exception
  if (!exception) return
  try {
    await ElMessageBox.confirm(
      `撤销这条例外确认？\n\n理由：${exception.reason}\n\n撤销后这条差异会重新出现在核对结果里。`,
      '撤销例外',
      { confirmButtonText: '撤销', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busy.value = true
  try {
    const next = await relationCheckApi.revoke(exception.id, appState.operator || null)
    emit('updated', next)
    ElMessage.success('已撤销例外确认')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="rc" :class="[check?.verdict ?? 'undetermined', { quiet: !showDetail, handled: fullySettled }]">
    <div class="rc-head">
      <span class="rc-title">关系核对</span>
      <span v-if="loading" class="rc-loading">核对中…</span>
      <template v-else>
        <span v-if="openReviews.length" class="rc-badge review">{{ openReviews.length }}</span>
        <span v-else-if="check?.applicable && (check.verdict === 'consistent' || fullySettled)" class="rc-badge ok">
          {{ fullySettled ? '已确认' : '一致' }}
        </span>
        <button
          v-if="relocationEnabled && check?.applicable"
          class="rc-migrate"
          title="把整个点位的使用人和设备迁到另一个点位"
          @click="emit('relocate', { mode: 'station' })"
        >
          迁移
        </button>
      </template>
    </div>

    <div class="rc-summary">{{ summary }}</div>

    <!-- 一致 / 无可核对对象：到此为止，不展开 -->
    <template v-if="showDetail">
      <div v-for="issue in check!.issues" :key="`${issue.rule}-${issue.asset_id ?? 0}`" class="issue" :class="issue.severity">
        <div class="issue-top">
          <span class="issue-dot" />
          <span class="issue-title">{{ issue.title }}</span>
          <span class="issue-tag">{{ issue.status_label }}</span>
        </div>
        <div class="issue-detail">{{ issue.detail }}</div>

        <!-- 已确认且仍有效 -->
        <div v-if="issue.exception && issue.exception.valid" class="exc valid">
          <div class="exc-line">
            <span class="exc-tag">已确认例外</span>
            <span class="exc-reason">{{ issue.exception.reason }}</span>
          </div>
          <button class="exc-action" :disabled="busy" @click="revokeException(issue)">撤销</button>
        </div>

        <!-- 已确认但已失效 —— 关系变了，旧确认不再遮掩 -->
        <div v-else class="exc-wrap">
          <div v-if="issue.exception && !issue.exception.valid" class="exc stale">
            <span class="exc-tag warn">旧例外已失效</span>
            <span class="exc-reason">当时的理由：{{ issue.exception.reason }}（关系随后发生了变化）</span>
          </div>
          <div class="issue-suggest" v-html="richText(issue.suggestion)" />
          <div class="issue-acts">
            <button class="rc-btn primary" :disabled="busy" @click="confirmException(issue)">
              确认例外
            </button>
          </div>
        </div>
      </div>

      <!-- 没有 issue 但状态不是"一致"：说清楚是哪一种，别留空白让人猜 -->
      <div v-if="!check!.issues.length" class="rc-note">
        <template v-if="check!.verdict === 'unregistered'">
          某一侧还没登记使用人 —— <b>这不是错误</b>，公共设备 / 备用机常常如此。
        </template>
        <template v-else>
          两侧都没有登记使用人，暂时无从对照。
        </template>
      </div>
    </template>

    <div v-if="check?.user_name && hasOwner(check.user_name)" class="rc-foot">
      以点位使用人「{{ userLabel(check.user_name) }}」为基准逐台对照
    </div>
    <div v-else-if="check?.applicable" class="rc-foot">
      点位未登记使用人，逐台设备只用「未登记」口径呈现
    </div>
  </div>
</template>

<style scoped>
.rc {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 12px 14px;
  background: #fff;
  margin-top: 16px;
}
.rc.quiet { background: #f8fafc; }
.rc.mismatch { border-color: #fde68a; background: #fffdf5; }
/* 差异全被有效例外盖住：不再是待办，收成安静的中性色 */
.rc.handled { border-color: #a7f3d0; background: #f7fefb; }

.rc-head {
  display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
}
.rc-title { font-size: 12px; font-weight: 700; color: #334155; letter-spacing: 0.2px; }
.rc-loading { font-size: 11px; color: #94a3b8; }
.rc-badge {
  font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 999px; line-height: 1.7;
}
.rc-badge.review { background: #fef3c7; color: #b45309; }
.rc-badge.ok { background: #d1fae5; color: #047857; }
.rc-migrate {
  margin-left: auto; height: 22px; padding: 0 10px; border-radius: 6px; font-size: 11px;
  border: 1px solid #cbd5e1; background: #fff; color: #475569; cursor: pointer;
}
.rc-migrate:hover { border-color: #2563eb; color: #2563eb; }

.rc-summary { font-size: 11.5px; color: #64748b; line-height: 1.7; }

.issue {
  margin-top: 10px; padding: 10px 11px; border-radius: 9px;
  background: #fffbeb; border: 1px solid #fde68a;
}
.issue.info { background: #f8fafc; border-color: #e2e8f0; }
.issue-top { display: flex; align-items: center; gap: 7px; }
.issue-dot {
  width: 6px; height: 6px; border-radius: 50%; background: #f59e0b; flex: none;
}
.issue.info .issue-dot { background: #94a3b8; }
.issue-title { font-size: 11.5px; font-weight: 700; color: #78350f; }
.issue.info .issue-title { color: #475569; }
.issue-tag {
  margin-left: auto; font-size: 10px; color: #b45309; font-weight: 600;
}
.issue.info .issue-tag { color: #94a3b8; }
.issue-detail { font-size: 11px; color: #475569; line-height: 1.75; margin-top: 5px; }
.issue-suggest { font-size: 11px; color: #64748b; line-height: 1.75; margin-top: 5px; }
/* `**粗体**` 渲染出来的重点 —— 只加粗不提色，避免和上面的标题抢注意力 */
.issue-suggest b { color: #334155; }
.issue.info .issue-suggest b { color: #475569; }
.issue-acts { display: flex; gap: 6px; margin-top: 8px; }

.exc-wrap { margin-top: 6px; }
.exc {
  display: flex; align-items: flex-start; gap: 8px; margin-top: 7px;
  padding: 7px 9px; border-radius: 8px; font-size: 11px; line-height: 1.7;
}
.exc.valid { background: #ecfdf5; border: 1px solid #a7f3d0; }
.exc.stale { background: #fff7ed; border: 1px solid #fed7aa; }
.exc-line { flex: 1; min-width: 0; }
.exc-tag {
  display: inline-block; font-weight: 700; color: #047857; margin-right: 6px;
}
.exc-tag.warn { color: #c2410c; }
.exc.stale .exc-reason { color: #9a3412; }
.exc-reason { color: #065f46; word-break: break-word; }
.exc-action {
  flex: none; height: 22px; padding: 0 9px; border-radius: 6px; font-size: 11px;
  border: 1px solid #a7f3d0; background: #fff; color: #047857; cursor: pointer;
}
.exc-action:hover { background: #f0fdf4; }

.rc-btn {
  height: 26px; padding: 0 12px; border-radius: 7px; font-size: 11.5px; font-weight: 600;
  cursor: pointer; border: 1px solid #cbd5e1; background: #fff; color: #475569;
}
.rc-btn.primary { border-color: #f59e0b; background: #f59e0b; color: #fff; }
.rc-btn.primary:hover:not(:disabled) { background: #d97706; }
.rc-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.rc-note { font-size: 11px; color: #64748b; line-height: 1.75; margin-top: 8px; }
.rc-note b { color: #0f172a; }
.rc-foot { font-size: 10.5px; color: #94a3b8; margin-top: 9px; }
</style>
