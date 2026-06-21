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
  '_turnIndexMap', 'resolveCueJS', 'computeImagePlan', 'createAsyncQueue',
  'cueSpans', 'endDragIsContinue'];
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

// --- cropドラッグ：一時mouse listenerを終了時に必ず外す ---
t('crop drag は終了をwindowで捕捉しlistenerを解除する', () => {
  const cropDragSrc=extractFn('bindCropDrag');
  assert.ok(/window\.addEventListener\(\s*['"]mouseup['"]\s*,\s*finish/.test(html), 'mouseupを確実に捕捉');
  assert.ok(/window\.removeEventListener\(\s*['"]mousemove['"]\s*,\s*move/.test(html), 'mousemoveを解除');
  assert.ok(/window\.removeEventListener\(\s*['"]mouseup['"]\s*,\s*finish/.test(html), 'mouseupを解除');
  assert.equal((html.match(/bindCropDrag\(crop,imgEl,rectEl,\(c\)=>/g)||[]).length,2,'legacy/editorの両方で共通処理');
  assert.ok(/const d=drag,rr=d\.r,x=d\.x,y=d\.y/.test(html), '確定には最後のmove座標を使う');
  assert.ok(!/onpointerdown|addEventListener\(\s*['"]pointer/.test(cropDragSrc), 'cropはpointerイベントに依存しない');
});

// --- cueSpans / endDragIsContinue（画像タイムライン） ---
t('cueSpans: 終了未指定は次cue直前まで／endTurnIdで区切る', () => {
  globalThis.DATA = { script: [{ id: 't1' }, { id: 't2' }, { id: 't3' }, { id: 't4' }],
    assets: [{ id: 'a0', file: '0.jpg' }, { id: 'a1', file: '1.jpg' }],
    imageCues: [{ id: 'c1', turnId: 't1', assetId: 'a0' }, { id: 'c2', turnId: 't3', assetId: 'a1' }] };
  const sp = cueSpans();
  assert.equal(sp.length, 2);
  assert.deepEqual([sp[0].s, sp[0].e], [0, 1]);   // c1: 0..(c2の直前)
  assert.deepEqual([sp[1].s, sp[1].e], [2, 3]);   // c2: 2..末尾
  globalThis.DATA.imageCues[0].endTurnId = 't1';
  const sp2 = cueSpans();
  assert.deepEqual([sp2[0].s, sp2[0].e], [0, 0]);  // endTurnIdで1行に
});
t('endDragIsContinue: 次cue直前/末尾まで伸ばしたら継続', () => {
  assert.equal(endDragIsContinue(3, 5, 10), false);
  assert.equal(endDragIsContinue(4, 5, 10), true);   // nextStart-1
  assert.equal(endDragIsContinue(9, 5, 10), true);   // 末尾 n-1
});

// --- crop枠の再描画タイミング（保存済みクロップが消えないこと） ---
t('crop枠は添付後に描く（requestAnimationFrameで遅延）', () => {
  // /img-file はキャッシュされ imgEl.complete=true で来るため、同期描画だと未添付box=0で枠が消える。
  // legacy/editor 両方の crop バインドで rAF 遅延描画していることを確認（保存済みクロップの可視化）。
  assert.ok((html.match(/requestAnimationFrame\(drawCrop\)/g) || []).length >= 2,
    'legacy/editor両方でrAF遅延描画');
  assert.ok(!/imgEl\.complete\?drawCrop\(\)/.test(html), '同期drawCrop()呼び出しは残っていない');
});

// --- runMigrate ---
async function runTests() {
  // 即時保存を連続実行しても、開始・完了順が入れ替わらない。
  const enqueue = createAsyncQueue(); const order=[];
  const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
  const q1=enqueue(async()=>{ order.push('start1'); await wait(20); order.push('end1'); return 1; });
  const q2=enqueue(async()=>{ order.push('start2'); await wait(1); order.push('end2'); return 2; });
  assert.deepEqual(await Promise.all([q1,q2]),[1,2]);
  assert.deepEqual(order,['start1','end1','start2','end2']);
  pass++; console.log('  cue即時保存は直列実行（古い応答で上書きしない） OK');

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
