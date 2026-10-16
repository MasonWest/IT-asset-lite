<script setup lang="ts">
/**
 * 轻资产口径说明。
 *
 * 为什么要有这一页：这个模块满屏自造词（期初确认 / 历史导入 / 净存在 / 冲正 /
 * 当前分配），README 里其实讲得很清楚，但**应用内没有任何地方指向它** ——
 * 用户必须先知道要去翻 README 才知道该怎么用，那就不叫易用。
 * 所以把口径搬进应用，从台账页「怎么用？」直接进得来。
 */
import { useRouter } from 'vue-router'

import AppIcon from '@/components/AppIcon.vue'
import { richText } from '@/utils/richText'

const router = useRouter()

const terms = [
  { term: '库存', desc: '实物在库里、还没发出去的数量。可以分放好几处（库房A、前台抽屉…），每处一条记录。' },
  { term: '已分配', desc: '已经发出去的数量 —— 在某人手上，或者装在某台机器 / 放在某个点位上。' },
  {
    term: '当前分配',
    desc: '现在这个品类分布在哪些位置、各多少。一条记录只有一个持有位置（库存 / 某人 / 某台机器 / 某个点位，四选一）。',
  },
  {
    term: '使用人',
    desc: '发给个人的东西记"在某人手上"，不记点位 —— 一个人可能跑好几个工位，替他推断"东西大概在哪"只会算错。东西固定在工位就记那个点位。',
  },
  {
    term: '净存在',
    desc: '进过这个系统的总数减去报损的总数，恒等于「库存 + 已分配」。它不因换手而变化。',
  },
  {
    term: '期初确认',
    desc: '按实际盘点结果登记的真实基线。历史数据不全时用它把账建起来 —— 必须填操作人，因为这是一份"有人认领"的基线。**允许补录 / 重做**，不永久封锁。',
  },
  {
    term: '历史导入',
    desc: '以前的 Excel 记录。只补"过去发生过什么"，不计入当前库存与持有 —— 系统不替你猜真实库存。',
  },
  {
    term: '未登记',
    desc: '有历史流水、但当前账还是空的（典型：只导入了分配、没做期初确认）。这是诚实的空态，不是 0 也不是负数 —— 登记一次期初确认就建起来了。',
  },
  { term: '冲正', desc: '登记错了就追加一条反向记录，原记录一个字不改。已确认的历史是证据。' },
  { term: '更换配件', desc: '机器上的旧件报废 + 新件顶上，一次操作写两条流水，同一个事务 —— 中间不会出现"这台机器少一根"的假象。' },
]

const flows = [
  { op: '入库', desc: '新采购的配件进库存，或按盘点结果建立期初库存。', icon: 'arrowIn', tone: 'g' },
  { op: '分配', desc: '从库存发给某个人。', icon: 'user', tone: '' },
  { op: '安装', desc: '装到某台正式资产上，或者放到某个点位。', icon: 'wrench', tone: 'v' },
  { op: '转移', desc: '东西不回库，直接在人与人 / 人与机器之间换手，总量不变。', icon: 'swap', tone: 'v' },
  { op: '拆卸 / 归还', desc: '从机器上或人手上退回来，回库存。', icon: 'arrowOut', tone: 'a' },
  { op: '调整', desc: '盘点后的库存校正（盘盈 / 盘亏）。误报损也走这里恢复。', icon: 'check', tone: 'a' },
  { op: '报损', desc: '彻底退出管理，不再占库存、也没有持有方。', icon: 'trash', tone: 'r' },
  { op: '更换配件', desc: '旧件报损 + 新件装上，一次操作两条流水同事务提交。', icon: 'swap', tone: 'a' },
]

const rules = [
  {
    icon: 'check',
    text: '发出去的东西，得真有那么多。从库存发查库存；从某人手上发（归还 / 转移 / 报损），查他手上实际有多少。',
  },
  { icon: 'check', text: '一个位置不会同时挂两条同品类的记录 —— 同一个库房、同一台机器、同一个人，都只有一条。' },
  { icon: 'check', text: '换个人用，用「转移」一步到位，不用先"归还"再"分配"。' },
  { icon: 'check', text: '删点位上还固定放着配件时会被拦住；报废机器上还有配件时会先提示你决定它们怎么处理 —— 系统**不替你改归属**。' },
  { icon: 'check', text: '手抖连点不会重复入账。' },
  { icon: 'check', text: '「/」这类占位符不能当使用人 —— 它表示"没填"，不是一个人。' },
  {
    icon: 'check',
    text: '报损了就报损了：不能再走一次「入库」复活。误报损请用「调整（盘盈）」，时间线上留下的是"调整"而不是"入库"。',
  },
  {
    icon: 'check',
    text: '「冲正」按钮只在真能冲正时才出现。报损这类没有机械反向的操作，操作列显示成"—"，鼠标放上去会告诉你去用哪个操作。',
  },
]
</script>

<template>
  <div class="help-page">
    <div class="page-head">
      <div>
        <div class="crumb">
          <router-link to="/light" class="link">轻资产</router-link>
          <span class="sep">/</span>
          <span>怎么用</span>
        </div>
        <h1>轻资产怎么用</h1>
        <p class="sub">
          键鼠套装、SSD、内存、转接头这类东西不算固定资产、没有正式编码，但确实在流转。
          这里讲清楚这套账是怎么算的。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="router.push('/light')">回到轻资产</el-button>
      </div>
    </div>

    <div class="card">
      <h2><AppIcon name="doc" :size="16" />一句话理解</h2>
      <p class="lead">
        轻资产<b>没有"直接改库存"这种操作</b>。一切变动都靠登记流水：入库、分配、安装、转移、
        拆卸、归还、调整、报损。每一次登记都是一条记录，只加不改 ——
        这样"谁在什么时候把东西给了谁"事后永远查得到。
      </p>
      <div class="note">
        <AppIcon name="info" :size="15" />
        <span>
          轻资产台账与正式资产台账是平级的，两边数字刻意分开算：
          正式资产有多少台，永远不含键鼠套装。
        </span>
      </div>
    </div>

    <div class="card">
      <h2><AppIcon name="list" :size="16" />这些业务动作，分别是什么意思</h2>
      <div class="grid">
        <div v-for="f in flows" :key="f.op" class="item">
          <span class="ico" :class="f.tone"><AppIcon :name="f.icon" :size="17" /></span>
          <div>
            <div class="t">{{ f.op }}</div>
        <div class="d" v-html="richText(f.desc)" />
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2><AppIcon name="alert" :size="16" />你会遇到的规矩</h2>
      <ul class="rules">
        <li v-for="(r, i) in rules" :key="i">
          <AppIcon :name="r.icon" :size="15" class="ck" />
          <span v-html="richText(r.text)" />
        </li>
      </ul>
    </div>

    <div class="card">
      <h2><AppIcon name="info" :size="16" />几个词是什么意思</h2>
      <table class="terms">
        <tbody>
          <tr v-for="t in terms" :key="t.term">
            <td class="k">{{ t.term }}</td>
            <td class="v" v-html="richText(t.desc)" />
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h2><AppIcon name="clock" :size="16" />配件装到主机上之后</h2>
      <p class="lead">
        装到某台主机上的 SSD，它的"当前使用人 / 所在点位"是每次现读那台主机的信息的 ——
        主机换了人，配件的使用环境自动跟着变，不需要你改两遍。
      </p>
      <p class="lead">
        但历史流水里存的是当时的编码快照：去年它装在哪，永远查得到，主机删了也查得到。
      </p>
    </div>
  </div>
</template>

<style scoped>
.help-page {
  max-width: 900px;
}
.crumb {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 4px;
}
.crumb .link {
  color: var(--el-color-primary);
  text-decoration: none;
}
.crumb .link:hover {
  text-decoration: underline;
}
.crumb .sep {
  margin: 0 6px;
}
.head-actions {
  margin-top: 4px;
}

.card {
  padding: 18px 20px;
  margin-bottom: 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.card h2 {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-1);
}
.lead {
  margin: 0 0 10px;
  font-size: 13.5px;
  line-height: 1.85;
  color: var(--text-2);
}
.lead :deep(b) {
  color: var(--text-1);
}
.lead:last-child {
  margin-bottom: 0;
}

.note {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px 12px;
  background: #f2f6ff;
  border: 1px solid #d6e2ff;
  border-radius: var(--radius-sm);
  color: #33507e;
  font-size: 12.5px;
  line-height: 1.7;
}
.note :deep(b) {
  color: #24406e;
}

.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
@media (max-width: 720px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
.item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
}
.item .ico {
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.item .ico.g {
  background: #e8f8e5;
  color: #1f8a17;
}
.item .ico.a {
  background: #fff4e6;
  color: #d2661d;
}
.item .ico.v {
  background: #f1ebfb;
  color: #7a4bd0;
}
.item .ico.r {
  background: #ffeceb;
  color: #d13b36;
}
.item .t {
  font-size: 13.5px;
  font-weight: 600;
}
.item .d {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-2);
}

.rules {
  margin: 0;
  padding: 0;
  list-style: none;
}
.rules li {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 7px 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--text-2);
  border-bottom: 1px dashed var(--border);
}
.rules li:last-child {
  border-bottom: 0;
}
.rules .ck {
  color: #34c724;
  margin-top: 3px;
}

.terms {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.terms td {
  padding: 9px 0;
  vertical-align: top;
  border-bottom: 1px dashed var(--border);
  line-height: 1.75;
}
.terms tr:last-child td {
  border-bottom: 0;
}
.terms td.k {
  width: 130px;
  color: var(--text-1);
  font-weight: 600;
  padding-right: 14px;
}
.terms td.v {
  color: var(--text-2);
}
.terms td.v :deep(b) {
  color: var(--text-1);
}
</style>
