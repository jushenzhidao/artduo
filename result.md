# 交付结果 · 即梦复刻「补全功能」

时间：2026-11-09 00:05 – 01:00（无人值守）
入口：`http://127.0.0.1:8788/`
代码目录：`/Users/betterme/WorkBuddy/2026-11-08-01-29-52/jimeng-clone/`

---

## 一、完成清单

| 阶段 | 内容 | 状态 | 产出 |
| --- | --- | --- | --- |
| P0 | 基线核查 | 完成 | `server.py --selftest` 102/102；各套件基线复核 |
| P1 | 创作页真实视频生成（文生 / 图生视频） | 完成 | `app.js` 视频链路（并行进程交付，自检 52/52） |
| P2 | 探索 + 资产 | 完成 | `views.js` / `views.css`、`media_lib.py`、`/api/library*` |
| P3 | AI 工具（裁切 / 压缩 / 抽帧 / GIF） | 完成 | `media_tools.py`、`/api/tools/*` |
| P4 | 主体库（含回收站与恢复） | 完成 | `media_lib.py` 主体段、`/api/subjects*` |
| P5 | 动效工作室（图片 → 运镜视频） | 完成 | `views.js` motion 视图、复用 `/api/video` |
| P6 | Agent 工作台（只规划，不自动执行） | 完成 | `/api/agent`、`normalize_agent`、Agent 视图 |
| P7 | 全量回归 + 文档 + 截图 | 完成 | 见第四节；`qa/views-note.md` |

### 新增 / 修改的文件

| 文件 | 变化 |
| --- | --- |
| `views.js` | 新增 ~1500 行：五个视图 + Agent 工作台 + 导航拦截 + 桥接 |
| `views.css` | 新增 ~500 行：与既有 token 一致的深/浅色、三档响应式 |
| `media_lib.py` | 新增：媒体扫描、提示词索引、收藏、回收站、主体库 |
| `media_tools.py` | 新增：PIL 裁切/压缩、ffmpeg 抽帧/转 GIF |
| `server.py` | 最小注入：导入两模块、放行 views.*、新增 `/api/library*`、`/api/subjects*`、`/api/tools/*`、`/api/agent`；`/api/video` 支持无首帧（后者由并行进程完成） |
| `index.html` | 两行：`views.css` / `views.js` |
| `app.js` | 6 行 bridge：`JimengBridge.setPrompt / setMode / addRefFiles`（另含并行进程的视频链路改动） |
| `tools/views_check.py` | 新增 52 项 UI 自检 |
| `tools/library_check.py` | 新增 54 项接口/对抗自检 |
| `tools/agent_unit.py` | 新增 12 项纯函数自检 |
| `tools/views_smoke.py` | 新增 5 入口烟测 |

---

## 二、功能说明（按页面）

### 探索 · 本机作品广场
浏览 `assets/generated/` 里的真实文件（当前 1036 个：1029 图 / 7 视频），按提示词或文件名搜索，按推荐（真实优先）/最新/最早/体积/时长排序，
收藏、看详情、复制提示词、下载、**做同款**（回填提示词与模式到创作页）。
提示词来自生成日志；没有记录就显示「（没有提示词记录）」，不编造。
**明确不伪造社区内容**：没有他人作品、点赞、关注。

### 资产
全页资产管理：全部/图片/视频/收藏/回收站五个页签；多选后可批量收藏、取消收藏、下载、移入回收站；
回收站可逐个恢复。**没有永久删除入口**——移入回收站是 `assets/generated/` → `assets/_trash/` 的同盘移动，
恢复后字节哈希一致。

### AI 工具
四个本地确定性工具，均不调用生成模型、不消耗积分：

| 工具 | 实现 | 参数 |
| --- | --- | --- |
| 智能裁切 | PIL 等比裁切（cover） | 8 种比例、5 种重心 |
| 图片压缩 | PIL JPEG | 质量 30–95、长边 800/1280/1600/2048、可选目标 KB |
| 视频抽帧 | ffmpeg | 首帧 / 中间 / 指定秒 |
| 视频转 GIF | ffmpeg palettegen | 帧率 8/12/16、宽度 320/480/640、起点/时长 |

选素材支持「从资产选择（可搜索）」或上传；结果自动落盘并出现在「最近处理」。

### 主体
把角色/产品参考图存成主体（名称、备注、1–9 张图、自动 512px 封面）。
「用于创作」把参考图解析成 File 后通过 bridge 放进创作页参考区（第 1 张为首帧/画布）。
删除 = 整个参考图目录移入 `_trash/subject-<id>-<ts>/`，页面下方「已删除的主体」可一键恢复。

### 动效工作室
选图（资产或上传）→ 选运镜预设（推近/拉远/横移/环绕/呼吸/风）→ 可改运镜描述 →
提交 `/api/video`（首帧=所选图，720p/8s 免费档，由服务端裁 9:16）→ 轮询到成片 → 播放/下载。
计费档未开启，按钮置灰并写明原因。

### Agent 工作台
创作页切到「Agent 模式」后出现入口；对话 → LLM 返回 `reply` + 1–3 条 `plan` →
每条方案有「填入创作页」按钮，只回填提示词/模式，**提交仍由用户按发送**，Agent 不会自动花费额度。
`JIMENG_AGENT=off|mock|auto` 可关闭 / 用确定性 mock / 走真实 LLM。

---

## 三、契约与安全（摘要）

- 媒体库 / 工具 / 主体 / Agent 的完整契约见 `qa/views-note.md`。
- 文件名只接受 basename + 白名单正则；`resolve()` 后做目录 containment 校验，`../`、绝对路径、符号链接全部 400。
- 工具输入只来自 `assets/generated/`、`assets/subjects/` 或本次上传解码出的临时文件；临时文件用后即删。
- 输出名服务端生成（`tool-<ts>-<rand>`），前端不能指定路径或覆盖已有文件。
- 回收站只有移动与恢复，没有 `rm`；主体删除同样可恢复。
- Agent 不调用生成接口，只输出文本方案。

---

## 四、验证结果

| 套件 | 结果 | 报告 |
| --- | --- | --- |
| `server.py --selftest` | **102/102** | 终端 |
| `config_test.py` | **59/59** | `qa/config-report.json` |
| `drama_check.py`（指向离线实例 8790） | **36/36** | `qa/drama-report.json` |
| `storyboard_check.py` | **71/71** | `qa/storyboard-report.json` |
| `optimize_check.py` | **24/24** | `qa/optimize-report.json` |
| `refs_ui_check.py` | **17/17** | `qa/refs-report.json` |
| `refs_proxy_check.py` | **9/9** | `qa/refs-proxy-report.json` |
| `video_check.py` | **69/69** | `qa/video-report.json` |
| `concurrency_check.py` | **30/30** | `qa/concurrency-report.json` |
| `llm_check.py`（A 段，零成本） | **16/16** | `qa/llm-report.json` |
| `e2e_bsk.py`（25 形态 × 全链路） | **25/25** | `qa/e2e-report.json` |
| `cloud_reel_check.py`（线上 reel.1task.cn） | **22/22** | `qa/cloud-reel-report.json` |
| `gen_video_check.py`（创作页视频） | **52/52** | `qa/genvideo-report.json` |
| `views_check.py`（五视图 + Agent） | **52/52** | `qa/views-report.json` |
| `library_check.py`（接口 + 对抗） | **54/54** | `qa/library-report.json` |
| `agent_unit.py`（输出收敛） | **12/12** | `qa/agent-unit-report.json` |
| **合计** | **650 项断言全绿** | |

截图：`qa/shots/views/`（`50-final-explore`、`51-final-subjects`、`52-final-tools`、
`12-assets-select`、`14-tools-crop`、`16b-subject-refs`、`17-motion`、`20-agent`、
`18-explore-390`、`19-assets-light`）。

复跑命令（需要几个本地实例）：

```bash
# 纯函数 / 零依赖
python3 server.py --selftest
python3 tools/agent_unit.py

# 扩展视图（需要 8801：JIMENG_REAL=0 JIMENG_AGENT=mock）
PORT=8801 JIMENG_REAL=0 JIMENG_MAX_CALLS=0 JIMENG_AGENT=mock python3 server.py &
python3 tools/views_check.py
python3 tools/library_check.py

# 创作页视频（自带 8884/8885 + mock avm）
python3 tools/gen_video_check.py
```

---

## 五、真实调用口径（重要）

- 本轮**没有发起任何真实的图片 / 视频生成请求**：所有生成链路验证都指向本地 mock 上游
  （`127.0.0.1:8799` 图片、`8795` 视频），`calls` 计数不增加，零计费。
- 有两次**真实 LLM** 调用是误跑 `drama_check.py` 默认目标（8788）造成的：它默认连真实文本模型，
  返回 6 集导致 4 项断言失败；改为 `DRAMA_BASE=http://127.0.0.1:8790`（`JIMENG_LLM=off`）后 36/36。
  文本调用不产生图片/视频额度，但已在 `decisions.md` 记录。
- Agent 的真实 LLM 路径只用 `JIMENG_AGENT=mock` 验证了结构与 UI；真实模型输出未在本轮实测
  （避免无人值守下的额外调用）。第一次点击由你确认。
- 视频计费档（1080p/10s）全程关闭：`AVM_ALLOW_PAID` 未设置，接口与 UI 双重拦截。

---

## 六、并行执行说明

本轮工作区内检测到另一个执行进程（00:05–00:45 多次写入 `app.js` / `styles.css` / `server.py`，
并运行 `tools/gen_video_check.py`）。为避免互相覆盖，采取了文件级分工：

- 对方：创作页「视频生成」全链路（`app.js` / `styles.css` 的视频相关改动）。
- 我方：五个扩展入口 + Agent（新增文件为主；`server.py` 只加独立路由；`app.js` 只加 6 行 bridge）。

最终代码是两边能力的并集；两边的自检均通过（视频 52/52、扩展 52/52）。
详细时间线见 `decisions.md` 与 `progress.md`。

---

## 七、验收路径

1. 打开 `http://127.0.0.1:8788/`（若服务未起：`cd jimeng-clone && JIMENG_MAX_CALLS=50 python3 server.py`）。
2. 左侧导航依次点：探索 → 资产 → AI 工具 → 主体 → 动效工作室。
3. 创作页切「视频生成」，可直接文生视频（免费档 720p/8s）；切「Agent 模式」→「Agent 工作台」。
4. 想验证真实出图/出片：在创作页发一次提示词（图片）或在动效工作室点生成（视频，免费档）。
5. 报告与证据：`result.md`（本文件）、`decisions.md`、`blockers.md`、`qa/views-note.md`、`qa/shots/views/`。

---

## 八、决策摘要（完整日志见 `decisions.md`）

| 我替你做的决定 | 理由 | 影响 |
| --- | --- | --- |
| 全程不发真实图片/视频生成请求 | 无人值守无法逐次确认，用户红线要求发前确认 | 所有生成验证走 mock，零计费 |
| 范围＝5 个占位入口 + 视频 + Agent；不碰会员/支付/消息 | 只补「明确写着占位」的功能缺口；资金类无后端依据 | 商业入口仍是占位 |
| 资产/主体只做软删除（移入 `_trash`，可恢复） | 无人值守下的永久删除不可逆 | 没有「永久删除」入口 |
| 视频计费档保持关闭 | 会真实扣额度 | UI 与接口双重拦截 |
| 视频免费档固定 720p/8s，不给 4–15s 假选项 | 上游免费窗口只有这一档 | 时长选择器如实标注 |
| 探索定位为本机作品广场，不伪造社区 | 克隆产品不能凭空造他人作品 | 页面明确写「不联网、不冒充」 |
| 与并行进程做文件级分工（它管 app.js 视频，我管新增视图） | 两个写入者同改大文件必互相覆盖 | 交付是两边并集；bridge 仅 6 行 |
| 测试产物统一移入回收站，不永久删 | 保持作品库干净且可回滚 | 回收站 126 个（可恢复） |
| `save_subject` 改为「全部校验通过才写 store」 | 旧实现失败会留下「未命名 0 图」幽灵主体 | 创建失败不再污染主体库 |
| 回收站列表不再逐文件 ffprobe | 126 个文件时接口慢到十几秒 | 回收站打开 <50ms |

## 九、未解决事项（完整见 `blockers.md`）

1. **并行进程可能再次覆盖 `app.js` 的 6 行 bridge**：已确认当前在；若「主体 → 用于创作」提示未开放接口，按 `blockers.md` 第 1 节补回。
2. **真实图片/视频链路本轮未实跑**：凭据与历史实测在 `qa/*-note.md`；你回来后点一次即可确认。
3. **Agent 真实 LLM 路径未实测**：只用 mock 验证了结构与 UI；首次点击由你确认。
4. **主体恢复接口需要最新 `server.py`**：交付前已重启 8788 并验证；若 404，重启服务即可。
5. **回收站不会自动清理**：126 个测试文件留在 `assets/_trash/`，需要时手动处理。


---

## 十、结论

五个原本只弹「界面占位功能」的入口，现在是**真实可用的本机功能**；Agent 模式从占位变成了
「先规划、后确认」的工作台；创作页视频生成已接真实链路（并行进程完成）。
全部 650 项自检通过，零真实生成消耗，破坏性操作全部可逆。
