/* =========================================================
   LumenSubs — subtitle file import (SRT / WebVTT / ASS / SSA)
   Parses a subtitle file into timed cues and maps each cue's lines onto
   the editor's source / translation fields. Browser global `SubParse`,
   also loadable from Node for the tests.
   ========================================================= */
(function (global) {
  'use strict';
  const CJK = /[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF\uAC00-\uD7AF]/;

  /* bytes → text: BOM, then UTF-8, then common legacy CJK encodings */
  function decode(buf) {
    const b = new Uint8Array(buf);
    if (b[0] === 0xFF && b[1] === 0xFE) return new TextDecoder('utf-16le').decode(b);
    if (b[0] === 0xFE && b[1] === 0xFF) return new TextDecoder('utf-16be').decode(b);
    for (const enc of ['utf-8', 'big5', 'shift_jis', 'gb18030']) {
      try { return new TextDecoder(enc, { fatal: true }).decode(b); } catch (e) { }
    }
    return new TextDecoder('windows-1252').decode(b);
  }

  /* "01:02:03,456" · "02:03.456" · "1:02:03.45" → seconds */
  function tc(s) {
    const m = String(s || '').trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$/);
    if (!m) return NaN;
    return (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]) + (m[4] ? Number('0.' + m[4]) : 0);
  }

  const ENT = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&nbsp;': ' ', '&lrm;': '\u200E', '&rlm;': '\u200F', '&quot;': '"', '&#39;': "'" };
  function cleanLine(l, vtt) {
    l = l.replace(/<[^>]*>/g, '').replace(/\{\\[^}]*\}/g, '');       // <i>, <font>, <c.x>, {\an8}
    if (vtt) l = l.replace(/&(amp|lt|gt|nbsp|lrm|rlm|quot|#39);/g, m => ENT[m]);
    return l.trim();
  }

  /* SRT and WebVTT: blocks separated by blank lines, one "-->" line each */
  function parseBlocks(text, vtt) {
    const out = [];
    const blocks = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split(/\n[ \t]*\n+/);
    for (const blk of blocks) {
      const lines = blk.split('\n');
      const i = lines.findIndex(l => l.includes('-->'));
      if (i < 0) {
        // text continuing after a stray blank line belongs to the previous cue
        const txt = lines.map(l => cleanLine(l, vtt)).filter(Boolean);
        const prev = out[out.length - 1];
        if (prev && txt.length && !(txt.length === 1 && /^\d+$/.test(txt[0])) && !/^(WEBVTT|NOTE|STYLE|REGION)\b/.test(lines[0])) prev.lines.push(...txt);
        continue;
      }
      if (vtt && /^(NOTE|STYLE|REGION)\b/.test(lines[0])) continue;
      const [a, rest] = lines[i].split('-->');
      const start = tc(a), end = tc((rest || '').trim().split(/\s+/)[0]);
      if (!(end > start)) continue;
      out.push({ start, end, lines: lines.slice(i + 1).map(l => cleanLine(l, vtt)).filter(Boolean) });
    }
    return out;
  }

  const assText = t => t.replace(/\{[^}]*\}/g, '').replace(/\\[Nn]/g, '\n').replace(/\\h/g, ' ');
  const splitLines = t => t.split('\n').map(s => s.trim()).filter(Boolean);

  function parseASS(text) {
    const out = [];
    let fmt = null, inEvents = false;
    for (const raw of text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n')) {
      const l = raw.trim();
      if (/^\[.*\]$/.test(l)) { inEvents = /^\[events\]$/i.test(l); continue; }
      if (!inEvents) continue;
      if (/^format\s*:/i.test(l)) { fmt = l.slice(l.indexOf(':') + 1).split(',').map(s => s.trim().toLowerCase()); continue; }
      if (!/^dialogue\s*:/i.test(l)) continue;
      const f = fmt || ['layer', 'start', 'end', 'style', 'name', 'marginl', 'marginr', 'marginv', 'effect', 'text'];
      const parts = []; let rest = l.slice(l.indexOf(':') + 1);
      for (let k = 0; k < f.length - 1; k++) { const j = rest.indexOf(','); if (j < 0) break; parts.push(rest.slice(0, j)); rest = rest.slice(j + 1); }
      parts.push(rest);
      const get = n => (parts[f.indexOf(n)] || '').trim();
      const start = tc(get('start')), end = tc(get('end'));
      if (!(end > start)) continue;
      const txt = f.indexOf('text') >= 0 ? (parts[f.indexOf('text')] || '') : '';
      const cue = { start, end, lines: splitLines(assText(txt)) };
      // LumenSubs' own ASS export marks the two languages with {\rTgt} / {\rSrc}
      const seg = txt.split(/\{[^}]*?\\r(Tgt|Src)\b[^}]*\}/);
      if (seg.length > 1) {
        cue.tagged = {};
        for (let k = 1; k < seg.length; k += 2) cue.tagged[seg[k].toLowerCase()] = joinLines(splitLines(assText(seg[k + 1] || '')));
      }
      out.push(cue);
    }
    return out;
  }

  function joinLines(ls) {
    return ls.reduce((a, l) => !a ? l : a + (CJK.test(a.slice(-1)) && CJK.test(l[0]) ? '' : ' ') + l, '').trim();
  }

  /* sort, merge cues that share their timing (one cue per language in some
     bilingual files) and make them non-overlapping, as the editor expects */
  function normalize(cues) {
    cues.sort((a, b) => a.start - b.start || a.end - b.end);
    const out = [];
    for (const c of cues) {
      const p = out[out.length - 1];
      if (p && Math.abs(p.start - c.start) < 0.02 && Math.abs(p.end - c.end) < 0.02) {
        p.lines.push(...c.lines);
        if (c.tagged) p.tagged = Object.assign(p.tagged || {}, c.tagged);
        continue;
      }
      out.push({ ...c, lines: [...c.lines] });
    }
    for (let i = 0; i < out.length - 1; i++) if (out[i].end > out[i + 1].start) out[i].end = out[i + 1].start;
    return out.filter(c => c.end - c.start >= 0.05 && (c.lines.length || c.tagged));
  }

  function parse(text, name) {
    const ext = ((String(name || '').match(/\.(\w+)$/) || [])[1] || '').toLowerCase();
    const format = ext === 'ass' || ext === 'ssa' || /^\s*\[script info\]/i.test(text) ? 'ass'
      : ext === 'vtt' || /^\uFEFF?WEBVTT/.test(text) ? 'vtt' : 'srt';
    const raw = format === 'ass' ? parseASS(text) : parseBlocks(text, format === 'vtt');
    return { format, cues: normalize(raw) };
  }

  const cls = l => CJK.test(l) ? 'cjk' : 'other';
  /* two languages in one cue → [top, bottom] */
  function splitDual(lines) {
    if (lines.length <= 1) return [lines.join(' '), ''];
    if (lines.length === 2) return [lines[0], lines[1]];
    const groups = [];
    lines.forEach(l => { const k = cls(l), g = groups[groups.length - 1]; if (g && g.k === k) g.ls.push(l); else groups.push({ k, ls: [l] }); });
    if (groups.length === 2) return [joinLines(groups[0].ls), joinLines(groups[1].ls)];
    const h = Math.ceil(lines.length / 2);
    return [joinLines(lines.slice(0, h)), joinLines(lines.slice(h))];
  }

  /* mode: 'src' | 'tgt' (whole text into one field) · 'tgt-top' | 'src-top' (bilingual) */
  function toSegs(cues, mode) {
    return cues.map(c => {
      let src = '', tgt = '';
      const dual = mode === 'tgt-top' || mode === 'src-top';
      if (dual && c.tagged && (c.tagged.tgt || c.tagged.src)) { src = c.tagged.src || ''; tgt = c.tagged.tgt || ''; }
      else if (mode === 'tgt') tgt = joinLines(c.lines);
      else if (!dual) src = joinLines(c.lines);
      else { const [a, b] = splitDual(c.lines); if (mode === 'tgt-top') { tgt = a; src = b; } else { src = a; tgt = b; } }
      return { start: +c.start.toFixed(3), end: +c.end.toFixed(3), src, tgt };
    });
  }

  /* best first guess for the import mode */
  function guessMode(cues, tgtLang) {
    if (cues.some(c => c.tagged && c.tagged.tgt && c.tagged.src)) return 'tgt-top';
    const mixed = cues.filter(c => { if (c.lines.length < 2) return false; const [a, b] = splitDual(c.lines); return a && b && cls(a) !== cls(b); });
    if (cues.length && mixed.length >= cues.length * 0.5) {
      const tgtCjk = /^(zh|yue|ja|ko)/.test(tgtLang || '');
      const topCjk = mixed.filter(c => CJK.test(splitDual(c.lines)[0])).length >= mixed.length / 2;
      return topCjk === tgtCjk ? 'tgt-top' : 'src-top';
    }
    return 'src';
  }

  const api = { decode, parse, toSegs, guessMode, tc };
  if (typeof module === 'object' && module.exports) module.exports = api; else global.SubParse = api;
})(typeof window !== 'undefined' ? window : globalThis);
