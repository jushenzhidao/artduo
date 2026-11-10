# artduo.1task.cn · 即梦复刻站

## 已部署事实（2026-11-09 17:05 CST）

| 项 | 值 |
| --- | --- |
| 入口 | `https://artduo.1task.cn/`（公开，无密码） |
| 访问控制 | 无 Basic Auth；读接口开放，写接口（改配置）在 nginx 层 403 |
| DNS | 原 A 记录 `64.178.99.49` → 已改为 `210.121.44.245`（阿里云，TTL 600） |
| 证书 | Let's Encrypt EC-256，签发 2026-11-09，到期 2027-02-07；acme.sh cron 每天 2/8/14/20 点检查续期 |
| 容器 | `artduo:1.0.0`，healthy，restarts=0；端口 `127.0.0.1:39210 → 8080` |
| 初始作品 | 36 个（30 图 + 2 条 H3 真视频 + 4 条整集） |
| 真实生成 | 开启：图片硬上限 50 次/进程，视频 12 次/进程，计费档关闭；本地账本 2000 积分 |
| 凭据探测 | BFL `/v1/models` 200、LLM `/v1/models` 200、AVM 任务查询 404 TaskNotFound（cookie 有效），均未产生生成费用 |

## 机器事实


| 项 | 值 |
| --- | --- |
| 机器 | ai-prod `210.121.44.245`（首尔，64C/48G） |
| 目录 | `/opt/artduo` |
| 容器 | `artduo`，`python:3.12-slim` + ffmpeg + fonts-noto-cjk，非 root uid 10001 |
| 端口 | `127.0.0.1:39210 → 8080`（只绑回环，公网只走 nginx） |
| 域名 | `https://artduo.1task.cn`（Let's Encrypt，acme.sh HTTP-01） |
| 鉴权 | 无登录；`/api/config`、`/api/quickcfg` 仅允许 GET，写操作 403 |
| 数据 | `/opt/artduo/data/{generated,subjects,trash,qa}` |

## 目录

```
/opt/artduo
├── app/                 # 代码 + Dockerfile（compose build context）
├── data/
│   ├── generated/       # 生成图/视频（nginx 直出）
│   ├── subjects/        # 主体参考图（nginx 直出）
│   ├── trash/           # 回收站（nginx 404，不对外）
│   └── qa/              # 日志、billing.db、library-store.json、缓存
├── nginx/
│   ├── artduo.1task.cn.conf       # :80 ACME + 301
│   ├── artduo.1task.cn-ssl.conf   # :443 入口
│   ├── apply-https.sh             # 一键签发 + 投 vhost + 验证矩阵
│   ├── .htpasswd                  # 已弃用（保留文件，nginx 不再引用）
├── docker-compose.yml
└── .env                 # 密钥与运行参数（600）
```

## 常用操作

```bash
# 起/更新
cd /opt/artduo && docker compose up -d --build

# 看状态 / 日志
docker compose ps
docker logs --tail 100 artduo

# 改运行参数（例如关掉真实生成）后重启
vi /opt/artduo/.env      # JIMENG_REAL=0
docker compose up -d

# 重新签发/续期/校验入口
bash /opt/artduo/nginx/apply-https.sh

# 如需重新加访问密码（改完 reload）
htpasswd -c /opt/artduo/nginx/.htpasswd artduo   # 再在 ssl.conf 打开 auth_basic 两行
# 改上游配置：直接改 /opt/artduo/.env 后 docker compose up -d
/www/server/nginx/sbin/nginx -t && /www/server/nginx/sbin/nginx -s reload
```

## 运行参数（.env）

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `JIMENG_PUBLIC_HOSTS` | `artduo.1task.cn` | 允许的公网 Host；同源判定要求 Origin 精确匹配 |
| `JIMENG_REAL` | `1` | 1=真实生成；0=纯演示（零出站） |
| `JIMENG_MAX_CALLS` | `50` | 单进程图片调用硬上限（≈ $1.9） |
| `JIMENG_MAX_VIDEOS` | `12` | 单进程视频任务硬上限（H3 免费档） |
| `JIMENG_LLM` | `auto` | 剧本/分镜/Agent 的文本模型 |
| `JIMENG_AGENT` | `auto` | Agent 规划；`off` 关闭 |
| `JIMENG_BILLING_MODE` | `auto` | 上游可读（new-api）就用上游额度拦截；读不到退化为只展示（shadow） |
| `JIMENG_CREDITS_PER_USD` | `100` | 1 美元 = 100 积分（1 积分 = $0.01 = 5000 quota） |
| `JIMENG_MODELS` | `flux-3-image,doubao-seedream-5-0-flash-260915` | 多模型，逗号分隔；首个为默认，单价逐个从上游 `/api/pricing` 取 |
| `BFL_BASE` | `https://apiduo.cn` | 出图上游（new-api 实例，带 `/api/pricing` 与 billing 端点） |
| `AVM_ALLOW_PAID` | `0` | 视频计费档关闭（1080p/10s 不可用） |

密钥：`BFL2OPENAI_API_KEY`、`LLM_KEY`、`AVM_COOKIE` 只存在 `/opt/artduo/.env`（600），
通过 compose `env_file` 注入容器；不进 git、不进镜像。

## 公网边界（nginx 层）

- `/api/{config,quickcfg}` → 只读 200、写 403（`limit_except GET`）；`/api/{trace,logs,build}` → 404。
- `/api/billing` 与 `POST /api/billing/sync` → 200（只读额度/单价，含 5s 缓存 + 失败短缓存）。
- `/api/tasks` 只允许 POST（建任务）；不允许 GET 列最近任务（会带别人的提示词与结果）。
- `/assets/_trash/` → 404（回收站内容不对外）。
- `/assets/generated/`、`/assets/subjects/` 由 nginx 直出并缓存 1 天。
- 无 Basic Auth；配置类接口只读（GET），写操作 403。

## 验证矩阵

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: artduo.1task.cn' http://127.0.0.1/       # 301
curl -s -o /dev/null -w '%{http_code}\n' https://artduo.1task.cn/                            # 401
curl -s -u artduo:<pass> https://artduo.1task.cn/api/health                                  # {"ok": true}
curl -s -u artduo:<pass> https://artduo.1task.cn/api/status | head -c 300
curl -s -u artduo:<pass> -o /dev/null -w '%{http_code}\n' https://artduo.1task.cn/api/trace  # 404
```

## 已知边界

1. 没有多用户体系：所有访问者共用同一份素材库、积分与任务队列；公网可达即共享（当前为公开演示站）。
2. 视频免费档依赖 `AVM_COOKIE`（网页会话），会过期；过期后视频提交报错，重新抓 cookie 写进 `.env` 重启。
3. 任务队列在内存里，容器重启后排队中的任务丢失（上游任务继续跑）。
4. 回收站不对外，但页面里回收站预览会 404；恢复操作仍可用。
5. `data/qa` 里有 billing.db 与日志，不要当静态目录暴露（nginx 未映射）。
