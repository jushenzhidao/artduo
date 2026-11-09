# ARTDUO

**两个视角，一个现场。** ARTDUO 是 LIN 与 QIAO 的双人线上艺术现场，首展「柔软机械 / Soft Machines」。

- 线上入口：<https://artduo.1task.cn>
- 镜像：`ghcr.io/jushenzhidao/artduo`（public，节点无需 docker login）
- 部署：node-10 / Docker Compose（127.0.0.1:39120，nginx 反代 + Let's Encrypt）

## 本地开发

```bash
npm run dev        # http://127.0.0.1:4173
npm run build      # 生成 dist/
npm run preview    # 预览 dist/
```

没有运行时依赖；构建脚本只做静态文件拷贝，`npm install` 不是必需的。

## 结构

```text
index.html              页面骨架（语义化、无框架）
src/style.css           视觉系统：墨黑 / 米纸 / 朱砂；暗亮双主题、响应式
src/main.js             作品数据、筛选、作品详情、主题、滚动揭示
public/works/*.svg      程序化生成的作品与艺术家肖像
scripts/build.mjs       静态构建到 dist/
scripts/serve.mjs       零依赖本地静态服务器
Dockerfile              node build → nginx:alpine
deploy/                 compose、容器 nginx、node-10 nginx 与部署脚本参考
.github/workflows/      push main 自动 patch 发版、推 GHCR、部署 node-10
```

## 发版与部署

推送 `main` 即触发 `.github/workflows/release.yml`：

1. 读取最新 `v*` tag，patch 自增（零 tag 时从 `v0.0.1` 起）。
2. `npm run build`，生成 `artduo-<version>.tar.gz`。
3. 构建并推送三份镜像 tag：`<version>` / `latest` / `sha-<short>`。
4. 创建 GitHub Release（附 dist 包），移动 `latest` git tag。
5. SSH 到 node-10，执行 `/opt/artduo/bin/deploy.sh <version>`：
   `docker compose pull` → `up -d` → 健康检查。

node-10 上只需要：

```text
/opt/artduo/docker-compose.yml   # image: ghcr.io/jushenzhidao/artduo:${IMAGE_TAG}
/opt/artduo/bin/deploy.sh        # 拉镜像、重启、健康检查
```

部署用户 `artduo` 在 `docker` 组内，只能操作自己的 compose 项目；GitHub Actions 使用专用 ed25519 key，不复用运维 fleet key。

## 端到端验收

```bash
# HTTP 层：状态码、资源、缓存、压缩、安全头、TLS、跳转、体积
node scripts/e2e.mjs --base https://artduo.1task.cn

# 本机无公网直连时，可走隧道并指定解析地址
node scripts/e2e.mjs --base https://artduo.1task.cn:8446 --resolve-to 127.0.0.1

# 浏览器层（本地辅助脚本，需 Chrome 远程调试端口）
python3 qa/e2e-browser.py --port 9453 --base https://artduo.1task.cn:8446
```

`scripts/e2e.mjs` 零依赖，失败会以非零状态退出，适合放进 CI 做发布后验收。

## 手动部署

```bash
# 在 node-10
sudo -u artduo /opt/artduo/bin/deploy.sh 0.1.0
```

回滚：把版本号换成上一个 release 的 tag（镜像 tag 不带 `v`），再执行一次即可。
