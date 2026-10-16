<script setup lang="ts">
/**
 * 资产详情页的「关联轻资产」区块（设计 §5.3）。
 *
 * 回答的是实际最常问的那个问题：**这台机器上装了什么配件**。
 *
 * ## 数据来自 `/light/by-asset/{id}`，直接查当前分配记录
 *
 * 走索引、不重放流水 —— 这正是"holdings 是快照"带来的好处（列表页打开是快的）。
 *
 * ## 空态给出口，不是只写"暂无"
 *
 * "这台机器上还没登记任何配件 —— 有的话现在登记"。
 * 点「登记配件变动」会带上这台设备打开登记流转（先选品类，再去向预选这台机器）。
 *
 * ## 轻资产不折进正式资产口径
 *
 * 这一块**不参与**任何资产统计 —— "150 台设备"不能变成"150 台设备 + 38 件配件"。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import LightMovementDialog from '@/components/LightMovementDialog.vue'
import { apiMessage, lightApi } from '@/api'
import type { Asset, AssetLightHoldings, LightCategory, LightHolding } from '@/types'
import { formatDate } from '@/utils/format'

const props = defineProps<{ asset: Asset }>()

const loading = ref(false)
const data = ref<AssetLightHoldings | null>(null)

/** 登记时先选品类（登记流转是"按品类"的），选完再打开对话框 */
const categories = ref<{ id: number; name: string; unit: string }[]>([])
const pickOpen = ref(false)
const pickedCategoryId = ref<number | null>(null)
const dialogOpen = ref(false)
const dialogCategory = ref<LightCategory | null>(null)

async function load() {
  loading.value = true
  try {
    data.value = await lightApi.byAsset(props.asset.id)
  } catch (error) {
    ElMessage.error(apiMessage(error))
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => props.asset.id, load, { immediate: true })

const items = computed(() => data.value?.items ?? [])

/**
 * 「共 N 件」。
 *
 * ⚠️ 原来是 `display ×qty` 拼起来的一长串 —— 而 `display` 在这块里**就是本机自己的编号**
 * （holder 是这台资产），于是页脚写着「B022-03-00038 ×1」：既和上面的列表重复，
 * 又把按钮挤到换行。页脚只需要一个总数。
 */
const totalText = computed(() => {
  const n = items.value.reduce((sum, h) => sum + h.quantity, 0)
  return n ? `共 ${n} 件` : ''
})

/** 副行：`品牌 规格`（没有就空）。主行永远是品类名，不是宿主编号。 */
function catSpec(h: LightHolding): string {
  return [h.category_brand, h.category_spec].filter(Boolean).join(' ')
}

async function openRegister() {
  try {
    const opts = await lightApi.filters()
    categories.value = opts.categories
  } catch (error) {
    ElMessage.error(apiMessage(error))
    return
  }
  if (!categories.value.length) {
    ElMessage.info('还没有轻资产品类 —— 先去轻资产台账建一个品类')
    return
  }
  pickedCategoryId.value = categories.value[0]?.id ?? null
  pickOpen.value = true
}

async function confirmPick() {
  if (pickedCategoryId.value === null) return
  try {
    dialogCategory.value = await lightApi.category(pickedCategoryId.value)
  } catch (error) {
    ElMessage.error(apiMessage(error))
    return
  }
  pickOpen.value = false
  dialogOpen.value = true
}

function onSaved() {
  void load()
}
</script>

<template>
  <section class="panel" style="margin-top: 16px" v-loading="loading">
    <div class="section-title">
      <span>关联轻资产</span>
      <span class="section-hint">装在这台机器上的配件 · 不计入资产统计口径</span>
    </div>

    <div v-if="items.length" class="light-list">
      <div v-for="row in items" :key="row.id" class="light-row">
        <span class="ic"><AppIcon name="chip" :size="15" /></span>
        <div class="txt">
          <!--
            ⚠️ **主行是品类名，不是 `row.display`**。这条记录的持有位置就是本机自己，
            所以 `display` 等于本页顶部那个资产编号 —— 渲染出来就是
            「B022-03-00038 / 正式资产」，看不出装的是块固态硬盘（2026-10-16 用户报障）。
          -->
          <div class="name">{{ row.category_name }}</div>
          <div class="sub">
            <template v-if="catSpec(row)">{{ catSpec(row) }}</template>
            <template v-if="row.since"> · {{ formatDate(row.since) }} 装上</template>
          </div>
        </div>
        <div class="qty">{{ row.quantity }}<span class="unit">{{ row.category_unit }}</span></div>
      </div>
    </div>

    <div v-else-if="!loading" class="empty">
      这台机器上还没登记任何配件 —— 有的话现在登记。
    </div>

    <div class="foot">
      <button class="lk" @click="openRegister">
        <AppIcon name="plus" :size="13" style="margin-right: 4px" />登记配件变动
      </button>
      <span v-if="totalText" class="sum">{{ totalText }}</span>
    </div>

    <!-- 先选品类（登记流转是"按品类"的），选完再去向预选这台机器 -->
    <el-dialog v-model="pickOpen" title="登记哪一类配件？" width="min(420px, 92vw)">
      <el-select v-model="pickedCategoryId" style="width: 100%" placeholder="选择轻资产品类">
        <el-option
          v-for="c in categories"
          :key="c.id"
          :value="c.id"
          :label="`${c.name}（单位 ${c.unit}）`"
        />
      </el-select>
      <div class="pick-tip">
        下一步会直接落到「装到某台机器」，并把去向预选成这台设备 —— 你只需要确认品类和数量。
      </div>
      <template #footer>
        <el-button @click="pickOpen = false">取消</el-button>
        <el-button type="primary" :disabled="pickedCategoryId === null" @click="confirmPick">
          继续
        </el-button>
      </template>
    </el-dialog>

    <LightMovementDialog
      v-if="dialogCategory"
      v-model="dialogOpen"
      :category="dialogCategory"
      preset-action-key="mount"
      preset-target-kind="asset"
      :preset-target-ref-id="asset.id"
      :preset-target-code="asset.asset_code"
      @saved="onSaved"
    />
  </section>
</template>

<style scoped>
.light-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.light-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.light-row .ic {
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.light-row .txt {
  flex: 1;
  min-width: 0;
}
.light-row .name {
  font-size: 13.5px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.light-row .sub {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}
.light-row .qty {
  flex: none;
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.light-row .qty .unit {
  margin-left: 2px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-3);
}
.empty {
  padding: 16px 0;
  color: var(--text-3);
  font-size: 13px;
}
.foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}
.lk {
  border: 0;
  background: transparent;
  color: var(--el-color-primary);
  font-size: 12.5px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  /* ⚠️ **必须 inline-flex**，不能只靠 `white-space: nowrap`。
     `AppIcon` 渲出来的是 `<svg style="display:block">`，放在普通 button 里
     它会**独占一行**、把「登记配件变动」挤到第二行（用户报的"换行了/不和谐"就是这个）。
     nowrap 管的是文字内部的折行，管不了"块级图标 + 文字"这种结构性堆叠。 */
  display: inline-flex;
  align-items: center;
  flex: none;
  white-space: nowrap;
}
.lk:hover {
  background: var(--el-color-primary-light-9);
}
.sum {
  font-size: 12px;
  color: var(--text-3);
  margin-left: auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pick-tip {
  margin-top: 10px;
  font-size: 12.5px;
  color: var(--text-3);
  line-height: 1.7;
}
</style>
