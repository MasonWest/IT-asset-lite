# 配置项（环境变量）

全部有默认值，**不改也能跑**。改法二选一：

```bat
set IT_ASSET_PORT=18080        :: 只在当前窗口生效
setx IT_ASSET_PORT 18080       :: 写进用户环境变量，之后都生效
```

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `IT_ASSET_PORT` | `8080` | 服务端口 |
| `IT_ASSET_HOST` | `0.0.0.0` | 监听地址，改成 `127.0.0.1` 就只有本机能访问 |
| `IT_ASSET_PUBLIC_BASE_URL` | 自动探测 | 强制指定二维码里用的地址，例如 `http://192.168.1.23:8080` |
| `IT_ASSET_DATA_DIR` | `backend/data` | 数据库文件存放目录 |
| `IT_ASSET_BACKUP_DIR` | `backups` | 备份目录（`backup.bat` 和设置弹窗都读它） |
| `IT_ASSET_SEED_ON_STARTUP` | 关闭 | 设成 `1` 才在启动时自动灌演示数据（演示 / 截图用） |
| `IT_ASSET_SKIP_SEED` | 关闭 | 设成 `1` 则连设备类型字典也不自动建（只建表，全空） |
| `IT_ASSET_FRONTEND_DIST` | `frontend/dist` | 前端构建产物目录 |
| `IT_ASSET_TRUST_PROXY` | 关闭 | 设成 `1` 才信任 `X-Forwarded-For` 里的客户端 IP |
| `IT_ASSET_DATABASE_URL` | `sqlite:///…/it_assets.db` | 换数据库时用（PostgreSQL 等） |

## 三个容易踩的点

1. **`IT_ASSET_TRUST_PROXY` 默认关**：审计日志里的 IP 直接来自连接，不信请求头。
   前面挂了反向代理又不打开这个开关，所有审计都会记成同一个 IP（代理的）。
   反过来，**没挂代理却打开了它**，客户端就能伪造 `X-Forwarded-For` 骗审计。
2. **`IT_ASSET_PUBLIC_BASE_URL` 影响二维码内容**：不设时按当前网卡自动探测，
   机器上有 VPN / 虚拟机时可能探到用不了的地址（这正是"手机扫不出来"最常见的原因）。
   界面上「⚙ 设置 → 自定义扫码地址」等价于这个变量，且改完立刻生效、不用重启。
3. **改端口不用改二维码**：端口变了，二维码地址会自动跟着变。

端口与局域网相关的排查见 [局域网与手机访问](../user/network.md)。
