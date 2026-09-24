import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'assets',
      component: () => import('@/views/AssetListView.vue'),
      meta: { title: '资产台账' },
    },
    {
      path: '/asset/:id',
      name: 'asset-detail',
      component: () => import('@/views/AssetDetailView.vue'),
      props: true,
      meta: { title: '设备详情' },
    },
    {
      path: '/pairings',
      name: 'pairings',
      component: () => import('@/views/PairingView.vue'),
      meta: { title: '配对管理' },
    },
    {
      path: '/inventory',
      name: 'inventory',
      component: () => import('@/views/InventoryListView.vue'),
      meta: { title: '盘点' },
    },
    {
      path: '/inventory/:id',
      name: 'inventory-detail',
      component: () => import('@/views/InventoryDetailView.vue'),
      props: true,
      meta: { title: '盘点详情' },
    },
    {
      path: '/labels',
      name: 'labels',
      component: () => import('@/views/LabelPrintView.vue'),
      meta: { title: '标签打印' },
    },
    {
      path: '/import',
      name: 'import',
      component: () => import('@/views/ImportView.vue'),
      meta: { title: '批量导入' },
    },
    {
      path: '/audit',
      name: 'audit',
      component: () => import('@/views/AuditView.vue'),
      meta: { title: '审计日志' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  const title = (to.meta?.title as string) || 'IT 资产管理系统'
  document.title = to.name === 'assets' ? 'IT 资产管理系统' : `${title} · IT 资产`
})

export default router
