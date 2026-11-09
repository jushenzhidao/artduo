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

const count = (await import('node:fs')).readdirSync(resolve(dist, 'works')).length;
console.log(`built dist/ (${count} artworks)`);
