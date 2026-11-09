/* ARTDUO — interactions: filter, lightbox, theme, reveal, cursor. */

const works = [
  {
    no: '01', title: 'Soft Machine', cn: '柔软机械 01', artist: 'LIN', key: 'lin',
    year: '2026', medium: '生成式 SVG / 数字微喷', size: '900 × 1125 px',
    img: '/works/w1.svg',
    alt: '深色背景上密集的米色同心椭圆，右侧一道红色竖带',
    note: '一组被反复拉伸的圆。它们不指向任何物体，只记录力如何穿过一个平面。'
  },
  {
    no: '02', title: 'Afterimage', cn: '残像 02', artist: 'QIAO', key: 'qiao',
    year: '2026', medium: '生成式 SVG / 数字微喷', size: '900 × 1125 px',
    img: '/works/w2.svg',
    alt: '米色背景上模糊的红色与粉色光斑，覆盖细密的黑色横线',
    note: '看过之后留下的颜色。图像退潮，记忆仍在原来的位置发热。'
  },
  {
    no: '03', title: 'Paper Memory', cn: '纸的记忆 03', artist: 'DUO', key: 'duo',
    year: '2026', medium: '生成式 SVG / 数字微喷', size: '900 × 1125 px',
    img: '/works/w3.svg',
    alt: '深色背景上叠放的米色、粉色与红色撕裂纸片',
    note: '一张纸被折叠、撕开、再摊平。两个人各自保留了一半的折痕。'
  },
  {
    no: '04', title: 'Signal Garden', cn: '信号花园 04', artist: 'QIAO', key: 'qiao',
    year: '2026', medium: '生成式 SVG / 数字微喷', size: '900 × 1125 px',
    img: '/works/w4.svg',
    alt: '米色背景上密集排列的黑色与红色短竖线，像一片信号草地',
    note: '把噪声种下去，长出一片可以被读懂的草地。'
  },
  {
    no: '05', title: 'Duet', cn: '双人 05', artist: 'DUO', key: 'duo',
    year: '2026', medium: '生成式 SVG / 尺寸可变', size: '900 × 1125 px',
    img: '/works/w5.svg',
    alt: '深色网格上红色与米色两个巨大的交叠圆形',
    note: '两个圆不必变成同一个圆。重叠的部分，就是这场展览。'
  },
  {
    no: '06', title: 'Red Fold', cn: '红色折叠 06', artist: 'LIN', key: 'lin',
    year: '2026', medium: '生成式 SVG / 数字微喷', size: '900 × 1125 px',
    img: '/works/w6.svg',
    alt: '米色背景上红色、粉色与黑色的大块折纸几何切面',
    note: '折叠不是隐藏，而是让一个平面获得第二个方向。'
  }
];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const grid = $('#workGrid');
const empty = $('#workEmpty');
const dialog = $('#lightbox');
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let activeFilter = 'all';
let visible = [...works];
let current = 0;
let lastFocus = null;

/* ---------- render ---------- */
function card(work, index) {
  const article = document.createElement('article');
  article.className = 'work';
  article.dataset.artist = work.key;
  article.dataset.index = String(index);
  article.tabIndex = 0;
  article.setAttribute('role', 'button');
  article.setAttribute('aria-label', `查看作品：${work.title}，${work.artist}`);
  article.innerHTML = `
    <div class="work-media">
      <span class="work-no">${work.no}</span>
      <img src="${work.img}" alt="${work.alt}" width="900" height="1125" loading="${index < 2 ? 'eager' : 'lazy'}">
      <span class="work-tag">${work.artist}</span>
    </div>
    <div class="work-info">
      <div>
        <h3>${work.title}</h3>
        <p>${work.cn} · ${work.year}</p>
      </div>
      <span>${work.artist}</span>
    </div>`;
  article.addEventListener('click', () => openWork(work));
  article.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openWork(work);
    }
  });
  return article;
}

works.forEach((work, index) => grid.appendChild(card(work, index)));

function applyFilter(key) {
  activeFilter = key;
  visible = key === 'all' ? [...works] : works.filter((work) => work.key === key);
  $$('.work', grid).forEach((node) => {
    const work = works[Number(node.dataset.index)];
    node.classList.toggle('is-hidden', !visible.includes(work));
  });
  visible.forEach((work, index) => {
    const node = $$('.work', grid)[works.indexOf(work)];
    if (!node) return;
    node.style.setProperty('--stagger', innerWidth > 760 && index % 2 ? '76px' : '0px');
  });
  empty.hidden = visible.length > 0;
  $$('.filter').forEach((button) => {
    const on = button.dataset.filter === key;
    button.classList.toggle('is-active', on);
    button.setAttribute('aria-pressed', String(on));
  });
}

$$('.filter').forEach((button) => {
  button.addEventListener('click', () => applyFilter(button.dataset.filter));
});
applyFilter('all');

/* ---------- lightbox ---------- */
function showWork(work) {
  $('#lbImage').src = work.img;
  $('#lbImage').alt = work.alt;
  $('#lbTitle').textContent = work.title;
  $('#lbCn').textContent = `${work.cn} · ${work.year}`;
  $('#lbArtist').textContent = work.artist;
  $('#lbYear').textContent = work.year;
  $('#lbMedium').textContent = work.medium;
  $('#lbSize').textContent = work.size;
  $('#lbNote').textContent = work.note;
  $('#lbIndex').textContent = `${work.no} / ${String(visible.length).padStart(2, '0')}`;
}

function openWork(work) {
  current = Math.max(0, visible.indexOf(work));
  lastFocus = document.activeElement;
  showWork(work);
  document.body.classList.add('is-locked');
  dialog.showModal();
  $('#lbClose').focus({ preventScroll: true });
}

function step(delta) {
  if (!visible.length) return;
  current = (current + delta + visible.length) % visible.length;
  showWork(visible[current]);
}

function closeDialog() {
  dialog.close();
  document.body.classList.remove('is-locked');
  if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus({ preventScroll: true });
}

$('#lbClose').addEventListener('click', closeDialog);
$('#lbPrev').addEventListener('click', () => step(-1));
$('#lbNext').addEventListener('click', () => step(1));
dialog.addEventListener('click', (event) => {
  if (event.target === dialog) closeDialog();
});
dialog.addEventListener('close', () => document.body.classList.remove('is-locked'));
document.addEventListener('keydown', (event) => {
  if (!dialog.open) return;
  if (event.key === 'Escape') { event.preventDefault(); closeDialog(); }
  if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
  if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
});

/* ---------- theme ---------- */
function syncThemeButton() {
  const light = document.documentElement.dataset.theme === 'light';
  $('#themeBtn').textContent = light ? 'DARK' : 'LIGHT';
  $('#themeBtn').setAttribute('aria-label', light ? '切换到暗色主题' : '切换到亮色主题');
  document.querySelector('meta[name="theme-color"]').content = light ? '#efe7da' : '#0b0b0c';
}
syncThemeButton();
$('#themeBtn').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('artduo-theme', next);
  syncThemeButton();
});

/* ---------- reveal / header / cursor ---------- */
if (!reduceMotion && 'IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-in');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
  $$('[data-reveal]').forEach((node) => observer.observe(node));
} else {
  $$('[data-reveal]').forEach((node) => node.classList.add('is-in'));
}

const head = $('#siteHead');
const onScroll = () => head.classList.toggle('is-scrolled', scrollY > 24);
document.addEventListener('scroll', onScroll, { passive: true });
onScroll();

if (matchMedia('(pointer: fine)').matches && !reduceMotion) {
  const cursor = $('.cursor');
  let x = innerWidth / 2, y = innerHeight / 2, tx = x, ty = y;
  addEventListener('pointermove', (event) => {
    tx = event.clientX; ty = event.clientY;
    cursor.classList.add('is-on');
  });
  addEventListener('pointerover', (event) => {
    cursor.classList.toggle('is-hover', !!event.target.closest('a, button, .work, .filter'));
  });
  const loop = () => {
    x += (tx - x) * 0.18; y += (ty - y) * 0.18;
    cursor.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
    requestAnimationFrame(loop);
  };
  loop();
}

/* keep stagger correct after resize */
addEventListener('resize', () => {
  visible.forEach((work, index) => {
    const node = $$('.work', grid)[works.indexOf(work)];
    if (node) node.style.setProperty('--stagger', innerWidth > 760 && index % 2 ? '76px' : '0px');
  });
}, { passive: true });
