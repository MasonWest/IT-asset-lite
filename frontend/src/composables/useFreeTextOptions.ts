import { computed, ref, toValue, type MaybeRefOrGetter } from 'vue'

import { matchByPinyin } from '@/utils/pinyin'

/**
 * 「候选值既有、也允许自己写一个新的」下拉框的全部行为。
 *
 * 用在使用人 / 点位使用人这类**自由文本**字段上：值不来自字典表（红线：不做人员表），
 * 但既有的一批值（真实库里几十个人名）又得能靠打拼音首字母快速挑出来。
 *
 * ## 两个坑，都收口在这一个文件里
 *
 * ### 1. 光有 `filterable` 是**存不进新值的**
 * `filterable` 只是让输入框能打字，它不是「允许自由输入」。候选里没有能对上的项时，
 * 回车什么都不会发生、`change` 根本不触发 —— 用户看到的现象是
 * **「这个框明明可以打字，但打完就是存不进去」**。（空间地图的「点位使用人」就踩了这条：
 * 注释写着"与资产表单同一套…新建项垫底"，实际只搬了拼音筛选、没搬那个新建项。）
 *
 * 所以必须自己往候选里补一项，见 `createLabel`。
 *
 * ### 2. 补的那一项**必须排在最后**，不能用 `allow-create`
 * EP 的 `allow-create` 会把创建项**固定渲染在候选列表最前面**，而本项目的下拉为了
 * 「打拼音首字母回车选中某个人」都开了 `default-first-option`（默认高亮第一项）——
 * 两者一撞，回车存进去的是输入框里的字面量：打 `dnn` 存「dnn」而不是那个人名，
 * 打半个名字存的是半截字符串。而这恰恰是打拼音首字母的人**必然**踩到的路径。
 *
 * 自己渲染成最后一项之后，语义变成「回车 = 选第一个真实候选」，
 * 只有候选全都不命中时才落到新建。
 *
 * ⚠️ **别顺手加 `allow-create`**：EP 2.14 的 `checkDefaultFirstOption` 会优先高亮
 * 带 `created` 标记的项，而那个标记只有它自己 `allow-create` 渲染出来的选项才带，
 * 手写的 `<el-option>` 不带（这正是我们想要的）。一加 `allow-create` 就前功尽弃。
 *
 * ## 用法
 *
 * ⚠️ **必须解构**。返回的是 ref，而 Vue **不会**自动拆开"普通对象里的 ref" ——
 * 模板里写 `v-for="u in picker.options"`，拿到的是**那个 ComputedRef 本身**，
 * `v-for` 会把它当对象迭代，下拉里就会渲染出
 * `['', ()=>xxx.filter(...), 一整个候选数组, true, 132, …]` 这种东西（实测踩过）。
 * 解构出来的 `userOptions` 是顶层绑定，模板里才会被正常拆包。
 *
 * ```vue
 * <script setup lang="ts">
 * const {
 *   options: userOptions,
 *   createLabel,
 *   onFilter: filterUsers,
 *   reset: resetUserQuery,
 *   onVisibleChange: onUserVisibleChange,
 * } = useFreeTextOptions(() => props.users)
 * </script>
 *
 * <template>
 *   <el-select
 *     filterable
 *     default-first-option
 *     :filter-method="filterUsers"
 *     :reserve-keyword="false"
 *     @change="resetUserQuery"
 *     @visible-change="onUserVisibleChange"
 *   >
 *     <el-option v-for="u in userOptions" :key="u" :label="u" :value="u" />
 *     <el-option
 *       v-if="createLabel"
 *       :key="`__new__${createLabel}`"
 *       :label="createLabel"
 *       :value="createLabel"
 *     />
 *   </el-select>
 * </template>
 * ```
 *
 * `source` 传 **getter**（`() => props.users`）而不是快照 —— 候选值可能在别的操作之后变化
 * （比如刚存了一个新名字，候选里就该多一个），传快照会一直用打开弹窗那一刻的旧数组。
 */
export function useFreeTextOptions(source: MaybeRefOrGetter<string[]>) {
  /** 用户敲进筛选框里的字。这是**我们自己的**一份，和 EP 内部那份是两套（见 `reset`） */
  const query = ref('')

  const all = computed(() => toValue(source) ?? [])

  /** 候选：原文 / 拼音首字母 / 全拼，命中任意一条就算（走公共的 `matchByPinyin`） */
  const options = computed(() => all.value.filter((v) => matchByPinyin(v, query.value)))

  /**
   * 「新建」候选的内容。返回空串 = 不该渲染这一项，两种情况：
   *   - 关键字是空的 —— 用户还没打算新建
   *   - 它就是某个已有候选 —— 直接选那一个，不要凭空造一个重复值
   */
  const createLabel = computed(() => {
    const q = query.value.trim()
    if (!q || all.value.includes(q)) return ''
    return q
  })

  function onFilter(q: string) {
    query.value = q
  }

  /**
   * 清关键字。**「选完」和「收起下拉」两个时机都要清，且 EP 那份和这份都得清**：
   * EP 的 `reserve-keyword` 默认是 `true`（本意是给远程搜索用，让人用同一个关键字连挑几个），
   * 所以下拉上要显式 `:reserve-keyword="false"`；但它清的是它自己的 `inputValue`、
   * **不会**回调 `filter-method`，我们这份只能靠 `@change` / `@visible-change` 自己清，
   * 否则会出现「输入框已经空了、候选还只剩一个」的错位，用户看着像卡住了。
   */
  function reset() {
    query.value = ''
  }

  function onVisibleChange(open: boolean) {
    if (!open) reset()
  }

  return { query, options, createLabel, onFilter, reset, onVisibleChange }
}
