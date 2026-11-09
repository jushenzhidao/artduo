#!/usr/bin/env node
/**
 * ARTDUO HTTP 端到端验收（零依赖）。
 *
 *   node scripts/e2e.mjs --base https://artduo.1task.cn [--http-base http://artduo.1task.cn]
 *                        [--json qa/e2e-live.json] [--md qa/e2e-live-report.md]
 *
 * 只发只读 GET，不会改动线上任何状态。
 */
import { request as httpsRequest } from 'node:https';
import { request as httpRequest } from 'node:http';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { connect as tlsConnect } from 'node:tls';

const args = process.argv.slice(2);
const argOf = (name, fallback = '') => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};
const BASE = argOf('--base', 'https://artduo.1task.cn').replace(/\/+$/, '');
const HTTP_BASE = argOf('--http-base', '').replace(/\/+$/, '');
const JSON_OUT = argOf('--json', '');
const MD_OUT = argOf('--md', '');
const HOST = new URL(BASE).hostname;
const RESOLVE_TO = argOf('--resolve-to', '');          // 本机隧道场景：域名解析到 127.0.0.1
const TIMEOUT = Number(argOf('--timeout', '15000'));
const SKIP_TLS = args.includes('--no-tls');   // 本地预览（http）时跳过证书/ALPN 检查

// 隧道场景：把域名固定解析到本机，同时兼容 Node 的 all:true 查询
function tunnelLookup(hostname, options, cb) {
  if (options && options.all) return cb(null, [{ address: RESOLVE_TO, family: 4 }]);
  return cb(null, RESOLVE_TO, 4);
}

const checks = [];
const check = (name, ok, detail = '') => {
  checks.push({ name, ok: !!ok, detail: String(detail).slice(0, 400) });
  const mark = ok ? 'PASS' : 'FAIL';
  console.log(`${mark}  ${name}${detail ? ` — ${detail}` : ''}`);
  return !!ok;
};

function raw(path, { method = 'GET', headers = {}, base = BASE, redirect = false } = {}) {
  const url = new URL(base + path);
  const isHttps = url.protocol === 'https:';
  const mod = isHttps ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => {
    const req = mod({
      protocol: url.protocol,
      hostname: RESOLVE_TO ? HOST : url.hostname,
      port: url.port || (isHttps ? 443 : 80),
      path: url.pathname + url.search,
      method,
      headers: { 'user-agent': 'artduo-e2e/1.0', ...headers },
      servername: isHttps ? url.hostname : undefined,
      timeout: TIMEOUT,
      lookup: RESOLVE_TO ? tunnelLookup : undefined,
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const buf = Buffer.concat(chunks);
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: buf,
          text: buf.toString('utf8'),
          ms: Date.now() - started,
        });
      });
    });
    const started = Date.now();
    req.on('timeout', () => req.destroy(new Error(`timeout ${TIMEOUT}ms`)));
    req.on('error', reject);
    req.end();
  });
}

async function get(path, opts) {
  let lastErr;
  for (let i = 0; i < 3; i += 1) {
    try {
      return await raw(path, opts);
    } catch (err) {
      lastErr = err;
      await new Promise((r) => setTimeout(r, 300));
    }
  }
  throw lastErr;
}

const sha256 = (buf) => createHash('sha256').update(buf).digest('hex');
const kb = (n) => `${(n / 1024).toFixed(1)}KB`;

mkdirSync('qa', { recursive: true });

/* ---------------- 1. 首页 ---------------- */
const home = await get('/');
check('首页 200', home.status === 200, `HTTP ${home.status} · ${kb(home.body.length)} · ${home.ms}ms`);
check('HTML content-type', /^text\/html/.test(home.headers['content-type'] || ''), home.headers['content-type']);
check('标题正确', home.text.includes('<title>ARTDUO — 柔软机械 / Soft Machines</title>'),
  home.text.match(/<title>([^<]*)</)?.[1]);
check('语言 zh-CN', /<html[^>]+lang="zh-CN"/.test(home.text));
check('viewport 声明', /name="viewport"[\s\S]{0,80}width=device-width/.test(home.text));
check('描述 meta', /name="description"[\s\S]{0,200}content="[^"]{30,}/.test(home.text));
check('OG 标题/描述', /property="og:title"/.test(home.text) && /property="og:description"/.test(home.text));
const ogImage = home.text.match(/property="og:image"[\s\S]{0,60}?content="([^"]*)"/)?.[1] || '';
check('OG 图片为绝对 URL', /^https:\/\//.test(ogImage), ogImage || '(缺失)');
check('OG 图片可访问', (await get(new URL(ogImage).pathname)).status === 200,
  `${new URL(ogImage).pathname} · ${(await get(new URL(ogImage).pathname)).headers['content-type']}`);
check('Twitter 大图卡', /name="twitter:card"[\s\S]{0,60}summary_large_image/.test(home.text));
check('canonical 指向自身', /rel="canonical"[^>]+https:\/\/artduo\.1task\.cn\//.test(home.text));
check('OG 站点地址', /property="og:url"/.test(home.text),
  home.text.match(/property="og:url"[\s\S]{0,60}?content="([^"]*)"/)?.[1] || '(缺失)');
check('favicon 声明', /rel="icon"[^>]+favicon\.svg/.test(home.text));
const cssHref = home.text.match(/href="(\/src\/style\.css[^"]*)"/)?.[1] || '';
const jsSrc = home.text.match(/src="(\/src\/main\.js[^"]*)"/)?.[1] || '';
check('样式表引用带版本号', /\/src\/style\.css\?v=[\w.-]+/.test(cssHref), cssHref || '(缺失)');
check('模块脚本引用带版本号', /\/src\/main\.js\?v=[\w.-]+/.test(jsSrc), jsSrc || '(缺失)');
check('作品网格容器存在', /id="workGrid"/.test(home.text));
const filterCount = (home.text.match(/class="filter[ "]/g) || []).length;
check('筛选器 4 个', filterCount === 4, `${filterCount} 个`);
check('作品详情 dialog 存在', /<dialog[^>]+id="lightbox"/.test(home.text));
check('无占位/未渲染文本', !/\b(undefined|NaN|\[object Object\])\b/.test(home.text));
check('无内联密钥特征', !/(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,})/.test(home.text));

/* ---------------- 2. 资源 ---------------- */
const assets = [
  '/src/style.css', '/src/main.js', '/favicon.svg', '/og.svg', '/og.png',
  '/robots.txt', '/sitemap.xml', '/works/grain.svg',
  '/works/w1.svg', '/works/w2.svg', '/works/w3.svg', '/works/w4.svg', '/works/w5.svg',
  '/works/w6.svg', '/works/lin.svg', '/works/qiao.svg',
];
const assetInfo = {};
for (const path of assets) {
  const res = await get(path);
  assetInfo[path] = { status: res.status, bytes: res.body.length, type: res.headers['content-type'] };
  const type = res.headers['content-type'] || '';
  const typeOk = path.endsWith('.css') ? /text\/css/.test(type)
    : path.endsWith('.js') ? /javascript/.test(type)
      : path.endsWith('.png') ? /image\/png/.test(type)
        : path.endsWith('.txt') ? /text\/plain/.test(type)
          : /svg|xml/.test(type);
  check(`资源 ${path}`, res.status === 200 && typeOk && res.body.length > 20,
    `HTTP ${res.status} · ${kb(res.body.length)} · ${res.headers['content-type']}`);
}
const htmlRefs = [...home.text.matchAll(/(?:href|src)="(\/[^"#?]+)"/g)].map((m) => m[1]);
const missingRefs = [...new Set(htmlRefs)].filter((p) => !assetInfo[p] && !/\/(works)\//.test(p));
check('HTML 内引用均有响应', missingRefs.length === 0, missingRefs.join(', ') || `${new Set(htmlRefs).size} 条`);

if (cssHref.includes('?')) {
  const versionedCss = await get(cssHref);
  check('版本化 CSS 与源文件一致',
    versionedCss.status === 200 && versionedCss.body.length === assetInfo['/src/style.css'].bytes,
    `HTTP ${versionedCss.status} · ${versionedCss.body.length}B`);
}
const jsText = (await get('/src/main.js')).text;
const jsWorks = (jsText.match(/\bno:\s*'?\d+'?/g) || []).length;
check('脚本内作品数据 6 条', jsWorks >= 6, `${jsWorks} 条`);

/* ---------------- 3. 缓存与压缩 ---------------- */
const css = await get('/src/style.css', { headers: { 'accept-encoding': 'gzip' } });
check('CSS gzip 压缩', css.headers['content-encoding'] === 'gzip',
  `${css.headers['content-encoding'] || '未压缩'} · ${css.body.length}B`);
check('CSS 长缓存 immutable', /max-age=604800/.test(css.headers['cache-control'] || '')
  && /immutable/.test(css.headers['cache-control'] || ''), css.headers['cache-control']);
check('HTML 不缓存', /no-cache/.test(home.headers['cache-control'] || ''), home.headers['cache-control']);
const js = await get('/src/main.js');
check('JS 长缓存', /immutable/.test(js.headers['cache-control'] || ''), js.headers['cache-control']);
const svg = await get('/works/w1.svg');
check('SVG 长缓存', /immutable/.test(svg.headers['cache-control'] || ''), svg.headers['cache-control']);

/* ---------------- 4. 安全头 ---------------- */
const h = home.headers;
check('HSTS 已开启', /max-age=\d{6,}/.test(h['strict-transport-security'] || ''), h['strict-transport-security']);
check('nosniff', h['x-content-type-options'] === 'nosniff', h['x-content-type-options']);
check('Referrer-Policy', /strict-origin/.test(h['referrer-policy'] || ''), h['referrer-policy']);
check('不暴露 nginx 版本', !/nginx\/\d/.test(h.server || ''), h.server);

/* ---------------- 5. 错误路径 ---------------- */
const notFoundSvg = await get('/works/does-not-exist.svg');
check('缺失静态资源 404', notFoundSvg.status === 404, `HTTP ${notFoundSvg.status}`);
const robots = await get('/robots.txt');
check('robots.txt 声明 sitemap', robots.status === 200 && /Sitemap:\s*https:\/\//.test(robots.text), robots.text.trim().split('\n').pop());
const sitemap = await get('/sitemap.xml');
check('sitemap 含首页', sitemap.status === 200 && sitemap.text.includes('<loc>https://artduo.1task.cn/</loc>'));
const spa = await get('/missing-page');
check('未知页面回退首页（SPA）', spa.status === 200 && /ARTDUO/.test(spa.text),
  `HTTP ${spa.status} · ${spa.headers['content-type']}`);

/* ---------------- 6. TLS ---------------- */
if (!SKIP_TLS) await new Promise((resolve) => {
  const req = httpsRequest({
    hostname: RESOLVE_TO ? HOST : new URL(BASE).hostname,
    port: new URL(BASE).port || 443,
    path: '/',
    method: 'HEAD',
    servername: HOST,
    timeout: TIMEOUT,
    lookup: RESOLVE_TO ? tunnelLookup : undefined,
  }, (res) => {
    const cert = res.socket.getPeerCertificate();
    const days = Math.round((new Date(cert.valid_to) - Date.now()) / 86400000);
    check('证书域名匹配', (cert.subjectaltname || '').includes(HOST), cert.subjectaltname);
    check('证书有效期 > 30 天', days > 30, `${days} 天后到期（${cert.valid_to}）`);
    check('TLS 1.2+', /TLSv1\.[23]/.test(res.socket.getProtocol() || ''), res.socket.getProtocol());
    res.resume();
    resolve();
  });
  req.on('error', () => { check('TLS 握手', false, '连接失败'); resolve(); });
  req.end();
});

/* ---------------- 6b. HTTP/2 协商 ---------------- */
if (!SKIP_TLS) await new Promise((resolve) => {
  const socket = tlsConnect({
    host: RESOLVE_TO || HOST,
    port: new URL(BASE).port || 443,
    servername: HOST,
    ALPNProtocols: ['h2', 'http/1.1'],
    timeout: TIMEOUT,
  }, () => {
    check('HTTP/2 ALPN 协商', socket.alpnProtocol === 'h2', socket.alpnProtocol || '未协商');
    socket.end();
    resolve();
  });
  socket.on('error', (err) => { check('HTTP/2 ALPN 协商', false, err.message); resolve(); });
});

/* ---------------- 7. HTTP → HTTPS ---------------- */
if (HTTP_BASE) {
  try {
    const redirect = await get('/', { base: HTTP_BASE });
    check('HTTP 跳转 HTTPS', [301, 302, 308].includes(redirect.status)
      && String(redirect.headers.location || '').startsWith('https://'),
    `HTTP ${redirect.status} → ${redirect.headers.location || '(无 location)'}`);
  } catch (err) {
    check('HTTP 跳转 HTTPS', false, err.message);
  }
}

/* ---------------- 8. 体积预算 ---------------- */
const total = home.body.length + (assetInfo['/src/style.css']?.bytes || 0)
  + (assetInfo['/src/main.js']?.bytes || 0)
  + ['/works/w1.svg', '/works/w2.svg', '/works/w3.svg', '/works/w4.svg', '/works/w5.svg', '/works/w6.svg']
    .reduce((sum, p) => sum + (assetInfo[p]?.bytes || 0), 0);
check('首屏资源 < 600KB', total < 600 * 1024, kb(total));
check('HTML < 40KB', home.body.length < 40 * 1024, kb(home.body.length));
check('CSS < 50KB', (assetInfo['/src/style.css']?.bytes || 0) < 50 * 1024,
  kb(assetInfo['/src/style.css']?.bytes || 0));
const biggest = Object.entries(assetInfo).sort((a, b) => b[1].bytes - a[1].bytes)[0];
check('单文件 < 200KB', biggest[1].bytes < 200 * 1024, `${biggest[0]} ${kb(biggest[1].bytes)}`);

/* ---------------- 9. 指纹 ---------------- */
const fingerprints = {
  'index.html': sha256(home.body),
  'src/style.css': sha256(css.body),
  'src/main.js': sha256(js.body),
};

/* ---------------- 输出 ---------------- */
const passed = checks.filter((c) => c.ok).length;
const failed = checks.filter((c) => !c.ok);
const result = {
  base: BASE,
  httpBase: HTTP_BASE || null,
  at: new Date().toISOString(),
  passed,
  total: checks.length,
  failed: failed.map((c) => ({ name: c.name, detail: c.detail })),
  checks,
  assets: assetInfo,
  fingerprints,
  bytes: { home: home.body.length, css: assetInfo['/src/style.css']?.bytes, js: assetInfo['/src/main.js']?.bytes, firstScreen: total },
};

console.log(`\n${passed}/${checks.length} passed${failed.length ? ` · ${failed.length} failed` : ''}`);
for (const f of failed) console.log(`  FAIL ${f.name} — ${f.detail}`);

if (JSON_OUT) {
  mkdirSync(dirname(JSON_OUT), { recursive: true });
  writeFileSync(JSON_OUT, `${JSON.stringify(result, null, 2)}\n`);
  console.log(`json → ${JSON_OUT}`);
}
if (MD_OUT) {
  const rows = checks.map((c) => `| ${c.ok ? '✅' : '❌'} | ${c.name} | ${c.detail || ''} |`).join('\n');
  const md = `# ARTDUO 线上 HTTP 端到端验收

- 目标：${BASE}
- 时间：${result.at}
- 结果：**${passed}/${checks.length} 通过**
- 首屏资源：${kb(total)}（HTML ${kb(home.body.length)} / CSS ${kb(assetInfo['/src/style.css']?.bytes || 0)} / JS ${kb(assetInfo['/src/main.js']?.bytes || 0)}）
- 指纹（sha256）：
  - index.html \`${fingerprints['index.html'].slice(0, 16)}\`
  - style.css \`${fingerprints['src/style.css'].slice(0, 16)}\`
  - main.js \`${fingerprints['src/main.js'].slice(0, 16)}\`

| 结果 | 检查项 | 观测值 |
| --- | --- | --- |
${rows}
`;
  mkdirSync(dirname(MD_OUT), { recursive: true });
  writeFileSync(MD_OUT, md);
  console.log(`md → ${MD_OUT}`);
}

process.exit(failed.length ? 1 : 0);
