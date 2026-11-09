import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, resolve } from 'node:path';

const distMode = process.argv[2] === 'dist';
const root = process.cwd();
const base = distMode ? resolve(root, 'dist') : root;
const publicDir = resolve(root, 'public');
const port = Number(process.env.PORT || 4173);

const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.webmanifest': 'application/manifest+json; charset=utf-8'
};

createServer(async (req, res) => {
  try {
    const url = decodeURIComponent((req.url || '/').split('?')[0]);
    let file;
    if (!distMode && url.startsWith('/src/')) file = join(root, url);
    else if (!distMode && (url.startsWith('/works/') || url === '/favicon.svg' || url === '/og.svg')) file = join(publicDir, url);
    else file = join(base, url === '/' ? 'index.html' : url);
    file = resolve(file);
    if (!file.startsWith(base) && !file.startsWith(resolve(root, 'src')) && !file.startsWith(publicDir)) throw new Error('forbidden');
    let info = await stat(file).catch(() => null);
    if (info?.isDirectory()) { file = join(file, 'index.html'); info = await stat(file).catch(() => null); }
    if (!info) {
      // 与生产一致：无扩展名的未知路径回退首页，静态资源仍返回 404
      if (!extname(file)) {
        const index = join(base, 'index.html');
        const body = await readFile(index);
        res.writeHead(200, { 'content-type': types['.html'], 'cache-control': 'no-store' });
        res.end(body);
        return;
      }
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    let body = await readFile(file);
    if (extname(file) === '.html') {
      // 开发时没有构建步骤，用 dev 占位，保持与生产相同的 URL 形状
      body = Buffer.from(body.toString('utf8').replaceAll('__ASSET_V__', 'dev'));
    }
    res.writeHead(200, {
      'content-type': types[extname(file)] || 'application/octet-stream',
      'cache-control': distMode ? 'public, max-age=300' : 'no-store'
    });
    res.end(body);
  } catch {
    res.writeHead(500, { 'content-type': 'text/plain' });
    res.end('server error');
  }
}).listen(port, '127.0.0.1', () => console.log(`ARTDUO ${distMode ? 'preview' : 'dev'} http://127.0.0.1:${port}`));
