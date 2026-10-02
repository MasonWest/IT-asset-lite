import { pinyin } from 'pinyin-pro'

/**
 * 中文候选值的拼音别名 —— 让「打首字母就能选」成立。
 *
 * 用在台账筛选栏和编辑资产里的「使用人」：候选值就是几十个人名 / 房间名，
 * 想把「林晓薇」挑出来得用鼠标在人堆里翻，打 `lxw` 就命中才顺手。
 *
 * 为什么不自己拿 GB2312 一级字库的编码区间去推首字母（那套边界表很常见、看着零依赖）：
 * 一级字库只收 3755 个高频字，**「婷 嘉 鑫 媛 婧 茗 娓 昝」这些名字常用字全在二级字库**，
 * 实测在本项目真实数据上 57 个使用人漏掉 9 个；而且它按「最常用读音」排，
 * 推不出姓氏读音（单→shàn、查→zhā、仇→qiú）。所以引 pinyin-pro。
 */

/**
 * 结果里剔掉非字母，只留拼音那一截：
 * `公用（前台）` 的括号、`/` 这种占位符会被 pinyin-pro 原样带出来，对匹配没用。
 */
const letters = (s: string) => s.replace(/[^a-z]/gi, '').toLowerCase()

export interface PinyinAlias {
  /** 首字母串：「林晓薇」→ `lxw` */
  py: string
  /** 全拼串：「林晓薇」→ `linxiaowei`，打全拼也能命中 */
  full: string
}

/** 候选项就那么几十个、还会被逐键反复匹配，所以算一次就缓存住 */
const cache = new Map<string, PinyinAlias>()

export function pinyinAlias(value: string): PinyinAlias {
  const hit = cache.get(value)
  if (hit) return hit

  /*
   * surname: 'head' = 只有**开头那个字**按姓氏读音，其余按常用读音。
   *
   * 这个区分是必须的，两种"一刀切"写法都错：
   *   - 不开姓氏模式 → 「单」「查」「曾」「解」「仇」「区」当姓时读音全错（s→d、z→c…）；
   *   - 全串开姓氏模式 → 姓氏读音会被当成整串的读音规则，把后面每个字的读音一起带偏
   *     （典型现象是首字母串完全对不上——用户按常用读音打的字母一个也命不中）。
   * 名字里只有第一个字是姓，所以只在开头开。
   */
  const opt = { toneType: 'none' as const, mode: 'surname' as const, surname: 'head' as const }
  const alias: PinyinAlias = {
    py: letters(pinyin(value, { ...opt, pattern: 'first' })),
    full: letters(pinyin(value, opt)),
  }
  cache.set(value, alias)
  return alias
}

/**
 * 关键字是否命中候选值：原文 / 首字母 / 全拼，命中任意一条就算。
 *
 * **纯本地字符串比较，不打接口、也不需要防抖。** 候选值一共几十个、别名只在首次
 * 出现时算一遍就缓存了，逐键过滤是微秒级 —— 加 1 秒延迟只会让人打字时觉得卡。
 */
export function matchByPinyin(value: string, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  if (value.toLowerCase().includes(q)) return true
  const { py, full } = pinyinAlias(value)
  return (py !== '' && py.includes(q)) || (full !== '' && full.includes(q))
}
