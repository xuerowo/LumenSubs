// node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../../web/subparse.js');

const enc = s => new TextEncoder().encode(s).buffer;

test('SRT: timings, tags, blank line inside a cue', () => {
  const srt = '﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i> there\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nFirst line\r\n\r\nstray second line\r\n\r\n3\r\n00:01:02,250 --> 00:01:03,000\r\n{\\an8}Top\r\n';
  const { format, cues } = P.parse(P.decode(enc(srt)), 'a.srt');
  assert.equal(format, 'srt');
  assert.deepEqual(cues.map(c => [c.start, c.end]), [[1, 2.5], [3, 4], [62.25, 63]]);
  assert.deepEqual(cues[0].lines, ['Hello there']);
  assert.deepEqual(cues[1].lines, ['First line', 'stray second line']);
  assert.deepEqual(cues[2].lines, ['Top']);
});

test('WebVTT: header, NOTE, settings, entities, short timestamps', () => {
  const vtt = 'WEBVTT - title\n\nNOTE a comment\n\nintro\n00:01.000 --> 00:02.000 align:start line:0\n<v Bob>Tom &amp; Jerry &lt;3</v>\n\n00:00:03.000 --> 00:00:04.000\n<c.yellow>Yes</c>\n';
  const { format, cues } = P.parse(vtt, 'a.vtt');
  assert.equal(format, 'vtt');
  assert.equal(cues.length, 2);
  assert.deepEqual(cues[0].lines, ['Tom & Jerry <3']);
  assert.equal(cues[1].start, 3);
});

test('ASS: custom Format order, commas in text, overrides, \\N', () => {
  const ass = '[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nComment: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,ignored\nDialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,{\\i1}Well, hello\\Nthere\nDialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,first\n';
  const { format, cues } = P.parse(ass, 'a.ass');
  assert.equal(format, 'ass');
  assert.deepEqual(cues.map(c => c.lines), [['first'], ['Well, hello', 'there']]);
  assert.equal(cues[1].start, 1.5);
});

test('ASS exported by LumenSubs keeps the two languages apart', () => {
  const ass = '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.50,0:00:01.80,Tgt,,0,0,0,,{\\an8\\pos(960,875)}{\\rTgt}很長的第一行\\N第二行\\N{\\rSrc}A long line\\Ncontinued\n';
  const { cues } = P.parse(ass, 'x.ass');
  assert.equal(P.guessMode(cues, 'zh-TW'), 'tgt-top');
  assert.deepEqual(P.toSegs(cues, 'tgt-top')[0], { start: 0.5, end: 1.8, src: 'A long line continued', tgt: '很長的第一行第二行' });
});

test('bilingual SRT: detection and assignment', () => {
  const srt = '1\n00:00:01,000 --> 00:00:02,000\n你好\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\n再見\nGoodbye\n';
  const { cues } = P.parse(srt, 'b.srt');
  assert.equal(P.guessMode(cues, 'zh-TW'), 'tgt-top');
  assert.equal(P.guessMode(cues, 'en'), 'src-top');
  assert.deepEqual(P.toSegs(cues, 'tgt-top')[0], { start: 1, end: 2, src: 'Hello', tgt: '你好' });
  assert.deepEqual(P.toSegs(cues, 'src')[1].src, '再見 Goodbye');
});

test('same-timed cues (one per language) are merged, overlaps trimmed', () => {
  const srt = '1\n00:00:01,000 --> 00:00:03,000\nHola\n\n2\n00:00:01,000 --> 00:00:03,000\nHello\n\n3\n00:00:02,500 --> 00:00:04,000\nNext\n';
  const { cues } = P.parse(srt, 'c.srt');
  assert.equal(cues.length, 2);
  assert.deepEqual(cues[0].lines, ['Hola', 'Hello']);
  assert.equal(cues[0].end, 2.5);
});

test('legacy encodings are detected', () => {
  const big5 = new Uint8Array([0xa7, 0x41, 0xa6, 0x6e]);            // 你好 in Big5
  assert.equal(P.decode(big5.buffer), '你好');
  const u16 = new Uint8Array([0xff, 0xfe, 0x41, 0x00, 0x42, 0x00]);
  assert.equal(P.decode(u16.buffer), 'AB');
});

test('garbage gives no cues', () => {
  assert.equal(P.parse('just some text\nwith no timings', 'x.srt').cues.length, 0);
});
