// UI 로직 — 상태는 Python(Api)이 갖고, 여기서는 0.5초마다 폴링해 그린다.
const $ = (s) => document.querySelector(s);
const api = () => window.pywebview.api;

const VIDEO_HEIGHTS = [2160, 1440, 1080, 720, 480, 360];
// 재생목록·스마트 모드는 영상마다 자막 언어가 달라 흔한 언어만 고르게 한다 (없으면 자동 번역 자막)
const COMMON_SUBS = [['ko', '한국어'], ['en', '영어'], ['ja', '일본어'], ['zh-Hans', '중국어(간체)'], ['es', '스페인어']];
const HEIGHT_TAG = { 2160: '4K', 1440: 'QHD', 1080: 'Full HD', 720: 'HD' };

let settings = {};
let lastJobsKey = '';
let current = null;                      // 다운로드 창에 띄운 조회 결과
let saveDir = '';                         // 다운로드 창의 저장 위치
// 마지막 선택을 기억 (4K 다운로더처럼 다음 다운로드에 그대로)
const prefs = { kind: 'video', ext: 'mp3', quality: 'v1080', subOn: false, subLang: 'ko' };

// ─── 헬퍼 ───
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const setHtml = (el, html) => {   // 같은 내용이면 DOM을 건드리지 않는다 (교체되면 진행 중인 클릭이 사라짐)
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
};
const fmtDuration = (s) => {
  if (!s) return '';
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
};
const fmtBytes = (b) => {
  if (!b) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i >= 2 ? 1 : 0)} ${u[i]}`;
};
const fmtEta = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`
  : s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`);
const fmtLabel = (f) => (f === 'mp3' || f === 'm4a') ? f.toUpperCase() : `MP4 · ${f.slice(1)}p`;
const firstUrl = (text) => ((text || '').match(/https?:\/\/\S+/i) || [''])[0];
const errMsg = (e) => (e && e.message ? e.message : String(e));

function toast(msg) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, 2600);
}

// ─── 대화상자 공통 ───
const DIALOGS = ['#dlg', '#url-dlg', '#err-dlg', '#smart-dlg', '#settings-dlg'];
const anyDialogOpen = () => DIALOGS.some((d) => !$(d).hidden);
const closeDialog = (el) => { el.hidden = true; };
document.querySelectorAll('.overlay').forEach((ov) => {
  ov.addEventListener('mousedown', (e) => { if (e.target === ov) closeDialog(ov); });
  ov.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) closeDialog(ov); });
});
function showError(msg) {
  $('#err-text').textContent = msg;
  $('#err-dlg').hidden = false;
}

// ─── 다운로드 목록 ───
function statusText(j) {
  switch (j.status) {
    case 'queued': return '대기 중';
    case 'downloading': {
      const parts = [`${j.percent}%`];
      if (j.speed) parts.push(`${fmtBytes(j.speed)}/s`);
      if (j.eta) parts.push(`${fmtEta(j.eta)} 남음`);
      return parts.join(' · ');
    }
    case 'processing': return '변환 중…';
    case 'paused': return `일시정지 · ${j.percent}%`;
    case 'error': return '오류';
    case 'canceled': return '취소됨';
    default: return '';
  }
}

const ICON = {
  pause: '<path d="M7 5v10M13 5v10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
  play: '<path d="M7 4.5v11l8.5-5.5z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
  retry: '<path d="M15.5 10a5.5 5.5 0 1 1-1.6-3.9M15.5 4v3h-3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  folder: '<path d="M3 6a1.5 1.5 0 0 1 1.5-1.5H8l1.5 2h6A1.5 1.5 0 0 1 17 8v6.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>',
  remove: '<path d="M6 6l8 8M14 6l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
};
const iconBtn = (act, title, icon) =>
  `<button class="icon-btn" data-act="${act}" title="${title}"><svg viewBox="0 0 20 20" width="18" height="18">${ICON[icon]}</svg></button>`;

function actionButtons(j) {
  const out = [];
  if (j.status === 'downloading' || j.status === 'processing') out.push(iconBtn('pause', '일시정지', 'pause'));
  if (j.status === 'paused') out.push(iconBtn('resume', '이어받기', 'play'));
  if (j.status === 'error' || j.status === 'canceled') out.push(iconBtn('resume', '다시 시도', 'retry'));
  if (j.status === 'done') out.push(iconBtn('reveal', '폴더에서 보기', 'folder'));
  out.push(iconBtn('remove', '목록에서 제거', 'remove'));
  return out.join('');
}

function renderJobs(jobs) {
  updateEmpty(jobs.length);
  const key = JSON.stringify(jobs);
  if (key === lastJobsKey) return;
  lastJobsKey = key;

  const list = $('#list');
  const existing = new Map([...list.children].map((el) => [el.dataset.id, el]));
  const els = new Map();                 // 이번에 그릴 줄 (기존 + 새로 만든 것)
  const ordered = [...jobs].reverse();   // 최근 항목이 위
  for (const j of ordered) {
    let el = existing.get(j.id);
    if (!el) {
      el = document.createElement('div');
      el.dataset.id = j.id;
      el.innerHTML = `
        <div class="thumb-wrap"><img class="thumb" alt=""><span class="dur" hidden></span></div>
        <div class="r-main">
          <div class="r-title"></div>
          <div class="r-meta"></div>
          <div class="r-progress"><div class="bar"><i></i></div><span class="r-status"></span></div>
          <div class="r-err" hidden></div>
        </div>
        <div class="r-actions"></div>`;
      if (j.thumbnail) el.querySelector('.thumb').src = j.thumbnail;
      const dur = el.querySelector('.dur');
      dur.textContent = fmtDuration(j.duration);
      dur.hidden = !j.duration;
    }
    el.className = `row ${j.status}`;
    el.querySelector('.r-title').textContent = j.title || j.url;
    const meta = [fmtLabel(j.fmt)];
    if (j.size) meta.push(fmtBytes(j.size));
    if (j.sub_lang) meta.push(`자막 ${j.sub_lang}`);
    setHtml(el.querySelector('.r-meta'), meta.map((m) => `<span>${esc(m)}</span>`).join('')
      + (j.status === 'done' ? '<span class="ok">완료</span>' : ''));
    const prog = el.querySelector('.r-progress');
    prog.hidden = j.status === 'done';
    prog.querySelector('i').style.width = `${j.percent}%`;
    prog.querySelector('.r-status').textContent = statusText(j);
    const err = el.querySelector('.r-err');
    err.hidden = !j.error;
    err.textContent = j.error || '';
    setHtml(el.querySelector('.r-actions'), actionButtons(j));
    els.set(j.id, el);
  }
  for (const [id, el] of existing) if (!els.has(id)) el.remove();
  // 순서가 바뀌었거나 새 줄이 있을 때만 다시 배치 (매번 옮기면 누르던 버튼이 떨어져 나간다)
  const want = ordered.map((j) => j.id).join();
  if ([...list.children].map((el) => el.dataset.id).join() !== want) {
    ordered.forEach((j) => list.appendChild(els.get(j.id)));
  }
}

function updateEmpty(jobCount = $('#list').children.length) {
  $('#empty').hidden = jobCount > 0 || $('#pending').children.length > 0;
}

$('#list').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-act]');
  if (btn) api()[btn.dataset.act](btn.closest('.row').dataset.id);
});
$('#list').addEventListener('dblclick', (e) => {
  const row = e.target.closest('.row.done');
  if (row) api().reveal(row.dataset.id);
});

// ─── 설치 배너 ───
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
    setHtml(banner, `<span>필수 구성요소 설치 실패: ${esc(p.error || 'yt-dlp 없음')}</span><button class="btn" id="btn-retry">다시 시도</button>`);
  } else {
    banner.hidden = true;
  }
}
$('#banner').addEventListener('click', (e) => { if (e.target.id === 'btn-retry') api().retry_bootstrap(); });

// ─── 링크 붙여넣기 → 분석 → 다운로드 창 ───
async function onPasteButton() {
  let text = '';
  try { text = await api().read_clipboard(); } catch { /* 클립보드 실패 시 입력창 */ }
  const url = firstUrl(text);
  if (url) startParse(url); else openUrlDialog('');
}

function openUrlDialog(prefill) {
  $('#url-input').value = prefill;
  $('#url-dlg').hidden = false;
  $('#url-input').focus();
}
function submitUrlDialog() {
  const url = firstUrl($('#url-input').value);
  if (!url) { toast('http(s)로 시작하는 주소를 입력하세요'); return; }
  closeDialog($('#url-dlg'));
  startParse(url);
}

let pendingSeq = 0;
async function startParse(url) {
  // 4K 다운로더처럼 목록 맨 위에 '분석 중' 줄을 띄운다
  const row = document.createElement('div');
  row.className = 'row pending';
  row.id = `pending-${++pendingSeq}`;
  row.innerHTML = `<div class="thumb-wrap"><div class="thumb"></div></div>
    <div class="r-main"><div class="r-title"></div><div class="r-progress"><span class="r-status">링크 분석 중…</span></div></div>`;
  row.querySelector('.r-title').textContent = url;
  $('#pending').prepend(row);
  updateEmpty();
  try {
    if (settings.smart && settings.smart.enabled) {
      const r = await api().smart_add(url);
      if (r.error) showError(r.error);
      else toast(`스마트 모드: ${r.added}개를 추가했어요`);
      return;
    }
    const info = await api().fetch_info(url);
    if (info.error) showError(info.error);
    else openDownloadDialog(info);
  } catch (e) {
    showError(`내부 오류: ${errMsg(e)}`);
  } finally {
    row.remove();
    updateEmpty();
  }
}

// ─── 다운로드 창 ───
function openDownloadDialog(info) {
  current = info;
  saveDir = settings.out_dir || '';
  $('#save-dir').value = saveDir;
  $('#save-dir').title = saveDir;
  if (info.type === 'playlist') renderPlaylistBody(info); else renderVideoBody(info);
  $('#dlg').hidden = false;
  $('#dlg-ok').focus();
}

function kindExtHtml() {
  return `
    <div class="opt-row">
      <select id="sel-kind">
        <option value="video" ${prefs.kind === 'video' ? 'selected' : ''}>영상 다운로드</option>
        <option value="audio" ${prefs.kind === 'audio' ? 'selected' : ''}>오디오 추출</option>
      </select>
      <select id="sel-ext"></select>
      <span id="slot-quality"></span>
    </div>`;
}
function fillExt() {
  const audio = $('#sel-kind').value === 'audio';
  $('#sel-ext').innerHTML = audio
    ? `<option value="mp3" ${prefs.ext === 'mp3' ? 'selected' : ''}>MP3</option><option value="m4a" ${prefs.ext === 'm4a' ? 'selected' : ''}>M4A</option>`
    : '<option value="mp4">MP4</option>';
}
function subRowHtml(options) {
  if (!options.length) return '<div class="sub-row"><span class="none">이 영상은 받을 수 있는 자막이 없어요</span></div>';
  const pick = options.some((o) => o.code === prefs.subLang) ? prefs.subLang : options[0].code;
  return `<div class="sub-row">
    <label class="check"><input type="checkbox" id="chk-sub" ${prefs.subOn ? 'checked' : ''}> 자막 다운로드 (.srt)</label>
    <select id="sel-sub" ${prefs.subOn ? '' : 'disabled'}>${options.map((o) =>
      `<option value="${esc(o.code)}" ${o.code === pick ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select>
  </div>`;
}
function bindCommon(onKindChange) {
  $('#sel-kind').addEventListener('change', () => { fillExt(); onKindChange(); });
  const chk = $('#chk-sub');
  if (chk) chk.addEventListener('change', () => { $('#sel-sub').disabled = !chk.checked; });
  fillExt();
}

function renderVideoBody(v) {
  $('#dlg-title').textContent = '영상 다운로드';
  const sub = [v.uploader, fmtDuration(v.duration)].filter(Boolean).join(' · ');
  $('#dlg-body').innerHTML = `
    <div class="clip">
      <img class="clip-thumb" src="${esc(v.thumbnail || '')}" alt="">
      <div class="clip-info">
        <div class="clip-title"></div>
        <div class="clip-sub">${esc(sub)}</div>
        ${v.playlist_url ? '<button class="link-btn" id="btn-whole-pl">이 영상이 든 재생목록 전체 보기</button>' : ''}
      </div>
    </div>
    ${kindExtHtml()}
    <div class="qlist" id="qlist"></div>
    ${subRowHtml(v.subtitles || [])}`;
  $('#dlg-body .clip-title').textContent = v.title || '';
  if (v.playlist_url) {
    $('#btn-whole-pl').onclick = () => { closeDialog($('#dlg')); startParse(v.playlist_url); };
  }
  bindCommon(renderQualities);
  renderQualities();
}

function renderQualities() {
  const audio = $('#sel-kind').value === 'audio';
  const v = current;
  let rows;
  if (audio) {
    rows = [{ value: 'audio', name: '최고 음질', tag: '', size: v.audio_size }];
  } else {
    rows = (v.qualities || []).filter((q) => q.height >= 144).map((q) => ({
      value: `v${q.height}`, name: `${q.height}p`, tag: HEIGHT_TAG[q.height] || '', size: q.size,
    }));
    if (!rows.length) rows = [{ value: 'v1080', name: '최고 화질', tag: '', size: null }];
  }
  const pick = rows.some((r) => r.value === prefs.quality) ? prefs.quality : rows[0].value;
  $('#qlist').innerHTML = rows.map((r) => `
    <label class="q">
      <input type="radio" name="q" value="${r.value}" ${r.value === pick ? 'checked' : ''}>
      <span class="q-name">${esc(r.name)}</span>
      ${r.tag ? `<span class="q-tag">${r.tag}</span>` : ''}
      <span class="q-size">${r.size ? `≈ ${fmtBytes(r.size)}` : ''}</span>
    </label>`).join('');
}

function renderPlaylistBody(pl) {
  $('#dlg-title').textContent = `재생목록 다운로드 · ${pl.entries.length}개`;
  const skipped = pl.skipped ? ` (받을 수 없는 ${pl.skipped}개 제외)` : '';
  $('#dlg-body').innerHTML = `
    <div class="clip-title" style="margin-bottom:10px"></div>
    <div class="pl-top">
      <label class="check"><input type="checkbox" id="pl-all" checked> 전체 선택</label>
      <span class="pl-count" id="pl-count"></span>
    </div>
    <div class="pl-list">${pl.entries.map((e, i) => `
      <label class="pl-item">
        <input type="checkbox" data-i="${i}" checked>
        <span class="pl-no">${i + 1}</span>
        <img src="${esc(e.thumbnail || '')}" alt="" loading="lazy">
        <span class="t">${esc(e.title)}</span>
        <span class="d">${fmtDuration(e.duration)}</span>
      </label>`).join('')}
    </div>
    ${kindExtHtml()}
    ${subRowHtml(COMMON_SUBS.map(([code, name]) => ({ code, name })))}`;
  $('#dlg-body .clip-title').textContent = (pl.title || '재생목록') + skipped;
  const count = () => {
    const n = document.querySelectorAll('.pl-list input:checked').length;
    $('#pl-count').textContent = `${pl.entries.length}개 중 ${n}개 선택`;
  };
  $('#pl-all').addEventListener('change', (e) => {
    document.querySelectorAll('.pl-list input').forEach((c) => { c.checked = e.target.checked; });
    count();
  });
  $('.pl-list').addEventListener('change', count);
  count();
  bindCommon(renderPlaylistQuality);
  renderPlaylistQuality();
}

function renderPlaylistQuality() {
  // 재생목록은 영상마다 화질이 달라 '이 화질 이하로 최고'를 고른다
  const audio = $('#sel-kind').value === 'audio';
  $('#slot-quality').innerHTML = audio ? '' : `<select id="sel-quality">${VIDEO_HEIGHTS.map((h) =>
    `<option value="v${h}" ${prefs.quality === `v${h}` ? 'selected' : ''}>${h}p${HEIGHT_TAG[h] ? ` (${HEIGHT_TAG[h]})` : ''}</option>`).join('')}</select>`;
}

function chosenFormat() {
  const audio = $('#sel-kind').value === 'audio';
  if (audio) return $('#sel-ext').value;
  if (current.type === 'playlist') return $('#sel-quality').value;
  const r = document.querySelector('#qlist input:checked');
  return r ? r.value : 'v1080';
}

async function submitDownload() {
  if (!current) return;
  const fmt = chosenFormat();
  const chk = $('#chk-sub');
  const subLang = chk && chk.checked ? $('#sel-sub').value : null;
  let items;
  if (current.type === 'playlist') {
    items = [...document.querySelectorAll('.pl-list input:checked')].map((c) => current.entries[+c.dataset.i]);
    if (!items.length) { toast('받을 항목을 선택하세요'); return; }
  } else {
    items = [{ url: current.url, id: current.id, title: current.title, thumbnail: current.thumbnail, duration: current.duration }];
  }
  // 다음 다운로드 창의 기본값으로 기억
  prefs.kind = $('#sel-kind').value;
  if (prefs.kind === 'audio') prefs.ext = fmt; else prefs.quality = fmt;
  prefs.subOn = !!subLang;
  if (subLang) prefs.subLang = subLang;
  try {
    const n = await api().add_download(items, fmt, saveDir, subLang);
    settings.out_dir = saveDir;
    closeDialog($('#dlg'));
    toast(`${n}개를 다운로드 목록에 추가했어요`);
  } catch (e) {
    toast(`추가 실패: ${errMsg(e)}`);
  }
}

$('#btn-browse').addEventListener('click', async () => {
  const dir = await api().choose_folder(saveDir);
  if (dir) { saveDir = dir; $('#save-dir').value = dir; $('#save-dir').title = dir; }
});
$('#dlg-ok').addEventListener('click', submitDownload);
$('#url-ok').addEventListener('click', submitUrlDialog);
$('#url-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitUrlDialog(); });
$('#btn-paste').addEventListener('click', onPasteButton);

// ─── 스마트 모드 ───
function renderSmartButton() {
  $('#btn-smart').classList.toggle('on', !!(settings.smart && settings.smart.enabled));
}
let smartDir = '';
function openSmartDialog() {
  const s = settings.smart || {};
  $('#smart-on').checked = !!s.enabled;
  const isAudio = s.fmt === 'mp3' || s.fmt === 'm4a';
  $('#smart-kind').value = isAudio ? s.fmt : 'video';
  $('#smart-quality').innerHTML = VIDEO_HEIGHTS.map((h) =>
    `<option value="v${h}" ${s.fmt === `v${h}` ? 'selected' : ''}>${h}p${HEIGHT_TAG[h] ? ` (${HEIGHT_TAG[h]})` : ''}</option>`).join('');
  $('#smart-quality').disabled = isAudio;
  $('#smart-sub').innerHTML = `<option value="">받지 않음</option>${COMMON_SUBS.map(([c, n]) =>
    `<option value="${c}" ${s.sub_lang === c ? 'selected' : ''}>${n}</option>`).join('')}`;
  smartDir = s.out_dir || settings.out_dir || '';
  $('#smart-dir').value = smartDir;
  $('#smart-dir').title = smartDir;
  $('#smart-dlg').hidden = false;
}
$('#smart-kind').addEventListener('change', () => { $('#smart-quality').disabled = $('#smart-kind').value !== 'video'; });
$('#smart-browse').addEventListener('click', async () => {
  const dir = await api().choose_folder(smartDir);
  if (dir) { smartDir = dir; $('#smart-dir').value = dir; $('#smart-dir').title = dir; }
});
$('#smart-ok').addEventListener('click', async () => {
  const kind = $('#smart-kind').value;
  const cfg = {
    enabled: $('#smart-on').checked,
    fmt: kind === 'video' ? $('#smart-quality').value : kind,
    sub_lang: $('#smart-sub').value || null,
    out_dir: smartDir,
  };
  try {
    settings.smart = await api().set_smart(cfg);
    renderSmartButton();
    closeDialog($('#smart-dlg'));
    toast(cfg.enabled ? '스마트 모드를 켰어요' : '스마트 모드를 껐어요');
  } catch (e) {
    toast(`저장 실패: ${errMsg(e)}`);
  }
});
$('#btn-smart').addEventListener('click', openSmartDialog);

// ─── 설정 ───
function openSettings() {
  $('#out-dir').textContent = settings.out_dir || '';
  $('#out-dir').title = settings.out_dir || '';
  $('#sel-concurrent').value = String(settings.max_concurrent || 2);
  $('#settings-dlg').hidden = false;
  refreshVersions();
}
const refreshVersions = () => api().app_info()
  .then((v) => { $('#versions').textContent = `앱 v${v.app} · yt-dlp ${v.ytdlp}`; })
  .catch(() => {});
$('#btn-settings').addEventListener('click', openSettings);
$('#btn-choose-folder').addEventListener('click', async () => {
  const dir = await api().choose_folder(settings.out_dir);
  if (!dir) return;
  settings.out_dir = await api().set_out_dir(dir);
  $('#out-dir').textContent = settings.out_dir;
  $('#out-dir').title = settings.out_dir;
});
$('#sel-concurrent').addEventListener('change', (e) => api().set_max_concurrent(+e.target.value));
$('#btn-update').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.textContent = '확인 중…';
  let msg;
  try { msg = await api().update_ytdlp(); } catch (err) { msg = `업데이트 실패: ${errMsg(err)}`; }
  btn.disabled = false;
  btn.textContent = '업데이트 확인';
  toast(msg);
  refreshVersions();
});
$('#btn-open-logs').addEventListener('click', () => api().open_logs());
$('#btn-open-folder').addEventListener('click', () => api().open_folder());
$('#btn-clear').addEventListener('click', () => api().clear_finished());

// ─── 키보드 ───
// 대화상자가 닫혀 있을 때 Ctrl+V → 붙여넣은 링크로 바로 분석
document.addEventListener('paste', (e) => {
  if (anyDialogOpen()) return;
  const text = (e.clipboardData.getData('text') || '').trim();
  if (!text) return;
  e.preventDefault();
  const url = firstUrl(text);
  if (url) startParse(url); else openUrlDialog(text);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const top = [...DIALOGS].reverse().map((d) => $(d)).find((d) => !d.hidden);
    if (top) closeDialog(top);
  }
  // 버튼·목록 상자에서의 Enter는 그 요소의 기본 동작에 맡긴다 (다운로드 버튼이면 클릭으로 한 번만 제출)
  if (e.key === 'Enter' && !$('#dlg').hidden && !['SELECT', 'BUTTON'].includes(e.target.tagName)) {
    e.preventDefault();
    submitDownload();
  }
  if (e.ctrlKey && e.key === ',') openSettings();
});

// ─── 폴링 루프 ───
async function tick() {
  try {
    const s = await api().get_state();
    settings = s.settings;
    renderJobs(s.jobs);
    renderBanner(s.binaries);
    renderSmartButton();
  } finally {
    setTimeout(tick, 500);
  }
}
window.addEventListener('pywebviewready', tick);
