<script setup lang="ts">
/**
 * 详情页的「配对设备」面板。
 *
 * 主机侧：列出当前挂着的显示器，可解绑、可再加显示器。
 * 显示器侧：显示挂在哪台主机上，可解绑、可换绑到别的机器。
 * 底部附一张配对记录表（含已解绑的），换绑轨迹一眼能翻到。
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { apiMessage, assetApi, pairingApi } from '@/api'
import { appState } from '@/stores/app'
import type { Asset, AssetBrief, Relation } from '@/types'
import { formatDate, orDash, statusMeta, typeIcon } from '@/utils/format'

const props = defineProps<{
  asset: Asset
}>()

const emit = defineEmits<{
  (e: 'changed'): void
}>()

const router = useRouter()

const relations = ref<Relation[]>([])
const historyLoading = ref(false)
const showHistory = ref(false)

const candidates = ref<AssetBrief[]>([])
const hosts = ref<AssetBrief[]>([])
const pickerLoading = ref(false)
const pickerOpen = ref(false)
const pickerMode = ref<'bind' | 'rebind'>('bind')
const pickedId = ref<number | undefined>(undefined)
const pickerNote = ref('')
const acting = ref(false)

const isHost = computed(() => props.asset.device_category === 'host')
const isMonitor = computed(() => props.asset.device_category === 'display')
const monitors = computed(() => props.asset.monitors ?? [])
const host = computed(() => props.asset.host)

const activeRelations = computed(() => relations.value.filter((r) => r.active))
const pastRelations = computed(() => relations.value.filter((r) => !r.active))

/** 当前操作人（可能为空，操作前会提醒填） */
function operator(): string {
  return appState.operator.trim()
}

async function loadRelations() {
  historyLoading.value = true
  try {
    const res = await assetApi.relations(props.asset.id)
    relations.value = res.items
  } catch {
    /* 拦截器已提示 */
  } finally {
    historyLoading.value = false
  }
}

async function openPicker(mode: 'bind' | 'rebind') {
  pickerMode.value = mode
  pickedId.value = undefined
  pickerNote.value = ''
  pickerOpen.value = true
  pickerLoading.value = true
  try {
    const board = await pairingApi.board()
    if (mode === 'bind') {
      // 已挂到别的主机上的显示器不在这里出现，想换就点显示器的「换绑」
      candidates.value = board.unpaired_monitors
    } else {
      hosts.value = board.hosts
        .map((h) => h.host)
        .filter((h) => h.id !== host.value?.id)
    }
  } catch {
    candidates.value = []
    hosts.value = []
  } finally {
    pickerLoading.value = false
  }
}

async function submitPicker() {
  if (!pickedId.value) {
    ElMessage.warning(pickerMode.value === 'bind' ? '先选一台显示器' : '先选一台主机')
    return
  }
  const op = operator()
  if (!op) {
    ElMessage.warning('请先在右上角 ⚙ 里设置操作人，或者到设备详情执行一次操作后再试')
    return
  }

  acting.value = true
  try {
    if (pickerMode.value === 'bind') {
      await pairingApi.bind({
        monitor_id: pickedId.value,
        host_id: props.asset.id,
        operator: op,
        note: pickerNote.value.trim() || null,
      })
      ElMessage.success('已绑定')
    } else {
      await pairingApi.rebind({
        monitor_id: props.asset.id,
        host_id: pickedId.value,
        operator: op,
        note: pickerNote.value.trim() || null,
      })
      ElMessage.success('已换绑')
    }
    pickerOpen.value = false
    await loadRelations()
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

async function release(relation: Relation) {
  const isHostSide = relation.host_id === props.asset.id
  const other = isHostSide ? relation.monitor : relation.host
  try {
    await ElMessageBox.confirm(
      `确定把「${other.asset_code}」从当前配对中解开？解绑记录会留在双方设备的变更历史里。`,
      '解绑确认',
      { type: 'warning', confirmButtonText: '解绑', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  acting.value = true
  try {
    await pairingApi.release(relation.id, { operator: operator() || null, note: '手动解绑' })
    ElMessage.success('已解绑')
    await loadRelations()
    emit('changed')
  } catch (error) {
    ElMessage.error(apiMessage(error))
  } finally {
    acting.value = false
  }
}

function openAsset(brief: AssetBrief) {
  void router.push(`/asset/${brief.id}`)
}

watch(() => props.asset.id, loadRelations, { immediate: true })
</script>

<template>
  <section class="panel pair-panel">
    <div class="section-title">
      <span>配对设备</span>
      <span class="count-chip" v-if="isHost">{{ monitors.length }} 台显示器</span>
      <span class="count-chip" v-else-if="isMonitor && host">已挂机</span>
      <span class="count-chip" v-else-if="isMonitor">未挂机</span>
    </div>

    <!-- 主机侧 -->
    <div v-if="isHost" class="pair-body">
      <div v-if="monitors.length" class="pair-list">
        <div v-for="m in monitors" :key="m.id" class="pair-item">
          <span class="type-badge sm">{{ typeIcon(m.device_type_name) }}</span>
          <div class="info" @click="openAsset(m)">
            <div class="c1">{{ m.asset_code }}</div>
            <div class="c2">
              {{ orDash(m.brand) }} {{ m.model || '' }}
              <template v-if="m.serial_number"> · SN {{ m.serial_number }}</template>
            </div>
          </div>
          <span
            class="status-pill"
            :style="{
              color: statusMeta(m.status).color,
              background: statusMeta(m.status).bg,
              borderColor: statusMeta(m.status).border,
            }"
          >
            {{ m.status_label }}
          </span>
          <el-button
            size="small"
            :disabled="acting"
            @click="release(activeRelations.find((r) => r.monitor_id === m.id) as Relation)"
          >
            解绑
          </el-button>
        </div>
      </div>

      <div v-else class="pair-empty">
        <div>这台主机还没有配显示器</div>
        <div class="sub">从下面点「绑定显示器」，把库房里待配的显示器挂上来。</div>
      </div>

      <div class="pair-actions">
        <el-button type="primary" size="small" @click="openPicker('bind')">+ 绑定显示器</el-button>
      </div>
    </div>

    <!-- 显示器侧 -->
    <div v-else-if="isMonitor" class="pair-body">
      <div v-if="host" class="pair-list">
        <div class="pair-item highlight">
          <span class="type-badge sm">{{ typeIcon(host.device_type_name) }}</span>
          <div class="info" @click="openAsset(host)">
            <div class="c1">{{ host.asset_code }}</div>
            <div class="c2">
              {{ host.device_type_name }} · {{ orDash(host.brand) }} {{ host.model || '' }}
            </div>
            <div class="c2">使用人 {{ orDash(host.user_name) }} · {{ orDash(host.location) }}</div>
          </div>
          <el-button size="small" :disabled="acting" @click="openPicker('rebind')">换绑</el-button>
          <el-button
            size="small"
            :disabled="acting"
            @click="release(activeRelations[0])"
          >
            解绑
          </el-button>
        </div>
      </div>

      <div v-else class="pair-empty">
        <div>这台显示器还没挂到任何主机上</div>
        <div class="sub">点「挂到主机」，选一台机器。</div>
      </div>

      <div class="pair-actions">
        <el-button v-if="!host" type="primary" size="small" @click="openPicker('rebind')">
          + 挂到主机
        </el-button>
      </div>
    </div>

    <!-- 其它类别 -->
    <div v-else class="pair-empty">
      <div>这一类设备不参与主机 / 显示器配对</div>
      <div class="sub">
        键盘、鼠标、打印机这类外设本来就不挂在某台主机上。
        如果这台设备其实能当主机或显示器，到「设备类型」里改一下它的类别即可。
      </div>
    </div>

    <!-- 配对记录 -->
    <div v-if="isHost || isMonitor" class="pair-history">
      <button class="history-toggle" @click="showHistory = !showHistory">
        {{ showHistory ? '收起' : '展开' }}配对记录（{{ relations.length }} 条，含已解绑）
      </button>

      <div v-if="showHistory" v-loading="historyLoading" class="history-table">
        <div v-if="!relations.length" class="pair-empty" style="padding: 16px 0">
          还没有任何配对记录
        </div>
        <div v-for="r in [...activeRelations, ...pastRelations]" :key="r.id" class="history-row">
          <span class="badge" :class="r.active ? 'on' : 'off'">{{ r.active ? '生效中' : '已解绑' }}</span>
          <span class="text">
            {{ r.host.asset_code }} ↔ {{ r.monitor.asset_code }}
          </span>
          <span class="time">
            {{ formatDate(r.created_at.slice(0, 10)) }}
            <template v-if="!r.active"> → {{ formatDate(r.released_at?.slice(0, 10)) }}</template>
          </span>
          <span class="who">{{ r.active ? (r.created_by || '—') : (r.released_by || '—') }}</span>
        </div>
      </div>
    </div>

    <!-- 选择弹窗 -->
    <el-dialog
      v-model="pickerOpen"
      :title="pickerMode === 'bind' ? `给 ${asset.asset_code} 绑定显示器` : `给 ${asset.asset_code} 换主机`"
      width="min(520px, 92vw)"
    >
      <div v-loading="pickerLoading">
        <div v-if="pickerMode === 'bind'">
          <div v-if="!candidates.length" class="pair-empty" style="padding: 20px 0">
            <div>没有待配对的显示器</div>
            <div class="sub">所有显示器都已经配出去了，或者还没有录入显示类设备。</div>
          </div>
          <el-radio-group v-else v-model="pickedId" class="picker-grid">
            <el-radio
              v-for="m in candidates"
              :key="m.id"
              :value="m.id"
              border
              class="picker-radio"
            >
              <span class="picker-code">{{ m.asset_code }}</span>
              <span class="picker-sub">
                {{ orDash(m.brand) }} {{ m.model || '' }}
                <template v-if="m.location"> · {{ m.location }}</template>
              </span>
            </el-radio>
          </el-radio-group>
        </div>

        <div v-else>
          <el-radio-group v-model="pickedId" class="picker-grid">
            <el-radio v-for="h in hosts" :key="h.id" :value="h.id" border class="picker-radio">
              <span class="picker-code">{{ h.asset_code }}</span>
              <span class="picker-sub">
                {{ h.device_type_name }} · 使用人 {{ orDash(h.user_name) }}
                <template v-if="h.location"> · {{ h.location }}</template>
              </span>
            </el-radio>
          </el-radio-group>
          <div v-if="!hosts.length" class="pair-empty" style="padding: 20px 0">
            没有其它可选的主机
          </div>
        </div>

        <el-input
          v-model="pickerNote"
          placeholder="备注（可选），例如 新同事入职加一台"
          clearable
          style="margin-top: 14px"
        />
      </div>

      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <el-button @click="pickerOpen = false">取消</el-button>
          <el-button type="primary" :loading="acting" @click="submitPicker">确定</el-button>
        </div>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.pair-panel {
  padding-bottom: 16px;
}

.count-chip {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-2);
  background: #f2f3f5;
  border-radius: 999px;
  padding: 2px 9px;
}

.pair-body {
  padding: 0 20px;
}

.pair-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pair-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
}

.pair-item.highlight {
  border-color: #c9d3e8;
  background: #fafbff;
}

.type-badge.sm {
  width: 32px;
  height: 32px;
  font-size: 15px;
  border-radius: 8px;
}

.pair-item .info {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.pair-item .info:hover .c1 {
  color: var(--primary);
}

.pair-item .info .c1 {
  font-size: 13.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.pair-item .info .c2 {
  font-size: 12px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pair-empty {
  padding: 8px 0 4px;
  color: var(--text-2);
  font-size: 13px;
}

.pair-empty .sub {
  margin-top: 3px;
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.7;
}

.pair-actions {
  margin-top: 12px;
}

.pair-history {
  margin-top: 14px;
  padding: 0 20px;
}

.history-toggle {
  border: 0;
  background: transparent;
  padding: 0;
  font-size: 12.5px;
  color: var(--text-2);
  cursor: pointer;
  font-family: inherit;
}

.history-toggle:hover {
  color: var(--primary);
}

.history-table {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.history-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  font-size: 12.5px;
  border-bottom: 1px solid var(--border);
}

.history-row:last-child {
  border-bottom: 0;
}

.history-row .badge {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11.5px;
}

.history-row .badge.on {
  background: #e8f7ee;
  color: #0f7b3f;
}

.history-row .badge.off {
  background: #f2f3f5;
  color: #7a7f87;
}

.history-row .text {
  font-variant-numeric: tabular-nums;
}

.history-row .time {
  margin-left: auto;
  color: var(--text-3);
  white-space: nowrap;
}

.history-row .who {
  color: var(--text-3);
  width: 60px;
  text-align: right;
  flex-shrink: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  max-height: 46vh;
  overflow-y: auto;
}

.picker-radio {
  width: 100%;
  height: auto;
  margin: 0 !important;
  padding: 10px 12px;
  align-items: flex-start;
}

.picker-radio :deep(.el-radio__label) {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.picker-code {
  font-size: 13.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: var(--text-1);
}

.picker-sub {
  font-size: 12px;
  color: var(--text-3);
  line-height: 1.6;
  white-space: normal;
}
</style>
