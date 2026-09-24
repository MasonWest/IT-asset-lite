<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { downloadUrl, transferApi } from '@/api'
import { useBackNav } from '@/composables/useBackNav'
import { appState } from '@/stores/app'
import type { ImportMode, ImportPreview, ImportResult, ImportRow } from '@/types'
import { importActionMeta, orDash } from '@/utils/format'

const router = useRouter()
/** 导入完回台账，筛选和页码照旧 */
const { withBack, goBack } = useBackNav('/')

type Stage = 'idle' | 'previewing' | 'previewed' | 'importing' | 'imported'

const stage = ref<Stage>('idle')
const picked = ref<File | null>(null)
const preview = ref<ImportPreview | null>(null)
const result = ref<ImportResult | null>(null)
const onlyProblems = ref(false)

const mode = ref<ImportMode>('upsert')
const autoCreateType = ref(true)

const canPreview = computed(() => Boolean(picked.value) && stage.value !== 'previewing')

const acceptTypes = '.xlsx,.xlsm,.csv'

function pickFile(file: File | null) {
  if (!file) {
    picked.value = null
    return
  }
  const name = file.name.toLowerCase()
  if (!name.endsWith('.xlsx') && !name.endsWith('.xlsm') && !name.endsWith('.csv')) {
    ElMessage.error('只支持 .xlsx 和 .csv。如果手里是 .xls，请先在 Excel 里「另存为」xlsx')
    return
  }
  picked.value = file
  // 换了文件，之前的预览和结果就作废了 —— 留着会让人以为导的还是旧文件
  preview.value = null
  result.value = null
  stage.value = 'idle'
}

function onDrop(event: DragEvent) {
  const file = event.dataTransfer?.files?.[0]
  if (file) pickFile(file)
}

function clearFile() {
  picked.value = null
  preview.value = null
  result.value = null
  stage.value = 'idle'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function runPreview() {
  if (!picked.value) return
  stage.value = 'previewing'
  result.value = null
  try {
    preview.value = await transferApi.preview(picked.value, {
      mode: mode.value,
      autoCreateType: autoCreateType.value,
      operator: appState.operator,
    })
    stage.value = 'previewed'
  } catch {
    // 后端会给出人话原因（解析失败 / 缺列 / 空文件），拦截器已经弹过提示
    stage.value = 'idle'
  }
}

async function runCommit() {
  if (!picked.value || !preview.value) return
  stage.value = 'importing'
  try {
    result.value = await transferApi.commit(picked.value, {
      mode: mode.value,
      autoCreateType: autoCreateType.value,
      operator: appState.operator,
    })
    stage.value = 'imported'
    ElMessage.success(
      `导入完成：新增 ${result.value.created} 条，更新 ${result.value.updated} 条`,
    )
  } catch {
    stage.value = 'previewed'
  }
}

function startOver() {
  clearFile()
}

const rowsToShow = computed<ImportRow[]>(() => {
  const rows = preview.value?.rows ?? []
  if (!onlyProblems.value) return rows
  return rows.filter((r) => r.action === 'error' || r.warnings.length > 0)
})

const problemCount = computed(() => (preview.value?.error ?? 0) + (preview.value?.unchanged ?? 0))

function downloadTemplate(fmt: 'xlsx' | 'csv') {
  downloadUrl(transferApi.templateUrl(fmt, true))
  ElMessage.success(`模板已开始下载（${fmt.toUpperCase()}）`)
}

function goAssets() {
  goBack()
}

function goAudit() {
  void router.push({ path: '/audit', query: withBack() })
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>批量导入</h1>
        <p class="sub">
          用 Excel 一次性录入或更新多台设备 · 支持 .xlsx / .csv · 导入前会先给你看一遍要改什么
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="goAssets">去资产台账</el-button>
        <el-button @click="goAudit">查看审计日志</el-button>
      </div>
    </div>

    <!-- ---------------------------------------------------------------- -->
    <!-- 1. 下载模板                                                        -->
    <!-- ---------------------------------------------------------------- -->
    <section class="panel step">
      <div class="step-head">
        <span class="idx">1</span>
        <div>
          <h2>下载模板</h2>
          <p>模板的列和系统字段一一对应，照着填就不会出错。不确定状态、日期怎么写，看模板里的「填写说明」工作表。</p>
        </div>
        <div class="step-actions">
          <el-button type="primary" @click="downloadTemplate('xlsx')">下载 Excel 模板</el-button>
          <el-button @click="downloadTemplate('csv')">下载 CSV 模板</el-button>
        </div>
      </div>
      <div class="tip-row">
        <span class="tip">💡 只有 <b>IT资产编号</b> 和 <b>设备类型</b> 是必填，其余留空即可</span>
        <span class="tip">💡 更新已有设备时，留空表示「保持原值不变」；想清空某格就写一个减号 <code>-</code></span>
        <span class="tip">💡 本系统导出的表格列与模板完全一致，可以直接改了再导回来</span>
      </div>
    </section>

    <!-- ---------------------------------------------------------------- -->
    <!-- 2. 选择文件与选项                                                   -->
    <!-- ---------------------------------------------------------------- -->
    <section class="panel step">
      <div class="step-head">
        <span class="idx">2</span>
        <div>
          <h2>选择文件并设置导入方式</h2>
          <p>文件第一行必须是表头，列名要和模板一致（顺序可以不同，多出来的列会被忽略）。</p>
        </div>
      </div>

      <div
        class="dropzone"
        :class="{ has: picked }"
        @dragover.prevent
        @drop.prevent="onDrop"
      >
        <input
          type="file"
          :accept="acceptTypes"
          class="file-input"
          @change="(e) => pickFile((e.target as HTMLInputElement).files?.[0] ?? null)"
        />
        <template v-if="!picked">
          <div class="dz-icon">📄</div>
          <div class="dz-title">把 .xlsx / .csv 文件拖到这里，或点击选择</div>
          <div class="dz-sub">还没填模板？先点上面「下载 Excel 模板」</div>
        </template>
        <template v-else>
          <div class="dz-icon">📗</div>
          <div class="dz-title">{{ picked.name }}</div>
          <div class="dz-sub">{{ formatSize(picked.size) }} · 点击可换一个文件</div>
        </template>
      </div>

      <div class="options">
        <div class="option-block">
          <div class="option-label">遇到已存在的 IT 资产编号时</div>
          <el-radio-group v-model="mode">
            <el-radio-button value="create_only">仅新增（报错）</el-radio-button>
            <el-radio-button value="upsert">新增或更新</el-radio-button>
          </el-radio-group>
          <div class="option-hint">
            <template v-if="mode === 'create_only'">
              只往库里加新设备。编号已经存在的行会报错并跳过，已有数据一个字节都不会被覆盖。
            </template>
            <template v-else>
              编号存在就更新那条记录（只改你填了值的列），不存在就新增。适合「导出 → 改 → 导回」。
            </template>
          </div>
        </div>

        <div class="option-block">
          <div class="option-label">设备类型在系统里不存在时</div>
          <el-switch
            v-model="autoCreateType"
            active-text="自动创建"
            inactive-text="报错"
            style="--el-switch-on-color: var(--primary)"
          />
          <div class="option-hint">
            <template v-if="autoCreateType">
              会自动把没见过的类型建出来（按名字猜类别，之后可在「设备类型」里改）。
              适合一次导入一批新品类。
            </template>
            <template v-else>
              类型名必须先在「设备类型」里建好，否则那一行会报错。适合严格控管类型字典。
            </template>
          </div>
        </div>
      </div>

      <div class="step-foot">
        <el-button v-if="picked" @click="clearFile">清除文件</el-button>
        <div class="spacer" />
        <el-button type="primary" :disabled="!canPreview" :loading="stage === 'previewing'" @click="runPreview">
          {{ stage === 'previewing' ? '正在解析…' : '预览导入结果' }}
        </el-button>
      </div>
    </section>

    <!-- ---------------------------------------------------------------- -->
    <!-- 3. 预览                                                            -->
    <!-- ---------------------------------------------------------------- -->
    <section v-if="preview" class="panel step">
      <div class="step-head">
        <span class="idx">3</span>
        <div>
          <h2>确认要做的改动</h2>
          <p>
            <b>{{ preview.filename }}</b> · {{ preview.mode_label }} ·
            共 {{ preview.total }} 行数据。这一步只做校验，还没写进数据库。
          </p>
        </div>
      </div>

      <div v-if="preview.empty" class="notice warn">
        <b>文件里没有数据行</b>，只有表头。请在表头下面填入设备信息后重新上传。
      </div>

      <div v-if="preview.missing_headers.length" class="notice info">
        文件里缺少这些列：<b>{{ preview.missing_headers.join('、') }}</b>。
        缺失的列不会被修改（更新模式下保持原值）。
      </div>

      <div v-if="preview.unknown_headers.length" class="notice info">
        忽略了不认识的多余列：<b>{{ preview.unknown_headers.join('、') }}</b>。
        这不影响导入 —— 导出文件里附带的「配对显示器」列就在这一类。
      </div>

      <div v-if="preview.auto_types.length" class="notice info">
        导入时会顺带创建这些设备类型：<b>{{ preview.auto_types.join('、') }}</b>
        （不想要的话，把上面「设备类型不存在时」改成「报错」）
      </div>

      <div v-if="preview.error" class="notice warn">
        有 <b>{{ preview.error }}</b> 行数据有问题。
        <b>这些行会被跳过，其余正常的行照常导入</b>；确认导入后可以在这里看到逐行原因。
      </div>

      <div class="stat-row compact">
        <div class="stat-card" :class="{ active: !onlyProblems }" @click="onlyProblems = false">
          <div class="k"><span>📄</span>总行数</div>
          <div class="v">{{ preview.total }}<small>行</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>✨</span>将新增</div>
          <div class="v good">{{ preview.create }}<small>条</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>✏️</span>将更新</div>
          <div class="v info">{{ preview.update }}<small>条</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>➖</span>无变化</div>
          <div class="v muted">{{ preview.unchanged }}<small>条</small></div>
        </div>
        <div class="stat-card" :class="{ active: onlyProblems }" @click="onlyProblems = true">
          <div class="k"><span>⚠️</span>错误</div>
          <div class="v" :class="{ bad: preview.error > 0 }">{{ preview.error }}<small>条</small></div>
        </div>
      </div>

      <div class="table-bar">
        <span class="left">
          {{ onlyProblems ? '只看有问题的行' : '全部行' }}（{{ rowsToShow.length }} 行）
        </span>
        <el-button v-if="problemCount" size="small" link @click="onlyProblems = !onlyProblems">
          {{ onlyProblems ? '显示全部' : '只看有问题的行' }}
        </el-button>
      </div>

      <el-table :data="rowsToShow" size="small" max-height="440" row-key="row" class="preview-table">
        <el-table-column label="Excel 行" width="82">
          <template #default="{ row }">
            <span class="mono">第 {{ row.row }} 行</span>
          </template>
        </el-table-column>
        <el-table-column label="IT 资产编号" width="140">
          <template #default="{ row }">
            <span :class="{ mono: true, dim: !row.asset_code }">{{ row.asset_code || '（空）' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="判定" width="92">
          <template #default="{ row }">
            <span
              class="action-pill"
              :style="{
                color: importActionMeta(row.action).color,
                background: importActionMeta(row.action).bg,
                borderColor: importActionMeta(row.action).border,
              }"
            >
              {{ importActionMeta(row.action).icon }} {{ row.action_label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="320">
          <template #default="{ row }">
            <ul v-if="row.errors.length" class="reasons bad">
              <li v-for="(e, i) in row.errors" :key="i">{{ e }}</li>
            </ul>
            <ul v-if="row.warnings.length" class="reasons warn">
              <li v-for="(w, i) in row.warnings" :key="i">{{ w }}</li>
            </ul>
            <ul v-if="row.changes.length" class="reasons ok">
              <li v-for="(c, i) in row.changes" :key="i">{{ c }}</li>
            </ul>
            <span v-if="!row.errors.length && !row.warnings.length && !row.changes.length" class="dim">
              {{ row.action === 'unchanged' ? '与系统里的数据完全一致，不需要改动' : '无' }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="preview.rows_truncated" class="notice info">
        表格只显示了前 {{ preview.rows.length }} 行（文件共 {{ preview.total }} 行）。
        上面的统计数字是全部行的，导入也会处理全部行。
      </div>

      <div class="step-foot">
        <span class="foot-note">
          {{ preview.can_commit
            ? `将写入 ${preview.will_write} 条（新增 ${preview.create} + 更新 ${preview.update}）`
            : '当前没有可写入的数据' }}
        </span>
        <div class="spacer" />
        <el-button @click="runPreview" :loading="stage === 'previewing'">重新预览</el-button>
        <el-button
          type="primary"
          :disabled="!preview.can_commit || stage === 'importing'"
          :loading="stage === 'importing'"
          @click="runCommit"
        >
          {{ stage === 'importing' ? '正在导入…' : `确认导入 ${preview.will_write} 条` }}
        </el-button>
      </div>
    </section>

    <!-- ---------------------------------------------------------------- -->
    <!-- 4. 结果                                                            -->
    <!-- ---------------------------------------------------------------- -->
    <section v-if="result" class="panel step">
      <div class="step-head">
        <span class="idx done">✓</span>
        <div>
          <h2>导入完成</h2>
          <p><b>{{ result.filename }}</b> · {{ result.mode_label }}</p>
        </div>
        <div class="step-actions">
          <el-button @click="goAssets">去看台账</el-button>
          <el-button type="primary" @click="startOver">再导一批</el-button>
        </div>
      </div>

      <div class="stat-row compact">
        <div class="stat-card">
          <div class="k"><span>✨</span>新增成功</div>
          <div class="v good">{{ result.created }}<small>条</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>✏️</span>更新成功</div>
          <div class="v info">{{ result.updated }}<small>条</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>➖</span>无变化跳过</div>
          <div class="v muted">{{ result.unchanged }}<small>条</small></div>
        </div>
        <div class="stat-card">
          <div class="k"><span>⚠️</span>导入失败</div>
          <div class="v" :class="{ bad: result.failed > 0 }">{{ result.failed }}<small>条</small></div>
        </div>
      </div>

      <div v-if="result.created_types.length" class="notice info">
        顺带创建了设备类型：<b>{{ result.created_types.join('、') }}</b>。
        类别是按名字猜的，如果不对请到「资产台账 → 设备类型」里改。
      </div>

      <div v-if="result.failed_rows.length" class="failed-block">
        <div class="failed-title">失败 {{ result.failed_rows.length }} 行，逐条原因如下：</div>
        <div v-for="row in result.failed_rows" :key="row.row" class="failed-row">
          <span class="mono">第 {{ row.row }} 行</span>
          <span class="mono dim">{{ orDash(row.asset_code) }}</span>
          <ul class="reasons bad">
            <li v-for="(e, i) in row.errors" :key="i">{{ e }}</li>
          </ul>
        </div>
      </div>

      <div class="notice ok">
        这次导入已经在
        <button class="linklike" @click="goAudit">审计日志</button>
        里留下了一条「批量导入」记录（含操作人、IP、时间和本文件的新增/更新条数）。
        被跳过的错误行只会出现在这里，不会写进数据库。
      </div>
    </section>

    <p class="page-note">
      想导出？到
      <button class="linklike" @click="goAssets">资产台账</button>
      页设置好筛选条件后点「导出」——
      这样导出的列和本页模板完全一致，改完可以直接再导回来。
    </p>
  </div>
</template>

<style scoped>
.step {
  padding: 18px 20px;
  margin-bottom: 14px;
}

.step-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}

.step-head h2 {
  font-size: 15px;
}

.step-head p {
  margin: 3px 0 0;
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.7;
}

.idx {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--primary-soft);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.idx.done {
  background: #e8f7ee;
  color: #0f7b3f;
  font-size: 14px;
}

.step-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.step-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.step-foot .spacer {
  flex: 1;
}

.foot-note {
  font-size: 13px;
  color: var(--text-2);
}

.tip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.tip {
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.8;
}

.tip code {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 4px;
}

/* ---- 拖拽区 ---- */
.dropzone {
  position: relative;
  margin-top: 14px;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius);
  background: var(--bg-soft);
  padding: 26px 18px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.18s, background 0.18s;
}

.dropzone:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
}

.dropzone.has {
  border-style: solid;
  border-color: var(--primary);
  background: var(--primary-soft);
}

/* 覆盖整个拖拽区的透明 file input：不用 el-upload 的默认外观，
   因为那个外观在手机上和飞书/Notion 风格差得太远 */
.file-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}

.dz-icon {
  font-size: 26px;
}

.dz-title {
  margin-top: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-1);
  word-break: break-all;
}

.dz-sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-3);
}

/* ---- 选项 ---- */
.options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  margin-top: 18px;
}

.option-block {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
}

.option-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
  margin-bottom: 10px;
}

.option-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.75;
}

/* ---- 提示条 ---- */
.notice {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.75;
}

.notice.info {
  background: var(--primary-soft);
  border: 1px solid #cbdaff;
  color: #1f4bb8;
}

.notice.warn {
  background: #fff4e6;
  border: 1px solid #ffd9a3;
  color: #a15c00;
}

.notice.ok {
  background: #e8f7ee;
  border: 1px solid #b7e6c9;
  color: #0f7b3f;
}

/* ---- 统计卡紧凑版 ---- */
.stat-row.compact {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  margin-top: 14px;
}

.stat-row.compact .stat-card {
  padding: 10px 12px;
}

.stat-row.compact .stat-card .v {
  font-size: 20px;
}

.stat-card .v.good {
  color: #0f7b3f;
}

.stat-card .v.info {
  color: #0b6bcb;
}

.stat-card .v.muted {
  color: var(--text-3);
}

.stat-card .v.bad {
  color: #d54941;
}

/* ---- 预览表 ---- */
.table-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0 6px;
  font-size: 13px;
  color: var(--text-2);
}

.table-bar .left {
  flex: 1;
}

.action-pill {
  display: inline-block;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid transparent;
  white-space: nowrap;
}

.reasons {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  line-height: 1.75;
}

.reasons.bad {
  color: #d54941;
}

.reasons.warn {
  color: #a15c00;
}

.reasons.ok {
  color: #0b6bcb;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.dim {
  color: var(--text-3);
}

/* ---- 失败行区块 ---- */
.failed-block {
  margin-top: 16px;
  border: 1px solid #f7c5c1;
  border-radius: var(--radius-sm);
  background: #fdecea;
  padding: 12px 14px;
}

.failed-title {
  font-size: 13px;
  font-weight: 600;
  color: #d54941;
  margin-bottom: 8px;
}

.failed-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 0;
  border-top: 1px dashed #f7c5c1;
  font-size: 12px;
  color: var(--text-1);
}

.failed-row:first-of-type {
  border-top: none;
}

.failed-row .reasons {
  flex: 1;
}

.linklike {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  color: var(--primary);
  cursor: pointer;
  text-decoration: underline;
}

.page-note {
  margin: 18px 2px 0;
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.8;
}

@media (max-width: 720px) {
  .step {
    padding: 15px 14px;
  }

  .step-actions {
    margin-left: 36px;
    width: 100%;
  }

  .step-foot {
    flex-direction: column;
    align-items: stretch;
  }

  .step-foot .spacer {
    display: none;
  }
}
</style>
