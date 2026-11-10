# 未解决事项 / 边界

按「会不会影响验收」排序。能在本轮自行绕过的，都写了绕法；剩下的写清楚需要什么。

## 1. 并行执行进程（已缓解，未彻底消除）

- **现象**：00:05–00:45 期间，同一工作区有另一个进程在改 `app.js` / `styles.css` / `server.py`
  并运行 `tools/gen_video_check.py`（它交付了创作页视频链路）。
- **本轮处理**：文件级分工——对方负责 `app.js`/`styles.css` 的视频改动，我方只新增
  `views.js` / `views.css` / `media_lib.py` / `media_tools.py`，`app.js` 只加 6 行 bridge。
  交付前已确认 bridge 与 `views.*` 引用都在（`JimengBridge` 在 `app.js:2931`）。
- **残余风险**：如果那个进程再次从旧副本整体重写 `app.js`，6 行 bridge 可能被覆盖，
  表现为「主体 → 用于创作」提示未开放参考区接口。
- **需要什么**：无。若出现该症状，重新加回 `window.JimengBridge = { setPrompt, setMode, addRefFiles }`
  即可（`progress.md` 有原文位置：`window.__qaApply = qaApply;` 之后）。

## 2. 真实图片 / 视频链路本轮未端到端实跑

- **原因**：无人值守无法在每次真实调用前取得确认；用户红线要求「发真实生成请求前确认」。
  本轮所有生成验证指向 mock 上游（图片 8799 / 视频 8795），`calls` 不增加。
- **已有证据**：历史真实链路记录在 `qa/storyboard-note.md`、`qa/video-note.md`、
  `qa/cloud-reel-note.md`（真实 flux 出图、H3 出片、云端拼接均有实测）。
- **需要什么**：你回来后点一次创作页发送（图片）或动效工作室生成（视频免费档），
  即可确认线上凭据仍有效。Agent 的真实 LLM 路径同理，第一次点击由你确认。

## 3. 两个旧测试套件的「非确定性」已修正，但依赖特定实例

- `drama_check.py` 默认连 `8788`（真实 LLM）。模型返回 6 集而不是请求的 10 集时，
  4 项断言会失败。**绕法**：`DRAMA_BASE=http://127.0.0.1:8790`（`JIMENG_LLM=off`），36/36。
- `e2e_bsk.py` 用 `qa/mock-upstream.jsonl` 的行号做基线；两次运行重叠时，上一轮迟到的请求
  会算进下一轮，出现 `L3=2`。**绕法**：跑前 `: > qa/mock-upstream.jsonl`，25/25。
- 这两处是测试隔离问题，不是产品缺陷；已在 `decisions.md` 记录，未改产品代码。

## 4. 主服务进程需要重启才能加载最新 `server.py`

- `8788` 当前进程启动于 00:35 左右，**不含** 00:46 之后新增的 `/api/subjects/restore`。
  静态文件（`views.js` / `app.js`）总是读磁盘，所以页面是新版；但主体恢复接口会 404。
- **本轮处理**：交付前重启 8788（`JIMENG_MAX_CALLS=50`，live 模式），并验证
  `/api/subjects` 返回 `trash` 字段。
- **踩坑**：用「后台任务」方式启动的实例会在本轮结束时收到 SIGTERM（exit 143）。已改用
  `nohup` 启动并验证存活。若你看到页面打不开，按下面命令重启：

  ```bash
  cd /Users/betterme/WorkBuddy/2026-11-08-01-29-52/jimeng-clone
  JIMENG_MAX_CALLS=50 nohup python3 server.py > qa/server-8788.log 2>&1 &
  ```

  看到 `/api/subjects/restore` 404，也说明进程是旧版，同样重启即可。

## 5. 测试产物与测试主体已清理，但回收站不会自动清空

- 本轮自检产生了 ~230 个 `tool-*` / `fixture-*` / `vid-cgt-mock-*` / `reel-ep*` 文件和
  几个测试主体；交付前已通过 `/api/library/trash` 移入 `assets/_trash/`（当前 126 个，可恢复），
  测试主体已清空（`subjects` 与 `subjectsTrash` 均为 0）。
- **没有做的事**：没有永久删除，也没有定时清理 `_trash/`。回收站会一直占磁盘
  （当前约几十 MB）。需要手动清空时，按 `assets/_trash/index.json` 逐个确认后再删。

## 6. 媒体库规模

- `assets/generated/` 当前约 900–1100 个文件。每次 `/api/library` 会 `scandir` 全目录，
  元数据有缓存，首屏约 0.2–0.4s；文件再多（>5000）需要 SQLite 索引或分页游标。
- **需要什么**：暂时不需要；如果作品上万，把 `media_lib.scan` 换成索引表即可。

## 7. 功能边界（明确没做，不是坏了）

| 项 | 现状 |
| --- | --- |
| 资产永久删除 | 不提供。只有回收站 + 恢复 |
| 主体跨设备同步 | 不做，只存本机 `assets/subjects/` |
| Agent 自动执行 / 自动重试 | 不做。只规划，提交必须人工按发送 |
| 视频计费档（1080p/10s） | 接口与 UI 双重拦截，`AVM_ALLOW_PAID` 未设置 |
| 动效时长/分辨率 | 固定 8s / 720p / 跟随首帧，不给假选项 |
| 工具任务队列 | 没有。4K 压缩 / 15s GIF 会占用请求线程数秒 |
| 短剧「转绘」「短剧画布」 | 仍是占位。属独立画布产品，超出本次补全范围 |
| 会员 / 支付 / 消息中心 | 仍是占位。涉及资金与账号，按决策不碰 |

## 8. 小概率 UI 问题

- 视频缩略图依赖 `#t=` 取帧：极短的视频（<0.2s）可能仍是黑帧；整集片已改为取 2.0s。
- 探索页对「没有提示词记录」的文件只显示文件名，这是日志缺失，不是加载失败。
- 浅色主题下 `vx-badge` 为半透明深底白字，强光下对比度偏低但可读；如需更高对比度，
  把 `views.css` 的 `.vx-badge` 背景改成不透明 `#1b1e24`。
