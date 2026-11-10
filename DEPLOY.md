# 部署：https://artduo.1task.cn/

## 结论

已上线，**公开访问，无需密码**：

- 地址：`https://artduo.1task.cn/`
- 打开即是即梦复刻页（创作 / 探索 / AI 工具 / 资产 / 主体 / 短剧 / 动效 / Agent）
- 顶部状态徽章应显示 `真实生成 · flux-3-image`

> 2026-11-09 17:20 修正：此前整站挂了 Basic Auth，浏览器拿到的是 401，页面根本打不开。
> 现已移除密码，改为「读接口放行 + 写接口拒绝」。

## 关键事实

| 项 | 值 |
| --- | --- |
| 机器 | ai-prod `210.121.44.245`（首尔） |
| 目录 | `/opt/artduo` |
| 容器 | `artduo:1.0.0`，healthy，`127.0.0.1:39210 → 8080` |
| DNS | `artduo.1task.cn` A → `210.121.44.245`（原 `64.178.99.49` 已替换） |
| HTTPS | Let's Encrypt EC-256，到期 2027-02-07；acme.sh 每天 4 次检查续期 |
| 数据 | `/opt/artduo/data/{generated,subjects,trash,qa}` |
| 初始作品 | 36 个（30 图 + 2 条 H3 视频 + 4 条整集） |

## 运行口径

- 真实图片生成开启，硬上限 **50 次/进程**；视频 **12 次/进程**；视频计费档关闭。
- 图片任务可并发提交：页面最多同时跑 **4 个**（取服务端 `concurrency`），提交后输入框立即清空、
  可以接着写下一个提示词；同一批全部结束后留在「任务队列」视图，方便一次对比多张结果。
  视频仍是独占任务，需等图片队列跑完。
- 本地账本 `2000` 积分（38 积分/图），`billing.db` 持久化；重启不重置账本，但会重置进程调用计数。
- 密钥只存在 `/opt/artduo/.env`（600），不进镜像、不进 git。
- 公网边界（2026-11-09 17:20 调整）：
  - **放行读**：`/api/status`、`/api/models`、`/api/config`(GET)、`/api/quickcfg`(GET)、`/api/library*`、`/api/subjects`、`/api/billing`、`/api/tools/*`
  - **禁止写**：`POST/DELETE /api/config`、`POST /api/quickcfg` 一律 403；改上游请改 `.env` 重启
  - **仍然 404**：`/api/{trace,logs,build}`、`/api/config/test`、`/api/billing/sync`、`/assets/_trash/`
  - `/api/tasks` 只允许 POST
  - 无 Basic Auth

## 常用命令

```bash
# 状态 / 日志
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 'cd /opt/artduo && docker compose ps && docker logs --tail 50 artduo'

# 改代码后更新
#   本地改完 → 重新打包 app/ → scp 到 /opt/artduo/app → docker compose up -d --build
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 'cd /opt/artduo && docker compose up -d --build'

# 关掉真实生成（零计费）
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 "sed -i 's/^JIMENG_REAL=.*/JIMENG_REAL=0/' /opt/artduo/.env && cd /opt/artduo && docker compose up -d"

# 换访问密码
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 'htpasswd -c /opt/artduo/nginx/.htpasswd artduo && /www/server/nginx/sbin/nginx -s reload'

# 重新签发/校验入口
ssh -i ~/.ssh/id_ed25519_ops root@210.121.44.245 'bash /opt/artduo/nginx/apply-https.sh'
```

## 部署包

本地 `deploy/artduo/ai-prod/`：

- `Dockerfile`、`.dockerignore`、`docker-compose.yml`
- `nginx/artduo.1task.cn.conf`（:80）、`nginx/artduo.1task.cn-ssl.conf`（:443）、`nginx/apply-https.sh`
- `README.md`：完整运维说明与验证矩阵

## 验证记录（2026-11-09 17:05 CST）

| 检查 | 结果 |
| --- | --- |
| `:80` → 301 | ✅ |
| `:443` 无密码 → 401 | ✅ |
| 带密码首页 | ✅ 200，标题「即梦 AI · 一站式 AI 创作平台（界面复刻）」 |
| `/api/health` | ✅ `{"ok": true}` |
| `/api/library?limit=1` | ✅ 36 个（30 图 / 6 视频） |
| `/api/trace`、`/api/config`、`/api/logs` | ✅ 404（已封） |
| `/api/tasks` GET / POST | ✅ 403 / 400（只允许建任务） |
| `/api/tools/crop` 写文件 | ✅ 生成 PNG，nginx 直出 200，随后可移入回收站 |
| 静态资源 `app.js/views.js/views.css/logo.svg` | ✅ 200，MIME 正确 |
| 证书链 | ✅ `ssl_verify_result=0` |
| 容器 | ✅ healthy，restarts=0 |
| BFL / LLM / AVM 凭据 | ✅ 均为只读探测通过，未产生生成费用 |

## 已知边界

1. 没有多用户体系：拿到密码的人共用同一份素材库、积分额度与任务队列。
2. 视频依赖 `AVM_COOKIE`（网页会话），会过期；过期后重新抓 cookie 写 `.env` 并 `docker compose up -d`。
3. 真实出图/出片本轮只做了凭据与接口探测，**没有实跑一次生成**（避免无人确认的计费）；你首次点击即真实验证。
4. 回收站不对外（nginx 404），页面里回收站预览会坏，但恢复操作可用。
5. 任务队列在内存里，容器重启后排队中的任务丢失；账本与日志在 `data/qa` 持久化。
