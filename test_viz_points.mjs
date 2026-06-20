// vizPoints の整合ロジック単体テスト（review_story_page.html の純関数/整合関数を抽出して検証）。
// 依存追加なし・Nodeのみ。実行: node test_viz_points.mjs
import fs from 'node:fs';
import assert from 'node:assert';

const html = fs.readFileSync(new URL('./review_story_page.html', import.meta.url), 'utf8');

// 関数定義を波括弧バランスで抽出する（DOM非依存の関数を対象に取り出す）。
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
  'splitVizPoints', 'clampVizPointValues', 'reconcileVizPointPos',
  'chSegs', 'pruneEmptySegs', 'clampItemFlags', 'retagSeg'];
const src = names.map(extractFn).join('\n\n');
// 非strictで関数を定義し globalThis へ公開（retagSeg等はグローバルの DATA/selSeg を参照）。
new Function('g', src + '\nObject.assign(g,{' + names.join(',') + '});')(globalThis);
globalThis.selSeg = null;

let pass = 0;
function t(name, fn) { fn(); pass++; console.log('  ' + name + ' OK'); }

t('splitVizPoints: 分割位置で前半/後半へ振り分け＋pos調整', () => {
  const vps = [{ id: 'vp1', type: 'panel_item', value: 0, pos: 2 },
               { id: 'vp2', type: 'panel_item', value: 1, pos: 8 }];
  const { a, b } = splitVizPoints(vps, 5, 0, 5, 5, 5);
  assert.equal(a.length, 1); assert.equal(a[0].pos, 2);
  assert.equal(b.length, 1); assert.equal(b[0].pos, 3); // 8 - bFrom(5)
});

t('clampVizPointValues: value>=n の点を削除', () => {
  const r = clampVizPointValues(
    [{ type: 'panel_item', value: 0, pos: 1 }, { type: 'panel_item', value: 2, pos: 3 }], 'panel_item', 2);
  assert.equal(r.length, 1); assert.equal(r[0].value, 0);
  assert.equal(clampVizPointValues([{ type: 'panel_item', value: 0, pos: 1 }], 'panel_item', 0), null);
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
  assert.equal(tn.vizPoints.length, 1);
  assert.equal(tn.vizPoints[0].pos, 0);
  setVizPointPos(tn, { type: 'reveal' }, 2); // 既存更新
  assert.equal(tn.vizPoints.length, 1);
  assert.equal(tn.vizPoints[0].pos, 2);
});

t('removeVizPoint: 点を削除（自動配置へ戻す）', () => {
  const tn = { vizPoints: [{ id: 'vp1', type: 'reveal', pos: 0 }] };
  removeVizPoint(tn, { type: 'reveal' });
  assert.equal(tn.vizPoints, undefined);
});

t('clampItemFlags: 同 type の vizPoints も再採番', () => {
  globalThis.DATA = { script: [{ chapter: 0, panel_item: 2,
    vizPoints: [{ type: 'panel_item', value: 2, pos: 3 }, { type: 'panel_item', value: 0, pos: 1 }] }] };
  clampItemFlags(0, 'panel_item', 2);
  const t0 = DATA.script[0];
  assert.equal(t0.panel_item, undefined);     // 2>=2 で削除
  assert.equal(t0.vizPoints.length, 1);
  assert.equal(t0.vizPoints[0].value, 0);
});

t('retagSeg: 範囲外になったセリフの vizPoints を掃除', () => {
  globalThis.selSeg = null;
  globalThis.DATA = { chapters: [{ vizList: [{ id: 's1', type: 'panel' }] }],
    script: [{ chapter: 0, vizSeg: 's1', vizPoints: [{ type: 'reveal', pos: 1 }] },
             { chapter: 0, vizSeg: 's1', vizPoints: [{ type: 'reveal', pos: 2 }] }] };
  retagSeg(0, 's1', 0, 0); // gi=0 のみ範囲。gi=1 は範囲外になる
  assert.equal(DATA.script[0].vizSeg, 's1');
  assert.equal(DATA.script[1].vizSeg, undefined);
  assert.equal(DATA.script[1].vizPoints, undefined); // 孤立せず削除
});

console.log('ALL PASS (' + pass + ')');
