/**
 * 导航栏「问题中心」那个角标。
 *
 * ## 为什么只有 `review` 才会显示数字
 *
 * 常驻红点会把人训练成无视它 —— 这是用户明确提的产品原则
 * （"问题中心不应该成为一张越来越长的红色告警清单"）。
 * 所以这里只盯**需要人看一眼**的那一档，而且用**琥珀**不用红。
 *
 * ## 为什么拿不到就什么都不做
 *
 * 角标是次要信息。它拉不到的时候（网络抖动 / 服务刚重启）**不许弹错误提示** ——
 * 用户正在别的页面干活，不该被一个角落的小数字打断。
 * 所以请求走 `silent`，失败就维持现状。
 *
 * 全局一份状态（模块级 ref）：导航栏只有一个，别每个页面各拉一次。
 */

import { ref } from 'vue'

import { insightsApi } from '@/api'

const reviewPending = ref(0)
const loaded = ref(false)

export function useInsightBadge() {
  /**
   * `page_size: 1` = **只要汇总**。首屏与角标都不需要明细，
   * 明细是按规则展开时才拉的（`by_rule` / `counts` 本来就是全库口径，不受分页影响）。
   */
  async function refresh() {
    try {
      const page = await insightsApi.issues({ page_size: 1 }, true)
      reviewPending.value = page.counts?.review ?? 0
      loaded.value = true
    } catch {
      // 故意吞掉：见文件头
    }
  }

  return { reviewPending, loaded, refresh }
}
