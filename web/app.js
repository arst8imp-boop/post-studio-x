// Post Studio - Frontend
const workspace = document.getElementById('workspace');
const template = document.getElementById('pane-template');
const historyModal = document.getElementById('history-modal');
const historyList = document.getElementById('history-list');
const historyBrandFilter = document.getElementById('history-brand-filter');
let nextPaneId = 0;

// ============ ペイン管理 ============
function makePane() {
  const node = template.content.cloneNode(true);
  const pane = node.querySelector('.pane');
  const id = ++nextPaneId;
  pane.dataset.paneId = id;
  bindPaneEvents(pane);
  // タイプ変更でカバーボタン表示制御
  pane.querySelector('[data-field="type"]').addEventListener('change', () => updateCoverBtn(pane));
  updateCoverBtn(pane);
  return pane;
}

function bindPaneEvents(pane) {
  pane.querySelector('[data-action="close"]').addEventListener('click', () => closePane(pane));
  pane.querySelector('[data-action="generate"]').addEventListener('click', () => generate(pane));
  pane.querySelector('[data-action="copy"]').addEventListener('click', () => copyOutput(pane));
  pane.querySelector('[data-action="cover"]').addEventListener('click', () => runCoverAuto(pane));
  pane.querySelector('[data-field="theme"]').addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      generate(pane);
    }
  });
}

function updateCoverBtn(pane) {
  const type = pane.querySelector('[data-field="type"]').value;
  const btn = pane.querySelector('[data-action="cover"]');
  // カバー画像は X記事 のみ対象
  btn.hidden = (type !== 'x_article');
  updateThemePlaceholder(pane);
}

function updateThemePlaceholder(pane) {
  const type = pane.querySelector('[data-field="type"]').value;
  const ta = pane.querySelector('[data-field="theme"]');
  if (type === 'reply_post') {
    ta.placeholder = '元ポストの原文をそのまま貼ってください（Cmd+Enterで生成）\n例：noteのフォロワー1000人いるのに、月3000円しか売れない…';
  } else if (type === 'quote_post') {
    ta.placeholder = '引用RTする元ポストの原文をそのまま貼ってください（Cmd+Enterで生成）';
  } else if (type === 'thread_reply') {
    ta.placeholder = 'やりとりをそのままコピペ（最後の相手の発言にリプします）\n例：\n@taro: 最近note伸びないんですよね\n自分: 分かります、X側の入口で詰まる人多いですよね\n@taro: あー、確かに告知だけになってました…';
  } else {
    ta.placeholder = 'テーマ・指示（Cmd+Enterで生成）\n例：『初めての100円が動いた日』を長文で。引用・リプを呼ぶ構造で。';
  }
}

function closePane(pane) {
  if (workspace.querySelectorAll('.pane').length <= 1) {
    resetPane(pane);
    return;
  }
  pane.remove();
  relayout();
}

function resetPane(pane) {
  pane.querySelector('[data-field="theme"]').value = '';
  pane.querySelector('[data-field="extra"]').value = '';
  pane.querySelector('[data-field="output"]').textContent = '';
  pane.querySelector('[data-field="status"]').textContent = '';
  pane.querySelector('[data-field="status"]').className = 'status';
  pane.querySelector('[data-action="copy"]').disabled = true;
  pane.querySelector('[data-action="copy"]').classList.remove('copied');
  pane.querySelector('[data-action="copy"]').textContent = 'コピー';
  pane.querySelector('[data-action="cover"]').disabled = true;
  const cover = pane.querySelector('[data-field="cover-preview"]');
  cover.hidden = true;
  cover.innerHTML = '';
  delete pane.dataset.historyId;
}

function addPane() {
  const pane = makePane();
  workspace.appendChild(pane);
  relayout();
}

function relayout() {
  const n = workspace.querySelectorAll('.pane').length;
  if (n === 0) return;
  let cols, rows;
  if (n === 1) { cols = 1; rows = 1; }
  else if (n === 2) { cols = 2; rows = 1; }
  else if (n === 3) { cols = 3; rows = 1; }
  else if (n === 4) { cols = 2; rows = 2; }
  else if (n <= 6) { cols = 3; rows = 2; }
  else if (n <= 9) { cols = 3; rows = 3; }
  else { cols = 4; rows = Math.ceil(n / 4); }
  workspace.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  workspace.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
}

// ============ 生成 ============
const META_RE = /<!--POST_STUDIO_META id=(\d+) rag_refs=(\d+)(?: auth=(\w+))?(?: in=(\d+))?(?: out=(\d+))?(?: cost_usd=([\d.]+))?-->/;

async function generate(pane) {
  const brand = pane.querySelector('[data-field="brand"]').value;
  const type = pane.querySelector('[data-field="type"]').value;
  const theme = pane.querySelector('[data-field="theme"]').value.trim();
  const extra = pane.querySelector('[data-field="extra"]').value.trim();
  const useRag = pane.querySelector('[data-field="use_rag"]').checked;
  const output = pane.querySelector('[data-field="output"]');
  const status = pane.querySelector('[data-field="status"]');
  const genBtn = pane.querySelector('[data-action="generate"]');
  const copyBtn = pane.querySelector('[data-action="copy"]');
  const coverBtn = pane.querySelector('[data-action="cover"]');

  if (!theme) {
    status.textContent = 'テーマを入力してください';
    status.className = 'status error';
    return;
  }

  output.innerHTML = '';
  status.textContent = useRag ? 'RAG検索中…' : '生成中…';
  status.className = 'status streaming';
  genBtn.disabled = true;
  copyBtn.disabled = true;
  coverBtn.disabled = true;

  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brand, type, theme, extra, use_rag: useRag }),
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`HTTP ${res.status}: ${err}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      renderOutput(output, stripMeta(buf));
      output.scrollTop = output.scrollHeight;
      status.textContent = `生成中… ${stripMeta(buf).length}字`;
    }
    buf += decoder.decode();
    const meta = buf.match(META_RE);
    const clean = stripMeta(buf);
    renderOutput(output, clean);
    output.dataset.raw = clean;
    if (meta) {
      pane.dataset.historyId = meta[1];
      const refs = parseInt(meta[2], 10) || 0;
      const auth = meta[3] || '';
      const tin = parseInt(meta[4] || '0', 10);
      const tout = parseInt(meta[5] || '0', 10);
      const costUsd = parseFloat(meta[6] || '0');
      let costLabel = '';
      if (auth === 'api') {
        const yen = costUsd * 150;
        costLabel = `・API課金 約${yen < 1 ? yen.toFixed(1) : Math.ceil(yen)}円`;
      } else if (auth === 'subscription') {
        costLabel = '・サブスク枠 ¥0';
      }
      status.textContent = `完了（${clean.length}字${refs ? `・RAG参考${refs}件` : ''}${costLabel}）`;
      if (tin || tout) status.title = `トークン: 入力 ${tin.toLocaleString()} / 出力 ${tout.toLocaleString()}`;
      refreshCostCounter();
    } else {
      status.textContent = `完了（${clean.length}字）`;
    }
    status.className = 'status';
    copyBtn.disabled = false;
    coverBtn.disabled = false;
    // タイプによってはカウンターを更新（リプの即時反映）
    if (type === 'reply_post') refreshReplyCounter();
  } catch (e) {
    status.textContent = `エラー: ${e.message}`;
    status.className = 'status error';
    console.error(e);
  } finally {
    genBtn.disabled = false;
  }
}

function stripMeta(text) {
  return text.replace(META_RE, '').trimEnd();
}

function renderOutput(el, text) {
  el.innerHTML = '';
  text.split('\n').forEach((line, i, arr) => {
    const s = line.trim();
    if (s.startsWith('【') && s.endsWith('】')) {
      const span = document.createElement('span');
      span.className = 'h';
      span.textContent = line;
      el.appendChild(span);
    } else {
      el.appendChild(document.createTextNode(line));
    }
    if (i < arr.length - 1) el.appendChild(document.createTextNode('\n'));
  });
}

async function copyOutput(pane) {
  const output = pane.querySelector('[data-field="output"]');
  const copyBtn = pane.querySelector('[data-action="copy"]');
  const raw = output.dataset.raw || output.textContent;
  try {
    await navigator.clipboard.writeText(raw);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = raw; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
  // コピー成功後、指示欄をクリアして次の生成に備える
  pane.querySelector('[data-field="theme"]').value = '';
  pane.querySelector('[data-field="extra"]').value = '';
  // テーマ入力欄にフォーカスを戻す
  pane.querySelector('[data-field="theme"]').focus();

  copyBtn.classList.add('copied');
  copyBtn.textContent = 'コピー済み';
  setTimeout(() => {
    copyBtn.classList.remove('copied');
    copyBtn.textContent = 'コピー';
  }, 2000);
}

// ============ 履歴 ============
async function openHistoryModal() {
  historyModal.hidden = false;
  await refreshHistory();
}
function closeHistoryModal() { historyModal.hidden = true; }

async function refreshHistory() {
  historyList.innerHTML = '<div class="history-empty">読み込み中…</div>';
  const brand = historyBrandFilter.value;
  const qs = brand ? `?brand=${encodeURIComponent(brand)}` : '';
  try {
    const res = await fetch(`/history${qs}`);
    const { items } = await res.json();
    if (!items.length) {
      historyList.innerHTML = '<div class="history-empty">履歴がありません</div>';
      return;
    }
    historyList.innerHTML = '';
    for (const it of items) {
      const el = document.createElement('div');
      el.className = 'history-item';
      const dt = it.created_at ? new Date(it.created_at).toLocaleString('ja-JP') : '';
      el.innerHTML = `
        <div class="h-row1">
          <span class="h-tag brand">${brandLabel(it.brand)}</span>
          <span class="h-tag">${typeLabel(it.type)}</span>
          ${it.rag_refs ? `<span class="h-tag">RAG×${it.rag_refs}</span>` : ''}
          <span style="flex:1"></span>
          <span>${dt}</span>
        </div>
        <div class="h-theme"></div>
        <div class="h-preview"></div>
      `;
      el.querySelector('.h-theme').textContent = it.theme;
      el.querySelector('.h-preview').textContent = it.preview;
      el.addEventListener('click', () => openHistoryItem(it.id));
      historyList.appendChild(el);
    }
  } catch (e) {
    historyList.innerHTML = `<div class="history-empty">エラー: ${e.message}</div>`;
  }
}

function typeLabel(t) {
  return {
    long_post: '長文', short_post: '短文', quote_post: '引用',
    reply_post: 'リプ', thread_reply: '会話リプ', x_article: 'X記事',
  }[t] || t;
}

async function openHistoryItem(id) {
  closeHistoryModal();
  try {
    const res = await fetch(`/history/${id}`);
    const item = await res.json();
    addPane();
    const all = workspace.querySelectorAll('.pane');
    const pane = all[all.length - 1];
    pane.querySelector('[data-field="brand"]').value = item.brand;
    pane.querySelector('[data-field="type"]').value = item.type;
    pane.querySelector('[data-field="theme"]').value = item.theme;
    pane.querySelector('[data-field="extra"]').value = item.extra || '';
    pane.querySelector('[data-field="use_rag"]').checked = !!item.use_rag;
    const output = pane.querySelector('[data-field="output"]');
    output.dataset.raw = item.output;
    renderOutput(output, item.output);
    pane.querySelector('[data-action="copy"]').disabled = false;
    pane.querySelector('[data-action="cover"]').disabled = false;
    updateCoverBtn(pane);
    pane.dataset.historyId = item.id;
    const status = pane.querySelector('[data-field="status"]');
    status.textContent = `履歴#${item.id}を復元`;
    status.className = 'status';
  } catch (e) {
    alert(`履歴復元失敗: ${e.message}`);
  }
}

// ============ まとめて生成 ============
// 全ペインのうち、テーマが入力されているものすべての ▶生成 を一括実行
function runBatchAll() {
  const all = Array.from(workspace.querySelectorAll('.pane'));
  const targets = all.filter(pane => {
    const theme = pane.querySelector('[data-field="theme"]').value.trim();
    const genBtn = pane.querySelector('[data-action="generate"]');
    return theme && !genBtn.disabled;
  });
  if (!targets.length) {
    alert('テーマが入力された生成可能なペインがありません。');
    return;
  }
  targets.forEach(p => generate(p));
}

// ============ バズピックアップ ============
const buzzModal = document.getElementById('buzz-modal');
const buzzList = document.getElementById('buzz-list');
const buzzSummary = document.getElementById('buzz-summary');

async function openBuzzModal() {
  buzzModal.hidden = false;
  await refreshBuzz();
}
function closeBuzzModal() { buzzModal.hidden = true; }

async function refreshBuzz() {
  buzzList.innerHTML = '<div class="buzz-empty">読み込み中…</div>';
  buzzSummary.textContent = '';
  const hours = document.getElementById('buzz-hours').value;
  const limit = document.getElementById('buzz-limit').value || 40;
  const minImp = document.getElementById('buzz-min-imp').value || 0;
  const keyword = document.getElementById('buzz-keyword').value.trim();
  const postType = document.getElementById('buzz-post-type').value;
  const qs = new URLSearchParams({ hours, limit, min_impressions: minImp, post_type: postType });
  if (keyword) qs.set('keyword', keyword);
  try {
    const res = await fetch(`/buzz/recent?${qs.toString()}`);
    const { items, stats } = await res.json();
    if (stats) {
      const latest = stats.latest_posted_at ? new Date(stats.latest_posted_at).toLocaleString('ja-JP') : '—';
      buzzSummary.textContent = `DB総数 ${stats.total} 件 / 過去24h ${stats.last_24h} / 過去7日 ${stats.last_7d} / 最新収集: ${latest}`;
    }
    if (!items.length) {
      buzzList.innerHTML = '<div class="buzz-empty">該当ポストなし。期間を広げるか、最低インプを下げてみてください。</div>';
      return;
    }
    buzzList.innerHTML = '';
    for (const it of items) {
      buzzList.appendChild(renderBuzzItem(it));
    }
  } catch (e) {
    buzzList.innerHTML = `<div class="buzz-empty">エラー: ${e.message}</div>`;
  }
}

function relativeTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${min}分前`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}時間前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}日前`;
  const mo = Math.floor(d / 30);
  return `${mo}ヶ月前`;
}

function renderBuzzItem(it) {
  const el = document.createElement('div');
  el.className = 'buzz-item';
  const eng = it.engagement_rate != null ? `${it.engagement_rate.toFixed(1)}%` : '—';
  const imp = it.impressions ? it.impressions.toLocaleString() : '—';
  const fol = it.author_followers ? it.author_followers.toLocaleString() : '—';
  const when = relativeTime(it.posted_at);
  const typeTag = it.post_type === 'article' ? '<span class="b-tag b-tag-article">X記事</span>'
                : it.post_type === 'long_tweet' ? '<span class="b-tag">長文</span>'
                : it.post_type === 'thread_reply' ? '<span class="b-tag">ツリー</span>'
                : '';
  el.innerHTML = `
    <div class="b-row1">
      <a class="b-author" href="${it.url}" target="_blank" rel="noopener">@${it.author_username}</a>
      <span class="b-meta">フォロワー ${fol}</span>
      ${typeTag}
      <span style="flex:1"></span>
      <span class="b-meta">${when}</span>
    </div>
    <div class="b-text"></div>
    ${it.article_title ? `<div class="b-title">${it.article_title}</div>` : ''}
    <div class="b-row2">
      <span class="b-stat b-imp">表示 ${imp}</span>
      <span class="b-stat">反応率 ${eng}</span>
      <span class="b-stat">いいね ${it.likes.toLocaleString()}</span>
      <span class="b-stat">RT ${it.retweets.toLocaleString()}</span>
      <span class="b-stat">リプ ${it.replies.toLocaleString()}</span>
      <span style="flex:1"></span>
      <a class="b-url" href="${it.url}" target="_blank" rel="noopener" title="クリックで開く">${it.url}</a>
      <button type="button" class="b-use" data-action="quote">引用RT用に流す</button>
      <button type="button" class="b-use" data-action="reply">リプ用に流す</button>
    </div>
  `;
  el.querySelector('.b-text').textContent = it.text;
  el.querySelector('[data-action="quote"]').addEventListener('click', () => sendToPane(it, 'quote_post'));
  el.querySelector('[data-action="reply"]').addEventListener('click', () => sendToPane(it, 'reply_post'));
  return el;
}

function sendToPane(item, type) {
  closeBuzzModal();
  addPane();
  const all = workspace.querySelectorAll('.pane');
  const pane = all[all.length - 1];
  pane.querySelector('[data-field="type"]').value = type;
  updateCoverBtn(pane);  // プレースホルダ & カバーボタン状態も同期
  const themeTa = pane.querySelector('[data-field="theme"]');
  // 引用/リプ生成のテーマ欄に元ポストをそのまま流し込む（@user は手がかりとして添える）
  const header = `@${item.author_username} の元ポスト（${item.url}）:`;
  themeTa.value = `${header}\n\n${item.text}`;
  themeTa.focus();
  const status = pane.querySelector('[data-field="status"]');
  status.textContent = `バズから流し込み: ${type === 'quote_post' ? '引用RT' : 'リプ'} 準備完了`;
  status.className = 'status';
}

// ============ カバー画像生成（自動）============
function closeAnyModal() {
  if (!historyModal.hidden) closeHistoryModal();
  if (!buzzModal.hidden) closeBuzzModal();
  const um = document.getElementById('usage-modal');
  if (um && !um.hidden) um.hidden = true;
  const acc = document.getElementById('accounts-modal');
  if (acc && !acc.hidden) acc.hidden = true;
  const ae = document.getElementById('account-edit-modal');
  if (ae && !ae.hidden) ae.hidden = true;
  const im = document.getElementById('ideas-modal');
  if (im && !im.hidden) im.hidden = true;
}

async function runCoverAuto(pane) {
  const output = pane.querySelector('[data-field="output"]');
  const text = output.dataset.raw || output.textContent || '';
  const status = pane.querySelector('[data-field="status"]');
  const coverBtn = pane.querySelector('[data-action="cover"]');
  const brand = pane.querySelector('[data-field="brand"]').value;

  if (!text.trim()) {
    status.textContent = 'カバー画像生成には先に本文生成が必要';
    status.className = 'status error';
    return;
  }

  status.textContent = '3行抽出→画像生成中（30〜60秒）…';
  status.className = 'status streaming';
  coverBtn.disabled = true;

  try {
    const body = { article_text: text, brand };
    if (pane.dataset.historyId) {
      body.history_id = parseInt(pane.dataset.historyId, 10);
    }
    const res = await fetch('/generate_cover_auto', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const e = await res.text();
      throw new Error(`HTTP ${res.status}: ${e}`);
    }
    const { url, lines } = await res.json();
    const ts = Date.now();
    const cacheBust = `${url}?t=${ts}`;

    // ペインにプレビュー
    const preview = pane.querySelector('[data-field="cover-preview"]');
    preview.innerHTML = `<img src="${cacheBust}" alt="cover">`;
    preview.hidden = false;

    // 自動ダウンロード
    triggerDownload(cacheBust, `cover_${ts}.png`);

    const linePreview = `${lines.line1} / ${lines.line2} / ${lines.line3}`;
    status.textContent = `カバー生成・DL完了 (${linePreview})`;
    status.className = 'status';
  } catch (e) {
    status.textContent = `カバー生成エラー: ${e.message}`;
    status.className = 'status error';
  } finally {
    coverBtn.disabled = false;
  }
}

function triggerDownload(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 100);
}

// ============ Topbar / Modal bindings ============
document.getElementById('add-pane-h').addEventListener('click', () => addPane());
document.getElementById('clear-all').addEventListener('click', () => {
  workspace.innerHTML = '';
  addPane();
});
document.getElementById('open-history').addEventListener('click', openHistoryModal);
document.getElementById('history-refresh').addEventListener('click', refreshHistory);
historyBrandFilter.addEventListener('change', refreshHistory);
document.getElementById('open-batch').addEventListener('click', runBatchAll);
document.getElementById('open-buzz').addEventListener('click', openBuzzModal);
document.getElementById('buzz-refresh').addEventListener('click', refreshBuzz);
document.getElementById('buzz-hours').addEventListener('change', refreshBuzz);
document.getElementById('buzz-post-type').addEventListener('change', refreshBuzz);
document.getElementById('buzz-keyword').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); refreshBuzz(); }
});

// イベント委譲: data-modal-close 属性を持つ要素のクリックを document レベルでキャッチ
// （個別 addEventListener より堅牢、DOM 再生成にも対応）
document.addEventListener('click', (e) => {
  const closeEl = e.target.closest('[data-modal-close]');
  if (!closeEl) return;
  e.preventDefault();
  e.stopPropagation();
  const target = closeEl.dataset.modalClose;
  if (target === 'history') closeHistoryModal();
  if (target === 'buzz') closeBuzzModal();
  if (target === 'usage') usageModal.hidden = true;
  if (target === 'accounts') accountsModal.hidden = true;
  if (target === 'accountedit') document.getElementById('account-edit-modal').hidden = true;
  if (target === 'ideas') ideasModal.hidden = true;
}, true); // capture フェーズで先取り

// ESCキーでモーダル閉じる
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeAnyModal();
});

// 初期ペイン
addPane();
refreshBrands();  // 新規作成したアカウントをプルダウンに反映

// リプ日次カウンター
async function refreshReplyCounter() {
  try {
    const res = await fetch('/history/stats/today?type=reply_post,thread_reply');
    const { count } = await res.json();
    document.getElementById('reply-counter-num').textContent = count;
  } catch (e) { /* 失敗時は黙ってスキップ */ }
}
refreshReplyCounter();
// 1分ごとに自動更新
setInterval(refreshReplyCounter, 60_000);

// ============ 使用量とコスト ============
const usageModal = document.getElementById('usage-modal');
let lastUsage = null;

function yen(v) {
  if (v <= 0) return '¥0';
  return v < 1 ? `約${v.toFixed(1)}円` : `約${Math.ceil(v).toLocaleString()}円`;
}

async function refreshCostCounter() {
  try {
    const res = await fetch('/usage');
    if (!res.ok) return;
    const u = await res.json();
    lastUsage = u;
    const el = document.getElementById('cost-counter-num');
    if (!el) return;
    const monthApi = u.stats?.month?.api || { cost_usd: 0 };
    const monthJpy = monthApi.cost_usd * u.usd_jpy_rate;
    const b = u.budget || {};
    // 課金許可中はオレンジで注意喚起、予算超過は赤、それ以外は通常色
    el.textContent = b.charges_allowed ? `${yen(monthJpy)}（課金許可中）` : yen(monthJpy);
    el.style.color = b.charges_allowed ? 'var(--warn)'
      : (b.monthly_jpy > 0 && b.remaining_jpy <= 0) ? 'var(--danger)' : '';
    if (usageModal && !usageModal.hidden) renderUsageDetail(u);
  } catch (e) { /* 表示だけの機能なので黙って無視 */ }
}

function usageRow(label, s, rate, isApi) {
  const tok = `入力 ${s.input_tokens.toLocaleString()} / 出力 ${s.output_tokens.toLocaleString()} tok`;
  const cost = isApi ? yen(s.cost_usd * rate) : '¥0';
  return `<tr>
    <td style="padding:4px 12px 4px 0; white-space:nowrap">${label}</td>
    <td style="padding:4px 12px; text-align:right">${s.count}本</td>
    <td style="padding:4px 12px; white-space:nowrap; color:var(--muted,#888)">${tok}</td>
    <td style="padding:4px 0; text-align:right; font-weight:600">${cost}</td>
  </tr>`;
}

function renderUsageDetail(u) {
  const box = document.getElementById('usage-detail');
  if (!box) return;
  const rate = u.usd_jpy_rate;
  const st = u.stats || {};
  const sub = u.subscription || {};
  const b = u.budget || {};

  const subStatus = sub.available_now
    ? '<span style="color:var(--green); font-weight:600">利用可能</span>'
    : `<span style="color:var(--danger); font-weight:600">上限中（${new Date(sub.blocked_until * 1000).toLocaleTimeString('ja-JP')} 頃に復帰 → それまでAPI課金で生成）</span>`;

  const allowed = !!b.charges_allowed;
  const stateBanner = allowed
    ? `<div class="budget-banner banner-warn"><b>API課金を許可中（予算ブロック解除）</b>
        <div class="banner-sub">サブスクが上限に達したときは従量課金（有料）で生成されます。使い終わったら「ブロックに戻す」を押してください。</div></div>`
    : `<div class="budget-banner banner-safe"><b>API課金をブロック中（追加課金なし）</b>
        <div class="banner-sub">サブスク枠のみで生成します。上限に達した間は生成を一時停止し、課金は一切発生しません。</div></div>`;
  const toggleBtn = `<button type="button" id="toggle-api-charges" class="toggle-btn ${allowed ? 'toggle-on' : 'toggle-off'}" data-allowed="${allowed ? '1' : '0'}">${allowed ? 'ブロックに戻す（課金を止める）' : 'ブロックを解除してAPI課金を許可する'}</button>`;

  let budgetNumeric = '';
  if (b.monthly_jpy > 0) {
    const spent = b.spent_month_jpy;
    const pct = Math.min(100, (spent / b.monthly_jpy) * 100);
    const barColor = pct >= 100 ? 'var(--danger)' : pct >= 70 ? 'var(--warn)' : 'var(--accent)';
    budgetNumeric = `
      <div style="margin:12px 0 4px">今月の予算 ${yen(b.monthly_jpy)} のうち <b>${yen(spent)}</b> 使用 —
        残り <b style="color:${b.remaining_jpy <= 0 ? 'var(--danger)' : 'inherit'}">${b.remaining_jpy <= 0 ? '0円（予算超過）' : yen(b.remaining_jpy)}</b></div>
      <div class="budget-bar"><div style="width:${pct}%; background:${barColor}"></div></div>`;
  } else if (b.monthly_jpy === 0) {
    budgetNumeric = `<div class="muted" style="margin-top:8px">.env の予算は 0円（既定は課金ブロック）。上のボタンで一時的に解除できます。</div>`;
  } else {
    budgetNumeric = `<div class="muted" style="margin-top:8px">予算は未設定。.env に <code>API_MONTHLY_BUDGET_JPY=750</code> のように書くと「残りいくら」を表示します。</div>`;
  }
  const budgetHtml = stateBanner + `<div style="margin:12px 0">${toggleBtn}</div>` + budgetNumeric;

  const section = (title, p) => `
    <h3 style="margin:18px 0 6px; font-size:14px">${title}</h3>
    <table style="border-collapse:collapse; font-size:13px; width:100%">
      ${usageRow('サブスク（追加課金なし）', p.subscription, rate, false)}
      ${usageRow('API（従量課金）', p.api, rate, true)}
    </table>`;

  box.innerHTML = `
    <div style="font-size:13.5px">
      <h3 style="margin:0 0 6px; font-size:14px">サブスク（Claude Code プラン）の状態</h3>
      <div>${subStatus}</div>
      <div style="color:var(--muted,#888); font-size:12.5px; margin-top:2px">
        ※ プラン枠の正確な残量はAPIから取得できません。ターミナルで <code>claude</code> を開いて <code>/usage</code> と打つと確認できます。
      </div>
      <h3 style="margin:18px 0 6px; font-size:14px">今月のAPI課金と予算</h3>
      ${budgetHtml}
      ${section('今日', st.today || {})}
      ${section('今月', st.month || {})}
      ${section('全期間', st.total || {})}
      <div style="color:var(--muted,#888); font-size:12px; margin-top:14px">
        円換算は $1=${rate}円 の概算。API単価: 入力$3 / 出力$15（100万トークンあたり・Sonnet）。
        トークン集計は本日以降の生成分から記録しています。
      </div>
    </div>`;
}

function openUsageModal() {
  usageModal.hidden = false;
  if (lastUsage) renderUsageDetail(lastUsage);
  refreshCostCounter();
}

async function setApiCharges(allowed) {
  if (allowed) {
    const ok = window.confirm(
      'API課金のブロックを解除します。\n\n'
      + 'サブスクが上限に達している間は、Anthropic APIの従量課金（有料）で生成されます。'
      + '\n本当に解除しますか？'
    );
    if (!ok) return;
  }
  try {
    const res = await fetch('/settings/allow_api_charges', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ allowed }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await refreshCostCounter();  // 新しい状態で再描画
  } catch (e) {
    alert(`切り替えに失敗しました: ${e.message}`);
  }
}

// 使用量モーダル内のトグルボタン（innerHTML再生成に強い委譲）
document.getElementById('usage-detail').addEventListener('click', (e) => {
  const btn = e.target.closest('#toggle-api-charges');
  if (btn) setApiCharges(btn.dataset.allowed !== '1');  // 現在の逆に切り替え
});

document.getElementById('cost-counter').addEventListener('click', openUsageModal);
document.getElementById('usage-refresh').addEventListener('click', refreshCostCounter);
refreshCostCounter();

// ============ ブランド一覧（組み込み + 新規作成分） ============
let BRANDS = [
  { key: 'taro', label: 'タロ' },
  { key: 'sheep', label: 'しーぷ' },
  { key: 'umai', label: '馬井' },
];

function brandLabel(key) {
  const b = BRANDS.find((x) => x.key === key);
  return b ? b.label : key;
}

function fillBrandSelect(sel, { includeAll = false } = {}) {
  if (!sel) return;
  const cur = sel.value;
  const list = BRANDS.filter((b) => !b.hidden);  // 非表示にした組み込みは出さない
  sel.innerHTML = (includeAll ? '<option value="">すべてのブランド</option>' : '')
    + list.map((b) => `<option value="${b.key}">${esc(b.label)}</option>`).join('');
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
}

async function refreshBrands() {
  try {
    const res = await fetch('/brands');
    if (!res.ok) return;
    BRANDS = (await res.json()).brands;
    // ペインのテンプレと既存ペイン、各モーダルのプルダウンを更新
    fillBrandSelect(document.getElementById('pane-template').content.querySelector('.select-brand'));
    document.querySelectorAll('#workspace .select-brand').forEach((s) => fillBrandSelect(s));
    fillBrandSelect(document.getElementById('ideas-brand'));
    fillBrandSelect(document.getElementById('history-brand-filter'), { includeAll: true });
  } catch (e) { /* 表示だけの機能なので黙って無視 */ }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ============ アカウント管理（新規作成・名前変更・削除） ============
const accountsModal = document.getElementById('accounts-modal');
const accountStatus = document.getElementById('account-status');

async function openAccountsModal() {
  accountsModal.hidden = false;
  await refreshBrands();   // builtin/hidden フラグを最新化してから描画
  renderAccountList();
}

function renderAccountList() {
  const box = document.getElementById('account-list');
  if (!box) return;
  const builtins = BRANDS.filter((b) => b.builtin);
  const customs = BRANDS.filter((b) => b.custom);

  const builtinRows = builtins.map((b) => `
    <div style="display:flex; align-items:center; gap:8px">
      <span style="flex:1; font-weight:600; ${b.hidden ? 'opacity:.5' : ''}">${esc(b.label)}${b.hidden ? '（非表示中）' : ''}</span>
      <button type="button" data-hide="${esc(b.key)}" data-to="${b.hidden ? '0' : '1'}">${b.hidden ? '再表示' : '非表示'}</button>
    </div>`).join('');

  const customRows = customs.length
    ? customs.map((b) => `
      <div style="display:flex; align-items:center; gap:8px">
        <span style="flex:1; font-weight:600">${esc(b.label)}</span>
        <button type="button" class="primary" data-edit="${esc(b.key)}">作り込む</button>
        <button type="button" data-rename="${esc(b.key)}">名前変更</button>
        <button type="button" class="danger-btn" data-delete="${esc(b.key)}">削除</button>
      </div>`).join('')
    : '<div style="color:var(--muted,#888)">まだ作成したアカウントはありません。</div>';

  box.innerHTML = `
    <div>
      <b>組み込みアカウント</b>
      <div style="margin-top:8px; display:flex; flex-direction:column; gap:6px">${builtinRows}</div>
      <div class="muted" style="font-size:12px; margin-top:6px">※ 削除はできませんが「非表示」で一覧から隠せます（いつでも再表示できます）。</div>
    </div>
    <div>
      <b>作成済みアカウント</b>
      <div style="margin-top:8px; display:flex; flex-direction:column; gap:6px">${customRows}</div>
    </div>`;
}

async function createAccount() {
  const input = document.getElementById('account-new-name');
  const name = input.value.trim();
  if (!name) { accountStatus.textContent = '表示名を入力してください'; return; }
  accountStatus.textContent = '作成中…';
  try {
    const res = await fetch('/brands', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    input.value = '';
    await refreshBrands();
    renderAccountList();
    accountStatus.textContent = `「${name}」を作成しました。ペインのプルダウンから選べます。`;
    setTimeout(() => { accountStatus.textContent = ''; }, 5000);
  } catch (e) {
    accountStatus.textContent = `作成エラー: ${e.message}`;
  }
}

async function renameAccount(key) {
  const current = brandLabel(key);
  const name = window.prompt('新しいアカウント名を入力してください', current);
  if (!name || !name.trim() || name.trim() === current) return;
  accountStatus.textContent = '変更中…';
  try {
    const res = await fetch(`/brands/${key}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await refreshBrands();
    renderAccountList();
    accountStatus.textContent = `名前を「${name.trim()}」に変更しました`;
    setTimeout(() => { accountStatus.textContent = ''; }, 5000);
  } catch (e) {
    accountStatus.textContent = `変更エラー: ${e.message}`;
  }
}

async function deleteAccount(key) {
  const label = brandLabel(key);
  if (!window.confirm(`アカウント「${label}」を削除します。一覧とペインのプルダウンから消えます。\n\n本当に削除しますか？`)) return;
  // 履歴も一緒に消すかを選ばせる（キャンセルなら履歴は残す）
  const withHistory = window.confirm(
    `「${label}」の生成履歴も一緒に削除しますか？\n\n`
    + '［OK］履歴もすべて削除する（元に戻せません）\n'
    + '［キャンセル］アカウントだけ消して履歴は残す'
  );
  accountStatus.textContent = '削除中…';
  try {
    const res = await fetch(`/brands/${key}?with_history=${withHistory}`, { method: 'DELETE' });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t.slice(0, 120) || `HTTP ${res.status}`);
    }
    const data = await res.json();
    await refreshBrands();
    renderAccountList();
    refreshCostCounter();
    const hist = data.removed_history ? `（履歴${data.removed_history}件も削除）` : '（履歴は保持）';
    accountStatus.textContent = `「${label}」を削除しました${hist}`;
    setTimeout(() => { accountStatus.textContent = ''; }, 6000);
  } catch (e) {
    accountStatus.textContent = `削除エラー: ${e.message}`;
  }
}

async function setBuiltinHidden(key, hidden) {
  accountStatus.textContent = hidden ? '非表示にしています…' : '再表示にしています…';
  try {
    const res = await fetch(`/brands/${key}/hidden`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hidden }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t.slice(0, 140) || `HTTP ${res.status}`);
    }
    const label = brandLabel(key);
    await refreshBrands();
    renderAccountList();
    accountStatus.textContent = hidden ? `「${label}」を非表示にしました` : `「${label}」を再表示しました`;
    setTimeout(() => { accountStatus.textContent = ''; }, 5000);
  } catch (e) {
    accountStatus.textContent = `切り替えエラー: ${e.message}`;
  }
}

document.getElementById('open-accounts').addEventListener('click', openAccountsModal);
document.getElementById('account-create').addEventListener('click', createAccount);
document.getElementById('account-new-name').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); createAccount(); }
});
document.getElementById('account-list').addEventListener('click', (e) => {
  const renameBtn = e.target.closest('[data-rename]');
  if (renameBtn) { renameAccount(renameBtn.dataset.rename); return; }
  const deleteBtn = e.target.closest('[data-delete]');
  if (deleteBtn) { deleteAccount(deleteBtn.dataset.delete); return; }
  const editBtn = e.target.closest('[data-edit]');
  if (editBtn) { openAccountEdit(editBtn.dataset.edit); return; }
  const hideBtn = e.target.closest('[data-hide]');
  if (hideBtn) { setBuiltinHidden(hideBtn.dataset.hide, hideBtn.dataset.to === '1'); }
});

// ============ アカウント作り込み（voiceビルダー＋過去ポスト学習） ============
const accountEditModal = document.getElementById('account-edit-modal');
const VOICE_FIELDS = ['first_person', 'tone', 'theme_areas', 'target_audience',
  'achievements', 'ng_topics', 'emoji_style', 'extra_rules'];
let aeKey = null;

async function openAccountEdit(key) {
  aeKey = key;
  document.getElementById('ae-title').textContent = brandLabel(key);
  document.getElementById('ae-status').textContent = '';
  document.getElementById('ae-ingest-status').textContent = '';
  document.getElementById('ae-posts').value = '';
  VOICE_FIELDS.forEach((f) => { const el = document.getElementById('ae-' + f); if (el) el.value = ''; });
  document.getElementById('ae-learned').textContent = '';
  accountEditModal.hidden = false;
  try {
    const res = await fetch(`/brands/${key}/voice`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    const p = d.profile || {};
    VOICE_FIELDS.forEach((f) => { const el = document.getElementById('ae-' + f); if (el) el.value = p[f] || ''; });
    document.getElementById('ae-learned').textContent =
      d.learned_posts ? `　学習済み ${d.learned_posts}件` : '　まだ学習していません';
  } catch (e) {
    document.getElementById('ae-status').textContent = `読込エラー: ${e.message}`;
  }
}

async function saveVoice() {
  if (!aeKey) return;
  const status = document.getElementById('ae-status');
  status.textContent = '保存中…';
  const body = {};
  VOICE_FIELDS.forEach((f) => { body[f] = document.getElementById('ae-' + f).value; });
  try {
    const res = await fetch(`/brands/${aeKey}/voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    status.textContent = '声を保存しました（次の生成から反映）';
    setTimeout(() => { status.textContent = ''; }, 4000);
  } catch (e) {
    status.textContent = `保存エラー: ${e.message}`;
  }
}

async function ingestPosts() {
  if (!aeKey) return;
  const text = document.getElementById('ae-posts').value.trim();
  const status = document.getElementById('ae-ingest-status');
  if (!text) { status.textContent = '過去ポストを貼ってください（空行で区切る）'; return; }
  status.innerHTML = '<span class="spinner"></span>学習中…';
  const btn = document.getElementById('ae-ingest');
  btn.disabled = true;
  try {
    const res = await fetch(`/brands/${aeKey}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) { const t = await res.text(); throw new Error(t.slice(0, 160) || `HTTP ${res.status}`); }
    const d = await res.json();
    status.textContent = `${d.added}件を学習しました（合計 ${d.total}件）`;
    document.getElementById('ae-learned').textContent = `　学習済み ${d.total}件`;
    document.getElementById('ae-posts').value = '';
  } catch (e) {
    status.textContent = `学習エラー: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('ae-save').addEventListener('click', saveVoice);
document.getElementById('ae-ingest').addEventListener('click', ingestPosts);

// ============ ネタ出し（テーマ・指示の提案） ============
const ideasModal = document.getElementById('ideas-modal');
const ideasStatus = document.getElementById('ideas-status');
const ideasResults = document.getElementById('ideas-results');
let ideasData = null;
let ideasTimer = null;

const IDEA_KIND_COLORS = {
  '実体験': '#c9a96e', 'ノウハウ': '#5ec7a2', '逆張り主張': '#e06060',
  'あるある共感': '#7f8fe0', '失敗談': '#d9a441', 'チェックリスト': '#5eb8c7',
};

function startIdeasProgress() {
  const t0 = Date.now();
  const tick = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    ideasStatus.innerHTML =
      `<span class="spinner"></span><span class="progress-dots">テーマ設定と過去の投稿履歴を踏まえてネタを考えています</span>`
      + `　<span style="opacity:.7">${sec}秒経過（目安30秒〜1分）</span>`;
  };
  tick();
  ideasTimer = setInterval(tick, 1000);
}

function stopIdeasProgress() {
  if (ideasTimer) { clearInterval(ideasTimer); ideasTimer = null; }
}

async function runIdeas() {
  const brand = document.getElementById('ideas-brand').value;
  const count = parseInt(document.getElementById('ideas-count').value, 10);
  const btn = document.getElementById('ideas-run');
  btn.disabled = true;
  startIdeasProgress();
  ideasResults.innerHTML = '';
  try {
    const res = await fetch('/suggest_themes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brand, count }),
    });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200));
    ideasData = await res.json();
    stopIdeasProgress();
    ideasStatus.textContent = `${ideasData.ideas.length}本のネタを提案しました。「ペインに流す」でそのまま生成できます。`;
    renderIdeas(ideasData, brand);
  } catch (e) {
    stopIdeasProgress();
    ideasStatus.textContent = `生成エラー: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
}

function renderIdeas(d, brand) {
  ideasResults.innerHTML = d.ideas.map((idea, i) => {
    const color = IDEA_KIND_COLORS[idea.kind] || '#8d8f9a';
    return `
    <div style="border:1px solid var(--mock,#333); border-radius:12px; padding:12px 14px; margin-top:10px">
      <div style="display:flex; align-items:flex-start; gap:10px">
        <span style="flex:none; background:${color}; color:#0c0d12; border-radius:99px; padding:1px 10px; font-size:11px; font-weight:700; margin-top:2px">${esc(idea.kind || 'ネタ')}</span>
        <div style="flex:1; min-width:0">
          <div>${esc(idea.theme)}</div>
          ${idea.hook ? `<div style="color:var(--muted,#888); font-size:12px; margin-top:4px">想定1行目: ${esc(idea.hook)}</div>` : ''}
        </div>
      </div>
      <div style="margin-top:8px; display:flex; gap:8px">
        <button type="button" class="primary" data-idea-use="${i}" data-idea-brand="${esc(brand)}">ペインに流す</button>
        <button type="button" data-idea-copy="${i}">コピー</button>
      </div>
    </div>`;
  }).join('');
}

ideasResults.addEventListener('click', async (e) => {
  const useBtn = e.target.closest('[data-idea-use]');
  if (useBtn && ideasData) {
    const idea = ideasData.ideas[parseInt(useBtn.dataset.ideaUse, 10)];
    if (!idea) return;
    ideasModal.hidden = true;
    addPane();
    const all = workspace.querySelectorAll('.pane');
    const pane = all[all.length - 1];
    pane.querySelector('[data-field="brand"]').value = useBtn.dataset.ideaBrand;
    const themeTa = pane.querySelector('[data-field="theme"]');
    themeTa.value = idea.theme;
    themeTa.focus();
    const status = pane.querySelector('[data-field="status"]');
    status.textContent = 'ネタ出しから流し込み: 準備完了';
    status.className = 'status';
    return;
  }
  const copyBtn = e.target.closest('[data-idea-copy]');
  if (copyBtn && ideasData) {
    const idea = ideasData.ideas[parseInt(copyBtn.dataset.ideaCopy, 10)];
    if (!idea) return;
    try {
      await navigator.clipboard.writeText(idea.theme);
      copyBtn.textContent = 'コピーしました';
      setTimeout(() => { copyBtn.textContent = 'コピー'; }, 2000);
    } catch { /* クリップボード不可の環境では無視 */ }
  }
});

document.getElementById('open-ideas').addEventListener('click', () => {
  ideasModal.hidden = false;
});
document.getElementById('ideas-run').addEventListener('click', runIdeas);
