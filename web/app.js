/* =========================================================
   LumenSubs — application
   ========================================================= */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
// yield to the event loop without timer throttling (background tabs clamp setTimeout to 1s)
const yieldUI = () => new Promise(r => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
const ICON = {
  play: '<svg class="svg i-play" viewBox="0 0 24 24"><polygon points="6 3 20 12 6 21"/></svg>',
  pause: '<svg class="svg" viewBox="0 0 24 24"><rect x="5" y="4" width="4.5" height="16" rx="1.2"/><rect x="14.5" y="4" width="4.5" height="16" rx="1.2"/></svg>',
  chev: '<svg class="svg" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  check: '<svg class="svg" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
  search: '<svg class="svg" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
  refresh: '<svg class="svg" viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>',
  split: '<svg class="svg" viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/></svg>',
  merge: '<svg class="svg" viewBox="0 0 24 24"><path d="m8 6 4-4 4 4"/><path d="M12 2v10.3a4 4 0 0 1-1.2 2.9L4 22"/><path d="m20 22-5-5"/></svg>',
  plus: '<svg class="svg" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  trash: '<svg class="svg" viewBox="0 0 24 24"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  ok: '<svg class="svg" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
  info: '<svg class="svg" viewBox="0 0 24 24"><path d="M12 16v-4M12 8h.01"/><circle cx="12" cy="12" r="10"/></svg>',
  warn: '<svg class="svg" viewBox="0 0 24 24"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
  spark: '<svg class="svg" viewBox="0 0 24 24"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/></svg>',
  folder: '<svg class="svg" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  film: '<svg class="svg" viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18M17 3v18M3 12h18"/></svg>',
  music: '<svg class="svg" viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
};

/* ---------------- API ---------------- */
async function api(method, url, body) {
  const r = await fetch(url, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  let d = null; try { d = await r.json(); } catch (e) { }
  if (!r.ok) throw new Error((d && (d.detail || d.error)) || `HTTP ${r.status}`);
  return d;
}
function uploadXHR(url, form, onProgress) {
  return new Promise((res, rej) => {
    const x = new XMLHttpRequest(); x.open('POST', url);
    x.upload.onprogress = e => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
    x.onload = () => { let d = null; try { d = JSON.parse(x.responseText); } catch (e) { } x.status < 300 ? res(d) : rej(new Error((d && d.detail) || `HTTP ${x.status}`)); };
    x.onerror = () => rej(new Error('上傳失敗'));
    x.send(form);
  });
}
async function pollJob(id, onProgress) {
  for (; ;) {
    const j = await api('GET', `/api/jobs/${id}`);
    onProgress && onProgress(j.progress);
    if (j.status === 'done') return j.result;
    if (j.status === 'error') throw new Error(j.error || '處理失敗');
    if (j.status === 'cancelled') { const e = new Error('已取消'); e.cancelled = true; throw e; }
    await sleep(500);
  }
}

/* ---------------- languages ---------------- */
const LANGS = [
  { v: 'auto', l: '自動偵測', n: 'Auto detect', c: 'AI' },
  { v: 'zh-TW', l: '繁體中文', n: 'Traditional Chinese', c: '繁' },
  { v: 'zh-CN', l: '簡體中文', n: 'Simplified Chinese', c: '简' },
  { v: 'yue', l: '粵語', n: 'Cantonese', c: '粵' },
  { v: 'en', l: '英文', n: 'English', c: 'EN' },
  { v: 'ja', l: '日文', n: '日本語', c: 'JA' },
  { v: 'ko', l: '韓文', n: '한국어', c: 'KO' },
  { v: 'es', l: '西班牙文', n: 'Español', c: 'ES' },
  { v: 'fr', l: '法文', n: 'Français', c: 'FR' },
  { v: 'de', l: '德文', n: 'Deutsch', c: 'DE' },
  { v: 'it', l: '義大利文', n: 'Italiano', c: 'IT' },
  { v: 'pt', l: '葡萄牙文', n: 'Português', c: 'PT' },
  { v: 'ru', l: '俄文', n: 'Русский', c: 'RU' },
  { v: 'ar', l: '阿拉伯文', n: 'العربية', c: 'AR' },
  { v: 'hi', l: '印地文', n: 'हिन्दी', c: 'HI' },
  { v: 'th', l: '泰文', n: 'ไทย', c: 'TH' },
  { v: 'vi', l: '越南文', n: 'Tiếng Việt', c: 'VI' },
  { v: 'id', l: '印尼文', n: 'Bahasa Indonesia', c: 'ID' },
  { v: 'ms', l: '馬來文', n: 'Bahasa Melayu', c: 'MS' },
  { v: 'fil', l: '菲律賓文', n: 'Filipino', c: 'FIL' },
  { v: 'tr', l: '土耳其文', n: 'Türkçe', c: 'TR' },
  { v: 'nl', l: '荷蘭文', n: 'Nederlands', c: 'NL' },
  { v: 'pl', l: '波蘭文', n: 'Polski', c: 'PL' },
  { v: 'sv', l: '瑞典文', n: 'Svenska', c: 'SV' },
  { v: 'da', l: '丹麥文', n: 'Dansk', c: 'DA' },
  { v: 'fi', l: '芬蘭文', n: 'Suomi', c: 'FI' },
  { v: 'cs', l: '捷克文', n: 'Čeština', c: 'CS' },
  { v: 'el', l: '希臘文', n: 'Ελληνικά', c: 'EL' },
  { v: 'hu', l: '匈牙利文', n: 'Magyar', c: 'HU' },
  { v: 'ro', l: '羅馬尼亞文', n: 'Română', c: 'RO' },
  { v: 'fa', l: '波斯文', n: 'فارسی', c: 'FA' },
  { v: 'mk', l: '馬其頓文', n: 'Македонски', c: 'MK' },
  { v: 'uk', l: '烏克蘭文', n: 'Українська', c: 'UK', tgtOnly: true },
  { v: 'he', l: '希伯來文', n: 'עברית', c: 'HE', tgtOnly: true },
];
const langOf = v => LANGS.find(x => x.v === v) || { v, l: v || '未知', n: '', c: '?' };

/* ---------------- state ---------------- */
let uid = 1;
const state = {
  mode: 'video', duration: 0, t: 0, playing: false, rate: 1,
  segs: [], selId: null, activeId: null,
  srcLang: 'auto', detected: '', tgtLang: 'zh-TW', tone: 'natural',
  opts: { keepNames: true, context: true, proofread: true, viz: true },
  zoom: 64, follow: true, query: '', audioAR: 16 / 9, arKey: '16:9', videoAR: 16 / 9,
};
const DEFAULT_STYLE = {
  display: 'dual', order: 'tgt-top', posX: 50, posY: 92, gap: 8, maxW: 86,
  bg: 'shadow', boxColor: '#0b1a2e', boxOpacity: 0.55, radius: 10, balance: true,
  tgt: { font: 'Noto Sans TC', size: 56, weight: 700, color: '#ffffff', stroke: 3, strokeColor: '#0b2545' },
  src: { font: 'Manrope', size: 36, weight: 600, color: '#cfeaff', stroke: 2, strokeColor: '#0b2545' },
};
let style = JSON.parse(JSON.stringify(DEFAULT_STYLE));
const project = { id: null, name: '未命名專案', media: {} };
let editTrack = 'tgt', styleVer = 0, busy = null;

/* ---------------- time helpers ---------------- */
const pad = (n, w = 2) => String(n).padStart(w, '0');
function fmt(t) { t = Math.max(0, t || 0); const ms = Math.round(t * 1000); const m = Math.floor(ms / 60000), s = Math.floor(ms / 1000) % 60; return `${pad(m)}:${pad(s)}.${pad(ms % 1000, 3)}`; }
function fmtShort(t) { t = Math.max(0, t || 0); const h = Math.floor(t / 3600); return (h ? h + ':' : '') + `${pad(Math.floor(t % 3600 / 60))}:${pad(Math.floor(t % 60))}`; }
function fmtFull(t, sep = ',') { const ms = Math.round(Math.max(0, t) * 1000); const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60; return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms % 1000, 3)}`; }
function parseTC(str) {
  str = String(str).trim().replace(',', '.'); if (!str) return NaN;
  const p = str.split(':').map(Number); if (p.some(isNaN)) return NaN;
  return p.reduce((a, v) => a * 60 + v, 0);
}
const sizeStr = b => b > 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(1) + ' MB';

/* ---------------- history ---------------- */
const hist = { u: [], r: [] };
function snap() { hist.u.push(JSON.stringify(state.segs)); if (hist.u.length > 150) hist.u.shift(); hist.r = []; updHist(); }
function undo() { if (!hist.u.length) return; hist.r.push(JSON.stringify(state.segs)); state.segs = JSON.parse(hist.u.pop()); segsChanged(); toast('已復原'); }
function redo() { if (!hist.r.length) return; hist.u.push(JSON.stringify(state.segs)); state.segs = JSON.parse(hist.r.pop()); segsChanged(); toast('已重做'); }
function updHist() { $('#undoBtn').disabled = !hist.u.length; $('#redoBtn').disabled = !hist.r.length; }

/* ---------------- toast ---------------- */
function toast(msg, ic = 'ok', ms = 2600) {
  const el = document.createElement('div'); el.className = 'toast' + (ic === 'warn' ? ' warn' : '');
  el.innerHTML = `<span class="ti">${ICON[ic] || ICON.ok}</span><span>${esc(msg)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, ms);
}

/* ---------------- segmented controls ---------------- */
function placeSeg(seg) {
  const on = seg.querySelector('button.on'); if (!on || !seg.offsetWidth) return;
  seg.style.setProperty('--x', on.offsetLeft + 'px'); seg.style.setProperty('--w', on.offsetWidth + 'px');
}
function initSeg(sel, onChange) {
  const seg = typeof sel === 'string' ? $(sel) : sel;
  seg.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b || b.classList.contains('on') || b.disabled) return;
    setSeg(seg, b.dataset.v); onChange && onChange(b.dataset.v);
  });
  placeSeg(seg);
  return seg;
}
function setSeg(seg, v) { if (typeof seg === 'string') seg = $(seg); $$('button', seg).forEach(x => x.classList.toggle('on', x.dataset.v === String(v))); placeSeg(seg); }
const segVal = sel => { const b = $(sel + ' button.on'); return b ? b.dataset.v : null; };
const refreshSegs = () => $$('.seg').forEach(placeSeg);

/* ---------------- custom select ---------------- */
const pop = $('#pop'); let popAnchor = null;
function closePop() { pop.classList.remove('show'); pop.onclick = null; popAnchor && popAnchor.classList.remove('open'); popAnchor = null; }
function openPop(anchor, opts, cur, pick, searchable = true) {
  if (popAnchor === anchor) { closePop(); return; }
  closePop(); popAnchor = anchor; anchor.classList.add('open');
  pop.innerHTML = (searchable ? `<div class="pop-search">${ICON.search}<input placeholder="搜尋…"></div>` : '') + '<div class="pop-list scroll"></div>';
  const list = $('.pop-list', pop), inp = $('input', pop);
  let items = [], kb = 0;
  const draw = q => {
    q = (q || '').toLowerCase();
    items = opts.filter(o => !q || (o.l + o.n + o.v).toLowerCase().includes(q));
    kb = Math.max(0, items.findIndex(o => o.v === cur)); if (q) kb = 0;
    list.innerHTML = items.map((o, i) => `<div class="pop-item ${o.v === cur ? 'on' : ''} ${i === kb && q ? 'kb' : ''}" data-v="${o.v}"><span style="${o.font ? `font-family:'${o.font}'` : ''}">${esc(o.l)}</span><small>${esc(o.n)}</small>${o.v === cur ? ICON.check : ''}</div>`).join('') || '<div class="pop-empty">找不到符合的項目</div>';
  };
  draw('');
  const r = anchor.getBoundingClientRect();
  pop.style.width = Math.max(r.width, 220) + 'px'; pop.style.left = Math.min(r.left, innerWidth - Math.max(r.width, 220) - 8) + 'px';
  const h = Math.min(340, (searchable ? 48 : 0) + opts.length * 36 + 12);
  pop.style.top = (r.bottom + h + 12 > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6) + 'px';
  requestAnimationFrame(() => { if (popAnchor === anchor) pop.classList.add('show'); });
  const on = $('.pop-item.on', list); on && on.scrollIntoView({ block: 'center' });
  if (inp) {
    inp.focus();
    inp.oninput = () => draw(inp.value);
    inp.onkeydown = e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); kb = clamp(kb + (e.key === 'ArrowDown' ? 1 : -1), 0, items.length - 1);
        $$('.pop-item', list).forEach((x, i) => x.classList.toggle('kb', i === kb)); $$('.pop-item', list)[kb]?.scrollIntoView({ block: 'nearest' });
      }
      if (e.key === 'Enter' && items[kb]) { pick(items[kb].v); closePop(); }
      if (e.key === 'Escape') closePop();
    };
  }
  list.onclick = e => { const it = e.target.closest('.pop-item'); if (it) { pick(it.dataset.v); closePop(); } };
}
document.addEventListener('pointerdown', e => { if (popAnchor && !pop.contains(e.target) && !popAnchor.contains(e.target)) closePop(); });
window.addEventListener('resize', closePop);
function makeSelect(btn, opts, value, onChange, { searchable = true, flag = true } = {}) {
  let cur = value;
  const render = () => { const o = opts.find(x => x.v === cur) || opts[0]; btn.innerHTML = (flag ? `<span class="flag">${o.c || ''}</span>` : '') + `<span class="sel-main" style="${o.font ? `font-family:'${o.font}'` : ''}">${esc(o.l)}</span><span class="sel-sub">${esc(o.n)}</span>${ICON.chev}`; };
  render();
  btn.addEventListener('click', () => openPop(btn, opts, cur, v => { cur = v; render(); onChange(v); }, searchable));
  return { set(v) { cur = v; render(); }, get: () => cur };
}

/* ---------------- DOM refs ---------------- */
const stage = $('#stage'), stageWrap = $('#stageWrap'), subsEl = $('#subs'), video = $('#video'), audio = $('#audio');
const subCv = $('#subCv'), bgCv = $('#bgCv');
const tlScroll = $('#tlScroll'), tlInner = $('#tlInner'), track = $('#track'), ruler = $('#ruler'), playhead = $('#playhead'), played = $('#played');
const rulerCv = $('#rulerCv'), waveCv = $('#waveCv');
const cueList = $('#cueList');

/* ---------------- stage layout ---------------- */
function currentAR() { return state.mode === 'audio' ? state.audioAR : state.videoAR; }
let stageW = 0, stageH = 0;
function layoutStage() {
  const full = document.fullscreenElement === stage;
  const W = full ? innerWidth : stageWrap.clientWidth, H = full ? innerHeight : stageWrap.clientHeight, ar = currentAR();
  let w = W, h = W / ar; if (h > H) { h = H; w = H * ar; }
  w = Math.max(10, Math.floor(w)); h = Math.max(10, Math.floor(h));
  if (full) { stage.style.width = '100vw'; stage.style.height = '100vh'; } else { stage.style.width = w + 'px'; stage.style.height = h + 'px'; }
  stageW = w; stageH = h;
  const dpr = devicePixelRatio || 1;
  [subCv, bgCv].forEach(c => { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); });
  drawBg(); renderSubs(true);
}
new ResizeObserver(() => { layoutStage(); refreshSegs(); }).observe(stageWrap);

/* ---------------- media / clock ---------------- */
function activeMedia() { return state.mode === 'video' ? (video.src ? video : null) : (audio.src ? audio : null); }
function setPlaying(p) {
  state.playing = p; stage.classList.toggle('paused', !p);
  $('#playBtn').innerHTML = p ? ICON.pause : ICON.play;
}
function play() {
  const m = activeMedia();
  if (!m && !state.segs.length) { toast(state.mode === 'video' ? '請先上傳影片' : '請先上傳音訊', 'info'); return; }
  if (state.t >= state.duration - 0.02) seek(0);
  if (m) m.play().catch(() => setPlaying(false));
  setPlaying(true);
}
function pause() { const m = activeMedia(); m && m.pause(); setPlaying(false); }
const toggle = () => state.playing ? pause() : play();
function seek(t) {
  state.t = clamp(t, 0, state.duration || 0);
  const m = activeMedia(); if (m) m.currentTime = state.t;
  if (!tlDrag) {
    const x = state.t * state.zoom, vw = tlScroll.clientWidth;
    if (x < tlScroll.scrollLeft || x > tlScroll.scrollLeft + vw - 40) tlScroll.scrollLeft = x - vw * .3;
  }
  frame(true);
}
[video, audio].forEach(m => { m.addEventListener('ended', () => setPlaying(false)); m.addEventListener('pause', () => { if (m === activeMedia() && state.playing && !m.seeking) setPlaying(false); }); });

let last = performance.now();
function loop(now) {
  const dt = (now - last) / 1000; last = now;
  const m = activeMedia();
  if (m) { if (!m.seeking) state.t = m.currentTime; }
  else if (state.playing) { state.t += dt * state.rate; if (state.t >= state.duration) { state.t = state.duration; setPlaying(false); } }
  frame();
  requestAnimationFrame(loop);
}
let lastTC = '', lastScrollX = -1;
function findSeg(t) {
  // binary search over sorted segs
  const a = state.segs; let lo = 0, hi = a.length - 1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (a[m].end <= t) lo = m + 1; else if (a[m].start > t) hi = m - 1; else return a[m]; }
  return null;
}
function frame(force) {
  const t = state.t;
  const tc = fmt(t); if (tc !== lastTC || force) { $('#tcNow').innerHTML = `${tc} <span>/ ${fmt(state.duration)}</span>`; lastTC = tc; }
  const x = t * state.zoom; playhead.style.transform = `translateX(${x}px)`; played.style.width = x + 'px';
  if (state.playing && !tlDrag) {
    const vw = tlScroll.clientWidth; if (x > tlScroll.scrollLeft + vw * .85 || x < tlScroll.scrollLeft) tlScroll.scrollLeft = x - vw * .2;
  }
  if (tlScroll.scrollLeft !== lastScrollX) { lastScrollX = tlScroll.scrollLeft; drawTimelineCanvases(); }
  if (state.mode === 'audio') vizFrame(t);
  const seg = findSeg(t);
  const id = seg ? seg.id : null;
  if (id !== state.activeId || force) {
    state.activeId = id;
    $$('.cue.active').forEach(e => e.classList.remove('active'));
    $$('.cue-blk.active').forEach(e => e.classList.remove('active'));
    if (id) {
      const c = cueList.querySelector(`.cue[data-id="${id}"]`); c && c.classList.add('active');
      const b = track.querySelector(`.cue-blk[data-id="${id}"]`); b && b.classList.add('active');
      if (state.follow && c && !cueList.contains(document.activeElement) && state.playing) {
        const lr = cueList.getBoundingClientRect(), cr = c.getBoundingClientRect();
        if (cr.top < lr.top || cr.bottom > lr.bottom) cueList.scrollTo({ top: c.offsetTop - cueList.clientHeight / 3, behavior: 'smooth' });
      }
    }
    renderSubs();
  }
}

/* ---------------- audio scene: background + visualiser ---------------- */
const bgImg = new Image(); bgImg.crossOrigin = 'anonymous';
bgImg.onload = () => { drawBg(); };
function drawBg() {
  if (state.mode !== 'audio' || !bgCv.width) return;
  const g = bgCv.getContext('2d');
  SubRender.drawBackground(g, bgCv.width, bgCv.height, bgImg.complete && bgImg.naturalWidth && project.media.image ? bgImg : null);
}
const vizBars = [];
(function () { const v = $('#viz'); for (let i = 0; i < 56; i++) { const b = document.createElement('i'); v.appendChild(b); vizBars.push(b); } })();
let vizData = null;
const VIZ_FPS = 30, VIZ_BANDS = 28;
async function loadViz(url) {
  vizData = null;
  if (!url) return;
  try { const r = await fetch(url); vizData = new Uint8Array(await r.arrayBuffer()); } catch (e) { vizData = null; }
}
let lastVizF = -1;
function vizFrame(t) {
  if (!state.opts.viz) return;
  const f = Math.floor(t * VIZ_FPS);
  if (f === lastVizF) return; lastVizF = f;
  const nF = vizData ? Math.floor(vizData.length / VIZ_BANDS) : 0;
  for (let i = 0; i < 56; i++) {
    let v = 0;
    if (nF) { const band = i < 28 ? 27 - i : i - 28; v = vizData[Math.min(f, nF - 1) * VIZ_BANDS + band] / 255; }
    const c = Math.abs(i - 27.5) / 28;
    vizBars[i].style.height = ((0.08 + 0.92 * v * (1 - c * 0.35)) * 100).toFixed(2) + '%';
  }
}

/* ---------------- subtitle overlay (canvas renderer) ---------------- */
let lastSubKey = '', subBox = null;
function renderSubs(force) {
  const seg = state.segs.find(s => s.id === state.activeId);
  const key = (seg ? `${seg.id}|${seg.src}|${seg.tgt}` : '-') + '|' + styleVer + '|' + subCv.width + 'x' + subCv.height;
  if (!force && key === lastSubKey) return; lastSubKey = key;
  const g = subCv.getContext('2d');
  g.clearRect(0, 0, subCv.width, subCv.height);
  subBox = null;
  if (!seg) { subsEl.style.display = 'none'; return; }
  const doDraw = () => {
    if (lastSubKey !== key) return;
    g.clearRect(0, 0, subCv.width, subCv.height);
    const bb = SubRender.draw(g, seg, style, subCv.width, subCv.height);
    const dpr = subCv.width / Math.max(1, stageW);
    if (bb) {
      subBox = { x: bb.tx / dpr, y: bb.ty / dpr, w: bb.tw / dpr, h: bb.th / dpr };
      Object.assign(subsEl.style, { display: 'block', left: subBox.x - 8 + 'px', top: subBox.y - 6 + 'px', width: subBox.w + 16 + 'px', height: subBox.h + 12 + 'px' });
    } else subsEl.style.display = 'none';
  };
  doDraw();
  SubRender.ensureFonts(seg, style).then(doDraw);
}
function bumpStyle() { styleVer++; renderSubs(true); scheduleSave(); }

// drag subtitles on stage
(function () {
  let drag = null;
  subsEl.addEventListener('pointerdown', e => {
    if (!subBox) return;
    e.stopPropagation(); subsEl.setPointerCapture(e.pointerId);
    const r = stage.getBoundingClientRect();
    drag = { r, ox: e.clientX - r.left - (subBox.x + subBox.w / 2), oy: e.clientY - r.top - subBox.y, h: subBox.h };
    subsEl.classList.add('drag'); stage.classList.add('dragging'); $('#guides').classList.add('show');
  });
  subsEl.addEventListener('pointermove', e => {
    if (!drag) return;
    const W = drag.r.width, H = drag.r.height;
    let cx = e.clientX - drag.r.left - drag.ox, top = e.clientY - drag.r.top - drag.oy;
    let x = cx / W * 100;
    const snapX = Math.abs(x - 50) < 1.8; if (snapX) x = 50;
    const a = clamp(top / Math.max(1, H - drag.h), 0.02, 0.98);
    style.posX = +clamp(x, 10, 90).toFixed(1); style.posY = +(a * 100).toFixed(1);
    $('#vGuide').classList.toggle('on', snapX);
    const b = $('#posBadge'); b.style.display = 'block'; b.textContent = `X ${style.posX}%  Y ${style.posY}%`;
    syncStyleInputs(); styleVer++; renderSubs(true);
  });
  const end = () => { if (!drag) return; drag = null; subsEl.classList.remove('drag'); stage.classList.remove('dragging'); $('#guides').classList.remove('show'); $('#vGuide').classList.remove('on'); $('#posBadge').style.display = 'none'; $$('.preset').forEach(x => x.classList.remove('on')); bumpStyle(); };
  subsEl.addEventListener('pointerup', end); subsEl.addEventListener('pointercancel', end);
  stage.addEventListener('click', e => {
    if (subsEl.contains(e.target)) return;
    if (stage.classList.contains('empty')) { (state.mode === 'video' ? $('#fileVideo') : $('#fileAudio')).click(); return; }
    toggle();
  });
  stage.addEventListener('dblclick', e => { if (!subsEl.contains(e.target) && !stage.classList.contains('empty')) $('#fsBtn').click(); });
})();

/* ---------------- timeline ---------------- */
const ZMIN = 2, ZMAX = 400;
const zoomToSlider = z => Math.round(Math.log(z / ZMIN) / Math.log(ZMAX / ZMIN) * 1000);
const sliderToZoom = v => ZMIN * Math.pow(ZMAX / ZMIN, v / 1000);
let peaks = null; // Uint8Array @50/s
async function loadPeaks(url) {
  peaks = null;
  if (url) { try { peaks = new Uint8Array(await (await fetch(url)).json()); } catch (e) { peaks = null; } }
  drawTimelineCanvases();
}
function renderTimeline() {
  const Z = state.zoom, W = Math.ceil(state.duration * Z) + 40;
  tlInner.style.width = Math.max(W, tlScroll.clientWidth) + 'px';
  renderTrack();
  drawTimelineCanvases();
  frame(true);
}
function drawTimelineCanvases() {
  const vw = tlScroll.clientWidth, dpr = devicePixelRatio || 1, sx = tlScroll.scrollLeft, Z = state.zoom;
  if (!vw) return;
  // ruler
  const rh = 26;
  rulerCv.width = Math.round(vw * dpr); rulerCv.height = rh * dpr; rulerCv.style.width = vw + 'px'; rulerCv.style.height = rh + 'px'; rulerCv.style.left = sx + 'px';
  let g = rulerCv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, vw, rh);
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];
  const major = steps.find(s => s * Z >= 80) || 3600, minor = major / 5;
  const t0 = Math.floor(sx / Z / minor) * minor, t1 = (sx + vw) / Z;
  g.font = '500 9.5px "JetBrains Mono", monospace'; g.textBaseline = 'alphabetic';
  for (let t = t0; t <= Math.min(t1, state.duration + 1e-6); t += minor) {
    const x = Math.round(t * Z - sx) + 0.5;
    const isMaj = Math.abs(t / major - Math.round(t / major)) < 1e-6;
    g.fillStyle = isMaj ? 'rgba(110,150,200,.6)' : 'rgba(110,150,200,.35)';
    g.fillRect(x, rh - (isMaj ? 9 : 5), 1, isMaj ? 9 : 5);
    if (isMaj) { g.fillStyle = '#7c93ae'; g.fillText(fmtShort(t) + (major < 1 ? '.' + String(Math.round((t % 1) * 10)) : ''), x + 4, rh - 10); }
  }
  // wave
  const wh = 56;
  waveCv.width = Math.round(vw * dpr); waveCv.height = wh * dpr; waveCv.style.width = vw + 'px'; waveCv.style.height = wh + 'px'; waveCv.style.left = sx + 'px';
  g = waveCv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, vw, wh);
  if (!peaks || !peaks.length) {
    g.fillStyle = 'rgba(42,123,255,.18)'; g.fillRect(0, wh / 2 - 0.5, Math.min(vw, state.duration * Z - sx), 1); return;
  }
  const grad = g.createLinearGradient(0, 0, 0, wh); grad.addColorStop(0, 'rgba(42,123,255,.75)'); grad.addColorStop(.5, 'rgba(28,198,238,.9)'); grad.addColorStop(1, 'rgba(42,123,255,.75)');
  g.fillStyle = grad;
  const PPS = 50, step = 3, maxX = Math.min(vw, state.duration * Z - sx);
  for (let x = 0; x < maxX; x += step) {
    const a = Math.floor((sx + x) / Z * PPS), b = Math.max(a + 1, Math.floor((sx + x + step) / Z * PPS));
    let m = 0; for (let i = a; i < b && i < peaks.length; i++) if (peaks[i] > m) m = peaks[i];
    const h = Math.max(1.5, m / 255 * (wh - 8));
    g.beginPath(); g.roundRect ? g.roundRect(x, (wh - h) / 2, 2, h, 1) : g.rect(x, (wh - h) / 2, 2, h); g.fill();
  }
}
tlScroll.addEventListener('scroll', () => { lastScrollX = -1; });
new ResizeObserver(() => { renderTimeline(); }).observe(tlScroll);
function renderTrack() {
  const Z = state.zoom;
  track.innerHTML = state.segs.map(s => `<div class="cue-blk ${s.id === state.selId ? 'sel' : ''} ${s.id === state.activeId ? 'active' : ''}" data-id="${s.id}" style="left:${s.start * Z}px;width:${Math.max(4, (s.end - s.start) * Z)}px"><i class="h l"></i><i class="h r"></i><b>${esc(s.tgt || ' ')}</b><small>${esc(s.src || ' ')}</small></div>`).join('');
}
function posBlock(s) { const b = track.querySelector(`.cue-blk[data-id="${s.id}"]`); if (b) { b.style.left = s.start * state.zoom + 'px'; b.style.width = Math.max(4, (s.end - s.start) * state.zoom) + 'px'; } }

let tlDrag = false;
function scrubStart(e) {
  if (e.target.closest('.cue-blk') || e.button !== 0) return;
  tlDrag = true; const r = tlInner.getBoundingClientRect();
  const go = ev => seek((ev.clientX - r.left) / state.zoom);
  go(e);
  const mv = ev => go(ev), up = () => { tlDrag = false; removeEventListener('pointermove', mv); removeEventListener('pointerup', up); };
  addEventListener('pointermove', mv); addEventListener('pointerup', up);
}
[ruler, $('#wave'), track].forEach(el => el.addEventListener('pointerdown', scrubStart));

track.addEventListener('pointerdown', e => {
  const b = e.target.closest('.cue-blk'); if (!b || e.button !== 0) return;
  e.preventDefault();
  const s = state.segs.find(x => x.id === +b.dataset.id); const i = state.segs.indexOf(s);
  const rect = b.getBoundingClientRect(), ox = e.clientX - rect.left;
  const mode = ox < 7 ? 'l' : ox > rect.width - 7 ? 'r' : 'm';
  const prevEnd = i > 0 ? state.segs[i - 1].end : 0, nextStart = i < state.segs.length - 1 ? state.segs[i + 1].start : state.duration;
  const o = { x: e.clientX, s: s.start, e: s.end }; let moved = false;
  b.classList.add('moving'); tlDrag = true;
  const mv = ev => {
    const d = (ev.clientX - o.x) / state.zoom;
    if (!moved && Math.abs(ev.clientX - o.x) < 3) return;
    if (!moved) { moved = true; snap(); }
    if (mode === 'm') { const len = o.e - o.s; const ns = clamp(o.s + d, prevEnd, nextStart - len); s.start = +ns.toFixed(3); s.end = +(ns + len).toFixed(3); }
    else if (mode === 'l') s.start = +clamp(o.s + d, prevEnd, s.end - 0.2).toFixed(3);
    else s.end = +clamp(o.e + d, s.start + 0.2, nextStart).toFixed(3);
    posBlock(s); updCueTimes(s); $('#tlSel').textContent = `#${pad(i + 1)}  ${fmt(s.start)} → ${fmt(s.end)}  (${(s.end - s.start).toFixed(2)}s)`;
    frame(true);
  };
  const up = () => {
    removeEventListener('pointermove', mv); removeEventListener('pointerup', up); b.classList.remove('moving'); tlDrag = false;
    if (!moved) { select(s.id, true); seek(s.start + 0.001); } else { select(s.id, false); scheduleSave(); }
  };
  addEventListener('pointermove', mv); addEventListener('pointerup', up);
});
$('#zoom').addEventListener('input', e => setZoom(sliderToZoom(+e.target.value)));
function setZoom(z, anchorT) {
  const centerT = anchorT ?? state.t;
  const px = centerT * state.zoom - tlScroll.scrollLeft;
  state.zoom = clamp(z, ZMIN, ZMAX); $('#zoom').value = zoomToSlider(state.zoom); fillRange($('#zoom'));
  renderTimeline(); tlScroll.scrollLeft = centerT * state.zoom - px;
}
tlScroll.addEventListener('wheel', e => {
  if (e.ctrlKey) { e.preventDefault(); const r = tlInner.getBoundingClientRect(); setZoom(state.zoom * (e.deltaY < 0 ? 1.15 : 0.87), (e.clientX - r.left) / state.zoom); }
  else if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { tlScroll.scrollLeft += e.deltaY; e.preventDefault(); }
}, { passive: false });
$('#tlFit').onclick = () => { if (state.duration) setZoom((tlScroll.clientWidth - 50) / state.duration, 0); tlScroll.scrollLeft = 0; };
function setTlWide(on, animate = true) {
  const apply = () => {
    $('.app').classList.toggle('tl-wide', on);
    $('#tlWide').dataset.tip = on ? '收合時間軸 W' : '左右展開時間軸 W';
    $('#tlWide').innerHTML = on
      ? '<svg class="svg" viewBox="0 0 24 24"><path d="m4 8 4 4-4 4M20 8l-4 4 4 4M8 12H2M22 12h-6"/></svg>'
      : '<svg class="svg" viewBox="0 0 24 24"><path d="m18 8 4 4-4 4M6 8l-4 4 4 4M2 12h20"/></svg>';
  };
  if (animate && document.startViewTransition && !document.hidden) {
    const vt = document.startViewTransition(apply);
    [vt.ready, vt.finished, vt.updateCallbackDone].forEach(p => p.catch(() => { }));
  } else apply();
  try { localStorage.setItem('tlWide', on ? '1' : ''); } catch (e) { }
}
$('#tlWide').onclick = () => setTlWide(!$('.app').classList.contains('tl-wide'));
try { if (localStorage.getItem('tlWide')) setTlWide(true, false); } catch (e) { }

/* ---------------- cue list ---------------- */
const visLen = s => [...String(s || '').replace(/\s/g, '')].length;
const cps = s => visLen(s.tgt) / Math.max(.1, s.end - s.start);
const cpsLimit = () => ['zh-TW', 'zh-CN', 'yue', 'ja', 'ko'].includes(state.tgtLang) ? 9 : 21;
function hl(text) { const q = state.query.trim(); if (!q) return esc(text); return esc(text).replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), m => `<mark>${m}</mark>`); }
function cueHTML(s, i) {
  return `<div class="cue ${s.id === state.activeId ? 'active' : ''} ${s.id === state.selId ? 'sel' : ''}" data-id="${s.id}">
    <div class="cue-head">
      <span class="idx">${pad(i + 1)}</span>
      <input class="tc" data-k="start" value="${fmt(s.start)}" spellcheck="false" title="↑↓ 微調 0.1s，Shift 1s">
      <span class="arrow">→</span>
      <input class="tc" data-k="end" value="${fmt(s.end)}" spellcheck="false" title="↑↓ 微調 0.1s，Shift 1s">
      <span class="dur">${(s.end - s.start).toFixed(1)}s</span>
      ${cps(s) > cpsLimit() ? '<span class="cps" title="閱讀速度偏快，建議延長顯示時間或精簡譯文"></span>' : ''}
      <div class="cue-actions">
        <button class="icon-btn" data-a="re" data-tip="重新翻譯">${ICON.refresh}</button>
        <button class="icon-btn" data-a="split" data-tip="分割">${ICON.split}</button>
        <button class="icon-btn" data-a="merge" data-tip="與下一句合併">${ICON.merge}</button>
        <button class="icon-btn" data-a="add" data-tip="在後方插入">${ICON.plus}</button>
        <button class="icon-btn del" data-a="del" data-tip="刪除">${ICON.trash}</button>
      </div>
    </div>
    <div class="cue-body">
      <textarea class="src" data-k="src" rows="1" spellcheck="false" dir="auto">${esc(s.src)}</textarea>
      <textarea class="tgt" data-k="tgt" rows="1" spellcheck="false" dir="auto" placeholder="${s.src ? '（尚未翻譯）' : ''}">${esc(s.tgt)}</textarea>
    </div>
  </div>`;
}
function renderList() {
  const q = state.query.trim().toLowerCase();
  const list = state.segs.map((s, i) => ({ s, i })).filter(({ s }) => !q || s.src.toLowerCase().includes(q) || s.tgt.toLowerCase().includes(q));
  if (!list.length) {
    cueList.innerHTML = q ? `<div class="list-empty">沒有符合搜尋的字幕</div>` :
      `<div class="list-empty"><div class="empty-ic">${ICON.spark}</div><b>尚無字幕</b><small>${activeMedia() ? '點擊左下「生成字幕」開始轉錄與翻譯' : (state.mode === 'video' ? '上傳影片後即可生成字幕' : '上傳音訊後即可生成字幕')}</small></div>`;
    return;
  }
  cueList.innerHTML = list.map(({ s, i }) => cueHTML(s, i)).join('');
  if (!supportsFieldSizing) $$('textarea', cueList).forEach(autoH);
  if (q) $$('textarea', cueList).forEach(ta => ta.classList.toggle('hit', ta.value.toLowerCase().includes(q)));
}
const supportsFieldSizing = CSS.supports && CSS.supports('field-sizing', 'content');
function autoH(ta) { if (supportsFieldSizing) return; ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
function updCueTimes(s) {
  const c = cueList.querySelector(`.cue[data-id="${s.id}"]`); if (!c) return;
  const [a, b] = $$('.tc', c); if (document.activeElement !== a) a.value = fmt(s.start); if (document.activeElement !== b) b.value = fmt(s.end);
  $('.dur', c).textContent = (s.end - s.start).toFixed(1) + 's';
}
function select(id, scroll) {
  state.selId = id;
  $$('.cue.sel,.cue-blk.sel').forEach(e => e.classList.remove('sel'));
  const c = cueList.querySelector(`.cue[data-id="${id}"]`), b = track.querySelector(`.cue-blk[data-id="${id}"]`);
  c && c.classList.add('sel'); b && b.classList.add('sel');
  const s = state.segs.find(x => x.id === id), i = state.segs.indexOf(s);
  if (s) $('#tlSel').textContent = `#${pad(i + 1)}  ${fmt(s.start)} → ${fmt(s.end)}  (${(s.end - s.start).toFixed(2)}s)`;
  if (scroll && c) { cueList.scrollTo({ top: c.offsetTop - cueList.clientHeight / 3, behavior: 'smooth' }); c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash'); }
  if (b && scroll) { const x = s.start * state.zoom; if (x < tlScroll.scrollLeft || x > tlScroll.scrollLeft + tlScroll.clientWidth - 60) tlScroll.scrollLeft = x - tlScroll.clientWidth * .3; }
}
cueList.addEventListener('click', e => {
  const c = e.target.closest('.cue'); if (!c) return; const id = +c.dataset.id;
  const a = e.target.closest('[data-a]');
  if (a) { cueAction(a.dataset.a, id); return; }
  if (state.selId !== id) select(id, false);
  if (!e.target.matches('textarea,input')) seek(state.segs.find(s => s.id === id).start + 0.001);
});
cueList.addEventListener('focusin', e => {
  const c = e.target.closest('.cue'); if (!c) return; const id = +c.dataset.id;
  if (state.selId !== id) select(id, false);
  if (e.target.matches('textarea')) { e.target.dataset.snapped = ''; const s = state.segs.find(x => x.id === id); if (state.t < s.start || state.t >= s.end) seek(s.start + 0.001); }
});
cueList.addEventListener('input', e => {
  const ta = e.target; if (!ta.matches('textarea')) return;
  const s = state.segs.find(x => x.id === +ta.closest('.cue').dataset.id);
  if (!ta.dataset.snapped) { snap(); ta.dataset.snapped = '1'; }
  s[ta.dataset.k] = ta.value; autoH(ta);
  const b = track.querySelector(`.cue-blk[data-id="${s.id}"]`); if (b) { $('b', b).textContent = s.tgt; $('small', b).textContent = s.src; }
  renderSubs(); updStats(); scheduleSave();
});
function commitTC(inp) {
  const s = state.segs.find(x => x.id === +inp.closest('.cue').dataset.id); if (!s) return;
  const v = parseTC(inp.value), k = inp.dataset.k, i = state.segs.indexOf(s);
  const lo = k === 'start' ? (i > 0 ? state.segs[i - 1].end : 0) : s.start + 0.2;
  const hi = k === 'start' ? s.end - 0.2 : (i < state.segs.length - 1 ? state.segs[i + 1].start : state.duration || 1e9);
  if (isNaN(v) || lo > hi) { inp.classList.add('bad'); setTimeout(() => inp.classList.remove('bad'), 700); inp.value = fmt(s[k]); return; }
  const nv = +clamp(v, lo, hi).toFixed(3);
  if (nv !== s[k]) { snap(); s[k] = nv; posBlock(s); frame(true); scheduleSave(); }
  if (Math.abs(nv - v) > 0.0005) { inp.classList.add('bad'); setTimeout(() => inp.classList.remove('bad'), 700); }
  inp.value = fmt(s[k]); updCueTimes(s);
}
cueList.addEventListener('keydown', e => {
  const inp = e.target;
  if (inp.matches('textarea') && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); inp.blur(); return; }
  if (!inp.matches('.tc')) return;
  if (e.key === 'Enter') { inp.blur(); return; }
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    e.preventDefault(); const v = parseTC(inp.value); if (isNaN(v)) return;
    inp.value = fmt(v + (e.key === 'ArrowUp' ? 1 : -1) * (e.shiftKey ? 1 : 0.1)); commitTC(inp);
  }
});
cueList.addEventListener('focusout', e => { if (e.target.matches('.tc')) commitTC(e.target); });

function splitText(t, frac = 0.5) {
  if (!t) return ['', ''];
  const chars = [...t]; const mid = chars.length * frac; let best = -1, bd = 1e9;
  for (let i = 1; i < chars.length - 1; i++) if (/[\s,，。、.!?！？;；:：]/.test(chars[i]) && Math.abs(i - mid) < bd) { bd = Math.abs(i - mid); best = i + 1; }
  if (best < 0 || bd > chars.length * 0.25) best = Math.round(mid);
  return [chars.slice(0, best).join('').trim(), chars.slice(best).join('').trim()];
}
async function cueAction(a, id) {
  const i = state.segs.findIndex(s => s.id === id), s = state.segs[i]; if (!s) return;
  if (a === 're') {
    if (!s.src.trim()) { toast('此句沒有原文', 'info'); return; }
    const c = cueList.querySelector(`.cue[data-id="${id}"]`); c && c.classList.add('busy');
    const pid0 = await ensureProject();
    try {
      const d = await api('POST', `/api/projects/${pid0}/retranslate`, {
        segs: state.segs.map(x => ({ src: x.src, tgt: x.tgt })), index: i, src_lang: srcLangCode(), tgt_lang: state.tgtLang,
        tone: state.tone, opts: tOpts(), current: s.tgt,
      });
      if (project.id !== pid0 || !state.segs.includes(s)) { c && c.classList.remove('busy'); return; }
      if (d.tgt) { snap(); s.tgt = d.tgt; const ta = c && $('textarea.tgt', c); if (ta) { ta.value = s.tgt; autoH(ta); } const b = track.querySelector(`.cue-blk[data-id="${id}"] b`); if (b) b.textContent = s.tgt; renderSubs(true); updStats(); scheduleSave(); toast(`第 ${i + 1} 句已重新翻譯`, 'spark'); }
    } catch (e) { toast(e.message, 'warn', 4000); }
    c && c.classList.remove('busy'); return;
  }
  snap();
  if (a === 'split') {
    const at = state.t > s.start + .2 && state.t < s.end - .2 ? state.t : (s.start + s.end) / 2;
    const frac = (at - s.start) / (s.end - s.start);
    const [s1, s2] = splitText(s.src, frac), [t1, t2] = splitText(s.tgt, frac);
    const n = { id: uid++, start: +at.toFixed(3), end: s.end, src: s2, tgt: t2 };
    s.end = +at.toFixed(3); s.src = s1; s.tgt = t1; state.segs.splice(i + 1, 0, n); toast('已分割字幕', 'split');
  } else if (a === 'merge') {
    const n = state.segs[i + 1]; if (!n) { hist.u.pop(); updHist(); toast('已是最後一句', 'info'); return; }
    const cjkEnd = /[　-ヿ㐀-鿿＀-￯]$/, cjkTgt = ['zh-TW', 'zh-CN', 'yue', 'ja'].includes(state.tgtLang);
    const join = (a, b, glue) => (a && b ? a + glue + b : a + b).trim();
    s.end = n.end; s.src = join(s.src, n.src, cjkEnd.test(s.src) ? '' : ' '); s.tgt = join(s.tgt, n.tgt, cjkTgt ? (cjkEnd.test(s.tgt) ? '' : ' ') : ' ');
    state.segs.splice(i + 1, 1); toast('已合併字幕', 'merge');
  } else if (a === 'add') {
    const nx = state.segs[i + 1], gapEnd = nx ? nx.start : state.duration;
    if (gapEnd - s.end < 0.4) { hist.u.pop(); updHist(); toast('此處空隙不足，請先縮短字幕', 'info'); return; }
    const n = { id: uid++, start: +(s.end + .05).toFixed(3), end: +Math.min(gapEnd - .05, s.end + 2.5).toFixed(3), src: '', tgt: '' };
    state.segs.splice(i + 1, 0, n); state.selId = n.id; segsChanged();
    setTimeout(() => { const ta = cueList.querySelector(`.cue[data-id="${n.id}"] textarea.src`); ta && ta.focus(); }, 30); return;
  } else if (a === 'del') {
    state.segs.splice(i, 1); if (state.selId === id) state.selId = null; toast('已刪除字幕', 'trash');
  }
  segsChanged();
}
function insertAtPlayhead() {
  const t = state.t;
  if (!state.duration) { toast('請先載入媒體', 'info'); return; }
  if (state.segs.some(s => t >= s.start && t < s.end)) { toast('播放頭位於既有字幕內', 'info'); return; }
  const nx = state.segs.find(s => s.start > t), end = Math.min(nx ? nx.start - .05 : state.duration, t + 2.5);
  if (end - t < .4) { toast('此處空隙不足', 'info'); return; }
  snap(); const n = { id: uid++, start: +t.toFixed(3), end: +end.toFixed(3), src: '', tgt: '' };
  state.segs.push(n); state.segs.sort((a, b) => a.start - b.start); state.selId = n.id; segsChanged();
  setTimeout(() => { const ta = cueList.querySelector(`.cue[data-id="${n.id}"] textarea.src`); ta && ta.focus(); }, 30);
}
function splitAtPlayhead() { const s = state.segs.find(s => state.t > s.start + .2 && state.t < s.end - .2); if (!s) { toast('請將播放頭移到字幕中段', 'info'); return; } cueAction('split', s.id); }
$('#tlAdd').onclick = insertAtPlayhead; $('#tlSplit').onclick = splitAtPlayhead;

function segsChanged() {
  renderList(); renderTimeline(); updStats(); updHist(); renderSubs(true); updGenButtons();
  if (state.selId) select(state.selId, false);
  scheduleSave();
}
function updStats() {
  $('#stCount').textContent = state.segs.length;
  $('#stDur').textContent = fmtShort(state.duration);
  $('#stChars').textContent = state.segs.reduce((a, s) => a + visLen(s.tgt), 0).toLocaleString();
}

let qTimer = 0;
$('#q').addEventListener('input', e => { clearTimeout(qTimer); $('#qClear').hidden = !e.target.value; qTimer = setTimeout(() => { state.query = e.target.value; renderList(); }, 120); });
$('#qClear').onclick = () => { $('#q').value = ''; $('#qClear').hidden = true; state.query = ''; renderList(); };
$('#followSw').parentElement.addEventListener('click', e => { e.preventDefault(); state.follow = !state.follow; $('#followSw').classList.toggle('on', state.follow); });

// global offset
let offset = 0;
const showOff = () => $('#offset').value = (offset >= 0 ? '+' : '') + offset.toFixed(2) + 's';
$('#offMinus').onclick = () => { offset = +(offset - .1).toFixed(2); showOff(); };
$('#offPlus').onclick = () => { offset = +(offset + .1).toFixed(2); showOff(); };
$('#offset').addEventListener('change', e => { const v = parseFloat(e.target.value.replace(/[^\d.\-+]/g, '')); offset = isNaN(v) ? 0 : v; showOff(); });
$('#offApply').onclick = () => {
  if (!offset) { toast('請先設定偏移量', 'info'); return; }
  snap(); const D = state.duration || 1e9;
  state.segs.forEach(s => { s.start = +clamp(s.start + offset, 0, D).toFixed(3); s.end = +clamp(s.end + offset, 0, D).toFixed(3); });
  state.segs = state.segs.filter(s => s.end - s.start > 0.05);
  toast(`全部字幕已${offset > 0 ? '延後' : '提前'} ${Math.abs(offset).toFixed(2)} 秒`); offset = 0; showOff(); segsChanged();
};

/* ---------------- style panel ---------------- */
const FONTS = [
  { v: 'Noto Sans TC', l: '思源黑體', n: 'Noto Sans TC', font: 'Noto Sans TC' },
  { v: 'Noto Serif TC', l: '思源宋體', n: 'Noto Serif TC', font: 'Noto Serif TC' },
  { v: 'LXGW WenKai TC', l: '霞鶩文楷', n: 'LXGW WenKai TC', font: 'LXGW WenKai TC' },
  { v: 'Noto Sans SC', l: '思源黑體 简', n: 'Noto Sans SC', font: 'Noto Sans SC' },
  { v: 'Noto Sans JP', l: 'Noto Sans JP', n: '日本語ゴシック', font: 'Noto Sans JP' },
  { v: 'Noto Sans KR', l: 'Noto Sans KR', n: '한국어 고딕', font: 'Noto Sans KR' },
  { v: 'Manrope', l: 'Manrope', n: 'Geometric Sans', font: 'Manrope' },
  { v: 'Inter', l: 'Inter', n: 'Neo-grotesque Sans', font: 'Inter' },
  { v: 'JetBrains Mono', l: 'JetBrains Mono', n: 'Monospace', font: 'JetBrains Mono' },
];
const COLORS = ['#ffffff', '#fff3b0', '#ffe14d', '#cfeaff', '#7dd3fc', '#a7f3d0', '#fda4af', '#0b1a2e'];
const STROKES = ['#0b2545', '#000000', '#1e3a8a', '#ffffff'];
const BOXES = ['#0b1a2e', '#000000', '#1e40af', '#ffffff'];
const PRESETS = [
  { name: '清透藍', s: { bg: 'shadow', tgt: { font: 'Noto Sans TC', size: 56, weight: 700, color: '#ffffff', stroke: 3, strokeColor: '#0b2545' }, src: { font: 'Manrope', size: 36, weight: 600, color: '#cfeaff', stroke: 2, strokeColor: '#0b2545' } } },
  { name: '電影院', s: { bg: 'shadow', tgt: { font: 'Noto Serif TC', size: 52, weight: 500, color: '#fbf7ee', stroke: 0, strokeColor: '#000000' }, src: { font: 'Noto Serif TC', size: 34, weight: 500, color: '#d6d0c4', stroke: 0, strokeColor: '#000000' } } },
  { name: '綜藝黃', s: { bg: 'none', tgt: { font: 'Noto Sans TC', size: 62, weight: 900, color: '#ffe14d', stroke: 6, strokeColor: '#000000' }, src: { font: 'Noto Sans TC', size: 36, weight: 700, color: '#ffffff', stroke: 4, strokeColor: '#000000' } } },
  { name: '極簡底框', s: { bg: 'box', boxColor: '#0b1a2e', boxOpacity: .6, radius: 10, tgt: { font: 'Noto Sans TC', size: 50, weight: 500, color: '#ffffff', stroke: 0, strokeColor: '#000000' }, src: { font: 'Manrope', size: 34, weight: 500, color: '#cfe6ff', stroke: 0, strokeColor: '#000000' } } },
];
function presetPreview(p) {
  const L = (c, t) => `<span style="font-family:'${c.font}';font-weight:${c.weight};color:${c.color};font-size:${c.size / 4.2}px;text-shadow:${c.stroke ? `0 0 ${c.stroke / 2.5}px ${c.strokeColor},0 0 1px ${c.strokeColor},0 0 1px ${c.strokeColor}` : '0 1px 4px rgba(0,0,0,.6)'};${p.s.bg === 'box' ? `background:${SubRender.hexA(p.s.boxColor, p.s.boxOpacity)};padding:1px 6px;border-radius:3px;` : ''}line-height:1.2">${t}</span>`;
  return L(p.s.tgt, '城市正在醒來') + L(p.s.src, 'The city wakes up');
}
$('#presets').innerHTML = PRESETS.map((p, i) => `<button class="preset" data-i="${i}"><div class="pv">${presetPreview(p)}</div><div class="nm">${p.name}</div></button>`).join('');
$('#presets').addEventListener('click', e => {
  const b = e.target.closest('.preset'); if (!b) return;
  const p = PRESETS[+b.dataset.i];
  Object.assign(style, JSON.parse(JSON.stringify({ ...p.s, tgt: undefined, src: undefined })));
  style.tgt = { ...p.s.tgt }; style.src = { ...p.s.src };
  $$('.preset').forEach(x => x.classList.toggle('on', x === b));
  syncStyleInputs(); bumpStyle(); toast(`已套用「${p.name}」`, 'spark');
});
function getRef(path) { return path.startsWith('T.') ? [style[editTrack], path.slice(2)] : [style, path]; }
function fillRange(r) { const p = (r.value - r.min) / (r.max - r.min) * 100; r.style.setProperty('--p', p + '%'); }
const OUTFMT = { size: v => v + 'px', stroke: v => v + 'px', boxOpacity: v => Math.round(v * 100) + '%', radius: v => v + 'px', posY: v => (+v).toFixed(0) + '%', posX: v => (+v).toFixed(0) + '%', gap: v => v + 'px', maxW: v => v + '%' };
function syncStyleInputs() {
  $$('[data-bind]').forEach(r => { const [o, k] = getRef(r.dataset.bind); r.value = o[k]; fillRange(r); });
  $$('[data-out]').forEach(el => { const [o, k] = getRef(el.dataset.out); el.textContent = OUTFMT[k] ? OUTFMT[k](o[k]) : o[k]; });
  $$('.swatches').forEach(sw => { const [o, k] = getRef(sw.dataset.key); $$('.swatch', sw).forEach(x => x.classList.toggle('on', x.dataset.c.toLowerCase() === String(o[k]).toLowerCase())); });
  fontSel.set(style[editTrack].font);
  setSeg('#weightSeg', style[editTrack].weight); setSeg('#bgSeg', style.bg);
  setSeg('#dispSeg', style.display); setSeg('#dispSeg2', style.display);
  $('#boxOpts').classList.toggle('dim', style.bg !== 'box');
  $$('.pos-btn').forEach(b => b.classList.toggle('on', Math.abs(+b.dataset.y - style.posY) < 1));
  $('#orderTxt').textContent = style.order === 'tgt-top' ? '譯文在上，原文在下' : '原文在上，譯文在下';
  $('[data-toggle="balance"] .sw').classList.toggle('on', style.balance !== false);
}
$$('[data-bind]').forEach(r => r.addEventListener('input', () => {
  const [o, k] = getRef(r.dataset.bind); o[k] = +r.value; fillRange(r);
  const out = $(`[data-out="${r.dataset.bind}"]`); if (out) out.textContent = OUTFMT[k] ? OUTFMT[k](o[k]) : o[k];
  if (k === 'posY') $$('.pos-btn').forEach(b => b.classList.toggle('on', Math.abs(+b.dataset.y - style.posY) < 1));
  $$('.preset').forEach(x => x.classList.remove('on'));
  bumpStyle();
}));
function buildSwatches(id, colors) {
  const sw = $(id);
  sw.innerHTML = colors.map(c => `<button class="swatch" data-c="${c}" style="background:${c}"></button>`).join('') + `<label class="color-in" data-tip="自訂顏色"><input type="color"></label>`;
  sw.addEventListener('click', e => { const b = e.target.closest('.swatch'); if (!b) return; setColor(sw, b.dataset.c); });
  $('input', sw).addEventListener('input', e => setColor(sw, e.target.value));
}
function setColor(sw, c) { const [o, k] = getRef(sw.dataset.key); o[k] = c; $$('.preset').forEach(x => x.classList.remove('on')); syncStyleInputs(); bumpStyle(); }
buildSwatches('#colorSw', COLORS); buildSwatches('#strokeSw', STROKES); buildSwatches('#boxSw', BOXES);
const fontSel = makeSelect($('#fontSel'), FONTS, style.tgt.font, v => { style[editTrack].font = v; $$('.preset').forEach(x => x.classList.remove('on')); bumpStyle(); }, { searchable: false, flag: false });
initSeg('#trackSeg', v => { editTrack = v; syncStyleInputs(); });
initSeg('#weightSeg', v => { style[editTrack].weight = +v; bumpStyle(); });
initSeg('#bgSeg', v => { style.bg = v; syncStyleInputs(); bumpStyle(); });
function setDisplay(v) { style.display = v; setSeg('#dispSeg', v); setSeg('#dispSeg2', v); bumpStyle(); }
initSeg('#dispSeg', setDisplay); initSeg('#dispSeg2', setDisplay);
$('#orderBtn').onclick = () => { style.order = style.order === 'tgt-top' ? 'src-top' : 'tgt-top'; syncStyleInputs(); bumpStyle(); };
$('#posBtns').addEventListener('click', e => { const b = e.target.closest('.pos-btn'); if (!b) return; style.posY = +b.dataset.y; style.posX = 50; syncStyleInputs(); bumpStyle(); });

/* ---------------- tabs ---------------- */
initSeg('#tabSeg', v => { $('#pageCues').classList.toggle('on', v === 'cues'); $('#pageStyle').classList.toggle('on', v === 'style'); requestAnimationFrame(refreshSegs); });

/* ---------------- languages ---------------- */
const srcSel = makeSelect($('#srcLang'), LANGS.filter(l => !l.tgtOnly), state.srcLang, v => { state.srcLang = v; updLangHeads(); renderDetect(); scheduleSave(); });
const tgtSel = makeSelect($('#tgtLang'), LANGS.filter(l => l.v !== 'auto'), state.tgtLang, v => {
  const changed = v !== state.tgtLang;
  state.tgtLang = v; updLangHeads(); scheduleSave();
  if (changed && state.segs.some(s => s.tgt)) toast(`按右下「重新翻譯」即可改為${langOf(v).l}，無需重新轉錄`, 'info', 3800);
});
$('#swapLang').onclick = () => {
  const s = srcLangCode();
  if (!s || langOf(state.tgtLang).tgtOnly) { toast('此語言組合無法互換', 'info'); return; }
  state.srcLang = state.tgtLang; state.tgtLang = s; srcSel.set(state.srcLang); tgtSel.set(state.tgtLang); updLangHeads(); renderDetect(); scheduleSave();
};
function srcLangCode() { return state.srcLang === 'auto' ? state.detected : state.srcLang; }
function updLangHeads() { const s = srcLangCode(); $('#srcHead').textContent = `原文${s ? ' · ' + langOf(s).l : ''}`; $('#tgtHead').textContent = `譯文 · ${langOf(state.tgtLang).l}`; }
function renderDetect() { $('#detectBadge').innerHTML = state.srcLang === 'auto' && state.detected && state.segs.length ? `<span class="detect">${ICON.check}已偵測為${langOf(state.detected).l}</span>` : ''; }

$('#toneChips').addEventListener('click', e => { const c = e.target.closest('.chip'); if (!c) return; state.tone = c.dataset.v; $$('#toneChips .chip').forEach(x => x.classList.toggle('on', x === c)); scheduleSave(); });
$$('[data-toggle]').forEach(row => row.addEventListener('click', () => {
  const k = row.dataset.toggle, sw = $('.sw', row), on = !sw.classList.contains('on');
  sw.classList.toggle('on', on);
  if (k === 'balance') { style.balance = on; bumpStyle(); return; }
  state.opts[k] = on;
  if (k === 'viz') { $('#viz').classList.toggle('off', !on); }
  scheduleSave();
}));
const tOpts = () => ({ keep_names: state.opts.keepNames, context: state.opts.context, proofread: state.opts.proofread });
function syncLeftInputs() {
  srcSel.set(state.srcLang); tgtSel.set(state.tgtLang);
  $$('#toneChips .chip').forEach(x => x.classList.toggle('on', x.dataset.v === state.tone));
  ['keepNames', 'context', 'proofread', 'viz'].forEach(k => { const r = $(`[data-toggle="${k}"] .sw`); r && r.classList.toggle('on', !!state.opts[k]); });
  $('#viz').classList.toggle('off', !state.opts.viz);
  setSeg('#arSeg', state.arKey); setSeg('#modeSeg', state.mode);
  updLangHeads(); renderDetect();
}

/* ---------------- projects ---------------- */
let saveTimer = 0, saving = false, dirty = false;
function serial() {
  return { mode: state.mode, segs: state.segs, style, srcLang: state.srcLang, detected: state.detected, tgtLang: state.tgtLang, tone: state.tone, opts: state.opts, arKey: state.arKey, zoom: state.zoom, t: state.t };
}
function setSaved(txt, ok = true) { const t = $('#savedTag'); t.classList.toggle('pending', !ok); $('em', t).textContent = txt; }
function scheduleSave() {
  dirty = true;
  if (!project.id) return;
  setSaved('儲存中…', false);
  clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 700);
}
async function saveNow() {
  if (!project.id || saving) { if (saving) { clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 400); } return; }
  saving = true; dirty = false;
  try {
    await api('PUT', `/api/projects/${project.id}`, { name: $('#projName').value.trim() || '未命名專案', state: serial() });
    const d = new Date(); setSaved(`已自動儲存 ${pad(d.getHours())}:${pad(d.getMinutes())}`);
  } catch (e) { setSaved('儲存失敗', false); }
  saving = false;
}
async function ensureProject() {
  if (project.id) return project.id;
  const p = await api('POST', '/api/projects', { name: $('#projName').value.trim() || '未命名專案' });
  project.id = p.id; project.media = {};
  try { localStorage.setItem('lastProject', p.id); } catch (e) { }
  scheduleSave();
  return p.id;
}
$('#projName').addEventListener('input', () => scheduleSave());
$('#projName').addEventListener('keydown', e => { if (e.key === 'Enter') e.target.blur(); });

function guardBusy() {
  if (busy || exporting) { toast('請等待目前的工作完成後再切換專案', 'info'); return true; }
  return false;
}
async function flushSave() { clearTimeout(saveTimer); if (dirty && project.id) await saveNow(); }
async function openProject(id) {
  await flushSave();
  const p = await api('GET', `/api/projects/${id}`);
  pause();
  project.id = p.id; project.name = p.name; project.media = p.media || {};
  try { localStorage.setItem('lastProject', p.id); } catch (e) { }
  $('#projName').value = p.name || '未命名專案';
  const st = p.state || {};
  style = Object.assign(JSON.parse(JSON.stringify(DEFAULT_STYLE)), st.style || {});
  style.tgt = Object.assign({}, DEFAULT_STYLE.tgt, (st.style || {}).tgt); style.src = Object.assign({}, DEFAULT_STYLE.src, (st.style || {}).src);
  state.segs = (st.segs || []).map(s => ({ id: uid++, start: +s.start, end: +s.end, src: s.src || '', tgt: s.tgt || '' }));
  state.srcLang = st.srcLang || 'auto'; state.detected = st.detected || ''; state.tgtLang = st.tgtLang || 'zh-TW'; state.tone = st.tone || 'natural';
  state.opts = Object.assign({ keepNames: true, context: true, proofread: true, viz: true }, st.opts || {});
  state.arKey = st.arKey || '16:9'; { const [a, b] = state.arKey.split(':').map(Number); state.audioAR = a / b; }
  if (st.zoom) state.zoom = st.zoom;
  $('#zoom').value = zoomToSlider(state.zoom); fillRange($('#zoom'));
  hist.u = []; hist.r = []; updHist(); state.selId = null; state.activeId = null;
  syncLeftInputs(); syncStyleInputs();
  await setMode(st.mode || 'video', true);
  applyMediaUI();
  if (st.t) seek(Math.min(st.t, state.duration || st.t));
  segsChanged();
  setSaved('已開啟專案');
}
async function newProject() {
  await flushSave();
  pause();
  project.id = null; project.media = {}; try { localStorage.removeItem('lastProject'); } catch (e) { }
  $('#projName').value = '未命名專案';
  state.segs = []; state.detected = ''; state.selId = null; hist.u = []; hist.r = []; updHist();
  style = JSON.parse(JSON.stringify(DEFAULT_STYLE)); syncStyleInputs();
  video.removeAttribute('src'); audio.removeAttribute('src'); video.load(); audio.load();
  peaks = null; vizData = null; bgImg.removeAttribute('src');
  applyMediaUI(); setMode(state.mode, true); segsChanged(); setSaved('尚未儲存');
}
$('#projBtn').onclick = async () => {
  const btn = $('#projBtn');
  if (popAnchor === btn) { closePop(); return; }
  let list = [];
  try { list = await api('GET', '/api/projects'); } catch (e) { }
  closePop(); popAnchor = btn; btn.classList.add('open');
  const when = ts => { const d = new Date(ts * 1000); return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  pop.innerHTML = `<div class="pop-item new" data-new="1">${ICON.plus}<span>新增專案</span></div><div class="pop-sep"></div><div class="pop-list scroll">${list.map(p => `
    <div class="pop-item proj-item ${p.id === project.id ? 'on' : ''}" data-id="${p.id}">
      <span class="pi-ic">${p.mode === 'audio' ? ICON.music : ICON.film}</span>
      <span class="pi-main"><b>${esc(p.name || '未命名專案')}</b><small>${when(p.updated)} · ${p.cues} 句${p.primary ? ' · ' + esc(p.primary) : ''}</small></span>
      <button class="icon-btn sm pi-del" data-del="${p.id}" data-tip="刪除專案">${ICON.trash}</button>
    </div>`).join('') || '<div class="pop-empty">還沒有專案</div>'}</div>`;
  const r = btn.getBoundingClientRect();
  pop.style.width = '340px'; pop.style.left = Math.max(8, r.left - 10) + 'px'; pop.style.top = r.bottom + 8 + 'px';
  requestAnimationFrame(() => { if (popAnchor === btn) pop.classList.add('show'); });
  pop.onclick = async e => {
    const del = e.target.closest('[data-del]');
    if (del) {
      e.stopPropagation();
      if (del.dataset.del === project.id && guardBusy()) return;
      if (!confirm('確定刪除此專案？專案內的媒體與輸出檔案都會被刪除。')) return;
      await api('DELETE', `/api/projects/${del.dataset.del}`);
      if (del.dataset.del === project.id) await newProject();
      closePop(); toast('已刪除專案', 'trash'); return;
    }
    const it = e.target.closest('.pop-item'); if (!it) return;
    closePop(); pop.onclick = null;
    if (guardBusy()) return;
    if (it.dataset.new) { await newProject(); toast('已建立新專案', 'spark'); return; }
    if (it.dataset.id && it.dataset.id !== project.id) { try { await openProject(it.dataset.id); toast('已開啟專案'); } catch (err) { toast(err.message, 'warn'); } }
  };
};

/* ---------------- mode + media ---------------- */
initSeg('#modeSeg', v => { setMode(v); scheduleSave(); });
async function setMode(m, quiet) {
  pause(); state.mode = m;
  setSeg('#modeSeg', m);
  $('#srcVideo').style.display = m === 'video' ? '' : 'none'; $('#srcAudio').style.display = m === 'audio' ? '' : 'none';
  stage.classList.toggle('audio', m === 'audio');
  const med = project.media[m];
  await loadPeaks(med && med.peaks);
  if (m === 'audio') { await loadViz(med && med.viz); drawBg(); }
  updDuration();
  applyMediaUI();
  layoutStage(); refreshSegs(); renderTimeline(); updStats(); renderList(); updGenButtons();
  const am = activeMedia(); if (am) am.currentTime = state.t;
}
function updDuration() {
  const med = project.media[state.mode];
  const am = activeMedia();
  let d = (am && isFinite(am.duration) && am.duration) || (med && med.info && med.info.duration) || 0;
  if (!d && state.segs.length) d = state.segs[state.segs.length - 1].end + 1;
  state.duration = d; state.t = Math.min(state.t, d);
}
function applyMediaUI() {
  const v = project.media.video, a = project.media.audio, im = project.media.image;
  // video card
  $('#videoCard').hidden = !v;
  if (v) {
    $('#videoName').textContent = v.name;
    const i = v.info || {};
    $('#videoInfo').textContent = v.ready ? `${i.width}×${i.height} · ${fmtShort(i.duration)} · ${sizeStr(v.size || 0)}` : '處理中…';
    if (v.preview && video.getAttribute('src') !== v.preview) { video.src = v.preview; video.volume = +$('#vol').value; }
    state.videoAR = i.width && i.height ? i.width / i.height : 16 / 9;
  } else if (video.getAttribute('src')) { video.removeAttribute('src'); video.load(); }
  $('#dropVideo b').textContent = v ? '更換影片' : '拖放或點擊上傳影片';
  // audio
  $('#audioName').textContent = a ? a.name : '上傳音訊';
  $('#audioInfo').textContent = a ? (a.ready ? `${fmtShort(a.info.duration)} · ${sizeStr(a.size || 0)}` : '處理中…') : 'MP3 · WAV · M4A · FLAC';
  $('#dropAudio').classList.toggle('has', !!a);
  if (a && a.preview && audio.getAttribute('src') !== a.preview) { audio.src = a.preview; audio.volume = +$('#vol').value; }
  if (!a && audio.getAttribute('src')) { audio.removeAttribute('src'); audio.load(); }
  $('#trackTitle').textContent = a ? a.name : '';
  // image
  $('#imgName').textContent = im ? im.name : '上傳背景圖';
  $('#imgInfo').textContent = im ? `${im.width}×${im.height}` : '未上傳時使用預設漸層背景';
  $('#imgThumb').style.backgroundImage = im ? `url("${im.url}")` : '';
  $('#imgThumb').classList.toggle('img', !!im);
  $('#dropImg').classList.toggle('has', !!im);
  if (im && bgImg.getAttribute('src') !== im.url) bgImg.src = im.url;
  // stage
  const hasMedia = !!(state.mode === 'video' ? v : a);
  stage.classList.toggle('empty', !hasMedia);
  stage.classList.toggle('has-video', state.mode === 'video' && !!v);
  $('#esTitle').textContent = state.mode === 'video' ? '拖放影片到這裡開始' : '拖放音訊到這裡開始';
  $('#esSub').textContent = state.mode === 'video' ? '自動轉錄、翻譯並生成雙語字幕' : '搭配背景圖，製作帶字幕的影片';
  drawBg();
}
video.addEventListener('loadedmetadata', () => {
  if (video.videoWidth) state.videoAR = video.videoWidth / video.videoHeight;
  if (state.mode === 'video') { updDuration(); layoutStage(); renderTimeline(); updStats(); }
  // thumbnail
  const th = $('#videoCard .file-thumb');
  const grab = () => { try { const c = document.createElement('canvas'); c.width = 88; c.height = Math.round(88 / state.videoAR); c.getContext('2d').drawImage(video, 0, 0, c.width, c.height); th.style.backgroundImage = `url(${c.toDataURL()})`; th.innerHTML = ''; } catch (e) { } };
  if (video.readyState >= 2) grab(); else video.addEventListener('loadeddata', grab, { once: true });
});
audio.addEventListener('loadedmetadata', () => { if (state.mode === 'audio') { updDuration(); renderTimeline(); updStats(); } });

function setRing(id, p) { const r = $(id); if (!r) return; r.classList.toggle('on', p != null && p < 1); if (p != null) r.style.setProperty('--p', clamp(p, 0, 1)); }
async function uploadMedia(kind, f) {
  if (busy) { toast('請等待目前工作完成', 'info'); return; }
  const pid = await ensureProject();
  const ringId = kind === 'video' ? '#videoRing' : kind === 'audio' ? '#audioRing' : null;
  pause();
  const fd = new FormData(); fd.append('kind', kind); fd.append('file', f);
  const prevMedia = project.media[kind];
  if (kind !== 'image') {
    project.media[kind] = { name: f.name, ready: false, size: f.size, info: {} };
    applyMediaUI(); setRing(ringId, 0.001);
    if (kind === 'video') { $('#videoCard .file-thumb').style.backgroundImage = ''; }
  }
  busy = 'upload';
  try {
    const d = await uploadXHR(`/api/projects/${pid}/media`, fd, p => setRing(ringId, p * 0.5));
    let res = d;
    if (d.job) res = await pollJob(d.job, p => setRing(ringId, 0.5 + p * 0.5));
    if (project.id !== pid) { busy = null; return; }
    project.media[kind] = res.media;
    setRing(ringId, null);
    if ($('#projName').value === '未命名專案' && kind !== 'image') { $('#projName').value = f.name.replace(/\.[^.]+$/, '').slice(0, 60); }
    if (kind === 'image') {
      applyMediaUI();
      const r = res.media.width / res.media.height, opts = { '16:9': 16 / 9, '9:16': 9 / 16, '1:1': 1, '4:3': 4 / 3 };
      let best = '16:9'; for (const k in opts) if (Math.abs(Math.log(opts[k] / r)) < Math.abs(Math.log(opts[best] / r))) best = k;
      state.arKey = best; state.audioAR = opts[best]; setSeg('#arSeg', best); layoutStage();
      toast('背景圖已套用', 'info');
    } else {
      state.t = 0;
      if (state.mode !== kind && !(kind === 'audio' && state.mode === 'audio')) await setMode(kind);
      else { await setMode(state.mode); }
      toast(state.segs.length ? '媒體已更新' : '已載入，點擊「生成字幕」開始', 'info', 3200);
    }
    scheduleSave();
  } catch (e) {
    setRing(ringId, null);
    if (project.id === pid && kind !== 'image') {
      if (prevMedia) project.media[kind] = prevMedia; else delete project.media[kind];
      applyMediaUI();
    }
    toast(e.message, 'warn', 5000);
  }
  busy = null;
}
function bindDrop(zone, input, handler) {
  const z = typeof zone === 'string' ? $(zone) : zone, inp = $(input);
  inp.addEventListener('change', () => { inp.files[0] && handler(inp.files[0]); inp.value = ''; });
  ['dragenter', 'dragover'].forEach(ev => z.addEventListener(ev, e => { e.preventDefault(); z.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => z.addEventListener(ev, e => { e.preventDefault(); z.classList.remove('over'); }));
  z.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; f && handler(f); });
}
bindDrop('#dropVideo', '#fileVideo', f => uploadMedia('video', f));
bindDrop('#dropAudio', '#fileAudio', f => uploadMedia('audio', f));
bindDrop('#dropImg', '#fileImg', f => uploadMedia('image', f));
// drop anything on the stage
['dragenter', 'dragover'].forEach(ev => stage.addEventListener(ev, e => { e.preventDefault(); stage.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => stage.addEventListener(ev, e => { e.preventDefault(); stage.classList.remove('over'); }));
stage.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0]; if (!f) return;
  if (f.type.startsWith('image/')) { if (state.mode !== 'audio') setMode('audio'); uploadMedia('image', f); }
  else if (f.type.startsWith('audio/') || /\.(mp3|wav|m4a|flac|ogg|opus|aac|wma)$/i.test(f.name)) { if (state.mode !== 'audio') setMode('audio'); uploadMedia('audio', f); }
  else uploadMedia(state.mode === 'audio' && !f.type.startsWith('video/') ? 'audio' : 'video', f);
});
initSeg('#arSeg', v => { const [a, b] = v.split(':').map(Number); state.audioAR = a / b; state.arKey = v; layoutStage(); scheduleSave(); });

/* ---------------- generate / translate ---------------- */
let curJob = null;
function updGenButtons() {
  const has = state.segs.length > 0;
  if (!busy) $('#genTxt').textContent = has ? '重新生成字幕' : '生成字幕';
  $('#retrBtn').hidden = !has || !!busy;
}
function setBusy(kind, p, label) {
  const b = $('#genBtn');
  if (kind) {
    busy = kind; b.classList.add('loading'); $('#cancelBtn').hidden = false; $('#retrBtn').hidden = true;
    const pct = Math.round((p || 0) * 100);
    b.style.setProperty('--p', (p || 0) * 100 + '%');
    $('#genTxt').textContent = `${label || '處理中'} ${pct}%`;
    stage.classList.add('busy'); $('#bvPct').textContent = pct + '%'; $('#bvTxt').textContent = label || 'AI 處理中';
    $('#busyVeil .v').style.strokeDashoffset = (119.4 * (1 - (p || 0))).toFixed(1);
  } else {
    busy = null; b.classList.remove('loading'); b.style.setProperty('--p', '0%'); $('#cancelBtn').hidden = true; stage.classList.remove('busy');
    updGenButtons();
  }
}
$('#cancelBtn').onclick = async () => { if (curJob) { try { await api('POST', `/api/jobs/${curJob}/cancel`); } catch (e) { } } };
$('#genBtn').onclick = async () => {
  if (busy) return;
  const med = project.media[state.mode];
  if (!med) { toast(state.mode === 'video' ? '請先上傳影片' : '請先上傳音訊', 'info'); (state.mode === 'video' ? $('#fileVideo') : $('#fileAudio')).click(); return; }
  if (!med.ready) { toast('媒體仍在處理中，請稍候', 'info'); return; }
  if (state.segs.length && !confirm('重新生成會覆蓋目前的字幕（可用 Ctrl+Z 復原）。確定繼續？')) return;
  pause();
  setBusy('gen', 0, 'AI 生成中');
  try {
    const pid = await ensureProject();
    const d = await api('POST', `/api/projects/${pid}/generate`, {
      mode: state.mode, src_lang: state.srcLang, tgt_lang: state.tgtLang, tone: state.tone, opts: tOpts(),
      max_chars: settingsCache.max_chars || 42, translate: true,
    });
    curJob = d.job;
    const res = await pollJob(d.job, p => setBusy('gen', p, 'AI 生成中'));
    if (project.id !== pid) throw new Error('專案已切換，已捨棄生成結果');
    snap();
    state.segs = res.segs.map(s => ({ id: uid++, start: +s.start, end: +s.end, src: s.src, tgt: s.tgt || '' }));
    state.detected = res.detected || ''; state.selId = null;
    segsChanged(); renderDetect(); updLangHeads(); seek(0);
    if (res.translate_error) toast('轉錄完成，但翻譯失敗：' + res.translate_error, 'warn', 6000);
    else toast(`字幕已生成 · 共 ${state.segs.length} 句`, 'spark');
  } catch (e) { if (!e.cancelled) toast(e.message, 'warn', 6000); else toast('已取消', 'info'); }
  curJob = null; setBusy(null);
};
$('#retrBtn').onclick = async () => {
  if (busy || !state.segs.length) return;
  setBusy('tr', 0, '翻譯中');
  try {
    const pid = await ensureProject();
    const ids = state.segs.map(s => s.id);
    const d = await api('POST', `/api/projects/${pid}/translate`, { segs: state.segs.map(s => ({ src: s.src })), src_lang: srcLangCode(), tgt_lang: state.tgtLang, tone: state.tone, opts: tOpts() });
    curJob = d.job;
    const res = await pollJob(d.job, p => setBusy('tr', p, '翻譯中'));
    if (project.id !== pid) throw new Error('專案已切換，已捨棄翻譯結果');
    snap();
    const byId = new Map(state.segs.map(s => [s.id, s]));
    ids.forEach((id, i) => { const s = byId.get(id); if (s && res.tgt[i] !== undefined) s.tgt = res.tgt[i]; });
    segsChanged(); toast(`已重新翻譯為${langOf(state.tgtLang).l}`, 'spark');
  } catch (e) { if (!e.cancelled) toast(e.message, 'warn', 6000); else toast('已取消', 'info'); }
  curJob = null; setBusy(null);
};

/* ---------------- transport ---------------- */
$('#playBtn').onclick = toggle;
const jumpCue = dir => {
  const t = state.t;
  const s = dir > 0 ? state.segs.find(s => s.start > t + .01) : [...state.segs].reverse().find(s => s.start < t - .3);
  if (s) { seek(s.start + .001); select(s.id, true); } else if (dir < 0) seek(0);
};
$('#prevCue').onclick = () => jumpCue(-1); $('#nextCue').onclick = () => jumpCue(1);
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
$('#speedBtn').onclick = () => { state.rate = SPEEDS[(SPEEDS.indexOf(state.rate) + 1) % SPEEDS.length]; video.playbackRate = audio.playbackRate = state.rate; $('#speedBtn').textContent = state.rate.toFixed(state.rate % 1 ? 2 : 1).replace(/0$/, '') + '×'; };
$('#vol').addEventListener('input', e => { fillRange(e.target); video.volume = audio.volume = +e.target.value; video.muted = audio.muted = false; $('#muteBtn').classList.remove('muted'); });
$('#muteBtn').onclick = () => { const m = !video.muted; video.muted = audio.muted = m; $('#muteBtn').classList.toggle('muted', m); };
$('#fsBtn').onclick = () => document.fullscreenElement ? document.exitFullscreen() : stage.requestFullscreen();
document.addEventListener('fullscreenchange', () => setTimeout(layoutStage, 60));

/* ---------------- modals ---------------- */
function openModal(id) { $(id).classList.add('show'); requestAnimationFrame(refreshSegs); }
function closeModal(id) { $(id).classList.remove('show'); }
$$('.modal-bg').forEach(m => { m.addEventListener('pointerdown', e => { m._down = e.target === m; }); m.addEventListener('click', e => { if ((e.target === m && m._down) || e.target.closest('[data-close]')) m.classList.remove('show'); }); });
$('#kbdBtn').onclick = () => openModal('#kbdModal');

// ---- settings
let settingsCache = {};
async function loadSettingsUI() {
  try {
    const s = await api('GET', '/api/settings'); settingsCache = s;
    $('#apiKey').value = ''; $('#apiKey').placeholder = s.api_key_set ? s.api_key : 'sk-••••••••••••••••';
    $('#keyState').textContent = s.api_key_set ? '已設定' : '尚未設定';
    $('#baseUrl').value = s.base_url; $('#modelName').value = s.model; $('#glossary').value = s.glossary || '';
    setSeg('#devSeg', s.device || 'cuda'); $('#maxChar').value = s.max_chars || 42; fillRange($('#maxChar')); $('#maxCharOut').textContent = (s.max_chars || 42);
  } catch (e) { }
}
$('#settingsBtn').onclick = () => { loadSettingsUI(); openModal('#settingsModal'); };
initSeg('#devSeg');
$('#maxChar').addEventListener('input', e => { fillRange(e.target); $('#maxCharOut').textContent = e.target.value; });
$('#testApi').onclick = async () => {
  const b = $('#testApi'); b.disabled = true; b.textContent = '測試中…';
  try {
    const d = await api('POST', '/api/settings/test', { api_key: $('#apiKey').value.trim(), base_url: $('#baseUrl').value.trim(), model: $('#modelName').value.trim() });
    d.ok ? toast(`連線成功 · ${d.ms} ms`) : toast('連線失敗：' + d.error, 'warn', 6000);
  } catch (e) { toast(e.message, 'warn'); }
  b.disabled = false; b.textContent = '測試連線';
};
$('#saveSettings').onclick = async () => {
  try {
    const body = { base_url: $('#baseUrl').value.trim(), model: $('#modelName').value.trim(), glossary: $('#glossary').value, device: segVal('#devSeg'), max_chars: +$('#maxChar').value };
    const k = $('#apiKey').value.trim(); if (k) body.api_key = k;
    settingsCache = await api('POST', '/api/settings', body);
    closeModal('#settingsModal'); toast('設定已儲存'); pollStatus();
  } catch (e) { toast(e.message, 'warn'); }
};

// ---- model status
let statusTimer = 0;
async function pollStatus() {
  clearTimeout(statusTimer);
  let next = 4000;
  try {
    const s = await api('GET', '/api/status');
    settingsCache = Object.assign(settingsCache, s.settings || {});
    const pill = $('#modelPill');
    pill.classList.remove('loading', 'error', 'idle');
    if (s.asr === 'ready') { pill.dataset.tip = `模型已就緒 · ${s.device.toUpperCase()}${s.gpu ? ' · ' + s.gpu : ''}`; next = 30000; }
    else if (s.asr === 'error') { pill.classList.add('error'); pill.dataset.tip = '模型載入失敗：' + (s.asr_error || '').slice(0, 60); }
    else { pill.classList.add('loading'); pill.dataset.tip = '模型載入中…'; }
    $('#modelTxt').textContent = s.asr === 'ready' ? 'Qwen3-ASR · DeepSeek' : s.asr === 'error' ? '模型載入失敗' : '模型載入中…';
    $$('[data-mtag]').forEach(t => { t.textContent = s.asr === 'ready' ? '已載入' : s.asr === 'error' ? '失敗' : '載入中'; t.className = 'tag ' + s.asr; });
    $('#gpuInfo').textContent = s.gpu || (s.device === 'cpu' ? 'CPU' : '');
    $('#encInfo').textContent = s.nvenc ? 'NVENC 硬體加速' : 'x264';
    if (!(s.settings || {}).api_key_set && !pollStatus.warned) { pollStatus.warned = true; toast('尚未設定 DeepSeek API Key，請至「設定」填寫', 'warn', 5000); }
  } catch (e) { next = 5000; }
  statusTimer = setTimeout(pollStatus, next);
}

// ---- export
let expFmt = 'srt', expContent = 'dual';
$('#fmtGrid').addEventListener('click', e => { const b = e.target.closest('.fmt'); if (!b) return; expFmt = b.dataset.v; $$('.fmt').forEach(x => x.classList.toggle('on', x === b)); updPreview(); updOutPrev(); });

/* ---- output location (folder + file name) ---- */
const outLoc = { dir: '', names: {} };           // custom names remembered per project
const cleanName = s => String(s || '').replace(/[\\/:*?"<>|]+/g, '_').replace(/^[\s.]+|[\s.]+$/g, '');
const outBase = () => cleanName($('#outName').value) || cleanName($('#projName').value) || 'subtitles';
const vidExt = () => segVal('#burnSeg') === 'soft' ? '.mkv' : '.mp4';
function joinPath(dir, name) { const sep = dir.includes('/') && !dir.includes('\\') ? '/' : '\\'; return dir.replace(/[\\/]+$/, '') + sep + name; }
function updOutPrev() {
  $('#outDir').textContent = outLoc.dir || '尚未選擇'; $('#outDirBox').title = outLoc.dir;
  $('#outExt').textContent = `.${expFmt} · ${vidExt()}`;
  const b = esc(outBase());
  $('#outPrev').innerHTML = `字幕檔 <b>${b}.${expFmt}</b><i></i>影片 <b>${b}${vidExt()}</b>`;
}
async function initOutLoc() {
  if (!outLoc.dir) { try { outLoc.dir = (await api('GET', '/api/default-dir')).dir; } catch (e) { } }
  const saved = outLoc.names[project.id || '_'];
  $('#outName').value = saved != null ? saved : cleanName($('#projName').value) || 'subtitles';
  updOutPrev();
}
$('#outName').addEventListener('input', () => { outLoc.names[project.id || '_'] = $('#outName').value; updOutPrev(); });
$('#outName').addEventListener('blur', () => { const v = cleanName($('#outName').value); if (v !== $('#outName').value) { $('#outName').value = v; outLoc.names[project.id || '_'] = v; updOutPrev(); } });
async function pickDir() {
  const b = $('#pickDir'); b.disabled = true;
  try { const d = await api('POST', '/api/dialog', { mode: 'dir', title: '選擇輸出資料夾', dir: outLoc.dir }); if (d.path) { outLoc.dir = d.path; updOutPrev(); } }
  finally { b.disabled = false; }
}
$('#pickDir').onclick = $('#outDirBox').onclick = () => pickDir().catch(e => toast(e.message, 'warn'));
async function targetPath(ext) {
  if (!outLoc.dir) { await pickDir(); if (!outLoc.dir) return null; }
  const path = joinPath(outLoc.dir, outBase() + ext);
  const c = await api('POST', '/api/path/check', { path });
  if (c.exists && !confirm(`「${outBase() + ext}」已存在於此資料夾，要覆蓋嗎？`)) return null;
  return c.path;
}
function showDone(path, size, isVideo) {
  const name = path.split(/[\\/]/).pop(), dir = path.slice(0, path.length - name.length - 1);
  $('#edName').textContent = name; $('#edName').title = path;
  $('#edSize').textContent = `${sizeStr(size)} · ${dir}`; $('#edSize').title = dir;
  $('#edPlay span').textContent = isVideo ? '播放' : '開啟';
  $('#edOpen').onclick = () => api('POST', '/api/reveal', { path }).catch(e => toast(e.message, 'warn'));
  $('#edPlay').onclick = () => api('POST', '/api/reveal', { path, open: true }).catch(e => toast(e.message, 'warn'));
  $('#expDone').hidden = false;
}
initSeg('#expContent', v => { expContent = v; updPreview(); });
initSeg('#burnSeg', () => updExpHint()); initSeg('#resSeg', () => updExpHint()); initSeg('#qSeg');
$('#exportBtn').onclick = () => {
  expContent = style.display; setSeg('#expContent', expContent);
  $('#expDone').hidden = true; if (!exporting) $('#expProg').hidden = true;
  const hasMedia = !!project.media[state.mode];
  $('#dlVideoTxt').textContent = segVal('#burnSeg') === 'soft' ? '匯出 MKV 影片' : '匯出 MP4 影片';
  $('#dlVideo').disabled = !hasMedia;
  openModal('#exportModal'); updPreview(); updExpHint(); initOutLoc();
};
function exportDims() {
  const res = segVal('#resSeg');
  if (state.mode === 'audio') {
    const base = res === '720' ? 720 : 1080;
    const ar = state.audioAR;
    return ar >= 1 ? [Math.round(base * ar / 2) * 2, base] : [base, Math.round(base / ar / 2) * 2];
  }
  const i = (project.media.video || {}).info || {};
  let w = i.width || 1920, h = i.height || 1080;
  if (res !== 'src') { const target = +res; const sh = Math.min(w, h); if (target < sh) { const k = target / sh; w = w * k; h = h * k; } }
  return [Math.round(w / 2) * 2, Math.round(h / 2) * 2];
}
function updExpHint() {
  const soft = segVal('#burnSeg') === 'soft';
  const [w, h] = exportDims();
  $('#dlVideoTxt').textContent = soft ? '匯出 MKV 影片' : '匯出 MP4 影片';
  updOutPrev();
  $('#expHint').innerHTML = `${ICON.info}<span>${soft ? '軟字幕封裝為 MKV（ASS 樣式軌），影像不重新編碼，速度最快；播放器可開關字幕。' : '硬字幕以預覽相同的渲染器逐句繪製後燒錄，成品與預覽完全一致。'} 輸出 ${w}×${h}${state.mode === 'audio' ? ` · 背景圖${state.opts.viz ? '＋音訊律動' : ''}` : ''}</span>`;
}
function textFor(s, c = expContent) {
  const T = s.tgt, S = s.src;
  if (c === 'tgt') return T; if (c === 'src') return S;
  return style.order === 'tgt-top' ? [T, S].filter(Boolean).join('\n') : [S, T].filter(Boolean).join('\n');
}
function assColor(hex, a = 0) { const h = hex.replace('#', ''); return `&H${pad(Math.round(a * 255).toString(16), 2)}${h.slice(4, 6)}${h.slice(2, 4)}${h.slice(0, 2)}`.toUpperCase(); }
const assEsc = t => String(t).replace(/\\/g, '\\\\').replace(/\{/g, '｛').replace(/\}/g, '｝');
function buildASS(content = expContent, dims) {
  const [W, H] = dims || exportDims();
  const k = 1080 / Math.min(W, H);
  const PX = Math.round(W * k), PY = Math.round(H * k);
  const cv = document.createElement('canvas').getContext('2d');
  const isBox = style.bg === 'box';
  const st = (n, c) => `Style: ${n},${c.font},${Math.round(c.size)},${assColor(c.color)},&H000000FF,${isBox ? assColor(style.boxColor, 1 - style.boxOpacity) : assColor(c.strokeColor)},${style.bg === 'shadow' ? '&H59230C00' : '&H00000000'},${c.weight >= 700 ? -1 : 0},0,0,0,100,100,0,0,${isBox ? 3 : 1},${isBox ? 8 : c.stroke},${style.bg === 'shadow' ? 2 : 0},8,20,20,20,1`;
  const at = t => fmtFull(t, '.').slice(1, -1);
  const ev = state.segs.map(s => {
    const L = SubRender.layout(cv, s, style, PX, PY, content);
    if (!L) return '';
    const parts = L.blocks.map(b => `{\\r${b.k === 'tgt' ? 'Tgt' : 'Src'}}` + b.lines.map(l => assEsc(l.t)).join('\\N'));
    return `Dialogue: 0,${at(s.start)},${at(s.end)},Tgt,,0,0,0,,{\\an8\\pos(${Math.round(L.cx)},${Math.round(L.y)})}${parts.join('\\N')}`;
  }).filter(Boolean);
  return `[Script Info]\nTitle: ${$('#projName').value}\nScriptType: v4.00+\nPlayResX: ${PX}\nPlayResY: ${PY}\nWrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n${st('Tgt', style.tgt)}\n${st('Src', style.src)}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n${ev.join('\n')}\n`;
}
function build(fmtK) {
  const segs = state.segs.filter(s => textFor(s).trim());
  if (fmtK === 'srt') return segs.map((s, i) => `${i + 1}\n${fmtFull(s.start)} --> ${fmtFull(s.end)}\n${textFor(s)}\n`).join('\n');
  if (fmtK === 'vtt') return 'WEBVTT\n\n' + segs.map((s, i) => `${i + 1}\n${fmtFull(s.start, '.')} --> ${fmtFull(s.end, '.')}\n${textFor(s)}\n`).join('\n');
  if (fmtK === 'txt') return segs.map(s => `[${fmtShort(s.start)}] ${textFor(s).replace(/\n/g, '\n        ')}`).join('\n');
  return buildASS();
}
function updPreview() { const txt = state.segs.length ? build(expFmt).split('\n').slice(0, 14).join('\n') + '\n…' : '（尚無字幕）'; $('#codePrev').textContent = txt; }
$('#dlSub').onclick = async () => {
  if (!state.segs.length) { toast('尚無字幕可匯出', 'info'); return; }
  try {
    const path = await targetPath('.' + expFmt); if (!path) return;
    const r = await api('POST', '/api/export/subtitle', { path, content: build(expFmt) });
    showDone(r.path, r.size, false); toast('字幕檔已儲存');
  } catch (e) { toast(e.message, 'warn', 5000); }
};
let exporting = false, expJob = null;
function expProgress(p, txt) { $('#expProg').hidden = false; $('#epFill').style.width = (p * 100).toFixed(1) + '%'; $('#epPct').textContent = Math.round(p * 100) + '%'; if (txt) $('#epTxt').textContent = txt; }
const canvasBlob = cv => new Promise(r => cv.toBlob(r, 'image/png'));
$('#dlVideo').onclick = async () => {
  if (exporting) { if (expJob) { try { await api('POST', `/api/jobs/${expJob}/cancel`); } catch (e) { } } else exporting = 'cancel'; return; }
  const mode = state.mode, med = project.media[mode];
  if (!med || !med.ready) { toast(mode === 'video' ? '請先上傳影片' : '請先上傳音訊', 'info'); return; }
  const soft = segVal('#burnSeg') === 'soft';
  const [W, H] = exportDims();
  let outPath;
  try { outPath = await targetPath(vidExt()); } catch (e) { toast(e.message, 'warn', 5000); return; }
  if (!outPath) return;
  exporting = true; $('#expDone').hidden = true; $('#dlVideoTxt').textContent = '取消輸出'; $('#dlVideo').classList.add('danger');
  pause();
  try {
    const pid = await ensureProject();
    const session = Math.random().toString(36).slice(2, 12).replace(/[^a-z0-9]/g, 'x');
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const g = cv.getContext('2d');
    let batch = new FormData(), nb = 0;
    const flush = async () => { if (!nb) return; batch.append('session', session); await uploadXHR(`/api/projects/${pid}/export/frames`, batch); batch = new FormData(); nb = 0; };
    const add = async (name) => { const b = await canvasBlob(cv); batch.append('files', b, name); nb++; if (nb >= 24) await flush(); };
    g.clearRect(0, 0, W, H); await add('blank.png');
    if (mode === 'audio') {
      if (project.media.image && !(bgImg.complete && bgImg.naturalWidth)) await new Promise(r => { bgImg.addEventListener('load', r, { once: true }); bgImg.addEventListener('error', r, { once: true }); });
      SubRender.drawBackground(g, W, H, project.media.image && bgImg.naturalWidth ? bgImg : null); await add('bg.png');
    }
    const cues = [];
    if (!soft) {
      const list = state.segs.filter(s => s.end > s.start);
      for (let i = 0; i < list.length; i++) {
        if (exporting === 'cancel') throw Object.assign(new Error('已取消'), { cancelled: true });
        const s = list[i];
        g.clearRect(0, 0, W, H);
        await SubRender.ensureFonts(s, style, expContent);
        const bb = SubRender.draw(g, s, style, W, H, expContent);
        if (!bb) continue;
        const name = `c${pad(i, 5)}.png`;
        await add(name);
        cues.push({ start: s.start, end: s.end, frame: name });
        if (i % 4 === 0) { expProgress(i / list.length * 0.3, `渲染字幕畫格 ${i + 1}/${list.length}`); await yieldUI(); }
      }
    }
    await flush();
    expProgress(0.3, '編碼影片中…');
    const meta = {
      mode, burn: soft ? 'soft' : 'hard', width: W, height: H, quality: segVal('#qSeg'), duration: state.duration,
      cues, viz: mode === 'audio' && state.opts.viz, name: outBase(), out_path: outPath,
      ass: soft ? buildASS(expContent, [W, H]) : '', sub_lang: expContent === 'src' ? (srcLangCode() || 'und') : state.tgtLang,
    };
    const d = await api('POST', `/api/projects/${pid}/export/video`, { session, meta });
    expJob = d.job;
    const res = await pollJob(d.job, p => expProgress(0.3 + p * 0.7, p < 0.06 ? '準備編碼…' : '編碼影片中…'));
    $('#expProg').hidden = true;
    showDone(res.path, res.size, true);
    toast('影片輸出完成', 'spark');
  } catch (e) {
    $('#expProg').hidden = true;
    if (!e.cancelled) toast(e.message, 'warn', 7000); else toast('已取消輸出', 'info');
  }
  exporting = false; expJob = null; $('#dlVideo').classList.remove('danger'); updExpHint();
};

/* ---------------- keyboard ---------------- */
document.addEventListener('keydown', e => {
  const typing = e.target.matches('input:not([type=range]),textarea,[contenteditable]');
  if ((e.ctrlKey || e.metaKey) && !typing) {
    if (e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); return; }
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'e') { e.preventDefault(); $('#exportBtn').click(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); saveNow(); return; }
  if (e.key === 'Escape') { $$('.modal-bg.show').forEach(m => m.classList.remove('show')); closePop(); if (typing) e.target.blur(); return; }
  if (typing || $('.modal-bg.show')) return;
  if (e.code === 'Space') { e.preventDefault(); toggle(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); seek(state.t - (e.shiftKey ? 5 : 1)); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); seek(state.t + (e.shiftKey ? 5 : 1)); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); jumpCue(-1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); jumpCue(1); }
  else if (e.key.toLowerCase() === 's' && !e.ctrlKey) splitAtPlayhead();
  else if (e.key.toLowerCase() === 'w' && !e.ctrlKey && !e.metaKey) $('#tlWide').click();
  else if ((e.key === 'Delete' || e.key === 'Backspace') && state.selId) cueAction('del', state.selId);
});
$('#undoBtn').onclick = undo; $('#redoBtn').onclick = redo;
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden' && dirty && project.id) { clearTimeout(saveTimer); saveNow(); } });
window.addEventListener('pagehide', () => {
  if (!dirty || !project.id) return;
  const blob = new Blob([JSON.stringify({ name: $('#projName').value, state: serial() })], { type: 'application/json' });
  navigator.sendBeacon(`/api/projects/${project.id}/save`, blob);
});

/* ---------------- boot ---------------- */
(async function boot() {
  $('#zoom').value = zoomToSlider(state.zoom);
  $$('input[type=range]').forEach(fillRange);
  syncStyleInputs(); syncLeftInputs();
  layoutStage(); renderList(); renderTimeline(); updStats(); setPlaying(false); updGenButtons();
  pollStatus(); loadSettingsUI();
  let last = null; try { last = localStorage.getItem('lastProject'); } catch (e) { }
  if (last) { try { await openProject(last); } catch (e) { try { localStorage.removeItem('lastProject'); } catch (x) { } } }
  document.fonts && document.fonts.ready.then(() => { refreshSegs(); bumpStyle(); });
  requestAnimationFrame(loop);
})();
