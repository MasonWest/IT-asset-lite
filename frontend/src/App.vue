<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  appState,
  effectiveBaseUrl,
  loadOverride,
  loadSystemInfo,
  saveOperator,
  saveOverride,
} from '@/stores/app'

const settingsOpen = ref(false)
const overrideInput = ref('')
const operatorInput = ref('')

onMounted(async () => {
  await loadSystemInfo()
  overrideInput.value = loadOverride()
  operatorInput.value = appState.operator
})

const baseDisplay = computed(() => effectiveBaseUrl() || '未探测到局域网地址')
const isOverridden = computed(() => Boolean(appState.override))

function openSettings() {
  overrideInput.value = loadOverride()
  operatorInput.value = appState.operator
  settingsOpen.value = true
}

async function saveSettings() {
  saveOverride(overrideInput.value)
  saveOperator(operatorInput.value)
  settingsOpen.value = false
  ElMessage.success('已保存')
}

async function refreshInfo() {
  await loadSystemInfo(true)
  ElMessage.success('已重新探测')
}
</script>

<template>
  <div class="app-shell">
    <header class="appbar">
      <div class="brand" @click="$router.push('/')">
        <span class="logo">IT</span>
        <span class="name">IT 资产管理系统</span>
      </div>

      <nav>
        <RouterLink to="/">资产台账</RouterLink>
        <RouterLink to="/pairings">配对管理</RouterLink>
        <RouterLink to="/inventory">盘点</RouterLink>
        <RouterLink to="/import">批量导入</RouterLink>
        <RouterLink to="/labels">标签打印</RouterLink>
        <RouterLink to="/audit">审计日志</RouterLink>
      </nav>

      <div class="right">
        <button class="operator-chip" :class="{ unset: !appState.operator }" @click="openSettings">
          <span class="ic">👤</span>
          <span class="txt">{{ appState.operator || '设置操作人' }}</span>
        </button>
        <span class="net-chip" :title="`扫码地址：${baseDisplay}`">
          <i class="dot" />
          {{ baseDisplay.replace(/^https?:\/\//, '') }}
        </span>
        <el-button link :title="'系统信息 / 操作人 / 扫码地址设置'" @click="openSettings">
          <span style="font-size: 16px">⚙</span>
        </el-button>
      </div>
    </header>

    <main class="page-host">
      <RouterView />
    </main>

    <el-dialog v-model="settingsOpen" title="操作人与扫码地址" width="min(520px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="当前操作人">
          <el-input
            v-model="operatorInput"
            placeholder="你的名字，例如 张伟"
            clearable
            @keyup.enter="saveSettings"
          />
          <div style="font-size: 12px; color: var(--text-3); line-height: 1.7; margin-top: 6px">
            系统没有账号体系，领用、归还、报废、配对、盘点这些操作都要记「谁做的」，
            所以这里填一次，之后所有操作都会带上。只存在你这台设备的浏览器里。
          </div>
        </el-form-item>

        <el-form-item label="自动探测的局域网访问地址">
          <div style="width: 100%">
            <div class="url-box" style="margin: 0 0 8px">
              {{ appState.baseUrl || '未探测到' }}
            </div>
            <div style="font-size: 12px; color: var(--text-3); line-height: 1.7">
              网卡 IP：{{ appState.lanIp || '—' }} · 端口：{{ appState.port || '—' }}
            </div>
          </div>
        </el-form-item>

        <el-form-item label="自定义扫码地址（可选）">
          <el-input v-model="overrideInput" placeholder="例如 http://192.168.1.23:8080" clearable />
          <div style="font-size: 12px; color: var(--text-3); line-height: 1.7; margin-top: 6px">
            电脑装了 VPN、虚拟机或 WSL 时，自动探测可能取到用不了的网卡地址。
            手机上扫码打不开的话，把这里改成电脑真实的局域网 IP + 端口即可。留空表示自动探测。
          </div>
        </el-form-item>
      </el-form>

      <el-descriptions :column="1" size="small" border style="margin-top: 4px">
        <el-descriptions-item label="版本">v{{ appState.version || '—' }}</el-descriptions-item>
        <el-descriptions-item label="数据库文件">
          <span style="word-break: break-all">{{ appState.databaseFile || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="备份目录">
          <span style="word-break: break-all">{{ appState.backupDir || '—' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="扫码地址来源">
          <el-tag v-if="isOverridden" type="warning" size="small">使用自定义地址</el-tag>
          <el-tag v-else type="success" size="small">自动探测</el-tag>
        </el-descriptions-item>
      </el-descriptions>

      <template #footer>
        <div style="display: flex; gap: 8px; justify-content: flex-end">
          <el-button @click="refreshInfo">重新探测</el-button>
          <el-button @click="overrideInput = ''">清空地址</el-button>
          <el-button type="primary" @click="saveSettings">保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>
