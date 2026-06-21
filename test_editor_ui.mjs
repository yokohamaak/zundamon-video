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

const names = ['rightTabs', 'rightDefaultTab', 'runMigrate'];
const src = names.map(extractFn).join('\n\n');
new Function('g', src + '\nObject.assign(g,{' + names.join(',') + '});')(globalThis);

let pass = 0;
function t(name, fn) { fn(); pass++; console.log('  ' + name + ' OK'); }

// --- rightTabs / rightDefaultTab ---
t('legacy: 画像タブを含む・素材タブは無い', () => {
  const ks = rightTabs(undefined, false, 0, 0).map(x => x[0]);
  assert.ok(ks.includes('image'), '画像あり');
  assert.ok(!ks.includes('assets'), '素材なし');
});
t('editor: 旧画像タブを出さない・素材タブを出す', () => {
  const ks = rightTabs('editor', false, 0, 3).map(x => x[0]);
  assert.ok(!ks.includes('image'), 'editorで画像タブ無し');
  assert.ok(ks.includes('assets'), 'editorで素材タブあり');
});
t('既定タブ: legacy=image / editor=image以外', () => {
  assert.equal(rightDefaultTab(undefined, false), 'image');
  assert.notEqual(rightDefaultTab('editor', false), 'image');
  assert.equal(rightDefaultTab('editor', true), 'viz');   // 演出中はviz
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
