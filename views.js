/* ============================================================
   即梦复刻 · 补全功能视图
   探索 / 资产 / AI 工具 / 主体 / 动效工作室
   ------------------------------------------------------------
   设计原则
   1. 新视图独立成页（#vpage），不改动 app.js 的内部状态机；
      需要回创作页时用 DOM 事件桥接（JimengBridge 存在则优先用）。
   2. 所有数据来自本机服务：/api/library、/api/subjects、/api/tools/*。
      页面文案如实标注来源，不伪造社区内容，不冒充模型产物。
   3. 任何破坏性动作都可逆：资产只移入回收站，主体删除只移走参考图。
   ============================================================ */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ico = (id, cls = '') => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${id}"></use></svg>`;

  const V = {
    current: null, lib: null, subjects: [], subjectsTrash: [], sel: new Set(), selectMode: false,
    explore: { type: 'all', sort: 'featured', q: '', items: [], offset: 0, total: 0, loading: false },
    assets: { type: 'all', sort: 'new', q: '', items: [], offset: 0, total: 0, loading: false, trash: false },
    tools: { active: null, src: null, srcUrl: '', out: null },
    motion: { src: null, srcUrl: '', preset: 'push', prompt: '', busy: false, out: null, video: null },
    agent: { messages: [], busy: false },
    subject: { editing: null, images: [] },
  };

  // ---------------------------------------------------------------- 基础
  function toast(msg, kind = 'info') {
    let stack = $('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `${ico(kind === 'error' ? 'warn' : kind === 'success' ? 'check' : 'sparkle')}<span>${esc(msg)}</span>`;
    stack.appendChild(el);
    setTimeout(() => {
      el.classList.add('is-out');
      setTimeout(() => el.remove(), 320);
    }, kind === 'error' ? 4200 : 2600);
  }

  const fmtBytes = (n) => {
    if (!Number.isFinite(n)) return '—';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  };
  const fmtDate = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  };
  const fmtDur = (s) => (Number.isFinite(s) && s > 0 ? `${s.toFixed(s < 10 ? 1 : 0)}s` : '');
  // 卡片上隐去测试标记前缀（__MOCK_SLOW__ 等）；详情里仍显示原始提示词，便于审计
  const cleanPrompt = (p) => String(p || '').replace(/^__[A-Z0-9_]{2,}__\s*/, '');
  const srcLabel = (s) => ({
    real: '真实', mock: 'MOCK · 零计费', local: '本地拼接', tool: '本地工具', unknown: '本机',
  }[s] || '本机');

  async function api(path, body, opts = {}) {
    const init = body
      ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { cache: 'no-store' };
    const ctrl = opts.timeout ? AbortSignal.timeout(opts.timeout) : undefined;
    const res = await fetch(path, { ...init, signal: ctrl });
    const data = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
    if (!res.ok || data.ok === false) {
      const err = new Error(data.error || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function fileToDataURI(file, maxSide = 1280) {
    return new Promise((resolve, reject) => {
      if (!file) return reject(new Error('没有选择文件'));
      const isVideo = /^video\//.test(file.type);
      if (isVideo) {
        if (file.size > 24 * 1024 * 1024) return reject(new Error('视频上限 24MB'));
        const fr = new FileReader();
        fr.onload = () => resolve({ uri: String(fr.result), name: file.name, kind: 'video' });
        fr.onerror = () => reject(new Error('读取失败'));
        fr.readAsDataURL(file);
        return;
      }
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const cv = document.createElement('canvas');
        cv.width = w; cv.height = h;
        cv.getContext('2d').drawImage(img, 0, 0, w, h);
        URL.revokeObjectURL(url);
        resolve({ uri: cv.toDataURL('image/jpeg', 0.9), name: file.name, kind: 'image', w, h });
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('图片解码失败')); };
      img.src = url;
    });
  }

  async function urlToDataURI(url, maxSide = 1024, quality = 0.9) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('读取本机文件失败');
    const blob = await res.blob();
    if (/^video\//.test(blob.type)) throw new Error('视频不能作为首帧上传');
    const bmp = await createImageBitmap(blob).catch(() => null);
    if (!bmp) throw new Error('图片解码失败');
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const cv = document.createElement('canvas');
    cv.width = Math.max(1, Math.round(bmp.width * scale));
    cv.height = Math.max(1, Math.round(bmp.height * scale));
    cv.getContext('2d').drawImage(bmp, 0, 0, cv.width, cv.height);
    return cv.toDataURL('image/jpeg', quality);
  }

  // ---------------------------------------------------------------- 与主页面桥接
  function setPromptAndMode(prompt, mode) {
    const bridge = window.JimengBridge;
    if (bridge && typeof bridge.setPrompt === 'function') {
      bridge.setPrompt(prompt, mode);
      return true;
    }
    const ta = $('#prompt');
    if (!ta) return false;
    if (mode) {
      const tab = $(`.mode-tab[data-mode="${mode}"]`);
      if (tab && !tab.classList.contains('is-active')) tab.click();
    }
    ta.value = prompt;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }

  function backToCreate() {
    closePage();
    const ta = $('#prompt');
    if (ta) { ta.focus({ preventScroll: true }); ta.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
    $('#crumb') && ($('#crumb').textContent = '创作 / 默认创作');
    $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.nav === '创作'));
  }

  // ---------------------------------------------------------------- 页面骨架
  const META = {
    探索: { key: 'explore', title: '探索', sub: '本机作品广场：这台机器上真实生成过的图片与视频。不联网、不冒充社区内容。' },
    资产: { key: 'assets', title: '资产', sub: '统一管理本机产物：收藏、筛选、移入回收站（可恢复）、一键做同款。' },
    AI工具: { key: 'tools', title: 'AI 工具', sub: '四个确定性本地工具：裁切、压缩、抽帧、转 GIF。不调用生成模型，不消耗积分。' },
    主体: { key: 'subjects', title: '主体', sub: '把角色 / 产品参考图存成主体，下次创作直接引用。只存在这台机器上。' },
    动效工作室: { key: 'motion', title: '动效工作室', sub: '给一张静图加运镜：首帧就是所选图片，走 H3 免费档 720p · 8s。' },
    agent: { key: 'agent', title: 'Agent 工作台', sub: '先给方案，再执行：Agent 只做规划，不替你花额度；每条方案都要你回到创作页确认后才会提交。' },
  };

  function ensureRoot() {
    let el = $('#vpage');
    if (el) return el;
    el = document.createElement('section');
    el.id = 'vpage';
    el.className = 'vpage';
    el.hidden = true;
    el.setAttribute('aria-label', '扩展功能');
    el.innerHTML = '<div class="vpage-inner" id="vpageInner"></div>';
    const main = $('#main');
    (main || document.body).appendChild(el);
    return el;
  }

  function openPage(key) {
    const meta = Object.values(META).find((m) => m.key === key);
    if (!meta) return;
    const navName = { explore: '探索', assets: '资产', tools: 'AI工具', subjects: '主体', motion: '动效工作室' }[key];
    ensureRoot().hidden = false;
    $('#app').classList.add('view-vpage');
    $('#app').classList.remove('view-drama');
    const drama = $('#dramaView');
    if (drama) drama.hidden = true;
    $('#crumb') && ($('#crumb').textContent = `创作 / ${meta.title}`);
    V.current = key;
    $$('.nav-item').forEach((b) => b.classList.toggle('is-active', b.dataset.nav === navName));
    $('#app').classList.remove('mobile-open');
    const inner = $('#vpageInner');
    inner.innerHTML = `
      <header class="vp-head">
        <div><h1>${esc(meta.title)}</h1><p>${esc(meta.sub)}</p></div>
        <div class="vp-actions" id="vpActions"></div>
      </header>
      <div id="vpBody"></div>`;
    const root = $('#vpage');
    root.scrollTop = 0;
    if (key === 'explore') renderExplore();
    if (key === 'assets') renderAssets();
    if (key === 'tools') renderTools();
    if (key === 'subjects') renderSubjects();
    if (key === 'motion') renderMotion();
    if (key === 'agent') renderAgent();
  }

  function closePage() {
    const el = $('#vpage');
    if (el) el.hidden = true;
    $('#app').classList.remove('view-vpage');
    V.current = null;
  }

  // 拦截 5 个扩展入口；创作 / 短剧交还给 app.js
  document.addEventListener('click', (e) => {
    const nav = e.target.closest('.nav-item');
    if (nav) {
      const key = META[nav.dataset.nav]?.key;
      if (!key) { closePage(); return; }
      e.preventDefault();
      e.stopImmediatePropagation();
      openPage(key);
      return;
    }
    if (e.target.closest('#libraryBtn')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      openPage('assets');
    }
  }, true);

  // ---------------------------------------------------------------- 通用：详情弹层
  let dlgEl = null;
  function closeDialog() {
    if (dlgEl) { dlgEl.remove(); dlgEl = null; }
  }
  function openDialog(title, bodyHtml, { wide = false, onMount } = {}) {
    closeDialog();
    const layer = document.createElement('div');
    layer.className = 'vdlg-layer';
    layer.innerHTML = `<div class="vdlg-scrim"></div>
      <div class="vdlg" role="dialog" aria-modal="true" style="${wide ? 'width:min(1120px,100%)' : ''}">
        <div class="vdlg-head"><h3>${esc(title)}</h3>
          <button class="vp-btn ghost" data-dlg-close aria-label="关闭">${ico('close')}</button></div>
        <div class="vdlg-body"></div>
      </div>`;
    layer.querySelector('.vdlg-body').innerHTML = bodyHtml;
    layer.querySelector('.vdlg-scrim').onclick = closeDialog;
    layer.querySelector('[data-dlg-close]').onclick = closeDialog;
    document.body.appendChild(layer);
    dlgEl = layer;
    if (onMount) onMount(layer);
    return layer;
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && dlgEl) { e.stopPropagation(); closeDialog(); }
  }, true);

  function posterUrl(item) {
    const t = /^reel-/.test(item.id || '') ? 2.0 : 0.1;
    return `${item.url}#t=${t}`;
  }

  function mediaHtml(item, controls = false) {
    if (item.type === 'video' || /\.mp4($|\?)/.test(item.url)) {
      return `<video src="${esc(item.url)}" ${controls ? 'controls autoplay playsinline' : 'muted playsinline preload="metadata"'}></video>`;
    }
    return `<img src="${esc(item.url)}" alt="${esc(item.prompt || item.id)}" loading="lazy">`;
  }

  function openDetail(item) {
    const isVideo = item.type === 'video';
    const body = `<div class="vdlg-split">
      <div class="vdlg-media">${mediaHtml(item, true)}</div>
      <div class="vdlg-meta">
        <dl class="vdlg-kv">
          <dt>来源</dt><dd>${esc(srcLabel(item.source))}</dd>
          <dt>文件</dt><dd>${esc(item.id)}</dd>
          <dt>尺寸</dt><dd>${item.w && item.h ? `${item.w}×${item.h}` : '—'}${item.seconds ? ` · ${fmtDur(item.seconds)}` : ''}${item.fps ? ` · ${item.fps}fps` : ''}</dd>
          <dt>体积</dt><dd>${fmtBytes(item.bytes)}</dd>
          <dt>时间</dt><dd>${esc(fmtDate(item.at))}</dd>
        </dl>
        <div>
          <div class="vt-tag" style="margin-bottom:6px">提示词</div>
          <div class="vdlg-prompt">${item.prompt ? esc(item.prompt) : '未记录提示词（该文件没有对应的生成日志）'}</div>
        </div>
        <div class="vdlg-actions">
          <button class="vp-btn primary" data-d="same">${ico('magic')}做同款</button>
          <button class="vp-btn" data-d="download">${ico('download')}下载</button>
          ${isVideo ? '' : `<button class="vp-btn" data-d="subject">${ico('subject')}存为主体</button>`}
          <button class="vp-btn" data-d="fav">${ico('heart')}${item.fav ? '取消收藏' : '收藏'}</button>
          ${item.prompt ? `<button class="vp-btn ghost" data-d="copy">${ico('assets')}复制提示词</button>` : ''}
        </div>
        <p class="vt-note">${isVideo
          ? '视频文件由本机代理转存，播放不需要联网。'
          : '图片是上游返回后落盘的原文件；页面只做等比展示，不修改原图。'}</p>
      </div>
    </div>`;
    const layer = openDialog(isVideo ? '视频详情' : '作品详情', body, { wide: true });
    layer.querySelector('[data-d="download"]').onclick = () => {
      const a = document.createElement('a');
      a.href = item.url; a.download = item.id;
      document.body.appendChild(a); a.click(); a.remove();
    };
    layer.querySelector('[data-d="same"]')?.addEventListener('click', () => {
      setPromptAndMode(item.prompt || '', isVideo ? 'video' : 'image');
      closeDialog(); backToCreate();
      toast('已把提示词填回创作页', 'success');
    });
    layer.querySelector('[data-d="subject"]')?.addEventListener('click', async () => {
      try {
        toast('正在读取图片…');
        const uri = await urlToDataURI(item.url);
        closeDialog();
        openSubjectDialog(null, [uri]);
      } catch (err) { toast(err.message, 'error'); }
    });
    layer.querySelector('[data-d="fav"]').onclick = async () => {
      try {
        const r = await api('/api/library/fav', { id: item.id, fav: !item.fav });
        item.fav = r.fav;
        toast(r.fav ? '已收藏' : '已取消收藏', 'success');
        closeDialog();
        if (V.current === 'explore') renderExplore();
        if (V.current === 'assets') renderAssets();
      } catch (err) { toast(err.message, 'error'); }
    };
    layer.querySelector('[data-d="copy"]')?.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(item.prompt);
        toast('提示词已复制', 'success');
      } catch { toast('复制失败，请手动选择文本', 'error'); }
    });
  }

  // ---------------------------------------------------------------- 探索
  function gridCard(item, { selectable = false, uniform = false } = {}) {
    const isVideo = item.type === 'video';
    const ratioClass = uniform ? '' : (item.w && item.h ? (item.h / item.w > 1.15 ? 'tall' : item.w / item.h > 1.3 ? 'wide' : '') : '');
    return `<button class="vx-card ${V.sel.has(item.id) ? 'is-sel' : ''}" data-id="${esc(item.id)}" type="button">
      <div class="vx-media ${ratioClass}">
        ${isVideo
          ? `<video src="${esc(posterUrl(item))}" muted playsinline preload="metadata"></video><span class="vx-play">${ico('play')}</span>`
          : `<img src="${esc(item.url)}" alt="${esc(item.prompt ? item.prompt.slice(0, 40) : item.id)}" loading="lazy">`}
        <div class="vx-badges">
          <span class="vx-badge ${esc(item.source)}">${esc(srcLabel(item.source))}</span>
          ${item.fav ? '<span class="vx-badge">收藏</span>' : ''}
        </div>
        <span class="vx-fav ${item.fav ? 'is-fav' : ''}" data-fav="${esc(item.id)}" role="button" aria-label="收藏">${ico('heart')}</span>
        ${selectable ? `<span class="vx-check">${ico('check')}</span>` : ''}
        ${item.seconds ? `<span class="vx-dur">${fmtDur(item.seconds)}</span>` : ''}
      </div>
      <div class="vx-meta">
        <div class="vx-prompt">${esc(cleanPrompt(item.prompt) || '（没有提示词记录）')}</div>
        <div class="vx-sub"><span>${esc(fmtDate(item.at))}</span><span>${item.w && item.h ? `${item.w}×${item.h}` : ''}</span><span>${fmtBytes(item.bytes)}</span></div>
      </div>
    </button>`;
  }

  function bindGrid(root, items, { selectable = false, onOpen = openDetail } = {}) {
    $$('.vx-card', root).forEach((card) => {
      const item = items.find((x) => x.id === card.dataset.id);
      if (!item) return;
      card.onclick = (e) => {
        if (e.target.closest('[data-fav]')) return;
        if (selectable) {
          if (V.sel.has(item.id)) V.sel.delete(item.id); else V.sel.add(item.id);
          card.classList.toggle('is-sel', V.sel.has(item.id));
          window.__vaUpdateBatch?.();
          return;
        }
        onOpen(item);
      };
      const fav = $('[data-fav]', card);
      if (fav) fav.onclick = async (e) => {
        e.stopPropagation();
        try {
          const r = await api('/api/library/fav', { id: item.id, fav: !item.fav });
          item.fav = r.fav;
          fav.classList.toggle('is-fav', r.fav);
          card.querySelector('.vx-badges')?.classList.toggle('has-fav', r.fav);
          toast(r.fav ? '已收藏' : '已取消收藏', 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
    });
  }

  async function fetchLibrary(target, { trash = false, append = false } = {}) {
    if (target.loading) return;
    target.loading = true;
    try {
      if (trash) {
        const data = await api('/api/library/trash');
        target.items = data.items || [];
        target.total = data.total || 0;
      } else {
        const qs = new URLSearchParams({
          type: target.type === 'fav' ? 'all' : target.type,
          sort: target.sort, q: target.q,
          limit: '48', offset: String(append ? target.items.length : 0),
          ...(target.type === 'fav' ? { fav: '1' } : {}),
        });
        const data = await api(`/api/library?${qs}`);
        target.items = append ? target.items.concat(data.items) : data.items;
        target.total = data.total;
        target.counts = data.counts;
      }
    } catch (err) {
      toast(`读取媒体库失败：${err.message}`, 'error');
    } finally {
      target.loading = false;
    }
  }

  async function renderExplore() {
    const st = V.explore;
    const body = $('#vpBody');
    if (!body) return;
    body.innerHTML = `<div class="vp-hero">
        <h2>这台机器上的作品</h2>
        <p>探索页不复刻线上社区的信息流：没有他人作品，也不伪造点赞。这里展示的是 <code>assets/generated/</code> 里真实存在的文件，提示词来自生成日志；查不到就如实写「没有记录」。</p>
        <div class="vp-hero-stats" id="vxStats"><div class="vp-stat"><b>—</b><span>加载中</span></div></div>
      </div>
      <div class="vp-bar">
        <div class="vp-tabs" id="vxTabs">
          <button class="vp-tab ${st.type === 'all' ? 'is-active' : ''}" data-t="all">全部</button>
          <button class="vp-tab ${st.type === 'image' ? 'is-active' : ''}" data-t="image">图片</button>
          <button class="vp-tab ${st.type === 'video' ? 'is-active' : ''}" data-t="video">视频</button>
          <button class="vp-tab ${st.type === 'fav' ? 'is-active' : ''}" data-t="fav">收藏</button>
        </div>
        <select class="vp-select" id="vxSort" aria-label="排序">
          <option value="featured" ${st.sort === 'featured' ? 'selected' : ''}>推荐（真实优先）</option>
          <option value="new" ${st.sort === 'new' ? 'selected' : ''}>最新优先</option>
          <option value="old" ${st.sort === 'old' ? 'selected' : ''}>最早优先</option>
          <option value="big" ${st.sort === 'big' ? 'selected' : ''}>体积最大</option>
          <option value="duration" ${st.sort === 'duration' ? 'selected' : ''}>时长最长</option>
        </select>
        <label class="vp-search">${ico('explore')}<input id="vxQ" placeholder="搜索提示词或文件名" value="${esc(st.q)}"></label>
      </div>
      <div id="vxGrid" class="vx-grid"></div>
      <div class="vp-more" id="vxMore"></div>`;
    $('#vpActions').innerHTML = `<button class="vp-btn" id="vxRefresh">${ico('refresh')}刷新</button>`;
    $('#vxRefresh').onclick = () => { st.offset = 0; loadExplore(true); };

    const paint = () => {
      const grid = $('#vxGrid');
      if (!grid) return;
      grid.innerHTML = st.items.length
        ? st.items.map((x) => gridCard(x)).join('')
        : `<div class="vp-empty" style="grid-column:1/-1"><strong>这里还没有作品</strong>去创作页生成第一张图，或换一个筛选条件。</div>`;
      bindGrid(grid, st.items);
      const more = $('#vxMore');
      more.innerHTML = st.items.length < st.total
        ? `<button class="vp-btn" id="vxLoadMore">加载更多（${st.items.length} / ${st.total}）</button>`
        : (st.items.length ? `<span class="vt-tag">已显示全部 ${st.total} 个</span>` : '');
      const btn = $('#vxLoadMore');
      if (btn) btn.onclick = async () => { await loadExplore(false); };
    };
    const paintStats = () => {
      const c = st.counts || {};
      $('#vxStats').innerHTML = `
        <div class="vp-stat"><b>${c.all ?? '—'}</b><span>全部作品</span></div>
        <div class="vp-stat"><b>${c.image ?? '—'}</b><span>图片</span></div>
        <div class="vp-stat"><b>${c.video ?? '—'}</b><span>视频</span></div>
        <div class="vp-stat"><b>${c.fav ?? '—'}</b><span>收藏</span></div>
        <div class="vp-stat"><b>${st.total}</b><span>当前筛选</span></div>`;
    };
    async function loadExplore(reset) {
      if (reset) st.items = [];
      await fetchLibrary(st, { append: !reset });
      paint(); paintStats();
    }
    $$('#vxTabs .vp-tab').forEach((b) => b.onclick = () => {
      st.type = b.dataset.t;
      $$('#vxTabs .vp-tab').forEach((x) => x.classList.toggle('is-active', x === b));
      loadExplore(true);
    });
    $('#vxSort').onchange = (e) => { st.sort = e.target.value; loadExplore(true); };
    let timer = 0;
    $('#vxQ').oninput = (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { st.q = e.target.value.trim(); loadExplore(true); }, 260);
    };
    await loadExplore(true);
  }

  // ---------------------------------------------------------------- 资产
  async function renderAssets() {
    const st = V.assets;
    const body = $('#vpBody');
    if (!body) return;
    body.innerHTML = `<div class="vp-bar">
        <div class="vp-tabs" id="vaTabs">
          <button class="vp-tab ${!st.trash && st.type === 'all' ? 'is-active' : ''}" data-t="all">全部</button>
          <button class="vp-tab ${!st.trash && st.type === 'image' ? 'is-active' : ''}" data-t="image">图片</button>
          <button class="vp-tab ${!st.trash && st.type === 'video' ? 'is-active' : ''}" data-t="video">视频</button>
          <button class="vp-tab ${!st.trash && st.type === 'fav' ? 'is-active' : ''}" data-t="fav">收藏</button>
          <button class="vp-tab ${st.trash ? 'is-active' : ''}" data-t="trash">回收站</button>
        </div>
        <label class="vp-search">${ico('assets')}<input id="vaQ" placeholder="搜索提示词或文件名" value="${esc(st.q)}"></label>
        <button class="vp-btn" id="vaSelect">选择</button>
      </div>
      <div class="vt-tag" id="vaHint" style="margin:-4px 0 12px"></div>
      <div id="vaGrid" class="vx-grid"></div>
      <div class="vp-more" id="vaMore"></div>
      <div id="vaBatch"></div>`;
    $('#vpActions').innerHTML = `<button class="vp-btn" id="vaRefresh">${ico('refresh')}刷新</button>`;
    $('#vaRefresh').onclick = () => loadAssets(true);

    const paint = () => {
      const grid = $('#vaGrid');
      if (!grid) return;
      if (st.trash) {
        grid.innerHTML = st.items.length ? st.items.map((x) => `
          <div class="vx-card" data-id="${esc(x.id)}">
            <div class="vx-media">${mediaHtml({ ...x, type: x.type })}
              <div class="vx-badges"><span class="vx-badge">回收站</span></div></div>
            <div class="vx-meta"><div class="vx-prompt">${esc(x.original || x.id)}</div>
              <div class="vs-actions"><button class="vp-btn" data-restore="${esc(x.id)}">${ico('refresh')}恢复</button></div></div>
          </div>`).join('') : `<div class="vp-empty" style="grid-column:1/-1"><strong>回收站是空的</strong>移入回收站的文件会出现在这里，可随时恢复。</div>`;
        $$('[data-restore]', grid).forEach((b) => b.onclick = async () => {
          try {
            await api('/api/library/restore', { ids: [b.dataset.restore] });
            toast('已恢复', 'success');
            loadAssets(true);
          } catch (err) { toast(err.message, 'error'); }
        });
        $('#vaHint').textContent = '回收站里的文件已移出生成目录，恢复后回到 assets/generated/。没有永久删除入口。';
        $('#vaMore').innerHTML = '';
        return;
      }
      grid.innerHTML = st.items.length
        ? st.items.map((x) => gridCard(x, { selectable: V.selectMode, uniform: true })).join('')
        : `<div class="vp-empty" style="grid-column:1/-1"><strong>没有匹配的资产</strong>换一个筛选条件，或先去创作页生成。</div>`;
      bindGrid(grid, st.items, { selectable: V.selectMode });
      const more = $('#vaMore');
      more.innerHTML = st.items.length < st.total
        ? `<button class="vp-btn" id="vaLoadMore">加载更多（${st.items.length} / ${st.total}）</button>` : '';
      const btn = $('#vaLoadMore');
      if (btn) btn.onclick = () => loadAssets(false);
      updateBatchBar();
    };
    async function loadAssets(reset) {
      if (reset) { st.items = []; V.sel.clear(); }
      await fetchLibrary(st, { trash: st.trash, append: !reset });
      paint();
      $('#vaHint').textContent = st.trash ? '' : `共 ${st.total} 个 · 勾选后可批量收藏 / 移入回收站 / 下载`;
      $('#vaSelect').textContent = V.selectMode ? '取消选择' : '选择';
      $('#vaSelect').classList.toggle('primary', V.selectMode);
    }
    function updateBatchBar() {
      const box = $('#vaBatch');
      if (!box) return;
      if (!V.selectMode) { box.innerHTML = ''; return; }
      box.innerHTML = `<div class="vp-batch">
        <b>已选 ${V.sel.size}</b>
        <button class="vp-btn" data-b="fav" ${V.sel.size ? '' : 'disabled'}>${ico('heart')}收藏</button>
        <button class="vp-btn" data-b="unfav" ${V.sel.size ? '' : 'disabled'}>${ico('heart')}取消收藏</button>
        <button class="vp-btn" data-b="download" ${V.sel.size ? '' : 'disabled'}>${ico('download')}下载</button>
        <button class="vp-btn danger" data-b="trash" ${V.sel.size ? '' : 'disabled'}>${ico('trash')}移入回收站</button>
      </div>`;
      $$('[data-b]', box).forEach((b) => b.onclick = async () => {
        const ids = Array.from(V.sel);
        if (!ids.length) return;
        try {
          if (b.dataset.b === 'trash') {
            await api('/api/library/trash', { ids });
            toast(`已移入回收站 ${ids.length} 个（可恢复）`, 'success');
          } else if (b.dataset.b === 'download') {
            ids.forEach((id, i) => setTimeout(() => {
              const a = document.createElement('a');
              a.href = `/assets/generated/${encodeURIComponent(id)}`; a.download = id;
              document.body.appendChild(a); a.click(); a.remove();
            }, i * 140));
            toast(`开始下载 ${ids.length} 个文件`);
            return;
          } else {
            for (const id of ids) await api('/api/library/fav', { id, fav: b.dataset.b === 'fav' });
            toast(b.dataset.b === 'fav' ? '已收藏' : '已取消收藏', 'success');
          }
          V.sel.clear();
          loadAssets(true);
        } catch (err) { toast(err.message, 'error'); }
      });
    }
    window.__vaUpdateBatch = updateBatchBar;
    $('#vaSelect').onclick = () => {
      V.selectMode = !V.selectMode;
      if (!V.selectMode) V.sel.clear();
      loadAssets(true);
    };
    $$('#vaTabs .vp-tab').forEach((b) => b.onclick = () => {
      const t = b.dataset.t;
      st.trash = t === 'trash';
      if (!st.trash) st.type = t;
      $$('#vaTabs .vp-tab').forEach((x) => x.classList.toggle('is-active', x === b));
      if (st.trash) V.selectMode = false;
      loadAssets(true);
    });
    let timer = 0;
    $('#vaQ').oninput = (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { st.q = e.target.value.trim(); loadAssets(true); }, 260);
    };
    await loadAssets(true);
  }

  // ---------------------------------------------------------------- 媒体选择器
  function pickMedia({ kind = 'image', title = '选择本机素材' } = {}) {
    return new Promise((resolve) => {
      let items = [];
      const type = kind === 'video' ? 'video' : 'image';
      const layer = openDialog(title, `
        <label class="vp-search" style="margin-bottom:12px;width:100%">${ico('explore')}
          <input id="vpPickQ" placeholder="搜索提示词或文件名" autocomplete="off"></label>
        <div id="vpPickBox"><div class="vp-empty">正在读取…</div></div>
        <p class="vt-note" style="margin-top:12px">只列出本机 assets/generated/ 下的${kind === 'video' ? '视频' : '图片'}；点选即用，不会复制文件。</p>`);
      const box = $('#vpPickBox', layer);
      const paint = () => {
        box.innerHTML = items.length
          ? `<div class="vpick" id="vpPick">${items.map((x) => `
              <button data-id="${esc(x.id)}" title="${esc(x.prompt || x.id)}">${x.type === 'video' ? `<video src="${esc(posterUrl(x))}" muted playsinline preload="metadata"></video>` : mediaHtml(x)}</button>`).join('')}</div>`
          : '<div class="vp-empty"><strong>没有匹配的素材</strong>换一个关键词，或直接在工具里上传文件。</div>';
        $$('#vpPick button', box).forEach((b) => b.onclick = () => {
          const item = items.find((x) => x.id === b.dataset.id);
          closeDialog();
          resolve(item || null);
        });
      };
      const load = async (q = '') => {
        try {
          const data = await api(`/api/library?type=${type}&limit=60${q ? `&q=${encodeURIComponent(q)}` : ''}`);
          items = data.items || [];
          paint();
        } catch (err) {
          box.innerHTML = `<div class="vp-empty"><strong>读取失败</strong>${esc(err.message)}</div>`;
        }
      };
      load();
      let timer = 0;
      const input = $('#vpPickQ', layer);
      input.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(() => load(input.value.trim()), 240);
      };
      layer.querySelector('.vdlg-scrim').addEventListener('click', () => resolve(null), { once: true });
      setTimeout(() => input.focus(), 60);
    });
  }

  // ---------------------------------------------------------------- AI 工具
  const TOOLS = [
    { id: 'crop', icon: 'ratio', name: '智能裁切', desc: '按目标比例居中裁切，可选重心。不拉伸、不补边。' },
    { id: 'compress', icon: 'hd', name: '图片压缩', desc: '限制长边与质量，输出 JPEG；支持目标体积。' },
    { id: 'frame', icon: 'image', name: '视频抽帧', desc: '从 mp4 取首帧 / 中间帧 / 指定秒，输出 PNG。' },
    { id: 'gif', icon: 'motion', name: '视频转 GIF', desc: '截取片段生成 GIF，可调帧率与宽度。' },
  ];

  function renderTools() {
    const body = $('#vpBody');
    if (!body) return;
    body.innerHTML = `<div class="vt-grid" id="vtGrid">${TOOLS.map((t) => `
      <button class="vt-card ${V.tools.active === t.id ? 'is-active' : ''}" data-tool="${t.id}" type="button">
        <span class="vt-ico">${ico(t.icon)}</span><b>${t.name}</b><p>${t.desc}</p>
        <span class="vt-tag">本地处理 · 不消耗积分</span>
      </button>`).join('')}</div>
      <div id="vtWork"></div>
      <div id="vtRecent"></div>`;
    loadRecentTools();
    $$('#vtGrid [data-tool]').forEach((b) => b.onclick = () => {
      V.tools.active = b.dataset.tool;
      V.tools.out = null;
      V.tools.src = null;
      V.tools.srcUrl = '';
      renderTools();
    });
    if (V.tools.active) renderToolWork(V.tools.active);
  }

  async function loadRecentTools() {
    const box = $('#vtRecent');
    if (!box) return;
    try {
      const d = await api('/api/library?q=tool-&sort=new&limit=8');
      if (!document.contains(box)) return;   // 用户已切换工具，别往旧 DOM 写
      const items = d.items || [];
      if (!items.length) { box.innerHTML = ''; return; }
      box.innerHTML = `<div class="vt-field" style="margin-top:20px"><label>最近处理 <em>本机工具产出</em></label></div>
        <div class="vx-grid">${items.map((x) => gridCard(x, { uniform: true })).join('')}</div>`;
      bindGrid(box, items);
    } catch { box.innerHTML = ''; }
  }

  function renderToolWork(id) {
    const t = TOOLS.find((x) => x.id === id);
    const box = $('#vtWork');
    const src = V.tools.src;
    const commonSlot = `<div class="vt-field"><label>素材 <em>${id === 'crop' || id === 'compress' ? '图片' : '视频'}</em></label>
      <div class="vt-slot" id="vtSlot">${V.tools.srcUrl
        ? (id === 'crop' || id === 'compress' ? `<img src="${esc(V.tools.srcUrl)}" alt="">` : `<video src="${esc(V.tools.srcUrl)}" muted playsinline></video>`)
        : `${ico('upload')}<span>点这里选择本机素材，或上传文件</span>`}</div>
      <div class="vt-chips"><button class="vp-btn" id="vtPick">${ico('assets')}从资产选择</button>
      <button class="vp-btn" id="vtUpload">${ico('upload')}上传</button></div>
      <input type="file" id="vtFile" accept="${id === 'crop' || id === 'compress' ? 'image/*' : 'video/mp4'}" hidden></div>`;

    let fields = '';
    if (id === 'crop') fields = `<div class="vt-field"><label>目标比例</label><div class="vt-chips" data-field="ratio">
      ${['1:1', '9:16', '16:9', '4:3', '3:4', '3:2'].map((r) => `<button class="vt-chip ${r === '9:16' ? 'is-active' : ''}" data-v="${r}">${r}</button>`).join('')}
      </div></div>
      <div class="vt-field"><label>重心</label><div class="vt-chips" data-field="focus">
      ${[['center', '居中'], ['top', '偏上'], ['bottom', '偏下'], ['left', '偏左'], ['right', '偏右']].map(([v, n], i) => `<button class="vt-chip ${i === 0 ? 'is-active' : ''}" data-v="${v}">${n}</button>`).join('')}
      </div></div>`;
    if (id === 'compress') fields = `<div class="vt-field"><label>质量 <em id="vtQVal">82</em></label><input class="vt-range" id="vtQ" type="range" min="30" max="95" value="82"></div>
      <div class="vt-field"><label>长边上限</label><div class="vt-chips" data-field="maxSide">
      ${[800, 1280, 1600, 2048].map((v, i) => `<button class="vt-chip ${i === 1 ? 'is-active' : ''}" data-v="${v}">${v}px</button>`).join('')}</div></div>
      <div class="vt-field"><label>目标体积 <em>可选</em></label><input class="vt-input" id="vtTarget" type="number" min="0" max="20000" placeholder="例如 300（KB），留空只按质量压"></div>`;
    if (id === 'frame') fields = `<div class="vt-field"><label>取帧位置</label><div class="vt-chips" data-field="at">
      ${[['first', '首帧'], ['mid', '中间'], ['custom', '指定秒']].map(([v, n], i) => `<button class="vt-chip ${i === 0 ? 'is-active' : ''}" data-v="${v}">${n}</button>`).join('')}</div></div>
      <div class="vt-field"><label>秒数 <em>仅在「指定秒」生效</em></label><input class="vt-input" id="vtAt" type="number" min="0" step="0.1" value="1"></div>`;
    if (id === 'gif') fields = `<div class="vt-field"><label>帧率</label><div class="vt-chips" data-field="fps">
      ${[8, 12, 16].map((v, i) => `<button class="vt-chip ${i === 1 ? 'is-active' : ''}" data-v="${v}">${v}fps</button>`).join('')}</div></div>
      <div class="vt-field"><label>宽度</label><div class="vt-chips" data-field="width">
      ${[320, 480, 640].map((v, i) => `<button class="vt-chip ${i === 1 ? 'is-active' : ''}" data-v="${v}">${v}px</button>`).join('')}</div></div>
      <div class="vt-field"><label>截取 <em>起点 / 时长（秒）</em></label>
        <div style="display:flex;gap:8px"><input class="vt-input" id="vtStart" type="number" min="0" step="0.1" value="0">
        <input class="vt-input" id="vtSecs" type="number" min="0.5" max="15" step="0.5" value="3"></div></div>`;

    box.innerHTML = `<div class="vt-work">
      <div class="vt-work-head"><b>${t.name}</b><span class="vt-tag">本地处理 · 不消耗积分</span></div>
      <div class="vt-work-body">
        <div class="vt-form">${commonSlot}${fields}
          <div style="display:flex;gap:8px;align-items:center">
            <button class="vp-btn primary" id="vtRun" ${src ? '' : 'disabled'}>${ico('magic')}开始处理</button>
            <span class="vt-note" id="vtState">${src ? '素材已就绪' : '先选一个素材'}</span>
          </div>
        </div>
        <div class="vt-preview" id="vtOut"><div class="vp-empty"><strong>还没有结果</strong>处理结果会出现在这里，并自动进入资产库。</div></div>
      </div>
    </div>`;

    const slot = $('#vtSlot');
    const pickBtn = $('#vtPick');
    const upBtn = $('#vtUpload');
    const fileInput = $('#vtFile');
    pickBtn.onclick = async () => {
      const item = await pickMedia({ kind: id === 'crop' || id === 'compress' ? 'image' : 'video' });
      if (!item) return;
      V.tools.src = { file: item.url, kind: item.type };
      V.tools.srcUrl = item.url;
      renderToolWork(id);
    };
    upBtn.onclick = () => fileInput.click();
    slot.onclick = () => fileInput.click();
    fileInput.onchange = async (e) => {
      const f = e.target.files?.[0];
      e.target.value = '';
      if (!f) return;
      try {
        const r = await fileToDataURI(f, 1600);
        V.tools.src = { upload: r.uri, kind: r.kind };
        V.tools.srcUrl = r.uri;
        renderToolWork(id);
      } catch (err) { toast(err.message, 'error'); }
    };

    $$('.vt-chips', box).forEach((group) => {
      const field = group.dataset.field;
      if (!field) return;
      group.querySelectorAll('.vt-chip').forEach((c) => c.onclick = () => {
        group.querySelectorAll('.vt-chip').forEach((x) => x.classList.toggle('is-active', x === c));
      });
    });
    if ($('#vtQ')) $('#vtQ').oninput = (e) => { $('#vtQVal').textContent = e.target.value; };
    const val = (field) => $(`.vt-chips[data-field="${field}"] .vt-chip.is-active`)?.dataset.v;

    $('#vtRun').onclick = async () => {
      if (!V.tools.src) return toast('先选一个素材', 'error');
      const base = V.tools.src.upload ? { upload: V.tools.src.upload } : { file: V.tools.src.file };
      const state = $('#vtState');
      const btn = $('#vtRun');
      btn.disabled = true;
      state.textContent = '处理中…';
      try {
        let out;
        if (id === 'crop') out = await api('/api/tools/crop', { ...base, ratio: val('ratio') || '9:16', focus: val('focus') || 'center' }, { timeout: 60000 });
        if (id === 'compress') out = await api('/api/tools/compress', {
          ...base, quality: Number($('#vtQ')?.value || 82), maxSide: Number(val('maxSide') || 1600),
          targetKb: Number($('#vtTarget')?.value || 0),
        }, { timeout: 60000 });
        if (id === 'frame') {
          const at = val('at') === 'custom' ? ($('#vtAt')?.value || '0') : val('at');
          out = await api('/api/tools/frame', { ...base, at }, { timeout: 60000 });
        }
        if (id === 'gif') out = await api('/api/tools/gif', {
          ...base, fps: Number(val('fps') || 12), width: Number(val('width') || 480),
          start: Number($('#vtStart')?.value || 0), seconds: Number($('#vtSecs')?.value || 3),
        }, { timeout: 120000 });
        V.tools.out = out;
        paintToolOut(id, out, base);
        state.textContent = '完成';
        toast('处理完成，已保存到资产库', 'success');
      } catch (err) {
        state.textContent = `失败：${err.message}`;
        toast(err.message, 'error');
      } finally { btn.disabled = false; }
    };
    if (V.tools.out) paintToolOut(id, V.tools.out, null);
  }

  function paintToolOut(id, out, base) {
    const box = $('#vtOut');
    if (!box) return;
    const isVideo = /\.mp4$/.test(out.url);
    box.innerHTML = `<div class="vt-out">
      <div class="vdlg-media">${isVideo ? `<video src="${esc(out.url)}" controls autoplay loop muted playsinline></video>` : `<img src="${esc(out.url)}" alt="处理结果">`}</div>
      <div class="vt-note">输出：${esc(out.file)} · ${fmtBytes(out.bytes)}${out.w ? ` · ${out.w}×${out.h}` : ''}${out.quality ? ` · q${out.quality}` : ''}${out.fps ? ` · ${out.fps}fps` : ''}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="vp-btn" href="${esc(out.url)}" download="${esc(out.file)}">${ico('download')}下载</a>
        <button class="vp-btn ghost" id="vtToAssets">${ico('assets')}去资产页看</button>
        ${base ? `<button class="vp-btn ghost" id="vtAgain">${ico('refresh')}再处理一次</button>` : ''}
      </div></div>`;
    $('#vtToAssets').onclick = () => { V.assets.type = 'all'; V.assets.trash = false; openPage('assets'); };
    $('#vtAgain')?.addEventListener('click', () => { V.tools.out = null; renderToolWork(id); });
  }

  // ---------------------------------------------------------------- 主体
  async function loadSubjects() {
    try {
      const data = await api('/api/subjects');
      V.subjects = data.items || [];
      V.subjectsTrash = data.trash || [];
    } catch (err) {
      V.subjects = [];
      toast(`读取主体失败：${err.message}`, 'error');
    }
    return V.subjects;
  }

  async function renderSubjects() {
    const body = $('#vpBody');
    if (!body) return;
    body.innerHTML = '<div class="vp-empty">正在读取主体库…</div>';
    await loadSubjects();
    body.innerHTML = `<div class="vs-grid">
      <button class="vs-card vs-new" id="vsNew" type="button">${ico('plus')}<strong>新建主体</strong><span class="vt-tag">上传 1–9 张参考图</span></button>
      ${V.subjects.length ? '' : `<article class="vs-card vs-tips">
        <div class="vs-body"><b>主体怎么用</b>
          <p>1. 存一组同一角色 / 产品的参考图；<br>2. 创作页点「参考」可一次带入；<br>3. 生成时第 1 张作为画布 / 首帧。</p>
          <div class="vt-tag">适合固定角色、产品、画风</div></div></article>`}
      ${V.subjects.map((s) => `<article class="vs-card" data-sid="${esc(s.id)}">
        <div class="vs-cover">${s.cover ? `<img src="${esc(s.cover)}" alt="${esc(s.name)}">` : ''}</div>
        <div class="vs-body">
          <b>${esc(s.name)}</b>
          <p>${esc(s.note || '没有备注')}</p>
          <div class="vt-tag">${s.refCount} 张参考图 · ${esc(fmtDate(s.createdAt))}</div>
          <div class="vs-actions">
            <button class="vp-btn" data-act="use">${ico('magic')}用于创作</button>
            <button class="vp-btn ghost" data-act="edit">${ico('upload')}加参考</button>
            <button class="vp-btn ghost danger" data-act="del">${ico('trash')}删除</button>
          </div>
        </div>
      </article>`).join('')}
    </div>
    <p class="vt-note" style="margin-top:14px">参考图保存在 <code>assets/subjects/</code>，只在本机；删除主体只是把参考图目录移入回收站，不会永久删除。</p>
    ${(V.subjectsTrash || []).length ? `<div class="vt-field" style="margin-top:18px"><label>已删除的主体 <em>可恢复</em></label></div>
      <div class="vs-grid">${V.subjectsTrash.map((s) => `<article class="vs-card" data-trash-sid="${esc(s.id)}">
        <div class="vs-body"><b>${esc(s.name)}</b><p>${s.refCount} 张参考图 · 删除于 ${esc(fmtDate(s.deletedAt))}</p>
          <div class="vs-actions"><button class="vp-btn" data-restore-sub="${esc(s.id)}">${ico('refresh')}恢复</button></div>
        </div></article>`).join('')}</div>` : ''}`;
    $('#vsNew').onclick = () => openSubjectDialog(null, []);
    $$('[data-restore-sub]').forEach((b) => b.onclick = async () => {
      try {
        await api('/api/subjects/restore', { id: b.dataset.restoreSub });
        toast('主体已恢复', 'success');
        renderSubjects();
      } catch (err) { toast(err.message, 'error'); }
    });
    $$('[data-sid]').forEach((card) => {
      const s = V.subjects.find((x) => x.id === card.dataset.sid);
      if (!s) return;
      card.querySelector('[data-act="use"]').onclick = async () => {
        // 依次把参考图转成 data URI，交给创作页的参考区（需要 app.js 暴露 bridge）
        const bridge = window.JimengBridge;
        if (!bridge || typeof bridge.addRefFiles !== 'function') {
          toast('当前页面版本未开放参考区接口：请到创作页用「参考」按钮上传', 'error');
          return;
        }
        try {
          toast(`正在载入 ${s.refs.length} 张参考图…`);
          const files = [];
          for (const u of s.refs) {
            const res = await fetch(u, { cache: 'no-store' });
            const blob = await res.blob();
            const ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
            files.push(new File([blob], `${s.name}-${files.length + 1}.${ext}`, { type: blob.type || 'image/png' }));
          }
          await bridge.addRefFiles(files);
          backToCreate();
          toast(`已把「${s.name}」加入参考`, 'success');
        } catch (err) { toast(err.message, 'error'); }
      };
      card.querySelector('[data-act="edit"]').onclick = () => {
        openSubjectDialog({ ...s, images: [] }, []);
      };
      card.querySelector('[data-act="del"]').onclick = async () => {
        if (!confirm(`删除主体「${s.name}」？参考图会移入回收站，可恢复。`)) return;
        try {
          await api('/api/subjects/delete', { id: s.id });
          toast('已删除（参考图在回收站）', 'success');
          renderSubjects();
        } catch (err) { toast(err.message, 'error'); }
      };
    });
  }

  function openSubjectDialog(subject, presetImages) {
    const editing = !!subject;
    const images = [...(presetImages || [])];
    const body = `<div class="vt-field"><label>主体名称 <em>必填，≤40 字</em></label>
        <input class="vt-input" id="vsName" maxlength="40" placeholder="例如：林晚 / 主推款保温杯" value="${esc(subject?.name || '')}"></div>
      <div class="vt-field"><label>备注 <em>可选</em></label>
        <input class="vt-input" id="vsNote" maxlength="200" placeholder="身份、外观特征、用途…" value="${esc(subject?.note || '')}"></div>
      <div class="vt-field"><label>参考图 <em>1–9 张，单张 ≤6MB</em></label>
        <div class="vs-drop" id="vsDrop">${ico('upload')} 点击选择，或把图片拖进来</div>
        <input type="file" id="vsFile" accept="image/*" multiple hidden>
        <div class="vs-refs" id="vsRefs"></div>
      </div>
      <p class="vt-note">图片只存在本机 assets/subjects/，不会上传到任何第三方；用于生成时作为参考图发给已配置的生成网关。</p>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="vp-btn ghost" data-dlg-close>取消</button>
        <button class="vp-btn primary" id="vsSave">${editing ? '保存并追加' : '创建主体'}</button>
      </div>`;
    const layer = openDialog(editing ? '编辑主体' : '新建主体', body);
    const refsBox = layer.querySelector('#vsRefs');
    const paint = () => {
      refsBox.innerHTML = images.map((u, i) => `<div style="position:relative"><img src="${esc(u)}" alt="参考图 ${i + 1}">
        <button class="vp-btn ghost" data-del="${i}" style="position:absolute;top:4px;right:4px;padding:2px 6px">×</button></div>`).join('');
      $$('[data-del]', refsBox).forEach((b) => b.onclick = () => { images.splice(Number(b.dataset.del), 1); paint(); });
    };
    if (editing && subject.refs?.length) {
      refsBox.innerHTML = subject.refs.map((u) => `<div><img src="${esc(u)}" alt="已有参考图"></div>`).join('')
        + '<p class="vt-note" style="grid-column:1/-1">已有参考图保留；新选择的图片会追加到后面。</p>';
    }
    if (images.length) paint();
    const input = layer.querySelector('#vsFile');
    const add = async (files) => {
      const list = Array.from(files || []).slice(0, 9 - images.length);
      for (const f of list) {
        try {
          const r = await fileToDataURI(f, 1024);
          images.push(r.uri);
        } catch (err) { toast(err.message, 'error'); }
      }
      paint();
    };
    layer.querySelector('#vsDrop').onclick = () => input.click();
    layer.querySelector('#vsDrop').ondragover = (e) => { e.preventDefault(); };
    layer.querySelector('#vsDrop').ondrop = (e) => { e.preventDefault(); add(e.dataTransfer.files); };
    input.onchange = (e) => { add(e.target.files); e.target.value = ''; };
    layer.querySelector('#vsSave').onclick = async () => {
      const name = layer.querySelector('#vsName').value.trim();
      const note = layer.querySelector('#vsNote').value.trim();
      if (!name) return toast('请填写主体名称', 'error');
      if (!images.length && !editing) return toast('至少上传一张参考图', 'error');
      const btn = layer.querySelector('#vsSave');
      btn.disabled = true;
      try {
        await api('/api/subjects', { id: subject?.id, name, note, images });
        closeDialog();
        toast(editing ? '已更新主体' : '主体已创建', 'success');
        if (V.current === 'subjects') renderSubjects();
      } catch (err) {
        toast(err.message, 'error');
      } finally { btn.disabled = false; }
    };
  }

  // ---------------------------------------------------------------- 动效工作室
  const MOTIONS = [
    { id: 'push', name: '缓慢推近', desc: '镜头向前，主体渐大', text: '镜头缓慢推近主体，轻微手持感，焦点始终在人物眼睛' },
    { id: 'pull', name: '缓慢拉远', desc: '从细节退到环境', text: '镜头从细节缓慢拉远，逐渐露出完整环境，保持画面稳定' },
    { id: 'pan', name: '横向平移', desc: '从左到右扫过', text: '镜头从左向右平稳横移，前景与背景产生自然视差' },
    { id: 'orbit', name: '轻微环绕', desc: '绕主体小幅移动', text: '镜头绕主体做小幅环绕运动，保持主体居中，背景缓慢流动' },
    { id: 'breathe', name: '呼吸感', desc: '几乎静止的微动', text: '镜头几乎静止，只有极轻微的呼吸式起伏，光线缓慢变化' },
    { id: 'wind', name: '风吹动', desc: '发丝与衣角飘动', text: '画面内产生自然风，发丝与衣角轻轻飘动，镜头保持稳定' },
  ];

  async function renderMotion() {
    const body = $('#vpBody');
    if (!body) return;
    let info = null;
    try { info = await api('/api/status'); } catch (err) { toast(`读取链路失败：${err.message}`, 'error'); }
    const video = info?.video || {};
    const ready = !!video.configured;
    const m = V.motion;
    body.innerHTML = `<div class="vm-grid">
      <div class="vm-stage" id="vmStage">${m.out
        ? `<video src="${esc(m.out)}" controls autoplay loop muted playsinline></video>`
        : (m.srcUrl ? `<img src="${esc(m.srcUrl)}" alt="待加动效的图片">`
          : `<div class="vp-empty"><strong>先选一张图</strong>可以是资产库里的作品，也可以直接上传。</div>`)}</div>
      <div class="vm-panel">
        <div class="vt-field"><label>首帧图片 <em>必填</em></label>
          <div class="vt-chips"><button class="vp-btn" id="vmPick">${ico('assets')}从资产选择</button>
          <button class="vp-btn" id="vmUpload">${ico('upload')}上传图片</button></div>
          <input type="file" id="vmFile" accept="image/*" hidden></div>
        <div class="vt-field"><label>运镜预设</label><div class="vm-presets">
          ${MOTIONS.map((x) => `<button class="vm-preset ${m.preset === x.id ? 'is-active' : ''}" data-p="${x.id}"><b>${x.name}</b><small>${x.desc}</small></button>`).join('')}
        </div></div>
        <div class="vt-field"><label>运镜描述 <em>可改</em></label>
          <textarea class="vt-input" id="vmPrompt" rows="3" maxlength="400">${esc(m.prompt || MOTIONS.find((x) => x.id === m.preset)?.text || '')}</textarea></div>
        <div class="vt-field"><label>规格</label>
          <div class="vt-note">首帧跟随比例 · 720p · 8s · H3 免费档（计费档未开启）</div></div>
        ${m.busy ? `<div class="vm-progress"><i id="vmBar"></i></div><div class="vm-tip" id="vmState">正在提交…</div>` : ''}
        <button class="vp-btn primary" id="vmGo" ${ready && m.src ? '' : 'disabled'}>${ico('motion')}生成动效视频</button>
        <div class="vm-tip" id="vmNote">${ready
          ? '提交后由上游出片，通常 1–3 分钟；页面会持续轮询，离开本页不会中断任务。'
          : '视频链路未配置：缺少 AVM_COOKIE，服务端会拒绝提交。'}</div>
        ${m.out ? `<div style="display:flex;gap:8px"><a class="vp-btn" href="${esc(m.out)}" download>${ico('download')}下载</a>
          <button class="vp-btn ghost" id="vmAgain">${ico('refresh')}换一张再来</button></div>` : ''}
      </div>
    </div>`;
    $('#vmPick').onclick = async () => {
      const item = await pickMedia({ kind: 'image' });
      if (!item) return;
      V.motion.src = { url: item.url, id: item.id };
      V.motion.srcUrl = item.url;
      V.motion.out = null;
      renderMotion();
    };
    $('#vmUpload').onclick = () => $('#vmFile').click();
    $('#vmFile').onchange = async (e) => {
      const f = e.target.files?.[0];
      e.target.value = '';
      if (!f) return;
      try {
        const r = await fileToDataURI(f, 1600);
        V.motion.src = { upload: r.uri };
        V.motion.srcUrl = r.uri;
        V.motion.out = null;
        renderMotion();
      } catch (err) { toast(err.message, 'error'); }
    };
    $$('.vm-preset').forEach((b) => b.onclick = () => {
      V.motion.preset = b.dataset.p;
      V.motion.prompt = MOTIONS.find((x) => x.id === b.dataset.p)?.text || '';
      renderMotion();
    });
    $('#vmPrompt').oninput = (e) => { V.motion.prompt = e.target.value; };
    $('#vmAgain')?.addEventListener('click', () => {
      V.motion.out = null; V.motion.src = null; V.motion.srcUrl = ''; V.motion.prompt = '';
      renderMotion();
    });
    $('#vmGo').onclick = async () => {
      if (!V.motion.src || V.motion.busy) return;
      V.motion.busy = true;
      V.motion.out = null;
      const prompt = `${($('#vmPrompt')?.value || V.motion.prompt || '').trim()}。画面不出现任何文字、字幕、水印。`;
      renderMotion();
      try {
        let image = V.motion.src.upload;
        if (!image) {
          const item = await api(`/api/library?q=${encodeURIComponent(V.motion.src.id)}&limit=1`);
          const hit = (item.items || []).find((x) => x.id === V.motion.src.id);
          if (!hit) throw new Error('找不到所选图片');
          image = await urlToDataURI(hit.url, 1024);
        }
        const sub = await api('/api/video', { prompt, image, ratio: 'adaptive', tier: 'free' });
        const id = sub.id;
        const started = Date.now();
        const bar = $('#vmBar');
        const state = $('#vmState');
        while (Date.now() - started < 8 * 60 * 1000) {
          await new Promise((r) => setTimeout(r, 3000));
          const pct = Math.min(94, Math.round((Date.now() - started) / 900));
          if (bar) bar.style.width = `${pct}%`;
          if (state) state.textContent = `生成中 · 已等待 ${Math.round((Date.now() - started) / 1000)}s`;
          let d;
          try { d = await api(`/api/video/${encodeURIComponent(id)}`); }
          catch (err) { if (err.status && err.status < 500) throw err; continue; }
          if (d.status === 'succeeded' && d.url) {
            V.motion.out = d.url; V.motion.busy = false;
            renderMotion();
            toast('动效视频已完成', 'success');
            return;
          }
          if (d.status === 'failed') throw new Error(d.error || '上游失败');
        }
        throw new Error('等待超时（8 分钟）');
      } catch (err) {
        V.motion.busy = false;
        renderMotion();
        toast(`生成失败：${err.message}`, 'error');
      }
    };
  }


  // ---------------------------------------------------------------- Agent 工作台
  const AG_QUICK = [
    '做一张雨夜都市海报，女主撑伞，霓虹反光',
    '给这张图配一个 8 秒的推镜视频',
    '把「先婚后爱」拆成 3 个主视觉方案',
    '做一个产品展示：保温杯，极简棚拍',
  ];

  function agentMsgHtml(m, i) {
    if (m.role === 'user') {
      return `<div class="ag-msg user"><div class="ag-bubble">${esc(m.content)}</div></div>`;
    }
    const plans = Array.isArray(m.plan) ? m.plan : [];
    return `<div class="ag-msg bot">
      <div class="ag-avatar">${ico('sparkle')}</div>
      <div class="ag-body">
        <div class="ag-bubble ${m.error ? 'is-error' : ''}">${esc(m.content)}</div>
        ${plans.length ? `<div class="ag-plans">${plans.map((pl, j) => `
          <div class="ag-plan">
            <div class="ag-plan-head"><span class="ag-kind ${pl.kind}">${pl.kind === 'video' ? '视频' : '图片'}</span>
              <b>${esc(pl.title || '方案')}</b><span class="vt-tag">${esc(pl.ratio)} · ${pl.count} ${pl.kind === 'video' ? '条' : '张'}</span></div>
            <p>${esc(pl.prompt)}</p>
            <button class="vp-btn" data-plan="${i}-${j}">${ico('send')}填入创作页</button>
          </div>`).join('')}</div>` : ''}
        ${m.model ? `<div class="ag-meta">${esc(m.mock ? 'MOCK 规划 · 零出站' : '模型：' + m.model)}</div>` : ''}
      </div>
    </div>`;
  }

  function renderAgent() {
    const body = $('#vpBody');
    if (!body) return;
    const st = V.agent;
    body.innerHTML = `<div class="ag-wrap">
      <div class="ag-thread" id="agThread">
        <div class="ag-intro">
          <div class="ag-avatar big">${ico('sparkle')}</div>
          <div><b>先聊想法，再决定花不花额度</b>
            <p>Agent 会拆出 1–3 个可执行方案。点「填入创作页」只是把提示词和模式带回去，<b>不会直接提交</b>；确认参数后由你按发送。</p></div>
        </div>
        ${st.messages.map(agentMsgHtml).join('')}
        ${st.busy ? `<div class="ag-msg bot"><div class="ag-avatar">${ico('sparkle')}</div>
          <div class="ag-body"><div class="ag-bubble thinking"><span class="spinner"></span>正在拆方案…</div></div></div>` : ''}
      </div>
      <div class="ag-quick">${AG_QUICK.map((q) => `<button class="vt-chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>
      <div class="ag-compose">
        <textarea id="agInput" rows="2" maxlength="2000" placeholder="描述你想做什么；Enter 发送，Shift + Enter 换行"></textarea>
        <button class="vp-btn primary" id="agSend" ${st.busy ? 'disabled' : ''}>${ico('send')}发送</button>
      </div>
      <p class="vt-note">Agent 只输出方案文本；图片/视频仍走创作页原有的真实链路与额度守卫。对话不落盘，刷新即清空。</p>
    </div>`;
    const thread = $('#agThread');
    if (thread) thread.scrollTop = thread.scrollHeight;
    $$('[data-q]').forEach((b) => b.onclick = () => {
      $('#agInput').value = b.dataset.q;
      agentSend();
    });
    $$('[data-plan]').forEach((b) => b.onclick = () => {
      const [mi, pj] = b.dataset.plan.split('-').map(Number);
      const pl = V.agent.messages[mi]?.plan?.[pj];
      if (!pl) return;
      setPromptAndMode(pl.prompt, pl.kind === 'video' ? 'video' : 'image');
      backToCreate();
      toast(`已填入「${pl.title}」的参数，确认后请自己点发送`, 'success');
    });
    const ta = $('#agInput');
    ta.onkeydown = (e) => {
      if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
      if (e.shiftKey) return;
      e.preventDefault();
      agentSend();
    };
    $('#agSend').onclick = agentSend;
  }

  async function agentSend() {
    const st = V.agent;
    if (st.busy) return;
    const ta = $('#agInput');
    const text = (ta?.value || '').trim();
    if (!text) return;
    st.messages.push({ role: 'user', content: text });
    st.busy = true;
    renderAgent();
    const box = $('#agThread');
    if (box) box.scrollTop = box.scrollHeight;
    try {
      const d = await api('/api/agent', { messages: st.messages.map((m) => ({ role: m.role, content: m.content })), context: { mode: 'agent' } }, { timeout: 120000 });
      st.messages.push({ role: 'assistant', content: d.reply, plan: d.plan || [], model: d.model, mock: d.mock });
    } catch (err) {
      st.messages.push({ role: 'assistant', content: `规划失败：${err.message}`, error: true });
    } finally {
      st.busy = false;
      renderAgent();
      const b2 = $('#agThread');
      if (b2) b2.scrollTop = b2.scrollHeight;
      $('#agInput')?.focus();
    }
  }

  // 创作页 Agent 模式：在模式滑杆下方挂一个入口，不改变原有模式行为
  function syncAgentEntry() {
    const on = $('.mode-tab[data-mode="agent"]')?.classList.contains('is-active');
    const host = $('#modeSwitch');
    let el = $('#agentEntry');
    if (on && host && !el) {
      el = document.createElement('button');
      el.id = 'agentEntry';
      el.className = 'agent-entry';
      el.type = 'button';
      el.innerHTML = `<span class="agent-entry-ico">${ico('sparkle')}</span>
        <span><b>Agent 工作台</b><small>先拆方案，再决定是否生成</small></span>
        <span class="agent-entry-go">${ico('chevron')}</span>`;
      el.onclick = () => openPage('agent');
      host.insertAdjacentElement('afterend', el);
    } else if (!on && el) {
      el.remove();
    }
  }

  // ---------------------------------------------------------------- 启动
  ensureRoot();
  const modeSwitch = $('#modeSwitch');
  if (modeSwitch) {
    new MutationObserver(syncAgentEntry).observe(modeSwitch, {
      subtree: true, attributes: true, attributeFilter: ['class'],
    });
  }
  syncAgentEntry();
  window.JimengViews = { open: openPage, close: closePage, state: V };
})();
