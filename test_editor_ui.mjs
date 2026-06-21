// Phase2 UI(1-A) の純関数を review_story_page.html から抽出して検証（依存なし・Nodeのみ）。
// 実行: node test_editor_ui.mjs
// 対象: rightTabs（editor時に旧画像タブを出さない）/ rightDefaultTab / runMigrate（保存成功時のみ移行）。
import fs from 'node:fs';
import assert from 'node:assert';

const html = fs.readFileSync(new URL('./review_story_page.html', import.meta.url), 'utf8');

function extractFn(name) {
  const m = html.match(new RegExp('(async\\s+)?function\\s+' + name + '\\s*\\('));
  if (!m) throw new Error('not found: ' + name);
  const idx = m.index;
  let i = html.indexOf('{', idx), depth = 0, j = i;
  for (; j < html.length; j++) {
    const c = html[j];
    if (c === '{') depth++;
    else if (c === '}') { if (--depth === 0) { j++; break; } }
  }
  return html.slice(idx, j);
}

const names = ['rightTabs', 'rightDefaultTab', 'imageTabKind', 'runMigrate', 'hideToggleOp',
  '_turnIndexMap', 'resolveCueJS', 'computeImagePlan'];
const src = names.map(extractFn).join('\n\n');
new Function('g', src + '\nObject.assign(g,{' + names.join(',') + '});')(globalThis);

let pass = 0;
function t(name, fn) { fn(); pass++; console.log('  ' + name + ' OK'); }

// --- rightTabs / rightDefaultTab / imageTabKind ---
t('legacy: 画像タブを含む・素材タブは無い', () => {
  const ks = rightTabs(undefined, false, 0, 0).map(x => x[0]);
  assert.ok(ks.includes('image') && !ks.includes('assets'));
});
t('editor: 画像タブ(新)＋素材タブ・画像中身はeditor', () => {
  const ks = rightTabs('editor', false, 0, 3).map(x => x[0]);
  assert.ok(ks.includes('image') && ks.includes('assets'));
  assert.equal(imageTabKind('editor'), 'editor');   // 旧 image_cuts/review 編集には行かない
  assert.equal(imageTabKind(undefined), 'legacy');
});
t('既定タブ: image（演出中はviz）', () => {
  assert.equal(rightDefaultTab(undefined, false), 'image');
  assert.equal(rightDefaultTab('editor', false), 'image');
  assert.equal(rightDefaultTab('editor', true), 'viz');
});

// --- computeImagePlan（Python resolve_turn_images と同じ規則） ---
t('継続: 画像は次cueまで継続', () => {
  globalThis.DATA = { script: [{ id: 't1' }, { id: 't2' }, { id: 't3' }, { id: 't4' }],
    assets: [{ id: 'a0', file: '0.jpg' }, { id: 'a1', file: '1.jpg' }],
    imageCues: [{ id: 'c1', turnId: 't1', assetId: 'a0' }, { id: 'c2', turnId: 't3', assetId: 'a1' }] };
  const p = computeImagePlan();
  assert.equal(p[0].image, '0.jpg'); assert.equal(p[1].image, '0.jpg');
  assert.equal(p[2].image, '1.jpg'); assert.equal(p[3].image, '1.jpg');
});
t('endTurnId: 区切り後は画像なし(null)', () => {
  globalThis.DATA = { script: [{ id: 't1' }, { id: 't2' }, { id: 't3' }],
    assets: [{ id: 'a0', file: '0.jpg' }],
    imageCues: [{ id: 'c1', turnId: 't1', assetId: 'a0', endTurnId: 't1' }] };
  const p = computeImagePlan();
  assert.equal(p[0].image, '0.jpg'); assert.equal(p[1], null); assert.equal(p[2], null);
});
t('hide→blank / 先頭cue無し→null / subjectはcontain', () => {
  globalThis.DATA = { script: [{ id: 't1' }, { id: 't2' }, { id: 't3' }],
    assets: [{ id: 'a0', file: '0.jpg', kind: 'subject' }],
    imageCues: [{ id: 'c1', turnId: 't2', assetId: 'a0' }, { id: 'c2', turnId: 't3', assetId: 'a0', hide: true }] };
  const p = computeImagePlan();
  assert.equal(p[0], null, '先頭cue無し→画像なし');
  assert.equal(p[1].fit, 'contain', 'subjectはcontain既定');
  assert.equal(p[2].blank, true, 'hide→blank');
});

// --- hideToggleOp（解除の分岐） ---
t('hideToggle: 素材無しhide解除→delete（前画像継続へ）', () => {
  const op = hideToggleOp({ id: 'c', hide: true, assetId: null }, 't2');
  assert.equal(op.op, 'delete'); assert.equal(op.cueId, 'c');
});
t('hideToggle: 素材ありhide解除→setOpts(hide:false)で保持', () => {
  const op = hideToggleOp({ id: 'c', hide: true, assetId: 'a0' }, 't2');
  assert.equal(op.op, 'setOpts'); assert.equal(op.opts.hide, false);
});
t('hideToggle: 非hideの開始cue→hide:true / 開始無し→add hide', () => {
  assert.equal(hideToggleOp({ id: 'c', hide: false, assetId: 'a0' }, 't2').opts.hide, true);
  const add = hideToggleOp(null, 't2');
  assert.equal(add.op, 'add'); assert.equal(add.assetId, null); assert.equal(add.opts.hide, true);
});

// --- cropドラッグ：window mousemove を使わない（再描画でリークしない） ---
t('crop drag は window mousemove listener を足さない', () => {
  assert.ok(!/window\.addEventListener\(\s*['"]mousemove/.test(html), 'window mousemove未使用');
  assert.ok((html.match(/setPointerCapture/g) || []).length >= 2, 'crop2箇所がpointer captureで完結');
});

// --- runMigrate ---
async function runTests() {
  // 保存失敗 → 移行しない（migrateFnを呼ばない・reloadしない）
  let migrateCalled = false, reloaded = false;
  const r1 = await runMigrate(() => true,
    async () => ({ ok: false, message: 'disk full' }),
    async () => { migrateCalled = true; return { ok: true }; },
    async () => { reloaded = true; }, () => {});
  assert.equal(r1.migrated, false); assert.equal(r1.reason, 'save-failed');
  assert.equal(migrateCalled, false, '保存失敗時はmigrateを呼ばない');
  assert.equal(reloaded, false, '保存失敗時はreloadしない');
  pass++; console.log('  保存失敗→移行もreloadもしない OK');

  // 成功 → 現DATA(theme込み)を保存してから移行→reload
  let savedArg = null, reloaded2 = false;
  const DATA = { theme: 'マイテーマ', script: [{ speaker: 'A', text: 'x' }] };
  const r2 = await runMigrate(() => true,
    async () => { savedArg = DATA; return { ok: true }; },
    async () => ({ ok: true, switched: true }),
    async () => { reloaded2 = true; }, () => {});
  assert.equal(r2.migrated, true); assert.equal(reloaded2, true);
  assert.equal(savedArg.theme, 'マイテーマ', '移行前に現DATA(theme込み)を保存');
  pass++; console.log('  成功→保存(theme込み)+移行+reload OK');

  // migrate失敗 → reloadしない
  let reloaded3 = false;
  const r3 = await runMigrate(() => true, async () => ({ ok: true }),
    async () => ({ ok: false, message: 'x' }), async () => { reloaded3 = true; }, () => {});
  assert.equal(r3.migrated, false); assert.equal(r3.reason, 'migrate-failed');
  assert.equal(reloaded3, false);
  pass++; console.log('  移行失敗→reloadしない OK');

  // キャンセル → 何もしない
  let called = false;
  const r4 = await runMigrate(() => false, async () => { called = true; return { ok: true }; },
    async () => ({ ok: true }), async () => {}, () => {});
  assert.equal(r4.migrated, false); assert.equal(called, false);
  pass++; console.log('  キャンセル→何もしない OK');
}

await runTests();
console.log('ALL PASS (' + pass + ')');
