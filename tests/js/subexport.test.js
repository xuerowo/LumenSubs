// node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const E = require('../../web/subexport.js');

const segs = [
  { start: 1, end: 2.5, src: 'Hello', tgt: '你好' },
  { start: 3661.2345, end: 3662, src: 'a --> b\n\n  c', tgt: '' },
  { start: 5, end: 6, src: '', tgt: '' },
];

test('SRT: numbering, time codes, bilingual order, no broken cue structure', () => {
  assert.equal(E.srt(segs, 'dual', 'tgt-top'),
    '1\n00:00:01,000 --> 00:00:02,500\n你好\nHello\n\n2\n01:01:01,235 --> 01:01:02,000\na → b\nc\n');
  assert.equal(E.srt(segs, 'tgt', 'tgt-top'), '1\n00:00:01,000 --> 00:00:02,500\n你好\n');
});

test('WebVTT escapes markup and uses dots', () => {
  const v = E.vtt([{ start: 0, end: 1, src: 'a < b & c', tgt: '' }], 'src', 'tgt-top');
  assert.equal(v, 'WEBVTT\n\n1\n00:00:00,000 --> 00:00:01,000\na &lt; b &amp; c\n'.replace(/,000/g, '.000'));
});

test('TXT transcript', () => {
  assert.equal(E.txt(segs, 'src', 'src-top'), '[00:01] Hello\n[1:01:01] a --> b\n        \n          c');
});

test('ASS colours, times and escaping', () => {
  assert.equal(E.assColor('#ff8000'), '&H000080FF');
  assert.equal(E.assColor('#0b1a2e', 0.45), '&H732E1A0B');
  assert.equal(E.assTime(3725.456), '1:02:05.46');
  assert.equal(E.assEsc(String.raw`{\b1}x`), '｛＼b1｝x');
  const style = { bg: 'shadow', boxColor: '#000000', boxOpacity: .5, tgt: { font: 'Noto Sans TC', color: '#ffffff', strokeColor: '#000000', weight: 700, stroke: 3 },
    src: { font: 'Manrope', color: '#cfeaff', strokeColor: '#000000', weight: 400, stroke: 2 } };
  const doc = E.ass(segs.slice(0, 1), {
    content: 'dual', style, W: 1920, H: 1080, title: 'T\nx', fontSize: c => 60,
    layout: () => ({ blocks: [{ k: 'tgt', lines: [{ t: '你好' }] }, { k: 'src', lines: [{ t: 'He{llo}' }] }], cx: 960, y: 900 }),
  });
  assert.match(doc, /Title: T x\n/);
  assert.match(doc, /PlayResX: 1920\nPlayResY: 1080/);
  assert.match(doc, /Style: Tgt,Noto Sans TC,60,&H00FFFFFF,/);
  assert.ok(doc.includes(String.raw`Dialogue: 0,0:00:01.00,0:00:02.50,Tgt,,0,0,0,,{\an8\pos(960,900)}{\rTgt}你好\N{\rSrc}He｛llo｝`));
});
