<script setup lang="ts">
/**
 * **期初确认**：按实际盘点结果，一次性把"此刻真实有什么、都在哪"登记成基线。
 *
 * ## 它和历史导入长得像，但性质完全不同
 *
 * - 期初确认 → 建立**现在**的基线，直接落 `light_holdings`，计入守恒式；
 * - 历史导入 → 补**过去**的事件，只写流水，绝不碰当前账。
 *
 * ## 允许补录 / 重做，不永久封锁
 *
 * 上线后发现漏录了品类、或盘点发现数量不对，都可以再登记一次。
 * 界面上写的是"按实际盘点结果登记期初"而不是"初始化" —— 反复做无意义的初始化会让基线失去意义，
 * 每次登记都会留一条带操作人的流水，真做了几次，历史里看得见。
 *
 * ## 预览的职责不是"列出改动"，而是让用户在点执行前预测结果
 *
 * 所以底部常驻一块把**每个持有位置**的「改前 → 改后」都摆出来，不变的也照列。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import AppIcon from '@/components/AppIcon.vue'
import { apiMessage, lightApi } from '@/api'
import { appState } from '@/stores/app'
import type { LightCategory, LightHolderCandidate, LightHolderKind, OpeningPreview } from '@/types'

const props = defineProps<{
  modelValue: boolean
  category: LightCategory
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'saved'): void
}>()

interface Row {
  key: number
  kind: Exclude<LightHolderKind, 'none'>
  stockLocation: string
  user: string
  refId: number | null
}

let seq = 0
const rows = ref<Row[]>([])

const stockLocations = ref<string[]>([])
const users = ref<string[]>([])
const candidates = ref<Record<number, LightHolderCandidate[]>>({})
const loadingCandidates = ref<Record<number, boolean>>({})
const preview = ref<OpeningPreview | null>(null)
const previewing = ref(false)
const saving = ref(false)
const operator = ref('')
const note = ref('')

function newRow(kind: Row['kind'] = 'stock'): Row {
  seq += 1
  return { key: seq, kind, stockLocation: '', user: '', refId: null }
}

const KIND_OPTIONS: { value: Row['kind']; label: string }[] = [
  { value: 'stock', label: '库存' },
  { value: 'user', label: '使用人' },
  { value: 'asset', label: '正式资产' },
  { value: 'workstation', label: '资产点位' },
]

/** 只有两种位置需要用户"选一个对象"（库存 / 使用人是自由文本） */
function needsPicker(row: Row): boolean {
  return row.kind === 'asset' || row.kind === 'workstation'
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    rows.value = [newRow('stock')]
    preview.value = null
    operator.value = appState.operator
    note.value = ''
    try {
      const opts = await lightApi.filters()
      stockLocations.value = opts.stock_locations
      users.value = opts.users
    } catch {
      /* 候选拿不到不拦着登记，只是没有下拉提示 */
    }
    await refreshPreview()
  },
)

async function searchCandidates(row: Row, keyword: string) {
  if (!needsPicker(row)) return
  loadingCandidates.value = { ...loadingCandidates.value, [row.key]: true }
  try {
    const list = await lightApi.holders(row.kind === 'workstation' ? 'workstation' : 'asset', keyword)
    candidates.value = { ...candidates.value, [row.key]: list }
  } catch {
    candidates.value = { ...candidates.value, [row.key]: [] }
  } finally {
    loadingCandidates.value = { ...loadingCandidates.value, [row.key]: false }
  }
}

function addRow() {
  rows.value.push(newRow('user'))
}
function removeRow(key: number) {
  rows.value = rows.value.filter((r) => r.key !== key)
}

/** 把界面上的行翻成后端要的 entries（**跳过还没填全的行**） */
function buildEntries() {
  const entries: { holder: { kind: LightHolderKind; ref_id?: number | null; user?: string | null; stock_location?: string | null }; quantity: number }[] = []
  for (const row of rows.value) {
    if (row.kind === 'user' && !row.user.trim()) continue
    if (needsPicker(row) && row.refId === null) continue
    entries.push({
      holder: {
        kind: row.kind,
        ref_id: needsPicker(row) ? row.refId : null,
        user: row.kind === 'user' ? row.user.trim() : null,
        stock_location: row.kind === 'stock' ? row.stockLocation.trim() || null : null,
      },
      quantity: quantities.value[row.key] ?? 1,
    })
  }
  return entries
}

const quantities = ref<Record<number, number>>({})

function quantityOf(key: number): number {
  return quantities.value[key] ?? 1
}
function setQuantity(key: number, value: number | undefined) {
  quantities.value = { ...quantities.value, [key]: value ?? 1 }
}

const entriesCount = computed(() => buildEntries().length)

async function refreshPreview() {
  const entries = buildEntries()
  preview.value = null
  if (!entries.length) return
  previewing.value = true
  try {
    preview.value = await lightApi.openingPreview({
      category_id: props.category.id,
      entries,
      operator: operator.value.trim() || null,
      note: note.value.trim() || null,
    })
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    previewing.value = false
  }
}

function candidateLabel(c: LightHolderCandidate): string {
  return c.hint ? `${c.code} · ${c.label} · ${c.hint}` : `${c.code} · ${c.label}`
}

async function submit() {
  const entries = buildEntries()
  if (!entries.length) {
    ElMessage.warning('至少填一条：这个品类现在有多少、都在哪')
    return
  }
  if (!operator.value.trim()) {
    ElMessage.warning('期初确认必须填写操作人（是谁确认的这份基线）')
    return
  }
  saving.value = true
  try {
    await lightApi.registerOpening({
      category_id: props.category.id,
      entries,
      operator: operator.value.trim() || null,
      note: note.value.trim() || null,
    })
    ElMessage.success('已登记期初确认，基线建立完成')
    emit('saved')
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`按实际盘点结果登记期初 · ${category.name}`"
    width="min(720px, 95vw)"
    @update:model-value="emit('update:modelValue', false)"
  >
    <div class="infobox">
      期初确认建立的是<b>此刻真实有什么、都在哪</b>的基线，直接落到当前分配记录里，并且会留一条
      <b>可审计</b>的流水。它和「历史导入」不是一回事：导入只补过去发生过的事情，
      不会变成现在的账。漏录了、数错了，随时可以再登记一次。
    </div>

    <table class="rows">
      <thead>
        <tr>
          <th style="width: 118px">持有位置</th>
          <th>具体对象</th>
          <th style="width: 108px">数量</th>
          <th style="width: 44px" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.key">
          <td>
            <el-select v-model="row.kind" style="width: 100%" @change="row.refId = null; row.user = ''; row.stockLocation = ''">
              <el-option v-for="k in KIND_OPTIONS" :key="k.value" :value="k.value" :label="k.label" />
            </el-select>
          </td>
          <td>
            <el-input
              v-if="row.kind === 'stock'"
              v-model="row.stockLocation"
              maxlength="128"
              placeholder="选填，如：3 楼库房A柜 / 前台抽屉 / W01"
              list="light-opening-locations"
            />
            <el-input
              v-else-if="row.kind === 'user'"
              v-model="row.user"
              maxlength="64"
              placeholder="使用人姓名"
              list="light-opening-users"
            />
            <el-select
              v-else
              v-model="row.refId"
              filterable
              remote
              :remote-method="(kw: string) => searchCandidates(row, kw)"
              :loading="loadingCandidates[row.key]"
              :placeholder="row.kind === 'asset' ? '搜资产编号 / 品牌型号 / 使用人' : '搜点位编码 / 使用人'"
              style="width: 100%"
              @visible-change="(v: boolean) => v && searchCandidates(row, '')"
            >
              <el-option
                v-for="c in candidates[row.key] ?? []"
                :key="c.id"
                :value="c.id"
                :label="candidateLabel(c)"
              />
            </el-select>
          </td>
          <td>
            <el-input-number
              :model-value="quantityOf(row.key)"
              :min="1"
              :max="100000"
              style="width: 100%"
              @update:model-value="(v: number | undefined) => setQuantity(row.key, v)"
            />
          </td>
          <td>
            <button class="rm" title="删掉这一行" @click="removeRow(row.key)">
              <AppIcon name="close" :size="13" />
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <div class="row-actions">
      <el-button size="small" @click="addRow">
        <AppIcon name="plus" :size="13" style="margin-right: 4px" />再加一处
      </el-button>
      <el-button size="small" :loading="previewing" @click="refreshPreview">刷新预览</el-button>
      <span class="cnt">共 {{ entriesCount }} 处</span>
    </div>

    <div class="two">
      <el-form-item label="操作人（必填）">
        <el-input v-model="operator" maxlength="64" placeholder="是谁确认的这份基线" />
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="note" maxlength="255" placeholder="选填，如：2026 年 Q1 盘点结果" />
      </el-form-item>
    </div>

    <!-- 确认条：让用户在点执行前预测结果 -->
    <div v-if="preview" class="preview">
      <div class="ttl">确认一下登记后会发生什么</div>
      <div v-if="preview.opening_done" class="prev-note">
        这个品类之前登记过期初确认 —— 本次是在<b>现有基础上再加</b>（补录），不是把原来的覆盖掉。
        如果只是想改数量，请用「调整（盘盈 / 盘亏）」并写清原因，时间线才读得懂。
      </div>
      <table>
        <tbody>
          <tr v-for="(r, i) in preview.rows" :key="i">
            <td class="k">{{ r.holder.display }}</td>
            <td class="v">
              <span class="was">{{ r.before }} {{ category.unit }}</span>
              <span class="arr">→</span>
              <span class="chg">{{ r.after }} {{ category.unit }}</span>
            </td>
          </tr>
          <tr>
            <td class="k">库存</td>
            <td class="v">
              <span class="was">{{ preview.stock.before }}</span>
              <span class="arr">→</span>
              <span :class="preview.stock.after !== preview.stock.before ? 'chg' : 'same'">
                {{ preview.stock.after }}</span>
              <span v-if="preview.stock.after === preview.stock.before" class="same">（不变）</span>
            </td>
          </tr>
          <tr>
            <td class="k">已分配</td>
            <td class="v">
              <span class="was">{{ preview.allocated.before }}</span>
              <span class="arr">→</span>
              <span :class="preview.allocated.after !== preview.allocated.before ? 'chg' : 'same'">
                {{ preview.allocated.after }}</span>
              <span v-if="preview.allocated.after === preview.allocated.before" class="same">（不变）</span>
            </td>
          </tr>
          <tr>
            <td class="k">净存在</td>
            <td class="v">
              <span class="was">{{ preview.net.before }}</span>
              <span class="arr">→</span>
              <span :class="preview.net.after !== preview.net.before ? 'chg' : 'same'">{{ preview.net.after }}</span>
              <span v-if="preview.net.after === preview.net.before" class="same">（不变）</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <datalist id="light-opening-locations">
      <option v-for="l in stockLocations" :key="l" :value="l" />
    </datalist>
    <datalist id="light-opening-users">
      <option v-for="u in users" :key="u" :value="u" />
    </datalist>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!entriesCount" @click="submit">
        登记期初确认
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.infobox {
  margin-bottom: 14px;
  padding: 11px 13px;
  border-radius: var(--radius-sm);
  background: #f2f6ff;
  border: 1px solid #d6e2ff;
  color: #33507e;
  font-size: 12.5px;
  line-height: 1.7;
}
.infobox b {
  color: #24406e;
}

.rows {
  width: 100%;
  border-collapse: collapse;
}
.rows th {
  font-size: 12px;
  color: var(--text-2);
  font-weight: 500;
  text-align: left;
  padding: 0 6px 6px 0;
}
.rows td {
  padding: 0 6px 8px 0;
  vertical-align: top;
}
.rm {
  border: 0;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  height: 32px;
  width: 32px;
  border-radius: 6px;
}
.rm:hover {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 14px;
}
.row-actions .cnt {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--text-3);
}

.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 14px;
}
@media (max-width: 640px) {
  .two { grid-template-columns: 1fr; }
}

.preview {
  margin-top: 6px;
  padding: 12px 14px;
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.preview .ttl {
  font-size: 12.5px;
  color: var(--text-2);
  margin-bottom: 9px;
}
.prev-note {
  margin-bottom: 9px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #fff4e6;
  border: 1px solid #ffdba8;
  color: #8a4700;
  font-size: 12px;
  line-height: 1.7;
}
.prev-note b { color: #6d3800; }
.preview table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.preview td {
  padding: 4px 0;
}
.preview td.k {
  color: var(--text-2);
  width: 46%;
}
.preview td.v {
  color: var(--text-1);
  font-weight: 500;
}
.preview .was { color: var(--text-3); font-weight: 400; }
.preview .arr { color: var(--text-3); margin: 0 5px; }
.preview .chg { color: var(--el-color-warning); font-weight: 600; }
.preview .same { color: var(--text-3); font-weight: 400; }
</style>
