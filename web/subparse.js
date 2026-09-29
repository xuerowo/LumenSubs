/* =========================================================
   LumenSubs — subtitle file import (SRT / WebVTT / ASS / SSA)
   Parses a subtitle file into timed cues and maps each cue's lines onto
   the editor's source / translation fields. Browser global `SubParse`,
   also loadable from Node for the tests.
   ========================================================= */
(function (global) {
  'use strict';
  const CJK = /[぀-ヿ㐀-鿿豈-﫿가-힯]/;

  /* ---------------- encoding ---------------- */
  // the ~500 most frequent Chinese characters (Traditional and Simplified):
  // text decoded with the wrong legacy encoding is made of rare characters
  const COMMON_HAN = new Set(('的一是不了人我在有他這这个個們们中來来上大為为和國国地到以說说時时要就出會会可也你對对生能而子那得於于着著下自之年過过發发後后作裡里用道行所然家種种事成方多經经麼么去法學学如都同現现當当沒没動动面起看定天分還还進进好小部其些主樣样理心她本前開开但因只從从想實实日軍军者意無无力它與与長长把機机十民第公此已工使情明性知全三又關关點点正業业外將将兩两高間间由問问很最重並并物手應应戰战向頭头文體体政美相見见被利什二等產产或新己制身果加西斯月話话合回特代內内信表化老給给世位次度門门任常先海通教兒儿原東东聲声提立及比員员解水名真論论處处走義义各入幾几口認认條条平系氣气題题活爾尔更別别打女變变四神總总何電电數数安少報报才結结反受目太量再感建務务做接必場场件計计管期市直德資资命山金指克許许統统區区保至隊队形社便空決决治展馬马科司五基眼書书非則则聽听白卻却界達达光放強强即像難难且權权思王象完設设式色路記记南品住告類类求據据程北邊边死張张該该交規规萬万取拉格望覺觉術术領领共確确傳传師师觀观清今切院讓让識识候帶带導导爭争運运笑飛飞風风步改收根干造言聯联持組组每濟济車车親亲極极林服快辦办議议往元英士證证近失轉转夫令準准布始怎呢存未遠远叫台單单影具羅罗字愛爱擊击流備备兵連连調调深商算質质團团集百需價价花黨党華华城石級级整府離离況况亞亚請请技際际約约示復复病息究線线似官火斷断精滿满支視视消越器容照須须九增研寫写稱称企八功嗎吗包片史委乎查輕轻易早曾除農农找裝装廣广顯显吧阿李標标談谈吃圖图念六引歷历首醫医局突專专費费號号盡尽另周較较注語语仍球今天們们買买東东西超市起嗎吗謝谢歡欢迎喜聽听見见朋友吃飯饭走吧啊呀哦嗯喂').split(''));
  const KANA = /[぀-ヿ]/, HANGUL = /[가-힯]/, CYRILLIC = /[Ѐ-ӿ]/, LATIN1 = /[À-ÿ]/;
  const LEGACY = ['big5', 'gb18030', 'shift_jis', 'euc-kr', 'windows-1251', 'windows-1252'];
  const ENC_LABEL = { 'utf-8': 'UTF-8', 'utf-16le': 'UTF-16', 'utf-16be': 'UTF-16', big5: 'Big5（繁中）', gb18030: 'GBK（簡中）',
    shift_jis: 'Shift_JIS（日文）', 'euc-kr': 'EUC-KR（韓文）', 'windows-1251': 'Windows-1251（俄文等）', 'windows-1252': 'Windows-1252（西歐）' };

  /* how plausible `text` is as a subtitle in the language `enc` is used for (0‥1) */
  function plausibility(text, enc) {
    let n = 0, good = 0;
    for (const ch of text) {
      if (ch.charCodeAt(0) < 0x80) continue;
      n++;
      if (enc === 'big5' || enc === 'gb18030') { if (COMMON_HAN.has(ch) || /[，。！？、：；「」『』（）…—]/.test(ch)) good++; }
      else if (enc === 'shift_jis') { if (KANA.test(ch) || COMMON_HAN.has(ch) || /[、。！？「」…ー]/.test(ch)) good++; }
      else if (enc === 'euc-kr') { if (HANGUL.test(ch)) good++; }
      else if (enc === 'windows-1251') { if (CYRILLIC.test(ch)) good++; }
      else if (LATIN1.test(ch) || /[‘’“”–—…€]/.test(ch)) good++;
    }
    if (!n) return 0;
    let score = good / n;
    if (enc === 'windows-1251') {
      // Western text read as Cyrillic mixes both alphabets inside words ("Йtй"); real Russian does not
      const words = text.match(/[A-Za-zЀ-ӿ]+/g) || [];
      const mixed = words.filter(w => /[A-Za-z]/.test(w) && /[Ѐ-ӿ]/.test(w)).length;
      if (words.length) score *= 1 - mixed / words.length;
    }
    return score;
  }

  function utf16Guess(b) {
    // text in UTF-16 without a BOM: every other byte of the (mostly ASCII) timing lines is zero
    const n = Math.min(b.length, 4000);
    let even = 0, odd = 0;
    for (let i = 0; i < n; i++) if (!b[i]) (i % 2 ? odd++ : even++);
    if (odd > n * 0.3 && even < n * 0.02) return 'utf-16le';
    if (even > n * 0.3 && odd < n * 0.02) return 'utf-16be';
    return null;
  }

  /* bytes → {text, encoding}. `enc` forces an encoding; otherwise BOM,
     UTF-16, UTF-8, and among the legacy encodings that decode without
     errors, the one whose result reads most like real text. */
  function decodeInfo(buf, enc) {
    const b = new Uint8Array(buf);
    if (enc) return { text: new TextDecoder(enc).decode(b), encoding: enc };
    if (b[0] === 0xFF && b[1] === 0xFE) return { text: new TextDecoder('utf-16le').decode(b), encoding: 'utf-16le' };
    if (b[0] === 0xFE && b[1] === 0xFF) return { text: new TextDecoder('utf-16be').decode(b), encoding: 'utf-16be' };
    const u16 = utf16Guess(b);
    if (u16) return { text: new TextDecoder(u16).decode(b), encoding: u16 };
    try { return { text: new TextDecoder('utf-8', { fatal: true }).decode(b), encoding: 'utf-8' }; } catch (e) { }
    let best = null;
    LEGACY.forEach((e, order) => {
      let text;
      try { text = new TextDecoder(e, { fatal: true }).decode(b); } catch (x) { return; }
      const score = plausibility(text, e) - order * 1e-3;          // ties: earlier (more common) encoding wins
      if (!best || score > best.score) best = { text, encoding: e, score };
    });
    return best ? { text: best.text, encoding: best.encoding } : { text: new TextDecoder('windows-1252').decode(b), encoding: 'windows-1252' };
  }
  const decode = (buf, enc) => decodeInfo(buf, enc).text;

  /* "01:02:03,456" · "02:03.456" · "1:02:03.45" → seconds */
  function tc(s) {
    const m = String(s || '').trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$/);
    if (!m) return NaN;
    return (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]) + (m[4] ? Number('0.' + m[4]) : 0);
  }

  const ENT = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&nbsp;': ' ', '&lrm;': '‎', '&rlm;': '‏', '&quot;': '"', '&#39;': "'" };
  // formatting tags only — "a < b and c > d" is text, not a tag
  const TAG = /<\/?(?:i|b|u|s|font|c|v|lang|ruby|rt|span)(?:[.\s][^<>]*)?>|<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}>/gi;
  function cleanLine(l, vtt) {
    l = l.replace(TAG, '').replace(/\{\\[^}]*\}/g, '');       // <i>, <font>, <c.x>, <00:01.000>, {\an8}
    if (vtt) l = l.replace(/&(amp|lt|gt|nbsp|lrm|rlm|quot|#39);/g, m => ENT[m]);
    return l.trim();
  }

  /* SRT and WebVTT: blocks separated by blank lines, one "-->" line per cue
     (several when a blank line is missing between cues) */
  function parseBlocks(text, vtt) {
    const out = [];
    const blocks = text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').split(/\n[ \t]*\n+/);
    for (const blk of blocks) {
      const lines = blk.split('\n');
      const heads = lines.map((l, i) => l.includes('-->') ? i : -1).filter(i => i >= 0);
      if (!heads.length) {
        // text continuing after a stray blank line belongs to the previous cue;
        // a lone number is the next cue's index only if it continues the numbering
        const txt = lines.map(l => cleanLine(l, vtt)).filter(Boolean);
        const prev = out[out.length - 1];
        const isIndex = txt.length === 1 && /^\d+$/.test(txt[0]) && +txt[0] === out.length + 1;
        if (prev && txt.length && !isIndex && !/^(WEBVTT|NOTE|STYLE|REGION)\b/.test(lines[0])) prev.lines.push(...txt);
        continue;
      }
      if (vtt && /^(NOTE|STYLE|REGION)\b/.test(lines[0])) continue;
      heads.forEach((h, k) => {
        const next = k + 1 < heads.length ? heads[k + 1] : lines.length;
        let body = lines.slice(h + 1, next);
        if (k + 1 < heads.length && body.length && /^\s*\d+\s*$/.test(body[body.length - 1])) body = body.slice(0, -1);
        const [a, rest] = lines[h].split('-->');
        const start = tc(a), end = tc((rest || '').trim().split(/\s+/)[0]);
        if (!(end > start)) return;
        out.push({ start, end, lines: body.map(l => cleanLine(l, vtt)).filter(Boolean) });
      });
    }
    return out;
  }

  // drawing mode ({\p1} … {\p0}) holds vector shapes, not text
  const assText = t => t.replace(/\{[^}]*\\p[1-9][^}]*\}[^{]*/g, '').replace(/\{[^}]*\}/g, '').replace(/\\[Nn]/g, '\n').replace(/\\h/g, ' ');
  const splitLines = t => t.split('\n').map(s => s.trim()).filter(Boolean);

  function parseASS(text) {
    const out = [];
    let fmt = null, inEvents = false;
    for (const raw of text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').split('\n')) {
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
      if (cue.lines.length || cue.tagged) out.push(cue);
    }
    return out;
  }

  function joinLines(ls) {
    return ls.reduce((a, l) => !a ? l : a + (CJK.test(a.slice(-1)) && CJK.test(l[0]) ? '' : ' ') + l, '').trim();
  }

  /* sort, merge cues that start together (one cue per language or speaker in
     some files) and make them non-overlapping, as the editor expects.
     Returns {cues, merged, trimmed, dropped} so the app can tell the user. */
  function normalize(cues) {
    cues.sort((a, b) => a.start - b.start || a.end - b.end);
    const out = [];
    let merged = 0, trimmed = 0, dropped = 0;
    for (const c of cues) {
      const p = out[out.length - 1];
      if (p && Math.abs(p.start - c.start) < 0.02) {
        if (Math.abs(p.end - c.end) >= 0.02) merged++;       // two speakers, not one cue per language
        p.lines.push(...c.lines);
        p.end = Math.max(p.end, c.end);
        if (c.tagged) p.tagged = Object.assign(p.tagged || {}, c.tagged);
        continue;
      }
      out.push({ ...c, lines: [...c.lines] });
    }
    for (let i = 0; i < out.length - 1; i++) if (out[i].end > out[i + 1].start + 0.001) { out[i].end = out[i + 1].start; trimmed++; }
    const kept = out.filter(c => c.end - c.start >= 0.05 && (c.lines.length || c.tagged));
    dropped = out.length - kept.length;
    return { cues: kept, merged, trimmed, dropped };
  }

  function parse(text, name) {
    const ext = ((String(name || '').match(/\.(\w+)$/) || [])[1] || '').toLowerCase();
    const format = ext === 'ass' || ext === 'ssa' || /^\s*\[script info\]/i.test(text) ? 'ass'
      : ext === 'vtt' || /^﻿?WEBVTT/.test(text) ? 'vtt' : 'srt';
    const raw = format === 'ass' ? parseASS(text) : parseBlocks(text, format === 'vtt');
    const n = normalize(raw);
    return { format, cues: n.cues, stats: { merged: n.merged, trimmed: n.trimmed, dropped: n.dropped } };
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

  const api = { decode, decodeInfo, parse, toSegs, guessMode, tc, ENC_LABEL };
  if (typeof module === 'object' && module.exports) module.exports = api; else global.SubParse = api;
})(typeof window !== 'undefined' ? window : globalThis);
