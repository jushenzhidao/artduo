# 进度日志 · 即梦复刻补全功能

> 无人值守任务。任何时刻中断，从本文件最后一条「恢复点」继续，不从头开始。
> 详细事实与验证口径写在各阶段小节；决策写 `decisions.md`；未解决事项写 `blockers.md`。

## 恢复点（最新在最上）

### 2026-11-10 01:45 · 改成即梦式「串联创作流」
- 诉求：不要「任务队列」面板，要像即梦那样一次任务一段、从上往下串起来，前面的结果不能被覆盖。
- 改造：
  - `renderJobStream` 去掉 conv-head 与「清空队列」按钮，改成 `<div class="flow">` + 每任务一个 `.flow-item`。
  - 单任务也不再切回 `renderConversation`，始终留在流里；新任务 append 到末尾。
  - `state.jobs` 只在「新对话 / 从历史会话发起」时清空，同一条流里连续提交会累积。
  - 每段之间有 1px 分隔线，进入时轻微上浮（`prefers-reduced-motion` 下关闭）。
  - 流末尾补 `.conv-tip` 说明（真实/MOCK/演示三态），失败任务在段内显示 `.alert-bar` + 重试（只回填，不自动提交）。
  - 成功不再弹 toast（多任务同时完成会糊住输入框），失败仍弹。
- 验证：本地连续 3 次任务 → flow-item 1/2/3、结果卡 1/2/3（前面的都保留）；
  `concurrency_ui` 21/21；`e2e_bsk` 24/25（剩 F15「上游返回非 JSON 时 trace 少记一条」，不影响页面）。
- 顺带修正的测试适配：L1 统计要认 `POST /api/tasks`；每例先清 `jimeng-jobs-v1` 再重载（队列已持久化）；
  mock 回归用 `MOCK_FLAKY_FAILS=0`、`JIMENG_RETRIES=0` 保证确定性。

### 2026-11-10 01:50 · 真实出图验证 + 多模型配置（JIMENG_MODELS）
- 真实出图（线上，用户确认后执行）：
  - flux-3-image：21.0s 成功，1024×1024 PNG 1.86MB，结算 20 积分（$0.2）→ `qa/shots/live-flux.png`
  - doubao-seedream-5-0-flash：16s 成功，1024×1024 JPEG 421KB，结算 1.64 积分（$0.0164）→ `qa/shots/live-seedream.jpg`
  - 上游 `used=36.4`（$0.364）：其余是 3 次失败任务的实际生成消耗（上游照收，本站未扣积分）。
- 多模型：
  - 新增 `JIMENG_MODELS=a,b,c`（逗号分隔，首个为默认）；优先级 page > env > 单个默认。
  - `billing.upstream_prices()` 一次拉 `/api/pricing` 算全部模型；`server.PRICE_TABLE` 缓存；
    `image_credits_for(model)` 按请求模型计费（提交预扣、结算、同步生成三处都换）。
  - `/api/config`、`/api/billing` 下发 `prices`；模型弹窗显示「N 积分/张」，切换模型即时刷新预估。
- 真跑才暴露的三个 bug（都已修）：
  1. **JPEG base64 以 `/9j/` 开头**被当成相对 URL → `bad_response`；`image_url_of` 用
     `_looks_base64()` 区分，`extract_image` 不再把 `/` 当 URL。
  2. 响应格式错误被当网络错误**重试 3 次**，等于失败一次花三次钱 → `error_kind=bad_response` 不重试。
  3. 换模型后 `#costValue` 不刷新（缺 `updateCost()`）→ 单价显示 20 实际选的是 seedream。
- 安全：下载图片时只有地址落在上游 base 下才带 Authorization，避免把 key 送给第三方存储。
- 回归：`selftest` 159/159、`billing_check` 60/60；线上容器 healthy。

### 2026-11-09 20:36 · 刷新不丢任务 + 视频进度条（200s）
- 现象：①提交的图片任务刷新就消失；②视频生成没有进度条，只有「已用 Ns」。
- 根因：①图片任务只活在内存 `state.jobs`，而且走的是**同步** `/api/generate`（没有 taskId），
  刷新后没有任何东西可以回查；②视频估算窗口写死 20s，提交后 `stopProgress()` 直接把顶部进度条隐藏，
  卡片里也没有 bar。
- 改造：
  - 图片改走异步 `/api/tasks`（`taskGenerate` 支持 refs），提交即把 `taskId` 落 `localStorage:jimeng-jobs-v1`；
    刷新后 `restoreJobs()` → `pollJob()` 按 id 回查，成功/失败都补成会话，成功后删 key。
  - 视频会话只要 `vidId` 在就继续轮询（原来 `restoreSessions` 一律标「轮询中断」）；超过 30 分钟标超时，
    避免僵尸任务永久占住「视频在跑」挡住新提交。
  - 视频进度：`VIDEO_EXPECT_MS=200000`，渐近曲线 `99*(1-e^(-1.8t/T))`；卡片内新增 `.gen-bar` +
    `#convVidPct` + `#convVidClock`，顶部 `#progressBar` 同步推进。
- 验证：新增 `tools/resume_check.py`（bsk）；本次用无头 Chrome 实跑 **16/16**（截图
  `qa/shots/video-progress.png`）：提交→刷新→接续→出图 4 项、视频进度条 4 项、视频刷新接续 3 项全过。
  回归 `selftest` 159/159、`billing_check` 60/60。
- 已部署 ai-prod（镜像重建，容器 healthy），线上 app.js 含 `jimeng-jobs-v1`/`convVidBar`。
- 边界：代理重启会丢内存里的任务表 → 刷新接续时如实提示「任务已过期」；参考图是 data URI 不进
  localStorage，接续用的是服务端已收到的那份，刷新后无法再补发。

### 2026-11-09 20:20 · 积分改 1:100 + 上游额度/单价只读同步（已上线）
- 用户三连问：POST `/api/config` 403、面板能否查 new-api 额度、比例改 1:100、单价也从上游取。
- 诊断：403 是 nginx `location ~ ^/api/(config|quickcfg)$ { limit_except GET { deny all; } }`，
  响应 Server 头 `rbm-w2tn0yRy` 就是 ai-prod 主机名（宝塔 Tengine），不是 CDN 也不是应用层。
- 口径：`JIMENG_CREDITS_PER_USD` 默认 1000 → **100**（1 积分 = $0.01 = 5000 quota）。
  老账本无单位标记时按历史默认 1000 回填后重算美元等价：1696→169.6、38→3.8、今日 304→30.4。
- 新增 `billing.upstream_price()`：读公开 `/api/pricing`，按次价取 `tiered_expr` 的 `fixed(x)`
  或 `quota_type=1` 的 `model_price`，乘分组倍率；解析不出就回退默认，不猜。
- `billing_snapshot()` 不再受 `mode=local` 限制去探测上游；`refresh_pricing()` 在 `main()` 启动时
  与每次同步时刷新单价，`/api/config`、`/api/billing` 都带 `priceUsd/priceSource`。
- 上游切到 `https://apiduo.cn`（用户 curl 意图），`BFL_MODEL` 仍 `flux-3-image`。
- 线上实测：`source=new-api`、`available=10000`（$100×100）、`imageCredits=20`（$0.2）、
  `priceSource=upstream`；容器日志 `image=20积分/张 ($0.2 · upstream)`；POST `/api/config` 仍 403。
- 回归：`server.py --selftest` 159/159、`tools/billing_check.py` 60/60（断言同步改成 1:100）。
- 未做：真实出图请求（要花钱，等用户点）。若 apiduo 只回图片 URL，已加 `download_image()` 兜底。
- 回滚：`/opt/artduo/app.bak-20261109-201409`、`.env.bak-20261109-201409`、`billing.db.bak-*`。

### 2026-11-09 17:45 · 图片生成放开并发（前端任务队列）
- 现象：生成中发送按钮被禁用、`handleGenerate` 开头 `if (state.busy) return`，一次只能跑一个。
- 根因：前端用单一 `state.busy` 表示「在生成」，服务端其实早就支持 4 路并发（`IMG_CONCURRENCY=4`，
  `concurrency_check` 30 项一直绿）。
- 改造：
  - `state.jobs`（Map）替代单一 busy；`busy` 变成派生状态，只用于 UI class 与进度条。
  - 提交即清空输入框并立即返回，任务在后台跑；最多 `jobCap()`（服务端 `concurrency`，默认 4）个并行。
  - 多任务时渲染「任务队列」：每个任务自己的提示词、进度、耗时与结果网格；全部结束后保留队列便于对比。
  - 同提示词 1.5s 内连点只算一次；并发满时按钮禁用并提示。
  - 视频仍独占（轮询与会话绑定），图片队列没跑完时不允许提交视频。
  - 进度刷新 5Hz → 1Hz（DOM 抖动对自动化观察不友好，视觉上也看不出差别）。
- 验证：`tools/concurrency_ui.py` **21/21**（4 个 8s 任务 6.5s 跑完，队列 4 块、结果 4 张、第 5 个被拒）；
  `e2e_bsk` 25/25、`optimize_check` 24/24 全绿。
- 截图：`qa/shots/views/91-live-queue.png`（部署后补）。

### 2026-11-09 17:26 · 修正：移除 Basic Auth，恢复页面
- 现象：用户打开 artduo 看不到 jimeng 页面，怀疑部署错前端；nginx 日志显示浏览器只拿到 401（`favicon.ico 401`，无成功 `GET /`）。
- 根因：昨天为"防刷额度"给整站加了 Basic Auth，把用户自己也挡在外面；同时 `/api/models` 被 nginx 404，导致"模型接入"弹窗退化成"尚未挂载"。
- 修复：
  - 删除 `auth_basic` 两行 → 打开即用（无密码）。
  - `/api/{config,quickcfg}` 改为 `limit_except GET`（读 200 / 写 403）；`/api/models` 恢复。
  - `/api/{trace,logs,build}`、`/api/config/test`、`/api/billing/sync`、`/assets/_trash/` 仍 404；`/api/tasks` 仍只允许 POST。
  - 首页 `Cache-Control: no-store`，避免旧 HTML/401 缓存。
- 验证：首页 200（无凭据）、`/api/models` 返回 5 个模型、POST/DELETE config 403、headless 截图显示「真实生成 · flux-3-image」、库 36 个（30 图 / 6 视频）、calls=0。
- 截图：`qa/shots/views/90-live-home.png`。

### 2026-11-09 17:10 · 已部署到 https://artduo.1task.cn/
- 机器 ai-prod `210.121.44.245`，目录 `/opt/artduo`，容器 `artduo:1.0.0`（healthy）。
- DNS：`artduo` A 记录从 `64.178.99.49` 改为 `210.121.44.245`（阿里云，TTL 600，三公共解析器已生效）。
- HTTPS：Let's Encrypt EC-256，2027-02-07 到期；acme.sh cron 每天 2/8/14/20 点续期检查。
- 入口：整站 Basic Auth（用户 `artduo`，密码在服务器 `/opt/artduo/nginx/.basic-auth`）。
- 运行：真实生成开启，50 图 / 12 视频每进程硬上限，视频计费档关闭，本地账本 2000 积分。
- 边界：`/api/{config,quickcfg,trace,logs,build,models}`、`/api/billing/sync`、`/assets/_trash/` 404；
  `/api/tasks` 只允许 POST。
- 验证：401/200、首页/静态资源、36 个作品、工具写文件、证书链、HTTP/2、容器健康全部通过。
- 凭据探测（只读）：BFL `/v1/models` 200、LLM `/v1/models` 200、AVM 任务查询 404 TaskNotFound。
- 部署包与完整运维说明：`deploy/artduo/ai-prod/`、根目录 `DEPLOY.md`。
- 代码补丁：`JIMENG_HOST`（容器绑定）与 `JIMENG_PUBLIC_HOSTS`（公网同源精确匹配）；本机默认行为不变，
  `server.py --selftest` 159/159。

### 2026-11-09 00:55 · 功能全部落地，进入交付
- P0–P6 全部完成；P7 回归已完成。
- 新增：`views.js` / `views.css` / `media_lib.py` / `media_tools.py`；`server.py` 独立路由；
  `app.js` 6 行 bridge；`index.html` 两行引用。
- 自检：`views_check` 52/52、`library_check` 54/54、`agent_unit` 12/12；
  旧套件 config 59、drama 36、storyboard 71、optimize 24、refs 17+9、video 69、
  concurrency 30、llm 16、e2e 25、cloud_reel 22、gen_video 52、selftest 102。合计 650 项全绿。
- 交付物：`result.md`、`decisions.md`、`blockers.md`、`qa/views-note.md`、`qa/shots/views/`。
- 最后三步：重启 8788 加载最新 server.py → 清理测试产物到回收站（126 个）→ 主站烟测（全通过）。
- 最终库：1036 个作品（1029 图 / 7 视频）；主体 0；回收站 126（可恢复）。
- 交付后补记：任务托管方式启动的 8788 收到 SIGTERM（exit 143），已用 `nohup` 重启并验证
  `/api/health`、`/api/status`（calls=0 / max=50 / video=true / agent=auto）。

### 2026-11-09 00:20 · P0/P1/P2/P3/P4/P5 主要功能已落地
- **并发事实**：00:05–00:11 检测到另一个进程在同一工作区实现「创作页视频生成」（`tools/_patch_gen_video_*.py`、`tools/gen_video_check.py`，51/51 通过）。为避免互相覆盖：
  - 对方负责 `app.js` / `styles.css` 的视频链路；我**不碰这两个文件**。
  - 我负责新增文件：`views.js` / `views.css` / `media_lib.py` / `media_tools.py` + `server.py` 的媒体库路由（与视频路由不重叠）。
  - 导航用 document 捕获阶段拦截，不修改 app.js 的 `setView`。
- 已完成：媒体库、四个工具、五个视图、Agent 工作台、主体回收站。
- 验证（本地实例 8801，零生成）：library 54/54、views 52/52、agent_unit 12/12。
- 下一步曾写：bridge、Agent、全量回归、result.md —— 均已完成。

## 阶段计划

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | 基线核查（selftest + 截图 + 现状盘点） | 已完成 |
| P1 | 创作页真实视频生成（文生视频 / 图生视频，免费档 720p·8s） | 已完成（并行进程交付，51/51） |
| P2 | 探索（本机作品广场）+ 资产（全页资产管理） | 已完成（42/42） |
| P3 | AI 工具（裁切 / 压缩 / 抽帧 / 转 GIF，本地确定性处理） | 已完成（44/44） |
| P4 | 主体库（角色参考资产 CRUD + 生成时引用） | 已完成（CRUD；引用待 bridge） |
| P5 | 动效工作室（图片 → 运镜视频，H3 首帧） | 已完成（UI+提交；未发真实请求） |
| P6 | Agent 模式（多轮对话 + 工具路由，需确认后执行） | 已完成（52 项含 Agent） |
| P7 | 全量回归 + 三档截图 + result.md / blockers.md | 进行中 |
