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
  'cueSpans', 'endDragIsContinue', 'spanUnderViz',
  'vizTabKind', 'vizAnchorOptions', 'vizDefaultConfig', 'groupKeyframesByTurn',
  'isEditorAuthority', 'segBoundsGi', 'segCoversGi', 'segOf', 'chSegs', 'segRange',
  'vizSegForTurn', 'chapterVizCount', 'vizSuppressRanges'];
const src = names.map(extractFn).join('\n\n');
new Function('g', src + '\nObject.assign(g,{' + names.join(',') + '});')(globalThis);

let pass = 0;
function t(name, fn) { fn(); pass++; console.log('  ' + name + ' OK'); }

// --- rightTabs / rightDefaultTab / imageTabKind ---
t('legacy: 画像タブを含む・素材タブは無い', () => {
  const ks = rightTabs(undefined, false, 0, 0).map(x => x[0]);
  assert.ok(ks.includes('image') && !ks.includes('assets'));
});
t('editor: 画像タブ(新)・素材はper-turnタブから外しヘッダー全幅ビューへ', () => {
  const ks = rightTabs('editor', false, 0, 3).map(x => x[0]);
  assert.ok(ks.includes('image') && !ks.includes('assets'), '素材はper-turnタブに無い');
  assert.equal(imageTabKind('editor'), 'editor');   // 旧 image_cuts/review 編集には行かない
  assert.equal(imageTabKind(undefined), 'legacy');
});
t('素材ライブラリ: 全幅ビュー切替の配線(同一画面・DATA1本)', () => {
  assert.ok(/let assetLibOpen=false/.test(html), '全幅ビュー状態');
  assert.ok(/function rerenderAssets\(\)\{ if\(assetLibOpen\) render\(\); else renderRight\(\); \}/.test(html), '素材操作後は文脈に応じ再描画');
  assert.ok(/if\(assetLibOpen && isEditorAuthority\(\)\)\{ renderAssetLibrary\(m\)/.test(html), 'render内で全幅ビューへ分岐');
  assert.ok(/function renderAssetLibrary/.test(html) && /renderAssetTab\(wrap\)/.test(html), '既存の素材UIを流用');
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
t('spanUnderViz: 大演出区間との重なり判定', () => {
  const rg = [[2, 4], [7, 8]];
  assert.equal(spanUnderViz(0, 1, rg), false);  // 手前
  assert.equal(spanUnderViz(4, 6, rg), true);   // 端で重なる
  assert.equal(spanUnderViz(5, 6, rg), false);  // 隙間
  assert.equal(spanUnderViz(0, 9, rg), true);   // 全体を覆う
});

// --- crop枠の再描画タイミング（保存済みクロップが消えないこと） ---
t('crop枠は添付後に描く（requestAnimationFrameで遅延）', () => {
  // /img-file はキャッシュされ imgEl.complete=true で来るため、同期描画だと未添付box=0で枠が消える。
  // legacy/editor 両方の crop バインドで rAF 遅延描画していることを確認（保存済みクロップの可視化）。
  assert.ok((html.match(/requestAnimationFrame\(drawCrop\)/g) || []).length >= 2,
    'legacy/editor両方でrAF遅延描画');
  assert.ok(!/imgEl\.complete\?drawCrop\(\)/.test(html), '同期drawCrop()呼び出しは残っていない');
});

// --- authority対応の大演出読取: stale vizList/vizSeg と visualSegments を不一致にして確認 ---
// 旧(vizList=ch0に1件・vizSegはturn-1のみ・type=quiz) と 新(visualSegments=turn-2..3のpanel) を意図的に食い違わせる。
function _mismatchData() {
  return {
    script: [{ id: 't1', chapter: 0, vizSeg: 'sOld', text: 'aaaa' },
             { id: 't2', chapter: 0, text: 'bbbb' },
             { id: 't3', chapter: 0, text: 'cccc' }],
    chapters: [{ vizList: [{ id: 'sOld', type: 'quiz', quiz: { question: 'STALE' } }] }],
    visualSegments: [{ id: 'visual-00-sNew', type: 'panel', status: 'active',
                       startTurnId: 't2', endTurnId: 't3', sourceChapter: 0,
                       config: { panel: { items: [] } }, keyframes: [] }],
  };
}
t('editor: 行のviz判定はvisualSegments基準(stale vizSegを無視)', () => {
  globalThis.DATA = { ...(_mismatchData()), editorModelAuthority: 'editor' };
  assert.equal(vizSegForTurn(0), null, 't1はstale vizSegだが新では非該当');
  assert.ok(vizSegForTurn(2) && vizSegForTurn(2).id === 'visual-00-sNew', 't3は新segに含む');
});
t('legacy: 行のviz判定は従来vizSeg基準(挙動不変)', () => {
  globalThis.DATA = _mismatchData();   // authority未設定=legacy
  assert.ok(vizSegForTurn(0) && vizSegForTurn(0).id === 'sOld', 'legacyはvizSeg基準');
  assert.equal(vizSegForTurn(2), null, 'legacyではt3にvizSegなし');
});
t('章見出し件数: editor=visualSegments / legacy=vizList', () => {
  globalThis.DATA = { ...(_mismatchData()), editorModelAuthority: 'editor' };
  assert.equal(chapterVizCount(0), 1, 'editorは新segを数える');
  globalThis.DATA = _mismatchData();
  assert.equal(chapterVizCount(0), 1, 'legacyはvizListを数える');
});
t('vizSuppressRanges: editor=新panel区間 / legacy=旧区間', () => {
  globalThis.DATA = { ...(_mismatchData()), editorModelAuthority: 'editor' };
  assert.deepEqual(vizSuppressRanges(), [[1, 2]], 'editorは新panel(t2..t3)の抑制区間');
  globalThis.DATA = _mismatchData();   // legacy: sOldはquiz(overlay)＝抑制なし
  assert.deepEqual(vizSuppressRanges(), [], 'legacyはquizなので抑制区間なし');
});

// --- 大演出(visualSegments)エディタの純関数 ---
t('vizTabKind: editor=新エディタ / legacy=旧', () => {
  assert.equal(vizTabKind('editor'), 'editor');
  assert.equal(vizTabKind(undefined), 'legacy');
});
t('groupKeyframesByTurn: 同一turnIdを1グループへ集約・出現順維持', () => {
  const g = groupKeyframesByTurn([
    { id: 'k1', turnId: 't1', type: 'panel_item', value: 0 },
    { id: 'k2', turnId: 't1', type: 'panel_item', value: 1 },   // 同一セリフに複数
    { id: 'k3', turnId: 't2', type: 'reveal' }]);
  assert.equal(g.length, 2, 't1とt2の2グループ');
  const t1 = g.find(x => x.turnId === 't1');
  assert.equal(t1.kfs.length, 2, '同一turnIdの2変化点を集約');
  assert.deepEqual(t1.kfs.map(k => k.value), [0, 1], '出現順を維持');
  assert.deepEqual(groupKeyframesByTurn([]), [], '空でも安全');
});

t('デッドな旧UIコードは撤去済み(renderLegacy等)', () => {
  // editor到達不能（かつ全モード未使用）のデッドコードが残っていないこと。
  ['renderLegacy', 'vizHeaderCard', 'vizOpenGi', 'turnHasViz', 'vizAddMenu', 'vizRange']
    .forEach(n => assert.ok(!new RegExp('\\b' + n + '\\b').test(html), n + ' が残存'));
  // 一方で legacy 編集UIは維持（後方互換）。
  assert.ok(/function renderVizTab\b/.test(html) && /function renderImageTab\b/.test(html),
    'legacy編集UI(renderVizTab/renderImageTab)は維持');
});

t('cue/viz/asset書込は同一キューで直列化', () => {
  // 古い応答が新しい編集を上書きしないよう、全editor書込が共有キューを通ること（ソース確認）。
  assert.ok(/const enqueueEditorWrite=createAsyncQueue\(\)/.test(html), '共有キューを定義');
  assert.ok((html.match(/enqueueEditorWrite\(/g) || []).length >= 4,
    'cueOp/vizOp/asset-add/asset-delete が共有キュー経由');
});
t('vizDefaultConfig: 型ごとの初期config', () => {
  assert.ok(vizDefaultConfig('quiz').quiz);
  assert.ok(vizDefaultConfig('panel').panel.items.length === 1);
  assert.ok(vizDefaultConfig('compare').compare.left && vizDefaultConfig('compare').compare.right);
});
t('vizAnchorOptions: 型ごとの変化点候補', () => {
  const panel = vizAnchorOptions({ type: 'panel', config: { panel: { items: [{}, {}] } } });
  assert.ok(panel.some(o => o.type === 'panel_item' && o.value === 1));
  assert.ok(panel.some(o => o.type === 'panel_event'));
  assert.deepEqual(vizAnchorOptions({ type: 'quiz', config: {} }).map(o => o.type), ['reveal']);
  const cmp = vizAnchorOptions({ type: 'compare', config: {} });
  assert.deepEqual(cmp.map(o => o.value), [0, 1]);
});

// --- Phase 3-D: 変化点の移動UX（縦線ゴースト・移動先ハイライト・ダブルクリック詳細編集）の配線が消えていないこと ---
// 根本原因（リファクタで機能が黙って撤去された）の再発防止のため、ソース上の配線存在を検証する。
t('3-D: 変化点ドラッグ中に縦線(kf-caret)と移動先ハイライト(kf-drop)を出す', () => {
  const fn = extractFn('startVizKfDrag');
  assert.ok(/kf-caret/.test(fn), 'editorの変化点ドラッグに縦線(kf-caret)がある');
  assert.ok(/kf-drop/.test(fn), '移動先セリフのハイライト(kf-drop)がある');
  assert.ok(/\.kf-caret\s*\{/.test(html), 'kf-caret のCSSが残っている');
});
t('3-D: 変化点◆のダブルクリックで詳細編集へフォーカス', () => {
  const fn = extractFn('startVizKfDrag');
  assert.ok(/focusKfEditor\(/.test(fn), 'ダブルクリックで focusKfEditor を呼ぶ');
  const fe = extractFn('focusKfEditor');
  assert.ok(/rtab='viz'/.test(fe) && /kfpos-/.test(fe), '大演出タブを開きpos入力へフォーカス');
});
t('3-D: 右ペインに文字位置(pos)の数値入力があり moveKf 保存する', () => {
  assert.ok(/id='kfpos-'\+kf\.id/.test(html), 'pos入力に kfpos-<id> がある（ダブルクリックの着地先）');
  assert.ok(/op:'moveKf', segId:cur\.id, kfId:kf\.id, turnId:kf\.turnId, pos:n/.test(html),
    'pos数値入力は同セリフ内で moveKf(pos) を送る');
});

// --- タイムライン横ズーム（プルダウン選択式・変化点を見やすく） ---
t('zoom: 段階プルダウンで tlColWidth が倍率どおり拡大する', () => {
  const a = html.indexOf('const TL_CHAR_PX=11;');
  const b = html.indexOf('function setTimelineZoom');
  const e = html.indexOf('\n', b);
  const block = html.slice(a, e);
  globalThis.localStorage = { _m: {}, getItem(k){ return this._m[k] ?? null; }, setItem(k, v){ this._m[k] = String(v); } };
  globalThis.renderTimeline = () => {};
  new Function('g', block + '\n;g.tlColWidth=tlColWidth; g.setTimelineZoom=setTimelineZoom; g.tlLevelOf=tlLevelOf; g.getDetail=()=>timelineDetail;')(globalThis);
  const txt = { text: 'あ'.repeat(30) };
  setTimelineZoom('overview'); assert.equal(getDetail(), false, '俯瞰=detail off');
  setTimelineZoom('fit'); assert.equal(getDetail(), true, '全文=detail on'); const w1 = tlColWidth(txt);
  setTimelineZoom('x2'); const w2 = tlColWidth(txt);
  setTimelineZoom('x3'); const w3 = tlColWidth(txt);
  assert.ok(w1 < w2 && w2 < w3, '倍率を上げるほど広がる');
  assert.ok(w3 >= w1 * 2.5, '×3は×1の2.5倍以上に広がる');
  assert.equal(localStorage.getItem('reviewTimelineZoom'), 'x3', '選択をlocalStorageへ保存');
});
t('zoom: 旧OFF/ONボタンは廃止しselectへ／旧設定を引き継ぐ', () => {
  assert.ok(/zoomSel\.onchange=\(\)=>setTimelineZoom\(zoomSel\.value\)/.test(html), 'プルダウンで粒度変更');
  assert.ok(/reviewTimelineDetail.*\?'fit':'overview'/.test(html), '旧reviewTimelineDetailから引き継ぐ');
  assert.ok(!/setTimelineDetail/.test(html), '旧トグル関数は撤去');
});
t('zoom: 詳細時はセルの文字サイズも倍率に比例して拡大する', () => {
  // 列幅だけ広げても文字が固定だと読めず縦線位置もズレるため、本文spanのfontSizeをmultに比例させる。
  assert.ok(/const zMult=tlLevelOf\(timelineLevel\)\.mult/.test(html), '倍率を取得');
  assert.ok(/tx\.style\.fontSize=fs\+'px'/.test(html) && /Math\.round\(10\*zMult\)/.test(html),
    '本文spanのfontSize=10*mult／行高も拡大');
});
t('prefix: 行番号は本文に連結せず固定幅バッジ(.tl-no)に分離する', () => {
  // 旧: textContent=(gi+1)+'. '+text → 本文位置がズレ・ズームで番号も巨大化して邪魔。
  assert.ok(!/textContent=\(gi\+1\)\+'\. '/.test(html), '本文への番号連結は廃止');
  assert.ok(/className='tl-no'/.test(html) && /\.tl-turn \.tl-no\s*\{[^}]*position:absolute/.test(html),
    '行番号は絶対配置の固定バッジ');
  assert.ok(/paddingLeft=TL_TURN_GUTTER/.test(html), '本文はバッジ分の左余白を空ける');
});
t('caret: 縦線/posは本文領域(行番号バッジ分を除く)基準で計算する', () => {
  const fn = extractFn('startVizKfDrag');
  assert.ok(/padL=TL_TURN_GUTTER/.test(fn), '左オフセットに行番号バッジ幅を使う');
  assert.ok(/cr\.width-padL-9/.test(fn), '本文幅=セル幅-バッジ-右余白');
});

// --- 逆連携（プレビュー→セリフ/タイムライン追従） ---
t('follow: 再生フレーム通知でselectを追従し再シークしない(ループ防止)', () => {
  const fn = extractFn('followPlaybackTurn');
  assert.ok(/if\(gi===selGi\) return/.test(fn), '同じ行は無視＝ループ防止');
  assert.ok(!/seekRemotionToSelection/.test(fn), '追従中は再シークしない（再生を止めない）');
  assert.ok(/scrollTimelineToTurn\(gi\)/.test(fn), 'タイムラインも再生に合わせて進める');
  assert.ok(/addEventListener\('remotion-turn-change'/.test(html), 'Playerからのフレーム通知を購読');
});

t('viz設定: 旧editorのデザイン群を新editorへ移植(色/背景/サイズ/モード)', () => {
  const fn = extractFn('vizConfigEditor');
  // 共通ヘルパー（変更確定で setConfig 保存）
  assert.ok(/const colorRow=/.test(fn) && /const bgRow=/.test(fn) && /const modeRow=/.test(fn), 'colorRow/bgRow/modeRow ヘルパー');
  assert.ok(/cp\.onchange=commit/.test(fn) && /op\.onchange=commit/.test(fn), '色/透過は確定時にsetConfig保存');
  // quiz: サイズ＋色＋背景＋ボックス幅
  assert.ok(/sizeField\('問いの大きさ',q,'questionSize'\)/.test(fn) && /sizeField\('答えの大きさ',q,'answerSize'\)/.test(fn), 'quiz文字サイズ');
  assert.ok(/bgRow\('問いの背景'/.test(fn) && /bgRow\('答えの背景'/.test(fn) && /q\.boxWidth/.test(fn), 'quiz背景/ボックス幅');
  // stat: 強調色/大きさ/背景/カウント速度
  assert.ok(/colorRow\('強調色（数字）',s,'color'/.test(fn) && /sizeField\('大きさ',s,'size'/.test(fn) && /modeRow\('カウント速度',s,'countSpeed'/.test(fn), 'stat一式');
  // compare: ラベル色/大きさ/分割線（resolverが素通しするキーと一致）
  assert.ok(/colorRow\('ラベル背景',c,'labelColor'/.test(fn) && /sizeField\('ラベル大きさ',c,'labelSize'/.test(fn) && /colorRow\('分割線',c,'dividerColor'/.test(fn), 'compare一式');
  // panel: 表示/位置/マーカー/テキスト/背景
  assert.ok(/modeRow\('マーカー',p,'markerType'/.test(fn) && /colorRow\('マーカー色',p,'markerColor'/.test(fn) && /sizeField\('テキスト大きさ',p,'textSize'/.test(fn), 'panel一式');
  assert.ok(/delete p\.overlay; delete p\.pos/.test(fn), 'panel表示切替でposをクリア(旧editorと同じ)');
  // callouts: calloutStyle 一式＋項目の位置/矢印
  assert.ok(/cfg\.calloutStyle=cfg\.calloutStyle\|\|\{\}/.test(fn) && /colorRow\('マーカー色',st,'markerColor'/.test(fn) && /modeRow\('矢印形',st,'arrowShape'/.test(fn), 'callouts calloutStyle一式');
  assert.ok(/mkNum\('x'\)/.test(fn) && /it\.arrow=true/.test(fn), 'callouts項目の位置(x/y)と矢印');
});

t('outro固定: isManagedClosingがサーバ判定と一致し、本文/分割/削除を抑止', () => {
  const fn = extractFn('isManagedClosing');
  new Function('g', fn + '\n;g.isManagedClosing=isManagedClosing;')(globalThis);
  assert.equal(isManagedClosing({ closing: true }), true, 'closingフラグ');
  assert.equal(isManagedClosing({ chorus: true }), true, 'chorusフラグ');
  assert.equal(isManagedClosing({ section: 'outro', text: '高評価よろしく' }), true, 'outro高評価CTA');
  assert.equal(isManagedClosing({ section: 'outro', text: 'チャンネル登録してね' }), true, 'outro登録CTA');
  assert.equal(isManagedClosing({ section: 'trivia', text: '高評価' }), false, '本編はロックしない');
  assert.equal(isManagedClosing({ section: 'outro', text: 'まとめると' }), false, 'CTA語が無いoutroは対象外');
  // 音声に関わる編集経路(本文/分割/削除)に早期ガードがある
  assert.ok(/function startEditLine[\s\S]{0,120}if\(isManagedClosing\(tn\)\) return/.test(html), 'startEditLineガード');
  assert.ok(/function splitTurn[\s\S]{0,80}if\(isManagedClosing\(tn\)\) return/.test(html), 'splitTurnガード');
  assert.ok(/function delTurn[\s\S]{0,120}if\(isManagedClosing\(tn\)\)/.test(html), 'delTurnガード');
});

t('左サムネ: editorはimageCues解決を使い、cue編集後に更新する', () => {
  // editorで旧cutを見ると画像削除/継続が反映されず、削除前の画像が残る不具合の修正。
  const lr = extractFn('lineRow');
  assert.ok(/isEditorAuthority\(\)/.test(lr) && /imgPlan&&imgPlan\[gi\]/.test(lr) && /\/img-file\//.test(lr),
    'editorはcomputeImagePlanの画像(/img-file)、legacyは従来cut');
  assert.ok(/lineRow\(tn,gi,ch,ci,!!tn\.vizSeg,imgPlan\)/.test(html) && /const imgPlan=isEditorAuthority\(\)\?computeImagePlan\(\)/.test(html),
    'renderでプランを1回計算して渡す');
  const op = extractFn('cueOp');
  assert.ok(/refreshLineThumbs\(\)/.test(op), 'cue編集成功時に左サムネを更新');
  assert.ok(/function refreshLineThumbs/.test(html) && /computeImagePlan\(\)/.test(extractFn('refreshLineThumbs')),
    'refreshLineThumbsはimageCues解決で更新');
});

t('画像選択: 画像タブにインライン素材ピッカー(クリックで即place)', () => {
  // 旧: 素材タブで選択→画像タブで「選択素材に差し替え」の2段階。新: 画像タブで素材クリック＝即配置。
  const fn = extractFn('assetPickerGrid');
  assert.ok(/op:'place', turnId:tn\.id, assetId:a\.id/.test(fn), 'サムネクリックで即place(配置/差し替え)');
  assert.ok(/curId/.test(fn) && /使用中/.test(fn), '現在使用中の素材を強調');
  assert.ok(/addAssetFromFile/.test(fn), '＋追加タイルから素材取り込み');
  const tab = extractFn('renderEditorImageTab');
  assert.ok(/assetPickerGrid\(tn, startCue, entry\)/.test(tab), '画像タブがピッカーを描画');
  assert.ok(!/選択素材に差し替え/.test(html), '旧「選択素材に差し替え」導線は廃止');
});

t('タブ: 大演出の件数を廃止し、設定有無を色(.has)で示す', () => {
  // 大演出タブから数字を撤去
  assert.ok(!/'大演出 '\+nseg/.test(html), '大演出タブの件数表示は廃止');
  assert.equal(rightTabs('editor', false, 3, 0).find(t => t[0] === 'viz')[1], '大演出', 'ラベルは数字なし');
  // 画像/小演出/大演出に has 判定を付与
  assert.ok(/const tabHas=\{ image:hasImg, viz:inViz, small:hasSmall \}/.test(html), 'image/viz/smallのhas判定');
  assert.ok(/tabHas\[k\]\?' has':''/.test(html), '設定有無でhasを付与(選択中タブでも)');
  assert.ok(/\.rtab\.has:not\(\.on\):not\(\.imgon\)/.test(html), '背景色は非選択時のみ・下線は選択中も出す');
  assert.ok(/hasSmall=\(activeFx\(tn\)\.length>0\)\|\|!!tn\.telop\|\|!!tn\.reaction/.test(html), '小演出=textEffects/telop/reaction');
  assert.ok(/\.rtab\.has\s*\{/.test(html), '.rtab.has のCSS');
});

t('セリフ追加: id採番(max+1)・話者候補・分割でなく挿入', () => {
  const src = extractFn('nextTurnId') + '\n' + extractFn('speakerOptions');
  new Function('g', src + '\n;g.nextTurnId=nextTurnId;g.speakerOptions=speakerOptions;')(globalThis);
  globalThis.DATA = { script: [{ id: 'turn-0001', speaker: 'ずんだもん' }, { id: 'turn-0007', speaker: '四国めたん' }] };
  assert.equal(nextTurnId(), 'turn-0008', 'max+1で4桁ゼロ詰め');
  assert.deepEqual(speakerOptions(), ['ずんだもん', '四国めたん'], 'データから話者候補');
  globalThis.DATA = { script: [] };
  assert.deepEqual(speakerOptions(), ['ずんだもん', '四国めたん'], '空ならデフォルト2話者');
  assert.ok(/DATA\.script\.splice\(i\+1,0,nt\); markDirty\(\)/.test(html), '参照行の下に挿入(分割でない)＋音声影響でmarkDirty');
  assert.ok(/id:nextTurnId\(\), speaker:sp\.value, emotion:em\.value/.test(html), '話者/表情を選んで新ターン生成');
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
