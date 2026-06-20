// vizPoints の整合ロジック単体テスト（review_story_page.html の純関数/整合関数を抽出して検証）。
// 依存追加なし・Nodeのみ。実行: node test_viz_points.mjs
import fs from 'node:fs';
import assert from 'node:assert';

const html = fs.readFileSync(new URL('./review_story_page.html', import.meta.url), 'utf8');

// 関数定義を波括弧バランスで抽出する。
function extractFn(name) {
  const idx = html.search(new RegExp('function\\s+' + name + '\\s*\\('));
  if (idx < 0) throw new Error('not found: ' + name);
  let i = html.indexOf('{', idx), depth = 0, j = i;
  for (; j < html.length; j++) {
    const c = html[j];
    if (c === '{') depth++;
    else if (c === '}') { if (--depth === 0) { j++; break; } }
  }
  return html.slice(idx, j);
}

const names = ['findVizPoint', 'nextVizPointId', 'setVizPointPos', 'removeVizPoint',
  'splitVizPoints', 'shiftVizPointValues', 'moveAnchorFlag', 'reconcileVizPointPos',
  'chSegs', 'pruneEmptySegs', 'clampItemFlags', 'retagSeg', 'setSegFlag', 'togglePanelItem'];
const src = names.map(extractFn).join('\n\n');
new Function('g', src + '\nObject.assign(g,{' + names.join(',') + '});')(globalThis);
globalThis.selSeg = null;
globalThis.markDirty = () => {};

let pass = 0;
function t(name, fn) { fn(); pass++; console.log('  ' + name + ' OK'); }

t('splitVizPoints: 分割位置で前半/後半へ振り分け＋pos調整', () => {
  const { a, b } = splitVizPoints(
    [{ id: 'vp1', type: 'panel_item', value: 0, pos: 2 }, { id: 'vp2', type: 'panel_item', value: 1, pos: 8 }],
    5, 0, 5, 5, 5);
  assert.equal(a.length, 1); assert.equal(a[0].pos, 2);
  assert.equal(b.length, 1); assert.equal(b[0].pos, 3); // 8 - bFrom(5)
});

t('shiftVizPointValues: 中間削除で繰り下げ', () => {
  const r = shiftVizPointValues(
    [{ type: 'panel_item', value: 0, pos: 1 }, { type: 'panel_item', value: 1, pos: 2 }, { type: 'panel_item', value: 2, pos: 3 }],
    'panel_item', 0); // 項目0を削除
  assert.deepEqual(r.map(p => p.value), [0, 1]); // 旧1→0, 旧2→1
  assert.deepEqual(r.map(p => p.pos), [2, 3]);   // pos は保持
  assert.equal(shiftVizPointValues([{ type: 'panel_item', value: 0, pos: 1 }], 'panel_item', 0), null);
});

t('reconcileVizPointPos: pos が新テキスト長を超えたらクランプ', () => {
  const vps = [{ type: 'reveal', pos: 20 }];
  assert.equal(reconcileVizPointPos(vps, 10), true);
  assert.equal(vps[0].pos, 10);
  assert.equal(reconcileVizPointPos([{ type: 'reveal', pos: 5 }], 10), false);
});

t('setVizPointPos: pos:0 は先頭の明示として保持（削除しない）', () => {
  const tn = { text: 'abc' };
  setVizPointPos(tn, { type: 'reveal' }, 0);
  assert.equal(tn.vizPoints.length, 1); assert.equal(tn.vizPoints[0].pos, 0);
  setVizPointPos(tn, { type: 'reveal' }, 2);
  assert.equal(tn.vizPoints.length, 1); assert.equal(tn.vizPoints[0].pos, 2);
});

t('removeVizPoint: 点を削除（自動配置へ戻す）', () => {
  const tn = { vizPoints: [{ id: 'vp1', type: 'reveal', pos: 0 }] };
  removeVizPoint(tn, { type: 'reveal' });
  assert.equal(tn.vizPoints, undefined);
});

t('clampItemFlags: 中間削除でフラグ/vizPointsを再採番', () => {
  globalThis.DATA = { script: [{ chapter: 0, panel_item: [0, 1, 2],
    vizPoints: [{ type: 'panel_item', value: 0, pos: 1 }, { type: 'panel_item', value: 2, pos: 5 }] }] };
  clampItemFlags(0, 'panel_item', 1); // 項目1を削除
  const t0 = DATA.script[0];
  assert.deepEqual(t0.panel_item, [0, 1]);           // 旧[0,1,2]→1削除→旧2→1
  assert.deepEqual(t0.vizPoints.map(p => p.value), [0, 1]); // 旧value2→1, value0残
});

t('moveAnchorFlag: 後半へ移った点の対応flagを tn→nt へ移す', () => {
  const tn = { panel_item: [0, 1] }, nt = {};
  moveAnchorFlag(tn, nt, { type: 'panel_item', value: 1 });
  assert.equal(tn.panel_item, 0); assert.equal(nt.panel_item, 1);
  const tn2 = { reveal: true }, nt2 = {};
  moveAnchorFlag(tn2, nt2, { type: 'reveal' });
  assert.equal(tn2.reveal, undefined); assert.equal(nt2.reveal, true);
  const tn3 = { compare_item: 1 }, nt3 = {};
  moveAnchorFlag(tn3, nt3, { type: 'compare_item', value: 1 });
  assert.equal(tn3.compare_item, undefined); assert.equal(nt3.compare_item, 1);
});

t('retagSeg: 範囲外になったセリフの vizPoints を掃除', () => {
  globalThis.selSeg = null;
  globalThis.DATA = { chapters: [{ vizList: [{ id: 's1', type: 'panel' }] }],
    script: [{ chapter: 0, vizSeg: 's1', vizPoints: [{ type: 'reveal', pos: 1 }] },
             { chapter: 0, vizSeg: 's1', vizPoints: [{ type: 'reveal', pos: 2 }] }] };
  retagSeg(0, 's1', 0, 0); // gi=0 のみ範囲。gi=1 は範囲外
  assert.equal(DATA.script[0].vizSeg, 's1');
  assert.equal(DATA.script[1].vizSeg, undefined);
  assert.equal(DATA.script[1].vizPoints, undefined);
});

t('setSegFlag: 他行へ移すと旧行のflagと演出点を削除', () => {
  globalThis.DATA = { script: [
    { chapter: 0, vizSeg: 's1', compare_item: 0, vizPoints: [{ type: 'compare_item', value: 0, pos: 3 }] },
    { chapter: 0, vizSeg: 's1' }] };
  const tn = DATA.script[1];
  setSegFlag(0, 's1', 'compare_item', 0, tn, true);
  assert.equal(DATA.script[0].compare_item, undefined);
  assert.equal(DATA.script[0].vizPoints, undefined); // 旧位置の点も消える
  assert.equal(tn.compare_item, 0);
});

t('togglePanelItem: 外すと自身の演出点も削除', () => {
  globalThis.DATA = { script: [{ chapter: 0, vizSeg: 's1', panel_item: 0,
    vizPoints: [{ type: 'panel_item', value: 0, pos: 3 }] }] };
  const tn = DATA.script[0];
  togglePanelItem(0, 's1', 0, tn); // k=0 を外す
  assert.equal(tn.panel_item, undefined);
  assert.equal(tn.vizPoints, undefined);
});

console.log('ALL PASS (' + pass + ')');
