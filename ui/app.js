// UI 로직 — 상태는 Python(Api)이 갖고, 여기서는 0.5초마다 폴링해 그린다.
const $ = (s) => document.querySelector(s);
const api = () => window.pywebview.api;

const STATUS_LABEL = {
  queued: '대기 중', downloading: '다운로드 중', processing: '변환 중',
  paused: '일시정지', done: '완료', error: '오류', canceled: '취소됨',
};

let filter = 'all';
let lastJobsKey = '';
let current = null;      // 모달에서 조회한 정보
let selectedFmt = null;

// ─── 포맷 헬퍼 ───
const fmtDuration = (s) => {
  if (!s) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
};
const fmtSpeed = (b) => {
  if (!b) return '';
  const u = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(1)} ${u[i]}`;
};
const fmtLabel = (f) => (f === 'mp3' || f === 'm4a') ? f.toUpperCase() : `${f.slice(1)}p`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// 같은 내용이면 DOM을 건드리지 않는다 (요소가 교체되면 진행 중인 클릭이 사라짐)
const setHtml = (el, html) => {
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
};

// ─── 토스트 ───
function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 260); }, 2500);
}

// ─── 목록 렌더링 ───
function renderJobs(jobs) {
  const visible = jobs.filter((j) =>
    filter === 'all' ||
    (filter === 'done' && j.status === 'done') ||
    (filter === 'active' && j.status !== 'done' && j.status !== 'canceled'));

  $('#empty').hidden = jobs.length > 0;
  $('#filters').hidden = jobs.length === 0;

  // 변화가 없으면 DOM을 건드리지 않는다 (hover 깜빡임 방지)
  const key = filter + JSON.stringify(visible);
  if (key === lastJobsKey) return;
  lastJobsKey = key;

  const list = $('#list');
  const existing = new Map([...list.children].map((el) => [el.dataset.id, el]));
  const keep = new Set();

  for (const j of visible) {
    keep.add(j.id);
    let el = existing.get(j.id);
    if (!el) {
      el = document.createElement('div');
      el.dataset.id = j.id;
      el.innerHTML = `
        <img class="thumb" alt="">
        <div class="card-main">
          <div class="card-title"></div>
          <div class="card-sub"></div>
          <div class="progress"><i></i></div>
          <div class="card-err" hidden></div>
        </div>
        <div class="card-actions"></div>`;
      if (j.thumbnail) el.querySelector('.thumb').src = j.thumbnail;
      list.appendChild(el);
    }
    el.className = `card ${j.status}`;
    el.querySelector('.card-title').textContent = j.title || j.url;
    const parts = [fmtLabel(j.fmt), STATUS_LABEL[j.status]];
    if (j.status === 'downloading') {
      parts.push(`${j.percent}%`);
      if (j.speed) parts.push(fmtSpeed(j.speed));
      if (j.eta) parts.push(`${fmtDuration(j.eta)} 남음`);
    } else if (j.duration) parts.push(fmtDuration(j.duration));
    el.querySelector('.card-sub').innerHTML = parts.map((p) => `<span>${esc(p)}</span>`).join('·');
    el.querySelector('.progress i').style.width = `${j.status === 'done' ? 100 : j.percent}%`;
    const err = el.querySelector('.card-err');
    err.hidden = !j.error;
    err.textContent = j.error || '';
    // 버튼은 상태가 바뀔 때만 교체 — 진행률마다 바꾸면 누르는 도중 버튼이 사라져 클릭이 씹힌다
    setHtml(el.querySelector('.card-actions'), actionButtons(j));
  }
  for (const [id, el] of existing) if (!keep.has(id)) el.remove();
}

function actionButtons(j) {
  const b = (act, title, icon) =>
    `<button class="icon-btn" data-act="${act}" title="${title}"><svg viewBox="0 0 16 16" width="16" height="16">${icon}</svg></button>`;
  const PAUSE = '<path d="M5.5 3.5v9M10.5 3.5v9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>';
  const PLAY = '<path d="M5 3.5v9l7-4.5z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>';
  const RETRY = '<path d="M13 8a5 5 0 1 1-1.5-3.5M13 2.5v3h-3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>';
  const X = '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>';
  const out = [];
  if (j.status === 'downloading' || j.status === 'processing') out.push(b('pause', '일시정지', PAUSE));
  if (j.status === 'paused') out.push(b('resume', '이어받기', PLAY));
  if (j.status === 'error') out.push(b('resume', '다시 시도', RETRY));
  out.push(b('remove', '목록에서 제거', X));
  return out.join('');
}

$('#list').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const id = btn.closest('.card').dataset.id;
  api()[btn.dataset.act](id);
});

document.querySelectorAll('.pill').forEach((p) => p.addEventListener('click', () => {
  document.querySelectorAll('.pill').forEach((x) => x.classList.toggle('active', x === p));
  filter = p.dataset.filter;
  lastJobsKey = '';
}));
$('#btn-clear').addEventListener('click', () => api().clear_finished());
// 배너는 계속 다시 그려지므로 버튼에 직접 달지 않고 위임
$('#banner').addEventListener('click', (e) => { if (e.target.id === 'btn-retry') api().retry_bootstrap(); });

// ─── 바이너리 설치 배너 ───
function renderBanner(bin) {
  const banner = $('#banner');
  const p = bin.progress;
  if (p.active) {
    banner.hidden = false;
    banner.className = 'banner';
    setHtml(banner, `<span>${esc(p.label)}</span><div class="bar"><i style="width:${p.percent}%"></i></div><span>${p.percent}%</span>`);
  } else if (p.error || !bin.ytdlp) {
    banner.hidden = false;
    banner.className = 'banner error';
    setHtml(banner, `<span>필수 구성요소 설치 실패: ${esc(p.error || 'yt-dlp 없음')}</span><button class="btn small" id="btn-retry">다시 시도</button>`);
  } else {
    banner.hidden = true;
  }
}

// ─── 링크 모달 ───
function openModal(url = '') {
  $('#modal').hidden = false;
  $('#url-input').value = url;
  $('#panel-body').innerHTML = '<div class="hint">영상 또는 재생목록 주소를 입력하세요 · Enter로 조회</div>';
  $('#panel-foot').hidden = true;
  current = null;
  $('#url-input').focus();
  if (/^https?:\/\//i.test(url.trim())) fetchInfo();
}
const closeModal = () => { $('#modal').hidden = true; };

const showError = (msg) => {
  $('#panel-body').innerHTML = `<div class="hint err">${esc(msg)}</div>`;
  $('#panel-foot').hidden = true;
};

async function fetchInfo(urlOverride) {
  const url = (typeof urlOverride === 'string' ? urlOverride : $('#url-input').value).trim();
  if (!/^https?:\/\//i.test(url)) { toast('http(s)로 시작하는 주소를 넣어 주세요'); return; }
  $('#url-input').value = url;
  $('#panel-body').innerHTML = '<div class="spinner"></div><div class="hint">정보 가져오는 중…</div>';
  $('#panel-foot').hidden = true;
  current = null;
  let info;
  try {
    info = await api().fetch_info(url);
  } catch (e) {
    info = { error: `내부 오류: ${e && e.message ? e.message : e}` };
  }
  if ($('#modal').hidden || $('#url-input').value.trim() !== url) return; // 그 사이 닫혔거나 바뀜
  if (info.error) { showError(info.error); return; }
  current = info;
  info.type === 'playlist' ? renderPlaylist(info) : renderVideo(info);
  renderFormats(info.type === 'video' ? info.heights : [2160, 1440, 1080, 720, 480, 360]);
}

function renderVideo(v) {
  $('#panel-body').innerHTML = `
    <div class="preview">
      <img src="${esc(v.thumbnail || '')}" alt="">
      <div class="meta">
        <div class="t">${esc(v.title)}</div>
        <div class="s">${esc(v.uploader || '')}${v.duration ? ' · ' + fmtDuration(v.duration) : ''}</div>
        ${v.playlist_url ? '<button class="btn small" id="btn-whole-pl">재생목록 전체 보기</button>' : ''}
      </div>
    </div>`;
  if (v.playlist_url) $('#btn-whole-pl').onclick = () => fetchInfo(v.playlist_url);
}

function renderPlaylist(pl) {
  const skipped = pl.skipped ? ` · 받을 수 없는 ${pl.skipped}개 제외` : '';
  $('#panel-body').innerHTML = `
    <div class="pl-head">
      <input type="checkbox" id="pl-all" checked>
      <label for="pl-all"><b>${esc(pl.title || '재생목록')}</b> · ${pl.entries.length}개${skipped}</label>
    </div>
    <div class="pl-list">${pl.entries.map((e, i) => `
      <label class="pl-item">
        <input type="checkbox" data-i="${i}" checked>
        <img src="${esc(e.thumbnail || '')}" alt="" loading="lazy">
        <span class="t">${esc(e.title)}</span>
        <span class="d">${fmtDuration(e.duration)}</span>
      </label>`).join('')}
    </div>`;
  $('#pl-all').addEventListener('change', (e) =>
    document.querySelectorAll('.pl-list input').forEach((c) => { c.checked = e.target.checked; }));
}

function renderFormats(heights) {
  const opts = [...heights.filter((h) => h >= 144).slice(0, 6).map((h) => `v${h}`), 'mp3', 'm4a'];
  if (!opts.includes(selectedFmt)) selectedFmt = opts[0];
  $('#fmt-pills').innerHTML = opts.map((f) =>
    `<button class="fmt${f === selectedFmt ? ' active' : ''}" data-f="${f}">${fmtLabel(f)}</button>`).join('');
  $('#panel-foot').hidden = false;
}

$('#fmt-pills').addEventListener('click', (e) => {
  const b = e.target.closest('.fmt');
  if (!b) return;
  selectedFmt = b.dataset.f;
  document.querySelectorAll('.fmt').forEach((x) => x.classList.toggle('active', x === b));
});

async function startDownload() {
  if (!current) return;
  let items;
  if (current.type === 'playlist') {
    items = [...document.querySelectorAll('.pl-list input:checked')]
      .map((c) => current.entries[+c.dataset.i]).filter((e) => e.url);
    if (!items.length) { toast('받을 항목을 선택하세요'); return; }
  } else {
    items = [{ url: current.url, title: current.title, thumbnail: current.thumbnail, duration: current.duration }];
  }
  try {
    const n = await api().add_download(items, selectedFmt);
    closeModal();
    toast(`✓ ${n}개를 대기열에 추가했어요`);
  } catch (e) {
    toast(`추가 실패: ${e && e.message ? e.message : e}`);
  }
}

// ─── 이벤트 연결 ───
async function pasteAndOpen() {
  let text = '';
  try { text = await navigator.clipboard.readText(); } catch { /* 권한 없으면 빈 입력창 */ }
  openModal(/^https?:\/\//i.test(text.trim()) ? text.trim() : '');
}
$('#btn-paste').addEventListener('click', pasteAndOpen);
$('#btn-paste-empty').addEventListener('click', pasteAndOpen);
$('#btn-fetch').addEventListener('click', () => fetchInfo());
$('#btn-download').addEventListener('click', startDownload);
$('#url-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') fetchInfo(); });

// 테두리 없는 창의 크기 조절 손잡이 (오른쪽 아래). 요청이 쌓이지 않게 한 번에 하나만 보낸다
(() => {
  let start = null, busy = false, pending = null;
  const send = () => {
    if (busy || !pending) return;
    const [w, h] = pending; pending = null; busy = true;
    Promise.resolve(api().win_resize(w, h)).finally(() => { busy = false; send(); });
  };
  const onMove = (e) => {
    pending = [start.w + e.screenX - start.x, start.h + e.screenY - start.y];
    send();
  };
  const onUp = () => {
    start = null;
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  };
  $('#resize-grip').addEventListener('mousedown', (e) => {
    e.preventDefault();
    start = { x: e.screenX, y: e.screenY, w: window.innerWidth, h: window.innerHeight };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });
})();
$('#modal').addEventListener('mousedown', (e) => { if (e.target.id === 'modal') closeModal(); });
$('#settings-modal').addEventListener('mousedown', (e) => {
  if (e.target.id === 'settings-modal') $('#settings-modal').hidden = true;
});

// 모달이 닫힌 상태에서 Ctrl+V → 클립보드 링크로 바로 열기
document.addEventListener('paste', (e) => {
  if (!$('#modal').hidden || !$('#settings-modal').hidden) return;
  const text = (e.clipboardData.getData('text') || '').trim();
  if (text) { e.preventDefault(); openModal(text); }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { closeModal(); $('#settings-modal').hidden = true; }
  if (e.key === 'Enter' && !$('#modal').hidden && current && document.activeElement.id !== 'url-input') startDownload();
  if (e.ctrlKey && e.key === ',') openSettings();
});

// 창 버튼
$('#btn-close').addEventListener('click', () => api().win_close());
$('#btn-min').addEventListener('click', () => api().win_minimize());
$('#btn-max').addEventListener('click', () => api().win_maximize());
$('#btn-open-folder').addEventListener('click', () => api().open_folder());

// 설정
let settings = {};
function openSettings() {
  $('#out-dir').textContent = settings.out_dir || '';
  $('#out-dir').title = settings.out_dir || '';
  $('#sel-concurrent').value = String(settings.max_concurrent || 2);
  $('#settings-modal').hidden = false;
  api().app_info().then((v) => { $('#versions').textContent = `앱 v${v.app} · yt-dlp ${v.ytdlp}`; });
}
$('#btn-open-logs').addEventListener('click', () => api().open_logs());
$('#btn-settings').addEventListener('click', openSettings);
$('#btn-choose-folder').addEventListener('click', async () => {
  const dir = await api().choose_folder();
  $('#out-dir').textContent = dir;
  $('#out-dir').title = dir;
});
$('#sel-concurrent').addEventListener('change', (e) => api().set_max_concurrent(+e.target.value));
$('#btn-update').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = '확인 중…';
  let msg;
  try { msg = await api().update_ytdlp(); } catch (err) { msg = `업데이트 실패: ${err}`; }
  btn.disabled = false;
  btn.textContent = '확인';
  toast(msg);
  api().app_info().then((v) => { $('#versions').textContent = `앱 v${v.app} · yt-dlp ${v.ytdlp}`; });
});

// ─── 폴링 루프 ───
async function tick() {
  try {
    const s = await api().get_state();
    settings = s.settings;
    renderJobs(s.jobs);
    renderBanner(s.binaries);
  } finally {
    setTimeout(tick, 500);
  }
}
window.addEventListener('pywebviewready', tick);
