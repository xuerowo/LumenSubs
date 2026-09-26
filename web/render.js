/* =========================================================
   LumenSubs — subtitle renderer (shared by live preview and export)
   One code path draws subtitles for the preview canvas and for the
   full-resolution PNG frames that FFmpeg burns into the video, so the
   exported video is pixel-identical to what the user sees.
   ========================================================= */
(function (global) {
  'use strict';
  const FALLBACK = ['Noto Sans TC', 'Noto Sans SC', 'Noto Sans JP', 'Noto Sans KR', 'Noto Sans Thai', 'Noto Sans Arabic',
    'Noto Sans Hebrew', 'Noto Sans Devanagari', 'Inter', 'Microsoft JhengHei', 'PingFang TC', 'sans-serif'];
  const stack = font => [font, ...FALLBACK.filter(f => f !== font)].map(f => f === 'sans-serif' ? f : `"${f}"`).join(',');

  // kinsoku: characters that must not start a line / must not end a line
  const NO_START = new Set('、。，．,.!?！？:;：；)）]」』】〉》〕｝}ー〜～…‥ゃゅょっぁぃぅぇぉャュョッァィゥェォヮゎ々゛゜’”%％°℃'.split(''));
  const NO_END = new Set('(（[「『【〈《〔｛{‘“'.split(''));
  const RTL_RE = /[֐-ࣿיִ-﷿ﹰ-﻿]/;
  const CJK_RE = /[぀-ヿ㐀-鿿豈-﫿가-힯]/;

  let segCache = null;
  function segmenter() {
    if (segCache === null) {
      try { segCache = new Intl.Segmenter(undefined, { granularity: 'word' }); } catch (e) { segCache = false; }
    }
    return segCache;
  }

  /* split text into unbreakable units (break opportunities between units) */
  function units(text) {
    const out = [];
    const sg = segmenter();
    let parts;
    if (sg) parts = Array.from(sg.segment(text), s => s.segment);
    else parts = text.match(/\s+|[぀-ヿ㐀-鿿豈-﫿]|[^\s぀-ヿ㐀-鿿豈-﫿]+/g) || [];
    // CJK words from the segmenter can be long; allow breaks between CJK chars
    // except where kinsoku forbids
    const flat = [];
    parts.forEach(p => {
      if (CJK_RE.test(p) && p.length > 4 && !/\s/.test(p)) { for (const ch of p) flat.push(ch); }
      else flat.push(p);
    });
    flat.forEach(p => {
      const first = p[0], lastU = out[out.length - 1];
      if (lastU !== undefined && !/^\s+$/.test(p) && (NO_START.has(first) || NO_END.has(lastU[lastU.length - 1]))) {
        out[out.length - 1] = lastU + p;
      } else out.push(p);
    });
    return out;
  }

  const mCache = new Map();
  function measure(ctx, font, s) {
    const k = font + '\u0001' + s;
    let v = mCache.get(k);
    if (v === undefined) { ctx.font = font; v = ctx.measureText(s).width; if (mCache.size > 20000) mCache.clear(); mCache.set(k, v); }
    return v;
  }

  function greedy(ctx, font, us, maxW) {
    const lines = [];
    let cur = '', curW = 0;
    const push = () => { const t = cur.replace(/\s+$/, ''); if (t) lines.push(t); cur = ''; curW = 0; };
    for (let u of us) {
      if (!cur && /^\s+$/.test(u)) continue;
      const w = measure(ctx, font, u);
      if (curW + w <= maxW || !cur) {
        if (!cur && w > maxW) {                       // single unit wider than the line: hard-break by chars
          let piece = '';
          for (const ch of u) {
            if (piece && measure(ctx, font, piece + ch) > maxW) { lines.push(piece); piece = ''; }
            piece += ch;
          }
          cur = piece; curW = measure(ctx, font, piece);
          continue;
        }
        cur += u; curW += w;
      } else { push(); if (/^\s+$/.test(u)) continue; cur = u; curW = w; }
    }
    push();
    return lines;
  }

  function wrap(ctx, font, text, maxW, balance) {
    const out = [];
    String(text).split(/\r?\n/).forEach(para => {
      para = para.trim(); if (!para) return;
      const us = units(para);
      let lines = greedy(ctx, font, us, maxW);
      if (balance && lines.length > 1) {
        // narrowest width that keeps the same number of lines → even lines
        let lo = maxW * 0.35, hi = maxW;
        for (let i = 0; i < 14; i++) {
          const mid = (lo + hi) / 2;
          if (greedy(ctx, font, us, mid).length <= lines.length) hi = mid; else lo = mid;
        }
        lines = greedy(ctx, font, us, hi + 0.5);
      }
      out.push(...lines);
    });
    return out;
  }

  function hexA(hex, a) {
    const n = parseInt(String(hex).replace('#', '').padEnd(6, '0').slice(0, 6), 16);
    return `rgba(${n >> 16},${n >> 8 & 255},${n & 255},${a})`;
  }

  function blocksFor(cue, style, display) {
    const d = display || style.display;
    const T = d !== 'src' && cue.tgt && cue.tgt.trim() ? { k: 'tgt', text: cue.tgt } : null;
    const S = d !== 'tgt' && cue.src && cue.src.trim() ? { k: 'src', text: cue.src } : null;
    return (style.order === 'tgt-top' ? [T, S] : [S, T]).filter(Boolean);
  }

  function fontOf(c, sc) { return `${c.weight} ${(c.size * sc).toFixed(2)}px ${stack(c.font)}`; }

  function layout(ctx, cue, style, W, H, display) {
    const sc = Math.min(W, H) / 1080;
    const isBox = style.bg === 'box';
    const padX = isBox ? 20 * sc : 0, padY = isBox ? 6 * sc : 0;
    const maxW = W * style.maxW / 100 - padX * 2;
    const blocks = blocksFor(cue, style, display).map(b => {
      const c = style[b.k];
      const font = fontOf(c, sc);
      const lines = wrap(ctx, font, b.text, Math.max(40, maxW), style.balance !== false).map(t => ({ t, w: measure(ctx, font, t), rtl: RTL_RE.test(t) }));
      const lh = c.size * sc * 1.32;
      return { k: b.k, c, font, lines, lh, sc };
    }).filter(b => b.lines.length);
    if (!blocks.length) return null;
    const gap = style.gap * sc;
    let h = 0, w = 0;
    blocks.forEach((b, i) => { h += b.lines.length * (b.lh + padY * 2) + (i ? gap : 0); b.lines.forEach(l => { w = Math.max(w, l.w + padX * 2); }); });
    const margin = W * 0.02;
    let cx = W * style.posX / 100;
    cx = Math.min(W - margin - w / 2, Math.max(margin + w / 2, cx));
    const a = style.posY / 100;
    let top = H * a - h * a;
    top = Math.min(H - h - H * 0.01, Math.max(H * 0.01, top));
    return { blocks, x: cx - w / 2, y: top, w, h, cx, padX, padY, gap, sc };
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, h / 2, w / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  /* draw one cue; returns its bounding box (canvas px) or null */
  function draw(ctx, cue, style, W, H, display) {
    if (!cue) return null;
    const L = layout(ctx, cue, style, W, H, display);
    if (!L) return null;
    const { sc, padX, padY } = L;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.miterLimit = 2;
    let y = L.y;
    L.blocks.forEach((b, bi) => {
      if (bi) y += L.gap;
      const c = b.c;
      ctx.font = b.font;
      b.lines.forEach(line => {
        const rowH = b.lh + padY * 2;
        const cy = y + rowH / 2 + b.lh * 0.02;
        ctx.direction = line.rtl ? 'rtl' : 'ltr';
        if (style.bg === 'box') {
          ctx.fillStyle = hexA(style.boxColor, style.boxOpacity);
          roundRect(ctx, L.cx - line.w / 2 - padX, y, line.w + padX * 2, rowH, style.radius * sc);
          ctx.fill();
        }
        const sw = (c.stroke || 0) * sc * 2;
        if (style.bg === 'shadow') {
          ctx.save();
          ctx.shadowColor = 'rgba(0,12,35,.65)';
          ctx.shadowOffsetX = 2 * sc; ctx.shadowOffsetY = 5 * sc; ctx.shadowBlur = 18 * sc;
          if (sw > 0) { ctx.strokeStyle = c.strokeColor; ctx.lineWidth = sw; ctx.strokeText(line.t, L.cx, cy); }
          else { ctx.fillStyle = c.color; ctx.fillText(line.t, L.cx, cy); }
          ctx.restore();
        }
        if (sw > 0) { ctx.strokeStyle = c.strokeColor; ctx.lineWidth = sw; ctx.strokeText(line.t, L.cx, cy); }
        ctx.fillStyle = c.color;
        ctx.fillText(line.t, L.cx, cy);
        y += rowH;
      });
    });
    ctx.restore();
    const pad = ((style.bg === 'shadow' ? 20 : 4) + 10) * sc;
    return { x: L.x - pad, y: L.y - pad, w: L.w + pad * 2, h: L.h + pad * 2, tx: L.x, ty: L.y, tw: L.w, th: L.h };
  }

  /* make sure every glyph we are about to draw is available */
  const loaded = new Set();
  function ensureFonts(cue, style, display) {
    const ps = [];
    blocksFor(cue, style, display).forEach(b => {
      const c = style[b.k];
      const key = c.weight + '|' + c.font + '|' + b.text;
      if (loaded.has(key)) return;
      const f = `${c.weight} 40px ${stack(c.font)}`;
      ps.push(document.fonts.load(f, b.text).then(() => { loaded.add(key); if (loaded.size > 5000) loaded.clear(); }).catch(() => { }));
    });
    return Promise.all(ps);
  }

  /* audio-mode background: image (cover) + veil, or the default gradient */
  function drawBackground(ctx, W, H, img) {
    ctx.save();
    if (img && img.naturalWidth) {
      const r = Math.max(W / img.naturalWidth, H / img.naturalHeight);
      const w = img.naturalWidth * r, h = img.naturalHeight * r;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, (W - w) / 2, (H - h) / 2, w, h);
    } else {
      const lg = ctx.createLinearGradient(0, 0, W, H);
      lg.addColorStop(0, '#123a73'); lg.addColorStop(1, '#0b1e44');
      ctx.fillStyle = lg; ctx.fillRect(0, 0, W, H);
      const blob = (cx, cy, rx, ry, color) => {
        ctx.save(); ctx.translate(cx, cy); ctx.scale(rx, ry);
        const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 1);
        g.addColorStop(0, color); g.addColorStop(0.6, hexA(color, 0)); g.addColorStop(1, hexA(color, 0));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, 1, 0, Math.PI * 2); ctx.fill(); ctx.restore();
      };
      blob(W * .2, H * .2, W * .8, H * .9, '#7cc4ff');
      blob(W * .85, H * .3, W * .7, H * .8, '#a88bff');
      blob(W * .5, H * 1.0, W * .9, H * .9, '#1fd1e8');
    }
    const v = ctx.createLinearGradient(0, 0, 0, H);
    v.addColorStop(0, 'rgba(0,10,30,.05)'); v.addColorStop(1, 'rgba(0,10,30,.35)');
    ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
    ctx.restore();
  }

  global.SubRender = { draw, layout, ensureFonts, drawBackground, stack, hexA, blocksFor };
})(window);
