import { cpSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.cwd();
const dist = resolve(root, 'dist');

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

cpSync(resolve(root, 'index.html'), resolve(dist, 'index.html'));
cpSync(resolve(root, 'src'), resolve(dist, 'src'), { recursive: true });
if (existsSync(resolve(root, 'public'))) {
  cpSync(resolve(root, 'public'), dist, { recursive: true });
}

// 资源内容版本号：CSS/JS 变化时 URL 跟着变，才能安全使用长缓存
const { readFileSync, writeFileSync } = await import('node:fs');
const { createHash } = await import('node:crypto');
const hash = createHash('sha256');
for (const file of ['src/style.css', 'src/main.js']) {
  hash.update(readFileSync(resolve(dist, file)));
}
const assetVersion = hash.digest('hex').slice(0, 10);
const htmlPath = resolve(dist, 'index.html');
const html = readFileSync(htmlPath, 'utf8');
if (!html.includes('__ASSET_V__')) {
  throw new Error('index.html is missing __ASSET_V__ placeholder');
}
writeFileSync(htmlPath, html.replaceAll('__ASSET_V__', assetVersion));
console.log(`asset version ${assetVersion}`);

const count = (await import('node:fs')).readdirSync(resolve(dist, 'works')).length;
console.log(`built dist/ (${count} artworks)`);
