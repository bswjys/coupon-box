(() => {
  'use strict';

  const VERSION = '1';
  const DB_NAME = 'coupon-box';
  const DB_VERSION = 1;
  const STORE = 'coupons';
  const LS_KEY = 'couponbox.v1';
  const MAX_PHOTOS = 3;
  const MAX_SIDE = 1600;
  const QUALITY = 0.82;
  const SOON_DAYS = 7;

  const CAT_LABEL = { cafe: '카페·간식', food: '외식·치킨', mart: '마트·편의점', etc: '기타' };

  const state = { items: [], tab: 'available', cat: 'all', q: '', mode: 'idb' };
  let db = null;
  let form = null;
  let currentId = null;
  let confirmResolver = null;
  let toastTimer = null;
  const els = {};

  /* ── 날짜 ───────────────────────── */
  const pad2 = (n) => String(n).padStart(2, '0');

  function todayISO() {
    const d = new Date();
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }

  function isoUTC(iso) {
    if (!iso) return NaN;
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    if (!y || !m || !d) return NaN;
    return Date.UTC(y, m - 1, d);
  }

  function dayDiff(expiry) {
    if (!expiry) return null;
    const t = isoUTC(expiry);
    if (Number.isNaN(t)) return null;
    return Math.round((t - isoUTC(todayISO())) / 86400000);
  }

  function koreanDate(iso) {
    if (!iso) return '';
    const [y, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return `${y}년 ${m}월 ${d}일`;
  }

  function shortDate(iso) {
    if (!iso) return '';
    const [, m, d] = String(iso).slice(0, 10).split('-').map(Number);
    return `${m}.${d}`;
  }

  function won(n) {
    if (n == null || Number.isNaN(Number(n))) return '0원';
    return `${String(Math.round(Number(n))).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}원`;
  }

  function comma(n) {
    if (n == null || n === '') return '';
    return String(Math.round(Number(n))).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  }

  function uid() {
    return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  /* ── D-day 배지 ───────────────────────── */
  function badgeOf(item) {
    if (item.used) return { key: 'used', label: '사용완료' };
    const d = dayDiff(item.expiry);
    if (d === null) return { key: 'none', label: '기한 없음' };
    if (d < 0) return { key: 'expired', label: '기한 지남' };
    if (d === 0) return { key: 'urgent', label: 'D-DAY' };
    if (d <= 3) return { key: 'urgent', label: `D-${d}` };
    if (d <= SOON_DAYS) return { key: 'soon', label: `D-${d}` };
    return { key: 'normal', label: `D-${d}` };
  }

  /* ── 정렬 ───────────────────────── */
  // 사용 가능: 기한 임박 → 먼 기한 → 기한 없음 → 기한 지남
  function sortAvailable(list) {
    return list.slice().sort((a, b) => {
      const da = dayDiff(a.expiry);
      const db2 = dayDiff(b.expiry);
      const ga = da === null ? 1 : da < 0 ? 2 : 0;
      const gb = db2 === null ? 1 : db2 < 0 ? 2 : 0;
      if (ga !== gb) return ga - gb;
      if (ga === 0 && da !== db2) return da - db2;
      if (ga === 2 && da !== db2) return db2 - da;
      return (a.createdAt || 0) - (b.createdAt || 0);
    });
  }

  // 사용완료: 최근에 쓴 것부터
  function sortDone(list) {
    return list.slice().sort((a, b) => {
      const ua = a.usedAt || '';
      const ub = b.usedAt || '';
      if (ua !== ub) return ub.localeCompare(ua);
      return (b.createdAt || 0) - (a.createdAt || 0);
    });
  }

  function visibleItems() {
    const q = state.q.trim().toLowerCase();
    let list = state.items.filter((i) => (state.tab === 'done' ? i.used : !i.used));
    if (state.cat !== 'all') list = list.filter((i) => i.category === state.cat);
    if (q) {
      list = list.filter((i) => (
        `${i.brand} ${i.name} ${i.giver} ${i.memo} ${CAT_LABEL[i.category] || ''}`
      ).toLowerCase().includes(q));
    }
    return state.tab === 'done' ? sortDone(list) : sortAvailable(list);
  }

  /* ── 저장 (IndexedDB · localStorage 대체) ───────────────────────── */
  function openDB() {
    return new Promise((resolve, reject) => {
      let req;
      try {
        req = indexedDB.open(DB_NAME, DB_VERSION);
      } catch (err) {
        reject(err);
        return;
      }
      req.onupgradeneeded = () => {
        const d = req.result;
        if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, { keyPath: 'id' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('indexeddb'));
      req.onblocked = () => reject(new Error('indexeddb blocked'));
    });
  }

  function ask(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function store(mode) {
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  const idbAll = () => ask(store('readonly').getAll());
  const idbPut = (item) => ask(store('readwrite').put(item));
  const idbDelete = (id) => ask(store('readwrite').delete(id));
  const idbClear = () => ask(store('readwrite').clear());

  function lsAll() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      const data = raw ? JSON.parse(raw) : null;
      return data && Array.isArray(data.items) ? data.items : [];
    } catch (err) {
      return [];
    }
  }

  function lsWrite(items) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ app: 'coupon-box', version: VERSION, items }));
      return true;
    } catch (err) {
      return false;
    }
  }

  async function persistItem(item) {
    if (state.mode === 'idb') {
      try {
        await idbPut(item);
        return true;
      } catch (err) {
        return false;
      }
    }
    return lsWrite(state.items);
  }

  async function forgetItem(id) {
    if (state.mode === 'idb') {
      try {
        await idbDelete(id);
        return true;
      } catch (err) {
        return false;
      }
    }
    return lsWrite(state.items);
  }

  async function persistAll(items) {
    if (state.mode === 'idb') {
      try {
        await idbClear();
        for (const it of items) await idbPut(it);
        return true;
      } catch (err) {
        return false;
      }
    }
    return lsWrite(items);
  }

  function normalize(raw) {
    const it = Object.assign({}, raw || {});
    it.id = it.id || uid();
    it.name = String(it.name || '').trim();
    it.brand = String(it.brand || '').trim();
    it.category = CAT_LABEL[it.category] ? it.category : 'etc';
    it.expiry = /^\d{4}-\d{2}-\d{2}$/.test(String(it.expiry || '')) ? it.expiry : null;
    it.used = !!it.used;
    it.usedAt = it.used ? (it.usedAt || todayISO()) : null;
    it.giver = String(it.giver || '').trim();
    it.memo = String(it.memo || '');
    const amount = Number(it.amount);
    it.amount = Number.isFinite(amount) && amount > 0 ? Math.round(amount) : null;
    const balance = Number(it.balance);
    it.balance = it.amount !== null && Number.isFinite(balance)
      ? Math.max(0, Math.round(balance))
      : null;
    it.photos = Array.isArray(it.photos)
      ? it.photos.filter((s) => typeof s === 'string' && s.startsWith('data:image/')).slice(0, MAX_PHOTOS)
      : [];
    it.createdAt = Number(it.createdAt) || Date.now();
    it.updatedAt = Number(it.updatedAt) || it.createdAt;
    return it;
  }

  /* ── 렌더 ───────────────────────── */
  function cacheEls() {
    [
      'tabTabs', 'countAvailable', 'countDone', 'searchInput', 'searchClear', 'catChips',
      'soonNote', 'couponGrid', 'emptyState', 'emptyTitle', 'emptyDesc', 'listCount',
      'fabAdd', 'addWrap', 'addClose', 'addSave', 'addTitle', 'addBody', 'photoGrid',
      'photoInput', 'fName', 'fBrand', 'catSeg', 'fNoExpiry', 'fExpiry', 'expiryHint',
      'fIsAmount', 'amountBox', 'fAmount', 'fBalance', 'fGiver', 'fMemo',
      'detailWrap', 'detailClose', 'detailBody', 'detailTitle', 'detailUse', 'detailEdit', 'detailDelete',
      'zoomWrap', 'zoomImg', 'zoomClose',
      'cropWrap', 'cropStage', 'cropView', 'cropCanvas', 'cropFrame', 'cropCancel', 'cropApply',
      'cropKeep', 'cropRotate', 'cropAuto', 'cropRatios', 'cropSize',
      'moreWrap', 'moreClose', 'moreInfo', 'backupBtn', 'restoreBtn', 'restoreInput', 'moreBtn',
      'confirmWrap', 'confirmTitle', 'confirmDesc', 'confirmOk', 'confirmCancel',
      'toast',
    ].forEach((id) => { els[id] = document.getElementById(id); });
  }

  function subLine(item) {
    const bits = [];
    if (item.used && item.usedAt) bits.push(`${shortDate(item.usedAt)} 사용`);
    if (item.giver) bits.push(item.giver);
    if (item.amount !== null && item.balance !== null) {
      bits.push(item.balance === item.amount ? `금액권 ${won(item.amount)}` : `잔액 ${won(item.balance)}`);
    }
    if (!bits.length) bits.push(CAT_LABEL[item.category] || '기타');
    return {
      text: bits.join(' · '),
      balance: !item.used && item.amount !== null && item.balance !== null,
    };
  }

  function cardHTML(item) {
    const b = badgeOf(item);
    const photo = item.photos[0];
    const brand = item.brand || CAT_LABEL[item.category] || '쿠폰';
    const sub = subLine(item);
    return `
      <button type="button" class="coupon${item.used ? ' is-used' : ''}${b.key === 'expired' ? ' is-expired' : ''}" data-id="${esc(item.id)}" aria-label="${esc(item.name)} 상세 보기">
        <span class="thumb">
          ${photo
            ? `<img src="${photo}" alt="${esc(item.name)} 쿠폰 사진" loading="lazy">`
            : `<span class="thumb-empty" aria-hidden="true"><svg viewBox="0 0 64 64" width="46" height="46"><rect x="7" y="17" width="50" height="30" rx="7" fill="#FFFFFF" stroke="#C9D6E5" stroke-width="2.6"/><path d="M41 17v30" stroke="#D2DEEB" stroke-width="2.6" stroke-dasharray="4 4.5" stroke-linecap="round"/><rect x="15" y="26" width="4" height="12" rx="2" fill="#D7E2EF"/><rect x="22" y="26" width="3" height="12" rx="1.5" fill="#D7E2EF"/><rect x="28" y="26" width="5" height="12" rx="2" fill="#D7E2EF"/></svg></span>`}
          <span class="dday ${b.key}">${b.label}</span>
          ${item.used ? '<span class="used-stamp"><span>사용완료</span></span>' : ''}
        </span>
        <span class="coupon-meta">
          <span class="coupon-brand">${esc(brand)}</span>
          <span class="coupon-name">${esc(item.name)}</span>
          <span class="coupon-sub${sub.balance ? ' balance' : ''}">${esc(sub.text)}</span>
        </span>
      </button>`;
  }

  const addCardHTML = `
      <button type="button" class="add-card" id="addCard">
        <span class="plus" aria-hidden="true">＋</span>
        <strong>쿠폰 사진등록</strong>
        <small>찍거나 골라서 담기</small>
      </button>`;

  function renderTabs() {
    const avail = state.items.filter((i) => !i.used).length;
    const done = state.items.filter((i) => i.used).length;
    els.countAvailable.textContent = String(avail);
    els.countDone.textContent = String(done);
    els.tabTabs.querySelectorAll('button[data-tab]').forEach((btn) => {
      btn.setAttribute('aria-selected', String(btn.dataset.tab === state.tab));
    });
    if (els.fabAdd) els.fabAdd.hidden = state.tab !== 'done';
  }

  function renderChips() {
    els.catChips.querySelectorAll('button[data-cat]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.cat === state.cat));
    });
  }

  function renderList() {
    const list = visibleItems();
    const grid = [state.tab === 'available' ? addCardHTML : '']
      .concat(list.map(cardHTML))
      .join('');
    els.couponGrid.innerHTML = grid;
    els.couponGrid.classList.toggle('is-empty', state.tab === 'available' && list.length === 0);

    const ready = state.items.filter((i) => !i.used && dayDiff(i.expiry) !== null && dayDiff(i.expiry) >= 0 && dayDiff(i.expiry) <= SOON_DAYS).length;
    els.soonNote.hidden = !(state.tab === 'available' && state.cat === 'all' && !state.q.trim() && ready > 0);
    els.soonNote.innerHTML = `<span>⏰ 7일 안에 쓸 쿠폰이 <b>${ready}장</b> 있어요</span>`;

    els.listCount.textContent = state.tab === 'done'
      ? `사용완료 ${list.length}개`
      : `쿠폰 ${list.length}개`;

    const showEmpty = list.length === 0;
    els.emptyState.hidden = !showEmpty;
    if (showEmpty) {
      if (state.q.trim()) {
        els.emptyTitle.textContent = '찾는 쿠폰이 없어요';
        els.emptyDesc.innerHTML = '다른 이름이나 브랜드로 검색해 보세요.';
      } else if (state.cat !== 'all') {
        els.emptyTitle.textContent = '이 카테고리에 쿠폰이 없어요';
        els.emptyDesc.innerHTML = '전체로 바꾸면 담아 둔 쿠폰을 모두 볼 수 있어요.';
      } else if (state.tab === 'done') {
        els.emptyTitle.textContent = '사용완료한 쿠폰이 없어요';
        els.emptyDesc.innerHTML = '쓴 쿠폰은 여기로 모여서<br>사용 가능한 쿠폰과 섞이지 않아요.';
      } else {
        els.emptyTitle.textContent = '아직 담아 둔 쿠폰이 없어요';
        els.emptyDesc.innerHTML = '쿠폰 사진을 찍어 담으면<br>기한이 가까운 순서로 먼저 보여 드려요.';
      }
    }
  }

  function render() {
    renderTabs();
    renderChips();
    renderList();
  }

  /* ── 토스트 ───────────────────────── */
  function hideToast() {
    clearTimeout(toastTimer);
    els.toast.classList.remove('show');
    els.toast.hidden = true;
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      els.toast.classList.remove('show');
      setTimeout(() => { els.toast.hidden = true; }, 220);
    }, 2400);
  }

  /* ── 오버레이 ───────────────────────── */
  /* 모달·추가창 화면 고정 (2026-11-28)
     열 때 스크롤 위치를 저장하고 body를 fixed로 잠가 배경이 흔들리지 않게 한다.
     여러 창이 겹쳐 열려도 마지막 창이 닫힐 때만 원래 위치로 돌아간다. */
  let lockedScrollY = null;
  const MODAL_KEYS = ['addWrap', 'detailWrap', 'moreWrap', 'confirmWrap', 'zoomWrap', 'cropWrap'];

  function lockBody() {
    const anyOpen = MODAL_KEYS.some((k) => els[k] && !els[k].hidden);
    const isLocked = document.body.classList.contains('modal-open');

    if (anyOpen && !isLocked) {
      lockedScrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
      document.body.style.top = `-${lockedScrollY}px`;
      document.body.classList.add('modal-open', 'locked');
    } else if (!anyOpen && isLocked) {
      document.body.classList.remove('modal-open', 'locked');
      document.body.style.top = '';
      const backY = lockedScrollY || 0;
      lockedScrollY = null;
      window.scrollTo(0, backY);
    }
  }

  function askConfirm({ title, desc, ok = '확인', tone = 'primary' }) {
    els.confirmTitle.textContent = title;
    els.confirmDesc.innerHTML = desc || '';
    els.confirmOk.textContent = ok;
    els.confirmOk.className = `btn ${tone === 'danger' ? 'danger' : 'primary'}`;
    els.confirmWrap.hidden = false;
    lockBody();
    return new Promise((resolve) => { confirmResolver = resolve; });
  }

  function resolveConfirm(value) {
    els.confirmWrap.hidden = true;
    lockBody();
    const fn = confirmResolver;
    confirmResolver = null;
    if (fn) fn(value);
  }

  /* ── 사진 ───────────────────────── */
  function compressImage(file, maxSide = MAX_SIDE, quality = QUALITY) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        try {
          const w = img.naturalWidth || img.width;
          const h = img.naturalHeight || img.height;
          if (!w || !h) throw new Error('empty image');
          const scale = Math.min(1, maxSide / Math.max(w, h));
          const cw = Math.max(1, Math.round(w * scale));
          const ch = Math.max(1, Math.round(h * scale));
          const cv = document.createElement('canvas');
          cv.width = cw;
          cv.height = ch;
          const ctx = cv.getContext('2d');
          ctx.fillStyle = '#ffffff';
          ctx.fillRect(0, 0, cw, ch);
          ctx.imageSmoothingEnabled = true;
          ctx.imageSmoothingQuality = 'high';
          ctx.drawImage(img, 0, 0, cw, ch);
          URL.revokeObjectURL(url);
          resolve(cv.toDataURL('image/jpeg', quality));
        } catch (err) {
          URL.revokeObjectURL(url);
          reject(err);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error('이미지를 읽지 못했어요'));
      };
      img.src = url;
    });
  }

  function renderPhotoGrid() {
    if (!form) return;
    const cells = form.photos.map((src, idx) => `
      <div class="photo-cell">
        <button type="button" class="photo" data-photo="${idx}" aria-label="쿠폰 사진 ${idx + 1} 크게 보기">
          <img src="${src}" alt="쿠폰 사진 ${idx + 1}">
        </button>
        <button type="button" class="photo-del" data-remove="${idx}" aria-label="쿠폰 사진 ${idx + 1} 빼기">×</button>
      </div>`).join('');
    const add = form.photos.length < MAX_PHOTOS
      ? '<button type="button" class="photo-add" id="photoAdd"><span aria-hidden="true">＋</span><small>사진 추가</small></button>'
      : '';
    els.photoGrid.innerHTML = cells + add;
  }

  // 사진을 고르면 바로 담지 않고 자르기 창을 먼저 띄운다
  async function handlePhotoFiles(files) {
    if (!form) return;
    const all = Array.from(files || []);
    els.photoInput.value = '';
    if (!all.length) return;
    const room = MAX_PHOTOS - form.photos.length;
    if (room <= 0) {
      toast(`사진은 ${MAX_PHOTOS}장까지 넣을 수 있어요`);
      return;
    }
    const usable = all.filter((file) => String(file.type || '').startsWith('image/'));
    if (!usable.length) {
      toast('사진을 읽지 못했어요. 다른 사진으로 해 주세요.');
      return;
    }
    if (usable.length > room) toast(`사진은 ${MAX_PHOTOS}장까지라 ${room}장만 넣었어요`);
    const batch = usable.slice(0, room);
    if (!els.cropWrap.hidden) {
      cropQueue = cropQueue.concat(batch);
      return;
    }
    cropQueue = batch;
    openNextCrop();
  }

  function openZoom(src) {
    if (!src) return;
    els.zoomImg.src = src;
    els.zoomWrap.hidden = false;
    lockBody();
  }

  function closeZoom() {
    els.zoomWrap.hidden = true;
    els.zoomImg.removeAttribute('src');
    lockBody();
  }

  /* ── 사진 자르기(크롭) ───────────────────────── */
  /* 고른·찍은 사진을 바로 담지 않고, 자를 영역을 고른 뒤 담는다.
     · 자르기 적용 — 네 모서리 핸들·박스 안쪽 드래그로 고른 영역만 잘라 JPEG로 압축
     · 원본 그대로 — 자르지 않고 예전처럼 전체를 압축해 담는다
     · 비율 칩(자유·1:1·3:4·4:3)과 ↺ 90° 회전 지원, Pointer 이벤트로 아이폰 터치 대응 */
  const CROP_RATIOS = { free: null, '1:1': 1, '3:4': 3 / 4, '4:3': 4 / 3 };
  const CROP_MAX = 2600; // 작업 캔버스 최대 변(px)
  const CROP_MIN = 48;   // 자를 영역 최소 변(화면 px)
  const CROP_ANALYZE = 160; // 자동 맞춤 분석용 축소 캔버스 긴 변(px)
  const crop = { file: null, ratio: 'free', rect: null, scale: 1, drag: null, detected: null };
  let cropQueue = [];

  function loadImageFile(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('이미지를 읽지 못했어요')); };
      img.src = url;
    });
  }

  function cropViewSize() {
    const box = els.cropView.getBoundingClientRect();
    return { w: box.width || 1, h: box.height || 1 };
  }

  function maxCropRect(ratio, vw, vh) {
    if (!ratio) return { x: 0, y: 0, w: vw, h: vh };
    let w = vw;
    let h = w / ratio;
    if (h > vh) { h = vh; w = h * ratio; }
    return { x: (vw - w) / 2, y: (vh - h) / 2, w, h };
  }

  // ── ✨ 자동 맞춤 ──
  // 축소 캔버스에서 주변 배경과 구분되는 밝은 종이·카드(또는 대비 높은 피사체) 영역을 찾는다.
  // 못 찾거나 감지 영역이 너무 작으면 null을 돌려주고, 부르는 쪽에서 전체 영역으로 되돌린다.
  function detectCropRect() {
    const src = els.cropCanvas;
    if (!src.width || !src.height) return null;
    const k = Math.min(1, CROP_ANALYZE / Math.max(src.width, src.height));
    const w = Math.max(16, Math.round(src.width * k));
    const h = Math.max(16, Math.round(src.height * k));
    const cv = document.createElement('canvas');
    cv.width = w;
    cv.height = h;
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(src, 0, 0, w, h);
    let px = null;
    try {
      px = ctx.getImageData(0, 0, w, h).data;
    } catch (err) {
      return null;
    }

    const lumOf = (i) => 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];

    // 테두리 띠에서 배경색·배경 편차를 잰다
    const band = Math.max(1, Math.round(Math.min(w, h) * 0.06));
    const Rs = [];
    const Gs = [];
    const Bs = [];
    const Ls = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (x >= band && x < w - band && y >= band && y < h - band) continue;
        const i = (y * w + x) * 4;
        Rs.push(px[i]);
        Gs.push(px[i + 1]);
        Bs.push(px[i + 2]);
        Ls.push(lumOf(i));
      }
    }
    if (!Rs.length) return null;
    const med = (arr) => { arr.sort((a, b) => a - b); return arr[arr.length >> 1]; };
    const bgR = med(Rs);
    const bgG = med(Gs);
    const bgB = med(Bs);
    const bgL = med(Ls);
    let dev = 0;
    for (let i = 0; i < Ls.length; i++) dev += Math.abs(Ls[i] - bgL);
    dev /= Ls.length;

    const brightTh = Math.max(26, dev * 2.6); // 배경보다 밝은 종이
    const colorTh = Math.max(62, dev * 4);    // 배경과 확 다른 색(대비 높은 피사체)

    const rowCount = new Float32Array(h);
    const colCount = new Float32Array(w);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const dl = lumOf(i) - bgL;
        const dc = Math.max(
          Math.abs(px[i] - bgR),
          Math.abs(px[i + 1] - bgG),
          Math.abs(px[i + 2] - bgB)
        );
        if (dl > brightTh || dc > colorTh) {
          rowCount[y] += 1;
          colCount[x] += 1;
        }
      }
    }

    // 성긴 무늬(글자·바코드)가 끊기지 않게 살짝 뭉갠다
    const smooth = (arr, size) => {
      const out = new Float32Array(arr.length);
      const half = Math.max(0, Math.floor((size - 1) / 2));
      for (let i = 0; i < arr.length; i++) {
        let sum = 0;
        let n = 0;
        for (let j = i - half; j <= i + half; j++) {
          if (j < 0 || j >= arr.length) continue;
          sum += arr[j];
          n += 1;
        }
        out[i] = n ? sum / n : 0;
      }
      return out;
    };
    const win = Math.max(3, (Math.round(Math.min(w, h) * 0.05) | 1));
    const bestRun = (counts, th, center) => {
      const runs = [];
      let cur = null;
      for (let i = 0; i < counts.length; i++) {
        if (counts[i] >= th) {
          if (cur) cur.b = i;
          else { cur = { a: i, b: i }; }
        } else if (cur) {
          runs.push(cur);
          cur = null;
        }
      }
      if (cur) runs.push(cur);
      if (!runs.length) return null;
      const mine = runs.filter((r) => r.a <= center && center <= r.b);
      const pool = mine.length ? mine : runs;
      return pool.reduce((best, r) => (r.b - r.a > best.b - best.a ? r : best), pool[0]);
    };

    // 뭉갠 값으로 구간을 찾고, 실제 픽셀 수로 가장자리를 다시 조인다
    const trim = (counts, run, minCount) => {
      let a = run.a;
      let b = run.b;
      while (a < b && counts[a] < minCount) a += 1;
      while (b > a && counts[b] < minCount) b -= 1;
      return { a, b };
    };
    const rowsFound = bestRun(smooth(rowCount, win), w * 0.2, h / 2);
    const colsFound = bestRun(smooth(colCount, win), h * 0.2, w / 2);
    if (!rowsFound || !colsFound) return null;
    const rowsRun = trim(rowCount, rowsFound, w * 0.12);
    const colsRun = trim(colCount, colsFound, h * 0.12);

    const padX = Math.max(1, Math.round(w * 0.01));
    const padY = Math.max(1, Math.round(h * 0.01));
    const x0 = Math.max(0, colsRun.a - padX);
    const x1 = Math.min(w - 1, colsRun.b + padX);
    const y0 = Math.max(0, rowsRun.a - padY);
    const y1 = Math.min(h - 1, rowsRun.b + padY);
    const nw = (x1 - x0 + 1) / w;
    const nh = (y1 - y0 + 1) / h;
    if (nw < 0.25 || nh < 0.25) return null; // 너무 작으면 안전하게 포기
    return { x: x0 / w, y: y0 / h, w: nw, h: nh };
  }

  // 감지한 영역(없으면 전체) 안에 지금 고른 비율을 맞춰 넣는다
  function cropFitToDetected() {
    const { w: vw, h: vh } = cropViewSize();
    const ratio = CROP_RATIOS[crop.ratio] || null;
    const d = crop.detected;
    let rect;
    if (!d) {
      rect = maxCropRect(ratio, vw, vh);
    } else {
      const bx = d.x * vw;
      const by = d.y * vh;
      const bw = d.w * vw;
      const bh = d.h * vh;
      if (!ratio) {
        rect = { x: bx, y: by, w: bw, h: bh };
      } else {
        let rw = bw;
        let rh = rw / ratio;
        if (rh > bh) { rh = bh; rw = rh * ratio; }
        rect = { x: bx + (bw - rw) / 2, y: by + (bh - rh) / 2, w: rw, h: rh };
      }
    }
    const cw = Math.min(rect.w, vw);
    const ch = Math.min(rect.h, vh);
    crop.rect = {
      x: Math.min(Math.max(0, rect.x), Math.max(0, vw - cw)),
      y: Math.min(Math.max(0, rect.y), Math.max(0, vh - ch)),
      w: cw,
      h: ch,
    };
    paintCropFrame();
  }

  // ✨ 자동 맞춤 — 지금 사진(회전 반영)에서 영역을 다시 찾아 맞춘다
  function autoFitCrop() {
    crop.detected = detectCropRect();
    cropFitToDetected();
    return !!crop.detected;
  }

  // 비율만 바꿀 때: 지금 영역의 넓이·중심을 지키며 비율을 맞춘다(반복해도 줄지 않게)
  function cropFitRatio(ratio) {
    const { w: vw, h: vh } = cropViewSize();
    const r0 = crop.rect || maxCropRect(null, vw, vh);
    if (!ratio) {
      crop.rect = { x: r0.x, y: r0.y, w: r0.w, h: r0.h };
      paintCropFrame();
      return;
    }
    const cx = r0.x + r0.w / 2;
    const cy = r0.y + r0.h / 2;
    const area = Math.max(1, r0.w * r0.h);
    let w = Math.sqrt(area * ratio);
    let h = w / ratio;
    const maxW = Math.min(vw, vh * ratio);
    if (w > maxW) { w = maxW; h = w / ratio; }
    w = Math.max(w, Math.min(CROP_MIN, vw));
    h = Math.max(h, Math.min(CROP_MIN, vh));
    if (h > vh) { h = vh; w = h * ratio; }
    if (w > vw) { w = vw; h = w / ratio; }
    const x = Math.min(Math.max(0, cx - w / 2), Math.max(0, vw - w));
    const y = Math.min(Math.max(0, cy - h / 2), Math.max(0, vh - h));
    crop.rect = { x, y, w, h };
    paintCropFrame();
  }

  function paintCropFrame() {
    if (!crop.rect) return;
    const r = crop.rect;
    els.cropFrame.style.left = `${r.x}px`;
    els.cropFrame.style.top = `${r.y}px`;
    els.cropFrame.style.width = `${r.w}px`;
    els.cropFrame.style.height = `${r.h}px`;
    els.cropSize.textContent = `자를 영역 ${Math.round(r.w * crop.scale)} × ${Math.round(r.h * crop.scale)}`;
  }

  function paintCropRatio() {
    els.cropRatios.querySelectorAll('button[data-ratio]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.ratio === crop.ratio));
    });
  }

  // 작업 캔버스를 화면에 맞춰 줄이고, 자를 영역 상자를 다시 그린다
  function layoutCrop() {
    const cv = els.cropCanvas;
    const w = cv.width || 1;
    const h = cv.height || 1;
    const availW = Math.max(80, els.cropStage.clientWidth - 48);
    const availH = Math.max(80, els.cropStage.clientHeight - 48);
    const fit = Math.min(availW / w, availH / h, 1);
    const vw = Math.max(40, w * fit);
    const vh = Math.max(40, h * fit);
    els.cropView.style.width = `${Math.round(vw * 100) / 100}px`;
    els.cropView.style.height = `${Math.round(vh * 100) / 100}px`;
    cv.style.width = `${vw}px`;
    cv.style.height = `${vh}px`;
    crop.scale = w / vw;
    crop.rect = maxCropRect(CROP_RATIOS[crop.ratio] || null, vw, vh);
    paintCropFrame();
  }

  async function openCrop(file) {
    if (!form || form.photos.length >= MAX_PHOTOS) return false;
    let img = null;
    try {
      img = await loadImageFile(file);
    } catch (err) {
      return false;
    }
    const nw = img.naturalWidth || img.width || 0;
    const nh = img.naturalHeight || img.height || 0;
    if (!nw || !nh) return false;
    if (!form || form.photos.length >= MAX_PHOTOS) return false;
    const k = Math.min(1, CROP_MAX / Math.max(nw, nh));
    const cv = els.cropCanvas;
    cv.width = Math.max(1, Math.round(nw * k));
    cv.height = Math.max(1, Math.round(nh * k));
    const ctx = cv.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
    crop.file = file;
    crop.ratio = 'free';
    crop.rect = null;
    crop.drag = null;
    paintCropRatio();
    els.cropWrap.hidden = false;
    lockBody();
    layoutCrop();
    autoFitCrop(); // 처음 열 때 밝은 종이/카드 영역을 찾아 미리 맞춰 둔다
    return true;
  }

  function closeCrop() {
    els.cropWrap.hidden = true;
    crop.file = null;
    crop.rect = null;
    crop.drag = null;
    crop.ratio = 'free';
    crop.detected = null;
    paintCropRatio();
    lockBody();
  }

  function cancelCrop() {
    cropQueue = [];
    closeCrop();
  }

  // 여러 장을 고르면 한 장씩 차례로 자르기 창을 띄운다
  async function openNextCrop() {
    while (cropQueue.length) {
      const file = cropQueue.shift();
      if (await openCrop(file)) return;
      toast('사진을 읽지 못했어요. 다른 사진으로 해 주세요.');
    }
  }

  function addCropPhoto(data) {
    if (!form) return false;
    if (form.photos.length >= MAX_PHOTOS) {
      toast(`사진은 ${MAX_PHOTOS}장까지 넣을 수 있어요`);
      return false;
    }
    form.photos.push(data);
    renderPhotoGrid();
    return true;
  }

  function rotateCrop() {
    const cv = els.cropCanvas;
    if (!cv.width || !cv.height) return;
    const tmp = document.createElement('canvas');
    tmp.width = cv.width;
    tmp.height = cv.height;
    tmp.getContext('2d').drawImage(cv, 0, 0);
    const next = document.createElement('canvas');
    next.width = tmp.height;
    next.height = tmp.width;
    const ctx = next.getContext('2d');
    ctx.translate(next.width, 0);
    ctx.rotate(Math.PI / 2);
    ctx.drawImage(tmp, 0, 0);
    cv.width = next.width;
    cv.height = next.height;
    const cvCtx = cv.getContext('2d');
    cvCtx.imageSmoothingEnabled = true;
    cvCtx.imageSmoothingQuality = 'high';
    cvCtx.drawImage(next, 0, 0);
    layoutCrop();
    autoFitCrop(); // 회전한 사진 기준으로 다시 찾는다
  }

  function applyCrop() {
    const cv = els.cropCanvas;
    if (!crop.rect || !cv.width || !cv.height) return;
    const r = crop.rect;
    const sx = Math.max(0, Math.round(r.x * crop.scale));
    const sy = Math.max(0, Math.round(r.y * crop.scale));
    const sw = Math.max(1, Math.min(cv.width - sx, Math.round(r.w * crop.scale)));
    const sh = Math.max(1, Math.min(cv.height - sy, Math.round(r.h * crop.scale)));
    const k = Math.min(1, MAX_SIDE / Math.max(sw, sh));
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(sw * k));
    out.height = Math.max(1, Math.round(sh * k));
    const ctx = out.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(cv, sx, sy, sw, sh, 0, 0, out.width, out.height);
    const data = out.toDataURL('image/jpeg', QUALITY);
    addCropPhoto(data);
    closeCrop();
    openNextCrop();
  }

  async function keepCropOriginal() {
    const file = crop.file;
    if (!file) return;
    let data = null;
    try {
      data = await compressImage(file);
    } catch (err) {
      toast('사진을 읽지 못했어요. 다른 사진으로 해 주세요.');
      closeCrop();
      openNextCrop();
      return;
    }
    addCropPhoto(data);
    closeCrop();
    openNextCrop();
  }

  // 비율을 지키면서 모서리를 끄는 크기 조절
  function cropResizeRect(r0, mode, dx, dy, ratio, vw, vh) {
    const left = mode.indexOf('l') >= 0;
    const top = mode.indexOf('t') >= 0;
    const ax = left ? r0.x + r0.w : r0.x;   // 고정점(반대 모서리)
    const ay = top ? r0.y + r0.h : r0.y;
    const px = (left ? r0.x : r0.x + r0.w) + dx;
    const py = (top ? r0.y : r0.y + r0.h) + dy;
    const maxW = Math.max(CROP_MIN, left ? ax : vw - ax);
    const maxH = Math.max(CROP_MIN, top ? ay : vh - ay);
    let w = Math.abs(px - ax);
    let h = Math.abs(py - ay);
    if (ratio) {
      w = Math.max(w, h * ratio);
      h = w / ratio;
      if (w > maxW) { w = maxW; h = w / ratio; }
      if (h > maxH) { h = maxH; w = h * ratio; }
      w = Math.max(w, Math.max(Math.min(CROP_MIN, maxW), Math.min(CROP_MIN, maxH) * ratio));
      h = Math.min(w / ratio, maxH);
      w = h * ratio;
    } else {
      w = Math.min(Math.max(w, Math.min(CROP_MIN, maxW)), maxW);
      h = Math.min(Math.max(h, Math.min(CROP_MIN, maxH)), maxH);
    }
    return { x: left ? ax - w : ax, y: top ? ay - h : ay, w, h };
  }

  function cropDragStart(e) {
    if (!crop.rect) return;
    const handle = e.target.closest('[data-handle]');
    const frame = e.target.closest('#cropFrame');
    if (!handle && !frame) return;
    if (typeof e.button === 'number' && e.button > 0) return;
    e.preventDefault();
    crop.drag = {
      mode: handle ? handle.dataset.handle : 'move',
      sx: e.clientX,
      sy: e.clientY,
      rect0: { ...crop.rect },
      ratio: handle ? (CROP_RATIOS[crop.ratio] || null) : null,
    };
    const target = handle || frame;
    if (target.setPointerCapture) {
      try { target.setPointerCapture(e.pointerId); } catch (err) { /* 무시 */ }
    }
  }

  function cropDragMove(e) {
    const d = crop.drag;
    if (!d) return;
    e.preventDefault();
    const { w: vw, h: vh } = cropViewSize();
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (d.mode === 'move') {
      const x = Math.min(Math.max(0, d.rect0.x + dx), Math.max(0, vw - d.rect0.w));
      const y = Math.min(Math.max(0, d.rect0.y + dy), Math.max(0, vh - d.rect0.h));
      crop.rect = { x, y, w: d.rect0.w, h: d.rect0.h };
    } else {
      crop.rect = cropResizeRect(d.rect0, d.mode, dx, dy, d.ratio, vw, vh);
    }
    paintCropFrame();
  }

  function cropDragEnd() {
    crop.drag = null;
  }

  /* ── 등록·수정 ───────────────────────── */
  function blankForm() {
    return {
      id: null,
      photos: [],
      name: '',
      brand: '',
      category: 'cafe',
      expiry: '',
      noExpiry: false,
      amount: null,
      balance: null,
      giver: '',
      memo: '',
      used: false,
      usedAt: null,
      createdAt: null,
    };
  }

  function paintForm() {
    if (!form) return;
    els.fName.value = form.name;
    els.fBrand.value = form.brand;
    els.fGiver.value = form.giver;
    els.fMemo.value = form.memo;
    els.fExpiry.value = form.expiry || '';
    els.fNoExpiry.setAttribute('aria-checked', String(form.noExpiry));
    els.fExpiry.disabled = form.noExpiry;
    els.expiryHint.textContent = form.noExpiry
      ? '기한이 없는 쿠폰은 목록 맨 뒤쪽에 모아 보여 드려요.'
      : '기한이 지나면 목록 뒤쪽에 ‘기한 지남’으로 표시돼요.';
    const amountOn = form.amount !== null || form.balance !== null;
    els.fIsAmount.setAttribute('aria-checked', String(amountOn));
    els.amountBox.hidden = !amountOn;
    els.fAmount.value = comma(form.amount);
    els.fBalance.value = comma(form.balance);
    els.catSeg.querySelectorAll('button[data-cat]').forEach((btn) => {
      btn.setAttribute('aria-pressed', String(btn.dataset.cat === form.category));
    });
    renderPhotoGrid();
  }

  function openForm(id = null) {
    const item = id ? state.items.find((i) => i.id === id) : null;
    if (item) {
      form = {
        id: item.id,
        photos: item.photos.slice(),
        name: item.name,
        brand: item.brand,
        category: item.category,
        expiry: item.expiry || '',
        noExpiry: !item.expiry,
        amount: item.amount,
        balance: item.balance,
        giver: item.giver,
        memo: item.memo,
        used: item.used,
        usedAt: item.usedAt,
        createdAt: item.createdAt,
      };
    } else {
      form = blankForm();
    }
    els.addTitle.textContent = item ? '쿠폰 수정' : '쿠폰 등록';
    paintForm();
    els.addBody.scrollTop = 0;
    els.addWrap.hidden = false;
    lockBody();
    if (!item) {
      const tile = els.photoGrid.querySelector('#photoAdd');
      if (tile) {
        tile.classList.add('pulse');
        setTimeout(() => tile.classList.remove('pulse'), 3000);
      }
    }
  }

  function closeForm() {
    els.addWrap.hidden = true;
    form = null;
    els.photoInput.value = '';
    lockBody();
  }

  function numValue(el) {
    if (!el) return null;
    const digits = String(el.value || '').replace(/[^\d]/g, '');
    if (!digits) return null;
    return Math.min(99999999, parseInt(digits, 10));
  }

  async function saveForm() {
    if (!form) return;
    const name = els.fName.value.trim();
    if (!name) {
      toast('쿠폰명을 적어 주세요');
      els.fName.focus();
      return;
    }
    const amountOn = els.fIsAmount.getAttribute('aria-checked') === 'true';
    let amount = null;
    let balance = null;
    if (amountOn) {
      amount = numValue(els.fAmount);
      balance = numValue(els.fBalance);
      if (amount === null && balance === null) {
        toast('금액을 적어 주세요');
        els.fAmount.focus();
        return;
      }
      if (amount === null) amount = balance;
      if (balance === null) balance = amount;
      if (balance > amount) {
        toast('남은 잔액은 금액보다 클 수 없어요');
        return;
      }
    }
    const wasEdit = !!form.id;
    const expiry = form.noExpiry ? null : (els.fExpiry.value || null);
    const base = wasEdit ? state.items.find((i) => i.id === form.id) : null;
    const now = Date.now();
    const item = normalize({
      id: form.id || uid(),
      name,
      brand: els.fBrand.value.trim(),
      category: form.category,
      expiry,
      used: base ? base.used : false,
      usedAt: base ? base.usedAt : null,
      giver: els.fGiver.value.trim(),
      memo: els.fMemo.value.trim(),
      amount,
      balance,
      photos: form.photos,
      createdAt: base ? base.createdAt : now,
      updatedAt: now,
    });

    const prev = state.items.slice();
    if (base) state.items = state.items.map((i) => (i.id === item.id ? item : i));
    else state.items = state.items.concat([item]);

    const ok = await persistItem(item);
    if (!ok) {
      state.items = prev;
      render();
      toast('저장하지 못했어요. 저장 공간을 확인해 주세요.');
      return;
    }
    closeForm();
    if (!wasEdit) {
      // 담은 쿠폰이 바로 보이도록 목록 상태를 되돌린다
      state.tab = 'available';
      state.cat = 'all';
    }
    render();
    toast(wasEdit ? '수정했어요' : '쿠폰을 담았어요');
  }

  /* ── 상세 ───────────────────────── */
  function openDetail(id) {
    const item = state.items.find((i) => i.id === id);
    if (!item) return;
    currentId = id;
    renderDetail();
    els.detailBody.scrollTop = 0;
    els.detailWrap.hidden = false;
    lockBody();
  }

  function closeDetail() {
    els.detailWrap.hidden = true;
    currentId = null;
    lockBody();
  }

  function renderDetail() {
    const item = state.items.find((i) => i.id === currentId);
    if (!item) { closeDetail(); return; }
    const b = badgeOf(item);
    const d = dayDiff(item.expiry);
    els.detailTitle.textContent = item.brand || '쿠폰';

    let line = '';
    let lineCls = '';
    if (item.used) {
      line = `${koreanDate(item.usedAt)}에 사용완료했어요`;
    } else if (d === null) {
      line = '유효기간이 없는 쿠폰이에요';
    } else if (d < 0) {
      line = `유효기간이 ${-d}일 지났어요`;
    } else if (d === 0) {
      line = '오늘까지 쓸 수 있어요';
      lineCls = 'urgent';
    } else {
      line = `${koreanDate(item.expiry)}까지 · ${d}일 남았어요`;
      lineCls = d <= 3 ? 'urgent' : d <= SOON_DAYS ? 'soon' : '';
    }

    let html = '';
    html += `<div class="detail-top"><span class="dday inline ${b.key}">${b.label}</span><span class="detail-brand">${esc(item.brand || CAT_LABEL[item.category])}</span></div>`;
    html += `<h3 class="detail-name">${esc(item.name)}</h3>`;
    html += `<p class="detail-line ${lineCls}">${esc(line)}</p>`;

    if (item.photos.length) {
      html += `<div class="shot-wrap">
        <button type="button" class="shot" data-zoom="0" aria-label="쿠폰 사진 1 크게 보기"><img src="${item.photos[0]}" alt="쿠폰 사진 1"></button>
      </div>`;
      if (item.photos.length > 1) {
        html += '<div class="shot-thumbs">';
        item.photos.slice(1).forEach((src, i) => {
          html += `<button type="button" class="shot-thumb" data-zoom="${i + 1}" aria-label="쿠폰 사진 ${i + 2} 크게 보기"><img src="${src}" alt="쿠폰 사진 ${i + 2}"></button>`;
        });
        html += '</div>';
      }
      html += '<p class="shot-hint">사진을 누르면 흰 바탕으로 크게 볼 수 있어요</p>';
    } else {
      html += '<div class="no-shot">등록된 사진이 없어요. 수정에서 사진을 더할 수 있어요.</div>';
    }

    if (item.amount !== null) {
      const left = item.balance === null ? item.amount : item.balance;
      html += `<div class="balance-card">
        <div class="balance-row"><span class="balance-k">금액</span><span class="balance-v">${won(item.amount)}</span></div>
        <div class="balance-row">
          <span class="balance-k">남은 잔액</span>
          <div class="input-money"><input type="text" inputmode="numeric" id="detailBalance" value="${comma(left)}" aria-label="남은 잔액"><span>원</span></div>
          <button type="button" class="balance-save" id="balanceSave">저장</button>
        </div>
      </div>`;
    }

    html += '<dl class="info">';
    html += `<div><dt>카테고리</dt><dd>${CAT_LABEL[item.category]}</dd></div>`;
    html += `<div><dt>유효기간</dt><dd>${item.expiry ? koreanDate(item.expiry) : '기한 없음'}</dd></div>`;
    if (item.giver) html += `<div><dt>준 사람</dt><dd>${esc(item.giver)}</dd></div>`;
    if (item.memo) html += `<div><dt>메모</dt><dd>${esc(item.memo)}</dd></div>`;
    html += '</dl>';

    els.detailBody.innerHTML = html;
    els.detailUse.textContent = item.used ? '되돌리기' : '사용완료';
    els.detailUse.className = 'btn primary';
  }

  async function toggleUsed() {
    const item = state.items.find((i) => i.id === currentId);
    if (!item) return;
    const next = normalize({
      ...item,
      used: !item.used,
      usedAt: item.used ? null : todayISO(),
      updatedAt: Date.now(),
    });
    const prev = state.items.slice();
    state.items = state.items.map((i) => (i.id === next.id ? next : i));
    const ok = await persistItem(next);
    if (!ok) {
      state.items = prev;
      render();
      toast('저장하지 못했어요');
      return;
    }
    closeDetail();
    render();
    toast(next.used ? '사용완료로 넘겼어요' : '사용 가능으로 되돌렸어요');
  }

  async function saveBalance() {
    const item = state.items.find((i) => i.id === currentId);
    if (!item) return;
    const input = document.getElementById('detailBalance');
    const v = numValue(input);
    if (v === null) {
      toast('남은 잔액을 숫자로 적어 주세요');
      return;
    }
    if (item.amount !== null && v > item.amount) {
      toast('금액보다 클 수 없어요');
      return;
    }
    const next = normalize({ ...item, balance: v, updatedAt: Date.now() });
    const prev = state.items.slice();
    state.items = state.items.map((i) => (i.id === next.id ? next : i));
    const ok = await persistItem(next);
    if (!ok) {
      state.items = prev;
      render();
      toast('저장하지 못했어요');
      return;
    }
    render();
    renderDetail();
    toast('남은 잔액을 저장했어요');
  }

  async function deleteCurrent() {
    const item = state.items.find((i) => i.id === currentId);
    if (!item) return;
    const ok = await askConfirm({
      title: '이 쿠폰을 지울까요?',
      desc: `‘${esc(item.name)}’ 쿠폰과 사진이 함께 지워져요.<br>되돌릴 수 없어요.`,
      ok: '지우기',
      tone: 'danger',
    });
    if (!ok) return;
    const prev = state.items.slice();
    state.items = state.items.filter((i) => i.id !== item.id);
    const done = await forgetItem(item.id);
    if (!done) {
      state.items = prev;
      render();
      toast('지우지 못했어요');
      return;
    }
    closeDetail();
    render();
    toast('쿠폰을 지웠어요');
  }

  /* ── 백업·복원 ───────────────────────── */
  function openMore() {
    hideToast();
    const total = state.items.length;
    const avail = state.items.filter((i) => !i.used).length;
    const done = total - avail;
    const photos = state.items.reduce((s, e) => s + e.photos.length, 0);
    let bytes = 0;
    try {
      bytes = new Blob([JSON.stringify({ version: VERSION, items: state.items })]).size;
    } catch (err) { bytes = 0; }
    const kb = bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`;
    const where = state.mode === 'idb' ? 'IndexedDB' : 'localStorage';
    els.moreInfo.textContent = `쿠폰 ${total}개 (사용 가능 ${avail} · 사용완료 ${done}) · 사진 ${photos}장 · 저장 용량 약 ${kb} · ${where}`;
    els.moreWrap.hidden = false;
    lockBody();
  }

  function backup() {
    const payload = {
      app: 'coupon-box',
      version: VERSION,
      exportedAt: new Date().toISOString(),
      items: state.items,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `coupon-box-backup-${todayISO()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    els.moreWrap.hidden = true;
    lockBody();
    toast('백업 파일을 내려받았어요');
  }

  async function restoreFromFile(file) {
    if (!file) return;
    let data = null;
    try {
      data = JSON.parse(await file.text());
    } catch (err) {
      toast('백업 파일을 읽을 수 없어요');
      return;
    }
    const list = Array.isArray(data) ? data : (data && data.items);
    if (!Array.isArray(list)) {
      toast('백업 파일이 아니에요');
      return;
    }
    const clean = list
      .filter((i) => i && (i.name || i.brand))
      .map(normalize);
    if (!clean.length) {
      toast('불러올 쿠폰이 없어요');
      return;
    }

    els.moreWrap.hidden = true;
    lockBody();
    const ok = await askConfirm({
      title: `백업 ${clean.length}개를 불러올까요?`,
      desc: '지금 쿠폰은 백업에 있는 내용으로 바뀌어요.',
      ok: '불러오기',
    });
    if (!ok) return;

    const prev = state.items.slice();
    const written = await persistAll(clean);
    if (!written) {
      state.items = prev;
      render();
      toast('복원하지 못했어요. 저장 공간을 확인해 주세요.');
      return;
    }
    state.items = clean;
    render();
    toast(`${clean.length}개를 불러왔어요`);
  }

  /* ── 이벤트 ───────────────────────── */
  function bindEvents() {
    els.fabAdd.addEventListener('click', () => openForm());

    els.tabTabs.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-tab]');
      if (!btn) return;
      state.tab = btn.dataset.tab;
      render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    els.catChips.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-cat]');
      if (!btn) return;
      state.cat = btn.dataset.cat;
      render();
    });

    els.couponGrid.addEventListener('click', (e) => {
      const add = e.target.closest('#addCard');
      if (add) { openForm(); return; }
      const card = e.target.closest('[data-id]');
      if (card) openDetail(card.dataset.id);
    });

    let searchTimer = null;
    els.searchInput.addEventListener('input', (e) => {
      state.q = e.target.value;
      els.searchClear.hidden = !state.q.length;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderList, 80);
    });
    els.searchClear.addEventListener('click', () => {
      els.searchInput.value = '';
      state.q = '';
      els.searchClear.hidden = true;
      renderList();
      els.searchInput.focus();
    });

    // 등록·수정 시트
    els.addClose.addEventListener('click', closeForm);
    els.addSave.addEventListener('click', saveForm);
    els.photoInput.addEventListener('change', (e) => {
      handlePhotoFiles(e.target.files);
    });
    els.photoGrid.addEventListener('click', (e) => {
      const del = e.target.closest('[data-remove]');
      if (del && form) {
        form.photos.splice(Number(del.dataset.remove), 1);
        renderPhotoGrid();
        return;
      }
      const add = e.target.closest('#photoAdd');
      if (add) { els.photoInput.click(); return; }
      const photo = e.target.closest('[data-photo]');
      if (photo && form) openZoom(form.photos[Number(photo.dataset.photo)]);
    });
    els.catSeg.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-cat]');
      if (!btn || !form) return;
      form.category = btn.dataset.cat;
      els.catSeg.querySelectorAll('button[data-cat]').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.dataset.cat === form.category));
      });
    });
    els.fNoExpiry.addEventListener('click', () => {
      if (!form) return;
      form.noExpiry = !form.noExpiry;
      els.fNoExpiry.setAttribute('aria-checked', String(form.noExpiry));
      els.fExpiry.disabled = form.noExpiry;
      if (form.noExpiry) els.fExpiry.value = '';
      els.expiryHint.textContent = form.noExpiry
        ? '기한이 없는 쿠폰은 목록 맨 뒤쪽에 모아 보여 드려요.'
        : '기한이 지나면 목록 뒤쪽에 ‘기한 지남’으로 표시돼요.';
    });
    els.fIsAmount.addEventListener('click', () => {
      if (!form) return;
      const on = els.fIsAmount.getAttribute('aria-checked') !== 'true';
      els.fIsAmount.setAttribute('aria-checked', String(on));
      els.amountBox.hidden = !on;
      if (on) els.fAmount.focus();
    });
    [els.fAmount, els.fBalance].forEach((el) => {
      el.addEventListener('input', () => {
        const v = numValue(el);
        el.value = v === null ? '' : comma(v);
      });
    });

    // 상세
    els.detailClose.addEventListener('click', closeDetail);
    els.detailUse.addEventListener('click', toggleUsed);
    els.detailEdit.addEventListener('click', () => {
      const id = currentId;
      closeDetail();
      openForm(id);
    });
    els.detailDelete.addEventListener('click', deleteCurrent);
    els.detailBody.addEventListener('click', (e) => {
      const save = e.target.closest('#balanceSave');
      if (save) { saveBalance(); return; }
      const shot = e.target.closest('[data-zoom]');
      if (shot) {
        const item = state.items.find((i) => i.id === currentId);
        if (item) openZoom(item.photos[Number(shot.dataset.zoom)]);
      }
    });
    els.detailBody.addEventListener('input', (e) => {
      if (e.target.id !== 'detailBalance') return;
      const v = numValue(e.target);
      e.target.value = v === null ? '' : comma(v);
    });

    // 확대
    els.zoomClose.addEventListener('click', closeZoom);
    els.zoomWrap.addEventListener('click', (e) => {
      if (e.target === els.zoomWrap || e.target === els.zoomImg) closeZoom();
      else if (!e.target.closest('.zoom-note')) closeZoom();
    });

    // 사진 자르기
    els.cropCancel.addEventListener('click', cancelCrop);
    els.cropApply.addEventListener('click', applyCrop);
    els.cropKeep.addEventListener('click', keepCropOriginal);
    els.cropRotate.addEventListener('click', rotateCrop);
    els.cropAuto.addEventListener('click', () => {
      if (!autoFitCrop()) toast('자를 영역을 찾지 못해 전체로 맞췄어요');
    });
    els.cropRatios.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-ratio]');
      if (!btn) return;
      const key = Object.prototype.hasOwnProperty.call(CROP_RATIOS, btn.dataset.ratio) ? btn.dataset.ratio : 'free';
      crop.ratio = key;
      paintCropRatio();
      cropFitRatio(CROP_RATIOS[key]);
    });
    els.cropView.addEventListener('pointerdown', cropDragStart);
    document.addEventListener('pointermove', cropDragMove);
    document.addEventListener('pointerup', cropDragEnd);
    document.addEventListener('pointercancel', cropDragEnd);
    window.addEventListener('resize', () => {
      if (!els.cropWrap || els.cropWrap.hidden) return;
      layoutCrop();
      cropFitToDetected();
    });

    // 더보기·백업
    els.moreBtn.addEventListener('click', openMore);
    els.moreClose.addEventListener('click', () => { els.moreWrap.hidden = true; lockBody(); });
    els.backupBtn.addEventListener('click', backup);
    els.restoreBtn.addEventListener('click', () => els.restoreInput.click());
    els.restoreInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      if (file) restoreFromFile(file);
    });

    // 확인 창
    els.confirmOk.addEventListener('click', () => resolveConfirm(true));
    els.confirmCancel.addEventListener('click', () => resolveConfirm(false));
    els.confirmWrap.addEventListener('click', (e) => {
      if (e.target === els.confirmWrap) resolveConfirm(false);
    });

    [['addWrap', closeForm], ['detailWrap', closeDetail]].forEach(([key, fn]) => {
      els[key].addEventListener('click', (e) => {
        if (e.target === els[key]) fn();
      });
    });
    els.moreWrap.addEventListener('click', (e) => {
      if (e.target === els.moreWrap) { els.moreWrap.hidden = true; lockBody(); }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!els.cropWrap.hidden) { cancelCrop(); return; }
      if (!els.zoomWrap.hidden) { closeZoom(); return; }
      if (!els.confirmWrap.hidden) { resolveConfirm(false); return; }
      if (!els.moreWrap.hidden) { els.moreWrap.hidden = true; lockBody(); return; }
      if (!els.detailWrap.hidden) { closeDetail(); return; }
      if (!els.addWrap.hidden) closeForm();
    });
  }

  /* ── 시작 ───────────────────────── */
  async function init() {
    cacheEls();
    bindEvents();
    let stored = [];
    try {
      db = await openDB();
      state.mode = 'idb';
      stored = await idbAll();
    } catch (err) {
      db = null;
      state.mode = 'local';
      stored = lsAll();
    }
    state.items = (stored || []).map(normalize);
    render();
    document.documentElement.dataset.ready = '1';

    if ('serviceWorker' in navigator) {
      const register = () => navigator.serviceWorker.register('./sw.js').catch(() => {});
      if (document.readyState === 'complete') register();
      else window.addEventListener('load', register);
    }
  }

  window.__couponbox = {
    version: VERSION,
    get items() { return state.items; },
    get view() { return { tab: state.tab, cat: state.cat, q: state.q, mode: state.mode }; },
    get form() { return form; },
    get currentId() { return currentId; },
    get crop() {
      return {
        open: !!(els.cropWrap && !els.cropWrap.hidden),
        ratio: crop.ratio,
        rect: crop.rect ? { ...crop.rect } : null,
        scale: crop.scale,
        source: els.cropCanvas ? { w: els.cropCanvas.width, h: els.cropCanvas.height } : null,
        detected: crop.detected ? { ...crop.detected } : null,
        queued: cropQueue.length,
      };
    },
    autoFitCrop,
    badgeOf,
    dayDiff,
    sortAvailable,
    sortDone,
    compressImage,
    async replaceAll(list) {
      const items = (list || []).map(normalize);
      await persistAll(items);
      state.items = items;
      render();
      return state.items.length;
    },
    async clearAll() {
      await persistAll([]);
      state.items = [];
      render();
    },
  };

  init();
})();
