/* 即梦 AI 生成页 · 界面复刻
 * 交互层：模式切换 / 参数浮层 / 会话结果 / 资产库 / 真实 flux-3-image 接入
 * 约定：视频与 Agent 永远走本地演示；只有图片模式在代理就绪时发真实请求。
 */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const ico = (name, cls = '') => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const ART = (n) => `assets/art-${String(n).padStart(2, '0')}.svg`;
  const RATIOS = ['1:1', '2:3', '3:4', '4:5', '9:16', '3:1', '3:2', '4:3', '5:4', '16:9', '21:9', '1:2'];
  const TYPES = [
    { id: 'image', label: '图片生成', icon: 'image' },
    { id: 'video', label: '视频生成', icon: 'video' },
    { id: 'agent', label: 'Agent 模式', icon: 'sparkle' },
    { id: 'music', label: '音乐生成', icon: 'music' },
    { id: 'audio', label: '音频生成', icon: 'audio' },
    { id: 'digital', label: '数字人', icon: 'avatar-bot' },
    { id: 'mimic', label: '动作模仿', icon: 'mimic' },
  ];
  const MODELS = {
    image: ['图片美学模型 V8.2', 'Seedream 5.0 Lite', 'flux-3-image（真实网关）', '图片 4.0'],
    video: ['HappyHorse 1.1', '即梦 Seedance 2.5', '即梦 Seedance 2.0', '视频 3.0 Fast'],
    agent: ['Doubao 2.1 Lite', 'Doubao 2.1 Pro', 'Doubao 2.1 Lite（快速）'],
  };
  const REF_METHODS = ['全能参考', '首尾帧', '文生视频', '图生视频'];
  const DETAILS = ['低', '中', '高', '超高'];

  const INSPIRATIONS = {
    image: [
      { n: 1, tag: 'Seedream 5.0 Lite', title: '商品图场景合成', desc: '暖金光影搭配天然石材布景，快速打造奢华、自然的高端产品大片。', prompt: '高端琥珀色香水瓶置于天然洞石台面，暖金色侧逆光，奶油色背景，商业产品摄影，柔和阴影，极简构图，8K 细节' },
      { n: 2, tag: 'Seedream 5.0 Lite', title: '镜面珠宝人像摄影', desc: '纯白背景搭配专业美妆平光，呈现真实细腻的肤质与产品使用瞬间。', prompt: '镜面珠宝人像摄影，纯白影棚背景，专业美妆平光，真实细腻肤质，珍珠耳饰高光，柔和米色调，时尚大片' },
      { n: 3, tag: 'Seedream 5.0 Lite', title: '极简耳机海报', desc: '黑灰极简视觉融合悬浮产品与毛玻璃 UI，打造高端科技品牌海报。', prompt: '黑灰极简耳机产品海报，悬浮的哑光金属头戴耳机，毛玻璃 UI 元素，冷调边缘光，大量留白，高端科技品牌视觉' },
    ],
    video: [
      { n: 4, tag: '即梦 Seedance 2.0 mini', title: '日系青春短片', desc: '清新柔光与胶片质感串联日常片段，呈现温柔、朦胧的青春故事。', prompt: '日系青春短片，夏日草地上穿白衬衫的少女逆风奔跑，清新柔光，胶片颗粒，镜头缓慢推近，15 秒' },
      { n: 5, tag: '即梦 Seedance 2.0 mini', title: '高燃汉堡宣传视频', desc: '食材悬浮解构、烈焰炙烤与高速卡点，打造冲击力十足的美食大片。', prompt: '高燃汉堡广告，食材悬浮解构，火焰炙烤，高速卡点剪辑，微距镜头，浓郁暖色，戏剧性布光' },
      { n: 6, tag: '即梦 Seedance 2.0 mini', title: '时尚换装快切短片', desc: '人物行走中无缝切换多套造型，搭配潮流 UI 与社交贴纸动效。', prompt: '时尚换装快切，模特迎面行走，每两步无缝切换一套造型，潮流 UI 贴纸，城市夜景，稳定器跟拍' },
    ],
    agent: [
      { n: 7, tag: '热门技能', title: '叙事短片导演分镜', desc: '以导演分镜方法论产出叙事短片：导演意图书、九列分镜表与逐 Clip 提示词，直至成片。', prompt: '用导演分镜方法，为一个 30 秒的雨夜书店短片产出 6 个分镜与逐镜提示词' },
      { n: 8, tag: '热门技能', title: '系列套图生成', desc: '抽象出稳定的系列视觉母体，锁定风格，生成一组风格统一但变量清晰的系列图片。', prompt: '以「东方极简茶器」为母题，生成一组 4 张风格统一的系列海报' },
      { n: 9, tag: '热门技能', title: '爆款电商短视频题材', desc: '电商短视频创作助手，支持多题材与时长配置，每个题材生成 5 组差异化方案。', prompt: '为一款便携咖啡机策划 5 组差异化短视频创意，时长 15 秒' },
    ],
    more: [
      { n: 10, tag: '灵感', title: '水母在海底遨游', desc: '深蓝海水中的半透明水母，体积光穿透水面，梦幻静谧。', prompt: '深蓝海水中半透明水母缓慢游动，体积光穿透水面，微小气泡，电影感，静谧' },
      { n: 11, tag: '灵感', title: '文艺复兴猫咪', desc: '身着贵族丝绸服饰的猫咪，古典油画质感与暖色烛光。', prompt: '文艺复兴风格，身着贵族丝绸服饰的猫咪肖像，古典油画质感，暖色烛光，深色背景' },
      { n: 12, tag: '灵感', title: '粉色星球飞行器', desc: '未来主义飞行器低空掠过粉色星球，梦幻渐变星环。', prompt: '插画风格，未来主义飞行器在粉色星球低空飞行，巨大星环，柔和渐变天空，梦幻色彩' },
    ],
  };

  const QA_FREEZE = new URLSearchParams(location.search).get('qa') === 'freeze';

  const state = {
    mode: 'image', typeLabel: '图片生成', model: MODELS.image[0], refMethod: '全能参考',
    ratio: '1:1', resolution: '2K', detail: '超高', count: 4, duration: 5, videoRes: '1080P',
    videoTier: 'free',
    prompt: '', refs: [], theme: 'dark', collapsed: false, busy: false, statusReady: false,
    // 图片任务队列：允许同时在飞多个（上限取服务端 concurrency，默认 4）。
    // busy 只是「有没有任务在跑」的派生状态，不再作为提交门禁。
    jobs: new Map(), jobSeq: 0, videoBusy: false, queueView: false,
    apiStatus: null, inspireOffset: 0, lightboxIndex: 0, lightboxItems: [],
    sessions: [], activeSession: 'new', assets: [], assetFilter: 'all', credits: 2000,
    billing: null, creditSource: 'local',
  };

  /* ---------- 纯函数：显式规则，与 server.py:estimate_credits 同口径 ---------- */
  function estimateCredits(s = state) {
    if (s.mode === 'image' && s.apiStatus?.real) {
      // 多模型各价：优先用该模型的上游单价，没读到才退回默认单价
      const m = (state.cfg?.models || []).includes(state.model) ? state.model : currentImageModel();
      const p = Number(state.billing?.prices?.[m]);
      return Number.isFinite(p) ? p : Number(state.billing?.imageCredits ?? 3.8);
    }
    // 免费档（turbo · 720p · 8s）上游 paid=false，页面积分不该动
    if (s.mode === 'video' && !s.frontendDemo && s.apiStatus?.video?.configured) return 0;
    const base = { image: 100, video: 18, agent: 60 }[s.mode] ?? 100;
    const mult = { '1K': 1, '2K': 2, '1080P': 1, '720P': 1 }[s.mode === 'video' ? s.videoRes : s.resolution] ?? 1;
    return s.mode === 'video' ? base * s.count * s.duration * mult : base * s.count * mult;
  }
  function isRealCall() { return state.mode === 'image' && !!state.apiStatus?.real; }
  // 快捷配置 models=… 下发的真实模型列表；没有就用界面兜底列表
  function configuredModels() {
    for (const ms of [state.cfg?.models, state.apiStatus?.config?.models]) {
      if (Array.isArray(ms) && ms.length) return ms;
    }
    return [];
  }
  function currentImageModel() {
    const ms = configuredModels();
    return ms.length && ms.includes(state.model) ? state.model : null;
  }
  function syncImageModelFromConfig() {
    const ms = configuredModels();
    if (!ms.length) return;
    if (!ms.includes(state.model)) {
      const preferred = state.cfg?.model || state.apiStatus?.config?.model;
      state.model = ms.includes(preferred) ? preferred : ms[0];
    }
  }
  // 视频走的是另一条链路（avm minimaxH3），图片的 real 标志管不到它
  function isRealVideo() {
    return state.mode === 'video' && !state.frontendDemo && !!state.apiStatus?.video?.configured;
  }
  // 视频真实档位：paid 必须服务端显式开，否则一律落回 free —— 防一次误点就花额度
  function videoTierInfo() {
    const v = state.apiStatus?.video;
    const allowPaid = !!v?.allowPaid;
    const tier = state.videoTier === 'paid' && allowPaid ? 'paid' : 'free';
    const t = v?.tiers?.[tier]
      || (tier === 'paid' ? { resolution: '1080p', duration: 10 } : { resolution: '720p', duration: 8 });
    return { tier, resolution: t.resolution, duration: t.duration, allowPaid };
  }
  // 上游回传的 credits 是唯一权威值；0 合法，缺失才用兜底值
  function chargedCredits(d) {
    return Number.isFinite(d && d.credits) ? d.credits : 3.8;
  }
  function placeholder() {
    if (state.mode === 'video') return '上传最多 12 个参考素材、输入文字或参考内容，自由组合图、文、音、视频多元素。例如：@图片1 模仿 @视频1 的动作。';
    if (state.mode === 'agent') return '输入想法、剧本或上传参考，支持 / 使用技能，添加主体，和 Agent 一起创作';
    return '上传参考图、输入文字或主体，描述你想生成的图片。';
  }
  function tagList(s, realOverride) {
    // 传入快照时必须用提交时的 real 值：否则生成中切模式会让历史标签跟着变
    const real = realOverride === undefined ? isRealCall() : !!realOverride;
    const rv = !!s.realVideo;
    const rm = s.model && configuredModels().includes(s.model) ? s.model : 'flux-3-image';
    const out = [s.typeLabel, real ? rm : rv ? 'minimaxH3' : s.model];
    if (s.mode === 'image') out.push(real ? '1024×1024 · 1K' : `${s.ratio} ${s.resolution}${s.detail}`, real ? '1 张' : `${s.count} 张`);
    if (s.mode === 'video') {
      const aspect = rv && (s.refs || []).length ? '跟随首帧' : s.ratio;
      out.push(rv ? ((s.refs || []).length ? '图生视频' : '文生视频') : s.refMethod,
        `${aspect} ${s.videoRes}`, `${s.duration}s`, `${rv ? 1 : s.count} 个`);
    }
    if (s.mode === 'agent') out.push('技能', `${s.count} 个结果`);
    const refsSent = Number.isFinite(s.refsSent) ? s.refsSent : (real ? s.refs.length : 0);
    if (refsSent > 0) out.push(`${refsSent} 个参考`);
    return out;
  }

  /** 快捷配置的落地提示。
   *
   * 服务端在收到 ?base_url=…&api_key=… 时就已经写盘并 302 回来了，
   * 所以这里只做两件事：说一句结果、把一次性参数从地址栏和历史里抹掉。
   */
  function applyQuickHint(qp) {
    const code = qp.get('_qc');
    if (!code) return;
    if (code === 'ok') {
      const t = qp.get('_qt') || 'image';
      const label = t === 'llm' ? '文本模型' : t === 'all' ? '图像网关 + 文本模型' : '图像网关';
      const base = qp.get('_qb') || '';
      const mask = qp.get('_qk') || '';
      const model = qp.get('_qm2') || '';
      toast(`快捷配置已生效：${label} → ${base}`
        + (mask ? ` · 密钥 ${mask}` : '') + (model ? ` · ${model}` : ''), 'success');
    } else {
      toast(`快捷配置未生效：${qp.get('_qm') || '参数不合法'}`, 'error');
    }
    ['_qc', '_qt', '_qb', '_qk', '_qm', '_qm2'].forEach((k) => qp.delete(k));
    const rest = qp.toString();
    history.replaceState(null, '', location.pathname + (rest ? `?${rest}` : '') + location.hash);
  }

  /* ---------- 通用 UI ---------- */
  function toast(msg, kind = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    const icon = kind === 'error' ? 'warn' : kind === 'success' ? 'check' : 'sparkle';
    el.innerHTML = `${ico(icon)}<span>${esc(msg)}</span>`;
    $('#toasts').appendChild(el);
    setTimeout(() => { el.classList.add('is-out'); setTimeout(() => el.remove(), 320); }, 2600);
  }
  function setHint(text, isError = false) {
    const el = $('#hintText');
    el.classList.toggle('is-error', isError);
    el.textContent = text;
  }
  const queueStats = () => {
    const all = [...(state.jobs?.values?.() || [])];
    const running = all.filter((j) => j.status === 'running').length;
    const n = Number(state.apiStatus?.concurrency);
    const cap = Number.isFinite(n) && n > 0 ? Math.min(n, 6) : 4;
    return { running, cap };
  };

  function defaultHint() {
    if (isRealCall()) {
      const n = state.refs.length;
      const { running, cap } = queueStats();
      const queue = running ? ` · 队列 ${running}/${cap}，可继续输入下一个` : ` · 可并发 ${cap} 个`;
      const base = state.apiStatus?.upstreamMode === 'mock'
        ? 'MOCK 链路 · 固定 1 张 1K · 零计费 · Enter 发送'
        : '真实链路 · 固定 1 张 1K · Enter 发送';
      // 参考图会真的发出去，必须提醒「点名物体」，否则上游可能近乎原样返回画布
      return (n ? `${base} · ${n} 张参考将随请求发送，提示词请点名要放入/改变的物体` : base) + queue;
    }
    if (state.mode === 'video') {
      if (!isRealVideo()) {
        return state.apiStatus?.video?.configured === false && !state.frontendDemo
          ? '视频链路未配置：proxy 缺少 AVM_COOKIE，当前为界面演示 · Enter 发送'
          : '视频生成为界面演示 · Enter 发送';
      }
      const v = state.apiStatus.video;
      const hasFrame = state.refs.some((r) => typeof r?.url === 'string' && r.url.startsWith('data:image/'));
      return `真实出片 · ${v.model} · ${v.tiers?.free?.resolution || '720p'}/${v.tiers?.free?.duration || 8}s`
        + ` · ${hasFrame ? '第 1 张参考作首帧' : '纯文生视频'} · Enter 发送`;
    }
    return 'Enter 发送，Shift + Enter 换行 · 演示模式不发起真实请求';
  }

  let popover = null;
  function closePopover() {
    if (!popover) return;
    popover.el.remove();
    popover.scrim.remove();
    popover.anchor?.classList.remove('is-open');
    popover = null;
  }
  function openPopover(anchor, html, opts = {}) {
    closePopover();
    anchor?.classList.add('is-open');
    const scrim = document.createElement('div');
    scrim.className = 'pop-scrim';
    scrim.addEventListener('click', closePopover);
    const el = document.createElement('div');
    el.className = 'popover';
    el.innerHTML = html;
    el.style.setProperty('--origin', opts.origin || 'bottom left');
    $('#popoverLayer').append(scrim, el);

    const rect = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = opts.align === 'right' ? rect.right - w : rect.left;
    let top = rect.top - h - 8;
    if (top < 8) top = Math.min(rect.bottom + 8, window.innerHeight - h - 8);
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    top = Math.max(8, top);
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
    popover = { el, scrim, anchor };
    return el;
  }

  /* ---------- 渲染：顶栏 / 模式 / 示例 ---------- */
  function syncLlmLabel() {
    const el = dramaEl('dramaModel');
    if (!el) return;
    const llm = state.apiStatus?.llm;
    el.textContent = llm?.configured ? llm.model : 'DreamScript';
    el.title = llm?.configured
      ? `剧本与镜头文字由 ${llm.model} 生成 · ${llm.base}`
      : '文本模型未配置：剧本走本地模板';
    el.dataset.llm = llm?.configured ? 'on' : 'off';
  }

  function renderApiBadge() {
    syncLlmLabel();
    let el = $('#apiBadge');
    if (!el) {
      el = document.createElement('span');
      el.id = 'apiBadge';
      el.className = 'api-badge';
      $('.topbar-left').appendChild(el);
    }
    if (state.mode === 'video') {
      const v = state.apiStatus?.video;
      const live = isRealVideo();
      el.classList.toggle('demo', !live);
      el.classList.remove('mock', 'unknown');
      el.title = live
        ? `真实出片：${v.model} @ ${v.base}；免费档 ${v.freeTier}；本进程 ${v.calls}/${v.maxCalls}`
        : (v?.configured === false ? '视频链路未配置：proxy 缺少 AVM_COOKIE，当前为界面演示' : '视频为界面演示：不发起上游请求');
      el.innerHTML = `<i class="led"></i>${live ? `真实出片 · ${v.model}` : '视频演示 · 零请求'}`;
      return;
    }
    const real = !!state.apiStatus?.real;
    const mock = state.apiStatus?.upstreamMode === 'mock';
    el.classList.toggle('demo', !real);
    el.classList.toggle('mock', real && mock);
    el.title = real
      ? (mock ? `可观测 MOCK 链路：完整 HTTP 往返但上游为 127.0.0.1，零计费；${state.apiStatus.model} @ ${state.apiStatus.base}`
              : `真实生成已就绪：${state.apiStatus.model} @ ${state.apiStatus.base}；固定 ${state.apiStatus.size}；本进程上限 ${state.apiStatus.maxCalls} 次`)
      : '演示模式：所有结果均为本地占位视觉，零网络请求';
    const unknown = !!state.apiStatus?.unknown;
    el.classList.toggle('unknown', unknown);
    const shownModel = currentImageModel() || state.apiStatus?.model || 'flux-3-image';
    el.innerHTML = `<i class="led"></i>${unknown ? '链路检测失败' : !real ? '演示模式 · 零请求' : mock ? 'MOCK 链路 · 零计费' : `真实生成 · ${shownModel}`}`;
  }

  function renderModeGlider() {
    const active = $('.mode-tab.is-active');
    const glider = $('#modeGlider');
    if (!active || !glider) return;
    const parent = $('#modeSwitch').getBoundingClientRect();
    const r = active.getBoundingClientRect();
    glider.style.width = `${r.width}px`;
    glider.style.transform = `translateX(${r.left - parent.left - 4}px)`;
  }
  function syncModeTabs() {
    $$('.mode-tab').forEach((b) => {
      const on = b.dataset.mode === state.mode;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', String(on));
    });
    renderModeGlider();
    $('#modeChip').textContent = state.typeLabel;
  }

  function renderInspiration() {
    const mode = INSPIRATIONS[state.mode] || INSPIRATIONS.image;
    const pool = [...mode, ...INSPIRATIONS.more];
    const off = state.inspireOffset % pool.length;
    const items = [0, 1, 2].map((i) => pool[(off + i) % pool.length]);
    $('#inspirationTitle').textContent = state.mode === 'agent' ? '热门技能' : '灵感推荐 · 试一试';
    $('#inspiration').innerHTML = items.map((it) => `
      <article class="insp-card" data-prompt="${esc(it.prompt)}" tabindex="0">
        <div class="insp-media">
          <img src="${ART(it.n)}" alt="${esc(it.title)}" loading="lazy">
          <span class="media-badge">${esc(it.tag)}</span>
          ${state.mode === 'video' ? `<span class="play-badge">${ico('play')}</span>` : ''}
        </div>
        <div class="insp-body">
          <div class="insp-title"><span>${esc(it.title)}</span><button class="try-btn" type="button">试一试</button></div>
          <div class="insp-desc">${esc(it.desc)}</div>
        </div>
      </article>`).join('');
  }

  /* ---------- 渲染：参数工具栏 ---------- */
  function pbtn(id, label, opts = {}) {
    return `<button class="param-btn ${opts.cls || ''}" id="${id}" type="button" ${opts.aria ? `aria-label="${opts.aria}"` : ''}>
      ${opts.icon ? ico(opts.icon) : ''}<span>${esc(label)}</span>${opts.caret === false ? '' : ico('chevron', 'caret')}
    </button>`;
  }
  function renderToolbar() {
    const s = state;
    let html = pbtn('pType', s.typeLabel, { icon: s.mode === 'video' ? 'video' : s.mode === 'image' ? 'image' : 'sparkle', cls: 'accent' });
    // 真实出片固定走上游槽位：参数条不能还挂着演示模型名
    html += pbtn('pModel', isRealVideo() ? 'minimaxH3' : s.model, { icon: 'magic' });
    if (s.mode === 'video') {
      const mode = isRealVideo() ? (videoFrame({ refs: s.refs }) ? '图生视频' : '文生视频') : s.refMethod;
      html += pbtn('pRefMethod', mode, { icon: 'ratio' });
    }
    if (s.mode === 'image') html += pbtn('pSize', `${s.ratio} ${s.resolution}${s.detail}`, { icon: 'ratio' });
    if (s.mode === 'video') {
      const vt = isRealVideo() ? videoTierInfo() : null;
      html += pbtn('pSize', `${s.ratio} ${vt ? vt.resolution.toUpperCase() : s.videoRes}`, { icon: 'ratio' });
      html += pbtn('pDuration', `${vt ? vt.duration : s.duration}s`, { icon: 'clock' });
      html += pbtn('pCount', vt ? '1个' : `${s.count}个`, { icon: 'tools' });
    } else {
      html += pbtn('pCount', s.mode === 'image' ? `${s.count}张` : `${s.count}个`, { icon: 'tools' });
    }
    html += `<button class="param-btn icon-only" id="pSettings" type="button" aria-label="创作设置" title="创作设置">${ico('gear')}</button>`;
    $('#toolbarLeft').innerHTML = html;

    $('#pType').onclick = (e) => typePopover(e.currentTarget);
    $('#pModel').onclick = (e) => modelPopover(e.currentTarget);
    if ($('#pRefMethod')) $('#pRefMethod').onclick = (e) => {
      if (isRealVideo()) {
        toast('生成方式由首帧自动决定：有首帧走图生视频，无首帧走文生视频', 'info');
        return;
      }
      simplePopover(e.currentTarget, '生成方式', REF_METHODS, state.refMethod, (v) => { state.refMethod = v; renderToolbar(); updateCost(); });
    };
    if ($('#pSize')) $('#pSize').onclick = (e) => sizePopover(e.currentTarget);
    if ($('#pDuration')) $('#pDuration').onclick = (e) => durationPopover(e.currentTarget);
    $('#pCount').onclick = (e) => countPopover(e.currentTarget);
    $('#pSettings').onclick = (e) => settingsPopover(e.currentTarget);
  }

  /* ---------- 模型接入配置（环境变量 > 页面配置 > 默认） ---------- */
  const CFG_SRC = {
    env: '环境变量', 'env-file': '环境变量·文件', page: '页面配置',
    default: '默认', 'default-file': '本机默认 .env', none: '未配置',
  };
  const cfgSrcClass = (src) => 'cfg-src ' + (
    src === 'env' || src === 'env-file' ? 'is-env'
      : src === 'page' ? 'is-page' : src === 'none' ? 'is-none' : '');

  function cfgStatusLine(msg, kind = '') {
    const el = $('#cfgStatus');
    el.textContent = msg || '';
    el.hidden = !msg;
    el.className = 'cfg-status' + (kind ? ' ' + kind : '');
  }

  async function fetchStatus(tries = 3) {
    // 单次瞬时失败不该把整页降级成「演示模式」：退避重试，最终失败才如实上报
    let lastErr;
    for (let i = 0; i < tries; i += 1) {
      try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (err) {
        lastErr = err;
        if (i < tries - 1) await new Promise((r) => setTimeout(r, 250 * (i + 1)));
      }
    }
    throw lastErr;
  }

  async function refreshServerStatus() {
    try {
      state.serverStatus = await fetchStatus();
      if (!state.frontendDemo) state.apiStatus = { ...state.serverStatus };
      state.statusReady = true;
      syncImageModelFromConfig();
      // 链路状态决定「参数条展示哪一档」：不重绘就会一直停在演示默认值
      renderApiBadge(); paintSbHead(); renderToolbar(); updateCost(); updateSend(); setHint(defaultHint());
      loadBilling();                  // key 可能刚热切换：额度来源跟着变
    } catch { /* 读取失败时保持现有状态，不假装成功 */ }
  }

  /* ---------- 积分：服务端账本是唯一权威值 ---------- */
  function fmtNum(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return '—';
    return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  function creditText(n) {
    return state.billing?.unlimited ? '无限' : fmtNum(n);
  }
  function creditSourceLabel(b = state.billing) {
    if (!b) return '读取中';
    if (b.source === 'new-api') return 'new-api 令牌额度';
    return b.enforce ? '本地账本' : '未接入额度';
  }

  async function loadBilling(force = false) {
    try {
      const b = await fetch(`/api/billing${force ? '?force=1' : ''}`, { cache: 'no-store' })
        .then((r) => r.json());
      if (!b || b.ok === false) throw new Error(b?.error || 'billing unavailable');
      state.billing = b;
      if (Number.isFinite(b.available)) state.credits = b.available;
      state.creditSource = b.source || 'local';
      updateCost();
      return b;
    } catch {
      return null;                    // 读不到就保留旧值，绝不假装余额已刷新
    }
  }

  function billingHtml() {
    const b = state.billing || {};
    const rows = [['可用积分', b.unlimited ? '无限' : fmtNum(b.available)],
                  ['资金来源', creditSourceLabel(b)]];
    if (b.source === 'new-api') {
      rows.push(['令牌总额度', b.unlimited ? '无限' : fmtNum(b.total)],
                ['已用', fmtNum(b.used)], ['冻结中', fmtNum(b.held)]);
    } else {
      rows.push(['账本余额', fmtNum(b.balance)], ['冻结中', fmtNum(b.held)],
                ['今日消耗', fmtNum(b.todaySpent)]);
    }
    if (b.enforce === false) rows.push(['计费状态', '未接入（不拦截）']);
    const priceSrc = { upstream: '上游定价表', env: '环境变量钉死', default: '默认值' }[b.priceSource] || '默认值';
    rows.push(['出图单价', `${fmtNum(b.imageCredits)} 积分/张 · ${priceSrc}`],
              ['换算口径', `1 USD = ${fmtNum(b.creditsPerUsd)} 积分 · ${fmtNum(b.quotaPerUsd)} quota`]);
    if (b.source === 'new-api' && Number(b.upstream?.unitUsd) && Number(b.upstream.unitUsd) !== 1) {
      rows.push(['额度单位折算', `1 站点单位 = ${b.upstream.unitUsd} USD`]);
    }
    const kindLabel = { hold: '冻结', settle: '结算', release: '释放', stale: '清理' };
    const ledger = (b.ledger || []).slice(0, 7).map((e) => {
      const t = new Date((e.ts || 0) * 1000);
      const hm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
      const sign = e.kind === 'settle' ? '−' : '';
      return `<div class="bill-entry"><span class="bill-kind is-${esc(e.kind)}">${kindLabel[e.kind] || esc(e.kind)}</span>
        <span class="bill-task">${esc((e.task_id || '').slice(-12))}</span>
        <span class="bill-amt">${sign}${fmtNum(e.credits)}</span><span class="bill-time">${hm}</span></div>`;
    }).join('') || '<div class="bill-empty">还没有流水</div>';
    const priceNote = b.priceSource === 'upstream'
      ? ' 单价取自上游 /api/pricing，换模型会自动跟随。'
      : ' 单价当前用默认值；上游定价表读不到时不会瞎猜。';
    const why = (b.source === 'new-api'
      ? '余额来自当前 sk- key 的 new-api 令牌额度；本页只读查询，不额外扣费。'
      : (b.enforce
        ? '本地持久化账本：提交冻结、成功结算、失败释放，重启不丢。'
        : `未接入 new-api 额度${b.upstream?.reason ? `（${esc(b.upstream.reason)}）` : ''}，当前只展示演示余额，不拦截生成。`)) + priceNote;
    return `<div class="pop-title">${ico('coin')}积分账户</div>
      <div class="bill-grid">${rows.map(([k, v]) => `<div class="bill-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div>
      <div class="pop-sep"></div>
      <div class="bill-head"><span>最近流水</span><span>${b.source === 'new-api' ? '上游额度 + 本地冻结' : '本地账本'}</span></div>
      <div class="bill-ledger">${ledger}</div>
      <button class="pop-opt bill-sync" id="billSync" type="button">${ico('refresh')}<span>同步上游额度</span><span class="opt-sub">只读</span></button>
      <div class="pop-note">${why} ${b.enforce
        ? `当前按 ${fmtNum(b.imageCredits)} 积分/张 1K 出图预扣，成功才结算。`
        : '当前不冻结、不结算；真实扣费以上游控制台为准。'}</div>`;
  }

  function billingPopover(anchor) {
    const el = openPopover(anchor, billingHtml(), { align: 'right' });
    const bind = () => {
      const btn = $('#billSync', el);
      if (btn) btn.onclick = async () => {
        btn.disabled = true;
        btn.querySelector('span').textContent = '同步中…';
        const b = await loadBilling(true);
        el.innerHTML = billingHtml();
        bind();
        const upOk = !!b && b.source === 'new-api';
        toast(b ? (upOk ? '已同步上游额度与单价' : '上游额度读不到，保留本地账本')
                : '同步失败：保留本地账本值', upOk ? 'ok' : 'error');
      };
    };
    bind();
    return el;
  }

  async function loadConfigInto() {
    const c = await fetch('/api/config', { cache: 'no-store' }).then((r) => r.json());
    state.cfg = c;
    syncImageModelFromConfig();

    // Base URL：输入框只显示「页面配置」里的值；没填就说明当前回退到哪一层
    const baseFallback = c.envBase ? `环境变量 ${c.envBase}` : `默认 ${c.defaultBase}`;
    $('#cfgBase').value = c.pageBase || '';
    $('#cfgBase').disabled = false;
    $('#cfgBase').placeholder = `未填，当前回退到${baseFallback}`;
    $('#cfgBaseSrc').textContent = CFG_SRC[c.baseSource] || c.baseSource;
    $('#cfgBaseSrc').className = cfgSrcClass(c.baseSource);
    $('#cfgBaseNote').textContent = c.pageBase
      ? `页面配置生效中：${c.base}；清空后回退到${baseFallback}。`
      : `页面未填，当前生效的是${baseFallback}。不带 /v1，代理会自动拼接。`;
    $('#cfgBaseNote').classList.remove('is-warn');

    // API Key：从不回显明文；用占位符说明「当前用的是哪一层的 Key」
    $('#cfgKey').value = '';
    $('#cfgKey').disabled = false;
    $('#cfgKey').type = 'password';
    $('#cfgReveal').textContent = '显示';
    $('#cfgKey').placeholder = c.pageHasKey
      ? `已配置 ${c.keyMasked}（页面配置），留空不改`
      : (c.envHasKey ? '未填，当前回退到环境变量里的 Key'
        : (c.keyConfigured ? `未填，当前回退到本机默认 .env（${c.keyMasked}）` : '尚未配置，请填写'));
    $('#cfgKeySrc').textContent = CFG_SRC[c.keySource] || c.keySource;
    $('#cfgKeySrc').className = cfgSrcClass(c.keySource);
    $('#cfgKeyNote').textContent = c.pageHasKey
      ? '页面 Key 生效中；只写入本机 0600 文件，不回传浏览器。'
      : (c.envHasKey ? '页面未填，当前使用环境变量里的 Key。'
        : (c.keyConfigured ? '页面未填，当前使用本机默认 .env 里的 Key。' : '尚未配置任何 Key。'));
    $('#cfgKeyNote').classList.remove('is-warn');
    $('#cfgClearKey').hidden = !c.pageHasKey;

    $('#cfgSave').disabled = false;
    $('#cfgClear').disabled = false;
    cfgStatusLine('');
    renderModels(null);
    if (c.keyConfigured) loadModels();   // 不 await：弹窗先出来，列表后到
    return c;
  }

  function renderModels(data) {
    const box = $('#cfgModels'), cnt = $('#cfgModelsCount');
    if (!data) {
      cnt.textContent = '未探测'; cnt.className = 'cfg-src';
      box.innerHTML = '<p class="cfg-models-empty">尚未探测</p>';
      return;
    }
    if (!data.ok) {
      cnt.textContent = '失败'; cnt.className = 'cfg-src is-none';
      box.innerHTML = `<p class="cfg-models-empty">${esc(data.error || '拉取失败')}</p>`;
      return;
    }
    const list = data.models || [];
    cnt.textContent = `${list.length} 个 · ${data.elapsedMs}ms`;
    cnt.className = 'cfg-src' + (data.hasModel ? '' : ' is-none');
    if (!list.length) {
      box.innerHTML = '<p class="cfg-models-empty">接口返回了空列表</p>';
      return;
    }
    const current = (state.cfg && state.cfg.model) || 'flux-3-image';
    box.innerHTML = list.map((id) => {
      const cur = id === current;
      return `<div class="cfg-model${cur ? ' is-current' : ''}"><i class="dot"></i><code>${esc(id)}</code>`
        + (cur ? '<span class="m-tag">当前</span>' : '') + '</div>';
    }).join('');
    if (!data.hasModel) {
      box.insertAdjacentHTML('beforeend',
        `<p class="cfg-models-empty">列表里没有 ${esc(current)}，本页无法生成</p>`);
    }
  }

  async function loadModels() {
    if (!state.cfg?.keyConfigured) {
      renderModels({ ok: false, error: '未配置 API Key，无法拉取模型列表' });
      return;
    }
    const btn = $('#cfgModelsRefresh');
    btn.classList.add('is-busy');
    $('#cfgModelsCount').textContent = '拉取中…';
    $('#cfgModelsCount').className = 'cfg-src';
    try {
      const res = await fetch('/api/models', { cache: 'no-store' });
      renderModels(await res.json());
    } catch (err) {
      renderModels({ ok: false, error: err?.message || String(err) });
    } finally {
      btn.classList.remove('is-busy');
    }
  }

  async function openConfig() {
    closePopover();
    $('#configModal').hidden = false;
    $('.app').classList.add('cfg-open');
    cfgStatusLine('');
    $('#cfgBase').disabled = false;
    $('#cfgKey').disabled = false;
    $('#cfgBase').value = '';
    $('#cfgKey').value = '';
    $('#cfgBaseSrc').textContent = '读取中';
    $('#cfgKeySrc').textContent = '读取中';
    try {
      await loadConfigInto();
      setTimeout(() => {
        if (!$('#cfgBase').disabled) $('#cfgBase').focus();
        else if (!$('#cfgKey').disabled) $('#cfgKey').focus();
      }, 60);
    } catch (err) {
      cfgStatusLine('读取配置失败：' + (err?.message || err), 'err');
    }
  }

  async function clearPageKey() {
    try {
      const res = await fetch('/api/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apiKey: '', clearKey: true }),
      });
      const d = await res.json().catch(() => ({ ok: false, error: '非 JSON 响应' }));
      if (!res.ok || !d.ok) { cfgStatusLine(d.error || `清除失败（HTTP ${res.status}）`, 'err'); return; }
      await loadConfigInto();
      await refreshServerStatus();
      cfgStatusLine('已清除页面 Key，回退到环境变量或默认', 'ok');
    } catch (err) {
      cfgStatusLine('清除失败：' + (err?.message || err), 'err');
    }
  }

  function closeConfig() {
    $('#configModal').hidden = true;
    $('.app').classList.remove('cfg-open');
  }

  function cfgPayload() {
    const body = {};
    if (!$('#cfgBase').disabled) body.base = $('#cfgBase').value.trim();
    const k = $('#cfgKey').value.trim();
    if (!$('#cfgKey').disabled && k) body.apiKey = k;
    return body;
  }

  async function saveConfig() {
    const btn = $('#cfgSave');
    btn.disabled = true;
    cfgStatusLine('正在保存…');
    try {
      const res = await fetch('/api/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfgPayload()),
      });
      const d = await res.json().catch(() => ({ ok: false, error: '非 JSON 响应' }));
      if (!res.ok || !d.ok) throw new Error(d.error || `HTTP ${res.status}`);
      await loadConfigInto();
      await refreshServerStatus();
      cfgStatusLine(`已生效：${d.upstreamMode === 'mock' ? 'MOCK 链路（零计费）' : '真实链路'} · 地址来源 ${CFG_SRC[d.config.baseSource] || d.config.baseSource} · 密钥来源 ${CFG_SRC[d.config.keySource] || d.config.keySource}`, 'ok');
      toast('配置已保存并热生效', 'success');
    } catch (err) {
      cfgStatusLine('保存失败：' + (err?.message || err), 'err');
    } finally {
      btn.disabled = false;
    }
  }

  async function testConfig() {
    const btn = $('#cfgTest');
    btn.disabled = true;
    cfgStatusLine('正在探测 /v1/models（只读，不发生成请求）…');
    try {
      const res = await fetch('/api/config/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cfgPayload()),
      });
      const d = await res.json().catch(() => ({ ok: false, error: '非 JSON 响应' }));
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      if (!d.ok) {
        cfgStatusLine(`连接失败：${d.error || '未知错误'}${d.httpStatus ? `（HTTP ${d.httpStatus}）` : ''}`, 'err');
        return;
      }
      renderModels(d);
      cfgStatusLine(
        d.hasModel
          ? `连接成功 · ${d.elapsedMs}ms · 已列出 ${d.count} 个模型`
          : `连接成功，但列表里没有 ${d.model}，本页无法生成`,
        d.hasModel ? 'ok' : 'err');
    } catch (err) {
      cfgStatusLine('探测失败：' + (err?.message || err), 'err');
    } finally {
      btn.disabled = false;
    }
  }

  async function clearConfig() {
    if (!confirm('清除页面保存的 Base URL 与 API Key？将回落到环境变量或默认配置。')) return;
    try {
      const res = await fetch('/api/config', { method: 'DELETE' });
      const d = await res.json().catch(() => ({ ok: false, error: '非 JSON 响应' }));
      if (!res.ok || !d.ok) {
        cfgStatusLine(d.error || `清除失败（HTTP ${res.status}）`, 'err');
        return;
      }
      await loadConfigInto();
      await refreshServerStatus();
      cfgStatusLine('已清除页面配置', 'ok');
      toast('页面配置已清除');
    } catch (err) {
      cfgStatusLine('清除失败：' + (err?.message || err), 'err');
    }
  }

  function simplePopover(anchor, title, options, current, onPick) {
    const html = `<div class="pop-title">${ico('gear')}${esc(title)}</div>` +
      options.map((o) => `<button class="pop-opt ${o === current ? 'is-selected' : ''}" data-v="${esc(o)}" type="button">
        <span>${esc(o)}</span>${o === current ? ico('check', 'check') : ''}</button>`).join('');
    const el = openPopover(anchor, html);
    $$('.pop-opt', el).forEach((b) => b.onclick = () => { onPick(b.dataset.v); closePopover(); });
  }

  function typePopover(anchor) {
    const html = `<div class="pop-title">${ico('create')}创作类型</div>` +
      TYPES.map((t) => `<button class="pop-opt ${t.label === state.typeLabel ? 'is-selected' : ''}" data-id="${t.id}" data-label="${esc(t.label)}" type="button">
        ${ico(t.icon)}<span>${esc(t.label)}</span>${t.label === state.typeLabel ? ico('check', 'check') : ''}</button>`).join('') +
      `<div class="pop-sep"></div><div class="pop-note">图片生成可接真实 flux-3-image；其余类型在复刻版中以演示数据呈现。</div>`;
    const el = openPopover(anchor, html);
    $$('.pop-opt', el).forEach((b) => b.onclick = () => {
      const id = b.dataset.id;
      state.typeLabel = b.dataset.label;
      const ms = id === 'image' ? configuredModels() : [];
      state.model = ms.length ? ms[0] : (MODELS[id] ? MODELS[id][0] : state.model);
      if (id === 'image' || id === 'video' || id === 'agent') state.mode = id;
      else { state.mode = 'agent'; toast(`${state.typeLabel} 为演示类型，使用 Agent 参数区`, 'info'); }
      $('#prompt').placeholder = placeholder();
      syncModeTabs(); renderInspiration(); renderToolbar(); updateCost(); setHint(defaultHint());
      closePopover();
    });
  }
  function modelPopover(anchor) {
    const realModels = state.mode === 'image' ? configuredModels() : [];
    const list = realModels.length ? realModels : (MODELS[state.mode] || MODELS.image);
    const html = `<div class="pop-title">${ico('magic')}模型</div>` +
      list.map((m) => {
        const unit = Number(state.billing?.prices?.[m]);
        const tag = Number.isFinite(unit) ? `<span class="opt-sub">${fmtNum(unit)} 积分/张</span>` : '';
        return `<button class="pop-opt ${m === state.model ? 'is-selected' : ''}" data-v="${esc(m)}" type="button">
        <span>${esc(m)}</span>${tag}${m === state.model ? ico('check', 'check') : ''}</button>`;
      }).join('') +
      `<div class="pop-sep"></div><div class="pop-note">${state.mode === 'video'
        ? '真实出片固定走上游槽位 minimaxH3（写 minimax_h3 会被 400）；此处模型名仅影响界面展示与演示结果。'
        : realModels.length
          ? '模型来自快捷配置 models=…；实际请求会按所选模型发送，服务端只接受列表内的值。'
          : '真实链路由本地代理固定为 flux-3-image；此处模型名仅影响界面展示与演示结果。'}</div>`;
    const el = openPopover(anchor, html);
    $$('.pop-opt', el).forEach((b) => b.onclick = () => {
      state.model = b.dataset.v;
      renderToolbar();
      updateCost();                 // 换模型就换单价：预估积分必须当场跟着变
      closePopover();
    });
  }
  function sizePopover(anchor) {
    if (state.mode === 'video' && isRealVideo()) return videoSizePopover(anchor);
    const isImg = state.mode === 'image';
    const resList = isImg ? ['1K', '2K'] : ['720P', '1080P'];
    const currentRes = isImg ? state.resolution : state.videoRes;
    const html = `<div class="pop-title">${ico('ratio')}画面比例</div>
      <div class="ratio-grid">${RATIOS.map((r) => {
        const [a, b] = r.split(':').map(Number);
        const w = Math.round(17 * Math.min(1, a / b)), h = Math.round(14 * Math.min(1, b / a));
        return `<button class="ratio-opt ${r === state.ratio ? 'is-selected' : ''}" data-r="${r}" type="button" title="${r}">
          <span class="ratio-shape" style="width:${w}px;height:${h}px"></span>${r}</button>`;
      }).join('')}</div>
      <div class="pop-section-label">${ico('hd')}分辨率</div>
      <div class="seg">${resList.map((r) => `<button data-res="${r}" class="${r === currentRes ? 'is-selected' : ''}" type="button">${r}</button>`).join('')}</div>
      ${isImg ? `<div class="pop-section-label">${ico('sparkle')}细节生成程度</div>
        <div class="seg">${DETAILS.map((d) => `<button data-detail="${d}" class="${d === state.detail ? 'is-selected' : ''}" type="button">${d}</button>`).join('')}</div>` : ''}
      <div class="pop-note">分辨率决定像素尺寸，细节生成程度影响细节表现与耗时。真实生成固定 1024×1024（1K）以控制成本。</div>`;
    const el = openPopover(anchor, html, { origin: 'bottom left' });
    $$('.ratio-opt', el).forEach((b) => b.onclick = () => { state.ratio = b.dataset.r; renderToolbar(); sizePopover($('#pSize')); });
    $$('[data-res]', el).forEach((b) => b.onclick = () => {
      if (isImg) state.resolution = b.dataset.res; else state.videoRes = b.dataset.res;
      renderToolbar(); updateCost(); sizePopover($('#pSize'));
    });
    $$('[data-detail]', el).forEach((b) => b.onclick = () => { state.detail = b.dataset.detail; renderToolbar(); sizePopover($('#pSize')); });
  }
  function videoSizePopover(anchor) {
    const v = state.apiStatus.video;
    const vt = videoTierInfo();
    const hasFrame = !!videoFrame({ refs: state.refs });
    const ratios = (v.ratios || ['9:16', '16:9', '1:1']).filter((r) => r !== 'adaptive');
    const html = `<div class="pop-title">${ico('ratio')}画面比例</div>
      ${hasFrame
        ? `<div class="pop-note">已上传首帧：成片比例跟随首帧画面（上游 ratio=adaptive）。移除首帧后可自选比例。</div>`
        : `<div class="ratio-grid">${ratios.map((r) => {
            const [a, b] = r.split(':').map(Number);
            const w = Math.round(17 * Math.min(1, a / b)), h = Math.round(14 * Math.min(1, b / a));
            return `<button class="ratio-opt ${r === state.ratio ? 'is-selected' : ''}" data-r="${r}" type="button" title="${r}">
              <span class="ratio-shape" style="width:${w}px;height:${h}px"></span>${r}</button>`;
          }).join('')}</div>`}
      <div class="pop-section-label">${ico('hd')}分辨率 / 时长</div>
      <div class="seg">
        <button data-vtier="free" class="${vt.tier === 'free' ? 'is-selected' : ''}" type="button">720P · 8s 免费</button>
        <button data-vtier="paid" class="${vt.tier === 'paid' ? 'is-selected' : ''}" type="button" ${vt.allowPaid ? '' : 'disabled title="服务端未开启计费档（AVM_ALLOW_PAID=1）"'}>1080P · 10s 计费</button>
      </div>
      <div class="pop-note">720p/8s 走免费额度（tier=turbo，上游 paid=false），不扣页面积分。1080p/10s 计价以账单为准，默认关闭。</div>`;
    const el = openPopover(anchor, html, { origin: 'bottom left' });
    $$('.ratio-opt', el).forEach((b) => b.onclick = () => {
      state.ratio = b.dataset.r; renderToolbar(); setHint(defaultHint()); videoSizePopover($('#pSize'));
    });
    $$('[data-vtier]', el).forEach((b) => b.onclick = () => {
      if (b.dataset.vtier === 'paid' && !vt.allowPaid) { toast('计费档未开启：服务端需设 AVM_ALLOW_PAID=1', 'error'); return; }
      state.videoTier = b.dataset.vtier;
      renderToolbar(); updateCost(); setHint(defaultHint()); videoSizePopover($('#pSize'));
    });
  }

  function durationPopover(anchor) {
    if (state.mode === 'video' && isRealVideo()) {
      const vt = videoTierInfo();
      const html = `<div class="pop-title">${ico('clock')}选择视频生成时长</div>
        <div class="seg">
          <button data-vtier="free" class="${vt.tier === 'free' ? 'is-selected' : ''}" type="button">8s · 免费</button>
          <button data-vtier="paid" class="${vt.tier === 'paid' ? 'is-selected' : ''}" type="button" ${vt.allowPaid ? '' : 'disabled title="需服务端开启计费档"'}>10s · 计费</button>
        </div>
        <div class="pop-note">时长与分辨率绑定：免费档固定 720p/8s；720p 9s 起就计费，1080p 无免费线。</div>`;
      const el = openPopover(anchor, html);
      $$('[data-vtier]', el).forEach((b) => b.onclick = () => {
        if (b.dataset.vtier === 'paid' && !vt.allowPaid) { toast('计费档未开启：服务端需设 AVM_ALLOW_PAID=1', 'error'); return; }
        state.videoTier = b.dataset.vtier;
        renderToolbar(); updateCost(); setHint(defaultHint()); closePopover();
      });
      return;
    }
    const html = `<div class="pop-title">${ico('clock')}选择视频生成时长</div>
      <div class="slider-row"><span>4s</span><input type="range" min="4" max="15" step="1" value="${state.duration}" id="durRange"><output id="durOut">${state.duration}s</output></div>
      <div class="pop-note">演示口径：时长同时影响积分估算，不发起真实视频生成。</div>`;
    const el = openPopover(anchor, html);
    const range = $('#durRange', el);
    range.oninput = () => { state.duration = Number(range.value); $('#durOut', el).textContent = `${state.duration}s`; renderToolbar(); updateCost(); };
  }
  function countPopover(anchor) {
    if (state.mode === 'video' && isRealVideo()) {
      toast('真实出片一次只产 1 个视频，数量不可调', 'info');
      return;
    }
    const list = state.mode === 'image' ? [1, 2, 4] : [1, 2];
    const unit = state.mode === 'image' ? '张' : '个';
    simplePopover(anchor, '生成数量', list.map((n) => `${n}${unit}`), `${state.count}${unit}`, (v) => {
      state.count = parseInt(v, 10); renderToolbar(); updateCost();
      if (isRealCall() && state.count > 1) toast('真实模式固定生成 1 张，数量设置仅影响演示', 'info');
    });
  }
  function settingsPopover(anchor) {
    const real = isRealCall();
    const html = `<div class="pop-title">${ico('gear')}创作设置</div>
      ${state.mode === 'video'
        ? `<div class="pop-opt" style="cursor:default">${ico('sparkle')}<span>出片链路</span><span class="opt-sub">${isRealVideo() ? `${state.apiStatus.video.model} 真实` : '本地演示'}</span></div>
           <div class="pop-opt" style="cursor:default">${ico('ratio')}<span>真实档位</span><span class="opt-sub">${(() => { const t = videoTierInfo(); return `${t.resolution} / ${t.duration}s${t.allowPaid ? '' : '（计费档关闭）'}`; })()}</span></div>
           <div class="pop-opt" style="cursor:default">${ico('coin')}<span>本进程已调用</span><span class="opt-sub">${state.apiStatus?.video?.calls ?? 0} / ${state.apiStatus?.video?.maxCalls ?? '-'}</span></div>`
        : `<div class="pop-opt" style="cursor:default">${ico('sparkle')}<span>生成链路</span><span class="opt-sub">${real ? 'flux-3-image 真实' : '本地演示'}</span></div>
           <div class="pop-opt" style="cursor:default">${ico('ratio')}<span>真实尺寸</span><span class="opt-sub">1024×1024 · 1K</span></div>
           <div class="pop-opt" style="cursor:default">${ico('coin')}<span>本进程已调用</span><span class="opt-sub">${state.apiStatus?.calls ?? 0} / ${state.apiStatus?.maxCalls ?? '-'}</span></div>`}
      <div class="pop-sep"></div>
      <button class="pop-opt" id="popToggleReal" type="button">${ico('refresh')}<span>${real ? '切换为演示模式' : '尝试启用真实生成'}</span></button>
      <button class="pop-opt" id="popForceLow" type="button">${ico('warn')}<span>模拟积分不足（脏态自检）</span></button>
      <div class="pop-note">这里的开关只影响本页展示，不会改动代理；模型接入请用左下角入口。</div>`;
    const el = openPopover(anchor, html, { align: 'right' });
    $('#popToggleReal', el).onclick = () => {
      state.frontendDemo = !state.frontendDemo;
      if (state.frontendDemo) { state.apiStatus = { ...(state.apiStatus || {}), real: false }; toast('已切换为演示模式，零网络请求'); }
      else { state.apiStatus = state.serverStatus; toast(state.apiStatus?.real ? '真实生成已启用' : '代理未就绪，仍为演示模式'); }
      renderApiBadge(); paintSbHead(); updateCost(); setHint(defaultHint()); closePopover();
    };
    $('#popForceLow', el).onclick = () => {
      state.credits = state.credits <= 10 ? 2000 : 10;
      updateCost(); closePopover();
      toast(state.credits <= 10 ? '余额已设为 10，可验证积分不足错误态' : '余额已恢复 2000');
    };
  }
  function updateCost() {
    const c = estimateCredits();
    const free = state.mode === 'video' && isRealVideo();
    const unlimited = !!state.billing?.unlimited;
    const enforce = state.billing?.enforce !== false;
    $('#costValue').textContent = free ? '免费' : c;
    $('#costChip').classList.toggle('over', !free && enforce && !unlimited && c > state.credits);
    $('#costChip').title = free
      ? `本次走 minimaxH3 免费额度（${videoTierInfo().resolution} / ${videoTierInfo().duration}s），不扣页面积分`
      : `本次预计消耗 ${c} 积分 · ${unlimited ? '无限额度' : `可用 ${fmtNum(state.credits)}`}（${creditSourceLabel()}${enforce ? '' : '，不拦截'}）`;
    $('#creditBalance').textContent = creditText(state.credits);
  }

  /* ---------- 参考素材：上传即压缩，最多 10 张 ---------- */
  const MAX_REFS = 10;
  const REF_MAX_SIDE = 1280;     // 长边压到 1280：够 i2i 用，又不至于把请求体撑爆
  const REF_QUALITY = 0.85;
  const REF_MAX_FILE = 20 * 1024 * 1024;

  function fileToRef(file) {
    return new Promise((resolve, reject) => {
      if (!file.type || !file.type.startsWith('image/')) {
        return reject(new Error(`${file.name || '文件'} 不是图片`));
      }
      if (file.size > REF_MAX_FILE) {
        return reject(new Error(`${file.name || '文件'} 超过 20MB`));
      }
      const objUrl = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(objUrl);
        const scale = Math.min(1, REF_MAX_SIDE / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
        const w = Math.max(1, Math.round((img.naturalWidth || 1) * scale));
        const h = Math.max(1, Math.round((img.naturalHeight || 1) * scale));
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        const ctx = c.getContext('2d');
        ctx.fillStyle = '#fff';           // 透明底铺白，否则 JPEG 会变黑
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        const url = c.toDataURL('image/jpeg', REF_QUALITY);
        resolve({ name: (file.name || '参考图').replace(/\.[^.]+$/, ''), url, w, h,
                  bytes: Math.round((url.length - 23) * 0.75) });
      };
      img.onerror = () => { URL.revokeObjectURL(objUrl); reject(new Error(`${file.name || '图片'} 无法解码`)); };
      img.src = objUrl;
    });
  }

  async function urlToRef(url, name) {
    const res = await fetch(url, { cache: 'force-cache' });
    if (!res.ok) throw new Error(`读取示例图失败（HTTP ${res.status}）`);
    const blob = await res.blob();
    return fileToRef(new File([blob], name, { type: blob.type || 'image/svg+xml' }));
  }

  async function addRefs(items, { silent = false } = {}) {
    const room = MAX_REFS - state.refs.length;
    if (room <= 0) { toast(`参考图最多 ${MAX_REFS} 张`, 'error'); return 0; }
    const refs = [];
    for (const it of items.slice(0, room)) {
      try { refs.push(await it()); } catch (err) { toast(err.message || '添加失败', 'error'); }
    }
    if (refs.length) {
      refs.forEach((r) => state.refs.push(r));
      renderRefs();
      // 提示行与费用都依赖 refs，必须一起刷新，否则文案还停在「无参考」状态
      updateCost(); updateSend(); setHint(defaultHint());
      if (!silent) toast(`已添加 ${refs.length} 张参考图（第 1 张作为画布）`);
    }
    if (items.length > room) toast(`已达上限，只添加了前 ${room} 张`, 'error');
    return refs.length;
  }

  /* ---------- 渲染：参考素材 ---------- */
  function renderRefs() {
    const tray = $('#refTray');
    tray.hidden = state.refs.length === 0;
    tray.innerHTML = state.refs.map((r, i) => `
      <div class="ref-chip" title="${esc(r.name)}">
        ${r.url ? `<img src="${esc(r.url)}" alt="">` : `<div class="ref-fallback">${ico('image')}</div>`}
        <em class="ref-index">${i === 0 ? (state.mode === 'video' ? '首帧' : '画布') : i + 1}</em>
        <button class="ref-remove" data-i="${i}" aria-label="移除参考">${ico('close')}</button>
      </div>`).join('') + (state.refs.length < MAX_REFS
      ? `<button class="ref-chip add-more" id="refMore" aria-label="添加参考">${ico('plus')}</button>`
      : `<div class="ref-limit">已满 ${MAX_REFS} 张</div>`);
    $$('.ref-remove', tray).forEach((b) => b.onclick = () => {
      state.refs.splice(Number(b.dataset.i), 1);
      renderRefs(); updateCost(); updateSend(); setHint(defaultHint());
    });
    if ($('#refMore', tray)) $('#refMore', tray).onclick = (e) => refPopover(e.currentTarget);
  }
  function refPopover(anchor) {
    const html = `<div class="pop-title">${ico('upload')}添加参考</div>
      <button class="pop-opt" id="refLocal" type="button">${ico('upload')}<span>上传本地图片 / 音视频</span></button>
      <button class="pop-opt" id="refSample" type="button">${ico('image')}<span>使用内置示例参考图</span></button>
      <div class="pop-note">${state.mode === 'video'
        ? (isRealVideo()
          ? `视频：第 1 张作为<b>首帧</b>随真实请求发送，其余 ${MAX_REFS - 1} 张不参与本次生成（上游只收一张首帧）。`
          : `视频当前为演示模式，参考图不会发送。上传即压到长边 ${REF_MAX_SIDE}px 的 JPEG。`)
        : `图片参考会随真实请求发送（第 1 张作为画布，最多 ${MAX_REFS} 张）。上传即压到长边 ${REF_MAX_SIDE}px 的 JPEG；Agent 为演示模式，不转发参考图。`}</div>`;
    const el = openPopover(anchor, html);
    $('#refLocal', el).onclick = () => { closePopover(); $('#fileInput').click(); };
    $('#refSample', el).onclick = () => {
      closePopover();
      const n = 1 + (state.refs.length % 12);
      // 示例图是本机 SVG：必须走同一条压缩链路，否则它不是 data URI、代理会拒
      addRefs([() => urlToRef(ART(n), `示例参考 ${n}.svg`)]);
    };
  }

  /* ---------- 渲染：会话列表 / 结果 ---------- */
  function seedSessions() {
    const now = Date.now();
    state.sessions = [
      { id: 's1', title: '商品图场景合成', mode: 'image', model: 'Seedream 5.0 Lite', prompt: '高端琥珀色香水瓶置于天然洞石台面，暖金色侧逆光，奶油色背景，商业产品摄影', params: ['图片生成', 'Seedream 5.0 Lite', '1:1 2K超高', '4 张'], images: [1, 2, 3, 11].map((n) => ({ url: ART(n), source: 'demo' })), at: now - 3600e3 },
      { id: 's2', title: '日系青春短片', mode: 'video', model: '即梦 Seedance 2.0 mini', prompt: '日系青春短片，夏日草地上穿白衬衫的少女逆风奔跑，清新柔光，胶片颗粒', params: ['视频生成', '即梦 Seedance 2.0 mini', '16:9 1080P', '5s'], images: [{ url: ART(4), source: 'demo', video: true, duration: 5 }], at: now - 7200e3 },
      { id: 's3', title: '极简耳机海报', mode: 'image', model: '图片美学模型 V8.2', prompt: '黑灰极简耳机产品海报，悬浮的哑光金属头戴耳机，毛玻璃 UI 元素', params: ['图片生成', '图片美学模型 V8.2', '1:1 2K超高', '4 张'], images: [3, 8, 9, 12].map((n) => ({ url: ART(n), source: 'demo' })), at: now - 86400e3 },
    ];
    state.assets = Array.from({ length: 12 }, (_, i) => ({ url: ART(i + 1), name: ['商品图场景合成', '镜面珠宝人像', '极简耳机海报', '日系青春短片', '高燃汉堡宣传', '时尚换装快切', '月光下的花园', '东方极简', '赛博城市夜景', '水母在海底遨游', '文艺复兴猫咪', '粉色星球飞行器'][i], type: i === 3 || i === 4 || i === 5 ? 'video' : 'image', fav: i === 1 || i === 9 }));
  }
  function renderSessions() {
    const html = `<button class="session-new session-item" id="sessNew" type="button">${ico('plus')}<span>新对话</span></button>` +
      state.sessions.map((s) => `<div class="session-item ${state.activeSession === s.id ? 'is-active' : ''}" data-id="${s.id}" role="option" tabindex="0">
        ${s.images && s.images.length
          ? (s.images[0].video && isVideoFile(s.images[0].url)
            ? `<span class="thumb thumb-video" aria-hidden="true">${ico('play')}</span>`
            : `<img class="thumb" src="${esc(s.images[0].url)}" alt="">`)
          : `<span class="thumb thumb-empty" aria-hidden="true">${ico(s.error ? 'warn' : 'image')}</span>`}<span class="label">${esc(s.title)}</span>
        <button class="sess-more" data-del="${s.id}" aria-label="删除对话">${ico('close')}</button></div>`).join('');
    $('#sessionList').innerHTML = html;
    $('#sessNew').onclick = () => { state.jobs.clear(); saveJobs(); newChat(); syncBusy(); };
    $$('.session-item[data-id]').forEach((el) => {
      el.onclick = (e) => { if (e.target.closest('[data-del]')) return; openSession(el.dataset.id); };
      el.onkeydown = (e) => { if (e.key === 'Enter') openSession(el.dataset.id); };
    });
    $$('[data-del]').forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      state.sessions = state.sessions.filter((s) => s.id !== b.dataset.del);
      saveSessions();
      if (state.activeSession === b.dataset.del) newChat(); else renderSessions();
      toast('对话已删除');
    });
  }
  function newChat() {
    state.activeSession = 'new';
    state.queueView = false;
    $('#welcome').hidden = false;
    $('#conversation').hidden = true;
    $('#crumb').textContent = '创作 / 默认创作';
    renderSessions();
  }
  function openSession(id) {
    const s = state.sessions.find((x) => x.id === id);
    if (!s) return;
    state.activeSession = id;
    state.queueView = false;
    $('#welcome').hidden = true;
    $('#conversation').hidden = false;
    $('#crumb').textContent = `创作 / ${s.title}`;
    renderConversation();
    renderSessions();
    $('#stage').scrollTop = 0;
  }

  function isVideoFile(url) { return /\.mp4(\?|$)/i.test(String(url || '')); }
  function resultCardHtml(img, i) {
    const playable = !!img.video && isVideoFile(img.url);
    const srcLabel = img.source === 'real' ? (img.video ? 'REAL · minimaxH3' : 'REAL · flux-3-image')
      : img.source === 'mock' ? 'MOCK · 零计费' : 'DEMO';
    return `<div class="result-card ${img.video ? 'wide' : ''}${playable ? ' is-video-file' : ''}" data-i="${i}" tabindex="0">
      ${playable
        ? `<video src="${esc(img.url)}" controls playsinline preload="metadata"></video>`
        : `<img src="${esc(img.url)}" alt="生成结果 ${i + 1}" loading="lazy">`}
      <span class="source-tag ${img.source === 'real' ? 'real' : img.source === 'mock' ? 'mock' : ''}">${srcLabel}</span>
      ${img.video && !playable ? `<span class="duration-badge">${img.duration || 5}s</span><span class="video-play">${ico('play')}</span>` : ''}
      <div class="result-overlay">
        <button class="round-btn" data-act="zoom" data-i="${i}" aria-label="放大">${ico('expand')}</button>
        <button class="round-btn" data-act="download" data-i="${i}" aria-label="下载">${ico('download')}</button>
        <button class="round-btn" data-act="same" data-i="${i}" aria-label="做同款">${ico('refresh')}</button>
      </div>
    </div>`;
  }
  function renderConversation(s = state.sessions.find((x) => x.id === state.activeSession)) {
    if (!s) return;
    const isVideo = s.mode === 'video';
    const running = s.status === 'running';
    const cls = running ? 'cols-1' : isVideo ? 'cols-video' : s.images.length === 1 ? 'cols-1' : s.images.length === 2 ? 'cols-2' : 'cols-4';
    $('#conversation').innerHTML = `
      <div class="conv-head">
        <div><h2>${esc(s.title)}</h2><p>${new Date(s.at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · ${running ? '生成中' : `${s.images.length} 个结果`} · ${s.error ? '生成失败' : s.source === 'real' ? '真实生成' : s.source === 'mock' ? 'MOCK 链路（零计费）' : '演示数据'}</p></div>
        <div class="conv-head-actions">
          ${running ? `<button class="pill-btn" id="convStopWait" type="button">${ico('close')}停止等待</button>` : ''}
          <button class="pill-btn" id="convAgain" type="button">${ico('refresh')}再次生成</button>
          <button class="pill-btn primary" id="convSame" type="button">${ico('magic')}做同款</button>
        </div>
      </div>
      <div class="msg-user"><div class="bubble-user">${esc(s.prompt)}<div class="bubble-tags">${s.params.map((t) => `<span class="bubble-tag">${esc(t)}</span>`).join('')}</div></div></div>
      <div class="msg-ai">
        <div class="ai-avatar">${ico('sparkle')}</div>
        <div class="ai-body">
          <div class="ai-status ${s.error ? 'is-error' : ''}">${running
            ? `<span class="spinner"></span><span>生成中</span><span class="sep">·</span><span>${esc(s.model || 'minimaxH3')}</span><span class="sep">·</span><span>${esc(s.vidMeta?.resolution || '720p')} / ${s.vidMeta?.duration || 8}s</span><span class="sep">·</span><span>已用 <b id="convVidClock">${s.vidElapsed || '0s'}</b></span>`
            : s.error
            ? `${ico('warn')}<span>生成失败</span><span class="sep">·</span><span>${esc(s.model || '即梦 AI')}</span>`
            : `${ico('check', 'ok')}<span class="ok">已完成</span><span class="sep">·</span><span>${esc(s.model || '即梦 AI')}</span><span class="sep">·</span><span>${s.elapsed ? `耗时 ${s.elapsed}` : '本地渲染'}</span>`}</div>
          ${s.error ? `<div class="alert-bar">${ico('warn')}<span>${esc(s.error)}</span><button class="alert-retry" id="convRetry" type="button">${ico('refresh')}重试</button></div>` : ''}
          ${running ? `<div class="gen-bar"><span id="convVidBar"></span></div>
            <div class="gen-foot"><span class="who">minimaxH3</span><span>任务 ${esc(s.vidId || '')}</span><span class="sep">·</span><span id="convVidPhase">${s.vidPhase === 'queued' ? '上游排队中' : '上游生成中'}</span></div>
            <div class="gen-grid n1 is-portrait"><div class="gen-tile"><span class="gen-pct" id="convVidPct">0%</span></div></div>
            <p class="gen-note">按 ${Math.round(VIDEO_EXPECT_MS / 1000)}s 估算进度；刷新页面会自动接续轮询，上游任务不会被取消。</p>` : ''}
          ${s.images.length ? `<div class="result-grid ${cls}">${s.images.map(resultCardHtml).join('')}</div>` : ''}
          ${s.images.length ? `<div class="result-actions">
            <button class="pill-btn" data-bulk="download" type="button">${ico('download')}全部下载</button>
            <button class="pill-btn" data-bulk="hd" type="button">${ico('hd')}超清处理</button>
            <button class="pill-btn" data-bulk="canvas" type="button">${ico('canvas')}去画布编辑</button>
            <button class="pill-btn" data-bulk="share" type="button">${ico('share')}分享</button>
          </div>` : ''}
          <p class="conv-tip">${s.error
            ? '本次没有生成图片；失败请求不会留下结果，可直接点「重试」重新提交。'
            : running ? '任务已提交到 aivideomaker，正在轮询状态；右上「停止等待」只停本页轮询，不取消上游任务。'
            : s.source === 'real' ? (isVideo
                ? '成片由 aivideomaker 的 minimaxH3 真实生成，已转存到 assets/generated/。'
                : '图片由 bfl.1task.cn 的 flux-3-image 真实生成，已落盘到 assets/generated/。')
              : s.source === 'mock' ? '结果由本地 MOCK 上游生成：完整走完 HTTP 链路并落盘，但未访问计费网关、未产生任何费用。'
                : '本地占位视觉，用于界面复刻演示；未发起任何生成请求。'}${!s.error && !running && isVideo && s.source === 'demo' ? ' 视频为静态封面占位，不代表真实视频产物。' : ''}</p>
        </div>
      </div>`;
    bindConversation(s);
  }

  function bindConversation(s) {
    const photos = s.images;
    $$('.result-card', $('#conversation')).forEach((card) => {
      card.onclick = (e) => {
        if (e.target.closest('video')) return;   // 播放器自己处理点击与拖动
        const btn = e.target.closest('[data-act]');
        const i = Number(btn?.dataset.i ?? card.dataset.i);
        if (btn?.dataset.act === 'zoom') openLightbox(s, i);
        else if (btn?.dataset.act === 'download') downloadImg(photos[i], i);
        else if (btn?.dataset.act === 'same') makeSame(s);
        else if (photos[i].video) {
          if (!isVideoFile(photos[i].url)) toast('这是演示占位封面，不是真实视频产物');
        } else openLightbox(s, i);
      };
      card.onkeydown = (e) => { if (e.key === 'Enter') openLightbox(s, Number(card.dataset.i)); };
    });
    if ($('#convRetry')) $('#convRetry').onclick = () => retrySession(s);
    if ($('#convStopWait')) $('#convStopWait').onclick = () => {
      s.status = 'error';
      s.error = '已停止等待（上游任务仍在跑，成片不会回到本页）';
      renderConversation(s); renderSessions(); saveSessions();
      toast('已停止轮询');
    };
    $('#convAgain').onclick = () => { $('#prompt').value = s.prompt; state.prompt = s.prompt; newChat(); $('#prompt').focus(); updateSend(); toast('已回填提示词，可调整参数后再次生成'); };
    $('#convSame').onclick = () => makeSame(s);
    $$('[data-bulk]').forEach((b) => b.onclick = () => {
      const act = b.dataset.bulk;
      if (act === 'download') { photos.forEach((p, i) => setTimeout(() => downloadImg(p, i), i * 120)); toast(`开始下载 ${photos.length} 个结果`); }
      else if (act === 'share') { navigator.clipboard?.writeText(location.href).then(() => toast('链接已复制')).catch(() => toast('复制失败，请手动复制地址栏')); }
      else if (act === 'hd') toast('超清处理为界面占位功能');
      else toast('画布编辑为界面占位功能');
    });
  }
  function makeSame(s) {
    $('#prompt').value = s.prompt; state.prompt = s.prompt;
    if (s.mode === 'image' || s.mode === 'video' || s.mode === 'agent') { state.mode = s.mode; state.typeLabel = { image: '图片生成', video: '视频生成', agent: 'Agent 模式' }[s.mode]; }
    newChat(); syncModeTabs(); renderInspiration(); renderToolbar(); updateCost(); $('#prompt').focus(); updateSend();
    toast('已复用提示词与参数');
  }
  function downloadImg(img, i) {
    const a = document.createElement('a');
    const ext = isVideoFile(img.url) ? '.mp4' : img.url.endsWith('.svg') ? '.svg' : '.png';
    a.href = img.url; a.download = `jimeng-${img.source || 'demo'}-${i + 1}${ext}`;
    document.body.appendChild(a); a.click(); a.remove();
  }

  function openLightbox(s, i) {
    state.lightboxItems = s.images;
    state.lightboxIndex = i;
    renderLightbox();
    $('#lightbox').hidden = false;
  }
  function renderLightbox() {
    const img = state.lightboxItems[state.lightboxIndex];
    if (!img) return;
    $('#lightboxImg').src = img.url;
    const srcLabel = img.source === 'real' ? '真实生成 · flux-3-image'
      : img.source === 'mock' ? 'MOCK 上游 · 零计费' : '演示占位视觉';
    $('#lightboxMeta').textContent = `${state.lightboxIndex + 1} / ${state.lightboxItems.length} · ${srcLabel}${img.video ? ' · 视频封面' : ''}`;
  }
  function closeLightbox() { $('#lightbox').hidden = true; }
  function stepLightbox(d) {
    state.lightboxIndex = (state.lightboxIndex + d + state.lightboxItems.length) % state.lightboxItems.length;
    renderLightbox();
  }

  /* ---------- 渲染：资产库 ---------- */
  function renderDrawer() {
    const list = state.assets.filter((a) => state.assetFilter === 'all' || (state.assetFilter === 'fav' ? a.fav : a.type === state.assetFilter));
    $('#drawerSub').textContent = `共 ${state.assets.length} 个作品 · 当前显示 ${list.length} 个`;
    $('#drawerGrid').innerHTML = list.length ? list.map((a) => {
      const idx = state.assets.indexOf(a);
      return `<div class="asset-card" data-idx="${idx}" tabindex="0">
        ${isVideoFile(a.url)
          ? `<div class="asset-video">${ico('play')}<span>${esc(a.name)}</span></div>`
          : `<img src="${a.url}" alt="${esc(a.name)}" loading="lazy">`}
        <button class="asset-fav ${a.fav ? 'is-fav' : ''}" data-fav="${idx}" aria-label="收藏">${ico('heart')}</button>
        <div class="asset-label">${esc(a.name)}</div>
      </div>`;
    }).join('') : `<div class="empty-state" style="grid-column:1/-1">${ico('assets')}这里还没有作品</div>`;
    $$('[data-fav]').forEach((b) => b.onclick = (e) => {
      e.stopPropagation(); const a = state.assets[Number(b.dataset.fav)]; a.fav = !a.fav; renderDrawer();
    });
    $$('.asset-card').forEach((c) => c.onclick = () => {
      const a = state.assets[Number(c.dataset.idx)];
      // 成片有自己的播放器：新标签打开比塞进 lightbox 的 <img> 合理
      if (isVideoFile(a.url)) { window.open(a.url, '_blank', 'noopener'); return; }
      openLightbox({ images: [{ url: a.url, source: 'demo' }] }, 0);
    });
  }
  function toggleDrawer(open) {
    const drawer = $('#drawer');
    drawer.classList.toggle('is-open', open);
    drawer.setAttribute('aria-hidden', String(!open));
    $('#drawerScrim').hidden = !open;
    if (open) renderDrawer();
  }

  /* ---------- 生成流程 ---------- */
  const jobList = () => [...state.jobs.values()];

  /* 任务队列持久化：刷新不再丢任务。
     为什么是「存 taskId 再回查」而不是把结果存浏览器：服务端任务本来就在跑，
     浏览器只需要记住「我在等哪个 id」，重新打开时按 id 把结果捞回来即可。 */
  const JOB_KEY = 'jimeng-jobs-v1';
  const JOB_KEEP_MS = 30 * 60 * 1000;      // 超过半小时的在飞任务不再接续

  const runningVideos = () => state.sessions.filter((s) => s.status === 'running' && s.vidId);

  function saveJobs() {
    const now = Date.now();
    const pack = jobList()
      .filter((j) => j.status === 'running' && j.taskId && now - j.startedAt < JOB_KEEP_MS)
      .map((j) => ({
        id: j.id, taskId: j.taskId, prompt: j.prompt, startedAt: j.startedAt,
        text: j.text || '',
        // 参考图是 data URI，几 MB 一张，不能塞进 localStorage；接续只认服务端已有的任务
        req: j.req ? { ...j.req, refs: (j.req.refs || []).map((r) => ({ name: r.name, url: null })) } : null,
      }));
    try {
      if (!pack.length) localStorage.removeItem(JOB_KEY);
      else localStorage.setItem(JOB_KEY, JSON.stringify({ v: 1, at: now, jobs: pack }));
    } catch { /* 配额满就不落盘，不影响本次会话 */ }
  }

  function restoreJobs() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(JOB_KEY) || 'null'); } catch { raw = null; }
    if (!raw || !Array.isArray(raw.jobs) || !raw.jobs.length) return 0;
    const now = Date.now();
    const clean = raw.jobs.filter((j) => j && typeof j.id === 'string'
      && typeof j.prompt === 'string' && typeof j.taskId === 'string'
      && Number.isFinite(j.startedAt) && now - j.startedAt < JOB_KEEP_MS);
    if (!clean.length) { try { localStorage.removeItem(JOB_KEY); } catch { /* ignore */ } return 0; }
    clean.forEach((j) => {
      state.jobs.set(j.id, {
        id: j.id, taskId: j.taskId, prompt: j.prompt, req: j.req || null,
        startedAt: j.startedAt, status: 'running', session: null, pct: 0,
        text: j.text || '刷新后继续等待结果…',
      });
    });
    return clean.length;
  }

  async function resumeJobs() {
    const jobs = jobList();
    if (!jobs.length) return;
    state.queueView = true;
    if (jobs.length === 1) showGenerating(jobs[0].text);
    else renderJobStream();
    syncBusy();
    jobs.forEach((job) => pollJob(job));
    toast(`已接续 ${jobs.length} 个图片任务`);
  }

  async function pollJob(job) {
    let wait = 600;
    for (let i = 0; i < 600; i += 1) {
      if (job.status !== 'running') return;
      let r = null;
      let d = {};
      try {
        r = await fetch(`/api/tasks/${encodeURIComponent(job.taskId)}`, { cache: 'no-store' });
        d = await r.json().catch(() => ({}));
      } catch { r = null; }
      if (r && r.status === 404) {
        finishJob(job, { error: '任务已过期（代理重启过？结果不会回到本页）' }, true);
        return;
      }
      if (r && r.ok) {
        const t = d.task || {};
        if (t.status === 'succeeded' && t.result) {
          finishJob(job, applyTaskResult(t.result, false, t), true);
          return;
        }
        if (t.status === 'failed') { finishJob(job, { error: t.error || '生成失败' }, true); return; }
        if (t.status === 'canceled') { finishJob(job, { error: '已取消' }, true); return; }
      }
      await sleep(wait);
      wait = Math.min(1500, wait + 100);
    }
    finishJob(job, { error: '等待超时' }, true);
  }
  const runningJobs = () => jobList().filter((j) => j.status === 'running');
  function jobCap() {
    const n = Number(state.apiStatus?.concurrency);
    return Number.isFinite(n) && n > 0 ? Math.min(n, 6) : 4;
  }

  function updateSend() {
    const el = $('#sendBtn');
    // 链路状态未知时不能发：否则会把「还没检测完」误当成演示模式，静默返回占位图。
    // 只要有并发余量就允许继续提交，这是「一次只跑一个」变「任务队列」的关键。
    const running = runningJobs().length;
    el.disabled = state.videoBusy || runningVideos().length > 0
      || running >= jobCap() || !state.statusReady || !state.prompt.trim();
    el.classList.toggle('is-busy', running > 0 || state.videoBusy);
    $('#counter').textContent = `${state.prompt.length} / 2000`;
    $('#counter').style.color = state.prompt.length > 1900 ? 'var(--danger)' : '';
  }
  function syncComposerHint() {
    const t = $('#prompt');
    if (!t) return;
    const { running, cap } = queueStats();
    t.placeholder = running
      ? `还有 ${running} 个任务在跑（上限 ${cap}）· 输入下一个提示词即可继续提交`
      : placeholder();
  }

  function syncBusy() {
    const running = runningJobs().length;
    state.busy = running > 0 || state.videoBusy || runningVideos().length > 0;
    $('.app').classList.toggle('is-busy', state.busy);
    $('#progress').hidden = !state.busy;
    if (!state.busy) $('#progressBar').style.width = '100%';
    else if (parseFloat($('#progressBar').style.width || '0') >= 100) $('#progressBar').style.width = '8%';
    // 图片任务在跑时仍要能继续输入下一个提示词；只有视频会锁住输入
    $('#prompt').disabled = state.videoBusy;
    syncComposerHint();
    // 队列变化时同步底部提示；已有错误提示时不要覆盖它
    const hint = $('#hintText');
    if (hint && !hint.classList.contains('is-error')) setHint(defaultHint());
    updateSend();
  }
  function setBusy(on, text) {
    state.videoBusy = !!on;
    syncBusy();
    if (on) setHint(text); else setTimeout(() => setHint(defaultHint()), 900);
  }
  function showGenerating(text) {
    const real = isRealCall();
    const rv = isRealVideo();
    const mock = real && state.apiStatus?.upstreamMode === 'mock';
    const lane = rv ? 'minimaxH3' : !real ? '演示模式' : mock ? 'MOCK 链路' : '真实链路';
    const tiles = (real || rv) ? 1 : 4;               // 上游固定只回 1 个，不假装 4 个
    const expected = rv ? VIDEO_EXPECT_MS : !real ? 1500 : mock ? 2200 : 30000;  // 仅用于「预估进度」
    const started = Date.now();
    $('#welcome').hidden = true;
    $('#conversation').hidden = false;
    $('#conversation').innerHTML = `
      <div class="msg-ai"><div class="ai-avatar">${ico('sparkle')}</div><div class="ai-body">
        <div class="ai-status"><span class="spinner"></span><span id="genText">${esc(text)}</span>
          <span class="sep">·</span><span id="genUsed">已用 0s</span></div>
        <div class="gen-grid n${tiles}">${Array.from({ length: tiles }, () => '<div class="gen-tile"></div>').join('')}</div>
        <div class="gen-foot"><span class="who">${lane}</span>
          <span id="genPctText">0%</span><span class="sep">·</span>
          <span>${rv ? '正在提交任务' : real ? '已提交上游，等待返回' : '本地渲染中'}</span></div>
        <p class="gen-note">进度为估算，以实际返回为准</p>
      </div></div>`;
    const first = $('.gen-tile', $('#conversation'));
    if (first) first.insertAdjacentHTML('beforeend', '<span class="gen-pct" id="genPct">0%</span>');
    $('#stage').scrollTop = $('#stage').scrollHeight;

    clearInterval(window.__genTimer);
    window.__genTimer = setInterval(() => {
      if (QA_FREEZE) return;
      const el = Date.now() - started;
      // 渐近曲线：前段按预估速度走，超时后继续缓慢逼近但永不到顶，
      // 避免线性封顶后数字长时间静止（看起来像卡死）。
      const p = Math.round(99 * (1 - Math.exp(-1.8 * el / expected)));
      const a = $('#genPct'), b = $('#genPctText'), c = $('#genUsed');
      if (a) a.textContent = `${p}%`;
      if (b) b.textContent = `${p}%`;
      if (c) c.textContent = `已用 ${Math.floor(el / 1000)}s`;
      $('#progressBar').style.width = `${p}%`;
    }, 1000);
  }
  function stopProgress() {
    clearInterval(window.__genTimer);
    $('#progressBar').style.width = '100%';
  }

  /* ---------- 并发任务队列 ----------
     同一时间允许多个图片任务在飞：每个 job 有自己的提示词、开始时间与结果。
     单任务时沿用上面的 showGenerating 观感；多任务时切到队列视图，全部结束后
     保留队列，方便一次对比多张结果。 */

  function jobBlockHtml(job) {
    const s = job.session;
    const tags = (job.req ? tagList(job.req, job.req.real) : [])
      .map((t) => `<span class="bubble-tag">${esc(t)}</span>`).join('');
    const user = `<div class="msg-user"><div class="bubble-user">${esc(job.prompt)}<div class="bubble-tags">${tags}</div></div></div>`;
    if (!s) {
      const lane = job.req?.real ? '真实链路' : '演示模式';
      return `<div class="flow-item" data-job="${job.id}">${user}<div class="msg-ai"><div class="ai-avatar">${ico('sparkle')}</div>
        <div class="ai-body">
          <div class="ai-status"><span class="spinner"></span><span>${esc(job.text || '生成中')}</span>
            <span class="sep">·</span><span class="job-used">已用 0s</span></div>
          <div class="gen-grid n1"><div class="gen-tile"><span class="gen-pct">0%</span></div></div>
          <div class="gen-foot"><span class="who">${lane}</span><span class="job-pct">0%</span>
            <span class="sep">·</span><span>已提交上游，等待返回</span></div>
          <p class="gen-note">进度为估算；可以继续提交下一个任务，最多 ${jobCap()} 个同时进行</p>
        </div></div></div>`;
    }
    const cls = s.images.length === 1 ? 'cols-1' : s.images.length === 2 ? 'cols-2' : 'cols-4';
    return `<div class="flow-item" data-job="${job.id}">${user}<div class="msg-ai"><div class="ai-avatar">${ico('sparkle')}</div>
      <div class="ai-body">
        <div class="ai-status ${s.error ? 'is-error' : ''}">${s.error
          ? `${ico('warn')}<span>生成失败</span><span class="sep">·</span><span>${esc(s.error)}</span>`
          : `${ico('check', 'ok')}<span class="ok">已完成</span><span class="sep">·</span><span>${esc(s.model || 'flux-3-image')}</span><span class="sep">·</span><span>耗时 ${esc(s.elapsed || '—')}</span>`}</div>
        ${s.error ? `<div class="alert-bar">${ico('warn')}<span>${esc(s.error)}</span><button class="alert-retry" data-retry="${job.id}" type="button">${ico('refresh')}重试</button></div>` : ''}
        ${s.images.length ? `<div class="result-grid ${cls}">${s.images.map(resultCardHtml).join('')}</div>` : ''}
      </div></div></div>`;
  }

  function bindJobCards(job) {
    const s = job.session;
    const box = document.querySelector(`[data-job="${job.id}"]`);
    if (!box || !s) return;
    box.querySelectorAll('.result-card').forEach((card) => {
      card.onclick = (e) => {
        if (e.target.closest('video')) return;
        const btn = e.target.closest('[data-act]');
        const i = Number(btn?.dataset.i ?? card.dataset.i);
        if (btn?.dataset.act === 'download') downloadImg(s.images[i], i);
        else if (btn?.dataset.act === 'same') makeSame(s);
        else if (!s.images[i].video) openLightbox(s, i);
      };
      card.onkeydown = (e) => { if (e.key === 'Enter' && !s.images[Number(card.dataset.i)].video) openLightbox(s, Number(card.dataset.i)); };
    });
    const retry = box.querySelector('[data-retry]');
    if (retry) retry.onclick = () => {
      // 只回填不自动提交：重试也是一次计费请求，必须让用户自己按发送
      state.prompt = job.prompt;
      $('#prompt').value = job.prompt;
      if (Array.isArray(job.req?.refs)) { state.refs = job.req.refs.map((r) => ({ ...r })); renderRefs(); }
      updateSend();
      $('#prompt').focus();
      toast('已回填提示词，确认参数后按发送');
    };
  }

  function flowTip() {
    const last = [...jobList()].reverse().find((j) => j.session);
    if (!last) return '任务已提交，结果会自动追加到这一段里。';
    const src = last.session.source;
    if (src === 'real') return '图片由 bfl.1task.cn 的 flux-3-image 真实生成，已落盘到 assets/generated/。';
    if (src === 'mock') return '结果由本地 MOCK 上游生成（零计费）：完整走完 HTTP 链路并落盘，但未访问计费网关、未产生任何费用。';
    return '本地占位视觉，用于界面复刻演示；未发起任何生成请求。';
  }

  function renderJobStream({ scroll = true } = {}) {
    state.queueView = true;
    const all = jobList();
    $('#welcome').hidden = true;
    $('#conversation').hidden = false;
    // 即梦式串联：一次任务一段，从上往下追加，不覆盖前面的结果
    $('#conversation').innerHTML = `<div class="flow">${all.map(jobBlockHtml).join('')}</div>
      <p class="conv-tip">${esc(flowTip())}</p>`;
    if (scroll) $('#stage').scrollTop = $('#stage').scrollHeight;
    all.forEach(bindJobCards);
    startJobTicker();
  }

  function startJobTicker() {
    clearInterval(window.__jobTimer);
    window.__jobTimer = setInterval(() => {
      if (QA_FREEZE) return;
      const running = runningJobs();
      if (!running.length) return;
      let oldest = null;
      running.forEach((job) => {
        const el = Date.now() - job.startedAt;
        const expected = job.req?.realVideo ? VIDEO_EXPECT_MS : 30000;
        const p = Math.round(99 * (1 - Math.exp(-1.8 * el / expected)));
        job.pct = p;
        const box = document.querySelector(`[data-job="${job.id}"]`);
        if (box) {
          const a = box.querySelector('.gen-pct'); if (a) a.textContent = `${p}%`;
          const b = box.querySelector('.job-pct'); if (b) b.textContent = `${p}%`;
          const c = box.querySelector('.job-used'); if (c) c.textContent = `已用 ${Math.floor(el / 1000)}s`;
        }
        if (!oldest || job.startedAt < oldest.startedAt) oldest = job;
      });
      if (oldest) $('#progressBar').style.width = `${oldest.pct || 0}%`;
    }, 1000);
  }
  function stopJobTicker() { clearInterval(window.__jobTimer); }

  /* 任务结束：还有在跑就重绘队列；全跑完时——单任务回到会话视图，
     多任务则留在队列里，方便一次看齐所有结果。 */
  function settleJobs() {
    saveJobs();
    const running = jobList().filter((j) => j.status === 'running');
    if (!running.length) stopJobTicker();
    // 用户切去看历史会话时不要抢视图；否则始终保持串联流（新任务接在旧结果下面）
    if (state.queueView) renderJobStream();
    syncBusy(); renderSessions(); updateCost();
    if (window.__qaSync) window.__qaSync();
  }

  /** 任务收尾：成功失败都落成一条会话；settleJobs 负责清队列与落盘。 */
  function finishJob(job, out, real) {
    const { prompt, req } = job;
    const images = (out && out.images) || [];
    const s = makeSession({
      prompt, images,
      source: real ? ((out && out.upstreamMode) === 'mock' ? 'mock' : 'real') : 'demo',
      elapsed: out && out.elapsed, error: out && out.error, req, refsSent: (out && out.refsUsed) || 0,
    });
    job.session = s;
    job.status = 'done';
    // 成功不再弹 toast：结果已经在串联流里，多任务同时完成时一串提示会糊住输入框
    if (out && out.error) toast(`「${prompt.slice(0, 10)}」生成失败：${out.error}`, 'error');
    settleJobs();
  }

  async function runJob(job, real) {
    const { prompt, req } = job;
    let out;
    try {
      if (real) {
        // 走异步 /api/tasks 而不是同步 /api/generate：只有异步任务有 id，刷新后才能接续
        out = await taskGenerate(prompt, job.idem, (status, tid) => {
          if (tid && job.taskId !== tid) { job.taskId = tid; saveJobs(); }
        }, req.realModel, req.refs);
      } else {
        await sleep(1100 + Math.random() * 500);
        out = { images: fallbackImages(), elapsed: '1.4s（本地）' };
      }
    } catch (err) {
      out = { error: `请求失败：${err?.message || String(err)}` };
    }
    finishJob(job, out, real);
  }

  function fallbackImages() {
    const n = isRealCall() ? 1 : state.mode === 'image' ? state.count : state.mode === 'video' ? state.count : 1;
    const pool = [1, 2, 3, 7, 8, 9, 10, 11, 12, 4, 5, 6];
    return Array.from({ length: n }, (_, i) => ({
      url: ART(pool[(i + state.sessions.length * 3) % pool.length]),
      source: 'demo',
      video: state.mode === 'video',
      duration: state.duration,
    }));
  }
  function snapshotReq() {
    // 提交那一刻的参数快照：请求返回前用户仍可切模式/改参数，会话记录不能跟着变
    return {
      mode: state.mode, typeLabel: state.typeLabel, model: state.model,
      ratio: state.ratio, resolution: state.resolution, detail: state.detail,
      count: state.count,
      duration: isRealVideo() ? videoTierInfo().duration : state.duration,
      videoRes: isRealVideo() ? videoTierInfo().resolution.toUpperCase() : state.videoRes,
      real: isRealCall(), realVideo: isRealVideo(), videoTier: state.videoTier,
      realModel: isRealCall() ? currentImageModel() : null,
      refs: state.refs.map((r) => ({ ...r })),
    };
  }

  function makeSession({ prompt, images, source, elapsed, error, req: reqIn, refsSent = 0 }) {
    const req = reqIn || snapshotReq();
    const s = {
      id: `s${Date.now()}`, title: prompt.slice(0, 16) + (prompt.length > 16 ? '…' : ''),
      mode: req.mode, prompt, params: tagList(req, req.real), images, source, elapsed, error, req, refsSent,
      model: req.mode === 'video' && req.realVideo ? 'minimaxH3'
        : (source === 'real' || source === 'mock') ? (req.realModel || 'flux-3-image') : req.model,
      at: Date.now(),
      status: 'done',
    };
    state.sessions.unshift(s);
    images.forEach((im, i) => { if (im.source === 'real' || im.source === 'mock') state.assets.unshift({ url: im.url, name: `${source === 'mock' ? 'MOCK' : im.video ? 'minimaxH3' : 'flux-3-image'} 产物 ${i + 1}`, type: im.video ? 'video' : 'image', fav: false }); });
    saveSessions();
    return s;
  }

  /* ---------- 会话持久化：刷新/重开页面不丢历史 ---------- */
  const STORE_KEY = 'jimeng-sessions-v1';
  const STORE_MAX = 12;

  function saveSessions() {
    // 参考图是 data URI，几张就能顶满 5MB：先带图存，配额不够再丢图保记录
    const pack = (keepRefImages) => state.sessions.slice(0, STORE_MAX).map((s) => ({
      id: s.id, title: s.title, mode: s.mode, prompt: s.prompt, params: s.params,
      images: (s.images || []).map((im) => ({ url: im.url, source: im.source, video: !!im.video, duration: im.duration })),
      source: s.source, elapsed: s.elapsed, error: s.error, model: s.model, at: s.at,
      status: s.status || 'done', vidId: s.vidId || null, vidStartedAt: s.vidStartedAt || null,
      vidMeta: s.vidMeta || null,
      refsSent: s.refsSent || 0,
      req: s.req ? { ...s.req, refs: (s.req.refs || []).map((r) => ({ name: r.name, url: keepRefImages ? r.url : null })) } : s.req,
    }));
    const write = (keep) => localStorage.setItem(STORE_KEY,
      JSON.stringify({ v: 1, at: Date.now(), refsDropped: !keep, sessions: pack(keep) }));
    try { write(true); }
    catch { try { write(false); } catch { /* 隐私模式：放弃持久化，不影响本次会话 */ } }
  }

  function restoreSessions() {
    let raw;
    try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch { raw = null; }
    if (!raw || !Array.isArray(raw.sessions) || !raw.sessions.length) return 0;
    const okMode = { image: 1, video: 1, agent: 1 };
    const clean = raw.sessions.filter((s) => s && typeof s.id === 'string'
      && typeof s.prompt === 'string' && okMode[s.mode]
      && Array.isArray(s.images) && s.images.every((im) => im && typeof im.url === 'string'))
      .slice(0, STORE_MAX);
    if (!clean.length) return 0;
    // running 的会话刷新后没有任何东西在轮询它：如实标成中断，不能让它一直转圈
    clean.forEach((s) => {
      // 有 vidId 就能按 id 接续轮询（服务端任务还在跑）；没有 id 的才是真中断
      if (s.status === 'running' && s.vidId) {
        // 僵尸任务保护：超过 30 分钟还没结果，别让它永远占着「视频在跑」挡住新提交
        if (Number.isFinite(s.vidStartedAt) && Date.now() - s.vidStartedAt > 30 * 60 * 1000) {
          s.status = 'error';
          s.error = '视频任务超过 30 分钟仍未出片，已标记超时；可重新生成';
        }
        return;
      }
      if (s.status === 'running') {
        s.status = 'error';
        s.error = '页面刷新时没拿到任务 id，轮询已中断；可重新生成';
      }
    });
    state.sessions = clean;   // 覆盖示例会话，避免每次刷新都重复
    return clean.length;
  }

  /** 32 位短哈希：幂等键要稳定、可读，别把整段提示词塞进 header。 */
  function hash32(str) {
    let h = 5381;
    for (let i = 0; i < str.length; i += 1) h = ((h * 33) ^ str.charCodeAt(i)) >>> 0;
    return h.toString(36);
  }

  /** 这一镜这一轮的唯一键：内容变了或主动重出，键就变；重复点击则命中同一个任务。 */
  function shotIdem(sh) {
    const raw = [drama.ep, sh.id, sh.size, sh.desc, sh.gen || 0,
                 currentImageModel() || 'default'].join('|');
    return `board:${drama.ep}:${sh.id}:${hash32(raw)}`;
  }

  /** 提交任务并轮询到终态。返回与 realGenerate 同形的结果（images/ms/credits/call）。 */
  async function taskGenerate(prompt, idem, onTick, model = null, refs = []) {
    const headers = { 'Content-Type': 'application/json' };
    if (idem) headers['Idempotency-Key'] = idem;
    const body = { prompt, idem };
    if (model) body.model = model;
    const imgs = (refs || []).filter((r) => typeof r?.url === 'string' && r.url.startsWith('data:image/'));
    if (imgs.length) body.refs = imgs.map((r) => ({ name: r.name, url: r.url }));
    const res = await fetch('/api/tasks', {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
    if (!res.ok || !data.ok) {
      if (data.code === 'insufficient_credits') loadBilling(true);
      return { error: data.error || `HTTP ${res.status}`, deduped: !!data.deduped,
               attempts: 0, code: data.code, need: data.need, available: data.available };
    }
    if (data.replay && data.result) return applyTaskResult(data.result, true);
    const tid = data.id;
    if (!tid) return { error: '代理没有返回任务 id' };
    if (onTick) onTick('queued', tid, data.task || {});
    let wait = 350;
    for (let i = 0; i < 900; i += 1) {                 // 上限约 10 分钟
      await new Promise((r) => setTimeout(r, wait));
      const r2 = await fetch(`/api/tasks/${encodeURIComponent(tid)}`, { cache: 'no-store' });
      const d2 = await r2.json().catch(() => ({}));
      if (r2.status === 404) return { error: '任务已过期（代理重启过？）', taskId: tid };
      const t = d2.task || {};
      if (t.status === 'succeeded' && t.result) return applyTaskResult(t.result, false, t);
      if (t.status === 'failed') {
        return { error: t.error || '生成失败', taskId: tid, attempts: t.attempts,
                 errorKind: t.error_kind, httpStatus: t.upstream_http };
      }
      if (t.status === 'canceled') return { error: '已取消', canceled: true, taskId: tid };
      if (onTick) onTick(t.status, tid, t);
      if (i > 8) wait = Math.min(1200, wait + 80);
    }
    return { error: '等待超时（10 分钟）', taskId: tid };
  }

  function applyTaskResult(result, replay, task) {
    // 服务端在任务成功前已经结算；这里强刷权威余额（绕过上游 5s 缓存），读不到才本地兜底
    loadBilling(true).then((b) => {
      if (!b && !replay) {
        state.credits = Math.max(0, state.credits - chargedCredits(result));
        updateCost();
      }
    });
    if (state.apiStatus && Number.isFinite(result.call)) state.apiStatus.calls = result.call;
    // 任务结果里可能没带 upstreamMode（异步任务视图），用当前链路兜底，
    // 否则 MOCK 任务会被标成 REAL，说明文案也会跟着错
    const src = ((result.upstreamMode || state.apiStatus?.upstreamMode) === 'mock') ? 'mock' : 'real';
    const imgs = result.images || [];
    return {
      images: imgs.map((x) => ({ url: x.url, source: src })),
      elapsed: `${((result.elapsed_ms || 0) / 1000).toFixed(1)}s`,
      upstreamMode: result.upstreamMode, refsUsed: result.refsUsed,
      credits: chargedCredits(result), ms: result.elapsed_ms || 0, call: result.call,
      model: result.model || task?.model || null,
      attempts: task?.attempts ?? result.attempt ?? 1, replay: !!replay,
    };
  }

  async function realGenerate(prompt, refs = [], modelOverride = null) {
    const body = { prompt, mode: 'image' };
    const model = modelOverride || currentImageModel();
    if (model) body.model = model;
    // 只把 data URI 发出去；示例/历史里没有 url 的参考无法复原，宁可少发也不发错的
    const imgs = refs.filter((r) => typeof r?.url === 'string' && r.url.startsWith('data:image/'));
    if (imgs.length) body.refs = imgs.map((r) => ({ name: r.name, url: r.url }));
    const res = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
    if (!res.ok || !data.ok) {
      if (data.code === 'insufficient_credits') loadBilling(true);
      return { error: data.error || `HTTP ${res.status}`, detail: data.detail,
               code: data.code, need: data.need, available: data.available };
    }
    loadBilling(true).then((b) => {
      if (!b) { state.credits = Math.max(0, state.credits - chargedCredits(data)); updateCost(); }
    });
    if (state.apiStatus) state.apiStatus.calls = data.call ?? ((state.apiStatus.calls || 0) + 1);
    const src = data.upstreamMode === 'mock' ? 'mock' : 'real';
    const refsUsed = Number.isFinite(data.refsUsed) ? data.refsUsed : imgs.length;
    return { images: data.images.map((x) => ({ url: x.url, source: src })),
             elapsed: `${(data.elapsed_ms / 1000).toFixed(1)}s`,
             upstreamMode: data.upstreamMode, refsUsed,
             model: data.model || null,
             credits: chargedCredits(data), ms: data.elapsed_ms, call: data.call };
  }

  /* ---------- 视频模式：提交异步任务 → 轮询 → 成片入库 ---------- */
  function videoFrame(req) {
    // 只有本页压好的 data URI 能当首帧；历史记录里丢图的参考不能复原，宁可不发
    const r = (req.refs || []).find((x) => typeof x?.url === 'string' && x.url.startsWith('data:image/'));
    return r ? r.url : null;
  }

  async function generateVideoSession(prompt, req) {
    const v = state.apiStatus?.video || {};
    const tier = req.videoTier === 'paid' && v.allowPaid ? 'paid' : 'free';
    const ratio = videoFrame(req) ? 'adaptive' : (req.ratio || '9:16');
    const label = `${v.model || 'minimaxH3'} · ${v.tiers?.[tier]?.resolution || '720p'} / ${v.tiers?.[tier]?.duration || 8}s`;
    setBusy(true, `正在提交视频任务 · ${label}…`);
    showGenerating(`提交视频任务 · ${v.base || 'aivem'} / ${label}`);

    let s = null;
    try {
      const res = await fetch('/api/video', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, image: videoFrame(req), tier, ratio }),
      });
      const data = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);

      s = makeSession({ prompt, images: [], source: 'real', elapsed: '', error: null, req });
      s.status = 'running';
      s.vidId = data.id;
      s.vidStartedAt = Date.now();
      s.vidMeta = { resolution: data.resolution, duration: data.duration, tier, ratio: data.ratio || ratio };
      state.activeSession = s.id;
      saveSessions();            // 先落盘再渲染：否则刷新时存的是「已完成但没有 vidId」的旧快照
      stopProgress();
      renderConversation(s); renderSessions(); updateCost();
      startVideoTicker(s);
      toast(`任务已提交：${data.id} · 上游排队中`);
      pollVideoSession(s);           // 不 await：让用户能继续操作，卡片自己刷新
    } catch (err) {
      stopProgress();
      const msg = err?.message || String(err);
      if (s) { s.status = 'error'; s.error = `提交失败：${msg}`; renderConversation(s); renderSessions(); saveSessions(); }
      else {
        const bad = makeSession({ prompt, images: [], source: 'real', error: `提交失败：${msg}`, req });
        bad.status = 'error';
        state.activeSession = bad.id; renderConversation(bad); renderSessions();
      }
      toast(`视频提交失败：${msg}`, 'error');
    } finally {
      setBusy(false);
      updateSend();
      if (window.__qaSync) window.__qaSync();
    }
  }

  function fmtSecs(ms) { return `${Math.max(0, Math.round(ms / 1000))}s`; }

  /* 视频进度：按 200s 估算。H3 免费档实测 1–3 分钟，20s 的曲线会让进度条早早卡在 99%。
     渐近曲线（1 - e^-1.8t/T）前段接近线性、后段收敛，超时也不会顶到 100%。 */
  const VIDEO_EXPECT_MS = 200000;
  function videoPct(startedAt) {
    const el = Math.max(0, Date.now() - (startedAt || Date.now()));
    return Math.min(99, Math.round(99 * (1 - Math.exp(-1.8 * el / VIDEO_EXPECT_MS))));
  }
  function startVideoTicker(s) {
    clearInterval(window.__vidTimer);
    const tick = () => {
      if (QA_FREEZE || !s || s.status !== 'running' || state.activeSession !== s.id) return;
      if (s.vidStartedAt && !s.vidPctBase) s.vidPctBase = s.vidStartedAt;
      const p = videoPct(s.vidStartedAt);
      const bar = document.getElementById('convVidBar');
      const pct = document.getElementById('convVidPct');
      const clock = document.getElementById('convVidClock');
      if (bar) bar.style.width = `${p}%`;
      if (pct) pct.textContent = `${p}%`;
      if (clock) clock.textContent = fmtSecs(Date.now() - (s.vidStartedAt || Date.now()));
      const top = $('#progressBar');
      if (top) top.style.width = `${p}%`;
    };
    tick();
    window.__vidTimer = setInterval(tick, 1000);
  }
  function stopVideoTicker() { clearInterval(window.__vidTimer); }

  async function pollVideoSession(s) {
    const id = s.vidId;
    const t0 = s.vidStartedAt || Date.now();
    let failures = 0;
    for (let i = 0; i < 120; i += 1) {          // 4s × 120 ≈ 8 分钟上限
      await sleep(4000);
      if (!s || s.status !== 'running' || s.vidId !== id) return;
      let d = null;
      try {
        const res = await fetch(`/api/video/${encodeURIComponent(id)}`, { cache: 'no-store' });
        d = await res.json();
      } catch { d = null; }
      if (!d) {
        failures += 1;
        if (failures >= 5) {
          s.status = 'error'; s.error = '轮询失败：代理不可达（上游任务可能仍在跑）';
          paintRunningVideo(s); return;
        }
        continue;
      }
      failures = 0;
      s.vidElapsed = fmtSecs(Date.now() - t0);
      if (typeof d.status === 'string') s.vidPhase = d.status;
      if (d.status === 'succeeded' && !d.url) {
        s.status = 'error';
        s.error = (typeof d.warn === 'string' ? d.warn : '成片已生成，但本地转存失败');
        paintRunningVideo(s); return;
      }
      if (d.status === 'succeeded' && d.url) {
        stopVideoTicker();
        s.status = 'done';
        s.images = [{ url: d.url, source: 'real', video: true, duration: d.duration || s.vidMeta?.duration || 8 }];
        s.elapsed = fmtSecs(Date.now() - t0);
        s.vidError = null;
        state.assets.unshift({ url: d.url, name: `minimaxH3 成片 ${state.assets.length + 1}`, type: 'video', fav: false });
        saveSessions();
        if (state.activeSession === s.id) renderConversation(s);
        renderSessions(); renderDrawer();
        toast(`视频已出片 · ${s.elapsed}`, 'success');
        return;
      }
      if (d.status === 'failed') {
        stopVideoTicker();
        s.status = 'error';
        s.error = (typeof d.error === 'string' ? d.error
          : (d.error && (d.error.message || d.error.code)) || d.warn || '上游失败');
        paintRunningVideo(s); return;
      }
      paintRunningVideo(s);          // queued / running：只更新计时，不重绘整页
    }
    stopVideoTicker();
    s.status = 'error'; s.error = '等待超时（8 分钟）'; paintRunningVideo(s);
  }

  function resumeRunningVideos() {
    const list = state.sessions.filter((s) => s.status === 'running' && s.vidId);
    if (!list.length) return;
    const first = list[0];
    if (state.activeSession === 'new') {
      state.activeSession = first.id;
      $('#crumb').textContent = `创作 / ${first.title}`;
      renderConversation(first);
      startVideoTicker(first);
    }
    list.forEach((s) => pollVideoSession(s));
    renderSessions(); syncBusy();
    toast(`已接续 ${list.length} 个视频任务的轮询`);
  }

  function paintRunningVideo(s) {
    // 轮询每 4s 一次：还在跑就只改计时文本，别整页重绘打断用户操作
    if (state.activeSession === s.id) {
      const clock = document.getElementById('convVidClock');
      if (clock && s.status === 'running') {
        clock.textContent = s.vidElapsed || '—';
        const phase = document.getElementById('convVidPhase');
        if (phase) phase.textContent = s.vidPhase === 'queued' ? '上游排队中' : '上游生成中';
        return;
      }
      renderConversation(s);
    } else renderSessions();
    saveSessions();
    if (window.__qaSync) window.__qaSync();
  }

  async function retrySession(s) {
    if (!s || state.busy) return;
    const r = s.req || {};
    const lost = (r.refs || []).filter((x) => !x.url).length;
    if (lost) {
      toast(`这条记录的 ${lost} 张参考图未保存（体积过大），请重新上传后再生成`, 'error');
      return;
    }
    ['mode', 'typeLabel', 'model', 'ratio', 'resolution', 'detail', 'count', 'duration', 'videoRes', 'videoTier']
      .forEach((k) => { if (r[k] !== undefined) state[k] = r[k]; });
    if (Array.isArray(r.refs)) state.refs = r.refs.map((x) => ({ ...x }));
    state.prompt = s.prompt;
    $('#prompt').value = s.prompt;
    syncModeTabs(); renderInspiration(); renderToolbar(); renderRefs();
    updateCost(); updateSend(); setHint(defaultHint());
    toast('正在重试上一次请求');
    await handleGenerate();
  }

  async function handleGenerate() {
    if (!state.statusReady) {
      setHint('正在检测生成链路，请稍候…', true);
      return;
    }
    const prompt = state.prompt.trim();
    if (!prompt) {
      $('#composer').classList.add('is-invalid');
      setTimeout(() => $('#composer').classList.remove('is-invalid'), 500);
      setHint('请输入提示词后再生成', true);
      return;
    }
    closePopover();
    const real = isRealCall();
    // 关键：在发请求之前固定参数，之后用户怎么改都不影响这条任务记录
    const req = snapshotReq();

    // 视频仍是独占任务：轮询状态跟单个会话绑定，混在图片队列里会互相打架
    if (req.realVideo) {
      if (runningJobs().length) {
        setHint('图片任务还在跑，视频请等它们完成后再提交', true);
        toast('图片任务进行中，暂不能提交视频', 'error');
        return;
      }
      if (state.videoBusy || runningVideos().length) {
        setHint('已有一个视频任务在跑，等它出片后再提交', true);
        toast('视频任务进行中，暂不能重复提交', 'error');
        return;
      }
      await generateVideoSession(prompt, req);
      return;
    }

    // 从历史会话发起 = 开一段新的串联流；在当前流里提交则接在末尾
    if (!state.queueView) state.jobs.clear();

    const cap = jobCap();
    if (runningJobs().length >= cap) {
      setHint(`并发已满：最多同时 ${cap} 个任务，等一个完成再提交`, true);
      toast(`并发已满（${cap} 个）`, 'error');
      return;
    }
    // 防抖：同一提示词 1.5s 内连点只算一次（按钮 disabled 之外的第二道保险）
    const now = Date.now();
    if (runningJobs().some((j) => j.prompt === prompt && now - j.startedAt < 1500)) return;

    if (!real && !isRealVideo() && estimateCredits() > state.credits) {
      setHint(`积分不足：本次需要 ${estimateCredits()}，余额 ${state.credits}。请调低生成数量或分辨率`, true);
      toast('积分不足，已阻止本次生成', 'error');
      return;
    }

    const mock = real && state.apiStatus?.upstreamMode === 'mock';
    const job = {
      id: `j${now}-${++state.jobSeq}`,
      idem: `job:${now}:${hash32(prompt)}`,
      taskId: null,
      prompt, req, startedAt: now, status: 'running', session: null, pct: 0,
      text: real
        ? (mock ? '正在走 MOCK 链路（零计费）…' : '正在调用 flux-3-image 真实生成，约 20–40 秒…')
        : '正在准备演示结果…',
    };
    state.jobs.set(job.id, job);
    saveJobs();

    // 提交即清空输入框：这样才能立刻写下一个提示词并继续提交
    state.prompt = '';
    $('#prompt').value = '';
    state.refs = [];
    renderRefs();
    syncBusy();

    renderJobStream();
    if (window.__qaSync) window.__qaSync();

    runJob(job, real);
  }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    $$('.mode-tab').forEach((b) => b.onclick = () => {
      state.mode = b.dataset.mode;
      state.typeLabel = { image: '图片生成', video: '视频生成', agent: 'Agent 模式' }[state.mode];
      state.model = MODELS[state.mode][0];
      state.count = state.mode === 'image' ? 4 : 1;
      state.duration = 5;
      if (state.mode === 'video' && isRealVideo()) { state.videoTier = 'free'; state.duration = videoTierInfo().duration; }
      state.inspireOffset = 0;
      $('#prompt').placeholder = placeholder();
      syncModeTabs(); renderInspiration(); renderToolbar(); updateCost(); setHint(defaultHint()); closePopover();
    });
    $('#shuffleBtn').onclick = () => { state.inspireOffset += 3; renderInspiration(); };
    $('#inspiration').onclick = (e) => {
      const card = e.target.closest('.insp-card');
      if (!card) return;
      $('#prompt').value = card.dataset.prompt;
      state.prompt = card.dataset.prompt;
      updateSend(); $('#prompt').focus();
      toast('已填入示例提示词');
    };
    $('#prompt').addEventListener('input', (e) => { state.prompt = e.target.value; updateSend(); if (!state.busy) setHint(defaultHint()); });
    $('#prompt').addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      // 中文输入法选词/联想确认也会发 Enter（且 isComposing 可能已变 false），
      // 这两种信号都要挡掉，否则选词就误触发一次生成。
      if (e.isComposing || e.keyCode === 229) return;
      const accel = e.metaKey || e.ctrlKey;
      if (!accel && e.shiftKey) return;                  // Shift + Enter → 换行
      if (!accel && !$('#prompt').value.trim()) return;  // 空内容回车就是换行
      e.preventDefault();
      handleGenerate();
    });
    $('#sendBtn').onclick = handleGenerate;

    $('#cfgClose').onclick = closeConfig;
    $$('[data-cfg-close]').forEach((el) => el.onclick = closeConfig);
    $('#cfgSave').onclick = saveConfig;
    $('#cfgTest').onclick = testConfig;
    $('#cfgClear').onclick = clearConfig;
    $('#cfgClearKey').onclick = clearPageKey;
    $('#cfgModelsRefresh').onclick = loadModels;
    $('#navModelConfig').onclick = () => {
      openConfig();
      $('#app').classList.remove('mobile-open');
    };
    $('#cfgReveal').onclick = () => {
      const i = $('#cfgKey');
      const show = i.type === 'password';
      i.type = show ? 'text' : 'password';
      $('#cfgReveal').textContent = show ? '隐藏' : '显示';
    };
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !$('#configModal').hidden) { e.preventDefault(); closeConfig(); }
    });
    // 落盘文件被清理/迁移后图片会 404：标灰并提示，而不是留一块空白
    document.addEventListener('error', (e) => {
      const im = e.target;
      if (!im || im.tagName !== 'IMG') return;
      im.style.visibility = 'hidden';
      const box = im.closest('.result-card, .asset-card, .insp-card, .session-item');
      if (box) box.classList.add('is-broken');
    }, true);
    $('#clearBtn').onclick = () => { $('#prompt').value = ''; state.prompt = ''; state.refs = []; renderRefs(); updateSend(); setHint(defaultHint()); };
    $('#refAddBtn').onclick = (e) => refPopover(e.currentTarget);
    $('#fileInput').onchange = async (e) => {
      const files = Array.from(e.target.files || []);
      e.target.value = '';            // 先清空，允许连续选同一个文件
      if (!files.length) return;
      await addRefs(files.map((f) => () => fileToRef(f)));
    };

    $('#collapseBtn').onclick = () => { $('#app').classList.add('is-collapsed'); renderModeGlider(); };
    $('#expandBtn').onclick = () => { $('#app').classList.remove('is-collapsed'); renderModeGlider(); };
    const mobileBtn = document.createElement('button');
    mobileBtn.className = 'icon-btn subtle mobile-menu';
    mobileBtn.setAttribute('aria-label', '打开导航');
    mobileBtn.innerHTML = ico('menu');
    mobileBtn.onclick = () => $('#app').classList.toggle('mobile-open');
    $('.topbar-left').prepend(mobileBtn);

    $('#themeBtn').onclick = () => {
      state.theme = state.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = state.theme;
      localStorage.setItem('jimeng-theme', state.theme);
      $('#themeIcon').innerHTML = `<use href="#i-${state.theme === 'dark' ? 'sun' : 'moon'}"></use>`;
      renderModeGlider();
    };
    $('#newChatBtn').onclick = newChat;
    $('#brandHome').onclick = (e) => { e.preventDefault(); newChat(); };
    $('#libraryBtn').onclick = () => toggleDrawer(true);
    $('#drawerClose').onclick = () => toggleDrawer(false);
    $('#drawerScrim').onclick = () => toggleDrawer(false);
    $$('.filter').forEach((b) => b.onclick = () => {
      state.assetFilter = b.dataset.filter;
      $$('.filter').forEach((x) => x.classList.toggle('is-active', x === b));
      renderDrawer();
    });
    $('#lightboxClose').onclick = closeLightbox;
    $('#lightboxPrev').onclick = () => stepLightbox(-1);
    $('#lightboxNext').onclick = () => stepLightbox(1);
    $('#lightbox').onclick = (e) => { if (e.target.id === 'lightbox') closeLightbox(); };

    const noop = (label) => toast(`${label} 为界面占位功能`);
    $('#creditBtn').onclick = async () => {
      if (!state.billing) await loadBilling();
      billingPopover($('#creditBtn'));
    };
    $('#vipBtn').onclick = () => noop('会员中心');
    $('#notifyBtn').onclick = () => noop('通知');
    $('#topAvatarBtn').onclick = () => noop('账户菜单');
    $('#userBtn').onclick = () => noop('账户菜单');
    $('#memberBtn').onclick = () => noop('会员权益');
    $('#messageBtn').onclick = () => noop('消息中心');
    $('#helpBtn').onclick = () => toast('本页为界面复刻：图片可接真实 flux-3-image，视频为演示');
    $$('.nav-item').forEach((b) => b.onclick = () => {
      $$('.nav-item').forEach((x) => x.classList.remove('is-active'));
      b.classList.add('is-active');
      if (b.dataset.nav === '资产') { setView('generate'); toggleDrawer(true); }
      else if (b.dataset.nav === '创作') { setView('generate'); newChat(); }
      else if (b.dataset.nav === '短剧工作室') setView('drama');
      else { setView('generate'); noop(b.dataset.nav); }
      $('#app').classList.remove('mobile-open');
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closePopover(); closeLightbox(); toggleDrawer(false); }
      if (!$('#lightbox').hidden) {
        if (e.key === 'ArrowLeft') stepLightbox(-1);
        if (e.key === 'ArrowRight') stepLightbox(1);
      }
    });
    window.addEventListener('resize', () => { renderModeGlider(); closePopover(); });
  }

  /* ---------- 短剧工作室 ---------- */
  const DRAMA_IDEAS = [
    ['剧本医生', '帮我诊断这个剧本的问题：主角动机不够强、第三集节奏拖，给出 3 条可执行的修改建议。'],
    ['玄幻仙侠短剧创意', '写一部玄幻仙侠短剧：废柴弟子意外继承上古剑灵，宗门大比上一剑破境。'],
    ['都市恋爱短剧创意', '写一部都市恋爱短剧：合租室友是隐藏身份的集团继承人，从互相嫌弃到并肩。'],
    ['东方神话志怪短剧创意', '写一部东方志怪短剧：赶考书生夜宿荒庙，遇到只替人还愿的狐妖。'],
    ['末世规则短剧创意', '写一部末世规则短剧：全球停电第七天，超市里出现了第一条不能违反的规则。'],
    ['年代家庭短剧创意', '写一部年代家庭短剧：九十年代小镇，母亲为了供女儿读书卖掉了缝纫机。'],
    ['古装爱情短剧创意', '写一部古装爱情短剧：和亲路上，公主与护卫在追杀中互换身份。'],
    ['小说拆解', '把这个故事拆成 10 集短剧：每集一个钩子，结尾留反转。'],
    ['创意生成剧本', '按这个创意写完整剧本：人物小传、三幕结构、10 集分集大纲。'],
    ['解说短剧编剧', '写一版解说向短剧脚本：3 分钟讲完，前 5 秒必须抓人。'],
    ['拉片反解剧本', '反解这段成片的剧作结构：节拍点、情绪曲线、钩子位置。'],
    ['都市男频短剧创意', '写一部都市男频短剧：被开除的外卖员，靠一份旧合同翻了盘。'],
  ];
  const DRAMA_FEATS = [
    ['剧本创作', '从灵感到好故事', 'sparkle', 'linear-gradient(135deg,#1c3f7a,#4f7fd0)'],
    ['短剧画布', '无限画布，自由编排', 'canvas', 'linear-gradient(135deg,#153a4a,#3f8aa6)'],
    ['短剧导演台', '排演布景，告别抽卡', 'film', 'linear-gradient(135deg,#3a3320,#9c8a54)'],
    ['智能预演', '预演镜头，规划分镜', 'video', 'linear-gradient(135deg,#1d2a54,#5867c8)'],
    ['片段重拍', '视频局部修改', 'refresh', 'linear-gradient(135deg,#4a2436,#b05a7e)'],
    ['重制转绘', '一片多版创作', 'magic', 'linear-gradient(135deg,#2c2450,#7a5bc4)'],
    ['超分工作流', '提升画质，最高至 4k', 'hd', 'linear-gradient(135deg,#14332c,#3f9b83)'],
    ['视频拉片', '把成片变成创作教程', 'play', 'linear-gradient(135deg,#3d2a18,#b07b45)'],
  ];
  const DRAMA_PROJECTS = [
    { name: '未命名项目', meta: '3 个角色 · 10 集', arts: [2, 5, 8], who: ['林晚', '顾衍', '苏禾'] },
    { name: '旧教室的第三排', meta: '3 个角色 · 12 集', arts: [1, 6, 9], who: ['陈屿', '周老师', '许南'] },
  ];
  const DRAMA_BEATS = ['开局钩子', '身份错位', '初次交锋', '暗线浮出', '误会加深', '关键证据',
                       '反转落地', '并肩作战', '付出代价', '收束与回响'];

  const drama = { tab: 'write', style: '都市', topic: '先婚后爱', ratio: '9:16', eps: 10,
                  script: null, shots: [], board: {}, ep: 1, reels: {},
                  reelOpts: { transition: 0, subtitles: false, titles: false, music: 'off' },
                  videoTier: 'free', concurrency: 3, reelMode: 'local',
                  sbBusy: false, sbStop: false, sbPos: 0, sbTotal: 0, sbDone: 0 };

  /* ---- 分镜：镜头文字由剧本模板拆解，画面由 flux-3-image 真出 ---- */
  const SHOT_PLAN = [
    { size: '大全景', dur: 4, act: '建立', line: '（环境声）雨、车流、霓虹的电流声' },
    { size: '中景', dur: 3, act: '相遇', line: '（男主）「你迟到了十分钟。」' },
    { size: '特写', dur: 2, act: '道具', line: '（音效）纸页被抽出的摩擦声' },
    { size: '近景', dur: 3, act: '反应', line: '（女主·画外）「这东西，你从哪拿到的？」' },
    { size: '过肩', dur: 3, act: '对峙', line: '（对手）「你以为你还有得选。」' },
    { size: '俯拍', dur: 4, act: '钩子', line: '（音效）门在画面深处合上，锁舌一声脆响' },
  ];
  const DRAMA_LOOK = {
    都市: { places: ['写字楼地下车库', '雨夜便利店门口', '顶楼会议室', '公寓玄关'],
            props: ['一份还没签字的股权转让书', '一部还在振动的手机', '一只没送出去的戒指'],
            prop2: '被踩碎的伞骨', lens: '35mm 手持感，冷白路灯与霓虹反光' },
    古装: { places: ['宫门长廊', '雨夜驿站', '书房灯下', '城墙之上'],
            props: ['一封火漆未拆的家书', '半枚断裂的玉佩', '一盏快要烧尽的灯'],
            prop2: '散落的灯灰', lens: '50mm 稳定器，烛火与月色双光源' },
    悬疑: { places: ['旧仓库', '停尸房走廊', '雨夜天台', '审讯室'],
            props: ['一枚沾着泥的钥匙', '一张被撕掉一半的照片', '一支没录完的录音笔'],
            prop2: '拖行的水痕', lens: '40mm 低调布光，单侧硬光切脸' },
    科幻: { places: ['轨道站台', '无菌舱室', '数据中庭', '废弃控制室'],
            props: ['一枚熄灭的权限徽章', '一管浑浊的样本', '一块碎裂的舱窗'],
            prop2: '贴地流动的冷却雾', lens: '24mm 广角，青蓝冷光与体积雾' },
    家庭: { places: ['老式厨房', '楼道口', '医院走廊', '阳台晾衣杆下'],
            props: ['一张对折的缴费单', '一只缺口的搪瓷杯', '一件没织完的毛衣'],
            prop2: '滴水的衣角', lens: '50mm 自然光，暖黄钨丝灯' },
  };

  function buildShots(sc, ep = 1) {
    const look = DRAMA_LOOK[sc.style] || DRAMA_LOOK['都市'];
    // 每集轮换场景与道具：否则 10 集分镜文案一模一样，出图也会雷同
    const rot = (ep - 1) % look.places.length;
    const p0 = look.places[rot];
    const p2 = look.places[(rot + 2) % look.places.length];
    const p3 = look.places[(rot + 3) % look.places.length];
    const prop = look.props[(ep - 1) % look.props.length];
    const bodies = [
      `${p0}，女主独自走进画面，背影很小，环境压过来`,
      `${p0}，男主从暗处走出来，两人隔着一段距离停住，谁都没先开口`,
      `${prop}被推到画面正中，指尖刚碰到边缘`,
      '女主近景：视线落下去的一瞬，瞳孔收缩，嘴唇动了一下没出声',
      '越过对手的肩膀拍女主，前景肩线虚焦，构图把人挤到画面边缘',
      `俯拍${p3}，只剩两道影子和一地${look.prop2}，门在画面深处合上`,
    ];
    return SHOT_PLAN.map((plan, i) => ({
      id: `S${String(i + 1).padStart(2, '0')}`, n: i + 1, ep,
      size: plan.size, dur: plan.dur, act: plan.act, line: plan.line,
      scene: i === 5 ? `内景 · ${p3}` : i >= 2 ? `内景 · ${p2}` : `外景 · ${p0}`,
      desc: bodies[i],
      status: 'idle', img: null, ms: 0, credits: 0, model: null, error: null,
      stale: false, pct: 0,
    }));
  }

  const SHOT_MOVE = ['镜头缓慢推入', '手持轻跟随', '极慢推近', '微微下压', '横移半米', '缓慢升起'];

  function videoPrompt(sh, sc) {
    const move = SHOT_MOVE[Math.max(0, Math.min(SHOT_MOVE.length - 1, sh.n - 1))];
    return `${sh.desc}。${move}，${sc.style}题材短剧镜头，电影感布光，画面稳定，不出现任何文字、字幕`;
  }

  function shotPrompt(sh, sc) {
    const look = DRAMA_LOOK[sc.style] || DRAMA_LOOK['都市'];
    // 只描述画面：不要求模型写字，避免出图带乱码字幕
    return `${sh.desc}。${sc.style}题材短剧分镜，${sh.size}，${look.lens}，电影感布光，`
         + '人物写实，画面干净，不出现任何文字、字幕、水印';
  }

  function dramaEl(id) { return document.getElementById(id); }

  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data || data.ok === false) {
      throw new Error((data && data.error) || `HTTP ${res.status}`);
    }
    return data;
  }

  function llmConfigured() { return !!state.apiStatus?.llm?.configured; }

  /** 提示词里点了集数（「共 6 集」/「6 集短剧」）就以它为准，否则用工具条的值。 */
  function requestedEps(prompt) {
    const m = String(prompt).match(/(\d{1,2})\s*集/);
    const n = m ? Number(m[1]) : Number(drama.eps);
    return Math.max(1, Math.min(40, Number.isFinite(n) && n > 0 ? n : 10));
  }

  function applyEps(eps) {
    if (eps === drama.eps) return;
    drama.eps = eps;
    const el = dramaEl('dramaEpsVal');
    if (el) el.textContent = String(eps);
  }

  function llmModelName() { return state.apiStatus?.llm?.model || '文本模型'; }

  // 这一集的镜头文字是模型写的还是模板拆的：只认 script.shotsByEp，刷新后也一致
  function epSource(ep) { return drama.script?.shotsByEp?.[ep] ? 'llm' : 'template'; }

  function epModelName(ep) {
    return drama.script?.shotsMeta?.[ep]?.model || llmModelName();
  }

  function shotsFromModel(list, ep) {
    return list.map((shot, i) => ({
      id: `S${String(i + 1).padStart(2, '0')}`, n: i + 1, ep,
      size: SHOT_SIZES.includes(shot.size) ? shot.size : '中景',
      dur: Math.max(1, Math.min(8, Number(shot.dur) || 3)),
      act: shot.act || '', scene: shot.scene || '',
      line: shot.line || '（环境声）', desc: shot.desc || '',
      status: 'idle', img: null, ms: 0, credits: 0, model: null, error: null,
      stale: false, pct: 0,
    }));
  }

  function modelToScript(data, meta, prompt) {
    const base = buildScript(prompt);            // 风格/选题/比例的兜底
    const eps = Array.isArray(data.episodes) && data.episodes.length ? data.episodes : base.episodes;
    const chars = Array.isArray(data.characters) ? data.characters : [];
    return {
      ...base,
      title: data.title
        ? (/^《/.test(data.title) ? data.title : `《${data.title}》`)
        : base.title,
      logline: data.logline || prompt,
      cast: chars.length ? chars.map((c) => `${c.name} · ${c.desc}`) : base.cast,
      chars,
      acts: Array.isArray(data.acts) && data.acts.length
        ? data.acts.map((a) => [a.name, a.summary]) : base.acts,
      episodes: eps,
      eps: eps.length,
      source: 'llm',
      model: meta?.model || llmModelName(),
      ms: Number(meta?.elapsed_ms) || 0,
      tokens: Number(meta?.completion_tokens) || 0,
      shotsByEp: Array.isArray(data.shots) && data.shots.length ? { 1: data.shots } : {},
      shotsMeta: Array.isArray(data.shots) && data.shots.length
        ? { 1: { model: meta?.model || llmModelName(), ms: Number(meta?.elapsed_ms) || 0 } } : {},
      at: Date.now(),
    };
  }

  function updateDramaCount() {
    const el = dramaEl('dramaPrompt');
    const n = el.value.length;
    dramaEl('dramaCount').textContent = `${n}/500 字`;
    dramaEl('dramaSend').disabled = !el.value.trim();
  }

  function setDramaTab(tab) {
    drama.tab = tab;
    $$('.drama-tab').forEach((b) => {
      const on = b.dataset.dtab === tab;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', String(on));
    });
    dramaEl('dramaPaneWrite').hidden = tab !== 'write';
    dramaEl('dramaPaneUpload').hidden = tab !== 'upload';
    dramaEl('dramaPaneSoon').hidden = !(tab === 'convert' || tab === 'canvas');
    if (tab === 'convert' || tab === 'canvas') {
      dramaEl('dramaSoonTitle').textContent = tab === 'convert' ? '转绘' : '短剧画布';
    }
  }

  function renderDramaLists() {
    dramaEl('dramaIdeas').innerHTML = DRAMA_IDEAS.map(([t]) =>
      `<button class="idea" type="button" data-idea="${esc(t)}">${esc(t)}</button>`).join('');
    $$('#dramaIdeas .idea').forEach((b) => b.onclick = () => {
      const item = DRAMA_IDEAS.find((x) => x[0] === b.dataset.idea);
      const el = dramaEl('dramaPrompt');
      el.value = item[1].slice(0, 500);
      updateDramaCount(); el.focus();
      toast(`已填入「${item[0]}」的示例提示词`);
    });

    dramaEl('dramaFeats').innerHTML = DRAMA_FEATS.map(([t, sub, icon, bg]) => `
      <button class="feat" type="button" data-feat="${esc(t)}">
        <span class="feat-thumb" style="background:${bg}"><svg class="ico"><use href="#i-${icon}"></use></svg></span>
        <span class="feat-body"><span class="feat-title">${esc(t)}</span><span class="feat-sub">${esc(sub)}</span></span>
      </button>`).join('');
    $$('#dramaFeats .feat').forEach((b) => b.onclick = () =>
      toast(`「${b.dataset.feat}」在真实产品里是独立流程，本页只复刻入口`));

    dramaEl('dramaProjects').innerHTML =
      `<button class="proj proj-new" id="dramaNewProj" type="button">
         <svg class="ico"><use href="#i-plus"></use></svg><span>新建短剧画布</span>
       </button>` +
      DRAMA_PROJECTS.map((pr) => `
        <button class="proj" type="button" data-proj="${esc(pr.name)}">
          <span class="proj-cover">${pr.who.map((name, i) =>
            `<span class="role" style="--h:${[210, 28, 320][i % 3]}"><b>${esc(name.slice(0, 1))}</b><em class="proj-who">${esc(name)}</em></span>`).join('')}</span>
          <span class="proj-name">${esc(pr.name)}</span>
          <span class="proj-meta">${esc(pr.meta)}</span>
        </button>`).join('');
    dramaEl('dramaNewProj').onclick = () => { dramaEl('dramaPrompt').focus(); toast('新建短剧画布：先在下方写一句创意'); };
    $$('#dramaProjects .proj[data-proj]').forEach((b) => b.onclick = () =>
      toast(`「${b.dataset.proj}」是示例项目，本页不含真实工程数据`));
  }

  function dramaPick(anchor, title, options, key, onDone) {
    // simplePopover 的 options 是字符串数组（不是对象），别再造对象了
    simplePopover(anchor, title, options, drama[key], (v) => { drama[key] = v; onDone(v); });
  }

  const DRAMA_STYLES = ['都市', '古装', '悬疑', '科幻', '家庭'];
  const DRAMA_TOPICS = ['先婚后爱', '复仇', '逆袭', '穿越', '校园'];

  function buildScript(prompt) {
    // 提示词里点了名就以提示词为准，否则退回工具条的选择：
    // 否则「古装复仇」会被默认的「先婚后爱」写成标题，自相矛盾
    const style = DRAMA_STYLES.find((x) => prompt.includes(x)) || drama.style;
    const topic = DRAMA_TOPICS.find((x) => prompt.includes(x)) || drama.topic;
    // 剧名取「选题 + 正文前 10 字」：优先冒号后的正文，否则取第一小句；
    // 直接截整句会切出「写一部先婚后爱短剧」这种重复信息
    const afterColon = (prompt.split(/[：:]/)[1] || '').trim();
    const clause = (afterColon.length >= 6 ? afterColon
      : prompt.split(/[，。！？；:\n]/).filter((x) => x.trim().length > 3)[0] || prompt)
      .replace(/^(帮我|请|写一部|写个|来一部)/, '').trim();
    const sub = clause.slice(0, 10) + (clause.length > 10 ? '…' : '');
    const title = `《${topic}：${sub || '未命名'}》`;
    const cast = [
      `女主 · ${drama.topic === '先婚后爱' ? '被迫联姻的继承人' : '带着秘密回到小城的人'}——表面退让，实际握着关键证据`,
      `男主 · ${drama.style === '古装' ? '位高权重的对手' : '看似冷漠的合作方'}——一直在暗中替她兜底`,
      `对手 · 把两人推向对立的人——动机不是坏，而是怕失去`,
    ];
    const acts = [
      ['第一幕 · 建立', `用一场误会把主角推进「不得不合作」的局面。钩子：她在签字前发现对方早知道真相。`],
      ['第二幕 · 升级', `三次试探、两次反噬：每次她以为赢了，代价都落在身边的人身上。中点必须让关系翻转一次。`],
      ['第三幕 · 收束', `真相公开的场合选在最不该公开的时候；结尾留一句可回味的台词，不解释完。`],
    ];
    const eps = Array.from({ length: drama.eps }, (_, i) => {
      const beat = DRAMA_BEATS[i % DRAMA_BEATS.length];
      const extra = i >= DRAMA_BEATS.length ? `（升级 ${Math.floor(i / DRAMA_BEATS.length) + 1}）` : '';
      return { n: i + 1, title: `${beat}${extra}`, hook: i === 0 ? '前 5 秒抛出冲突' : i === drama.eps - 1 ? '留第二季口子' : '结尾反转' };
    });
    return { title, style, topic, ratio: drama.ratio, eps: drama.eps,
             logline: prompt, cast, acts, episodes: eps, at: Date.now() };
  }

  function renderScript(sc, scroll = true) {
    const box = dramaEl('dramaOut');
    const byLlm = sc.source === 'llm';
    const srcName = byLlm ? (sc.model || '文本模型') : '本地模板';
    const srcSecs = Math.round((Number(sc.ms) || 0) / 1000);
    const srcTitle = byLlm
      ? `剧本由 ${srcName} 生成（${srcSecs}s${sc.tokens ? ` · ${sc.tokens} tokens` : ''}）；分镜图另由 flux-3-image 真实生成`
      : '剧本大纲按关键词拼接，未调用文本模型；下方的分镜图另由 flux-3-image 真实生成';
    const srcNote = byLlm
      ? `剧本由 <b>${esc(srcName)}</b> 真实生成（${srcSecs}s）；分镜<b>图</b>不是模板——下面每张点「生成」都会真的调一次 flux-3-image。`
      : '剧本部分是按关键词拼的结构模板（未接文本模型）；分镜<b>图</b>不是模板——下面每张点「生成」都会真的调一次 flux-3-image。';
    box.hidden = false;
    box.innerHTML = `
      <div class="drama-out-head">
        <div>
          <h3>${esc(sc.title)}</h3>
          <p>${esc(sc.style)} · ${esc(sc.topic)} · ${sc.eps} 集 · ${esc(sc.ratio)}</p>
        </div>
        <span class="drama-badge-local" data-src="${byLlm ? 'llm' : 'template'}" title="${esc(srcTitle)}">剧本：${esc(srcName)}</span>
        <button class="pill-btn" id="dramaCopy" type="button"><svg class="ico"><use href="#i-share"></use></svg>复制</button>
      </div>
      <div class="drama-out-body">
        <div class="script-block"><h4>一句话梗概</h4><p>${esc(sc.logline)}</p></div>
        <div class="script-block"><h4>主要人物</h4><p>${sc.cast.map(esc).join('\n')}</p></div>
        <div class="script-block"><h4>三幕结构</h4><p>${sc.acts.map(([h, t]) => `${h}：${t}`).join('\n\n')}</p></div>
        <div class="script-block"><h4>分集大纲（${sc.eps} 集）</h4>
          <div class="script-eps">${sc.episodes.map((e) =>
            `<div class="script-ep"><b>${String(e.n).padStart(2, '0')}</b><span>${esc(e.title)} · ${esc(e.hook)}</span></div>`).join('')}</div>
        </div>
        <p class="cfg-note">${srcNote}</p>
      </div>`;
    dramaEl('dramaCopy').onclick = () => {
      const text = [sc.title, sc.logline, '', ...sc.cast, '', ...sc.acts.map(([h, t]) => `${h}：${t}`), '',
        ...sc.episodes.map((e) => `第 ${e.n} 集 ${e.title}：${e.hook}`)].join('\n');
      navigator.clipboard?.writeText(text).then(() => toast('大纲已复制')).catch(() => toast('复制失败，请手动选择', 'error'));
    };
    if (scroll) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  /* ---- 分镜工作台：多集 · 可改镜 · 可重拍 · 刷新不丢 ---- */
  const SHOT_SIZES = ['大全景', '全景', '中景', '近景', '特写', '过肩', '俯拍'];
  const BOARD_KEY = 'jimeng-storyboard-v1';
  const BOARD_MAX_EP = 40;

  function sbEl(id) { return document.getElementById(id); }
  function doneOf(list) { return list.filter((s) => s.status === 'done').length; }

  function buildBoard(sc) {
    const board = {};
    const total = Math.min(sc.eps, BOARD_MAX_EP);
    for (let ep = 1; ep <= total; ep += 1) {
      const written = sc.shotsByEp && sc.shotsByEp[ep];
      board[ep] = written && written.length ? shotsFromModel(written, ep) : buildShots(sc, ep);
    }
    return board;
  }

  function saveBoard() {
    if (!drama.script || !drama.board) return;
    const pack = {};
    Object.keys(drama.board).forEach((ep) => {
      pack[ep] = drama.board[ep].map((s) => ({
        n: s.n, size: s.size, dur: s.dur, act: s.act, scene: s.scene, line: s.line, desc: s.desc,
        status: s.status === 'running' ? 'idle' : s.status,
        img: s.img, ms: s.ms, credits: s.credits, model: s.model, error: s.error, stale: !!s.stale,
        vidStatus: ['done', 'error'].includes(s.vidStatus) ? s.vidStatus : 'idle',
        vidUrl: s.vidUrl || null, vidMs: s.vidMs || 0, vidErr: s.vidErr || null,
      }));
    });
    try {
      localStorage.setItem(BOARD_KEY, JSON.stringify({
        v: 1, at: Date.now(), ep: drama.ep, script: drama.script, board: pack,
        reels: drama.reels, reelOpts: drama.reelOpts, videoTier: drama.videoTier,
        concurrency: drama.concurrency, reelMode: drama.reelMode,
      }));
    } catch { /* 配额满就放弃持久化，不影响本次会话 */ }
  }

  function restoreBoard() {
    let raw;
    try { raw = JSON.parse(localStorage.getItem(BOARD_KEY) || 'null'); } catch { raw = null; }
    if (!raw || raw.v !== 1 || !raw.script || !raw.board) return false;
    const sc = raw.script;
    if (!Array.isArray(sc.episodes) || !sc.episodes.length) return false;
    const board = {};
    Object.keys(raw.board).forEach((k) => {
      const ep = Number(k);
      const list = raw.board[k];
      if (!Number.isFinite(ep) || ep < 1 || !Array.isArray(list) || !list.length) return;
      board[ep] = list.slice(0, 12).map((s, i) => {
        const img = typeof s.img === 'string' && s.img ? s.img : null;
        const status = (s.status === 'done' && img) || s.status === 'error' ? s.status : 'idle';
        return {
          id: `S${String(i + 1).padStart(2, '0')}`, ep, n: i + 1,
          size: SHOT_SIZES.includes(s.size) ? s.size : '中景',
          dur: Number(s.dur) || 3, act: s.act || '', scene: s.scene || '',
          line: s.line || '', desc: String(s.desc || ''),
          status, img, ms: Number(s.ms) || 0, credits: Number(s.credits) || 0,
          model: s.model || null, error: s.error || null, stale: !!s.stale, pct: 0,
          vidStatus: s.vidStatus === 'done' && s.vidUrl ? 'done' : (s.vidStatus === 'error' ? 'error' : 'idle'),
          vidUrl: typeof s.vidUrl === 'string' ? s.vidUrl : null,
          vidMs: Number(s.vidMs) || 0, vidErr: s.vidErr || null, vidId: null, vidStartedAt: 0,
        };
      });
    });
    if (!board[1]) return false;
    drama.script = sc;
    drama.board = board;
    drama.ep = board[Number(raw.ep)] ? Number(raw.ep) : 1;
    drama.shots = board[drama.ep];
    const reels = {};
    Object.keys(raw.reels || {}).forEach((k) => {
      const r = raw.reels[k];
      if (r && typeof r.url === 'string' && r.url.startsWith('/assets/generated/reel-')) {
        reels[Number(k)] = { url: r.url, segments: Number(r.segments) || 0,
                             seconds: Number(r.seconds) || 0, ms: Number(r.ms) || 0 };
      }
    });
    drama.reels = reels;
    if (raw.reelOpts && typeof raw.reelOpts === 'object') {
      const o = raw.reelOpts;
      drama.reelOpts = {
        transition: [0, 0.3, 0.5].includes(Number(o.transition)) ? Number(o.transition) : 0,
        subtitles: !!o.subtitles, titles: !!o.titles,
        music: o.music === 'drone' ? 'drone' : 'off',
      };
    }
    if (raw.videoTier === 'paid' || raw.videoTier === 'free') drama.videoTier = raw.videoTier;
    if ([1, 2, 3, 4].includes(Number(raw.concurrency))) drama.concurrency = Number(raw.concurrency);
    if (raw.reelMode === 'cloud' || raw.reelMode === 'local') drama.reelMode = raw.reelMode;
    return true;
  }

  function clearBoard() {
    try { localStorage.removeItem(BOARD_KEY); } catch { /* ignore */ }
    drama.script = null; drama.board = {}; drama.shots = []; drama.ep = 1;
    sbEl('sbSection')?.remove();
    const box = dramaEl('dramaOut');
    if (box) { box.hidden = true; box.innerHTML = ''; }
  }

  function shotCardHTML(sh) {
    return `
      <article class="sb-card" data-shot="${sh.id}" data-status="${sh.status}" data-stale="${sh.stale ? 'true' : 'false'}">
        <div class="sb-frame" style="--ar:${String(drama.ratio).replace(':', ' / ')}">
          <div class="sb-ph"><svg class="ico"><use href="#i-image"></use></svg><span>待生成</span></div>
          <img class="sb-img" alt="${sh.id} 分镜图" hidden>
          <video class="sb-video" playsinline muted loop preload="metadata" hidden></video>
          <div class="sb-loading" hidden></div>
          <span class="sb-queue" hidden>排队中</span>
          <div class="sb-vidmask" hidden><span class="sb-vidtxt">排队中…</span><i class="sb-vidline"></i></div>
          <span class="sb-no">${sh.id}</span>
          <span class="sb-pill">${sh.size} · ${sh.dur.toFixed(1)}s</span>
          <span class="sb-pct" hidden>0%</span>
          <div class="sb-bar" hidden><i></i></div>
        </div>
        <div class="sb-body">
          <p class="sb-mini"><b>${sh.id}</b><span>${sh.size} · ${sh.dur.toFixed(1)}s</span></p>
          <p class="sb-desc" title="${esc(sh.scene)}">${esc(sh.desc)}</p>
          <p class="sb-line">${esc(sh.line)}</p>
          <div class="sb-foot">
            <button class="sb-gen" type="button">生成分镜图</button>
            <button class="sb-vid" type="button" hidden>出视频</button>
            <div class="sb-meta">
              <span class="sb-state">1 次调用</span>
              <button class="sb-edit" type="button">编辑</button>
            </div>
          </div>
        </div>
      </article>`;
  }

  function renderStoryboard() {
    const box = dramaEl('dramaOut');
    if (!box || !drama.script || !drama.shots.length) return;
    const sc = drama.script;
    sbEl('sbSection')?.remove();
    const sec = document.createElement('section');
    sec.className = 'sb';
    sec.id = 'sbSection';
    sec.innerHTML = `
      <div class="sb-head">
        <div class="sb-title">
          <h4>分镜表 · 第 ${drama.ep} 集 · ${drama.shots.length} 镜</h4>
          <p>${sbSubtitle()}${llmConfigured() ? ` · <button class="sb-inline-link" id="sbLlmEp" type="button">${epSource(drama.ep) === 'llm' ? '再让模型重写本集' : '用模型写本集镜头'}</button>` : ''}</p>
        </div>
        <div class="sb-eps" role="group" aria-label="切换集数">
          <button class="sb-epnav" id="sbPrev" type="button" aria-label="上一集">‹</button>
          <span class="sb-epnow" id="sbEpNow">${drama.ep} / ${sc.eps}</span>
          <button class="sb-epnav" id="sbNext" type="button" aria-label="下一集">›</button>
        </div>
        <span class="sb-badge" id="sbBadge" data-mode="idle">待出图</span>
        <div class="sb-acts">
          <button class="sb-tool" id="sbGear" type="button" title="生成设置" aria-label="生成设置">⚙</button>
          <button class="sb-tool" id="sbAddShot" type="button" title="在本集末尾加一镜" aria-label="加一镜">＋镜</button>
          <button class="pill-btn" id="sbReelBtn" type="button">预览本集成片</button>
          <button class="pill-btn" id="sbAll" type="button">生成本集</button>
          <button class="pill-btn sb-stop" id="sbStop" type="button" hidden>停止</button>
        </div>
      </div>
      <div class="sb-reel" id="sbReelBox" hidden>
        <div class="sb-reel-head">
          <b>本集成片 · 第 ${drama.ep} 集</b>
          <span class="sb-reel-meta" id="sbReelMeta"></span>
          <a class="pill-btn" id="sbReelDl" download>下载</a>
        </div>
        <video class="sb-reel-video" id="sbReelVideo" controls playsinline preload="metadata"></video>
      </div>
      <div class="sb-grid" id="sbGrid">${drama.shots.map(shotCardHTML).join('')}</div>
      <p class="sb-note" id="sbNote"><span id="sbProgress"></span> · 上游固定 1024×1024 方形出图，画框按 ${esc(sc.ratio)} 裁切预览（不改原图）；积分按 1K / 1 张本机预估，不是上游账单。链路没就绪时按钮会直接拒绝，不会拿占位图冒充。</p>`;
    box.appendChild(sec);
    drama.shots.forEach((sh) => {
      const card = sbEl('sbGrid').querySelector(`[data-shot="${sh.id}"]`);
      if (!card) return;
      card.querySelector('.sb-gen').onclick = () => genShot(sh.id);
      card.querySelector('.sb-vid').onclick = () => genVideo(sh);
      card.querySelector('.sb-video').onclick = (e) => { e.preventDefault(); e.currentTarget.play?.(); };
      card.querySelector('.sb-edit').onclick = () => editShot(sh);
      card.querySelector('.sb-img').onclick = () => {
        if (!sh.img) return;
        const src = (sh.model || '').startsWith('MOCK') ? 'mock' : 'real';
        openLightbox({ images: [{ url: sh.img, source: src }] }, 0);
      };
    });
    sbEl('sbAll').onclick = genAllShots;
    sbEl('sbReelBtn').onclick = genReel;
    sbEl('sbGear').onclick = () => openReelSettings(sbEl('sbGear'));
    sbEl('sbAddShot').onclick = addShot;
    if (sbEl('sbLlmEp')) sbEl('sbLlmEp').onclick = rewriteEpWithLlm;
    sbEl('sbStop').onclick = async () => {
      drama.sbStop = true;
      const pending = drama.shots.filter((s) => s.taskId && s.status === 'queued');
      await Promise.all(pending.map((s) => fetch(
        `/api/tasks/${encodeURIComponent(s.taskId)}/cancel`, { method: 'POST' }).catch(() => {})));
      paintSbHead(); drama.shots.forEach(paintShot);
      toast(pending.length
        ? `已取消排队中的 ${pending.length} 张；正在跑的那几张不会被打断`
        : '已停止排队：当前正在跑的这一张不会被中断');
    };
    sbEl('sbPrev').onclick = () => setEp(drama.ep - 1);
    sbEl('sbNext').onclick = () => setEp(drama.ep + 1);
    drama.shots.forEach(paintShot);
    paintReel();
    paintSbHead();
  }

  function sbSubtitle() {
    if (epSource(drama.ep) === 'llm') {
      return `镜头文字由 ${esc(epModelName(drama.ep))} 按剧情写；画面用 flux-3-image 真实生成，每张 = 1 次上游请求`;
    }
    return '镜头文字由剧本模板拆解，可逐镜改画面/景别；画面用 flux-3-image 真实生成，每张 = 1 次上游请求';
  }

  async function rewriteEpWithLlm() {
    if (drama.sbBusy) { toast('正在批量出图，先等这一轮跑完'); return; }
    const ep = drama.ep;
    const sc = drama.script;
    if (!sc || !llmConfigured()) return;
    const made = drama.shots.filter((sh) => sh.img || sh.vidUrl).length;
    if (made && !confirm(`第 ${ep} 集已有 ${made} 个镜头出过图。重写镜头会让它们与新描述对不上（图片不会自动删除）。继续？`)) return;
    const btn = sbEl('sbLlmEp');
    if (btn) { btn.disabled = true; btn.textContent = '模型写作中…'; }
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (btn) btn.textContent = `模型写作中… ${Math.round((Date.now() - t0) / 1000)}s`;
    }, 500);
    try {
      const r = await postJSON('/api/shots', {
        prompt: dramaEl('dramaPrompt').value.trim() || sc.logline,
        ep,
        context: { title: sc.title, logline: sc.logline, characters: sc.chars || [],
                   episodes: sc.episodes },
      });
      sc.shotsByEp = sc.shotsByEp || {};
      sc.shotsByEp[ep] = r.data;
      sc.shotsMeta = sc.shotsMeta || {};
      sc.shotsMeta[ep] = { model: r.meta?.model || llmModelName(), ms: Number(r.meta?.elapsed_ms) || 0 };
      drama.board[ep] = shotsFromModel(r.data, ep);
      if (ep === drama.ep) drama.shots = drama.board[ep];
      renderStoryboard();
      saveBoard();
      toast(`第 ${ep} 集镜头已由 ${r.meta?.model || llmModelName()} 重写（${Math.round((r.meta?.elapsed_ms || 0) / 1000)}s）`);
    } catch (err) {
      if (btn) { btn.disabled = false; btn.textContent = '用模型写本集镜头'; }
      toast(`重写失败：${String(err.message).slice(0, 60)}`, 'error');
    } finally {
      clearInterval(tick);
    }
  }

  function setEp(ep) {
    const total = drama.script?.eps || 1;
    const next = Math.max(1, Math.min(total, ep));
    if (next === drama.ep) return;
    if (drama.sbBusy) { toast('正在批量生成，先等这一轮跑完'); return; }
    drama.ep = next;
    drama.shots = drama.board[next] || [];
    saveBoard();
    renderStoryboard();
  }

  function paintShot(sh) {
    const card = sbEl('sbGrid')?.querySelector(`[data-shot="${sh.id}"]`);
    if (!card) return;
    card.dataset.status = sh.status;
    card.dataset.stale = sh.stale ? 'true' : 'false';
    const img = card.querySelector('.sb-img');
    const ph = card.querySelector('.sb-ph');
    const load = card.querySelector('.sb-loading');
    const bar = card.querySelector('.sb-bar');
    const pct = card.querySelector('.sb-pct');
    const btn = card.querySelector('.sb-gen');
    const edit = card.querySelector('.sb-edit');
    const stateEl = card.querySelector('.sb-state');
    const running = sh.status === 'running';
    const queuedNow = sh.status === 'queued';
    load.hidden = !(running || queuedNow);
    bar.hidden = !running;
    pct.hidden = !running;
    if (queuedNow) {
      const q = card.querySelector('.sb-queue');
      if (q) { q.hidden = false; q.textContent = sh.queuePos ? `排队第 ${sh.queuePos} 位` : '排队中'; }
    } else {
      const q = card.querySelector('.sb-queue');
      if (q) q.hidden = true;
    }
    const video = card.querySelector('.sb-video');
    const mask = card.querySelector('.sb-vidmask');
    const vbtn = card.querySelector('.sb-vid');
    const vbusy = sh.vidStatus === 'queued' || sh.vidStatus === 'running';
    const configured = !!state.apiStatus?.video?.configured;
    if (sh.vidUrl) {
      if (video.getAttribute('src') !== sh.vidUrl) {
        video.src = sh.vidUrl;
        video.poster = sh.img || '';
      }
      video.hidden = false;
      if (sh.img) { if (img.getAttribute('src') !== sh.img) img.src = sh.img; img.hidden = true; ph.hidden = true; }
    } else if (video) {
      video.hidden = true;
    }
    if (mask) {
      mask.hidden = !vbusy;
      if (vbusy) {
        const waited = Math.max(0, Math.round((Date.now() - (sh.vidStartedAt || Date.now())) / 1000));
        const label = mask.querySelector('.sb-vidtxt');
        if (label) label.textContent = sh.vidStatus === 'queued' ? `视频排队中 · ${waited}s` : `视频生成中 · ${waited}s`;
      }
    }
    if (vbtn) {
      vbtn.hidden = !sh.img;
      vbtn.disabled = vbusy || !sh.img || !configured;
      vbtn.textContent = vbusy ? '出视频中…'
        : sh.vidStatus === 'done' ? '重出视频'
        : sh.vidStatus === 'error' ? '重试视频' : '出视频';
      vbtn.title = !configured ? '视频链路未配置（proxy 缺 AVM_COOKIE）'
        : sh.vidStatus === 'error' ? (sh.vidErr || '失败')
        : `H3 免费档 · ${state.apiStatus?.video?.resolution || '720p'} · ${state.apiStatus?.video?.duration || 8}s · 首帧=这张分镜图`;
    }
    if (sh.img) {
      if (!sh.vidUrl) {
        if (img.getAttribute('src') !== sh.img) img.src = sh.img;
        img.hidden = false; ph.hidden = true;
      }
    } else {
      img.hidden = true; ph.hidden = false;
      const label = ph.querySelector('span');
      if (label) label.textContent = sh.status === 'error' ? '生成失败' : '待生成';
    }
    if (running) {
      bar.firstElementChild.style.width = `${sh.pct}%`;
      pct.textContent = `${Math.round(sh.pct)}%`;
    }
    btn.disabled = running || queuedNow || drama.sbBusy;
    btn.textContent = queuedNow ? '排队中…'
      : running ? '生成中…'
      : sh.status === 'done' ? (sh.stale ? '按新提示词生成' : '重新生成')
      : sh.status === 'error' ? '重试'
      : sh.stale ? '按新提示词生成' : '生成分镜图';
    if (edit) edit.disabled = running || queuedNow;
    const took = sh.ms >= 1000 ? `${(sh.ms / 1000).toFixed(1)}s` : `${Math.max(1, Math.round(sh.ms))}ms`;
    stateEl.textContent = queuedNow ? (sh.queuePos ? `已提交 · 排队第 ${sh.queuePos} 位` : '已提交 · 排队中')
      : running ? `上游生成中…${sh.attempts > 1 ? ` · 第 ${sh.attempts} 次尝试` : ''}`
      : sh.status === 'error' ? (sh.error || '失败')
      : sh.vidStatus === 'done' ? `视频 ${(sh.vidMs / 1000).toFixed(0)}s · H3 免费档`
      : sh.vidStatus === 'error' ? (sh.vidErr || '视频失败')
      : sh.status === 'done' ? `${sh.model || 'flux-3-image'} · ${took} · 预计 ${sh.credits} 积分${sh.stale ? ' · 已改提示词' : ''}`
      : sh.stale ? '改过提示词 · 待出图'
      : '1 次调用';
    stateEl.title = sh.status === 'error' ? (sh.error || '') : stateEl.textContent;
  }

  function paintSbHead() {
    const badge = sbEl('sbBadge');
    if (!badge) return;
    const shots = drama.shots;
    const total = shots.length;
    const done = doneOf(shots);
    const err = shots.filter((s) => s.status === 'error').length;
    const live = !!state.apiStatus?.real;
    const mock = state.apiStatus?.upstreamMode === 'mock';
    badge.dataset.mode = !live ? 'demo' : mock ? 'mock' : 'live';
    badge.textContent = !live ? '演示模式 · 不出图'
      : mock ? `MOCK 上游 · ${done}/${total}（零计费）`
      : `flux-3-image · ${done}/${total} 张${err ? ` · ${err} 失败` : ''}`;
    badge.title = !live ? '链路未就绪：点生成会拒绝，不拿假图冒充'
      : mock ? '上游是 127.0.0.1 的 mock，完整往返但零计费'
      : `${state.apiStatus?.model} @ ${state.apiStatus?.base} · 本集已出图 ${done}/${total}`;
    const now = sbEl('sbEpNow');
    if (now) now.textContent = `${drama.ep} / ${drama.script.eps}`;
    const prev = sbEl('sbPrev');
    const next = sbEl('sbNext');
    if (prev) prev.disabled = drama.ep <= 1;
    if (next) next.disabled = drama.ep >= drama.script.eps;
    const all = sbEl('sbAll');
    const stop = sbEl('sbStop');
    if (all) {
      all.disabled = drama.sbBusy || !live || done === total;
      const flying = drama.shots.filter((s) => s.status === 'running').length;
      all.textContent = drama.sbBusy
        ? `生成中 ${drama.sbDone}/${drama.sbTotal}${flying > 1 ? ` · ${flying} 在飞` : ''}`
        : done === total ? '本集已全部生成' : `生成本集（${total - done} 张）`;
      if (!live) all.title = '链路未就绪：演示模式不出图';
    }
    if (stop) stop.hidden = !drama.sbBusy;
    const rb = sbEl('sbReelBtn');
    if (rb) {
      const segs = reelSegments().length;
      const r = drama.reels[drama.ep];
      rb.disabled = segs === 0;
      rb.textContent = segs === 0 ? '预览本集成片'
        : (r && r.segments === segs ? `重拼本集（${segs} 段）` : `预览本集（${segs} 段）`);
      const optNote = [
        `转场 ${Number(drama.reelOpts.transition) === 0 ? '硬切' : drama.reelOpts.transition + 's'}`,
        drama.reelOpts.subtitles ? '字幕' : '',
        drama.reelOpts.titles ? '片头片尾' : '',
        drama.reelOpts.music === 'drone' ? '底音' : '',
      ].filter(Boolean).join(' · ');
      rb.title = segs === 0 ? '本集还没有镜头视频'
        : `用 ffmpeg 把本集 ${segs} 段按镜序拼成一条（704×1248/24fps · ${optNote}，本地能力，不花上游额度）`;
    }
    const prog = sbEl('sbProgress');
    if (prog && drama.board) {
      const allDone = Object.values(drama.board).reduce((a, list) => a + doneOf(list), 0);
      const allTotal = Object.values(drama.board).reduce((a, list) => a + list.length, 0);
      const vids = Object.values(drama.board).reduce(
        (a, list) => a + list.filter((s) => s.vidStatus === 'done' && s.vidUrl).length, 0);
      prog.textContent = `全剧 ${allDone}/${allTotal} 镜已出图${vids ? ` · 视频 ${vids} 段` : ''}`;
    }
  }

  function editShot(sh) {
    const card = sbEl('sbGrid')?.querySelector(`[data-shot="${sh.id}"]`);
    const anchor = card?.querySelector('.sb-edit') || card;
    if (!anchor) return;
    const sizes = SHOT_SIZES.map((x) =>
      `<button class="sb-size ${x === sh.size ? 'is-selected' : ''}" data-size="${esc(x)}" type="button">${esc(x)}</button>`).join('');
    const el = openPopover(anchor, `
      <div class="pop-title"><svg class="ico"><use href="#i-gear"></use></svg>第 ${drama.ep} 集 · ${sh.id}</div>
      <label class="sb-edit-label" for="sbEditDesc">画面描述（改动只影响下一次生成）</label>
      <textarea id="sbEditDesc" class="sb-edit-ta" rows="3" maxlength="160">${esc(sh.desc)}</textarea>
      <div class="sb-edit-label">景别</div>
      <div class="sb-size-row" id="sbSizeRow">${sizes}</div>
      <div class="sb-edit-actions">
        <button class="pill-btn sb-edit-move" id="sbEditUp" type="button">上移</button>
        <button class="pill-btn sb-edit-move" id="sbEditDown" type="button">下移</button>
        <button class="pill-btn sb-danger" id="sbEditDel" type="button">删除</button>
        <button class="pill-btn" id="sbEditCancel" type="button">取消</button>
        <button class="pill-btn sb-edit-save" id="sbEditSave" type="button">保存</button>
      </div>`, { align: 'right' });
    let picked = sh.size;
    Array.from(el.querySelectorAll('[data-size]')).forEach((b) => {
      b.onclick = () => {
        picked = b.dataset.size;
        Array.from(el.querySelectorAll('[data-size]')).forEach((x) => x.classList.toggle('is-selected', x === b));
      };
    });
    el.querySelector('#sbEditCancel').onclick = closePopover;
    el.querySelector('#sbEditUp').onclick = () => moveShot(sh, -1);
    el.querySelector('#sbEditDown').onclick = () => moveShot(sh, 1);
    el.querySelector('#sbEditDel').onclick = () => deleteShot(sh);
    el.querySelector('#sbEditSave').onclick = () => {
      const desc = el.querySelector('#sbEditDesc').value.trim().slice(0, 160);
      if (!desc) { toast('画面描述不能为空', 'error'); return; }
      const changed = desc !== sh.desc || picked !== sh.size;
      sh.desc = desc;
      sh.size = picked;
      if (changed) sh.stale = true;
      closePopover();
      paintShot(sh);
      saveBoard();
      toast(changed ? '已改这一镜：点「按新提示词生成」才会重出' : '没有改动');
    };
    el.querySelector('#sbEditDesc').focus();
  }

  async function genShot(id, fromQueue = false) {
    const sh = drama.shots.find((s) => s.id === id);
    if (!sh || !drama.shots.includes(sh) || sh.status === 'running') return false;
    if (!state.statusReady) { toast('正在检测生成链路，请稍候…', true); return false; }
    if (!state.apiStatus?.real) {
      toast('演示模式不出图：这里不会用占位图冒充 flux 结果', 'error');
      return false;
    }
    if (drama.sbBusy && !fromQueue) { toast('正在批量生成，先等这一轮跑完'); return false; }
    if (!fromQueue && sh.status === 'done') sh.gen = (sh.gen || 0) + 1;   // 主动重出 = 新任务

    // 先提交（毫秒级返回），再轮询；这里不再让一条 HTTP 请求挂在等上游上。
    sh.status = 'queued'; sh.pct = 0; sh.error = null; sh.attempts = 0;
    sh.taskId = null; sh.queuePos = 0;
    paintShot(sh); paintSbHead();
    const t0 = performance.now();
    let started = false;
    const timer = setInterval(() => {
      if (sh.status !== 'running') { clearInterval(timer); return; }
      sh.pct = Math.min(93, sh.pct + (2.4 + Math.random() * 5.6) * (1 - sh.pct / 130));
      paintShot(sh);
    }, 800);
    let out;
    try {
      out = await taskGenerate(shotPrompt(sh, drama.script), shotIdem(sh), (st, tid, task) => {
        sh.taskId = tid;
        if (st === 'running' && sh.status !== 'running') {
          started = true; sh.status = 'running'; sh.pct = 3;
        }
        if (typeof task.queue_position === 'number' && task.queue_position > 0) sh.queuePos = task.queue_position;
        if (typeof task.attempts === 'number') sh.attempts = task.attempts;
        paintShot(sh); paintSbHead();
      }, currentImageModel());
    } catch (err) {
      out = { error: String((err && err.message) || err) };
    } finally {
      clearInterval(timer);
      if (!started) sh.status = 'queued';
    }
    const ok = !out.error && out.images && out.images[0];
    if (!ok) {
      sh.status = out.canceled ? 'idle' : 'error';
      sh.error = out.canceled ? null : String(out.error || '上游没有返回图片').slice(0, 80);
      sh.ms = Math.round(performance.now() - t0);
      sh.taskId = null; sh.queuePos = 0;
    } else {
      sh.status = 'done'; sh.img = out.images[0].url; sh.stale = false;
      sh.ms = out.ms || Math.round(performance.now() - t0);
      sh.credits = Number.isFinite(out.credits) ? out.credits : 0;
      sh.model = out.model
        ? (out.upstreamMode === 'mock' ? `MOCK ${out.model}` : out.model)
        : (out.upstreamMode === 'mock' ? 'MOCK flux-3-image' : 'flux-3-image');
      sh.attempts = out.attempts || 1;
      sh.pct = 100; sh.taskId = null; sh.queuePos = 0;
      state.assets.unshift({ url: sh.img, name: `flux-3-image 分镜 E${drama.ep}${sh.id}`, type: 'image', fav: false });
    }
    paintShot(sh); paintSbHead(); saveBoard();
    return !!ok;
  }

  async function imgToDataURI(url) {
    const res = await fetch(url, { cache: 'force-cache' });
    if (!res.ok) throw new Error(`首帧读取失败 HTTP ${res.status}`);
    const blob = await res.blob();
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = () => reject(new Error('首帧读取失败'));
      fr.readAsDataURL(blob);
    });
  }

  async function genVideo(sh) {
    if (sh.vidStatus === 'queued' || sh.vidStatus === 'running') return false;
    if (!sh.img) { toast('先出这一镜的分镜图，再出视频'); return false; }
    if (!state.statusReady) { toast('正在检测链路，请稍候…', true); return false; }
    if (!state.apiStatus?.video?.configured) { toast('视频链路未配置：proxy 缺少 AVM_COOKIE', 'error'); return false; }
    sh.vidStatus = 'queued'; sh.vidErr = null; sh.vidUrl = null; sh.vidStartedAt = Date.now();
    paintShot(sh); saveBoard();
    let data;
    try {
      const image = await imgToDataURI(sh.img);
      const res = await fetch('/api/video', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: videoPrompt(sh, drama.script), image, tier: drama.videoTier }),
      });
      data = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    } catch (err) {
      sh.vidStatus = 'error';
      sh.vidErr = String((err && err.message) || err).slice(0, 80);
      paintShot(sh); saveBoard();
      return false;
    }
    sh.vidId = data.id;
    sh.vidStatus = 'running';
    paintShot(sh); saveBoard();
    pollVideo(sh);
    return true;
  }

  async function pollVideo(sh) {
    const id = sh.vidId;
    if (!id) return;
    const t0 = sh.vidStartedAt || Date.now();
    let failures = 0;
    for (let i = 0; i < 90; i += 1) {
      await new Promise((r) => setTimeout(r, 4000));
      if (sh.vidId !== id || sh.vidStatus !== 'running') return;
      let d = null;
      try {
        const res = await fetch(`/api/video/${encodeURIComponent(id)}`, { cache: 'no-store' });
        d = await res.json();
      } catch (err) {
        d = null;
      }
      if (!d) {
        failures += 1;
        if (failures >= 5) { sh.vidStatus = 'error'; sh.vidErr = '轮询失败（代理不可达）'; paintShot(sh); saveBoard(); return; }
        paintShot(sh);
        continue;
      }
      failures = 0;
      if (d.status === 'succeeded' && !d.url) {
        sh.vidStatus = 'error';
        sh.vidErr = (typeof d.warn === 'string' ? d.warn : '成片已生成，但本地转存失败').slice(0, 80);
        paintShot(sh); saveBoard();
        return;
      }
      if (d.status === 'succeeded' && d.url) {
        sh.vidStatus = 'done'; sh.vidUrl = d.url; sh.vidMs = Date.now() - t0; sh.vidErr = null;
        paintShot(sh); paintSbHead(); saveBoard();
        toast(`视频已出：${sh.id} · ${(sh.vidMs / 1000).toFixed(0)}s`);
        return;
      }
      if (d.status === 'failed') {
        sh.vidStatus = 'error';
        sh.vidErr = (typeof d.error === 'string' ? d.error
          : (d.error && (d.error.message || d.error.code)) || d.warn || '上游失败').slice(0, 80);
        paintShot(sh); saveBoard();
        return;
      }
      paintShot(sh);
    }
    sh.vidStatus = 'error'; sh.vidErr = '等待超时（6 分钟）';
    paintShot(sh); saveBoard();
  }

  function saveReelOpts(patch) {
    Object.assign(drama.reelOpts, patch);
    saveBoard();
    paintSbHead();
  }

  function openReelSettings(anchor) {
    const o = drama.reelOpts;
    const v = state.apiStatus?.video || {};
    const allowPaid = !!v.allowPaid;
    const tiers = v.tiers || { free: { resolution: '720p', duration: 8 } };
    const el = openPopover(anchor, `
      <div class="pop-title">${ico('gear')}生成设置</div>
      <div class="sb-set-label">成片算力（拼本集用谁跑）</div>
      <div class="sb-set-row">
        <button class="sb-chip ${drama.reelMode === 'local' ? 'is-on' : ''}" data-reelmode="local" type="button">本地 ffmpeg</button>
        <button class="sb-chip ${drama.reelMode === 'cloud' ? 'is-on' : ''}" data-reelmode="cloud" type="button"
          ${state.apiStatus?.reelCloud?.configured ? '' : 'disabled title="云端未配置（qa/reel-cloud-key.txt）"'}>云端 ${esc((state.apiStatus?.reelCloud?.base || 'reel.1task.cn').replace('https://', ''))}</button>
      </div>
      <div class="sb-set-label">出图任务（提交即返回，服务端队列执行）</div>
      <div class="sb-set-row sb-set-info">
        <span>${Number(state.apiStatus?.workers) || 0} 路并发 · 队列上限 ${Number(state.apiStatus?.queueMax) || 0}
          · 失败重试 ${Number(state.apiStatus?.retries) || 0} 次（指数退避 + 抖动）</span>
      </div>
      <div class="sb-set-label">视频档位（点「出视频」时用）</div>
      <div class="sb-set-row">
        <button class="sb-chip ${drama.videoTier === 'free' ? 'is-on' : ''}" data-tier="free" type="button">
          免费 ${tiers.free.resolution} · ${tiers.free.duration}s</button>
        <button class="sb-chip ${drama.videoTier === 'paid' ? 'is-on' : ''}" data-tier="paid" type="button"
          ${allowPaid ? '' : 'disabled'} title="${allowPaid ? '会真实计费，请确认后再点' : '服务端未开启计费档（AVM_ALLOW_PAID=1）'}">
          ${(tiers.paid || {}).resolution || '1080p'} · ${(tiers.paid || {}).duration || 10}s（计费）</button>
      </div>
      <div class="sb-set-label">成片转场</div>
      <div class="sb-set-row">
        ${[[0, '硬切'], [0.3, '淡化 0.3s'], [0.5, '淡化 0.5s']].map(([v2, label]) =>
          `<button class="sb-chip ${Number(o.transition) === v2 ? 'is-on' : ''}" data-tr="${v2}" type="button">${label}</button>`).join('')}
      </div>
      <div class="sb-set-label">成片内容</div>
      <div class="sb-set-row">
        <button class="sb-chip ${o.subtitles ? 'is-on' : ''}" data-flag="subtitles" type="button">逐镜字幕</button>
        <button class="sb-chip ${o.titles ? 'is-on' : ''}" data-flag="titles" type="button">片头片尾</button>
        <button class="sb-chip ${o.music === 'drone' ? 'is-on' : ''}" data-flag="music" type="button">合成底音</button>
      </div>
      <p class="pop-note">成片全在本地 ffmpeg 完成，不花上游额度；字幕取分镜台词，底音是本地合成的低频垫音，不是配乐。</p>`, { align: 'right' });
    Array.from(el.querySelectorAll('[data-reelmode]')).forEach((b) => {
      b.onclick = () => {
        if (b.disabled) return;
        drama.reelMode = b.dataset.reelmode === 'cloud' ? 'cloud' : 'local';
        saveBoard(); closePopover(); openReelSettings(anchor);
        toast(drama.reelMode === 'cloud' ? '成片改用云端 reel.1task.cn（会先上传本机镜头）' : '成片改回本地 ffmpeg');
      };
    });
    Array.from(el.querySelectorAll('[data-tier]')).forEach((b) => {
      b.onclick = () => {
        if (b.disabled) return;
        if (b.dataset.tier === 'paid' && !window.confirm('计费档会真的扣上游额度，确定切换到 1080p/10s？')) return;
        drama.videoTier = b.dataset.tier;
        saveBoard(); closePopover(); paintSbHead();
        toast(b.dataset.tier === 'paid' ? '已切到计费档（1080p/10s）' : '已切回免费档（720p/8s）');
      };
    });
    Array.from(el.querySelectorAll('[data-tr]')).forEach((b) => {
      b.onclick = () => {
        saveReelOpts({ transition: Number(b.dataset.tr) });
        closePopover(); openReelSettings(anchor);
      };
    });
    Array.from(el.querySelectorAll('[data-flag]')).forEach((b) => {
      b.onclick = () => {
        const k = b.dataset.flag;
        if (k === 'music') saveReelOpts({ music: drama.reelOpts.music === 'drone' ? 'off' : 'drone' });
        else saveReelOpts({ [k]: !drama.reelOpts[k] });
        closePopover(); openReelSettings(anchor);
      };
    });
    return el;
  }

  function renumber(list) {
    list.forEach((s, i) => { s.n = i + 1; s.id = `S${String(i + 1).padStart(2, '0')}`; });
  }

  function addShot() {
    const list = drama.shots;
    const n = list.length + 1;
    list.push({
      id: `S${String(n).padStart(2, '0')}`, ep: drama.ep, n,
      size: '中景', dur: 3, act: '新增', scene: '待定', line: '',
      desc: '（新加的一镜：写下画面，再生成）',
      status: 'idle', img: null, ms: 0, credits: 0, model: null, error: null,
      stale: false, pct: 0, vidStatus: 'idle', vidUrl: null, vidMs: 0,
      vidErr: null, vidId: null, vidStartedAt: 0,
    });
    saveBoard();
    renderStoryboard();
    toast(`已在第 ${drama.ep} 集末尾加一镜（共 ${list.length} 镜）`);
  }

  function moveShot(sh, d) {
    const list = drama.shots;
    const i = list.indexOf(sh);
    const j = i + d;
    if (i < 0 || j < 0 || j >= list.length) { toast('已经到头了'); return; }
    [list[i], list[j]] = [list[j], list[i]];
    renumber(list);
    closePopover();
    saveBoard();
    renderStoryboard();
    toast(`已${d < 0 ? '上移' : '下移'}一镜，成片顺序跟着变`);
  }

  function deleteShot(sh) {
    const list = drama.shots;
    if (list.length <= 1) { toast('至少保留一镜', 'error'); return; }
    const i = list.indexOf(sh);
    if (i < 0) return;
    list.splice(i, 1);
    renumber(list);
    closePopover();
    saveBoard();
    renderStoryboard();
    toast('已删除这一镜，成片需要重拼');
  }

  function reelSegments() {
    return drama.shots.filter((s) => s.vidUrl).map((s) => s.vidUrl);
  }

  function paintReel() {
    const box = sbEl('sbReelBox');
    if (!box) return;
    const r = drama.reels[drama.ep];
    if (!r || !r.url) { box.hidden = true; return; }
    box.hidden = false;
    const video = sbEl('sbReelVideo');
    if (video && video.getAttribute('src') !== r.url) video.src = r.url;
    const meta = sbEl('sbReelMeta');
    if (meta) {
      const via = r.via === 'cloud'
        ? `云端 ${esc((state.apiStatus?.reelCloud?.base || 'reel.1task.cn').replace('https://', ''))}`
        : '本地 ffmpeg 拼接';
      meta.textContent = `${r.segments} 段 · ${(r.seconds || 0).toFixed(1)}s · ${via}`;
    }
    const dl = sbEl('sbReelDl');
    if (dl) { dl.href = r.url; dl.download = r.url.split('/').pop(); }
  }

  async function genReel() {
    const segs = reelSegments();
    if (!segs.length) { toast('本集还没有镜头视频，先给至少一镜出视频'); return false; }
    const btn = sbEl('sbReelBtn');
    const old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '拼接中…'; }
    try {
      const res = await fetch('/api/reel', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          files: segs, ep: drama.ep,
          options: drama.reelOpts,
          title: drama.script?.title || '',
          cloud: drama.reelMode === 'cloud',
          lines: drama.shots.filter((s) => s.vidUrl).map((s) => s.line || s.desc.slice(0, 18)),
        }),
      });
      const d = await res.json().catch(() => ({ ok: false, error: '代理返回了非 JSON 内容' }));
      if (!res.ok || !d.ok) throw new Error(d.error || `HTTP ${res.status}`);
      drama.reels[drama.ep] = { url: d.url, segments: d.segments,
                                seconds: d.seconds, ms: d.elapsed_ms, via: d.via || 'local' };
      saveBoard();
      paintReel();
      if (btn) btn.textContent = old;
      paintSbHead();
      toast(`本集成片已拼好：${d.segments} 段 / ${(d.seconds || 0).toFixed(1)}s`
        + (d.via === 'cloud' ? ` · 云端 ${((d.cloud?.elapsed_ms || 0) / 1000).toFixed(1)}s` : ''));
      sbEl('sbReelBox')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      return true;
    } catch (err) {
      if (btn) btn.textContent = old;
      toast(String((err && err.message) || err).slice(0, 70), 'error');
      paintSbHead();
      return false;
    }
  }

  async function genAllShots() {
    if (drama.sbBusy) return;
    if (!state.statusReady) { toast('正在检测生成链路，请稍候…', true); return; }
    if (!state.apiStatus?.real) { toast('演示模式不出图：不会用占位图冒充 flux 结果', 'error'); return; }
    const todo = drama.shots.filter((s) => s.status !== 'done' || s.stale);
    if (!todo.length) { toast('本集分镜已全部出图'); return; }
    // 提交即返回：这里一口气把本集所有待出的镜头都交给服务端队列，
    // 并发由 worker 池决定（不再由浏览器一条条等着发）。
    drama.sbBusy = true; drama.sbStop = false;
    drama.sbTotal = todo.length; drama.sbPos = 0; drama.sbDone = 0;
    paintSbHead(); drama.shots.forEach(paintShot);
    const lanes = Math.max(1, Number(state.apiStatus?.workers) || 4);
    const runs = todo.map((s) => genShot(s.id, true).then((okOne) => {
      drama.sbDone += 1; paintSbHead(); return okOne;
    }));
    await Promise.all(runs);
    const ok = todo.filter((s) => s.status === 'done').length;
    drama.sbBusy = false;
    paintSbHead(); drama.shots.forEach(paintShot); saveBoard();
    toast(drama.sbStop ? `已停止：本集出图 ${ok}/${todo.length} 张`
      : `本集出图完成：${ok}/${todo.length} 张（服务端 ${lanes} 路并发）`);
  }

  function readDramaFile(file) {
    if (!file) return;
    if (!/\.txt$/i.test(file.name) && file.type !== 'text/plain') {
      toast('目前只读取 .txt 文本；图片/视频请用创作页的参考图', 'error');
      return;
    }
    if (file.size > 512 * 1024) { toast('文本超过 512KB，请先截断', 'error'); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const el = dramaEl('dramaPrompt');
      const text = String(reader.result || '').replace(/\r\n/g, '\n').trim();
      el.value = text.slice(0, 500);
      setDramaTab('write');
      updateDramaCount(); el.focus();
      toast(text.length > 500 ? '文本过长，已截取前 500 字' : `已读入《${file.name}》${text.length} 字`);
    };
    reader.onerror = () => toast('读取失败', 'error');
    reader.readAsText(file, 'utf-8');
  }

  function initDrama() {
    renderDramaLists();
    updateDramaCount();

    $$('.drama-tab').forEach((b) => b.onclick = () => setDramaTab(b.dataset.dtab));

    const ta = dramaEl('dramaPrompt');
    ta.addEventListener('input', updateDramaCount);
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && ta.value.trim()) {
        e.preventDefault(); dramaGenerate();
      }
    });

    dramaEl('dramaStyle').onclick = (e) => dramaPick(e.currentTarget, '风格', ['都市', '古装', '悬疑', '科幻', '家庭'], 'style',
      (v) => { dramaEl('dramaStyleVal').textContent = v; toast(`风格：${v}`); });
    dramaEl('dramaTopic').onclick = (e) => dramaPick(e.currentTarget, '选题', ['先婚后爱', '复仇', '逆袭', '穿越', '校园'], 'topic',
      (v) => { dramaEl('dramaTopicVal').textContent = v; toast(`选题：${v}`); });
    dramaEl('dramaRatio').onclick = (e) => dramaPick(e.currentTarget, '比例', ['9:16', '16:9', '1:1'], 'ratio',
      (v) => { dramaEl('dramaRatioVal').textContent = v; toast(`比例：${v}`); });
    dramaEl('dramaEps').onclick = (e) => {
      const opts = ['6 集', '10 集', '12 集', '20 集', '30 集'];
      simplePopover(e.currentTarget, '集数', opts, `${drama.eps} 集`, (v) => {
        const n = Number(String(v).replace(/\D/g, '')) || 10;
        drama.eps = n; dramaEl('dramaEpsVal').textContent = String(n); toast(`集数：${n}`);
      });
    };
    dramaEl('dramaModel').onclick = () => toast(llmConfigured()
      ? `文本模型：${llmModelName()}（${state.apiStatus?.llm?.base || ''}）· 点发送即真实调用`
      : '文本模型未配置：剧本走本地模板，分镜图仍真实生成');

    dramaEl('dramaAdd').onclick = () => dramaEl('dramaFile').click();
    dramaEl('dramaUploadBtn').onclick = () => dramaEl('dramaFile').click();
    dramaEl('dramaUploadBtn2').onclick = () => dramaEl('dramaFile').click();
    dramaEl('dramaFile').onchange = (e) => {
      const f = e.target.files && e.target.files[0];
      e.target.value = '';
      readDramaFile(f);
    };
    dramaEl('dramaAt').onclick = () => {
      const el = dramaEl('dramaPrompt');
      const at = el.selectionStart ?? el.value.length;
      el.value = el.value.slice(0, at) + '@' + el.value.slice(at);
      el.focus(); el.setSelectionRange(at + 1, at + 1);
      updateDramaCount();
    };
    dramaEl('dramaSend').onclick = dramaGenerate;

    const card = dramaEl('dramaCard');
    ['dragenter', 'dragover'].forEach((ev) => card.addEventListener(ev, (e) => {
      e.preventDefault(); dramaEl('dramaDropZone').classList.add('is-over');
    }));
    ['dragleave', 'drop'].forEach((ev) => card.addEventListener(ev, (e) => {
      e.preventDefault(); dramaEl('dramaDropZone').classList.remove('is-over');
    }));
    card.addEventListener('drop', (e) => {
      const f = e.dataTransfer?.files?.[0];
      if (f) readDramaFile(f);
    });

    dramaEl('dramaAll').onclick = () => toast('「我的短剧」为示例数据，不含真实工程');
  }

  function commitScript(sc) {
    drama.script = sc;
    drama.board = buildBoard(sc);
    drama.ep = 1;
    drama.shots = drama.board[1];
    drama.sbPos = 0; drama.sbTotal = 0;
    renderScript(sc);
    renderStoryboard();
    saveBoard();
  }

  function paintDramaBusy(on, label) {
    const btn = dramaEl('dramaSend');
    const tip = dramaEl('dramaLlm');
    if (btn) {
      btn.classList.toggle('is-busy', on);
      btn.disabled = on || !dramaEl('dramaPrompt').value.trim();
      btn.setAttribute('aria-busy', String(on));
    }
    if (tip) {
      tip.hidden = !on;
      if (on) tip.textContent = label;
    }
  }

  async function dramaGenerate() {
    const prompt = dramaEl('dramaPrompt').value.trim();
    if (!prompt) { dramaEl('dramaPrompt').focus(); return; }
    if (drama.sbBusy) { drama.sbStop = true; toast('已停止上一轮分镜出图'); }
    const eps = requestedEps(prompt);
    applyEps(eps);

    // 没配文本模型就走模板 —— 页面会标「剧本：本地模板」，不冒充模型输出
    if (!llmConfigured()) {
      commitScript(buildScript(prompt));
      toast(`文本模型未配置，已出剧本模板 + ${drama.script.eps} 集分镜（每集 ${drama.shots.length} 镜）`);
      return;
    }

    paintDramaBusy(true, '模型写作中… 0s');
    const t0 = Date.now();
    const tick = setInterval(() => {
      const tip = dramaEl('dramaLlm');
      if (tip) tip.textContent = `模型写作中… ${Math.round((Date.now() - t0) / 1000)}s`;
    }, 500);
    try {
      const r = await postJSON('/api/script', { prompt, episodes: eps });
      commitScript(modelToScript(r.data, r.meta, prompt));
      const secs = Math.round((r.meta?.elapsed_ms || 0) / 1000);
      toast(`${r.meta?.model || '模型'} 写完剧本 + ${drama.script.eps} 集分镜（${secs}s）`);
    } catch (err) {
      commitScript(buildScript(prompt));
      toast(`模型没写成（${String(err.message).slice(0, 48)}），已回退本地模板`, 'error');
    } finally {
      clearInterval(tick);
      paintDramaBusy(false, '');
    }
  }

  function setView(view) {
    const isDrama = view === 'drama';
    $('#app').classList.toggle('view-drama', isDrama);
    $('#dramaView').hidden = !isDrama;
    if (isDrama) {
      $('#crumb').textContent = '创作 / 短剧工作室';
      dramaEl('dramaPrompt')?.focus({ preventScroll: true });
    } else {
      $('#crumb').textContent = '创作 / 默认创作';
    }
    $$('.nav-item').forEach((b) => b.classList.toggle('is-active',
      b.dataset.nav === (isDrama ? '短剧工作室' : '创作')));
    requestAnimationFrame(renderModeGlider);
  }

  /* ---------- 启动 ---------- */
  async function init() {
    const qp = new URLSearchParams(location.search);
    applyQuickHint(qp);
    const saved = qp.get('theme') || localStorage.getItem('jimeng-theme');
    if (saved) state.theme = saved === 'light' ? 'light' : 'dark';
    const qMode = qp.get('mode');
    if (qMode && ['image', 'video', 'agent'].includes(qMode)) {
      state.mode = qMode;
      state.typeLabel = { image: '图片生成', video: '视频生成', agent: 'Agent 模式' }[qMode];
      state.model = MODELS[qMode][0];
      state.count = qMode === 'image' ? 4 : 1;
    }
    document.documentElement.dataset.theme = state.theme;
    $('#themeIcon').innerHTML = `<use href="#i-${state.theme === 'dark' ? 'sun' : 'moon'}"></use>`;
    seedSessions();
    restoreSessions();
    bind();
    initDrama();
    if (restoreBoard()) { renderScript(drama.script, false); renderStoryboard(); }
    if (qp.get('view') === 'drama') setView('drama');
    window.__qaStoryboard = () => ({
      live: !!state.apiStatus?.real,
      upstreamMode: state.apiStatus?.upstreamMode || 'none',
      badge: sbEl('sbBadge')?.textContent || '',
      badgeMode: sbEl('sbBadge')?.dataset.mode || '',
      busy: !!drama.sbBusy, total: drama.shots.length, ep: drama.ep,
      totalEp: drama.script?.eps || 0,
      done: doneOf(drama.shots),
      boardDone: Object.values(drama.board).reduce((a, l) => a + doneOf(l), 0),
      boardTotal: Object.values(drama.board).reduce((a, l) => a + l.length, 0),
      section: !!sbEl('sbSection'),
      note: sbEl('sbNote')?.textContent || '',
      progress: sbEl('sbProgress')?.textContent || '',
      restored: !!drama.script,
      shots: drama.shots.map((s) => ({ id: s.id, ep: s.ep, size: s.size, desc: s.desc,
        scene: s.scene, status: s.status, stale: !!s.stale, img: s.img, ms: s.ms,
        credits: s.credits, model: s.model, error: s.error,
        vidStatus: s.vidStatus || 'idle', vidUrl: s.vidUrl || null, vidMs: s.vidMs || 0,
        vidErr: s.vidErr || null, vidId: s.vidId || null })),
      videoPrompts: drama.shots.map((s) => (drama.script ? videoPrompt(s, drama.script) : '')),
      scriptSource: drama.script?.source || 'template',
      scriptModel: drama.script?.model || null,
      scriptTitle: drama.script?.title || '',
      epSource: epSource(drama.ep),
      epModel: drama.script?.shotsMeta?.[drama.ep]?.model || null,
      llm: state.apiStatus?.llm || null,
      reel: drama.reels[drama.ep] || null,
      reelOpts: { ...drama.reelOpts }, videoTier: drama.videoTier,
      reelSegs: reelSegments().length,
      concurrency: Number(state.apiStatus?.workers) || 0,
      reelMode: drama.reelMode,
      reelCloud: state.apiStatus?.reelCloud || null,
      sbDone: drama.sbDone,
      queued: drama.shots.filter((s) => s.status === 'queued').length,
      running: drama.shots.filter((s) => s.status === 'running').length,
      taskIds: drama.shots.map((s) => s.taskId || null),
      attempts: drama.shots.map((s) => s.attempts || 0),
      inflight: state.apiStatus?.inflight ?? 0,
      serverConcurrency: state.apiStatus?.concurrency ?? 0,
      video: state.apiStatus?.video || null,
      prompts: drama.shots.map((s) => (drama.script ? shotPrompt(s, drama.script) : '')),
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
    });
    window.__qaSbClear = () => { clearBoard(); return true; };
    window.__qaDrama = () => ({
      view: $('#app').classList.contains('view-drama') ? 'drama' : 'generate',
      tab: drama.tab, style: drama.style, topic: drama.topic, ratio: drama.ratio, eps: drama.eps,
      promptLen: (dramaEl('dramaPrompt') || {}).value?.length || 0,
      hasScript: !!drama.script, scriptEps: drama.script?.episodes?.length || 0,
      ideas: document.querySelectorAll('#dramaIdeas .idea').length,
      feats: document.querySelectorAll('#dramaFeats .feat').length,
      projects: document.querySelectorAll('#dramaProjects .proj[data-proj]').length,
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      vw: innerWidth,
    });
    $('#prompt').placeholder = placeholder();
    syncModeTabs(); renderInspiration(); renderToolbar(); renderSessions(); renderRefs();
    updateCost(); updateSend(); setHint('正在检测生成链路…');
    renderApiBadge();
    requestAnimationFrame(renderModeGlider);

    let statusUnknown = false;
    try {
      state.serverStatus = await fetchStatus();
      state.apiStatus = { ...state.serverStatus };
      syncImageModelFromConfig();
    } catch {
      // 代理不可达：如实标记为「检测失败」，不伪装成演示模式
      statusUnknown = true;
      state.serverStatus = { real: false, keyConfigured: false, maxCalls: 0, calls: 0, unknown: true };
      state.apiStatus = { ...state.serverStatus };
    }
    state.frontendDemo = false;
    state.statusReady = true;
    // 首屏先用空状态渲染过参数条：状态回来后必须重绘，否则视频档位停在演示默认值
    renderApiBadge(); paintSbHead(); renderToolbar(); updateCost(); updateSend();
    loadBilling();                       // 不阻塞首屏：先显示本地兜底值，后到即刷新
    if (statusUnknown) {
      setHint('无法连接本地代理：请在终端确认 server.py 正在运行，然后刷新页面', true);
      toast('链路检测失败，本次不会发起真实请求', 'error');
    } else {
      setHint(defaultHint());
    }

    // 刷新恢复：图片队列按 taskId 回查，视频会话按 vidId 继续轮询
    if (restoreJobs()) resumeJobs();
    resumeRunningVideos();

    // ---- 自检接口：仅改内存与只读汇总，不发起任何额外请求 ----
    const QA_ALLOWED = ['mode', 'typeLabel', 'model', 'ratio', 'resolution', 'detail',
                        'count', 'duration', 'videoRes', 'refMethod', 'credits'];
    function qaApply(f) {
      if (!f || typeof f !== 'object') return false;
      QA_ALLOWED.forEach((k) => { if (Object.prototype.hasOwnProperty.call(f, k)) state[k] = f[k]; });
      if (Array.isArray(f.refs)) {
        state.refs = f.refs.map((r, i) => ({ name: (r && r.name) || `参考图 ${i + 1}`, url: (r && r.url) || null }));
      }
      if (f.demo === true) {
        state.apiStatus = { ...(state.apiStatus || {}), real: false };
        state.frontendDemo = true;
      } else if (f.demo === false) {
        state.frontendDemo = false;
        state.apiStatus = { ...(state.serverStatus || {}) };
      }
      $('#prompt').placeholder = placeholder();
      syncModeTabs(); renderInspiration(); renderToolbar(); renderRefs();
      renderApiBadge(); paintSbHead(); updateCost(); updateSend(); setHint(defaultHint());
      requestAnimationFrame(renderModeGlider);
      if (window.__qaSync) window.__qaSync();
      return true;
    }
    window.__qaApply = qaApply;
    window.__qaChargedCredits = chargedCredits;
    // 扩展视图（探索/资产/工具/主体/动效）的最小桥接面：
    // 只暴露「填提示词 / 切模式 / 加参考图」三件事，外部不碰内部状态。
    window.JimengBridge = {
      setPrompt(prompt, mode) {
        if (typeof prompt !== 'string') return false;
        if (mode && ['image', 'video', 'agent'].includes(mode) && state.mode !== mode) {
          const tab = $(`.mode-tab[data-mode="${mode}"]`);
          if (tab) tab.click();
        }
        $('#prompt').value = prompt;
        state.prompt = prompt;
        updateSend(); setHint(defaultHint());
        return true;
      },
      setMode(mode) {
        if (!['image', 'video', 'agent'].includes(mode)) return false;
        const tab = $(`.mode-tab[data-mode="${mode}"]`);
        if (tab && state.mode !== mode) tab.click();
        return true;
      },
      async addRefFiles(files) {
        const list = Array.from(files || []);
        if (!list.length) return 0;
        return addRefs(list.map((f) => () => fileToRef(f)));
      },
    };
    window.__qaSnapshot = () => ({
      mode: state.mode, typeLabel: state.typeLabel, model: state.model,
      ratio: state.ratio, resolution: state.resolution,
      detail: state.detail, count: state.count, duration: state.duration, videoRes: state.videoRes,
      credits: state.credits, creditSource: state.creditSource,
      models: configuredModels(), model: state.model,
      billingSource: state.billing?.source || null, billingHeld: state.billing?.held ?? null,
      refs: state.refs.length, theme: state.theme,
      apiReal: !!state.apiStatus?.real, upstreamMode: state.apiStatus?.upstreamMode || 'none',
      sessions: state.sessions.length, busy: state.busy, statusReady: state.statusReady,
      jobs: runningJobs().length, jobsTotal: state.jobs.size, jobCap: jobCap(), queueView: state.queueView,
      realVideo: isRealVideo(), videoTier: state.videoTier,
      vidConfigured: !!state.apiStatus?.video?.configured,
      vidAllowPaid: !!state.apiStatus?.video?.allowPaid,
      activeStatus: (state.sessions.find((x) => x.id === state.activeSession) || {}).status || null,
      vw: innerWidth, vh: innerHeight, dpr: devicePixelRatio,
    });
    window.__qaFacts = () => {
      const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : '');
      const list = (sel) => Array.from(document.querySelectorAll(sel)).map(txt);
      const imgs = Array.from(document.querySelectorAll('.result-card img'));
      return {
        snapshot: window.__qaSnapshot(),
        sourceTags: list('.source-tag'),
        alerts: list('.alert-bar'),
        bubbleTags: list('.bubble-tag'),
        convTips: list('.conv-tip'),
        hint: txt($('#hintText')),
        apiBadge: txt($('.api-badge')),
        convDesc: txt($('.conv-tip')) || txt($('.conv-head p')),
        resultCards: document.querySelectorAll('.result-card').length,
        videos: Array.from(document.querySelectorAll('.result-card video')).map((v) => ({
          src: v.getAttribute('src') || '', rs: v.readyState, err: v.error ? v.error.code : null })),
        running: !!document.getElementById('convVidClock'),
        assetVideos: document.querySelectorAll('.asset-video').length,
        sessionItems: document.querySelectorAll('.session-item').length,
        sendDisabled: !!($('#sendBtn') && $('#sendBtn').disabled),
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        docW: document.documentElement.scrollWidth,
        lightboxDisplay: $('#lightbox') ? getComputedStyle($('#lightbox')).display : 'missing',
        imgOk: imgs.map((im) => im.complete && im.naturalWidth > 0),
        imgSrc: imgs.map((im) => im.getAttribute('src')),
      };
    };
    window.__qaSync = () => { document.body.dataset.qa = JSON.stringify(window.__qaSnapshot()); };
    window.__qaSync();
    const formQ = qp.get('form');
    if (formQ) {
      try { qaApply(JSON.parse(formQ)); } catch { setHint('form 参数非法，已忽略', true); }
    }
    if (location.protocol === 'file:') {
      setHint('当前以 file:// 打开：真实生成不可用，请访问 http://127.0.0.1:8788');
      toast('file:// 下为演示模式；用本地服务地址可启用真实生成');
    }
    // 可复现自检钩子：仅影响本页状态，不发起任何额外请求。
    const qa = qp.get('qa');
    if (qa === 'lowcredit') {
      state.credits = 10; updateCost();
      $('#prompt').value = '赛博城市夜景，霓虹雨巷，电影感';
      state.prompt = $('#prompt').value; updateSend();
      setHint('积分不足：本次需要 ' + estimateCredits() + '，余额 10。请调低生成数量或分辨率', true);
    } else if (qa === 'drawer') {
      toggleDrawer(true);
    } else if (qa === 'result') {
      openSession(state.sessions[0].id);
    } else if (qa === 'popover') {
      setTimeout(() => $('#pSize')?.click(), 60);
    } else if (qa === 'realresult') {
      fetch('/api/logs').then((r) => r.json()).then((d) => {
        const last = [...(d.entries || [])].reverse().find((e) => e.status === 'success' && e.upstream_mode !== 'mock');
        if (!last) return;
        const s = { id: 'qa-real', title: '真实生成 · flux-3-image', mode: 'image', model: 'flux-3-image',
          prompt: 'A single red ceramic cube on a cream linen table, soft studio side light, minimalist product photography, no text',
          params: ['图片生成', 'flux-3-image', '1024×1024 · 1K', '1 张'],
          images: [{ url: '/assets/generated/' + last.file, source: 'real' }],
          source: 'real', elapsed: (last.elapsed_ms / 1000).toFixed(1) + 's', at: Date.now() };
        state.sessions.unshift(s);
        openSession('qa-real');
      }).catch(() => {});
    } else if (qa === 'metrics') {
      const rect = (sel) => { const e = $(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.width), Math.round(r.right)]; };
      const m = { vw: innerWidth, docW: document.documentElement.scrollWidth,
        welcome: rect('#welcome'), modeSwitch: rect('#modeSwitch'), insp: rect('#inspiration'),
        composer: rect('#composer'), bar: rect('.composer-toolbar'), left: rect('#toolbarLeft'),
        right: rect('.toolbar-right'), send: rect('#sendBtn'), stage: rect('#stage') };
      document.title = 'METRICS:' + JSON.stringify(m);
    } else if (qa === 'error') {
      state.credits = 10; updateCost();
      $('#prompt').value = '一只在森林中奔跑的小鹿';
      state.prompt = $('#prompt').value; updateSend();
      $('#conversation').hidden = false; $('#welcome').hidden = true;
      $('#conversation').innerHTML = '<div class="alert-bar" style="max-width:760px;margin:40px auto">' +
        ico('warn') + '<span>上游返回 HTTP 502：The image service is busy。本次未计费，可稍后重试。</span></div>';
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
