/* =========================================================
   LumenSubs — subtitle file export (SRT / WebVTT / ASS / TXT)
   Pure functions over the editor's cues, so the files users take away are
   covered by tests. Browser global `SubExport`, also loadable from Node.
   ========================================================= */
(function (global) {
  'use strict';
  const pad = (n, w = 2) => String(n).padStart(w, '0');

  function fmtFull(t, sep = ',') {
    const ms = Math.round(Math.max(0, t) * 1000);
    const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60;
    return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(ms % 1000, 3)}`;
  }
  function fmtShort(t) {
    t = Math.max(0, t || 0); const h = Math.floor(t / 3600);
    return (h ? h + ':' : '') + `${pad(Math.floor(t % 3600 / 60))}:${pad(Math.floor(t % 60))}`;
  }

  /* content: 'dual' | 'tgt' | 'src'; order: 'tgt-top' | 'src-top' */
  function textFor(s, content, order) {
    const T = s.tgt || '', S = s.src || '';
    if (content === 'tgt') return T;
    if (content === 'src') return S;
    return order === 'tgt-top' ? [T, S].filter(Boolean).join('\n') : [S, T].filter(Boolean).join('\n');
  }
  // a blank line or "-->" inside the text would break the cue structure
  const cueText = (s, content, order) => textFor(s, content, order).replace(/\r/g, '').replace(/[ \t]*\n[\s]*/g, '\n')
    .replace(/-->/g, '→').trim();
  const vttEsc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  function srt(segs, content, order) {
    return segs.filter(s => cueText(s, content, order)).map((s, i) =>
      `${i + 1}\n${fmtFull(s.start)} --> ${fmtFull(s.end)}\n${cueText(s, content, order)}\n`).join('\n');
  }
  function vtt(segs, content, order) {
    return 'WEBVTT\n\n' + segs.filter(s => cueText(s, content, order)).map((s, i) =>
      `${i + 1}\n${fmtFull(s.start, '.')} --> ${fmtFull(s.end, '.')}\n${vttEsc(cueText(s, content, order))}\n`).join('\n');
  }
  function txt(segs, content, order) {
    return segs.filter(s => cueText(s, content, order)).map(s =>
      `[${fmtShort(s.start)}] ${textFor(s, content, order).replace(/\n/g, '\n        ')}`).join('\n');
  }

  /* #rrggbb (+ transparency 0‥1) → ASS &HAABBGGRR */
  function assColor(hex, a = 0) {
    const h = String(hex).replace('#', '').padEnd(6, '0');
    return `&H${pad(Math.round(a * 255).toString(16), 2)}${h.slice(4, 6)}${h.slice(2, 4)}${h.slice(0, 2)}`.toUpperCase();
  }
  // ASS has no escape for "\" or braces: swap in look-alike full-width characters
  const assEsc = t => String(t).replace(/\\/g, '＼').replace(/\{/g, '｛').replace(/\}/g, '｝');
  function assTime(t) {
    const cs = Math.round(Math.max(0, t) * 100);
    return `${Math.floor(cs / 360000)}:${pad(Math.floor(cs / 6000) % 60)}:${pad(Math.floor(cs / 100) % 60)}.${pad(cs % 100)}`;
  }

  /* opts: {content, style, title, W, H, layout(cue, PX, PY, content) → {blocks:[{k, lines:[{t}]}], cx, y} | null,
            fontSize(trackStyle) → ASS font size} */
  function ass(segs, opts) {
    const { style, W, H } = opts;
    const k = 1080 / Math.min(W, H);
    const PX = Math.round(W * k), PY = Math.round(H * k);
    const isBox = style.bg === 'box';
    const st = (n, c) => `Style: ${n},${c.font},${opts.fontSize(c)},${assColor(c.color)},&H000000FF,${isBox ? assColor(style.boxColor, 1 - style.boxOpacity) : assColor(c.strokeColor)},${style.bg === 'shadow' ? '&H59230C00' : '&H00000000'},${c.weight >= 600 ? -1 : 0},0,0,0,100,100,0,0,${isBox ? 3 : 1},${isBox ? 8 : c.stroke},${style.bg === 'shadow' ? 2 : 0},8,20,20,20,1`;
    const ev = segs.map(s => {
      const L = opts.layout(s, PX, PY, opts.content);
      if (!L) return '';
      const parts = L.blocks.map(b => `{\\r${b.k === 'tgt' ? 'Tgt' : 'Src'}}` + b.lines.map(l => assEsc(l.t)).join('\\N'));
      return `Dialogue: 0,${assTime(s.start)},${assTime(s.end)},Tgt,,0,0,0,,{\\an8\\pos(${Math.round(L.cx)},${Math.round(L.y)})}${parts.join('\\N')}`;
    }).filter(Boolean);
    const title = String(opts.title || '').replace(/[\r\n]+/g, ' ');
    return `[Script Info]\nTitle: ${title}\nScriptType: v4.00+\nPlayResX: ${PX}\nPlayResY: ${PY}\nWrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n${st('Tgt', style.tgt)}\n${st('Src', style.src)}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n${ev.join('\n')}\n`;
  }

  const api = { fmtFull, fmtShort, textFor, cueText, srt, vtt, txt, ass, assColor, assEsc, assTime };
  if (typeof module === 'object' && module.exports) module.exports = api; else global.SubExport = api;
})(typeof window !== 'undefined' ? window : globalThis);
