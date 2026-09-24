import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { LocationQueryRaw } from 'vue-router'

/**
 * 子页面「返回上一级」的统一实现。
 *
 * 台账列表的筛选 / 页码 / 视图都写在 URL query 上（见 `AssetListView`），所以跳去
 * 子页面时用 `?back=` 把当时的**完整地址**一起带过去，返回时原样送回来 ——
 * 用户点进设备详情再退出来，筛选不会被清空。
 *
 * 之所以不用 `router.back()`：扫码直接打开详情页的人，浏览器历史里压根没有列表页，
 * `back()` 会把他弹出应用。写在 URL 里则刷新也还在，行为可预测、可测。
 *
 * `back` 不在（扫码、书签、外链）就退到 `fallback`。
 */
export function useBackNav(fallback = '/') {
  const route = useRoute()
  const router = useRouter()

  /**
   * 这一页的「返回」该去哪儿。
   *
   * 只认单个 `/` 开头的内部路径 —— `//evil.com` 这种协议相对地址会被当成站内路由推，
   * 不能放进来。
   */
  const backTarget = computed(() => {
    const back = route.query.back
    return typeof back === 'string' && back.startsWith('/') && !back.startsWith('//')
      ? back
      : fallback
  })

  /** 构造「跳去子页面」的 query：把当前页面记成返回目标 */
  function withBack(query: LocationQueryRaw = {}): LocationQueryRaw {
    return { ...query, back: route.fullPath }
  }

  function goBack() {
    void router.push(backTarget.value)
  }

  return { backTarget, withBack, goBack }
}
