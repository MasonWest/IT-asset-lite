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
      path: '/map',
      name: 'space-map',
      component: () => import('@/views/SpaceMapView.vue'),
      /**
       * `flush: true` = 这一页**满铺**，不走 `main.page-host` 的阅读宽度与内边距。
       *
       * 地图不是"一屏文字"：`max-width: 1240px` + `padding: 24px 20px 64px`
       * 是为台账/列表这类阅读型页面定的，套到地图上会把它截成一个居中窄块，
       * 并且总高超出视口、凭空多出一条纵向滚动条（原型是满铺的，所以接入后"整体缩小了"）。
       */
      meta: { title: '空间地图', flush: true },
    },
    {
      /**
       * 平面图编辑器。独立页而不是 `/map` 上的模式：
       * `/map` 手机端固定区占掉大半，塞不下绘图工具条；且"编辑"与"看地图"
       * 该是两件事，避免只想看看时误触改坐标。
       */
      path: '/map/editor',
      name: 'map-editor',
      component: () => import('@/views/MapEditorView.vue'),
      meta: { title: '编辑平面图', flush: true },
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
