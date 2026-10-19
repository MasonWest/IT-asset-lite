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
      path: '/insights',
      name: 'insights',
      component: () => import('@/views/InsightsView.vue'),
      /**
       * 问题中心。**放在「资产台账」之后** —— 它是"该关注什么"的总览入口，
       * 看完总览再钻进台账/地图处理具体对象，符合使用顺序。
       */
      meta: { title: '问题中心' },
    },
    {
      path: '/asset/:id',
      name: 'asset-detail',
      component: () => import('@/views/AssetDetailView.vue'),
      props: true,
      meta: { title: '设备详情' },
    },
    {
      /**
       * 轻资产（不纳入正式台账的 IT 配件）。
       * 与正式资产台账**平级**而不是它的子页 —— 两边的统计口径刻意分开，
       * 混在一起会让"150 台设备"这种数字永远说不清包不包括键鼠套装。
       */
      path: '/light',
      name: 'light',
      component: () => import('@/views/LightListView.vue'),
      meta: { title: '轻资产' },
    },
    {
      /**
       * ⚠️ 必须排在 `/light/:categoryId` **之前**：Vue Router 按声明顺序匹配，
       * 否则 `/light-help` 会被 `:categoryId` 当成一个品类 id 吃掉，进不去这一页。
       * （同一个坑项目里已经记过一次 —— 见 `/floor-maps/default` 与 `/assets/grouped` 的注释。）
       */
      path: '/light-help',
      name: 'light-help',
      component: () => import('@/views/LightHelpView.vue'),
      meta: { title: '轻资产怎么用' },
    },
    {
      path: '/light/:categoryId',
      name: 'light-category',
      component: () => import('@/views/LightCategoryView.vue'),
      props: true,
      meta: { title: '轻资产品类' },
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
      /**
       * vgpu / WebGPU 视觉实验（技术验证）。
       *
       * 刻意**不进主导航**、不挂任何业务数据 —— 它是一次可独立移除的探针，
       * 而不是一个功能页面。移除方式见 src/lab/vgpu/README.md。
       */
      path: '/lab/vgpu',
      name: 'lab-vgpu',
      component: () => import('@/lab/vgpu/VgpuLabView.vue'),
      meta: { title: 'WebGPU 实验' },
    },
    {
      /**
       * 图标预览（开发期工具页）。
       *
       * 与 `/lab/vgpu` 同样的性质：**不进主导航、不挂业务数据**，
       * 是一次可独立移除的探针。用途是图标改造期用真实浏览器核对
       * 全部图标在 16/20/30px 下的形状 —— 改完即可删。
       */
      path: '/lab/icons',
      name: 'lab-icons',
      component: () => import('@/lab/icons/IconLabView.vue'),
      meta: { title: '图标预览' },
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
