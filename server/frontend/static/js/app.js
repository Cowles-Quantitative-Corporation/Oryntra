


'use strict';

var plan = {};
window.plan = plan;

const nativeFetch = window.fetch.bind(window);
function apiFetch(url, options = {}) {
  const opts = Object.assign({ credentials: 'same-origin', cache: 'no-store' }, options || {});
  opts.headers = opts.headers || {};
  return nativeFetch(url, opts);
}


const IS_PRO_BUILD = false;
const AUTH_TOKEN_KEY = 'oryntra_auth_token';
const AUTH_USER_KEY = 'oryntra_auth_user';
const AUTH_TOKEN_COOKIE = 'oryntra_client_session';
const AUTH_USER_COOKIE = 'oryntra_user_cache';
let lastDialogFocus = null;
let lastDevLabFocus = null;

function getCookie(name) {
  try {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.$?*|{}()\[\]\\/\+^]/g, '\\$&') + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  } catch (_) {
    return '';
  }
}

function setClientCookie(name, value, days = 30) {
  try {
    const maxAge = Math.max(1, days) * 24 * 60 * 60;
    document.cookie = `${name}=${encodeURIComponent(value || '')}; Max-Age=${maxAge}; Path=/; SameSite=Lax`;
  } catch (_) {}
}

function deleteClientCookie(name) {
  try {
    document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
  } catch (_) {}
}

function safeStorageGet(key) {
  try { return localStorage.getItem(key) || ''; } catch (_) { return ''; }
}

function safeStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_) {}
}

function safeStorageRemove(key) {
  try { localStorage.removeItem(key); } catch (_) {}
}

const APP_THEME_KEY = 'oryntra_theme';
const themeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
let themeMediaListenerAttached = false;

function getThemePreference() {
  const stored = safeStorageGet(APP_THEME_KEY);
  return ['system', 'light', 'dark'].includes(stored) ? stored : 'light';
}

function resolveTheme(preference = getThemePreference()) {
  if (preference === 'light' || preference === 'dark') return preference;
  return themeMediaQuery.matches ? 'dark' : 'light';
}

function updateThemeControls(preference) {
  document.querySelectorAll('[data-theme-value]').forEach((choice) => {
    const selected = choice.dataset.themeValue === preference;
    choice.setAttribute('aria-checked', selected ? 'true' : 'false');
    choice.tabIndex = selected ? 0 : -1;
  });
}

function applyTheme(preference, options = {}) {
  const nextPreference = ['system', 'light', 'dark'].includes(preference) ? preference : 'light';
  const previousTheme = document.documentElement.dataset.theme;
  const resolvedTheme = resolveTheme(nextPreference);

  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.dataset.themePreference = nextPreference;
  if (options.persist !== false) safeStorageSet(APP_THEME_KEY, nextPreference);

  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) {
    const pageColor = getComputedStyle(document.documentElement).getPropertyValue('--bg-void').trim();
    themeColor.content = pageColor || (resolvedTheme === 'light' ? '#d4dce5' : '#080d14');
  }
  updateThemeControls(nextPreference);

  if (options.refreshChart && previousTheme !== resolvedTheme && currentTicker) {
    loadTradingView(currentTicker, currentInterval, currentAnalysis?.exchange);
  }
}

function initThemeSettings() {
  const choices = Array.from(document.querySelectorAll('[data-theme-value]'));
  const preference = getThemePreference();
  applyTheme(preference, { persist: false });

  choices.forEach((choice, index) => {
    choice.addEventListener('click', () => {
      applyTheme(choice.dataset.themeValue, { refreshChart: true });
    });
    choice.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
      const next = choices[(index + step + choices.length) % choices.length];
      next.focus();
      applyTheme(next.dataset.themeValue, { refreshChart: true });
    });
  });

  if (!themeMediaListenerAttached) {
    themeMediaQuery.addEventListener('change', () => {
      if (getThemePreference() === 'system') {
        applyTheme('system', { persist: false, refreshChart: true });
      }
    });
    themeMediaListenerAttached = true;
  }
}

const APP_VERSION = '1.1.3';
const APP_RELEASE_KEY = 'oryntra_client_release';
const PUBLIC_ANALYSIS_ENGINE = 'v8';

function applyReleaseClientReset() {
  try {
    const previous = safeStorageGet(APP_RELEASE_KEY);
    if (previous !== APP_VERSION) {
      const keys = Object.keys(localStorage || {});
      keys.forEach((key) => {
        if (
          key === 'oryntra_pattern_engine_mode' ||
          key === 'oryntra_workspace_model' ||
          key === 'oryntra_total_stock_searches' ||
          key.startsWith('oryntra_paper_cache_')
        ) {
          localStorage.removeItem(key);
        }
      });
      safeStorageSet(APP_RELEASE_KEY, APP_VERSION);
    }
    safeStorageSet('oryntra_pattern_engine_mode', PUBLIC_ANALYSIS_ENGINE);
  } catch (_) {
    safeStorageSet('oryntra_pattern_engine_mode', PUBLIC_ANALYSIS_ENGINE);
  }
}

applyReleaseClientReset();

let authToken = safeStorageGet(AUTH_TOKEN_KEY) || getCookie(AUTH_TOKEN_COOKIE) || '';
let currentUser = null;
let authMode = 'login';
let analysisAccessState = {
  ready: false,
  policy: null,
  quota: null,
};
let analysisAccessPromise = null;
let runtimeReadyPromise = null;
let subscriptionModalContext = 'membership';
let runtimeCapabilities = {
  partner_ads: {enabled:false, placements:{native:false, desktop:false, mobile:false}},
  subscription_offers_enabled: false,
  turnstile: {enabled:false, site_key:''},
};
let pendingAnalysisIntent = null;
let providerOnboardingRequest = null;
// A provider key stays on this browser device and never enters a cookie or an Oryntra request.
let browserProviderKeys = { polygon: '', twelvedata: '' };
const PROVIDER_KEY_DATABASE = 'oryntra-browser-provider-keys-v1';
const PROVIDER_KEY_STORE = 'keys';
let providerKeyLoadOwner = '';

function providerKeyOwner() {
  return currentUser?.id ? String(currentUser.id) : '';
}

function openProviderKeyDatabase() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) { reject(new Error('This browser does not support secure local key storage.')); return; }
    const request = window.indexedDB.open(PROVIDER_KEY_DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(PROVIDER_KEY_STORE, {keyPath: 'id'});
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('Browser key storage is unavailable.'));
  });
}

async function withProviderKeyStore(mode, operation) {
  const database = await openProviderKeyDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(PROVIDER_KEY_STORE, mode);
      const store = transaction.objectStore(PROVIDER_KEY_STORE);
      let result;
      try { result = operation(store); } catch (error) { reject(error); return; }
      transaction.oncomplete = () => resolve(result?.result);
      transaction.onerror = () => reject(transaction.error || new Error('Browser key storage failed.'));
    });
  } finally {
    database.close();
  }
}

function providerKeyRecordId(provider) {
  return `${providerKeyOwner()}:${provider}`;
}

async function loadPersistedProviderKeys() {
  const owner = providerKeyOwner();
  if (!owner || providerKeyLoadOwner === owner) return;
  providerKeyLoadOwner = owner;
  try {
    const records = await Promise.all(['polygon', 'twelvedata'].map(provider => withProviderKeyStore('readonly', store => store.get(`${owner}:${provider}`))));
    browserProviderKeys = {
      polygon: String(records[0]?.value || ''),
      twelvedata: String(records[1]?.value || ''),
    };
  } catch (_) {
    browserProviderKeys = {polygon: '', twelvedata: ''};
  }
}

async function persistBrowserProviderKey(provider, value) {
  const owner = providerKeyOwner();
  if (!owner) return;
  const id = providerKeyRecordId(provider);
  if (!value) {
    await withProviderKeyStore('readwrite', store => store.delete(id));
    return;
  }
  await withProviderKeyStore('readwrite', store => store.put({id, value, updatedAt: new Date().toISOString()}));
}

function authHeaders(json=true) {
  const headers = json ? {'Content-Type':'application/json'} : {};
  authToken = authToken || safeStorageGet(AUTH_TOKEN_KEY) || getCookie(AUTH_TOKEN_COOKIE) || '';
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  return headers;
}

function apiJson(response) {
  return response.json().catch(() => ({})).then(payload => {
    if (response.ok) return payload;
    const detail = payload.detail || payload;
    if (response.status === 402 || (detail && ['SUBSCRIPTION_REQUIRED', 'MODEL_SUBSCRIPTION_REQUIRED'].includes(detail.code))) {
      showSubscriptionModal(detail?.model || 'membership');
      throw new Error(detail?.message || 'An active Oryntra AI Pro subscription is required for this feature.');
    }
    if (response.status === 409 && detail?.code === 'MODEL_RESEARCH_NOT_RELEASED') {
      showSubscriptionModal('minerva_unreleased');
      throw new Error(detail?.message || 'This research candidate is not released yet.');
    }
    if (response.status === 401) {
      openAuthModal('login');
      throw new Error('Sign in required.');
    }
    const validationMessage = Array.isArray(detail)
      ? detail.map((item) => {
          const location = Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
          return `${location ? `${location}: ` : ''}${item?.msg || item?.message || 'Invalid value'}`;
        }).join(' · ')
      : '';
    const message = typeof detail === 'string'
      ? detail
      : validationMessage || detail?.message || detail?.error || payload?.message || payload?.error;
    const error = new Error(message || `Request failed with HTTP ${response.status}.`);
    error.statusCode = response.status;
    error.code = detail && typeof detail === 'object' ? detail.code : payload?.code;
    error.detail = detail;
    throw error;
  });
}


const API = {
  scan: (ticker, period='6mo', model='v8') => apiFetch('/api/intelligence/scan', {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify({ticker, period, model})
  }).then(apiJson),

  scanMultiple: (tickers, period='6mo', model='v8') => apiFetch('/api/intelligence/scan-multiple', {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify({tickers, period, model})
  }).then(apiJson),

  scanUploaded: (ticker, period, provider, bars, model='v8') => apiFetch('/api/intelligence/scan-upload', {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify({ticker, period, provider, bars, model})
  }).then(apiJson),

  explain: (ticker, analysis, question=null) => apiFetch(`/api/ai/explain`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ticker, analysis, question})
  }).then(r => r.json()),

  stats: () => apiFetch('/api/app/stats', {cache: 'no-store'}).then(apiJson),
  runtime: () => apiFetch('/api/app/version', {cache:'no-store'}).then(apiJson),

  quant: {
    run: (data) => apiFetch('/api/quant/run', {method:'POST', headers:authHeaders(true), body:JSON.stringify(data)}).then(apiJson),
    runUploaded: (data) => apiFetch('/api/quant/run-upload', {method:'POST', headers:authHeaders(true), body:JSON.stringify(data)}).then(apiJson),
  },

  universal: {
    blueprint: () => apiFetch('/api/universal/blueprint', {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
    taxonomySymbol: (symbol) => apiFetch(`/api/universal/taxonomy/${encodeURIComponent(symbol)}`, {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
  },

  portfolioLab: {
    runUploaded: (data) => apiFetch('/api/portfolio-lab/run-upload', {method:'POST', headers:authHeaders(true), body:JSON.stringify(data)}).then(apiJson),
    runs: () => apiFetch('/api/portfolio-lab/runs', {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
  },

  debug: {
    access: () => apiFetch('/api/internal/debug/access', {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
    subscription: (plan) => apiFetch('/api/internal/debug/subscription', {method:'POST', headers:authHeaders(true), body:JSON.stringify({plan})}).then(apiJson),
  },

  backtest: {
    runUploaded: (data) => apiFetch('/api/backtest/run-upload', {method:'POST', headers:authHeaders(true), body:JSON.stringify(data)}).then(apiJson),
  },

  intelligence: {
    status: () => apiFetch('/api/intelligence/status', {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
    quota: () => apiFetch('/api/intelligence/quota', {headers:authHeaders(false), cache:'no-store'}).then(apiJson),
  },

  auth: {
    signup: (data) => apiFetch('/api/auth/signup', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)}).then(apiJson),
    login:  (data) => apiFetch('/api/auth/login',  {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)}).then(apiJson),
    oauthProviders: () => apiFetch('/api/auth/oauth/providers', {cache:'no-store'}).then(apiJson),
    me:     () => apiFetch('/api/auth/me', {headers: authHeaders(false)}).then(r => r.json()),
    logout: () => apiFetch('/api/auth/logout', {method:'POST', headers: authHeaders(false)}).then(r => r.json()),
    deleteAccount: (password) => apiFetch('/api/auth/account', {method:'DELETE', headers:authHeaders(true), body:JSON.stringify({password})}).then(apiJson),
    subscribe: (plan_code) => apiFetch('/api/auth/subscribe', {method:'POST', headers: authHeaders(true), body: JSON.stringify({plan_code})}).then(apiJson),
    providerCredentials: () => apiFetch('/api/auth/provider-credentials', {headers: authHeaders(false)}).then(apiJson),
  },

  watchlist: {
    get:    () => apiFetch('/api/watchlist/', {headers:authHeaders(false)}).then(apiJson),
    add:    (ticker) => apiFetch('/api/watchlist/add', {
      method:'POST', headers: authHeaders(true),
      body: JSON.stringify({ticker})
    }).then(r => r.json()),
    remove: (ticker) => apiFetch(`/api/watchlist/${ticker}`, {method:'DELETE', headers:authHeaders(false)}).then(apiJson),
  },

  dev: {
    modes: () => apiFetch('/api/dev/pattern-modes').then(apiJson),
    patternLab: (data) => apiFetch('/api/dev/pattern-lab/run', {method:'POST', headers: authHeaders(true), body: JSON.stringify(data)}).then(apiJson),
    patternLabStart: (data) => apiFetch('/api/dev/pattern-lab/start', {method:'POST', headers: authHeaders(true), body: JSON.stringify(data)}).then(apiJson),
    patternLabStatus: (jobId) => apiFetch(`/api/dev/pattern-lab/status/${encodeURIComponent(jobId)}`).then(apiJson),
    patternLabStop: (jobId) => apiFetch(`/api/dev/pattern-lab/stop/${encodeURIComponent(jobId)}`, {method:'POST', headers: authHeaders(false)}).then(apiJson),
    patternLabResume: (jobId) => apiFetch(`/api/dev/pattern-lab/resume/${encodeURIComponent(jobId)}`, {method:'POST', headers: authHeaders(false)}).then(apiJson),
    patternLabJobs: () => apiFetch('/api/dev/pattern-lab/jobs').then(apiJson),
    patternLabUniverse: (count, seed) => apiFetch(`/api/dev/pattern-lab/universe?count=${encodeURIComponent(count)}&seed=${encodeURIComponent(seed)}`).then(apiJson),
    cacheStatus: (tickers) => apiFetch('/api/dev/cache/status?tickers=' + encodeURIComponent((tickers || []).join(','))).then(apiJson),
    cacheWarmStart: (data) => apiFetch('/api/dev/cache/warm-start', {method:'POST', headers: authHeaders(true), body: JSON.stringify(data)}).then(apiJson),
    cacheWarmStatus: (jobId) => apiFetch('/api/dev/cache/warm-status/' + encodeURIComponent(jobId)).then(apiJson),
    vaiModelStatus: () => apiFetch('/api/dev/vai2/model/status').then(apiJson),
    vaiTrainStart: (data) => apiFetch('/api/dev/vai/train/start', {method:'POST', headers: authHeaders(true), body: JSON.stringify(data)}).then(apiJson),
    vaiTrainStatus: (jobId) => apiFetch('/api/dev/vai/train/status/' + encodeURIComponent(jobId)).then(apiJson),
  },

  paper: {
    getOpen:  () => apiFetch('/api/paper/trades', {headers: authHeaders(false)}).then(apiJson),
    getAll:   () => apiFetch('/api/paper/trades/all', {headers: authHeaders(false)}).then(apiJson),
    open:     (data) => apiFetch('/api/paper/open', {
      method:'POST', headers: authHeaders(true),
      body: JSON.stringify(data)
    }).then(apiJson),
    close:    (data) => apiFetch('/api/paper/close', {
      method:'POST', headers: authHeaders(true),
      body: JSON.stringify(data)
    }).then(apiJson),
    stats:    () => apiFetch('/api/paper/stats', {headers: authHeaders(false)}).then(apiJson),
  }
};


function sanitizeTickerSymbol(raw) {
  const value = String(raw || '').trim().toUpperCase();
  if (!value) return '';
  if (value.includes('@') || value.includes('://') || /\s/.test(value)) return '';
  return value.replace(/[^A-Z0-9.\-]/g, '').slice(0, 10);
}

function clearTickerIfAutofilledEmail() {
  const input = document.getElementById('tickerInput');
  if (!input) return false;
  const value = String(input.value || '');
  if (value.includes('@') || value.toLowerCase().includes('gmail.com') || value.toLowerCase().includes('yahoo.com') || value.toLowerCase().includes('outlook.com')) {
    input.value = '';
    input.placeholder = 'AAPL, TSLA, NVDA...';
    return true;
  }
  return false;
}

let currentAnalysis = null;
let tvWidget        = null;
let currentInterval = 'D';
let currentPeriod   = '6mo';
let currentTicker   = '';
let savedPatternMode = safeStorageGet('oryntra_pattern_engine_mode');
const PUBLIC_ENGINE_MODES = ['v8'];
if (!PUBLIC_ENGINE_MODES.includes(savedPatternMode)) {
  savedPatternMode = 'v8';
  safeStorageSet('oryntra_pattern_engine_mode', 'v8');
}
let currentPatternMode = PUBLIC_ANALYSIS_ENGINE;
let currentCacheWarmJobId = null;
let currentPatternLabJobId = null;
const PATTERN_LAB_ACTIVE_KEY = 'oryntra_pattern_lab_active_job';
const PATTERN_LAB_LAST_KEY = 'oryntra_pattern_lab_last_job';
let currentVAITrainingJobId = null;
const JOB_STARTING = '__starting__';
let lastPatternLabProgressPct = 0;
let lastPatternLabCompletedTickers = 0;
const DEFAULT_PATTERN_LAB_TICKERS = 'AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,AVGO,JPM,V,XOM,CVX,UNH,LLY,JNJ,WMT,COST,HD,MCD,NKE,CAT,BA,RTX,NEE,PLTR,CRWD,SPY,QQQ,SMH'.split(',');
const TRAINING_TICKERS_150 = 'AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,AVGO,JPM,V,XOM,CVX,UNH,LLY,JNJ,WMT,COST,HD,MCD,NKE,CAT,BA,RTX,NEE,PLTR,CRWD,SPY,QQQ,SMH,ORCL,NFLX,CRM,ADBE,INTC,MU,QCOM,TXN,AMAT,LRCX,KLAC,MRVL,NOW,SNOW,DDOG,NET,PANW,ZS,MDB,SHOP,UBER,ABNB,DASH,PYPL,COIN,HOOD,SOFI,SQ,MSTR,DELL,GS,MS,BAC,C,WFC,AXP,BLK,SCHW,COF,MA,BRK.B,PGR,TRV,AIG,USB,PNC,TFC,BK,ICE,CME,ABBV,MRK,PFE,TMO,DHR,ABT,ISRG,SYK,MDT,GILD,AMGN,REGN,VRTX,BMY,CVS,HUM,CI,ELV,ZBH,BSX,LOW,SBUX,TGT,TJX,ROST,LULU,CMG,YUM,KO,PEP,PG,CL,KMB,MDLZ,CAG,GIS,KR,DG,DLTR,EL,DE,GE,HON,UPS,FDX,LMT,NOC,GD,ETN,EMR,MMM,URI,CSX,NSC,UNP,DAL,UAL,AAL,LUV,RCL,COP,SLB,EOG,MPC,PSX,OXY,KMI,WMB,HAL,BKR,DUK,SO,AEP,EXC,SRE,XEL,D,PEG,ED,AWK'.split(',');

const PARTNER_AD_MARKUP = {
  native: '<!doctype html><html><body style="margin:0;overflow:hidden"><script async data-cfasync="false" src="https://pl31315343.profitableratecpmnetwork.com/b9eb4d7740a43b0752f4b754ad2da5f7/invoke.js"></script><div id="container-b9eb4d7740a43b0752f4b754ad2da5f7"></div></body></html>',
  desktop: '<!doctype html><html><body style="margin:0;overflow:hidden"><script>window.atOptions={key:"624025a8c48a0dc2913c48c298076d52",format:"iframe",height:90,width:728,params:{}};</script><script src="https://www.highrevenueformat.com/624025a8c48a0dc2913c48c298076d52/invoke.js"></script></body></html>',
  mobile: '<!doctype html><html><body style="margin:0;overflow:hidden"><script>window.atOptions={key:"83c7f4353a691c8b530dc86236b66ef1",format:"iframe",height:50,width:320,params:{}};</script><script src="https://www.highrevenueformat.com/83c7f4353a691c8b530dc86236b66ef1/invoke.js"></script></body></html>',
};

function clearPartnerAdvertisements() {
  const region = document.getElementById('partnerAdvertising');
  if (region) region.hidden = true;
  document.querySelectorAll('.partner-ad-frame').forEach((slot) => {
    slot.replaceChildren();
    slot.hidden = true;
    delete slot.dataset.loaded;
  });
}

function mountPartnerAdvertisement(slotId, markup) {
  const slot = document.getElementById(slotId);
  if (!slot || slot.dataset.loaded === 'true') return;
  const frame = document.createElement('iframe');
  frame.title = 'Advertisement';
  frame.loading = 'lazy';
  frame.referrerPolicy = 'no-referrer';
  frame.setAttribute('sandbox', 'allow-scripts allow-popups');
  frame.srcdoc = markup;
  slot.append(frame);
  slot.dataset.loaded = 'true';
}

function initPartnerAdvertisements() {
  clearPartnerAdvertisements();
  if (!currentUser || !runtimeCapabilities.partner_ads?.enabled) return;
  const placements = runtimeCapabilities.partner_ads?.placements || {};
  const region = document.getElementById('partnerAdvertising');
  const enabledSlots = [
    ['native', 'partnerNativeAd', PARTNER_AD_MARKUP.native],
    ['desktop', 'partnerDesktopAd', PARTNER_AD_MARKUP.desktop],
    ['mobile', 'partnerMobileAd', PARTNER_AD_MARKUP.mobile],
  ].filter(([placement]) => Boolean(placements[placement]));
  enabledSlots.forEach(([, slotId, markup]) => {
    const slot = document.getElementById(slotId);
    if (slot) slot.hidden = false;
    mountPartnerAdvertisement(slotId, markup);
  });
  if (region) region.hidden = enabledSlots.length === 0;
}


document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  initAnalysisAccess();
  runtimeReadyPromise = initRuntimeCapabilities();
  initTabs();
  initQuantLab();
  initPortfolioLab();
  initScanner();
  initWatchlist();
  initPaperTrades();
  initModal();
  loadSearchCounter();
  initDevTools();
  initSettingsPage();
  initOwnerDebugMenu();
  initAccessibility();
});

function visibleDialog() {
  return Array.from(document.querySelectorAll('.modal-overlay[role="dialog"]'))
    .filter(modal => window.getComputedStyle(modal).display !== 'none')
    .at(-1) || null;
}

function openAccessibleDialog(modal, preferredFocus) {
  if (!modal) return;
  if (window.getComputedStyle(modal).display === 'none') lastDialogFocus = document.activeElement;
  modal.style.display = 'flex';
  const target = preferredFocus || modal.querySelector('input, select, button, [tabindex]:not([tabindex="-1"])');
  window.setTimeout(() => target?.focus(), 0);
}

function closeAccessibleDialog(modal) {
  if (!modal) return;
  modal.style.display = 'none';
  const target = lastDialogFocus;
  lastDialogFocus = null;
  window.setTimeout(() => target?.focus(), 0);
}

function initAccessibility() {
  document.addEventListener('keydown', event => {
    const modal = visibleDialog();
    if (modal) {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (modal.id === 'authModal') closeAuthModal();
        if (modal.id === 'paperModal') closeModal();
        if (modal.id === 'subscriptionModal') closeSubscriptionModal();
        if (modal.id === 'ownerDebugModal') closeAccessibleDialog(modal);
        return;
      }
      if (event.key === 'Tab') {
        const focusable = Array.from(modal.querySelectorAll(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )).filter(element => window.getComputedStyle(element).display !== 'none');
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
      return;
    }
    const panel = document.getElementById('devLabPanel');
    if (event.key === 'Escape' && panel && !panel.classList.contains('dev-hidden')) {
      event.preventDefault();
      panel.classList.add('dev-hidden');
      document.querySelector('.beta-version-badge, .beta-badge')?.setAttribute('aria-expanded', 'false');
      lastDevLabFocus?.focus();
      lastDevLabFocus = null;
    }
  });
}

function mountOwnerDebugMenu() {
  if (document.getElementById('ownerDebugModal')) return;
  document.body.insertAdjacentHTML('beforeend', `<div class="modal-overlay" id="ownerDebugModal" style="display:none" role="dialog" aria-modal="true" aria-labelledby="ownerDebugTitle"><div class="modal-box owner-debug-box"><button class="oryntra-auth-close" id="ownerDebugClose" type="button" aria-label="Close debug menu">×</button><p class="eyebrow">Owner diagnostic controls</p><h2 id="ownerDebugTitle">Workspace entitlement</h2><p class="auth-modal-subtitle">This changes only the signed-in account’s owner test entitlement. It does not create a payment, App Store purchase, or customer subscription.</p><label>Subscription test state<select id="ownerDebugPlan"><option value="actual">Use actual billing state</option><option value="base">Base</option><option value="pro">Oryntra Pro</option><option value="max_bundle">Max Bundle</option></select></label><p id="ownerDebugStatus" class="form-footnote">Checking access…</p><button class="button button-primary" id="ownerDebugApply" type="button">Apply test entitlement</button></div></div>`);
  document.getElementById('ownerDebugClose')?.addEventListener('click', () => closeAccessibleDialog(document.getElementById('ownerDebugModal')));
  document.getElementById('ownerDebugApply')?.addEventListener('click', applyOwnerDebugPlan);
}

function ensureOwnerDebugButton() {
  let button = document.getElementById('ownerDebugButton');
  if (button) return button;
  const anchor = document.querySelector('.sidebar-bottom .sidebar-version, .sidebar-bottom .sidebar-meta');
  if (!anchor) return null;
  button = document.createElement('button');
  button.id = 'ownerDebugButton';
  button.type = 'button';
  button.hidden = true;
  button.className = 'nav-item';
  button.title = 'Owner controls · Command-Shift-O';
  button.textContent = '◆ Owner controls';
  anchor.insertAdjacentElement('beforebegin', button);
  return button;
}

async function openOwnerDebugMenu() {
  if (!currentUser) return;
  try {
    const access = await API.debug.access();
    if (!access?.enabled) return;
    mountOwnerDebugMenu();
    const plan = String(access.test_override?.plan_code || 'actual').toLowerCase();
    const select = document.getElementById('ownerDebugPlan');
    if (select) select.value = ['base', 'pro', 'max_bundle'].includes(plan) ? plan : 'actual';
    const status = document.getElementById('ownerDebugStatus');
    if (status) status.textContent = access.test_override ? `Current test state: ${access.test_override.plan_name}.` : 'Using actual billing state.';
    openAccessibleDialog(document.getElementById('ownerDebugModal'), select);
  } catch (_) {
    // A normal account should not be able to distinguish this from no menu.
  }
}

async function applyOwnerDebugPlan() {
  const select = document.getElementById('ownerDebugPlan');
  const status = document.getElementById('ownerDebugStatus');
  const button = document.getElementById('ownerDebugApply');
  if (!select || !button) return;
  button.disabled = true;
  try {
    const response = await API.debug.subscription(select.value);
    currentUser = {...currentUser, subscription: response.subscription || null};
    storeCachedAuthUser(currentUser);
    setAuthUI(currentUser);
    if (status) status.textContent = response.test_override ? `Test state applied: ${response.test_override.plan_name}.` : 'Actual billing state restored.';
    refreshAnalysisAccess({force:true, silent:true}).catch(() => {});
  } catch (_) {
    if (status) status.textContent = 'The debug entitlement could not be changed.';
  } finally {
    button.disabled = false;
  }
}

function initOwnerDebugMenu() {
  ensureOwnerDebugButton()?.addEventListener('click', openOwnerDebugMenu);
  document.addEventListener('keydown', event => {
    const macShortcut = event.metaKey && event.shiftKey && event.code === 'KeyO';
    const legacyShortcut = event.ctrlKey && event.shiftKey && event.altKey && event.code === 'KeyO';
    if (macShortcut || legacyShortcut) {
      event.preventDefault();
      openOwnerDebugMenu();
    }
  });
}

async function refreshOwnerDebugAvailability() {
  const button = ensureOwnerDebugButton();
  if (!button) return;
  button.hidden = true;
  if (!currentUser) return;
  try {
    const access = await API.debug.access();
    button.hidden = !access?.enabled;
  } catch (_) {
    button.hidden = true;
  }
}


function mountIdentityProviderOptions() {
  const form = document.getElementById('authForm');
  if (!form || document.getElementById('oauthProviderOptions')) return;
  form.insertAdjacentHTML('afterend', `<div id="oauthProviderOptions" class="oauth-provider-options"><div class="auth-divider"><span>or continue with</span></div><div class="oauth-provider-buttons" aria-label="Continue with an identity provider"><button class="oauth-provider-button" data-oauth-provider="google" type="button" disabled><span class="oauth-provider-mark oauth-google-mark" aria-hidden="true">G</span>Continue with Google</button><button class="oauth-provider-button" data-oauth-provider="apple" type="button" disabled><span class="oauth-provider-mark oauth-apple-mark" aria-hidden="true">●</span>Continue with Apple</button></div><p id="oauthProviderStatus" class="oauth-provider-status" role="status">Checking sign-in options…</p></div>`);
}

function mountQuantModelCatalog() {
  const options = [
    ['v8_official', 'V8 Official · Free'],
    ['minerva_baseline', 'Minerva baseline · Pro / Max'],
  ];
  ['quantModel', 'settingsQuantModel'].forEach(id => {
    const select = document.getElementById(id);
    if (!select) return;
    const selected = options.some(([value]) => value === select.value) ? select.value : 'v8_official';
    const current = Array.from(select.options).map(option => option.value);
    if (current.length !== options.length || current.some((value, index) => value !== options[index][0])) {
      select.innerHTML = options.map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join('');
    }
    select.value = selected;
  });
}

function mountSubscriptionStructure() {
  const modal = document.getElementById('subscriptionModal');
  if (!modal || modal.querySelector('.subscription-shell')) return;
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'subscriptionTitle');
  const features = [
    ['V8 scanner & evidence cards', 'Included', 'Included', 'Included'],
    ['Minerva baseline Quant Lab', '—', 'Included', 'Included'],
    ['Daily scanner reviews', '10 / day', '200 / day', 'Unlimited'],
    ['Watchlist & paper journal', '20 symbols', 'Unlimited', 'Unlimited'],
    ['Historical research demonstrations', 'Included', 'Included', 'Included'],
    ['Frozen public Quant profile', 'Included', 'Included', 'Included'],
    ['Portfolio Lab research ledger', '—', 'Included', 'Included'],
    ['Saved research presets & exports', '✕', 'Included', 'Included'],
    ['Rule Mirror Pro', '✕', '✕', 'Included'],
  ];
  const rows = features.map(([feature, base, pro, max]) => `<tr><th scope="row">${escapeHtml(feature)}</th><td>${escapeHtml(base)}</td><td>${escapeHtml(pro)}</td><td>${escapeHtml(max)}</td></tr>`).join('');
  modal.innerHTML = `<div class="modal-box subscription-shell"><button class="oryntra-auth-close" id="subscriptionClose" type="button" aria-label="Close subscription page">×</button><p class="eyebrow">Oryntra membership</p><h2 id="subscriptionTitle">Research software access.</h2><p id="subscriptionCopy" class="auth-modal-subtitle">Subscriptions are temporarily unavailable while checkout is under maintenance. Any future subscription provides software access only—not CQC equity, profits, trading participation, or a managed strategy.</p><button class="button button-primary subscription-free-continue" id="subscriptionFreeContinue" type="button">Continue with free V8</button><div class="subscription-plan-headings"><section><span>BASE</span><b>Research essentials</b><small>V8 scanner and bounded historical research.</small><strong class="subscription-current-plan">Current free workspace</strong></section><section><span>PRO</span><b>Oryntra Pro</b><small>Expanded workspace capacity and validation-gated candidate updates.</small><strong class="subscription-price">$6.99 / month</strong><button class="button button-primary subscription-buy" data-subscription-maintenance type="button">Buy Pro</button></section><section><span>MAX BUNDLE</span><b>Oryntra Pro + Rule Mirror Pro</b><small>Expanded access across both products.</small><strong class="subscription-price">$10.99 / month</strong><button class="button button-secondary subscription-buy" data-subscription-maintenance type="button">Buy Max Bundle</button></section></div><p class="subscription-appstore-note">$1 cheaper per month if bought through App Store.</p><div class="subscription-compare-scroll"><table class="subscription-compare"><thead><tr><th>Feature</th><th>Base</th><th>Pro</th><th>Max bundle</th></tr></thead><tbody>${rows}</tbody></table></div><p class="subscription-footnote">Research candidates remain gated by validation and are never unlocked merely by payment.</p></div>`;
}

function estCalendarDate() {
  try {
    return new Intl.DateTimeFormat('en-CA', {timeZone:'America/New_York', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
  } catch (_) {
    return new Date().toISOString().slice(0, 10);
  }
}

function showDailyScannerOffer(user, intent) {
  if (!runtimeCapabilities.subscription_offers_enabled || !user?.id || intent?.type !== 'scan') return false;
  const key = `oryntra_subscription_offer_seen_${user.id}_${estCalendarDate()}`;
  try {
    if (localStorage.getItem(key)) return false;
    localStorage.setItem(key, '1');
  } catch (_) {}
  pendingAnalysisIntent = intent;
  showSubscriptionModal('daily_offer');
  return true;
}

function mountMinervaModelGate() {
  ['quantModel', 'settingsQuantModel'].forEach(id => {
    const select = document.getElementById(id);
    if (!select || select.dataset.minervaGateMounted) return;
    if (!select.querySelector('option[value="minerva_v1"]')) {
      const option = new Option('Minerva · Pro research preview', 'minerva_v1');
      option.dataset.access = 'subscription';
      select.append(option);
    }
    select.dataset.minervaGateMounted = 'true';
    let lastAvailable = select.value;
    select.addEventListener('change', () => {
      if (select.value !== 'minerva_v1') {
        lastAvailable = select.value;
        return;
      }
      select.value = lastAvailable;
      if (!hasActiveSubscription()) {
        showSubscriptionModal('minerva_v1');
        return;
      }
      showSubscriptionModal('minerva_unreleased');
    });
  });
}

const WORKSPACE_MODEL_KEY = 'oryntra_workspace_model';
const WORKSPACE_MODELS = [
  ['v8', 'V8 Official · Scanner · Free'],
  ['minerva_baseline', 'Minerva baseline · Quant Lab · Pro / Max'],
];

function workspaceModel() {
  const value = safeStorageGet(WORKSPACE_MODEL_KEY) || 'v8';
  return WORKSPACE_MODELS.some(([id]) => id === value) ? value : 'v8';
}

function syncWorkspaceModelControls(value) {
  document.querySelectorAll('[data-workspace-model]').forEach(select => { select.value = value; });
}

function chooseWorkspaceModel(value) {
  if (value === 'minerva_baseline') {
    if (!hasActiveSubscription()) {
      syncWorkspaceModelControls(workspaceModel());
      showSubscriptionModal('minerva_v1');
      return;
    }
    safeStorageSet(WORKSPACE_MODEL_KEY, value);
    currentPatternMode = value;
    // V8 remains the single-ticker scanner engine. Minerva has a frozen,
    // multi-symbol Quant Lab implementation, so do not substitute it into a
    // scanner result or present a cosmetic selector.
    const quantModel = document.getElementById('quantModel');
    if (quantModel) quantModel.value = 'minerva_baseline';
    const settingsModel = document.getElementById('settingsQuantModel');
    if (settingsModel) settingsModel.value = 'minerva_baseline';
    safeStorageSet('oryntra_quant_model', 'minerva_baseline');
    syncWorkspaceModelControls(value);
    updatePatternModePill();
    updateSettingsEngineDisplay();
    document.querySelector('[data-tab="quant"]')?.click();
    return;
  }
  safeStorageSet(WORKSPACE_MODEL_KEY, value);
  currentPatternMode = value;
  safeStorageSet('oryntra_pattern_engine_mode', value);
  syncWorkspaceModelControls(value);
  updatePatternModePill();
  updateSettingsEngineDisplay();
}

function hasActiveSubscription(user=currentUser) {
  const plan = String(user?.subscription?.plan_code || '').trim().toLowerCase();
  return Boolean(plan && !['base', 'free'].includes(plan));
}

function mountWorkspaceModelControls() {
  currentPatternMode = workspaceModel();
  document.querySelectorAll('.tab-panel > .page-header').forEach(header => {
    if (header.parentElement?.id === 'tab-portfolio') return;
    if (header.querySelector('[data-workspace-model]')) return;
    const select = document.createElement('select');
    select.dataset.workspaceModel = 'true';
    select.setAttribute('aria-label', 'Workspace research model');
    select.innerHTML = WORKSPACE_MODELS.map(([id, label]) => `<option value="${id}">${escapeHtml(label)}</option>`).join('');
    select.value = workspaceModel();
    select.addEventListener('change', () => chooseWorkspaceModel(select.value));
    const control = document.createElement('label');
    control.className = 'workspace-model-control';
    control.innerHTML = '<span>Research engine</span>';
    control.append(select);
    header.append(control);
  });
  syncWorkspaceModelControls(workspaceModel());
}

async function refreshIdentityProviderOptions() {
  const status = document.getElementById('oauthProviderStatus');
  const buttons = Array.from(document.querySelectorAll('[data-oauth-provider]'));
  if (!buttons.length) return;
  try {
    const result = await API.auth.oauthProviders();
    const enabled = result?.providers || {};
    buttons.forEach(button => {
      const provider = button.dataset.oauthProvider;
      button.disabled = !enabled?.[provider]?.enabled;
      button.title = button.disabled ? `${provider[0].toUpperCase()}${provider.slice(1)} sign-in is being configured.` : '';
      button.onclick = () => beginIdentityProviderSignIn(provider);
    });
    if (status) status.textContent = buttons.some(button => !button.disabled) ? 'Use the same verified email to link an existing password account.' : 'Google and Apple sign-in will become available after their secure provider credentials are configured.';
  } catch (_) {
    buttons.forEach(button => { button.disabled = true; });
    if (status) status.textContent = 'Sign-in options are temporarily unavailable.';
  }
}

function beginIdentityProviderSignIn(provider) {
  const error = document.getElementById('authError');
  const acceptLegal = Boolean(document.getElementById('authLegalAccept')?.checked);
  if (authMode === 'signup' && !acceptLegal) {
    if (error) error.textContent = 'Accept the Terms, Privacy Policy, and research-only disclosure before creating an account with an identity provider.';
    return;
  }
  window.location.assign(`/api/auth/oauth/${encodeURIComponent(provider)}/start?intent=${encodeURIComponent(authMode)}&accept_legal=${acceptLegal ? 'true' : 'false'}`);
}

function initAuth() {
  mountIdentityProviderOptions();
  mountQuantModelCatalog();
  mountWorkspaceModelControls();
  mountSubscriptionStructure();
  refreshIdentityProviderOptions();
  const authBtn = document.getElementById('authOpenBtn');
  if (authBtn) authBtn.addEventListener('click', () => currentUser ? logoutUser() : openAuthModal('login'));

  const cancel = document.getElementById('authCancel');
  if (cancel) cancel.addEventListener('click', closeAuthModal);
  const submit = document.getElementById('authSubmit');
  const form = document.getElementById('authForm');
  if (form) form.addEventListener('submit', event => { event.preventDefault(); submitAuth(); });
  else if (submit) submit.addEventListener('click', submitAuth);

  document.querySelectorAll('.auth-tab').forEach(btn => {
    btn.addEventListener('click', () => openAuthModal(btn.dataset.mode || 'login'));
  });
  document.getElementById('authModeSwitch')?.addEventListener('click', () => openAuthModal(authMode === 'signup' ? 'login' : 'signup'));
  document.getElementById('saveOnboardingPolygonKey')?.addEventListener('click', () => saveOnboardingProviderKey('polygon'));
  document.getElementById('saveOnboardingTwelvedataKey')?.addEventListener('click', () => saveOnboardingProviderKey('twelvedata'));
  document.getElementById('editProviderKeys')?.addEventListener('click', closeProviderSavedChoice);
  document.getElementById('continueToOryntra')?.addEventListener('click', () => {
    closeProviderSavedChoice();
    closeProviderOnboarding();
    resumePendingAnalysisIntent();
  });
  document.getElementById('providerOnboardingLogout')?.addEventListener('click', async () => { closeProviderOnboarding(); await logoutUser(); openAuthModal('login'); });

  const subClose = document.getElementById('subscriptionClose');
  if (subClose) subClose.addEventListener('click', closeSubscriptionModal);
  document.getElementById('subscriptionFreeContinue')?.addEventListener('click', closeSubscriptionModal);
  document.getElementById('subscriptionModal')?.addEventListener('click', event => {
    if (event.target === event.currentTarget) closeSubscriptionModal();
  });
  const betaPreviewAuthBtn = document.getElementById('betaPreviewAuthBtn');
  if (betaPreviewAuthBtn) betaPreviewAuthBtn.addEventListener('click', () => openAuthModal(currentUser ? 'login' : 'signup'));
  document.querySelectorAll('[data-subscription-maintenance]').forEach(btn => {
    btn.addEventListener('click', () => {
      const copy = document.getElementById('subscriptionCopy');
      if (copy) copy.textContent = 'Subscriptions under maintenance. Check back again later.';
    });
  });
  document.querySelectorAll('.plan-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!currentUser) {
        closeSubscriptionModal();
        openAuthModal('signup');
        return;
      }
      try {
        const res = await API.auth.subscribe(btn.dataset.plan);
        applyAuthResponse(res);
        closeSubscriptionModal();
        showError('Plan activated for testing. Replace this mock unlock with Stripe before real paid launch.');
        setTimeout(hideError, 3500);
      } catch (e) {
        showError(String(e));
      }
    });
  });

  // Do not reveal the workspace based on browser cache.  The API session must
  // verify first so a logged-out visitor cannot access even a transient shell.
  setAuthUI(null);
  refreshAuthState();
}

function loadCachedAuthUser() {
  try {
    const raw = safeStorageGet(AUTH_USER_KEY) || getCookie(AUTH_USER_COOKIE);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function storeCachedAuthUser(user) {
  try {
    if (user) {
      const packed = JSON.stringify(user);
      safeStorageSet(AUTH_USER_KEY, packed);
      setClientCookie(AUTH_USER_COOKIE, packed, 30);
    } else {
      safeStorageRemove(AUTH_USER_KEY);
      deleteClientCookie(AUTH_USER_COOKIE);
    }
  } catch (_) {}
}

async function refreshAuthState() {
  try {
    const res = await API.auth.me();
    if (res && res.authenticated) {
      currentUser = res.user;
      storeCachedAuthUser(currentUser);
      setAuthUI(currentUser);
      loadPersistedProviderKeys().then(() => refreshProviderCredentialSettings()).catch(() => {});
      refreshAnalysisAccess({silent:true}).catch(() => {});
      return;
    }
    authToken = '';
    safeStorageRemove(AUTH_TOKEN_KEY);
    deleteClientCookie(AUTH_TOKEN_COOKIE);
    storeCachedAuthUser(null);
    setAuthUI(null);
  } catch (_) {
    setAuthUI(null);
  }
}

function setAuthUI(user) {
  currentUser = user || null;
  const signedIn = Boolean(currentUser);
  document.body.classList.toggle('auth-gated', !signedIn);
  document.querySelector('.app-shell')?.setAttribute('aria-hidden', String(!signedIn));
  const state = document.getElementById('authStateText');
  const btn = document.getElementById('authOpenBtn');
  if (!user) {
    clearPartnerAdvertisements();
    refreshOwnerDebugAvailability();
    if (state) state.textContent = 'SIGNED OUT';
    if (btn) btn.textContent = 'LOGIN';
    analysisAccessState = {ready:false, policy:null, quota:null};
    renderAnalysisAccess();
    renderPortfolioLabAccess();
    openAuthModal('login');
    return;
  }
  const plan = user.subscription ? (user.subscription.plan_name || user.subscription.plan_code || 'ACTIVE') : 'FREE';
  if (state) state.textContent = `${user.email} · ${plan}`;
  if (btn) btn.textContent = 'LOGOUT';
  const workspaceName = document.getElementById('workspaceName');
  const workspaceSubtitle = document.getElementById('workspaceSubtitle');
  if (workspaceName) workspaceName.textContent = user.display_name || user.email;
  if (workspaceSubtitle) workspaceSubtitle.textContent = 'Private research workspace';
  closeAuthModal();
  initPartnerAdvertisements();
  refreshOwnerDebugAvailability();
  renderAnalysisAccess();
  renderPortfolioLabAccess();
}

function openAuthModal(mode='login') {
  authMode = mode;
  const modal = document.getElementById('authModal');
  if (!modal) return;
  document.querySelectorAll('.auth-tab').forEach(b => {
    const selected = b.dataset.mode === mode;
    b.classList.toggle('active', selected);
    b.setAttribute('aria-selected', String(selected));
  });
  const nameField = document.getElementById('authNameField');
  const legalField = document.getElementById('authLegalField');
  if (nameField) nameField.style.display = mode === 'signup' ? '' : 'none';
  if (legalField) legalField.style.display = mode === 'signup' ? '' : 'none';
  const legalAccept = document.getElementById('authLegalAccept');
  if (legalAccept && mode !== 'signup') legalAccept.checked = false;
  const submit = document.getElementById('authSubmit');
  const kicker = document.getElementById('authModalKicker');
  const title = document.getElementById('authModalTitle');
  const subtitle = document.getElementById('authModalSubtitle');
  const switcher = document.getElementById('authModeSwitch');
  const password = document.getElementById('authPassword');
  if (kicker) kicker.textContent = mode === 'signup' ? 'Create your research workspace' : 'Welcome back';
  if (title) title.textContent = mode === 'signup' ? 'Start with your evidence.' : 'Sign in to continue.';
  if (subtitle) subtitle.textContent = mode === 'signup' ? 'Create an account, then connect one provider key directly from this browser.' : 'Your saved research stays tied to your account; provider keys stay on this browser.';
  if (submit) submit.innerHTML = mode === 'signup' ? 'Create account <span>→</span>' : 'Sign in <span>→</span>';
  if (switcher) switcher.textContent = mode === 'signup' ? 'Already have an account? Sign in' : 'New here? Create an account';
  if (password) password.autocomplete = mode === 'signup' ? 'new-password' : 'current-password';
  const err = document.getElementById('authError');
  if (err) err.textContent = '';
  mountTurnstile();
  openAccessibleDialog(
    modal,
    mode === 'signup' ? document.getElementById('authName') : document.getElementById('authEmail')
  );
}

function mountTurnstile() {
  const form = document.getElementById('authForm');
  const existing = document.getElementById('turnstileAuthSlot');
  if (!runtimeCapabilities.turnstile?.enabled || !runtimeCapabilities.turnstile?.site_key) {
    existing?.remove();
    return;
  }
  if (!form || existing) return;
  const slot = document.createElement('div');
  slot.id = 'turnstileAuthSlot';
  slot.className = 'turnstile-slot';
  const error = document.getElementById('authError');
  form.insertBefore(slot, error || null);
  const render = () => {
    if (!window.turnstile || slot.dataset.rendered) return;
    window.turnstile.render(slot, {sitekey: runtimeCapabilities.turnstile.site_key, theme: 'auto'});
    slot.dataset.rendered = 'true';
  };
  render();
  if (!slot.dataset.rendered) window.setTimeout(render, 300);
}

function closeAuthModal() {
  if (!currentUser) return;
  const modal = document.getElementById('authModal');
  closeAccessibleDialog(modal);
}

function setProviderOnboardingMessage(message, warning=false) {
  const target = document.getElementById('providerOnboardingMessage');
  if (!target) return;
  target.textContent = message || '';
  target.classList.toggle('is-warning', Boolean(warning));
}

function hasBrowserProviderKey(preferred = 'auto') {
  if (preferred && preferred !== 'auto' && preferred !== 'cache_only') return Boolean(browserProviderKeys[preferred]);
  return Boolean(browserProviderKeys.polygon || browserProviderKeys.twelvedata);
}

async function openProviderOnboarding() {
  const modal = document.getElementById('providerOnboardingModal');
  if (!modal || !currentUser) return;
  setProviderOnboardingMessage('Choose a provider and save its key on this browser device.');
  openAccessibleDialog(modal, document.getElementById('onboardingPolygonApiKey'));
  if (hasBrowserProviderKey()) {
    closeProviderOnboarding();
    resumePendingAnalysisIntent();
  }
}

function closeProviderOnboarding() {
  closeAccessibleDialog(document.getElementById('providerOnboardingModal'));
}

function openProviderSavedChoice(provider) {
  const modal = document.getElementById('providerKeySavedModal');
  const copy = document.getElementById('providerKeySavedCopy');
  if (copy) copy.textContent = `Your ${provider === 'polygon' ? 'Massive' : 'Twelve Data'} key is saved only on this browser device and is sent directly to that provider. Oryntra never receives or stores it.`;
  openAccessibleDialog(modal, document.getElementById('continueToOryntra'));
}

function closeProviderSavedChoice() {
  closeAccessibleDialog(document.getElementById('providerKeySavedModal'));
}

async function saveOnboardingProviderKey(provider) {
  if (!currentUser) { openAuthModal('login'); return; }
  const input = document.getElementById(provider === 'polygon' ? 'onboardingPolygonApiKey' : 'onboardingTwelvedataApiKey');
  const apiKey = input?.value || '';
  if (!apiKey.trim()) {
    setProviderOnboardingMessage(`Paste your ${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} API key first.`, true);
    return;
  }
  const previous = browserProviderKeys[provider];
  browserProviderKeys[provider] = apiKey.trim();
  try {
    setProviderOnboardingMessage(`Verifying ${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} with a completed SPY daily candle…`);
    await fetchDirectMarketBars('SPY', '1y', provider, 1);
    await persistBrowserProviderKey(provider, browserProviderKeys[provider]);
  } catch (error) {
    browserProviderKeys[provider] = previous;
    setProviderOnboardingMessage(error.message || 'The provider could not return a completed SPY daily OHLCV candle for this key.', true);
    return;
  }
  if (input) input.value = '';
  renderProviderCredentialSettings();
  setProviderOnboardingMessage('Key connected on this browser only. Choose what you want to do next.');
  openProviderSavedChoice(provider);
}

async function requireProviderKey(intent = null, preferred = 'auto') {
  if (!currentUser) return false;
  await loadPersistedProviderKeys();
  if (hasBrowserProviderKey(preferred)) return true;
  pendingAnalysisIntent = intent || pendingAnalysisIntent;
  providerOnboardingRequest = intent;
  openProviderOnboarding();
  return false;
}

function directProviderFor(preferred = 'auto') {
  if (preferred && preferred !== 'auto' && preferred !== 'cache_only' && browserProviderKeys[preferred]) return preferred;
  if (browserProviderKeys.polygon) return 'polygon';
  if (browserProviderKeys.twelvedata) return 'twelvedata';
  throw new Error('Connect a Polygon / Massive or Twelve Data key in this browser first.');
}

function providerDateRange(period = '6mo') {
  const now = new Date();
  const from = new Date(now);
  // The scanner uses 320 completed daily bars for long moving averages and
  // pattern context, so short display windows still fetch a sufficient daily
  // calculation history (weekends and market holidays reduce bar count).
  const days = { '1mo': 540, '6mo': 540, '1y': 540, '2y': 780, '5y': 1900, all: 3650, '5m': 540 }[period] || 540;
  from.setUTCDate(from.getUTCDate() - days);
  const date = value => value.toISOString().slice(0, 10);
  return {from: date(from), to: date(now)};
}

function normalizeDirectBars(rows, provider, minimumBars = 120) {
  const normalized = (rows || []).map((row) => {
    if (provider === 'polygon') {
      return {timestamp: new Date(Number(row.t)).toISOString(), open:Number(row.o), high:Number(row.h), low:Number(row.l), close:Number(row.c), volume:Number(row.v || 0)};
    }
    return {timestamp: row.datetime || row.timestamp, open:Number(row.open), high:Number(row.high), low:Number(row.low), close:Number(row.close), volume:Number(row.volume || 0)};
  }).filter(bar => bar.timestamp && [bar.open, bar.high, bar.low, bar.close, bar.volume].every(Number.isFinite));
  if (normalized.length < minimumBars) throw new Error(`The provider returned fewer than ${minimumBars} completed daily bars. Choose a longer window or verify your provider plan.`);
  return normalized.slice(-2000);
}

async function fetchDirectMarketBars(ticker, period = '6mo', preferred = 'auto', minimumBars = 120) {
  const provider = directProviderFor(preferred);
  const key = browserProviderKeys[provider];
  const range = providerDateRange(period);
  let endpoint;
  if (provider === 'polygon') {
    endpoint = `https://api.polygon.io/v2/aggs/ticker/${encodeURIComponent(ticker)}/range/1/day/${range.from}/${range.to}?adjusted=true&sort=asc&limit=50000&apiKey=${encodeURIComponent(key)}`;
  } else {
    endpoint = `https://api.twelvedata.com/time_series?symbol=${encodeURIComponent(ticker)}&interval=1day&start_date=${range.from}&end_date=${range.to}&outputsize=5000&apikey=${encodeURIComponent(key)}`;
  }
  let response;
  try {
    response = await nativeFetch(endpoint, {method:'GET', mode:'cors', credentials:'omit', cache:'no-store'});
  } catch (_) {
    throw new Error(`${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} did not allow this browser request. Check the key, provider plan, and browser CORS support.`);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.status === 'error' || payload?.code) {
    throw new Error(payload?.message || payload?.error || `${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} rejected the market-data request.`);
  }
  const bars = normalizeDirectBars(provider === 'polygon' ? payload.results : payload.values, provider, minimumBars);
  return {provider, bars};
}

async function submitAuth() {
  await (runtimeReadyPromise || Promise.resolve());
  const err = document.getElementById('authError');
  if (err) err.textContent = '';
  const email = (document.getElementById('authEmail')?.value || '').trim();
  const password = document.getElementById('authPassword')?.value || '';
  const display_name = (document.getElementById('authName')?.value || '').trim();
  const accept_legal = Boolean(document.getElementById('authLegalAccept')?.checked);
  const captcha_token = document.querySelector('[name="cf-turnstile-response"]')?.value || '';
  if (authMode === 'signup' && !accept_legal) {
    if (err) err.textContent = 'Accept the Terms, Privacy Policy, and research-only disclosure to create an account.';
    return;
  }
  if (runtimeCapabilities.turnstile?.enabled && !captcha_token) {
    if (err) err.textContent = 'Complete the security check before continuing.';
    return;
  }
  try {
    const completedMode = authMode;
    const res = authMode === 'signup'
      ? await API.auth.signup({email, password, display_name, accept_legal, captcha_token})
      : await API.auth.login({email, password, captcha_token});
    const openedDailyOffer = applyAuthResponse(res, {resume: completedMode !== 'signup'});
    closeAuthModal();
    if (completedMode === 'signup' && !openedDailyOffer) openProviderOnboarding();
  } catch (e) {
    if (err) err.textContent = String(e);
  }
}

function applyAuthResponse(res, {resume=true} = {}) {
  if (res.token) {
    authToken = res.token;
    safeStorageSet(AUTH_TOKEN_KEY, authToken);
    setClientCookie(AUTH_TOKEN_COOKIE, authToken, 30);
  } else {
    authToken = authToken || safeStorageGet(AUTH_TOKEN_KEY) || getCookie(AUTH_TOKEN_COOKIE) || '';
  }
  const user = res.user || null;
  storeCachedAuthUser(user);
  setAuthUI(user);
  loadUniversalBlueprint();
  loadPersistedProviderKeys().then(() => refreshProviderCredentialSettings()).catch(() => {});
  clearTickerIfAutofilledEmail();
  if (document.querySelector('#tab-paper.active')) {
    loadPaperTrades().catch(() => {});
  }
  if (resume) {
    refreshAnalysisAccess({silent:true})
      .then(() => resumePendingAnalysisIntent())
      .catch(() => resumePendingAnalysisIntent());
  }
  refreshProviderCredentialSettings().catch(() => {});
  return false;
}

async function logoutUser() {
  try { await API.auth.logout(); } catch (_) {}
  authToken = '';
  safeStorageRemove(AUTH_TOKEN_KEY);
  safeStorageRemove(AUTH_USER_KEY);
  deleteClientCookie(AUTH_TOKEN_COOKIE);
  deleteClientCookie(AUTH_USER_COOKIE);
  pendingAnalysisIntent = null;
  providerOnboardingRequest = null;
  browserProviderKeys = {polygon: '', twelvedata: ''};
  providerKeyLoadOwner = '';
  setAuthUI(null);
  refreshProviderCredentialSettings().catch(() => {});
  loadPaperTrades().catch(() => {});
}

function showSubscriptionModal(context='membership') {
  const modal = document.getElementById('subscriptionModal');
  if (!modal) return;
  subscriptionModalContext = context;
  const title = document.getElementById('subscriptionTitle');
  const copy = document.getElementById('subscriptionCopy');
  if (context === 'minerva_v1') {
    if (title) title.textContent = 'Minerva access';
    if (copy) copy.textContent = 'Minerva is reserved for active subscribers. Membership and checkout will be configured here after the subscription experience is designed.';
  } else if (context === 'minerva_unreleased') {
    if (title) title.textContent = 'Minerva validation in progress';
    if (copy) copy.textContent = 'Your subscription is recognized. Minerva is still a private research candidate, so it cannot run in the workspace until its validation and release gate is complete.';
  } else if (context === 'daily_offer') {
    if (title) title.textContent = 'V8 is ready to scan.';
    if (copy) copy.textContent = 'V8 remains free. You can close this comparison or continue below and your scan will start—no plan selection required.';
  } else {
    if (title) title.textContent = 'Choose the research workspace.';
    if (copy) copy.textContent = 'Compare the workspaces below. Subscriptions are temporarily unavailable while checkout is under maintenance.';
  }
  openAccessibleDialog(modal, document.getElementById('subscriptionClose'));
}

function closeSubscriptionModal() {
  const modal = document.getElementById('subscriptionModal');
  const shouldResume = subscriptionModalContext === 'daily_offer';
  subscriptionModalContext = 'membership';
  closeAccessibleDialog(modal);
  if (shouldResume) resumePendingAnalysisIntent();
}

function initTabs() {
  const tabs = Array.from(document.querySelectorAll('.tab-btn'));
  const labels = {scanner: 'Market scanner', watchlist: 'Watchlist', paper: 'Paper trades', backtest: 'Historical backtest', quant: 'Systematic research', portfolio: 'Portfolio Lab', settings: 'Settings'};
  const activate = btn => {
    const tab = btn.dataset.tab;
    tabs.forEach(item => {
      const selected = item === btn;
      item.classList.toggle('active', selected);
      item.setAttribute('aria-selected', String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll('.tab-panel').forEach(panel => {
      const selected = panel.id === `tab-${tab}`;
      panel.classList.toggle('active', selected);
      panel.setAttribute('aria-hidden', String(!selected));
    });
    const crumb = document.getElementById('pageCrumb');
    if (crumb) crumb.textContent = labels[tab] || 'Workspace';
    if (tab === 'watchlist') loadWatchlist();
    if (tab === 'paper') loadPaperTrades();
    if (tab === 'quant') loadUniversalBlueprint();
    if (tab === 'portfolio') loadPortfolioLedger();
    if (tab === 'settings' && currentUser) {
      refreshAnalysisAccess({silent:true}).catch(() => {});
      refreshProviderCredentialSettings().catch(() => {});
    }
  };
  tabs.forEach((btn, index) => {
    btn.addEventListener('click', () => {
      activate(btn);
    });
    btn.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = tabs.length - 1;
      tabs[nextIndex].focus();
      activate(tabs[nextIndex]);
    });
  });
}

function initQuantLab() {
  const button = document.getElementById('quantRunBtn');
  if (!button) return;
  // Public Quant Lab is deliberately a frozen historical demonstration. The
  // server repeats this boundary; these removals prevent misleading controls.
  document.querySelector('.universal-blueprint-panel')?.setAttribute('hidden', '');
  document.querySelector('.quant-workbench-grid')?.setAttribute('hidden', '');
  ['quantLookback', 'quantLongShort'].forEach(id => document.getElementById(id)?.closest('label')?.setAttribute('hidden', ''));
  document.querySelectorAll('[data-quant-preset]').forEach(item => item.closest('.quant-preset-row')?.setAttribute('hidden', ''));
  button.addEventListener('click', runQuantResearch);
  document.querySelectorAll('.quant-allocation-slider, .quant-strategy-set input[type="checkbox"]').forEach(input => {
    input.addEventListener('input', updateQuantAllocationUI);
    input.addEventListener('change', updateQuantAllocationUI);
  });
  document.getElementById('quantModel')?.addEventListener('change', event => {
    if (event.target.value === 'minerva_baseline') {
      const period = document.getElementById('quantPeriod');
      if (period && ['1y', '2y'].includes(period.value)) period.value = '5y';
      const note = document.getElementById('quantStatus');
      if (note) note.textContent = 'Minerva baseline needs at least 758 completed daily sessions, so Quant Lab has been set to five years.';
      return;
    }
    const profiles = {
      v1_corporate_quant_system: {time_series_trend: 25, cross_sectional_momentum: 20, mean_reversion: 10, defensive_low_volatility: 10, corporate_quality: 35},
      universal_v2: {time_series_trend: 100, cross_sectional_momentum: 0, mean_reversion: 0, defensive_low_volatility: 0, corporate_quality: 0},
      v1_long_only_trend_momentum_research: {time_series_trend: 60, cross_sectional_momentum: 40, mean_reversion: 0, defensive_low_volatility: 0, corporate_quality: 0},
      v8_regime_diversified: {time_series_trend: 35, cross_sectional_momentum: 30, mean_reversion: 15, defensive_low_volatility: 20},
      v8_balanced: {time_series_trend: 45, cross_sectional_momentum: 40, mean_reversion: 15, defensive_low_volatility: 0},
      v8_trend_first: {time_series_trend: 65, cross_sectional_momentum: 25, mean_reversion: 10, defensive_low_volatility: 0},
      v8_relative_strength: {time_series_trend: 25, cross_sectional_momentum: 65, mean_reversion: 10, defensive_low_volatility: 0},
      equal_weight_baseline: {time_series_trend: 34, cross_sectional_momentum: 33, mean_reversion: 33, defensive_low_volatility: 0},
    };
    Object.entries(profiles[event.target.value] || profiles.v8_regime_diversified).forEach(([strategy, value]) => {
      const slider = document.querySelector(`.quant-allocation-slider[data-strategy="${strategy}"]`);
      if (slider) slider.value = String(value);
      const checkbox = document.querySelector(`.quant-strategy-set input[type="checkbox"][value="${strategy}"]`);
      if (checkbox && (value > 0 || strategy === 'corporate_quality')) checkbox.checked = value > 0;
    });
    if (event.target.value === 'v1_long_only_trend_momentum_research' || event.target.value === 'universal_v2') {
      const longShort = document.getElementById('quantLongShort');
      if (longShort) longShort.checked = false;
    }
    updateQuantAllocationUI();
  });
  const portfolioPresets = {
    conservative: {quantTargetVol: 8, quantMaxGross: .8, quantMaxName: 15, quantRebalance: 'monthly', quantCost: 25},
    balanced: {quantTargetVol: 12, quantMaxGross: 1, quantMaxName: 25, quantRebalance: 'weekly', quantCost: 12},
    active: {quantTargetVol: 18, quantMaxGross: 1, quantMaxName: 35, quantRebalance: 'weekly', quantCost: 5},
  };
  document.querySelectorAll('[data-quant-preset]').forEach(button => {
    button.addEventListener('click', () => {
      const preset = portfolioPresets[button.dataset.quantPreset];
      if (!preset) return;
      Object.entries(preset).forEach(([id, value]) => {
        const field = document.getElementById(id);
        if (field) field.value = String(value);
      });
      document.querySelectorAll('[data-quant-preset]').forEach(item => item.classList.toggle('is-active', item === button));
    });
  });
  updateQuantAllocationUI();
}

async function loadUniversalBlueprint() {
  const target = document.getElementById('universalBlueprint');
  if (!target) return;
  if (!currentUser) {
    target.innerHTML = '<p class="muted">Sign in to inspect the research foundation. This does not enable broker execution or a new public scanner.</p>';
    return;
  }
  target.innerHTML = '<p class="muted">Loading research foundation…</p>';
  try {
    renderUniversalBlueprint(await API.universal.blueprint());
  } catch (error) {
    target.innerHTML = '<p class="muted">The Universal V2 foundation is available only when the private-research or full Quant Lab route is enabled.</p>';
  }
}

function renderUniversalBlueprint(blueprint) {
  const target = document.getElementById('universalBlueprint');
  if (!target) return;
  const policy = blueprint?.position_policy || {};
  const market = blueprint?.market_context || {};
  const taxonomy = blueprint?.taxonomy || {};
  const seed = taxonomy.seed_snapshot || {};
  const workstreams = Array.isArray(blueprint?.workstreams) ? blueprint.workstreams : [];
  const states = (policy.state_fields || []).map(field => `<code>${escapeHtml(field)}</code>`).join('') || '<span>Not available</span>';
  const cards = workstreams.map(item => `<article class="universal-workstream"><div><span class="universal-readiness">${escapeHtml(String(item.readiness || '').replaceAll('_', ' '))}</span><h3>${escapeHtml(item.title || item.id || '')}</h3></div><p>${escapeHtml(item.next_gate || '')}</p><details><summary>Inputs, tuning surface & safeguards</summary><div class="universal-workstream-detail"><strong>Inputs</strong><span>${escapeHtml((item.inputs || []).join(' · '))}</span><strong>May tune</strong><span>${escapeHtml((item.tunable_knobs || []).join(' · '))}</span><strong>Fixed</strong><span>${escapeHtml((item.fixed_invariants || []).join(' · '))}</span></div></details></article>`).join('');
  target.innerHTML = `<div class="universal-policy-summary"><div><span>POSITION POLICY</span><b>${escapeHtml(String(policy.status || 'foundation').replaceAll('_', ' '))}</b><small>${escapeHtml(policy.integration_status || '')}</small></div><div><span>POSITION STATE</span><div class="universal-state-fields">${states}</div></div></div><div class="universal-policy-summary"><div><span>MARKET CONTEXT</span><b>${escapeHtml(String(market.status || 'foundation').replaceAll('_', ' '))}</b><small>${escapeHtml(market.integration_status || '')}</small></div><div><span>MULTI-FAMILY TAXONOMY</span><b>${escapeHtml(String(taxonomy.status || 'foundation').replaceAll('_', ' '))}</b><small>${escapeHtml(`${taxonomy.family_count || 0} curated families. ${seed.available ? '25,000-security automated seed loaded; not point-in-time backtest data.' : taxonomy.snapshot_contract?.universe_definition || ''}`)}</small></div></div>${seed.available ? `<form id="universalTaxonomyLookup" class="universal-taxonomy-lookup"><label><span>Inspect a seed member</span><input name="symbol" value="AAPL" maxlength="32" autocomplete="off" aria-label="Seed taxonomy ticker"></label><button type="submit" class="ghost-btn">Show families</button><p id="universalTaxonomyResult" class="muted">This lookup shows classification context only; it does not alter a model or create a trade.</p></form>` : ''}<div class="universal-workstream-grid">${cards}</div><details class="universal-astra-handoff"><summary>Astra tuning handoff</summary><ol>${(blueprint?.astra_handoff || []).map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ol></details>`;
  target.querySelector('#universalTaxonomyLookup')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const symbol = new FormData(event.currentTarget).get('symbol')?.toString().trim();
    const result = target.querySelector('#universalTaxonomyResult');
    if (!symbol || !result) return;
    result.textContent = 'Loading family context…';
    try {
      const response = await API.universal.taxonomySymbol(symbol);
      const matches = (response.matches || []).map(match => `${match.security?.name || match.security?.symbol || symbol}: ${(match.memberships || []).map(edge => edge.family_id).join(' · ')}`).join(' | ');
      result.textContent = matches || 'No family memberships were found.';
    } catch (error) {
      result.textContent = 'That symbol is not present in the local seed universe.';
    }
  });
}

function updateQuantAllocationUI() {
  const checked = new Set(Array.from(document.querySelectorAll('.quant-strategy-set input[type="checkbox"]:checked')).map(input => input.value));
  let total = 0;
  document.querySelectorAll('.quant-allocation-slider').forEach(slider => {
    const active = checked.has(slider.dataset.strategy);
    slider.disabled = !active;
    const value = active ? Math.max(0, Number(slider.value) || 0) : 0;
    total += value;
    const output = document.querySelector(`[data-allocation-output="${slider.dataset.strategy}"]`);
    if (output) output.textContent = `${value}%`;
  });
  const status = document.getElementById('quantAllocationStatus');
  if (!status) return;
  status.textContent = `Allocation: ${total.toFixed(0)}% across active strategy sleeves. ${total > 0 ? 'The server will normalize active sleeves to 100%.' : 'Set at least one active allocation above 0%.'}`;
  status.classList.toggle('is-warning', total <= 0 || Math.abs(total - 100) > .1);
}

function quantNumber(value, digits=2, suffix='') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}${suffix}`;
}

function quantHeatColor(value, scale=1) {
  const safe = Math.min(1, Math.abs(Number(value) || 0) / scale);
  const alpha = (0.08 + safe * 0.7).toFixed(2);
  return Number(value) >= 0 ? `rgba(31, 111, 174, ${alpha})` : `rgba(168, 82, 91, ${alpha})`;
}

function renderCorrelationHeatmap(correlation) {
  const symbols = correlation?.symbols || [], values = correlation?.values || [];
  if (!symbols.length || !values.length) return '<p class="muted">Correlation data needs more completed sessions.</p>';
  const cells = symbols.map((rowSymbol, rowIndex) => {
    const row = values[rowIndex] || [];
    return `<span class="quant-heatmap-label">${escapeHtml(rowSymbol)}</span>${symbols.map((columnSymbol, columnIndex) => {
      const value = row[columnIndex];
      if (!Number.isFinite(Number(value))) return '<span class="quant-heat-cell is-empty">—</span>';
      const foreground = Math.abs(Number(value)) > .56 ? '#fff' : 'var(--ink)';
      return `<span class="quant-heat-cell" style="background:${quantHeatColor(value)};color:${foreground}" title="${escapeHtml(rowSymbol)} / ${escapeHtml(columnSymbol)}: ${Number(value).toFixed(3)}">${Number(value).toFixed(2)}</span>`;
    }).join('')}`;
  }).join('');
  return `<div class="quant-heatmap" style="grid-template-columns:64px repeat(${symbols.length}, minmax(38px, 1fr))"><span></span>${symbols.map(symbol => `<span class="quant-heatmap-head">${escapeHtml(symbol)}</span>`).join('')}${cells}</div><p class="quant-diagnostic-note">${escapeHtml(correlation.note || '')}</p>`;
}

function renderCorrelationStress(stress) {
  const scenarios = stress?.scenarios || [];
  if (!scenarios.length) return `<p class="muted">${escapeHtml(stress?.note || 'Correlation stress needs more overlapping return history.')}</p>`;
  const rows = scenarios.map(item => `<tr><td>${escapeHtml(item.label || item.id || 'Scenario')}</td><td>${quantNumber(item.convergence_to_positive_one * 100, 0, '%')}</td><td>${quantNumber(item.baseline_21_session_volatility_pct, 2, '%')}</td><td>${quantNumber(item.stressed_21_session_volatility_pct, 2, '%')}</td><td>${quantNumber(item.risk_multiplier, 3, '×')}</td></tr>`).join('');
  return `<table><thead><tr><th>Scenario</th><th>Move toward +1</th><th>Base 21d vol</th><th>Stressed 21d vol</th><th>Risk multiple</th></tr></thead><tbody>${rows}</tbody></table><p class="quant-diagnostic-note">${escapeHtml(stress.method || '')}<br>${escapeHtml(stress.note || '')}</p>`;
}

function renderAssumptionLedger(ledger) {
  const groups = [['timing', 'Timing & controls'], ['portfolio', 'Portfolio limits'], ['execution', 'Execution-cost assumptions'], ['evidence', 'Evidence & stress inputs']];
  const sections = groups.map(([key, title]) => {
    const rows = Array.isArray(ledger?.[key]) ? ledger[key] : [];
    if (!rows.length) return '';
    return `<div class="quant-explainer"><strong>${escapeHtml(title)}</strong>${rows.map(row => `<div><b>${escapeHtml(row.label || '—')}</b><br>${escapeHtml(row.value || '—')}</div>`).join('')}</div>`;
  }).join('');
  const omissions = Array.isArray(ledger?.omissions) ? ledger.omissions : [];
  if (!sections && !omissions.length) return '<p class="muted">No assumption ledger was returned for this report.</p>';
  return `${sections}${omissions.length ? `<div class="quant-warning"><strong>Material omissions</strong><ul>${omissions.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}<p class="quant-diagnostic-note">${escapeHtml(ledger.note || '')}</p>`;
}

function renderMonthlyHeatmap(monthly) {
  const years = monthly?.years || [], values = monthly?.values || [];
  if (!years.length || !values.length) return '<p class="muted">Monthly return data needs completed history.</p>';
  const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const rows = years.map((year, rowIndex) => `<tr><th>${escapeHtml(String(year))}</th>${monthNames.map((name, monthIndex) => {
    const value = values[rowIndex]?.[monthIndex];
    if (!Number.isFinite(Number(value))) return '<td class="quant-month-cell is-empty">—</td>';
    const foreground = Math.abs(Number(value)) > 4 ? '#fff' : 'var(--ink)';
    return `<td class="quant-month-cell" style="background:${quantHeatColor(value, 8)};color:${foreground}" title="${escapeHtml(String(year))} ${name}: ${quantNumber(value, 2, '%')}">${quantNumber(value, 1, '%')}</td>`;
  }).join('')}</tr>`).join('');
  return `<div class="quant-monthly-scroll"><table class="quant-monthly-table"><thead><tr><th>Year</th>${monthNames.map(name => `<th>${name}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div><p class="quant-diagnostic-note">${escapeHtml(monthly.note || '')}</p>`;
}

function renderQuantSeries(points, kind='equity') {
  if (!Array.isArray(points) || points.length < 2) return '<span>Insufficient completed history</span>';
  const values = points.map(point => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return '<span>Insufficient completed history</span>';
  const width = 420, height = 98, low = Math.min(...values), high = Math.max(...values), range = Math.max(high - low, .001);
  const line = values.map((value, index) => `${(index / (values.length - 1) * width).toFixed(1)},${(height - (value - low) / range * height).toFixed(1)}`).join(' ');
  const className = kind === 'drawdown' ? 'quant-series-drawdown' : kind === 'volatility' ? 'quant-series-volatility' : 'quant-series-equity';
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><polyline class="${className}" points="${line}" /></svg>`;
}

async function runQuantResearch() {
  const button = document.getElementById('quantRunBtn');
  const status = document.getElementById('quantStatus');
  const results = document.getElementById('quantResults');
  if (!currentUser) {
    status.textContent = 'Create an account or sign in to use Quant Lab.';
    openAuthModal('signup');
    return;
  }
  const tickers = (document.getElementById('quantTickers')?.value || '').split(/[\s,]+/).map(sanitizeTickerSymbol).filter(Boolean);
  if (tickers.length < 2) { status.textContent = 'Choose at least two symbols for the fixed public research profile.'; return; }
  const provider = document.getElementById('quantProvider')?.value || 'auto';
  const requestedProvider = provider === 'cache_only' ? 'auto' : provider;
  if (!await requireProviderKey({type:'quant'}, requestedProvider)) {
    status.textContent = 'Connect the selected provider key in this browser to use Quant Lab.';
    return;
  }
  const period = document.getElementById('quantPeriod')?.value || '2y';
  const model = document.getElementById('quantModel')?.value || 'v8_official';
  const payload = {tickers, model};
  button.disabled = true; results.hidden = true; status.textContent = 'Loading histories, applying fixed rules, and modeling next-session execution…';
  try {
    const activeProvider = directProviderFor(requestedProvider);
    status.textContent = `Feel free to take a break and switch apps, this scan may take a minute. Loading daily histories directly from ${activeProvider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'}…`;
    if (activeProvider === 'polygon' && tickers.length > 4) {
      throw new Error('Polygon / Massive Basic Quant Lab runs support up to four symbols at once. Use four or fewer symbols, or select Twelve Data for a larger universe.');
    }
    const histories = [];
    const markets = await Promise.all(
      tickers.map(ticker => fetchDirectMarketBars(ticker, period, activeProvider)),
    );
    markets.forEach((market, index) => {
      histories.push({ticker: tickers[index], bars: market.bars});
    });
    status.textContent = 'Building the research report with costs, regimes, and risk controls…';
    const report = await API.quant.runUploaded({...payload, provider: activeProvider, histories});
    renderQuantReport(report);
    status.textContent = `Completed ${report.universe.sessions} sessions across ${report.universe.symbols.length} usable symbols. Raw browser-supplied bars were processed in memory only.`;
  }
  catch (error) { status.textContent = `Research run could not finish: ${error.message || String(error)}`; }
  finally { button.disabled = false; }
}

function renderQuantReport(report) {
  const results = document.getElementById('quantResults');
  if (!results) return;
  const cards = (report.results || []).map(item => `<article class="panel quant-result-card"><div class="quant-result-head"><div><div class="quant-result-kicker">${escapeHtml(item.id || '')}</div><h3>${escapeHtml(item.label || '')}</h3></div><span class="quant-research-badge">RESEARCH ONLY</span></div><p>${escapeHtml(item.description || '')}</p><div class="quant-result-allocation"><span>CONFIGURED CONTRIBUTION</span><b>${quantNumber(item.configured_allocation_pct, 1, '%')}</b></div><div class="quant-metrics"><div><span>ANN. RETURN</span><strong>${quantNumber(item.annualized_return_pct, 2, '%')}</strong></div><div><span>MAX DRAWDOWN</span><strong>${quantNumber(item.max_drawdown_pct, 2, '%')}</strong></div><div><span>VOLATILITY</span><strong>${quantNumber(item.annualized_volatility_pct, 2, '%')}</strong></div><div><span>SHARPE</span><strong>${quantNumber(item.sharpe_zero_cash_rate)}</strong></div><div><span>HIST. ES 95%</span><strong>${quantNumber(item.historical_expected_shortfall_95_pct, 2, '%')}</strong></div><div><span>ANN. TURNOVER</span><strong>${quantNumber(item.annualized_turnover, 2, '×')}</strong></div></div>${item.validation_warning ? `<div class="quant-warning">${escapeHtml(item.validation_warning)}</div>` : ''}<div class="quant-curve">${renderQuantCurve(item.equity_curve || [])}</div><details class="quant-detail"><summary>Method, environment & risk</summary><div class="quant-explainer"><div><strong>HOW IT WORKS</strong><br>${escapeHtml(item.how_it_works || '')}</div><div><strong>BEST ENVIRONMENT</strong><br>${escapeHtml(item.best_environment || '')}</div><div><strong>KEY RISK</strong><br>${escapeHtml(item.key_risk || '')}</div></div></details></article>`).join('');
  const risk = report.portfolio_risk || {}, validation = report.validation || {}, diagnostics = report.visual_diagnostics || {}, performance = diagnostics.performance || {}, execution = report.execution || {}, corporate = report.corporate_data || {}, macro = report.macro_data || {}, attribution = report.factor_attribution || {}, health = report.strategy_health || [], ledger = report.assumption_ledger || {};
  const kpi = (name, value) => `<div class="quant-kpi"><span>${name}</span><strong>${value}</strong></div>`;
  const regimeRows = (report.regime_breakdown || []).map(row => `<tr><td>${escapeHtml(row.regime)}</td><td>${escapeHtml(String(row.sessions))}</td><td>${quantNumber(row.total_return_pct,2,'%')}</td><td>${quantNumber(row.annualized_volatility_pct,2,'%')}</td></tr>`).join('') || '<tr><td colspan="4">Insufficient completed history for a regime comparison.</td></tr>';
  const positionRows = (risk.latest_positions || []).map(row => `<tr><td>${escapeHtml(row.symbol)}</td><td>${quantNumber(row.weight_pct,2,'%')}</td></tr>`).join('') || '<tr><td colspan="2">No active hypothetical positions.</td></tr>';
  const qualityRows = (report.data_quality?.symbols || []).map(row => `<tr><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(String(row.bars))}</td><td>${quantNumber(row.missing_session_pct,2,'%')}</td><td>${escapeHtml(row.last_bar || '—')}</td></tr>`).join('') || '<tr><td colspan="4">No quality record available.</td></tr>';
  const warnings = (report.methodology?.warnings || []).map(text => `<li>${escapeHtml(text)}</li>`).join('');
  results.innerHTML = `<div class="quant-run-meta"><span>DATASET FINGERPRINT</span><code>${escapeHtml((report.dataset_fingerprint || '').slice(0,20))}…</code><span>${escapeHtml(report.universe?.start || '—')} → ${escapeHtml(report.universe?.end || '—')}</span><span>PROVIDER · ${escapeHtml(report.data_provider || '—')}</span></div><section class="quant-kpi-grid">${kpi('Gross exposure', quantNumber(risk.latest_gross_exposure,2,'×'))}${kpi('Net exposure', quantNumber(risk.latest_net_exposure,2,'×'))}${kpi('Effective positions', quantNumber(risk.effective_number_of_positions,2))}${kpi('Avg. |correlation|', quantNumber(risk.average_abs_correlation_126_sessions,3))}${kpi('Holdout return', quantNumber(validation.holdout?.total_return_pct,2,'%'))}${kpi('Holdout max drawdown', quantNumber(validation.holdout?.max_drawdown_pct,2,'%'))}</section><div class="quant-result-grid">${cards}</div><div class="quant-diagnostics-grid"><section class="panel quant-diagnostic"><p class="eyebrow">Regime report</p><h2>How did the rules behave?</h2><table><thead><tr><th>Historical state</th><th>Sessions</th><th>Return</th><th>Volatility</th></tr></thead><tbody>${regimeRows}</tbody></table></section><section class="panel quant-diagnostic"><p class="eyebrow">Portfolio controls</p><h2>Latest hypothetical exposure</h2><div class="quant-risk-list"><span>Largest name <b>${quantNumber(risk.largest_name_weight_pct,2,'%')}</b></span><span>Effective positions <b>${quantNumber(risk.effective_number_of_positions,2)}</b></span><span>Average correlation <b>${quantNumber(risk.average_abs_correlation_126_sessions,3)}</b></span></div><table><thead><tr><th>Symbol</th><th>Weight</th></tr></thead><tbody>${positionRows}</tbody></table></section></div><div class="quant-diagnostics-grid"><section class="panel quant-diagnostic"><p class="eyebrow">Structured-data coverage</p><h2>Corporate & macro evidence</h2><div class="quant-risk-list"><span>Corporate quality <b>${quantNumber(corporate.signal_coverage_pct,1,'%')} · ${escapeHtml(corporate.status || '—')}</b></span><span>Rates, curve, credit & inflation <b>${quantNumber(macro.signal_coverage_pct,1,'%')} · ${escapeHtml(macro.status || '—')}</b></span><span>Liquidity limit breaches <b>${escapeHtml(String(execution.participation_limit_breaches ?? '—'))}</b></span><span>Market beta <b>${quantNumber(attribution.market_beta_126_sessions,3)}</b></span></div><p class="quant-diagnostic-note">${escapeHtml(execution.note || '')}</p></section><section class="panel quant-diagnostic"><p class="eyebrow">Strategy health</p><h2>Recent alpha decay</h2>${health.length ? `<table><thead><tr><th>Sleeve</th><th>Recent bps</th><th>Decay bps</th><th>Status</th></tr></thead><tbody>${health.map(row => `<tr><td>${escapeHtml(row.strategy)}</td><td>${quantNumber(row.recent_mean_daily_bps,2)}</td><td>${quantNumber(row.alpha_decay_daily_bps,2)}</td><td>${escapeHtml(row.status)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">No health history yet.</p>'}</section></div><div class="quant-diagnostics-grid"><section class="panel quant-diagnostic"><p class="eyebrow">Correlation matrix</p><h2>Where diversification may fail</h2>${renderCorrelationHeatmap(diagnostics.correlation)}</section><section class="panel quant-diagnostic"><p class="eyebrow">Correlation stress</p><h2>Hypothetical diversification breakdown</h2>${renderCorrelationStress(diagnostics.correlation_stress)}</section></div><div class="quant-diagnostics-grid"><section class="panel quant-diagnostic"><p class="eyebrow">Monthly return heatmap</p><h2>Return distribution over time</h2>${renderMonthlyHeatmap(diagnostics.monthly_returns)}</section><section class="panel quant-diagnostic"><p class="eyebrow">Risk path</p><h2>Equity and drawdown</h2><div class="quant-series-grid"><div><span>NET EQUITY INDEX</span><div class="quant-series">${renderQuantSeries(performance.equity_curve, 'equity')}</div></div><div><span>DRAWDOWN (%)</span><div class="quant-series">${renderQuantSeries(performance.drawdown_curve, 'drawdown')}</div></div><div><span>ROLLING 63D VOLATILITY</span><div class="quant-series">${renderQuantSeries(performance.rolling_volatility_63_pct, 'volatility')}</div></div></div><p class="quant-diagnostic-note">${escapeHtml(performance.note || '')}</p></section></div><div class="quant-diagnostics-grid"><section class="panel quant-diagnostic"><p class="eyebrow">Chronological validation</p><h2>Development vs holdout</h2><p class="muted">${escapeHtml(validation.note || validation.message || '')}</p><div class="quant-validation-grid"><div><span>Development</span><b>${quantNumber(validation.development?.total_return_pct,2,'%')}</b><small>${escapeHtml(String(validation.development?.observations || '—'))} sessions</small></div><div><span>Holdout</span><b>${quantNumber(validation.holdout?.total_return_pct,2,'%')}</b><small>${escapeHtml(String(validation.holdout?.observations || '—'))} sessions</small></div></div></section><section class="panel quant-diagnostic"><p class="eyebrow">Data quality</p><h2>Universe coverage</h2><table><thead><tr><th>Symbol</th><th>Bars</th><th>Missing</th><th>Latest</th></tr></thead><tbody>${qualityRows}</tbody></table></section></div><section class="panel quant-method-card"><p class="eyebrow">Assumption ledger</p><h2>What this report assumes</h2>${renderAssumptionLedger(ledger)}</section><section class="panel quant-method-card"><p class="eyebrow">Method & limitations</p><ul>${warnings}</ul></section>`;
  results.hidden = false;
}

function renderQuantCurve(points) {
  if (!Array.isArray(points) || points.length < 2) return '<span>Insufficient equity-curve data</span>';
  const values = points.map(point => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return '<span>Insufficient equity-curve data</span>';
  const width=420, height=92, low=Math.min(...values), high=Math.max(...values), range=Math.max(high-low,.001);
  const line = values.map((value,index) => `${(index/(values.length-1)*width).toFixed(1)},${(height-(value-low)/range*height).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><polyline class="${values.at(-1) >= values[0] ? 'quant-curve-up' : 'quant-curve-down'}" points="${line}" /></svg>`;
}

async function initRuntimeCapabilities() {
  try {
    const runtime = await API.runtime();
    runtimeCapabilities = {
      ...runtimeCapabilities,
      ...runtime,
      partner_ads: {...runtimeCapabilities.partner_ads, ...(runtime?.partner_ads || {})},
      turnstile: {...runtimeCapabilities.turnstile, ...(runtime?.turnstile || {})},
    };
    initPartnerAdvertisements();
    mountTurnstile();
    const privateResearch = Boolean(runtime?.private_research_routes);
    const devPanel = document.getElementById('devLabPanel');
    const betaBadge = document.querySelector('.beta-version-badge');
    if (!privateResearch) {
      if (devPanel) devPanel.hidden = true;
      if (betaBadge) {
        betaBadge.removeAttribute('role');
        betaBadge.removeAttribute('tabindex');
        betaBadge.removeAttribute('aria-controls');
        betaBadge.setAttribute('aria-label', 'Oryntra AI beta version');
      }
    }
  } catch (_) {}
}

function normalizeAnalysisAccess(payload) {
  return {
    ready: payload?.status === 'ready' || payload?.policy?.analysis_permitted === true,
    policy: payload?.policy || null,
    quota: payload?.quota || null,
  };
}

function renderAnalysisAccess() {
  const ready = Boolean(currentUser && analysisAccessState.ready);
  const policy = analysisAccessState.policy || {};
  const quota = analysisAccessState.quota || {};
  const scannerDot = document.getElementById('analysisScannerDot');
  const scannerTitle = document.getElementById('analysisScannerTitle');
  const scannerMessage = document.getElementById('analysisScannerMessage');
  const scannerButton = document.getElementById('analysisScannerManageBtn');
  const badge = document.getElementById('analysisAccessBadge');
  const list = document.getElementById('analysisAccessList');
  const error = document.getElementById('analysisAccessError');

  [scannerDot, badge].forEach(el => {
    if (!el) return;
    el.classList.remove('is-connected', 'is-error', 'is-offline');
    el.classList.add(ready ? 'is-connected' : 'is-offline');
  });

  if (!currentUser) {
    if (scannerTitle) scannerTitle.textContent = 'Sign in to analyze';
    if (scannerMessage) scannerMessage.textContent = 'Oryntra returns derived analysis only. TradingView independently supplies the embedded chart.';
    if (scannerButton) scannerButton.textContent = 'SIGN IN';
    if (badge) badge.textContent = 'SIGNED OUT';
    if (list) list.innerHTML = '<div class="analysis-empty-state">Sign in to view your daily analysis allowance.</div>';
    if (error) error.textContent = '';
    return;
  }

  if (ready) {
    const used = Number.isFinite(Number(quota.used)) ? Number(quota.used) : '—';
    if (scannerTitle) scannerTitle.textContent = 'Market intelligence ready';
    if (scannerMessage) scannerMessage.textContent = `API calls made today: ${used}. Raw OHLCV is never returned.`;
    if (scannerButton) scannerButton.textContent = 'VIEW USAGE';
    if (badge) badge.textContent = 'READY';
    if (list) list.innerHTML = `<div class="analysis-empty-state"><strong>API calls made today: ${escapeHtml(String(used))}</strong><br>Chart: TradingView · Analysis: server-side derived output · Raw bars: not returned.</div>`;
    if (error) error.textContent = '';
    return;
  }

  const mode = policy.license_mode || 'personal_research';
  if (scannerTitle) scannerTitle.textContent = mode === 'personal_research' ? 'Owner research mode' : 'Analysis temporarily unavailable';
  if (scannerMessage) scannerMessage.textContent = mode === 'personal_research'
    ? 'This data license is restricted to the configured owner account.'
    : 'Public analysis remains disabled until the approved market-data agreement is activated.';
  if (scannerButton) scannerButton.textContent = 'ACCOUNT';
  if (badge) badge.textContent = 'RESTRICTED';
  if (list) list.innerHTML = '<div class="analysis-empty-state">The server license gate is preventing public analysis. This is intentional.</div>';
}

async function refreshAnalysisAccess(options = {}) {
  const {silent = false, force = false} = options;
  if (!currentUser) {
    analysisAccessState = {ready:false, policy:null, quota:null};
    renderAnalysisAccess();
    return analysisAccessState;
  }
  if (analysisAccessPromise && !force) return analysisAccessPromise;
  analysisAccessPromise = API.intelligence.status()
    .then(payload => {
      analysisAccessState = normalizeAnalysisAccess(payload);
      renderAnalysisAccess();
      return analysisAccessState;
    })
    .catch(err => {
      analysisAccessState = {ready:false, policy:null, quota:null};
      renderAnalysisAccess();
      if (!silent) {
        const target = document.getElementById('analysisAccessError');
        if (target) target.textContent = err.message || String(err);
      }
      throw err;
    })
    .finally(() => { analysisAccessPromise = null; });
  return analysisAccessPromise;
}

async function requireAnalysisAccess(intent = null) {
  if (!currentUser) {
    pendingAnalysisIntent = intent;
    openAuthModal('login');
    return false;
  }
  if (!await requireProviderKey(intent)) return false;
  try {
    const status = await refreshAnalysisAccess({silent:true});
    if (status.ready) return true;
  } catch (error) {
    showError(error.message || String(error));
    return false;
  }
  showError('Analysis is not enabled for this account or server license mode.');
  return false;
}

async function resumePendingAnalysisIntent() {
  if (!pendingAnalysisIntent || !currentUser) return;
  const intent = pendingAnalysisIntent;
  pendingAnalysisIntent = null;
  const status = await refreshAnalysisAccess({silent:true}).catch(() => analysisAccessState);
  if (!status.ready) return;
  if (intent.type === 'scan') runScan(intent.ticker, intent.period);
  if (intent.type === 'scan-all') scanAllWatchlist();
  if (intent.type === 'quant') runQuantResearch();
  if (intent.type === 'portfolio') runPortfolioLab();
}

function renderPortfolioLabAccess() {
  const lock = document.getElementById('portfolioAccessLock');
  const workspace = document.getElementById('portfolioWorkspace');
  if (!lock || !workspace) return;
  const allowed = hasPortfolioProAccess(currentUser);
  lock.hidden = allowed;
  workspace.hidden = !allowed;
}

function hasPortfolioProAccess(user) {
  // CQC Max includes Oryntra Pro. Keep this explicit rather than inferring it
  // from a display name, which can be changed by a billing provider.
  return ['pro', 'plus', 'max', 'max_bundle', 'max-bundle', 'cqc_max', 'cqc-max'].includes(String(user?.subscription?.plan_code || '').toLowerCase());
}

function portfolioDirectiveCard(row) {
  const action = String(row.action || 'HOLD').toLowerCase();
  const lifecycle = row.lifecycle || {};
  const lifecycleLine = lifecycle.current_stop
    ? `Active research stop ${Number(lifecycle.current_stop).toFixed(2)} · ${Number(lifecycle.held_sessions || 0)} sessions held`
    : 'No active lifecycle state recorded for this symbol.';
  return `<article class="portfolio-directive portfolio-${escapeHtml(action)}"><div class="portfolio-directive-head"><strong>${escapeHtml(row.symbol || '—')}</strong><span>${escapeHtml(row.action || 'UNCHANGED')}</span></div><div class="portfolio-directive-values"><div><small>Study weight</small><b>${Number(row.target_weight_pct || row.target_weight || 0).toFixed(2)}%</b></div><div><small>Prior study weight</small><b>${Number(row.prior_weight_pct || row.prior_weight || 0).toFixed(2)}%</b></div><div><small>Reference close</small><b>${Number(row.reference_close || row.reference_price || 0).toFixed(2)}</b></div><div><small>Modeled units</small><b>${Number(row.estimated_target_shares || row.estimated_shares || 0).toFixed(2)}</b></div></div><p>${escapeHtml(row.rationale || '')}</p><small class="portfolio-lifecycle">${escapeHtml(lifecycleLine)}</small></article>`;
}

function renderPortfolioDecision(report, {stored=false} = {}) {
  const results = document.getElementById('portfolioResults');
  if (!results) return;
  const directives = report.directives || [];
  const actions = directives.reduce((counts, row) => { counts[row.action] = (counts[row.action] || 0) + 1; return counts; }, {});
  const summary = ['INCREASE', 'REDUCE', 'EXIT', 'UNCHANGED'].filter(key => actions[key]).map(key => `<span><b>${actions[key]}</b> ${key.toLowerCase()}</span>`).join('') || '<span>No study changes</span>';
  results.innerHTML = `<section class="panel portfolio-decision-summary"><div><p class="eyebrow">${stored ? 'Latest saved study' : 'Study saved'}</p><h2>${escapeHtml(report.label || 'Portfolio Lab')}</h2><p>${escapeHtml(report.execution_note || 'Educational hypothetical study only. Not financial advice or a recommendation; no broker order was created.')}</p></div><div class="portfolio-action-summary">${summary}</div><div class="portfolio-fingerprints"><span>AS OF <b>${escapeHtml(report.as_of || '—')}</b></span><span>DATASET <code>${escapeHtml(String(report.dataset_fingerprint || '').slice(0, 14))}…</code></span><span>CONFIG <code>${escapeHtml(String(report.configuration_fingerprint || '').slice(0, 14))}…</code></span></div></section><section class="portfolio-directive-grid">${directives.map(portfolioDirectiveCard).join('')}</section>`;
}

async function loadPortfolioLedger() {
  renderPortfolioLabAccess();
  if (!hasPortfolioProAccess(currentUser)) return;
  try {
    const response = await API.portfolioLab.runs();
    if (response.runs?.length) renderPortfolioDecision(response.runs[0], {stored:true});
  } catch (error) {
    if (error.statusCode !== 402) console.warn('Portfolio ledger could not load', error);
  }
}

async function runPortfolioLab() {
  if (!currentUser) { openAuthModal('login'); return; }
  if (!hasPortfolioProAccess(currentUser)) { showSubscriptionModal('membership'); return; }
  const button = document.getElementById('portfolioRunBtn');
  const loading = document.getElementById('portfolioLoading');
  const tickers = (document.getElementById('portfolioTickers')?.value || '').split(/[\s,]+/).map(sanitizeTickerSymbol).filter((ticker, index, all) => ticker && all.indexOf(ticker) === index);
  if (tickers.length < 2) { showError('Portfolio Lab needs at least two unique ticker symbols.'); return; }
  if (tickers.length > 24) { showError('Portfolio Lab accepts up to 24 symbols per research ledger.'); return; }
  const preferredProvider = document.getElementById('portfolioProvider')?.value || 'auto';
  if (!await requireProviderKey({type:'portfolio'}, preferredProvider)) return;
  try {
    const access = await refreshAnalysisAccess({silent:true});
    if (!access.ready) throw new Error('Analysis is not enabled for this account or server license mode.');
    button.disabled = true;
    loading.style.display = '';
    const activeProvider = directProviderFor(preferredProvider);
    const markets = await Promise.all(tickers.map(ticker => fetchDirectMarketBars(ticker, '2y', activeProvider, 254)));
    const report = await API.portfolioLab.runUploaded({
      label: (document.getElementById('portfolioLabel')?.value || 'Portfolio Lab').trim() || 'Portfolio Lab',
      histories: markets.map((market, index) => ({ticker: tickers[index], bars: market.bars})),
    });
    renderPortfolioDecision(report);
  } catch (error) {
    if (error.code === 'SUBSCRIPTION_REQUIRED' || error.statusCode === 402) showSubscriptionModal('membership');
    else showError(error.message || String(error));
  } finally {
    if (button) button.disabled = false;
    if (loading) loading.style.display = 'none';
  }
}

function initPortfolioLab() {
  document.getElementById('portfolioAccessBtn')?.addEventListener('click', () => showSubscriptionModal('membership'));
  document.getElementById('portfolioRunBtn')?.addEventListener('click', runPortfolioLab);
  renderPortfolioLabAccess();
}

function initAnalysisAccess() {
  document.getElementById('analysisScannerManageBtn')?.addEventListener('click', () => {
    if (!currentUser) openAuthModal('login');
    else document.querySelector('[data-tab="settings"]')?.click();
  });
  document.getElementById('analysisAccessRefreshBtn')?.addEventListener('click', () => refreshAnalysisAccess({force:true}));
  renderAnalysisAccess();
  renderPortfolioLabAccess();
}

function initScanner() {
  const input   = document.getElementById('tickerInput');
  const scanBtn = document.getElementById('scanBtn');

  scanBtn.addEventListener('click', () => runScan());
  input.addEventListener('keydown', e => { if (e.key === 'Enter') runScan(); });
  input.addEventListener('focus', () => clearTickerIfAutofilledEmail());
  input.addEventListener('input', () => {
    if (clearTickerIfAutofilledEmail()) return;
    const clean = sanitizeTickerSymbol(input.value);
    if (input.value && input.value !== clean) input.value = clean;
  });

  document.querySelectorAll('.quick-pick-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const t = chip.dataset.ticker;
      if (input) input.value = t;
      runScan(t);
    });
  });

  const periodGroup = document.querySelector('.chart-period-group');
  if (periodGroup) {
    console.log('✅ Period group found - attaching delegated listener');
    periodGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('.tv-period-btn');
      if (!btn) return; 
      
      console.log('🎯 PERIOD BUTTON CLICKED:', btn.dataset.period, '(' + btn.textContent + ')');
      
      document.querySelectorAll('.tv-period-btn').forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');
      console.log('✅ ACTIVE CLASS SET TO:', btn.dataset.period);
      
      currentPeriod = btn.dataset.period;
      console.log('✅ CURRENT PERIOD:', currentPeriod);
      
      if (currentTicker) {
        console.log('✅ TRIGGERING SCAN - Ticker:', currentTicker, 'Period:', currentPeriod);
        runScan(currentTicker, currentPeriod);
      } else {
        console.log('⚠️ No ticker yet - search first');
      }
    });
  } else {
    console.error('❌ Period group NOT found!');
  }

  const intervalGroup = document.querySelector('.chart-interval-group');
  if (intervalGroup) {
    console.log('✅ Interval group found - attaching delegated listener');
    intervalGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('.tv-interval-btn');
      if (!btn) return; 
      
      console.log('🎯 INTERVAL BUTTON CLICKED:', btn.dataset.interval, '(' + btn.textContent + ')');
      
      document.querySelectorAll('.tv-interval-btn').forEach(b => {
        b.classList.remove('active');
        b.setAttribute('aria-pressed', 'false');
      });
      
      btn.classList.add('active');
      btn.setAttribute('aria-pressed', 'true');
      console.log('✅ ACTIVE CLASS SET TO:', btn.dataset.interval);
      
      currentInterval = btn.dataset.interval;
      console.log('✅ CURRENT INTERVAL:', currentInterval);
      
      if (currentTicker) {
        console.log('✅ LOADING TRADINGVIEW - Ticker:', currentTicker, 'Interval:', currentInterval);
        loadTradingView(currentTicker, currentInterval, currentAnalysis?.exchange);
      } else {
        console.log('⚠️ No ticker yet - search first');
      }
    });
  } else {
    console.error('❌ Interval group NOT found!');
  }

  const refreshAiBtn = document.getElementById('refreshAiBtn');
  if (refreshAiBtn) {
    refreshAiBtn.addEventListener('click', () => {
      if (currentAnalysis) generateAI(currentAnalysis);
    });
  }

  document.getElementById('addToWatchlistBtn').addEventListener('click', () => {
    if (currentTicker) addToWatchlist(currentTicker);
  });
}

async function runScan(ticker = null, period = null) {
  const inputEl = document.getElementById('tickerInput');
  const rawInput = ticker || (inputEl ? inputEl.value : '');
  if (String(rawInput || '').includes('@')) {
    if (inputEl) inputEl.value = '';
    showError('That looks like an email address, not a ticker. Try AAPL, TSLA, NVDA, SPY, etc.');
    return;
  }
  const raw = sanitizeTickerSymbol(rawInput);
  if (!raw) return;
  if (inputEl) inputEl.value = raw;

  currentTicker = raw;
  currentPeriod = period || currentPeriod;
  const scanIntent = {type:'scan', ticker:raw, period:currentPeriod};
  if (showDailyScannerOffer(currentUser, scanIntent)) return;
  const allowed = await requireAnalysisAccess(scanIntent);
  if (!allowed) return;
  showLoading(true);
  hideError();
  hideResults();

  try {
    animateLoadingSteps();
    const market = await fetchDirectMarketBars(raw, currentPeriod, 'auto', 320);
    const data = await API.scanUploaded(raw, currentPeriod, market.provider, market.bars, PUBLIC_ANALYSIS_ENGINE);
    currentAnalysis = data;
    if (Number.isFinite(Number(data.search_counter))) {
      updateSearchCounter(data.search_counter);
    } else {
      const cached = Number(localStorage.getItem('oryntra_total_stock_searches') || '0');
      updateSearchCounter((Number.isFinite(cached) ? cached : 0) + 1);
      loadSearchCounter();
    }
    renderResults(data);
    loadTradingView(raw, currentInterval, data.exchange);
    generateAI(data);
    document.getElementById('addToWatchlistBtn').style.display = '';
    showResults();
  } catch (err) {
    console.error('Oryntra scan failed:', err);
    if (['MARKET_DATA_LICENSE_REQUIRED','PUBLIC_ANALYSIS_DISABLED','SUBSCRIPTION_REQUIRED','DAILY_ANALYSIS_LIMIT_REACHED'].includes(err?.code)) {
      await refreshAnalysisAccess({silent:true, force:true}).catch(() => {});
      showError(err.message || 'Analysis is not available for this account.');
      return;
    }
    let msg = typeof err === 'string' ? err : (err && err.message ? err.message : 'Couldn\'t analyze that ticker.');
    if (/not found|check the symbol|no .*data/i.test(msg)) {
      msg = `"${raw}" didn't return data. Double-check the symbol — try a major US ticker like AAPL, MSFT, or SPY.`;
    } else if (/rate limit/i.test(msg)) {
      msg = 'Hit the data provider\'s rate limit. Wait a few seconds and try again.';
    } else if (/No (Polygon|Twelve Data) API key is saved|Secure provider-key storage/i.test(msg)) {
      msg = 'Add your Polygon or Twelve Data key in Settings, or try a symbol already available in the local cache.';
      document.querySelector('[data-tab="settings"]')?.click();
    } else if (/network|timed out|timeout/i.test(msg)) {
      msg = 'Network hiccup reaching the data provider. Check your connection and try again.';
    }
    showError(msg);
  } finally {
    showLoading(false);
  }
}

async function loadSearchCounter() {
  try {
    const stats = await API.stats();
    const count = Number(stats.total_stock_searches);
    if (Number.isFinite(count)) {
      updateSearchCounter(count, {persist: true});
      return;
    }
    throw new Error('Invalid counter payload');
  } catch (err) {
    const cached = Number(localStorage.getItem('oryntra_total_stock_searches') || '0');
    updateSearchCounter(Number.isFinite(cached) ? cached : 0, {persist: false, animate: false});
  }
}

function updateSearchCounter(value, options = {}) {
  const {persist = true, animate = true} = options;
  const els = document.querySelectorAll('.stock-search-counter-number');
  if (!els.length) return;
  const n = Number(value);
  const safe = Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0;
  const text = safe.toLocaleString();
  els.forEach(el => { el.textContent = text; });
  if (persist) {
    localStorage.setItem('oryntra_total_stock_searches', String(safe));
  }
  if (!animate) return;

  ['homeSearchCounter', 'resultsSearchCounter'].forEach(id => {
    const card = document.getElementById(id);
    if (!card) return;
    card.classList.remove('counter-pop');
    void card.offsetWidth;
    card.classList.add('counter-pop');
  });
}


function renderLabBasedGrade(grade) {
  if (!grade || !grade.grade) return '';
  const evidence = Array.isArray(grade.evidence) ? grade.evidence : [];
  const warnings = Array.isArray(grade.warnings) ? grade.warnings : [];
  const evHtml = evidence.length ? `<ul>${evidence.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '';
  const warnHtml = warnings.length ? `<ul>${warnings.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>` : '';
  const g = String(grade.grade || '');
  const tone = g.startsWith('A') ? 'good' : (g.startsWith('F') || g.startsWith('D') ? 'bad' : 'mid');
  return `
    <section class="lab-grade-card lab-grade-${tone}">
      <div class="lab-grade-main">
        <div>
          <span class="lab-grade-label">LAB-BASED TRADE GRADE</span>
          <strong class="lab-grade-letter">${escapeHtml(g)}</strong>
        </div>
        <div class="lab-grade-score">${fmtNum(grade.score)} / 100</div>
      </div>
      <div class="lab-grade-meta">Direction: <b>${escapeHtml(grade.direction || 'NEUTRAL')}</b> · Regime: <b>${escapeHtml(grade.regime || 'UNKNOWN')}</b>${grade.top_pattern ? ` · Pattern: <b>${escapeHtml(grade.top_pattern)}</b>` : ''}</div>
      <div class="lab-grade-note">${escapeHtml(grade.lab_basis || '')}</div>
      ${evHtml ? `<details open><summary>Why it scored well</summary>${evHtml}</details>` : ''}
      ${warnHtml ? `<details ${tone === 'bad' ? 'open' : ''}><summary>Grade warnings</summary>${warnHtml}</details>` : ''}
    </section>`;
}

function renderResults(d) {
  const labGradeHtml = renderLabBasedGrade(d.lab_based_grade || d.lab_grade || d.grade_lab_based);
  const tp    = d.trade_plan || {};
  const plan  = tp;
  window.plan = tp;
  const setup = d.setup || {};
  const preds = d.predictions || {};
  const vol   = d.volume_context || {};
  const mom   = d.momentum || {};
  const levels = d.levels || {};
  const bb    = d.bollinger || {};
  const macd  = d.macd || {};
  const stoch = d.stochastic || {};
  const referencePrice = Number(tp.entry_ideal ?? tp.entry_low ?? tp.entry_high);

  setText('rTicker',  d.ticker);
  setText('rCompany', d.company_name || '');
  setText('rPrice', Number.isFinite(referencePrice) ? fmt$(referencePrice) : '—');
  const chEl = document.getElementById('rChange');
  if (chEl) {
    chEl.textContent = Number.isFinite(referencePrice) ? 'MODEL ENTRY' : 'DERIVED';
    chEl.className = 'price-change';
  }
  setText('r52wHigh', fmt$(levels.resistance));
  setText('r52wLow',  fmt$(levels.support));
  setText('rAtrPct',  d.atr_pct ? `${d.atr_pct}%` : '—');

  const srcEl = document.getElementById('rDataSource');
  if (srcEl) {
    const tf = d.timeframe ? ` · ${String(d.timeframe).toUpperCase()}` : '';
    srcEl.textContent = `SERVER-SIDE DERIVED ANALYSIS${tf} · CHART BY TRADINGVIEW`;
  }

  const setupColor = setupColorMap(setup.setup_type, tp.direction);
  const setupEl    = document.getElementById('setupBadge');
  if (setupEl) {
    setupEl.textContent = (setup.setup_type || '—').replace(/_/g, ' ');
    setupEl.style.color = setupColor;
  }

  const signal     = tp.signal || 'HOLD';
  const bannerEl   = document.getElementById('signalBanner');
  const iconEl     = document.getElementById('signalIcon');
  const labelEl    = document.getElementById('signalLabel');

  const signalMap = {
    'STRONG_BUY':  { cls: 'strong-buy',  icon: '▲▲', label: 'STRONG BUY'  },
    'BUY':         { cls: 'buy',         icon: '▲',  label: 'BUY'         },
    'HOLD':        { cls: 'hold',        icon: '◆',  label: 'HOLD'        },
    'SELL':        { cls: 'sell',        icon: '▼',  label: 'SELL'        },
    'STRONG_SELL': { cls: 'strong-sell', icon: '▼▼', label: 'STRONG SELL' },
  };
  const sm = signalMap[signal] || signalMap['HOLD'];
  if (bannerEl) bannerEl.className = `signal-banner ${sm.cls}`;
  if (iconEl) iconEl.textContent = sm.icon;
  if (labelEl) labelEl.textContent = sm.label;

  const dirEl = document.getElementById('setupDirection');
  if (dirEl) {
    dirEl.textContent = tp.direction ? `◀ ${tp.direction} ▶` : '—';
    dirEl.style.color = tp.direction === 'LONG' ? 'var(--bull)' : tp.direction === 'SHORT' ? 'var(--bear)' : 'var(--neutral)';
  }

  const score = tp.quality_score || 0;
  setText('qualityScore',    score.toFixed(0));
  setText('qualityGrade',    tp.quality_grade || '—');
  setText('convictionLabel', tp.conviction || '—');

  const bar = document.getElementById('qualityBar');
  if (bar) {
    bar.style.width = `${score}%`;
    bar.style.background = score >= 70 ? 'var(--bull)' : score >= 50 ? 'var(--neutral)' : 'var(--bear)';
  }

  const cvEl = document.getElementById('convictionLabel');
  if (cvEl) cvEl.style.color = score >= 70 ? 'var(--bull)' : score >= 50 ? 'var(--neutral)' : 'var(--bear)';

  const conf = d.confluence || {};
  const agreeing = Number(conf.agreeing || 0);
  const total    = Number(conf.total || 7);
  const confDir  = conf.confirmation || 'MIXED';
  const confColor = confDir === 'BULLISH' ? 'var(--bull)' : confDir === 'BEARISH' ? 'var(--bear)' : 'var(--text-dim)';
  setText('confluenceCount', `${agreeing}/${total}`);
  const cdEl = document.getElementById('confluenceDir');
  if (cdEl) { cdEl.textContent = confDir; cdEl.style.color = confColor; }
  const dotsEl = document.getElementById('confluenceDots');
  if (dotsEl) {
    dotsEl.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const dot = document.createElement('span');
      dot.className = 'confluence-dot';
      dot.style.background = i < agreeing ? confColor : 'var(--border)';
      dotsEl.appendChild(dot);
    }
  }

  renderMARow('maRow9',   'EMA 9',   d.ema9,   referencePrice);
  renderMARow('maRow21',  'EMA 21',  d.ema21,  referencePrice);
  renderMARow('maRow20',  'SMA 20',  d.ma20,   referencePrice);
  renderMARow('maRow50',  'SMA 50',  d.ma50,   referencePrice);
  renderMARow('maRow200', 'SMA 200', d.ma200,  referencePrice);

  renderGauge('rsiGauge',   d.rsi14,     0, 100);
  renderGauge('stochGauge', stoch.k,     0, 100);
  renderGauge('bbGauge',    bb.pct,      0, 100);
  setText('rsiValue',  d.rsi14 ? d.rsi14.toFixed(1) : '—');
  setText('stochValue', stoch.k ? stoch.k.toFixed(1) : '—');
  setText('bbPct',     bb.pct  ? bb.pct.toFixed(1)  : '—');
  setText('macdHist',  macd.hist  ? (macd.hist > 0 ? '+' : '') + macd.hist.toFixed(3) : '—');

  renderOscSignal('rsiSignal',   rsiSignalText(d.rsi14),   rsiSignalClass(d.rsi14));
  renderOscSignal('stochSignal', stoch.signal || '—',  oscClass(stoch.signal));
  renderOscSignal('bbSignal',    bbSignalText(bb.pct),  bbSignalClass(bb.pct));
  renderOscSignal('macdSignal',  macd.cross || '—',    macdClass(macd.cross));

  const adxData  = d.adx         || {};
  const obvData  = d.obv         || {};
  const ichiData = d.ichimoku    || {};
  const adxVal   = adxData.value;
  const wrVal    = d.williams_r;
  const vwapVal  = d.vwap_20d;
  const emaCross = d.ema_cross   || '';
  const volDiv   = vol.price_divergence || 'NONE';

  renderGauge('adxGauge', adxVal != null ? Math.min(adxVal, 60) : null, 0, 60);
  setText('adxValue', adxVal != null ? adxVal.toFixed(1) : '—');
  renderOscSignal('adxSignal', adxData.trend || '—',
    adxVal >= 40 ? 'signal-bull' : adxVal >= 25 ? 'signal-neutral' : 'signal-bear');

  const dip = adxData.di_plus, dim = adxData.di_minus;
  if (dip != null && dim != null) {
    setText('diValue', `+${dip.toFixed(1)} / −${dim.toFixed(1)}`);
    renderOscSignal('diSignal', dip > dim ? 'BULLS LEAD' : 'BEARS LEAD',
      dip > dim ? 'signal-bull' : 'signal-bear');
  }

  renderGauge('wrGauge', wrVal != null ? (wrVal + 100) : null, 0, 100);
  setText('wrValue', wrVal != null ? wrVal.toFixed(1) : '—');
  const wrSig  = wrVal != null ? (wrVal >= -20 ? 'OVERBOUGHT' : wrVal <= -80 ? 'OVERSOLD' : 'NEUTRAL') : '—';
  const wrCls  = wrVal != null ? (wrVal >= -20 ? 'signal-bear' : wrVal <= -80 ? 'signal-bull' : 'signal-neutral') : '';
  renderOscSignal('wrSignal', wrSig, wrCls);

  setText('obvTrend', obvData.trend || '—');
  const obvCls = obvData.signal === 'CONFIRMING' ? 'signal-bull'
               : obvData.signal === 'DIVERGING'  ? 'signal-bear' : 'signal-neutral';
  renderOscSignal('obvSignal', obvData.signal || '—', obvCls);

  if (vwapVal != null) {
    setText('vwapValue', '$' + vwapVal.toFixed(2));
    const aboveVwap = d.above_vwap;
    renderOscSignal('vwapSignal', aboveVwap ? 'ABOVE' : 'BELOW',
      aboveVwap ? 'signal-bull' : 'signal-bear');
  } else {
    setText('vwapValue', '—');
  }

  const ichiSig = ichiData.signal || '';
  setText('ichiValue', ichiSig.replace('_', ' ') || '—');
  const ichiCls = ichiSig.includes('BULL') ? 'signal-bull'
                : ichiSig.includes('BEAR') ? 'signal-bear' : 'signal-neutral';
  renderOscSignal('ichiSignal', ichiSig || '—', ichiCls);

  setText('emaCrossValue', emaCross || '—');
  renderOscSignal('emaCrossSignal', emaCross,
    emaCross === 'BULLISH' ? 'signal-bull' : emaCross === 'BEARISH' ? 'signal-bear' : 'signal-neutral');

  const vdLabel = volDiv === 'BULLISH_DIVERGENCE' ? '⬆ BULL DIVERGENCE'
                : volDiv === 'BEARISH_DIVERGENCE' ? '⬇ BEAR DIVERGENCE' : 'NONE';
  const vdEl = document.getElementById('volDivValue');
  if (vdEl) {
    vdEl.textContent = vdLabel;
    vdEl.style.color = volDiv === 'BULLISH_DIVERGENCE' ? 'var(--bull)'
                     : volDiv === 'BEARISH_DIVERGENCE' ? 'var(--bear)' : 'var(--text-dim)';
  }

  const posSize = tp.position_size || null;
  const posSizeRow = document.getElementById('posSizeRow');
  if (posSize && posSize.shares && posSizeRow) {
    posSizeRow.style.display = '';
    setText('posSizeVal', `${posSize.shares} shares ($${posSize.position_value.toLocaleString()})`);
    setText('posSizeNote', posSize.note || '');
  } else if (posSizeRow) {
    posSizeRow.style.display = 'none';
  }
  const ratio = Number(vol.relative_ratio || 0);
  const volColor = ratio >= 2.0 ? 'var(--bull)' : ratio >= 1.5 ? 'var(--neutral)' : ratio > 0 && ratio < 0.8 ? 'var(--bear)' : 'var(--text-primary)';
  const ratioEl = document.getElementById('volRatio');
  if (ratioEl) {
    ratioEl.textContent = ratio > 0 ? `${ratio.toFixed(2)}×` : '—';
    ratioEl.style.color = volColor;
  }

  const participation = ratio >= 2 ? 'SURGE' : ratio >= 1.5 ? 'ELEVATED' : ratio > 0 && ratio < 0.8 ? 'LIGHT' : ratio > 0 ? 'NORMAL' : 'UNAVAILABLE';
  const sigTagEl = document.getElementById('volSignal');
  if (sigTagEl) {
    sigTagEl.textContent = participation;
    sigTagEl.style.background = volColor + '22';
    sigTagEl.style.color = volColor;
  }

  setText('volCurrent', participation);
  setText('volAvg', vol.price_divergence ? String(vol.price_divergence).replace(/_/g, ' ') : 'NONE');

  const vtEl = document.getElementById('volTrend');
  if (vtEl) {
    vtEl.textContent = vol.trend || '—';
    vtEl.style.color = vol.trend === 'INCREASING' ? 'var(--bull)' : vol.trend === 'DECLINING' ? 'var(--bear)' : 'var(--text-secondary)';
  }

  const sideNote = document.getElementById('tradePlanSide');
  const targetLabel = document.querySelector('.zone-target .zone-label');
  const stopLabel = document.querySelector('.zone-stop .zone-label');
  const entryLabel = document.querySelector('.zone-entry .zone-label');
  const tradeZones = document.querySelector('.trade-zones');

  if (tp.direction && tp.direction !== 'NEUTRAL') {
    const isShort = tp.direction === 'SHORT';
    const entry = Number(tp.entry_ideal);
    const target = Number(tp.target);
    const stop = Number(tp.stop);
    const validLong = tp.direction === 'LONG' && target > entry && stop < entry;
    const validShort = isShort && target < entry && stop > entry;

    if (targetLabel) targetLabel.textContent = isShort ? 'SHORT TARGET' : 'LONG TARGET';
    if (entryLabel) entryLabel.textContent = 'ENTRY ZONE';
    if (stopLabel) stopLabel.textContent = isShort ? 'STOP ABOVE' : 'STOP LOSS';

    if (tradeZones) tradeZones.className = `trade-zones ${isShort ? 'short' : 'long'}`;

    if (sideNote) {
      sideNote.className = `trade-plan-side ${isShort ? 'short' : 'long'}`;
      sideNote.textContent = isShort
        ? 'SHORT PLAN — profit target is below entry; stop is above entry.'
        : 'LONG PLAN — profit target is above entry; stop is below entry.';
    }

    const planIsValid = validLong || validShort;
    setText('planEntry',      fmt$(tp.entry_ideal));
    setText('planEntryRange', tp.entry_low ? `${fmt$(tp.entry_low)} – ${fmt$(tp.entry_high)}` : '—');
    setText('planStop',       fmt$(tp.stop));
    setText('planTarget',     fmt$(tp.target));
    setText('planStopPct',    tp.risk_pct   ? `−${tp.risk_pct}%` : '—');
    setText('planTargetPct',  tp.reward_pct ? `+${tp.reward_pct}%` : '—');
    const rrEl = document.getElementById('planRR');
    if (rrEl) {
      rrEl.textContent = planIsValid && tp.risk_reward ? `${tp.risk_reward}:1` : 'CHECK';
      rrEl.style.color = planIsValid
        ? (tp.risk_reward >= 2 ? 'var(--bull)' : tp.risk_reward >= 1.5 ? 'var(--neutral)' : 'var(--bear)')
        : 'var(--bear)';
    }

    const actionRow = document.getElementById('tradeActionRow');
    const paperBtn = document.getElementById('paperTradeBtn');
    if (actionRow) actionRow.style.display = planIsValid ? '' : 'none';
    if (paperBtn) paperBtn.onclick = planIsValid ? () => openPaperModal(d) : null;
  } else {
    if (tradeZones) tradeZones.className = 'trade-zones';
    if (targetLabel) targetLabel.textContent = 'TARGET';
    if (stopLabel) stopLabel.textContent = 'STOP LOSS';
    if (entryLabel) entryLabel.textContent = 'ENTRY ZONE';
    if (sideNote) {
      sideNote.className = 'trade-plan-side';
      sideNote.textContent = 'No active long/short trade plan for this scan.';
    }
    ['planEntry','planEntryRange','planStop','planTarget','planStopPct','planTargetPct'].forEach(id => setText(id, '—'));
    setText('planRR', 'N/A');
    const actionRow = document.getElementById('tradeActionRow');
    if (actionRow) actionRow.style.display = 'none';
  }

  renderPred('5d',  preds['5d']);
  renderPred('10d', preds['10d']);
  renderPred('20d', preds['20d']);

  const trendEl = document.getElementById('trendBadge');
  const isUp   = d.trend && d.trend.includes('UP');
  const isDown = d.trend && d.trend.includes('DOWN');
  if (trendEl) {
    trendEl.textContent = (d.trend || 'UNKNOWN').replace(/_/g,' ');
    trendEl.style.background = isUp ? 'var(--bull-dim)' : isDown ? 'var(--bear-dim)' : 'var(--neutral-dim)';
    trendEl.style.color = isUp ? 'var(--bull)' : isDown ? 'var(--bear)' : 'var(--neutral)';
    trendEl.style.padding = '4px 12px';
    trendEl.style.borderRadius = '4px';
  }

  setText('trendStrength', d.trend_strength != null ? `R²: ${d.trend_strength.toFixed(0)}%` : '');

  renderMomentumBars({ '5D': mom['5d'], '20D': mom['20d'], '60D': mom['60d'] });

  setText('levelR2',    fmt$(levels.resist_2));
  setText('levelR1',    fmt$(levels.resist_1));
  setText('levelPivot', fmt$(levels.pivot));
  setText('levelS1',    fmt$(levels.support_1));
  setText('levelS2',    fmt$(levels.support_2));

  renderPatterns(d.patterns || {});

  const rulesList = document.getElementById('rulesList');
  const rules     = setup.rules_fired || [];
  if (rulesList) {
    rulesList.innerHTML = rules.length
      ? rules.map(r => `<div class="rule-item"><span class="rule-bullet">▸</span>${escHtml(r)}</div>`).join('')
      : '<div class="rule-item" style="color:var(--text-dim)">No strong rules triggered.</div>';
  }

  const allScores = setup.all_scores || {};
  const winner    = setup.setup_type;
  const scoresGrid = document.getElementById('allScoresGrid');
  if (scoresGrid) {
    scoresGrid.innerHTML = Object.entries(allScores)
      .sort(([,a],[,b]) => b - a)
      .map(([name, sc]) => `
        <div class="score-chip ${name === winner ? 'winner' : ''}">
          <div class="score-chip-name">${name.replace(/_/g,' ')}</div>
          <div class="score-chip-val">${sc.toFixed(0)}</div>
          <div class="score-chip-bar-wrap"><div class="score-chip-bar" style="width:${sc}%"></div></div>
        </div>
      `).join('');
  }
}


function renderPatterns(patterns) {
  const list = document.getElementById('patternList');
  const count = document.getElementById('patternCount');
  const summaryRow = document.getElementById('patternSummaryRow');
  if (!list || !count || !summaryRow) return;

  const recent = patterns.recent || [];
  const summary = patterns.summary || {};
  const filter = summary.display_filter || {};
  count.textContent = `${recent.length || 0} SHOWN`;

  const byFamily = summary.by_family || {};
  const byDirection = summary.by_direction || {};
  summaryRow.innerHTML = [
    `FILTERED ${summary.displayed_patterns ?? recent.length ?? 0}`,
    `TOTAL ${summary.total_patterns || 0}`,
    `HIGH ${summary.high_confidence_count || 0}`,
    `1D ANY`,
    `30D HIGH ${filter.high_confidence || 70}%+`,
    `BULL ${byDirection.BULLISH || 0}`,
    `BEAR ${byDirection.BEARISH || 0}`,
    `FVG ${byFamily.FVG || 0}`,
    `CANDLE ${byFamily.CANDLE || 0}`,
  ].map(x => `<span class="pattern-summary-chip">${escHtml(x)}</span>`).join('');

  if (!recent.length) {
    list.innerHTML = '<div class="pattern-empty">No recent mid/high-confidence patterns detected on this scan.</div>';
    return;
  }

  const sorted = [...recent]
    .sort((a, b) => {
      const ai = Number(a.candle_index ?? 0);
      const bi = Number(b.candle_index ?? 0);
      const ac = Number(a.confidence ?? 0);
      const bc = Number(b.confidence ?? 0);
      return (bi - ai) || (bc - ac);
    })
    .slice(0, 8);

  list.innerHTML = sorted.map((p, idx) => {
    const direction = (p.direction || 'NEUTRAL').toLowerCase();
    const family = p.pattern_family || 'PATTERN';
    const name = (p.pattern_name || 'UNKNOWN').replace(/_/g, ' ');
    const conf = Number(p.confidence || 0).toFixed(0);
    const date = formatPatternDate(p.timestamp);
    const zone = (p.zone_low != null && p.zone_high != null)
      ? `<div class="pattern-zone">ZONE ${fmt$(p.zone_low)} – ${fmt$(p.zone_high)}</div>`
      : '';
    const data = encodeURIComponent(JSON.stringify(p));
    return `
      <button type="button" class="pattern-item ${direction}" data-pattern="${data}" onclick="focusPatternFromCard(this)">
        <div class="pattern-head">
          <div class="pattern-name">${escHtml(name)}</div>
          <div class="pattern-confidence">${conf}%</div>
        </div>
        <div class="pattern-meta">
          <span class="pattern-tag">${escHtml(family)}</span>
          <span class="pattern-tag">${escHtml(p.direction || 'NEUTRAL')}</span>
          <span class="pattern-tag">${escHtml(date)}</span>
        </div>
        ${zone}
        <div class="pattern-click-hint">${escHtml(p.display_reason || 'CLICK TO LOCATE DATE')}</div>
      </button>
    `;
  }).join('');
}

function formatPatternDate(value) {
  if (!value) return 'DATE —';
  const raw = String(value).slice(0, 10);
  const date = new Date(`${raw}T12:00:00`);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

window.focusPatternFromCard = function(el) {
  try {
    const p = JSON.parse(decodeURIComponent(el.dataset.pattern || '{}'));
    focusPatternOnChart(p);
  } catch (err) {
    console.error('Failed to focus pattern', err);
  }
};

function focusPatternOnChart(p) {
  const chartCard = document.querySelector('.card-chart');
  const focusBox = document.getElementById('patternFocus');
  const focusText = document.getElementById('patternFocusText');
  const focusLink = document.getElementById('patternFocusLink');
  if (!focusBox || !focusText || !focusLink) return;

  document.querySelectorAll('.pattern-item.active').forEach(x => x.classList.remove('active'));
  const encoded = encodeURIComponent(JSON.stringify(p));
  const active = document.querySelector(`.pattern-item[data-pattern="${encoded}"]`);
  if (active) active.classList.add('active');

  const name = (p.pattern_name || 'Pattern').replace(/_/g, ' ');
  const direction = p.direction || 'NEUTRAL';
  const family = p.pattern_family || 'PATTERN';
  const conf = Number(p.confidence || 0).toFixed(0);
  const date = formatPatternDate(p.timestamp);
  const zone = (p.zone_low != null && p.zone_high != null)
    ? ` Zone: ${fmt$(p.zone_low)} – ${fmt$(p.zone_high)}.`
    : '';
  const trigger = p.trigger_price != null ? ` Trigger: ${fmt$(p.trigger_price)}.` : '';

  focusText.textContent = `${name} (${family}, ${direction}, ${conf}%). Look at the candle dated ${date}.${zone}${trigger}`;
  const ticker = (currentAnalysis && currentAnalysis.ticker) ? currentAnalysis.ticker : (document.getElementById('tickerInput')?.value || '').toUpperCase();
  const tvSymbol = ticker ? normalizeTVSymbol(ticker) : '';
  focusLink.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}`;
  focusLink.textContent = ticker ? `OPEN ${ticker} CHART ↗` : 'OPEN CHART ↗';
  focusBox.classList.remove('hidden');

  if (chartCard) {
    chartCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function loadTradingView(ticker, interval, exchange = "") {
  const container = document.getElementById('tradingview_widget');
  if (!container) return;
  const tvSymbol = normalizeTVSymbol(ticker, exchange);
  container.innerHTML = '';

  const widget = document.createElement('div');
  widget.className = 'tradingview-widget-container';
  widget.style.cssText = 'height:100%;width:100%;background:#07111f;border-radius:16px;overflow:hidden;';
  widget.innerHTML = `<div class="tradingview-widget-container__widget" style="height:calc(100% - 28px);width:100%"></div>
    <div class="tradingview-widget-copyright" style="height:28px;display:flex;align-items:center;justify-content:center;background:#07111f;font:10px var(--font-body);">
      <a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tvSymbol)}" rel="noopener nofollow" target="_blank" style="color:#38cff3;text-decoration:none;font-weight:700;">${escapeHtml(ticker)} chart by TradingView</a>
    </div>`;
  const script = document.createElement('script');
  script.type = 'text/javascript';
  script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
  script.async = true;
  script.text = JSON.stringify({
    autosize: true,
    symbol: tvSymbol,
    interval: interval || 'D',
    timezone: 'exchange',
    theme: 'dark',
    style: '1',
    locale: 'en',
    backgroundColor: '#07111F',
    gridColor: 'rgba(56, 207, 243, 0.08)',
    hide_top_toolbar: true,
    hide_side_toolbar: true,
    hide_legend: true,
    hide_volume: true,
    withdateranges: false,
    allow_symbol_change: false,
    save_image: false,
    details: false,
    hotlist: false,
    calendar: false,
    support_host: 'https://www.tradingview.com'
  });
  widget.appendChild(script);
  container.appendChild(widget);
}

function normalizeTVSymbol(ticker, exchange = "") {
  const cleanTicker = String(ticker || '').toUpperCase().trim();
  if (!cleanTicker) return '';
  if (cleanTicker.includes(':')) return cleanTicker;
  const cryptoMap = {'BTC':'COINBASE:BTCUSD','BTC-USD':'COINBASE:BTCUSD','ETH':'COINBASE:ETHUSD','ETH-USD':'COINBASE:ETHUSD'};
  if (cryptoMap[cleanTicker]) return cryptoMap[cleanTicker];

  const cleanExchange = String(exchange || '').toUpperCase();
  if (/NASDAQ|NMS|NGM|NCM/.test(cleanExchange)) return `NASDAQ:${cleanTicker}`;
  if (/NYSE|NYQ/.test(cleanExchange)) return `NYSE:${cleanTicker}`;
  if (/AMEX|ASE/.test(cleanExchange)) return `AMEX:${cleanTicker}`;
  if (/ARCA/.test(cleanExchange)) return `AMEX:${cleanTicker}`;

  
  
  return cleanTicker;
}

async function generateAI(analysis, question = null) {
  const textEl    = document.getElementById('aiText');
  const loadingEl = document.getElementById('aiLoading');

  textEl.style.display    = 'none';
  loadingEl.style.display = 'flex';

  try {
    const result = await API.explain(analysis.ticker, analysis, question);
    const raw    = result.explanation || 'No explanation available.';

    textEl.innerHTML       = formatAIText(raw);
    textEl.style.display   = '';
    loadingEl.style.display = 'none';
  } catch (e) {
    textEl.textContent     = 'AI analysis unavailable right now. Using the dashboard signals instead.';
    textEl.style.display   = '';
    loadingEl.style.display = 'none';
  }
}

function formatAIText(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text-primary)">$1</strong>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g,   '<br/>')
    .replace(/(\$[\d,.]+)/g, '<span style="color:var(--accent-primary)">$1</span>')
    .replace(/(LONG|BULLISH|BUY)/g, '<span style="color:var(--bull)">$1</span>')
    .replace(/(SHORT|BEARISH|SELL)/g, '<span style="color:var(--bear)">$1</span>');
}


function initDevTools() {
  const panel = document.getElementById('devLabPanel');
  const select = document.getElementById('patternEngineSelect');
  const closeBtn = document.getElementById('devLabCloseBtn');
  const runBtn = document.getElementById('runPatternLabBtn');
  const stopLabBtn = document.getElementById('stopPatternLabBtn');
  const resumeLabBtn = document.getElementById('resumePatternLabBtn');
  const warmBtn = document.getElementById('warmPatternCacheBtn');
  const cacheStatusBtn = document.getElementById('patternCacheStatusBtn');
  const load150Btn = document.getElementById('loadTraining150Btn');
  const trainVAIBtn = document.getElementById('trainVAIBtn');
  const vaiStatusBtn = document.getElementById('vaiModelStatusBtn');
  const badge = document.querySelector('.beta-version-badge') || document.querySelector('.beta-badge');
  let badgeClicks = 0;

  if (select) {
    if (!Array.from(select.options).some(opt => opt.value === currentPatternMode)) {
      // Pattern Lab has its own comparison catalog. Keep the public workspace
      // on V8 when a stale or private-only mode is encountered.
      if (currentPatternMode !== 'minerva_v1') {
        currentPatternMode = 'v8';
        safeStorageSet('oryntra_pattern_engine_mode', 'v8');
      }
    }
    select.value = Array.from(select.options).some(opt => opt.value === currentPatternMode) ? currentPatternMode : 'v8';
    if (select.value !== currentPatternMode) {
      currentPatternMode = 'v8';
      select.value = 'v8';
      safeStorageSet('oryntra_pattern_engine_mode', 'v8');
    }
    select.addEventListener('change', () => {
      currentPatternMode = select.value || 'v8';
      safeStorageSet('oryntra_pattern_engine_mode', currentPatternMode);
      if (['official','v8','vai2'].includes(currentPatternMode)) updateSettingsEngineDisplay();
      const settingsSelect = document.getElementById('settingsEngineSelect'); if (settingsSelect && ['official','v8','vai2'].includes(currentPatternMode)) settingsSelect.value = currentPatternMode;
      updatePatternModePill();
      if (currentTicker) showError(`Pattern engine set to ${currentPatternMode.toUpperCase()}. Run the scan again to compare.`);
      setTimeout(hideError, 2800);
    });
  }

  if (closeBtn) closeBtn.addEventListener('click', () => {
    if (!panel) return;
    panel.classList.add('dev-hidden');
    badge?.setAttribute('aria-expanded', 'false');
    lastDevLabFocus?.focus();
    lastDevLabFocus = null;
  });
  if (runBtn) runBtn.addEventListener('click', runPatternLab);
  if (stopLabBtn) stopLabBtn.addEventListener('click', stopPatternLab);
  if (resumeLabBtn) resumeLabBtn.addEventListener('click', resumePatternLab);
  if (warmBtn) warmBtn.addEventListener('click', warmPatternCache);
  if (cacheStatusBtn) cacheStatusBtn.addEventListener('click', showPatternCacheStatus);
  if (load150Btn) load150Btn.addEventListener('click', loadTraining150Tickers);
  if (trainVAIBtn) trainVAIBtn.addEventListener('click', trainVAIModel);
  if (vaiStatusBtn) vaiStatusBtn.addEventListener('click', showVAIModelStatus);

  const savedPatternJob = safeStorageGet(PATTERN_LAB_ACTIVE_KEY);
  if (savedPatternJob) {
    currentPatternLabJobId = savedPatternJob;
    pollPatternLabJob(savedPatternJob);
  }

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && String(e.key).toLowerCase() === 'd') {
      e.preventDefault();
      toggleDevLab();
    }
  });

  if (badge) {
    badge.style.cursor = 'pointer';
    badge.title = 'Developer menu: Ctrl+Shift+D or click 5 times';
    badge.addEventListener('keydown', event => {
      if (!['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      badgeClicks = 0;
      toggleDevLab();
    });
    badge.addEventListener('click', () => {
      badgeClicks += 1;
      if (badgeClicks >= 5) {
        badgeClicks = 0;
        toggleDevLab();
      }
    });
  }
  updatePatternModePill();
}

function toggleDevLab() {
  const panel = document.getElementById('devLabPanel');
  if (!panel) return;
  const badge = document.querySelector('.beta-version-badge') || document.querySelector('.beta-badge');
  const opening = panel.classList.contains('dev-hidden');
  if (opening) lastDevLabFocus = document.activeElement;
  panel.classList.toggle('dev-hidden');
  badge?.setAttribute('aria-expanded', String(!panel.classList.contains('dev-hidden')));
  if (!panel.classList.contains('dev-hidden')) {
    panel.scrollIntoView({behavior:'smooth', block:'start'});
    panel.focus({preventScroll:true});
  } else {
    lastDevLabFocus?.focus();
    lastDevLabFocus = null;
  }
}

function updatePatternModePill() {
  let pill = document.getElementById('patternModePill');
  const target = document.querySelector('.beta-version-badge') || document.querySelector('.beta-badge');
  if (!target) return;
  if (!pill) {
    pill = document.createElement('span');
    pill.id = 'patternModePill';
    pill.className = 'pattern-mode-pill';
    target.insertAdjacentElement('afterend', pill);
  }
  pill.textContent = `ENGINE: ${engineLabel(currentPatternMode)}`;
}


function engineLabel(mode) {
  const labels = {v8: 'V8', minerva_baseline: 'MINERVA · PRO/MAX', minerva_v1: 'MINERVA · PRO'};
  return labels[String(mode || '').toLowerCase()] || 'V8';
}

function setAppEngine(mode, opts={}) {
  const allowed = ['v8'];
  const next = allowed.includes(String(mode || '').toLowerCase()) ? String(mode).toLowerCase() : 'v8';
  currentPatternMode = next;
  safeStorageSet('oryntra_pattern_engine_mode', next);
  safeStorageSet(WORKSPACE_MODEL_KEY, next);
  syncWorkspaceModelControls(next);
  const devSelect = document.getElementById('patternEngineSelect');
  if (devSelect && Array.from(devSelect.options).some(o => o.value === next)) devSelect.value = next;
  const settingsSelect = document.getElementById('settingsEngineSelect');
  if (settingsSelect) settingsSelect.value = next;
  updatePatternModePill();
  updateSettingsEngineDisplay();
  if (opts.notice && currentTicker) {
    showError(`Engine set to ${engineLabel(next)}. Run the scan again to compare.`);
    setTimeout(hideError, 2600);
  }
}

function initSettingsPage() {
  const select = document.getElementById('settingsEngineSelect');
  if (select) {
    select.value = 'v8';
    select.addEventListener('change', () => setAppEngine(select.value, {notice:true}));
  }
  initQuantSettings();
  initProviderCredentialSettings();
  initThemeSettings();
  document.getElementById('settingsDeleteAccountBtn')?.addEventListener('click', deleteCurrentAccount);
  updateSettingsEngineDisplay();
}

async function deleteCurrentAccount() {
  if (!currentUser) { openAuthModal('login'); return; }
  const password = window.prompt('Enter your password to permanently delete this Oryntra account. This cannot be undone.');
  if (!password) return;
  try {
    await API.auth.deleteAccount(password);
    await Promise.all(['polygon', 'twelvedata'].map(provider => persistBrowserProviderKey(provider, ''))).catch(() => {});
    await logoutUser();
    window.alert('Your account has been deleted.');
  } catch (error) {
    window.alert(error.message || 'Account deletion could not be completed.');
  }
}

function setProviderCredentialMessage(text, warning=false) {
  const message = document.getElementById('providerCredentialMessage');
  if (!message) return;
  message.textContent = text;
  message.classList.toggle('is-warning', Boolean(warning));
}

function renderProviderCredentialSettings() {
  ['polygon', 'twelvedata'].forEach(provider => {
    const status = document.getElementById(`${provider}CredentialStatus`);
    if (status) status.textContent = browserProviderKeys[provider] ? 'CONNECTED · THIS BROWSER' : 'NOT CONNECTED';
  });
  setProviderCredentialMessage('Keys are saved only on this browser device and sent directly to your selected provider. Oryntra never receives or stores them.');
}

async function refreshProviderCredentialSettings() {
  renderProviderCredentialSettings();
}

function initProviderCredentialSettings() {
  const save = async provider => {
    if (!currentUser) { openAuthModal('login'); return; }
    const input = document.getElementById(`${provider}ApiKeyInput`);
    const apiKey = input?.value || '';
    if (!apiKey.trim()) { setProviderCredentialMessage(`Paste your ${provider === 'polygon' ? 'Polygon' : 'Twelve Data'} API key first.`, true); return; }
    const previous = browserProviderKeys[provider];
    browserProviderKeys[provider] = apiKey.trim();
    try {
      setProviderCredentialMessage(`Verifying ${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} with a completed SPY daily candle…`);
      await fetchDirectMarketBars('SPY', '1y', provider, 1);
      await persistBrowserProviderKey(provider, browserProviderKeys[provider]);
    } catch (error) {
      browserProviderKeys[provider] = previous;
      setProviderCredentialMessage(error.message || 'The provider could not return a completed SPY daily OHLCV candle for this key.', true);
      return;
    }
    if (input) input.value = '';
    renderProviderCredentialSettings();
    setProviderCredentialMessage(`${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} is saved on this browser device only.`);
  };
  const remove = async provider => {
    if (!currentUser) { openAuthModal('login'); return; }
    browserProviderKeys[provider] = '';
    try {
      await persistBrowserProviderKey(provider, '');
    } catch (error) {
      setProviderCredentialMessage(error.message || 'This browser could not remove the saved key.', true);
      return;
    }
    renderProviderCredentialSettings();
    setProviderCredentialMessage(`${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'} key removed from this browser device.`);
  };
  document.getElementById('savePolygonApiKeyBtn')?.addEventListener('click', () => save('polygon'));
  document.getElementById('saveTwelvedataApiKeyBtn')?.addEventListener('click', () => save('twelvedata'));
  document.getElementById('removePolygonApiKeyBtn')?.addEventListener('click', () => remove('polygon'));
  document.getElementById('removeTwelvedataApiKeyBtn')?.addEventListener('click', () => remove('twelvedata'));
  refreshProviderCredentialSettings();
}

function initQuantSettings() {
  [['settingsQuantProvider', 'quantProvider', 'oryntra_quant_provider'], ['settingsQuantModel', 'quantModel', 'oryntra_quant_model']].forEach(([settingsId, quantId, storageKey]) => {
    const settings = document.getElementById(settingsId), quant = document.getElementById(quantId);
    if (!settings || !quant) return;
    const saved = safeStorageGet(storageKey);
    if (saved && Array.from(settings.options).some(option => option.value === saved)) {
      settings.value = saved; quant.value = saved;
      if (quantId === 'quantModel') quant.dispatchEvent(new Event('change'));
    }
    settings.addEventListener('change', () => { quant.value = settings.value; safeStorageSet(storageKey, settings.value); quant.dispatchEvent(new Event('change')); });
    quant.addEventListener('change', () => { settings.value = quant.value; safeStorageSet(storageKey, quant.value); });
  });
}

function updateSettingsEngineDisplay() {
  const el = document.getElementById('settingsCurrentEngine');
  if (el) el.textContent = `ENGINE: ${engineLabel(currentPatternMode)}`;
}

function copyTerminalText(elementId) {
  const el = document.getElementById(elementId);
  const text = el ? (el.innerText || el.textContent || '') : '';
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showCopyToast('Copied terminal output.')).catch(() => fallbackCopyText(text));
  } else {
    fallbackCopyText(text);
  }
}

function fallbackCopyText(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try { document.execCommand('copy'); showCopyToast('Copied terminal output.'); } catch (_) {}
  document.body.removeChild(ta);
}

function showCopyToast(message) {
  const banner = document.getElementById('errorBanner');
  const text = document.getElementById('errorText');
  if (banner && text) {
    text.textContent = message;
    banner.style.display = 'flex';
    setTimeout(() => { banner.style.display = 'none'; }, 1800);
  }
}

function terminalBlock(id, text, title='COPY TERMINAL') {
  return `<div class="copy-terminal-card"><div class="copy-terminal-head"><strong>${escapeHtml(title)}</strong><button class="dev-close-btn" type="button" onclick="copyTerminalText('${id}')">COPY TERMINAL</button></div><pre id="${id}" class="copy-terminal-pre">${escapeHtml(text || '')}</pre></div>`;
}


async function syncDocumentedBetaCounter() {
  try {
    const res = await fetch('/api/dev/counters/sync-beta-counts', {method: 'POST'});
    if (!res.ok) throw new Error(`Counter sync failed (${res.status})`);
    const data = await res.json();
    const after = data.after || {};
    if (Number.isFinite(Number(after.stock_searches))) {
      updateSearchCounter(Number(after.stock_searches), {persist: true, animate: true});
    }
    const out = document.getElementById('patternLabOutput');
    if (out) {
      out.innerHTML = `<div class="dev-lab-note">Beta counter synced. Market analyses: ${Number(after.stock_searches || 0).toLocaleString()} · Engine checks: ${Number(after.pattern_lab_engine_checks || 0).toLocaleString()}</div>` + out.innerHTML;
    }
    return data;
  } catch (err) {
    const out = document.getElementById('patternLabOutput');
    if (out) out.innerHTML = `<div class="dev-error">${escapeHtml(err.message || String(err))}</div>` + out.innerHTML;
    throw err;
  }
}

function getSelectedPatternLabEngines() {
  const boxes = Array.from(document.querySelectorAll('#patternLabEngineChecks input[type="checkbox"]'));
  let selected = boxes.filter(b => b.checked).map(b => String(b.value || '').trim()).filter(Boolean);
  if (!selected.length) {
    selected = ['official'];
    const v5 = boxes.find(b => b.value === 'official');
    if (v5) v5.checked = true;
  }
  return selected;
}

function selectedPatternLabEnginesLabel(engineModes) {
  const labels = {official:'V1.0 Official', v8:'V1.0 Research', vai2:'V1.0 Quant'};
  return (engineModes || []).map(m => labels[m] || String(m).toUpperCase()).join(' vs ');
}

async function runPatternLab() {
  const out = document.getElementById('patternLabOutput');
  if (!out) return;
  if (currentPatternLabJobId) {
    showCopyToast(currentPatternLabJobId === JOB_STARTING ? 'Pattern Lab is starting.' : 'A Pattern Lab job is already running.');
    return;
  }
  const tickers = (document.getElementById('patternLabTickers')?.value || '')
    .split(',').map(t => sanitizeTickerSymbol(t)).filter(Boolean);
  const period = document.getElementById('patternLabPeriod')?.value || '5y';
  const horizon_days = Number(document.getElementById('patternLabHorizon')?.value || 10);
  const step = Number(document.getElementById('patternLabStep')?.value || 5);
  const max_tests_per_ticker = Number(document.getElementById('patternLabMaxTests')?.value || 40);
  const data_source = document.getElementById('patternLabDataSource')?.value || 'cache_only';
  const engine_modes = getSelectedPatternLabEngines();
  const universe_mode = document.getElementById('patternLabUniverseMode')?.value || 'manual';
  const universe_size = Number(document.getElementById('patternLabUniverseSize')?.value || 150);
  const random_seed = Number(document.getElementById('patternLabSeed')?.value || 73021);
  const sampling_mode = document.getElementById('patternLabSamplingMode')?.value || 'even';
  const random_window_bars = Number(document.getElementById('patternLabRandomWindowBars')?.value || 180);
  const start_date = document.getElementById('patternLabStartDate')?.value || '';
  const end_date = document.getElementById('patternLabEndDate')?.value || '';
  const transaction_cost_bps = Number(document.getElementById('patternLabTransactionCost')?.value || 6);
  const slippage_bps = Number(document.getElementById('patternLabSlippage')?.value || 4);
  const walk_forward_folds = Number(document.getElementById('patternLabWalkForwardFolds')?.value || 5);
  const bootstrap_samples = Number(document.getElementById('patternLabBootstrapSamples')?.value || 500);
  const payload = {
    tickers, period, horizon_days, step, max_tests_per_ticker, data_source, engine_modes,
    universe_mode, universe_size, random_seed, sampling_mode, random_window_bars,
    start_date, end_date, transaction_cost_bps, slippage_bps,
    walk_forward_folds, bootstrap_samples,
    api_delay_seconds: Number(document.getElementById('patternCacheDelay')?.value || 13),
  };

  currentPatternLabJobId = JOB_STARTING;
  lastPatternLabProgressPct = 0;
  lastPatternLabCompletedTickers = 0;
  out.innerHTML = renderPatternLabProgressCard({
    status: 'queued',
    phase: 'starting',
    progress_pct: 0,
    total_tickers: universe_mode === 'manual' ? tickers.length : universe_size,
    completed_tickers: 0,
    current_ticker: '—',
    message: `Starting ${selectedPatternLabEnginesLabel(engine_modes)} test from ${data_source}.`
  });

  try {
    const job = await API.dev.patternLabStart(payload);
    const jobId = String(job?.job_id || '').trim();
    if (!jobId) throw new Error('Pattern Lab did not return a job ID.');
    currentPatternLabJobId = jobId;
    safeStorageSet(PATTERN_LAB_ACTIVE_KEY, jobId);
    safeStorageSet(PATTERN_LAB_LAST_KEY, jobId);
    renderPatternLabProgress(job);
    pollPatternLabJob(jobId);
  } catch (err) {
    currentPatternLabJobId = null;
    out.innerHTML = `<div class="dev-empty">Pattern lab failed to start: ${escapeHtml(String(err))}</div>`;
  }
}

async function pollPatternLabJob(jobId) {
  if (!jobId || currentPatternLabJobId !== jobId) return;
  try {
    const job = await API.dev.patternLabStatus(jobId);
    if (currentPatternLabJobId !== jobId) return;
    renderPatternLabProgress(job);
    if (job.status === 'queued' || job.status === 'running' || job.status === 'stopping') {
      setTimeout(() => pollPatternLabJob(jobId), 1500);
    } else if (job.status === 'done' || job.status === 'stopped') {
      renderPatternLabResults(job.result || job);
      safeStorageSet(PATTERN_LAB_LAST_KEY, jobId);
      localStorage.removeItem(PATTERN_LAB_ACTIVE_KEY);
      currentPatternLabJobId = null;
    } else if (job.status === 'failed' || job.status === 'not_found') {
      safeStorageSet(PATTERN_LAB_LAST_KEY, jobId);
      localStorage.removeItem(PATTERN_LAB_ACTIVE_KEY);
      currentPatternLabJobId = null;
    }
  } catch (err) {
    if (currentPatternLabJobId !== jobId) return;
    const out = document.getElementById('patternLabOutput');
    if (out) out.innerHTML = `<div class="dev-empty">Pattern lab progress check failed: ${escapeHtml(String(err))}. Retrying...</div>`;
    setTimeout(() => pollPatternLabJob(jobId), 5000);
  }
}

async function stopPatternLab() {
  if (!currentPatternLabJobId || currentPatternLabJobId === JOB_STARTING) {
    showCopyToast(currentPatternLabJobId === JOB_STARTING ? 'Pattern Lab is still starting.' : 'No Pattern Lab job is running.');
    return;
  }
  try {
    const job = await API.dev.patternLabStop(currentPatternLabJobId);
    renderPatternLabProgress(job);
    pollPatternLabJob(currentPatternLabJobId);
  } catch (err) {
    showCopyToast(`Could not stop Pattern Lab: ${err.message || String(err)}`);
  }
}

async function resumePatternLab() {
  if (currentPatternLabJobId) {
    showCopyToast('A Pattern Lab job is already active.');
    return;
  }
  const prior = safeStorageGet(PATTERN_LAB_LAST_KEY);
  if (!prior) {
    showCopyToast('No saved Pattern Lab checkpoint is available.');
    return;
  }
  try {
    const job = await API.dev.patternLabResume(prior);
    if (!job?.job_id || job.status === 'not_found') throw new Error(job?.message || 'No checkpoint is available.');
    currentPatternLabJobId = String(job.job_id);
    safeStorageSet(PATTERN_LAB_ACTIVE_KEY, currentPatternLabJobId);
    safeStorageSet(PATTERN_LAB_LAST_KEY, currentPatternLabJobId);
    renderPatternLabProgress(job);
    pollPatternLabJob(currentPatternLabJobId);
  } catch (err) {
    showCopyToast(`Could not resume Pattern Lab: ${err.message || String(err)}`);
  }
}

function renderPatternLabProgress(job) {
  const out = document.getElementById('patternLabOutput');
  if (!out || !job) return;
  if (job.status === 'done' && job.result) {
    renderPatternLabResults(job.result);
    return;
  }
  out.innerHTML = renderPatternLabProgressCard(job);
}

function renderPatternLabProgressCard(job) {
  let pct = Math.max(0, Math.min(100, Number(job.progress_pct || 0)));
  const completedTickers = Number(job.completed_tickers || 0);
  if (job.status === 'done') {
    pct = 100;
  } else {
    pct = Math.max(pct, lastPatternLabProgressPct || 0);
  }
  lastPatternLabProgressPct = pct;
  lastPatternLabCompletedTickers = Math.max(lastPatternLabCompletedTickers || 0, completedTickers);
  const totalTickers = Number(job.total_tickers || (Array.isArray(job.tickers) ? job.tickers.length : 0));
  const completedChecks = Number(job.completed_checks || 0);
  const totalChecks = Number(job.total_checks_estimated || 0);
  const status = String(job.status || 'running').toUpperCase();
  const phase = String(job.phase || '').replace(/_/g, ' ').toUpperCase();
  const current = escapeHtml(job.current_ticker || '—');
  const date = escapeHtml(job.current_date || '—');
  const message = escapeHtml(job.message || 'Running pattern test...');
  const started = job.started_at ? new Date(job.started_at).getTime() : Date.now();
  const elapsedSec = Number.isFinite(Number(job.elapsed_seconds)) ? Math.round(Number(job.elapsed_seconds)) : Math.max(0, Math.round((Date.now() - started) / 1000));
  const etaSec = Math.max(0, Math.round(Number(job.eta_seconds || 0)));
  const errors = Array.isArray(job.ticker_errors) && job.ticker_errors.length
    ? `<div class="dev-lab-note">Recent errors: ${job.ticker_errors.slice(-4).map(e => `${escapeHtml(e.ticker || '')}: ${escapeHtml(e.error || '')}`).join(' · ')}</div>` : '';
  const policy = job.resource_policy || {};
  const workerNote = job.worker_pid
    ? `<div class="dev-lab-note">Worker PID ${escapeHtml(String(job.worker_pid))} · CPU share ${Math.round(Number(policy.cpu_share || 0.30) * 100)}% · nice ${escapeHtml(String(policy.nice ?? 12))} · checkpoint ${job.checkpoint_available ? 'available' : 'pending'}</div>`
    : '';

  return `
    <div class="dev-cache-card pattern-test-progress-card">
      <div class="dev-summary-title">PATTERN TEST: ${escapeHtml(status)} ${phase ? '· ' + escapeHtml(phase) : ''}</div>
      <div class="dev-progress big"><span style="width:${pct}%"></span></div>
      <div class="dev-summary-metric"><span>Progress</span><strong>${pct.toFixed(2)}%</strong></div>
      <div class="dev-summary-metric"><span>Tickers</span><strong>${Math.max(completedTickers, lastPatternLabCompletedTickers || 0)}/${totalTickers}</strong></div>
      <div class="dev-summary-metric"><span>Engine checks</span><strong>${completedChecks}${totalChecks ? '/' + totalChecks : ''}</strong></div>
      <div class="dev-summary-metric"><span>Current ticker</span><strong>${current}</strong></div>
      <div class="dev-summary-metric"><span>Current date</span><strong>${date}</strong></div>
      <div class="dev-summary-metric"><span>Elapsed</span><strong>${elapsedSec}s</strong></div>
      <div class="dev-summary-metric"><span>ETA</span><strong>${etaSec ? etaSec + 's' : 'calculating'}</strong></div>
      <div class="dev-lab-note">${message}</div>
      ${workerNote}
      ${errors}
    </div>`;
}


function applyPatternLabCounterUpdate(res) {
  const cu = res && res.counter_update ? res.counter_update : null;
  if (!cu) return '';
  const total = Number(cu.total_stock_searches);
  if (Number.isFinite(total)) {
    updateSearchCounter(total, {persist: true});
  }
  const added = Number(cu.added_stock_analyses || 0);
  const checks = Number(cu.added_engine_checks || 0);
  if (!added && !checks) return '';
  return ` · Counter synced: ${added.toLocaleString()} market-analysis runs${checks ? ' / ' + checks.toLocaleString() + ' engine checks' : ''}`;
}


function buildPatternLabTerminal(res) {
  const lines = [];
  lines.push('ORYNTRA PATTERN LAB TERMINAL REPORT');
  lines.push('='.repeat(64));
  lines.push(`Generated: ${res.generated_at || new Date().toISOString()}`);
  const p = res.params || {};
  lines.push(`Tickers: ${(p.tickers || []).join(',')}`);
  lines.push(`Period: ${p.period || 'unknown'} | Horizon: ${p.horizon_days || '?'} | Step: ${p.step || '?'} | Max/ticker: ${p.max_tests_per_ticker || '?'}`);
  lines.push(`Engines: ${(p.engine_modes || []).map(m => String(m).toUpperCase()).join(', ')}`);
  lines.push(`Sampling: ${p.sampling_mode || 'unknown'} | Seed: ${p.random_seed ?? '?'} | Window bars: ${p.random_window_bars ?? '?'}`);
  lines.push(`Costs: ${p.transaction_cost_bps ?? 0} bps transaction + ${p.slippage_bps ?? 0} bps slippage | Evaluation profiles persisted: ${p.persist_evaluation_profiles === true}`);
  lines.push('');
  lines.push('SUMMARY');
  lines.push('Mode | Tests | Actionable | Coverage | Win % | Avg Return % | MFE/MAE | Target % | Stop %');
  (res.summary || []).forEach(s => lines.push(`${String(s.mode).toUpperCase()} | ${s.tests} | ${s.actionable} | ${fmtNum(s.coverage_pct)} | ${fmtNum(s.win_rate_pct)} | ${fmtNum(s.avg_return_pct)} | ${fmtNum(s.reward_risk_ratio)} | ${fmtNum(s.target_hit_rate_pct)} | ${fmtNum(s.stop_hit_rate_pct)}`));
  lines.push('');
  lines.push('BASELINES');
  lines.push('Mode | Tests | Win % | Avg Return % | MFE % | MAE %');
  (res.baselines || []).forEach(b => lines.push(`${String(b.mode).toUpperCase()} | ${b.tests} | ${fmtNum(b.win_rate_pct)} | ${fmtNum(b.avg_return_pct)} | ${fmtNum(b.avg_mfe_pct)} | ${fmtNum(b.avg_mae_pct)}`));
  lines.push('');
  lines.push('TOP TICKERS');
  (res.ticker_level || []).slice(0, 30).forEach(r => lines.push(`${String(r.mode).toUpperCase()} ${r.ticker}: signals=${r.signals ?? r.actionable ?? 0}, win=${fmtNum(r.win_rate_pct)}%, return=${fmtNum(r.avg_return_pct)}%, MFE=${fmtNum(r.avg_mfe_pct)}%, MAE=${fmtNum(r.avg_mae_pct)}%`));
  lines.push('');
  lines.push('TOP PATTERNS');
  (res.pattern_level || []).slice(0, 30).forEach(r => lines.push(`${String(r.mode).toUpperCase()} ${String(r.top_pattern).toUpperCase()}: signals=${r.signals ?? r.actionable ?? 0}, win=${fmtNum(r.win_rate_pct)}%, return=${fmtNum(r.avg_return_pct)}%, target=${fmtNum(r.target_hit_rate_pct)}%, stop=${fmtNum(r.stop_hit_rate_pct)}%`));
  lines.push('');
  lines.push('REGIMES');
  (res.regime_level || []).slice(0, 30).forEach(r => lines.push(`${String(r.mode).toUpperCase()} ${String(r.regime).toUpperCase()}: signals=${r.signals ?? r.actionable ?? 0}, win=${fmtNum(r.win_rate_pct)}%, return=${fmtNum(r.avg_return_pct)}%`));
  lines.push('');
  lines.push('BIAS / GENERALIZATION GATES');
  (res.bias_audit || []).forEach(r => lines.push(`${String(r.mode).toUpperCase()}: long=${fmtNum(r.long_signal_share_pct)}%, preset_return_gap=${fmtNum(r.preset_return_gap_pct)}%, time_range=${fmtNum(r.time_return_range_pct)}%, passes=${r.passes_direction_balance}/${r.passes_preset_gap}/${r.passes_time_stability}`));
  const cu = res.counter_update || {};
  lines.push('');
  lines.push(`COUNTER: added=${cu.added_stock_analyses || 0} market-analysis runs, engine_checks=${cu.added_engine_checks || 0}`);
  lines.push('');
  lines.push('OUT-OF-SAMPLE VALIDATION');
  Object.entries(res.robust_validation || {}).forEach(([mode, validation]) => {
    const wf = validation?.walk_forward || {};
    const pooled = wf?.pooled_out_of_sample || {};
    const boot = validation?.bootstrap || {};
    lines.push(`${String(mode).toUpperCase()}: positive_folds=${wf.positive_test_folds || 0}/${wf.total_test_folds || 0}, pooled_expectancy=${fmtNum(pooled.expectancy_pct)}%, pooled_win=${fmtNum(pooled.win_rate_pct)}%, expectancy_95ci=${JSON.stringify(boot.expectancy_95_ci_pct || null)}`);
  });
  if (Array.isArray(res.ticker_errors) && res.ticker_errors.length) {
    lines.push(''); lines.push('TICKER ERRORS');
    res.ticker_errors.slice(0, 80).forEach(e => lines.push(`${e.ticker}: ${e.error}`));
  }
  return lines.join('\n');
}

function renderPatternLabResults(res) {
  const out = document.getElementById('patternLabOutput');
  if (!out) return;
  const summary = Array.isArray(res.summary) ? res.summary : [];
  const best = res.best_mode ? res.best_mode.mode : '';
  const cards = summary.map(s => `
    <div class="dev-summary-card ${s.mode === best ? 'best' : ''}">
      <div class="dev-summary-title">${escapeHtml(String(s.mode || '').toUpperCase())}${s.mode === best ? ' · BEST' : ''}</div>
      <div class="dev-summary-metric"><span>Win rate</span><strong>${fmtNum(s.win_rate_pct)}%</strong></div>
      <div class="dev-summary-metric"><span>Avg return</span><strong>${fmtNum(s.avg_return_pct)}%</strong></div>
      <div class="dev-summary-metric"><span>Coverage</span><strong>${fmtNum(s.coverage_pct)}%</strong></div>
      <div class="dev-summary-metric"><span>Actionable</span><strong>${s.actionable || 0}/${s.tests || 0}</strong></div>
      <div class="dev-summary-metric"><span>Target/stop</span><strong>${fmtNum(s.target_hit_rate_pct)}% / ${fmtNum(s.stop_hit_rate_pct)}%</strong></div>
      <div class="dev-summary-metric"><span>Avg confidence</span><strong>${fmtNum(s.avg_confidence)}</strong></div>
    </div>`).join('');

  const rows = summary.map(s => `
    <tr>
      <td>${escapeHtml(String(s.mode || '').toUpperCase())}</td>
      <td>${s.tests || 0}</td>
      <td>${s.actionable || 0}</td>
      <td>${fmtNum(s.coverage_pct)}%</td>
      <td>${fmtNum(s.win_rate_pct)}%</td>
      <td>${fmtNum(s.avg_return_pct)}%</td>
      <td>${fmtNum(s.avg_mfe_pct)}%</td>
      <td>${fmtNum(s.avg_mae_pct)}%</td>
      <td>${fmtNum(s.reward_risk_ratio)}</td>
      <td>${fmtNum(s.target_hit_rate_pct)}%</td>
      <td>${fmtNum(s.stop_hit_rate_pct)}%</td>
      <td>${s.errors || 0}</td>
    </tr>`).join('');

  const baselineRows = renderLabMiniRows(res.baselines || [], ['mode','tests','win_rate_pct','avg_return_pct','avg_mfe_pct','avg_mae_pct'], {
    mode:'Baseline', tests:'Tests', win_rate_pct:'Win %', avg_return_pct:'Avg return %', avg_mfe_pct:'MFE %', avg_mae_pct:'MAE %'
  });

  const directionRows = renderLabMiniRows(res.direction_split || [], ['mode','direction','actionable','win_rate_pct','avg_return_pct','target_hit_rate_pct','stop_hit_rate_pct'], {
    mode:'Mode', direction:'Direction', actionable:'Signals', win_rate_pct:'Win %', avg_return_pct:'Avg return %', target_hit_rate_pct:'Target %', stop_hit_rate_pct:'Stop %'
  });

  const thresholdRows = renderLabMiniRows((res.threshold_report || []).filter(r => Number(r.actionable || 0) > 0), ['mode','threshold','actionable','coverage_pct','win_rate_pct','avg_return_pct'], {
    mode:'Mode', threshold:'Min conf', actionable:'Signals', coverage_pct:'Coverage %', win_rate_pct:'Win %', avg_return_pct:'Avg return %'
  }, 80);

  const confidenceRows = renderLabMiniRows(res.confidence_buckets || [], ['mode','bucket','actionable','win_rate_pct','avg_return_pct','target_hit_rate_pct','stop_hit_rate_pct'], {
    mode:'Mode', bucket:'Confidence', actionable:'Signals', win_rate_pct:'Win %', avg_return_pct:'Avg return %', target_hit_rate_pct:'Target %', stop_hit_rate_pct:'Stop %'
  }, 80);

  const tickerRows = renderLabMiniRows(res.ticker_level || [], ['mode','ticker','actionable','win_rate_pct','avg_return_pct','avg_mfe_pct','avg_mae_pct'], {
    mode:'Mode', ticker:'Ticker', actionable:'Signals', win_rate_pct:'Win %', avg_return_pct:'Avg return %', avg_mfe_pct:'MFE %', avg_mae_pct:'MAE %'
  }, 120);

  const patternRows = renderLabMiniRows(res.pattern_level || [], ['mode','top_pattern','actionable','win_rate_pct','avg_return_pct','target_hit_rate_pct','stop_hit_rate_pct'], {
    mode:'Mode', top_pattern:'Pattern', actionable:'Signals', win_rate_pct:'Win %', avg_return_pct:'Avg return %', target_hit_rate_pct:'Target %', stop_hit_rate_pct:'Stop %'
  }, 120);

  const regimeRows = renderLabMiniRows(res.regime_level || [], ['mode','regime','actionable','win_rate_pct','avg_return_pct'], {
    mode:'Mode', regime:'Regime', actionable:'Signals', win_rate_pct:'Win %', avg_return_pct:'Avg return %'
  }, 80);
  const biasRows = renderLabMiniRows(res.bias_audit || [], ['mode','long_signal_share_pct','preset_return_gap_pct','preset_coverage_gap_pp','time_return_range_pct','passes_direction_balance','passes_preset_gap','passes_time_stability'], {
    mode:'Mode', long_signal_share_pct:'Long share %', preset_return_gap_pct:'Preset return gap %', preset_coverage_gap_pp:'Preset coverage gap pp', time_return_range_pct:'Time range %', passes_direction_balance:'Direction pass', passes_preset_gap:'Preset pass', passes_time_stability:'Time pass'
  }, 40);

  const walkForwardRows = [];
  const bootstrapRows = [];
  Object.entries(res.robust_validation || {}).forEach(([mode, validation]) => {
    const wf = validation?.walk_forward || {};
    (wf.folds || []).forEach(fold => walkForwardRows.push({
      mode, fold: fold.fold, threshold: fold.selected_confidence_threshold,
      test_start: fold.test_start, test_end: fold.test_end,
      actionable: fold.test?.actionable || 0, win_rate_pct: fold.test?.win_rate_pct || 0,
      expectancy_pct: fold.test?.expectancy_pct || 0
    }));
    const boot = validation?.bootstrap || {};
    bootstrapRows.push({
      mode, samples: boot.samples || 0,
      expectancy_95_ci_pct: Array.isArray(boot.expectancy_95_ci_pct) ? boot.expectancy_95_ci_pct.join(' to ') : 'n/a',
      win_rate_95_ci_pct: Array.isArray(boot.win_rate_95_ci_pct) ? boot.win_rate_95_ci_pct.join(' to ') : 'n/a',
      positive_folds: `${wf.positive_test_folds || 0}/${wf.total_test_folds || 0}`,
      pooled_expectancy_pct: wf.pooled_out_of_sample?.expectancy_pct || 0
    });
  });
  const walkForwardTable = renderLabMiniRows(walkForwardRows, ['mode','fold','threshold','test_start','test_end','actionable','win_rate_pct','expectancy_pct'], {
    mode:'Mode', fold:'Fold', threshold:'Chosen conf', test_start:'Test start', test_end:'Test end', actionable:'Signals', win_rate_pct:'OOS win %', expectancy_pct:'OOS expectancy %'
  }, 100);
  const bootstrapTable = renderLabMiniRows(bootstrapRows, ['mode','samples','positive_folds','pooled_expectancy_pct','expectancy_95_ci_pct','win_rate_95_ci_pct'], {
    mode:'Mode', samples:'Bootstrap draws', positive_folds:'Positive folds', pooled_expectancy_pct:'Pooled OOS expectancy %', expectancy_95_ci_pct:'Expectancy 95% CI', win_rate_95_ci_pct:'Win-rate 95% CI'
  }, 20);

  const errors = Array.isArray(res.ticker_errors) && res.ticker_errors.length
    ? `<div class="dev-lab-note">Ticker errors: ${res.ticker_errors.map(e => `${escapeHtml(e.ticker)}: ${escapeHtml(e.error)}`).join(' · ')}</div>` : '';
  const cache = res.cache || {};
  const testedEngines = Array.isArray(res?.params?.engine_modes) ? res.params.engine_modes.map(m => String(m).toUpperCase()).join(', ') : 'selected';
  const counterLine = applyPatternLabCounterUpdate(res);
  const promotion = res?.production_promotion || {};
  const isolationText = promotion.automatic === false ? ' · Live weights unchanged' : '';
  const cacheLine = `<div class="dev-lab-note">Engines tested: ${escapeHtml(testedEngines)} · Data source: ${escapeHtml(cache.data_source || res?.params?.data_source || 'unknown')} · Cache hits: ${cache.cache_hits ?? 0} · API fetches: ${cache.api_fetches ?? 0} · DB size: ${fmtNum(cache.db_size_mb || 0)} MB${escapeHtml(counterLine)}${escapeHtml(isolationText)}</div>`;

  const terminalText = buildPatternLabTerminal(res);
  out.innerHTML = `
    ${terminalBlock('patternLabTerminalText', terminalText, 'ENGINE VS ENGINE RESULTS')}
    <div class="dev-summary-grid">${cards || '<div class="dev-empty">No summary returned.</div>'}</div>
    <table class="dev-lab-table">
      <thead><tr><th>Mode</th><th>Tests</th><th>Actionable</th><th>Coverage</th><th>Win Rate</th><th>Avg Return</th><th>Avg MFE</th><th>Avg MAE</th><th>MFE/MAE</th><th>Target Hit</th><th>Stop Hit</th><th>Errors</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${cacheLine}
    <div class="dev-lab-note">${escapeHtml(res.note || 'Educational approximate backtest only.')}</div>
    ${errors}

    <div class="dev-detail-grid">
      ${renderLabSection('Purged walk-forward out-of-sample folds', walkForwardTable)}
      ${renderLabSection('Cluster-bootstrap uncertainty', bootstrapTable)}
      ${renderLabSection('Baselines: does Oryntra beat dumb guesses?', baselineRows)}
      ${renderLabSection('Bullish vs bearish split', directionRows)}
      ${renderLabSection('Confidence thresholds', thresholdRows)}
      ${renderLabSection('Confidence buckets', confidenceRows)}
      ${renderLabSection('Ticker-level results', tickerRows)}
      ${renderLabSection('Pattern-level results', patternRows)}
      ${renderLabSection('Market-regime results', regimeRows)}
      ${renderLabSection('Bias and generalization gates', biasRows)}
    </div>
  `;
}

function renderLabSection(title, tableHtml) {
  return `
    <section class="dev-lab-section">
      <h3>${escapeHtml(title)}</h3>
      ${tableHtml || '<div class="dev-empty">No rows.</div>'}
    </section>`;
}

function renderLabMiniRows(items, keys, labels, limit = 80) {
  const rows = (Array.isArray(items) ? items : []).slice(0, limit);
  if (!rows.length) return '';
  const head = keys.map(k => `<th>${escapeHtml(labels[k] || k)}</th>`).join('');
  const body = rows.map(r => `<tr>${keys.map(k => `<td>${formatLabCell(k, r[k])}</td>`).join('')}</tr>`).join('');
  return `<div class="dev-lab-table-wrap"><table class="dev-lab-table compact"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function formatLabCell(key, value) {
  if (value === undefined || value === null) return '—';
  if (String(key).includes('pct') || String(key).includes('rate')) return `${fmtNum(value)}%`;
  if (typeof value === 'number') return fmtNum(value);
  return escapeHtml(String(value).toUpperCase());
}


async function loadTraining150Tickers() {
  const input = document.getElementById('patternLabTickers');
  const count = Number(document.getElementById('patternLabUniverseSize')?.value || 150);
  const seed = Number(document.getElementById('patternLabSeed')?.value || 73021);
  try {
    const universe = await API.dev.patternLabUniverse(count, seed);
    if (input) input.value = (universe.tickers || []).join(',');
    const mode = document.getElementById('patternLabUniverseMode');
    if (mode) mode.value = 'unseen150';
    showCopyToast(`Generated ${universe.count || 0} tickers unused by the current cache.`);
  } catch (err) {
    showCopyToast(`Universe generation failed: ${err.message || String(err)}`);
  }
}

function buildVAITrainingPayload() {
  const tickers = (document.getElementById('patternLabTickers')?.value || DEFAULT_PATTERN_LAB_TICKERS.join(','))
    .split(',').map(t => sanitizeTickerSymbol(t)).filter(Boolean).slice(0, 150);
  return {
    tickers,
    period: document.getElementById('patternLabPeriod')?.value || '5y',
    horizon_days: Number(document.getElementById('patternLabHorizon')?.value || 10),
    step: Number(document.getElementById('patternLabStep')?.value || 8),
    max_tests_per_ticker: Number(document.getElementById('patternLabMaxTests')?.value || 30),
    data_source: document.getElementById('patternLabDataSource')?.value || 'cache_only',
    min_samples: 80,
    model_version: 'vai2',
    force_promote: false,
  };
}

async function trainVAIModel() {
  const out = document.getElementById('vaiTrainingOutput');
  if (currentVAITrainingJobId) {
    showCopyToast(currentVAITrainingJobId === JOB_STARTING ? 'V1.0 Quant training is starting.' : 'V1.0 Quant training is already running.');
    return;
  }
  const payload = buildVAITrainingPayload();
  currentVAITrainingJobId = JOB_STARTING;
  if (out) out.innerHTML = `<div class="dev-lab-loading">Starting V1.0 Quant training on ${payload.tickers.length} tickers...</div>`;
  try {
    const job = await API.dev.vaiTrainStart(payload);
    const jobId = String(job?.job_id || '').trim();
    if (!jobId) throw new Error('V1.0 Quant training did not return a job ID.');
    currentVAITrainingJobId = jobId;
    renderVAITrainingStatus(job);
    pollVAITrainingJob(jobId);
  } catch (err) {
    currentVAITrainingJobId = null;
    if (out) out.innerHTML = `<div class="dev-error">V1.0 Quant training failed to start: ${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function pollVAITrainingJob(jobId) {
  if (!jobId || currentVAITrainingJobId !== jobId) return;
  const out = document.getElementById('vaiTrainingOutput');
  try {
    const job = await API.dev.vaiTrainStatus(jobId);
    if (currentVAITrainingJobId !== jobId) return;
    renderVAITrainingStatus(job);
    if (job.status === 'queued' || job.status === 'running') {
      setTimeout(() => pollVAITrainingJob(jobId), 3000);
    } else {
      currentVAITrainingJobId = null;
    }
  } catch (err) {
    if (currentVAITrainingJobId !== jobId) return;
    if (out) out.innerHTML = `<div class="dev-error">V1.0 Quant training status check failed: ${escapeHtml(err.message || String(err))}. Retrying...</div>`;
    setTimeout(() => pollVAITrainingJob(jobId), 5000);
  }
}

function renderVAITrainingStatus(job) {
  const out = document.getElementById('vaiTrainingOutput');
  if (!out || !job) return;
  const pct = Math.max(0, Math.min(100, Number(job.progress_pct || 0)));
  if (job.status === 'done' || job.result) {
    const result = job.result || {};
    const terminal = String(result.terminal_output || JSON.stringify(result, null, 2))
      .replaceAll('VAI 2.2', 'V1.0 Quant')
      .replaceAll('VAI2.2', 'V1.0 Quant')
      .replaceAll('VAI 2.1', 'V1.0 Quant')
      .replaceAll('VAI 2.2', 'V1.0 Quant')
      .replaceAll('VAI2.1', 'V1.0 Quant')
      .replaceAll('V7', 'V1.0')
      .replaceAll('V8', 'V1.0');
    out.innerHTML = `${terminalBlock('vaiTrainingTerminalText', terminal, 'V1.0 QUANT TRAINING OUTPUT')}
      <div class="dev-cache-card">
        <div class="dev-summary-title">V1.0 QUANT TRAINING: ${escapeHtml(String(job.status || '').toUpperCase())}</div>
        <div class="dev-summary-metric"><span>Status</span><strong>${escapeHtml(String(result.status || job.status || 'unknown').toUpperCase())}</strong></div>
        <div class="dev-summary-metric"><span>Samples</span><strong>${Number(result?.model_status?.samples || result?.training?.samples || 0).toLocaleString()}</strong></div>
        <div class="dev-summary-metric"><span>Threshold</span><strong>${fmtNum(result?.model_status?.threshold || 0)}</strong></div>
      </div>`;
    return;
  }
  out.innerHTML = `<div class="dev-cache-card">
    <div class="dev-summary-title">V1.0 QUANT TRAINING: ${escapeHtml(String(job.status || '').toUpperCase())}</div>
    <div class="dev-summary-metric"><span>Progress</span><strong>${fmtNum(pct)}%</strong></div>
    <div class="dev-summary-metric"><span>Phase</span><strong>${escapeHtml(job.phase || '—')}</strong></div>
    <div class="dev-summary-metric"><span>Current</span><strong>${escapeHtml(job.current_ticker || '—')}</strong></div>
    <div class="dev-lab-note">${escapeHtml(job.message || '')}</div>
    <div class="dev-progress"><span style="width:${pct}%"></span></div>
  </div>`;
}

async function showVAIModelStatus() {
  const out = document.getElementById('vaiTrainingOutput');
  if (out) out.innerHTML = '<div class="dev-lab-loading">Checking V1.0 Quant model...</div>';
  try {
    const status = await API.dev.vaiModelStatus();
    const terminal = `V1.0 QUANT MODEL STATUS\n${'='.repeat(32)}\nTrained: ${status.trained}\nVersion: ${status.version || 'none'}\nCreated: ${status.created_at || 'none'}\nSamples: ${status.samples || 0}\nThreshold: ${status.threshold || 'n/a'}\nValidation: ${JSON.stringify(status.validation || {}, null, 2)}\n\nTop positive features:\n${(status.top_positive_features || []).map(x => '  + ' + x[0] + ': ' + fmtNum(x[1])).join('\n')}\n\nTop negative features:\n${(status.top_negative_features || []).map(x => '  - ' + x[0] + ': ' + fmtNum(x[1])).join('\n')}`;
    if (out) out.innerHTML = terminalBlock('vaiTrainingTerminalText', terminal, 'V1.0 QUANT MODEL STATUS');
  } catch (err) {
    if (out) out.innerHTML = `<div class="dev-error">V1.0 Quant status failed: ${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function warmPatternCache() {
  const statusEl = document.getElementById('patternCacheStatus');
  if (currentCacheWarmJobId) {
    showCopyToast(currentCacheWarmJobId === JOB_STARTING ? 'Cache warming is starting.' : 'A cache warm job is already running.');
    return;
  }
  currentCacheWarmJobId = JOB_STARTING;
  if (statusEl) statusEl.innerHTML = '<div class="dev-lab-loading">Starting cache warm job...</div>';
  try {
    let tickers = (document.getElementById('patternLabTickers')?.value || '')
      .split(',').map(t => sanitizeTickerSymbol(t)).filter(Boolean).slice(0, 150);
    if (!tickers.length && (document.getElementById('patternLabUniverseMode')?.value || 'manual') !== 'manual') {
      const count = Number(document.getElementById('patternLabUniverseSize')?.value || 150);
      const seed = Number(document.getElementById('patternLabSeed')?.value || 73021);
      const universe = await API.dev.patternLabUniverse(count, seed);
      tickers = universe.tickers || [];
      const input = document.getElementById('patternLabTickers');
      if (input) input.value = tickers.join(',');
    }
    const period = document.getElementById('patternLabPeriod')?.value || '5y';
    const delay_seconds = Number(document.getElementById('patternCacheDelay')?.value || 13);
    const max_cache_gb = Number(document.getElementById('patternCacheMaxGb')?.value || 10);
    const job = await API.dev.cacheWarmStart({tickers, period, delay_seconds, max_cache_gb});
    const jobId = String(job?.job_id || '').trim();
    if (!jobId) throw new Error('Cache warm did not return a job ID.');
    currentCacheWarmJobId = jobId;
    renderCacheWarmStatus(job);
    pollCacheWarmJob(jobId);
  } catch (err) {
    currentCacheWarmJobId = null;
    if (statusEl) statusEl.innerHTML = `<div class="dev-empty">Cache warm failed to start: ${escapeHtml(err.message || String(err))}</div>`;
  }
}

async function pollCacheWarmJob(jobId) {
  if (!jobId || currentCacheWarmJobId !== jobId) return;
  try {
    const job = await API.dev.cacheWarmStatus(jobId);
    if (currentCacheWarmJobId !== jobId) return;
    renderCacheWarmStatus(job);
    if (job.status === 'queued' || job.status === 'running') {
      setTimeout(() => pollCacheWarmJob(jobId), 2500);
    } else {
      currentCacheWarmJobId = null;
    }
  } catch (err) {
    if (currentCacheWarmJobId !== jobId) return;
    const statusEl = document.getElementById('patternCacheStatus');
    if (statusEl) statusEl.innerHTML = `<div class="dev-empty">Cache status check failed: ${escapeHtml(String(err))}. Retrying...</div>`;
    setTimeout(() => pollCacheWarmJob(jobId), 5000);
  }
}

function renderCacheWarmStatus(job) {
  const statusEl = document.getElementById('patternCacheStatus');
  if (!statusEl || !job) return;
  const total = Number(job.total || 0);
  const completed = Number(job.completed || 0);
  const pct = total ? Math.min(100, Math.round(completed / total * 100)) : 0;
  const errors = Array.isArray(job.errors) && job.errors.length
    ? `<div class="dev-lab-note">Recent errors: ${job.errors.slice(-4).map(e => `${escapeHtml(e.ticker || '')}: ${escapeHtml(e.error || '')}`).join(' · ')}</div>` : '';
  const message = job.message
    ? `<div class="dev-lab-note">${escapeHtml(job.message)}</div>` : '';
  statusEl.innerHTML = `
    <div class="dev-cache-card">
      <div class="dev-summary-title">CACHE WARM: ${escapeHtml(String(job.status || '').toUpperCase())}</div>
      <div class="dev-summary-metric"><span>Progress</span><strong>${completed}/${total} (${pct}%)</strong></div>
      <div class="dev-summary-metric"><span>Stored bars</span><strong>${job.stored_bars || 0}</strong></div>
      <div class="dev-summary-metric"><span>Current</span><strong>${escapeHtml(job.current_ticker || '—')}</strong></div>
      <div class="dev-summary-metric"><span>DB size</span><strong>${fmtNum((job.db_size_bytes || 0) / 1024 / 1024)} MB</strong></div>
      <div class="dev-progress"><span style="width:${pct}%"></span></div>
      ${message}
      ${errors}
    </div>`;
}

async function showPatternCacheStatus() {
  const statusEl = document.getElementById('patternCacheStatus');
  const tickers = (document.getElementById('patternLabTickers')?.value || DEFAULT_PATTERN_LAB_TICKERS.join(','))
    .split(',').map(t => sanitizeTickerSymbol(t)).filter(Boolean).slice(0, 150);
  if (statusEl) statusEl.innerHTML = '<div class="dev-lab-loading">Checking local candle cache...</div>';
  try {
    const res = await API.dev.cacheStatus(tickers);
    const rows = Array.isArray(res.rows) ? res.rows : [];
    const preview = rows.slice(0, 12).map(r => `${escapeHtml(r.ticker)}:${r.bars}`).join(' · ');
    const missing = Array.isArray(res.missing) ? res.missing : [];
    if (statusEl) statusEl.innerHTML = `
      <div class="dev-cache-card">
        <div class="dev-summary-title">LOCAL OHLCV CACHE</div>
        <div class="dev-summary-metric"><span>Cached tickers</span><strong>${res.tickers_cached || 0}/${res.tickers_requested || 0}</strong></div>
        <div class="dev-summary-metric"><span>DB size</span><strong>${fmtNum(res.db_size_mb || 0)} MB</strong></div>
        <div class="dev-lab-note">Preview bars: ${preview || 'No cached bars yet.'}</div>
        <div class="dev-lab-note">Missing: ${missing.slice(0, 80).map(escapeHtml).join(', ') || 'none'}</div>
      </div>`;
  } catch (err) {
    if (statusEl) statusEl.innerHTML = `<div class="dev-empty">Cache status failed: ${escapeHtml(String(err))}</div>`;
  }
}

function fmtNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(2) : '0.00';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#039;','"':'&quot;'}[ch]));
}

async function loadWatchlist() {
  const container = document.getElementById('watchlistContainer');
  try {
    const items = await API.watchlist.get();
    if (!items.length) {
      container.innerHTML = '<div class="wl-empty">No tickers in watchlist. Add some above.</div>';
      return;
    }
    container.innerHTML = items.map(item => `
      <div class="wl-card">
        <span class="wl-ticker">${escHtml(item.ticker)}</span>
        <div class="wl-actions">
          <button class="wl-scan-btn" type="button" onclick="scanFromWatchlist('${escHtml(item.ticker)}')">SCAN</button>
          <button class="wl-del-btn" type="button" aria-label="Remove ${escHtml(item.ticker)} from watchlist" onclick="removeFromWatchlist('${escHtml(item.ticker)}')">✕</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = '<div class="wl-empty">Failed to load watchlist.</div>';
  }
}

function initWatchlist() {
  document.getElementById('wlAddBtn').addEventListener('click', async () => {
    const ticker = document.getElementById('wlInput').value.trim().toUpperCase();
    if (ticker) {
      await addToWatchlist(ticker);
      document.getElementById('wlInput').value = '';
      loadWatchlist();
    }
  });

  document.getElementById('wlInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('wlAddBtn').click();
  });

  document.getElementById('wlScanAllBtn').addEventListener('click', scanAllWatchlist);
}

async function addToWatchlist(ticker) {
  await API.watchlist.add(ticker);
}

window.removeFromWatchlist = async function(ticker) {
  await API.watchlist.remove(ticker);
  loadWatchlist();
};

window.scanFromWatchlist = function(ticker) {
  document.getElementById('tickerInput').value = ticker;
  document.querySelector('[data-tab="scanner"]').click();
  runScan();
};

async function scanAllWatchlist() {
  const allowed = await requireAnalysisAccess({type:'scan-all'});
  if (!allowed) return;
  const items = await API.watchlist.get();
  if (!items.length) return;

  const btn = document.getElementById('wlScanAllBtn');
  btn.textContent = '⟳ SCANNING...';
  btn.disabled    = true;

  try {
    const tickers = items.map(i => i.ticker);
    const results = [], errors = [];
    for (let index = 0; index < tickers.length; index += 1) {
      btn.textContent = `⟳ ${index + 1}/${tickers.length}`;
      try {
        const market = await fetchDirectMarketBars(tickers[index], currentPeriod, 'auto', 320);
        results.push(await API.scanUploaded(tickers[index], currentPeriod, market.provider, market.bars, PUBLIC_ANALYSIS_ENGINE));
      } catch (error) {
        errors.push({ticker: tickers[index], error: error.message || String(error)});
      }
    }
    renderScannerResults(results);
    if (errors.length) showError(`${errors.length} watchlist symbol${errors.length === 1 ? '' : 's'} could not be scanned. ${errors[0].ticker}: ${errors[0].error}`);
  } finally {
    btn.textContent = '⬡ SCAN ALL';
    btn.disabled    = false;
  }
}

function renderScannerResults(results) {
  const container = document.getElementById('scannerResults');
  const grid      = document.getElementById('scannerResultsGrid');
  container.style.display = '';

  if (!results.length) {
    grid.innerHTML = '<div style="color:var(--text-dim);padding:20px;">No results.</div>';
    return;
  }

  grid.innerHTML = results.map(d => {
    const tp    = d.trade_plan || {};
    const setup = d.setup || {};
    const color = tp.direction === 'LONG' ? 'var(--bull)' : tp.direction === 'SHORT' ? 'var(--bear)' : 'var(--neutral)';
    return `
      <button class="scanner-result-card" type="button" onclick="scanFromWatchlist('${d.ticker}')">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px;">
          <span class="src-ticker">${d.ticker}</span>
          <span class="src-price">${fmt$(d.trade_plan?.entry_ideal)}</span>
        </div>
        <div class="src-setup" style="color:${color}">
          ${(setup.setup_type||'—').replace(/_/g,' ')} — ${tp.direction || 'NEUTRAL'}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">
          <span class="src-score">Score: ${(tp.quality_score||0).toFixed(0)} | ${tp.quality_grade||'—'}</span>
          <span style="font-size:11px;color:var(--text-dim)">R:R ${tp.risk_reward ? tp.risk_reward.toFixed(1)+':1' : '—'}</span>
        </div>
      </button>`;
  }).join('');
}

function initPaperTrades() {}

function paperCacheKey() {
  return `oryntra_paper_cache_${currentUser && currentUser.id ? currentUser.id : 'guest'}`;
}

function storePaperCache(trades, stats) {
  try {
    const compactTrades = (trades || []).slice(0, 100).map(t => ({
      id: t.id, ticker: t.ticker, direction: t.direction,
      entry_price: t.entry_price, stop_price: t.stop_price, target_price: t.target_price,
      size: t.size, status: t.status, opened_at: t.opened_at, closed_at: t.closed_at,
      close_price: t.close_price, pnl: t.pnl, pnl_pct: t.pnl_pct,
      setup_type: t.setup_type, quality_score: t.quality_score,
      notes: (t.notes || '').slice(0, 160)
    }));
    localStorage.setItem(paperCacheKey(), JSON.stringify({ trades: compactTrades, stats: stats || {}, saved_at: Date.now() }));
  } catch (_) {}
}

function loadPaperCache() {
  try {
    const raw = localStorage.getItem(paperCacheKey());
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

async function loadPaperTrades() {
  try {
    if (!currentUser) await refreshAuthState();
    const [trades, stats] = await Promise.all([API.paper.getAll(), API.paper.stats()]);
    storePaperCache(trades, stats);
    renderPaperStats(stats);
    renderPaperGrid(trades);
  } catch (e) {
    console.error('Paper trades load error:', e);
    const cached = loadPaperCache();
    if (cached && Array.isArray(cached.trades)) {
      renderPaperStats(cached.stats || {});
      renderPaperGrid(cached.trades);
      const grid = document.getElementById('paperTradesGrid');
      if (grid) grid.insertAdjacentHTML('afterbegin', '<div class="pt-empty">Showing cached paper trades. Sign in again if this does not update.</div>');
      return;
    }
    const grid = document.getElementById('paperTradesGrid');
    const stats = document.getElementById('paperStats');
    if (stats) stats.innerHTML = '';
    if (grid) grid.innerHTML = '<div class="pt-empty">Sign in to save and view your paper trades.</div>';
  }
}

function renderPaperStats(stats) {
  const container = document.getElementById('paperStats');
  const chips = [
    { label: 'TOTAL TRADES', val: stats.total_trades || 0, color: 'var(--text-primary)' },
    { label: 'WIN RATE',     val: `${(stats.win_rate||0).toFixed(1)}%`, color: stats.win_rate >= 50 ? 'var(--bull)' : 'var(--bear)' },
    { label: 'TOTAL P&L',    val: fmtPnl(stats.total_pnl || 0), color: (stats.total_pnl||0) >= 0 ? 'var(--bull)' : 'var(--bear)' },
    { label: 'EXPECTANCY',   val: fmtPnl(stats.expectancy || 0), color: (stats.expectancy||0) >= 0 ? 'var(--bull)' : 'var(--bear)' },
  ];
  container.innerHTML = chips.map(c => `
    <div class="stat-chip">
      <div class="stat-chip-label">${c.label}</div>
      <div class="stat-chip-val" style="color:${c.color}">${c.val}</div>
    </div>`).join('');
}


function fmtPct(val) {
  if (val == null || isNaN(Number(val))) return '—';
  const n = Number(val);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`;
}

function fmtTradeDate(value) {
  if (!value) return '—';
  const d = new Date(String(value).replace(' ', 'T'));
  if (isNaN(d.getTime())) return String(value).substring(0, 16);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function buildTradeLevelStrip(entry, current, target) {
  const values = [entry, current, target].map(Number);
  if (!values.every(Number.isFinite)) return '<div class="pt-spark-empty">PRICE LEVELS UNAVAILABLE</div>';

  const w = 164;
  const h = 46;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0.0001);
  const y = value => h - ((value - min) / span) * h;
  const directionClass = current >= entry ? 'up' : 'down';

  return `<svg class="pt-sparkline ${directionClass}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Entry, current, and target price levels">
    <line x1="0" y1="${y(entry).toFixed(1)}" x2="${w}" y2="${y(entry).toFixed(1)}" stroke="currentColor" stroke-opacity="0.35" stroke-dasharray="4 3" />
    <line x1="0" y1="${y(target).toFixed(1)}" x2="${w}" y2="${y(target).toFixed(1)}" stroke="currentColor" stroke-opacity="0.55" stroke-dasharray="2 2" />
    <circle cx="${(w * 0.5).toFixed(1)}" cy="${y(current).toFixed(1)}" r="4" fill="currentColor" />
  </svg>`;
}


function renderPaperGrid(trades) {
  const grid = document.getElementById('paperTradesGrid');
  if (!trades.length) {
    grid.innerHTML = '<div class="pt-empty">No paper trades yet. Scan a ticker and click "Paper Trade This".</div>';
    return;
  }
  grid.innerHTML = trades.map(t => {
    const isOpen  = t.status === 'OPEN';
    const currentPrice = t.current_price ?? t.close_price ?? null;
    const livePnl = t.current_pnl ?? t.pnl ?? 0;
    const livePnlPct = t.current_pnl_pct ?? t.pnl_pct ?? 0;
    const pnl     = t.pnl || 0;
    const pnlPct  = t.pnl_pct || 0;
    const pnlClass = (isOpen ? livePnl : pnl) >= 0 ? 'win' : 'loss';
    const dirClass = t.direction === 'LONG' ? 'long' : 'short';
    const levelStrip = buildTradeLevelStrip(t.entry_price, currentPrice, t.target_price);
    return `
      <div class="pt-card pt-card-expanded">
        <div class="pt-id-block">
          <div class="pt-ticker">${escHtml(t.ticker)}</div>
          <div class="pt-dir ${dirClass}">${escHtml(t.direction || '')}</div>
          <div class="pt-setup-label">${escHtml(t.setup_type || '')}</div>
        </div>

        <div class="pt-info pt-trade-levels">
          <div><span>Entry</span>${fmt$(t.entry_price)}</div>
          <div><span>Stop</span>${fmt$(t.stop_price)}</div>
          <div><span>Target</span>${fmt$(t.target_price)}</div>
        </div>

        <div class="pt-info pt-trade-meta">
          <div><span>Date Purchased</span>${fmtTradeDate(t.opened_at)}</div>
          <div><span>Current Stock Price</span>${fmt$(currentPrice)}</div>
          <div><span>Data Source</span>${escHtml(t.snapshot_source || 'cached')}</div>
        </div>

        <div class="pt-mini-chart-wrap">
          <div class="pt-mini-chart-head">
            <span>TRADE LEVELS</span>
            <span>${t.current_price_at ? escHtml(String(t.current_price_at).substring(0, 10)) : ''}</span>
          </div>
          ${levelStrip}
        </div>

        <div class="pt-info pt-notes-block">
          <div><span>Notes</span>${escHtml(t.notes || '') || '—'}</div>
          ${t.status !== 'OPEN' ? `<div><span>Closed</span>${fmtTradeDate(t.closed_at)} @ ${fmt$(t.close_price)}</div>` : ''}
        </div>

        <div class="pt-pnl ${isOpen ? pnlClass : pnlClass}">
          ${isOpen
            ? `<div class="pnl-val">${fmtPnl(livePnl)}</div>
               <div class="pnl-pct">${fmtPct(livePnlPct)}</div>
               <div class="pt-status-line">OPEN · Size $${Number(t.size || 0).toLocaleString()}</div>
               <button class="pt-close-btn" type="button" onclick="closePaperTrade(${t.id}, ${t.ticker ? `'${escHtml(t.ticker)}'` : null})">CLOSE</button>`
            : `<div class="pnl-val">${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(2)}</div>
               <div class="pnl-pct">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</div>
               <div class="pt-status-line">${escHtml(t.status || 'CLOSED')}</div>`
          }
        </div>
      </div>`;
  }).join('');
}


window.closePaperTrade = async function(tradeId, ticker) {
  const price = prompt(`Close price for trade #${tradeId}${ticker ? ` (${ticker})` : ''}:`);
  if (!price || isNaN(parseFloat(price))) return;
  try {
    const result = await API.paper.close({trade_id: tradeId, close_price: parseFloat(price)});
    alert(`Trade closed: ${result.outcome} | P&L: ${result.pnl >= 0 ? '+' : ''}$${result.pnl.toFixed(2)} (${result.pnl_pct.toFixed(2)}%)`);
    loadPaperTrades();
  } catch (e) {
    alert('Failed to close trade.');
  }
};


function initModal() {
  document.getElementById('modalCancel').addEventListener('click', closeModal);
  document.getElementById('modalConfirm').addEventListener('click', confirmPaperTrade);
  document.getElementById('paperModal').addEventListener('click', e => {
    if (e.target === document.getElementById('paperModal')) closeModal();
  });
}

function openPaperModal(analysis) {
  if (!currentUser) {
    openAuthModal('login');
    showError('Sign in to save paper trades to your account.');
    return;
  }
  const tp  = analysis.trade_plan || {};
  const dir = tp.direction || 'LONG';
  document.getElementById('modalTicker').textContent = analysis.ticker;
  document.getElementById('modalDir').value          = dir;
  document.getElementById('modalEntry').value        = tp.entry_ideal || '';
  document.getElementById('modalStop').value         = tp.stop  || '';
  document.getElementById('modalTarget').value       = tp.target || '';
  document.getElementById('modalNotes').value        = `${(analysis.setup||{}).setup_type||''} — Score: ${(tp.quality_score||0).toFixed(0)}`;
  openAccessibleDialog(document.getElementById('paperModal'), document.getElementById('modalDir'));
}

function closeModal() {
  closeAccessibleDialog(document.getElementById('paperModal'));
}

async function confirmPaperTrade() {
  const ticker  = document.getElementById('modalTicker').textContent;
  const setup   = currentAnalysis ? (currentAnalysis.setup || {}) : {};
  const tp      = currentAnalysis ? (currentAnalysis.trade_plan || {}) : {};

  const data = {
    ticker:        ticker,
    direction:     document.getElementById('modalDir').value,
    entry_price:   parseFloat(document.getElementById('modalEntry').value),
    stop_price:    parseFloat(document.getElementById('modalStop').value),
    target_price:  parseFloat(document.getElementById('modalTarget').value),
    size:          parseFloat(document.getElementById('modalSize').value) || 1000,
    notes:         document.getElementById('modalNotes').value,
    setup_type:    setup.setup_type || null,
    quality_score: tp.quality_score || null,
  };

  if (!data.entry_price || !data.stop_price || !data.target_price) {
    alert('Please fill in all price fields.');
    return;
  }

  try {
    await API.paper.open(data);
    closeModal();
    alert(`Paper trade opened: ${data.direction} ${ticker} @ $${data.entry_price}`);
    loadPaperTrades().catch(() => {});
  } catch (e) {
    alert('Failed to open paper trade.');
  }
}


function renderMARow(id, name, maPrice, price) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!maPrice) { el.innerHTML = `<span class="ma-name">${name}</span><span>—</span>`; return; }
  const above  = price > maPrice;
  const distPct = ((price - maPrice) / maPrice * 100).toFixed(2);
  el.className = `ma-row ${above ? 'above' : 'below'}`;
  el.innerHTML = `
    <span class="ma-name">${name}</span>
    <span class="ma-price">${fmt$(maPrice)}</span>
    <span class="ma-dist ${above ? 'dist-up' : 'dist-down'}">${distPct > 0 ? '+' : ''}${distPct}%</span>`;
}

function renderGauge(id, val, min=0, max=100) {
  const el = document.getElementById(id);
  if (!el || val == null) return;
  const pct     = Math.max(0, Math.min(100, (val - min) / (max - min) * 100));
  const color   = pct >= 80 ? 'var(--bear)' : pct <= 20 ? 'var(--bull)' : 'var(--neutral)';
  el.style.background = `linear-gradient(to right, ${color} ${pct}%, var(--bg-elevated) ${pct}%)`;
  el.style.position = 'relative';
  const existing = el.querySelector('.gauge-needle');
  const needle   = existing || document.createElement('div');
  needle.className     = 'gauge-needle';
  needle.style.cssText = `position:absolute;top:-3px;left:${pct}%;width:10px;height:10px;border-radius:50%;background:${color};border:2px solid var(--bg-card);transform:translateX(-50%);transition:left 0.5s ease;`;
  if (!existing) el.appendChild(needle);
}

function renderOscSignal(id, text, cssClass) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className   = `osc-signal ${cssClass}`;
}

function renderPred(horizon, pred) {
  if (!pred) return;
  const pct    = pred.expected_pct || 0;
  const signal = pred.signal || 'HOLD';
  const conf   = pred.confidence || 0;

  const pctEl  = document.getElementById(`pred${horizon}`);
  const sigEl  = document.getElementById(`predSig${horizon}`);
  const confEl = document.getElementById(`predConf${horizon}`);

  if (!pctEl) return;

  pctEl.textContent  = `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
  pctEl.style.color  = signal.includes('BUY') ? 'var(--bull)' : signal.includes('SELL') ? 'var(--bear)' : 'var(--text-secondary)';

  const signalDisplay = {
    'STRONG_BUY':  '▲▲ STRONG BUY',
    'BUY':         '▲ BUY',
    'HOLD':        '◆ HOLD',
    'SELL':        '▼ SELL',
    'STRONG_SELL': '▼▼ STRONG SELL',
  }[signal] || signal;

  const signalClass = {
    'STRONG_BUY':  'strong-buy',
    'BUY':         'buy',
    'HOLD':        'hold',
    'SELL':        'sell',
    'STRONG_SELL': 'strong-sell',
  }[signal] || 'hold';

  if (sigEl) {
    sigEl.textContent = signalDisplay;
    sigEl.className = `pred-signal ${signalClass}`;
    sigEl.style.padding = '2px 7px';
    sigEl.style.borderRadius = '3px';
    sigEl.style.fontSize = '9px';
    sigEl.style.letterSpacing = '0.08em';
    sigEl.style.display = 'inline-block';
  }

  if (confEl) confEl.textContent = conf ? `${conf}% conf.` : '';
}

function renderMomentumBars(data) {
  const container = document.getElementById('momentumBars');
  if (!container) return;
  container.innerHTML = Object.entries(data).map(([label, val]) => {
    if (val == null) return '';
    const color   = val >= 0 ? 'var(--bull)' : 'var(--bear)';
    const barW    = Math.min(Math.abs(val) * 2, 100);
    return `
      <div class="momentum-bar-item">
        <span class="mom-label">${label}</span>
        <div class="mom-bar-wrap">
          <div class="mom-bar" style="width:${barW}%;background:${color};"></div>
        </div>
        <span class="mom-pct" style="color:${color}">${val >= 0 ? '+' : ''}${val.toFixed(1)}%</span>
      </div>`;
  }).join('');
}


function rsiSignalText(rsi) {
  if (!rsi) return '—';
  if (rsi >= 70) return 'OVERBOUGHT';
  if (rsi <= 30) return 'OVERSOLD';
  if (rsi >= 55) return 'BULLISH';
  if (rsi <= 45) return 'BEARISH';
  return 'NEUTRAL';
}

function rsiSignalClass(rsi) {
  if (!rsi) return 'neutral';
  if (rsi >= 70) return 'bear';
  if (rsi <= 30) return 'bull';
  if (rsi >= 55) return 'bull';
  if (rsi <= 45) return 'bear';
  return 'neutral';
}

function oscClass(signal) {
  if (!signal) return 'neutral';
  const s = signal.toUpperCase();
  if (s.includes('BULL') || s.includes('BUY')  || s.includes('OVER') && s.includes('SOLD')) return 'bull';
  if (s.includes('BEAR') || s.includes('SELL') || s.includes('OVER') && s.includes('BOUGHT')) return 'bear';
  return 'neutral';
}

function bbSignalText(pct) {
  if (pct == null) return '—';
  if (pct >= 90)  return 'UPPER BAND';
  if (pct <= 10)  return 'LOWER BAND';
  if (pct >= 60)  return 'UPPER HALF';
  if (pct <= 40)  return 'LOWER HALF';
  return 'MIDPOINT';
}

function bbSignalClass(pct) {
  if (pct == null) return 'neutral';
  if (pct >= 90)  return 'bear';
  if (pct <= 10)  return 'bull';
  return 'neutral';
}

function macdClass(cross) {
  if (!cross) return 'neutral';
  if (cross.includes('BULL')) return 'bull';
  if (cross.includes('BEAR')) return 'bear';
  return 'neutral';
}

function setupColorMap(type, direction) {
  const map = {
    BREAKOUT:           'var(--accent-primary)',
    PULLBACK:           'var(--bull)',
    TREND_CONTINUATION: 'var(--accent-cyan)',
    REVERSAL_ATTEMPT:   'var(--accent-purple)',
    OVEREXTENDED:       'var(--bear)',
    NO_TRADE:           'var(--text-dim)',
  };
  return map[type] || 'var(--text-primary)';
}


function fmt$(val) {
  if (val == null || val === 0) return '—';
  return `$${Number(val).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}`;
}

function fmtVol(val) {
  if (!val) return '—';
  if (val >= 1e9)  return `${(val/1e9).toFixed(2)}B`;
  if (val >= 1e6)  return `${(val/1e6).toFixed(2)}M`;
  if (val >= 1e3)  return `${(val/1e3).toFixed(1)}K`;
  return val.toLocaleString();
}

function fmtPnl(val) {
  const sign = val >= 0 ? '+' : '';
  return `${sign}$${Math.abs(val).toFixed(2)}`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? '—';
}


function showLoading(show) {
  document.getElementById('loadingScreen').style.display = show ? 'flex' : 'none';
}

function hideResults() {
  document.getElementById('resultsGrid').style.display = 'none';
  const homeCounter = document.getElementById('homeSearchCounter');
  if (homeCounter) homeCounter.style.display = '';
  const welcome = document.getElementById('welcomeState');
  if (welcome) welcome.style.display = '';
  const bioBanner = document.getElementById('bioBanner');
  if (bioBanner) bioBanner.classList.remove('bio-hidden');
  const bioFooter = document.getElementById('bioFooter');
  if (bioFooter) bioFooter.style.display = 'none';
}

function showResults() {
  document.getElementById('resultsGrid').style.display = 'grid';
  const homeCounter = document.getElementById('homeSearchCounter');
  if (homeCounter) homeCounter.style.display = 'none';
  const welcome = document.getElementById('welcomeState');
  if (welcome) welcome.style.display = 'none';
  const bioBanner = document.getElementById('bioBanner');
  if (bioBanner) bioBanner.classList.add('bio-hidden');
  const bioFooter = document.getElementById('bioFooter');
  if (bioFooter) bioFooter.style.display = '';
}

function showError(msg) {
  const banner = document.getElementById('errorBanner');
  document.getElementById('errorText').textContent = msg;
  banner.style.display = 'flex';
}

function hideError() {
  document.getElementById('errorBanner').style.display = 'none';
}

let loadingStepIndex = 0;
let loadingInterval  = null;
function animateLoadingSteps() {
  const steps = document.querySelectorAll('.l-step');
  steps.forEach(s => s.className = 'l-step');
  loadingStepIndex = 0;
  clearInterval(loadingInterval);
  loadingInterval = setInterval(() => {
    if (loadingStepIndex < steps.length) {
      if (loadingStepIndex > 0) steps[loadingStepIndex - 1].className = 'l-step done';
      steps[loadingStepIndex].className = 'l-step active';
      loadingStepIndex++;
    } else {
      clearInterval(loadingInterval);
    }
  }, 600);
}


document.addEventListener('DOMContentLoaded', () => {
  initAuth();
  const newPaperTradeBtn = document.getElementById('newPaperTradeBtn');
  if (newPaperTradeBtn) {
    newPaperTradeBtn.addEventListener('click', () => {
      if (!currentAnalysis) {
        showError('Run a scanner analysis first so the paper trade has a documented research context.');
        activateTab('scanner');
        return;
      }
      openPaperModal(currentAnalysis);
    });
  }
  const btRunBtn = document.getElementById('btRunBtn');
  if (btRunBtn) {
    btRunBtn.addEventListener('click', runBacktest);
    document.getElementById('btTicker').addEventListener('keydown', e => {
      if (e.key === 'Enter') runBacktest();
    });
  }
});

async function runBacktest() {
  const ticker   = (document.getElementById('btTicker').value || '').trim().toUpperCase();
  const period   = document.getElementById('btPeriod').value;
  const minScore = parseFloat(document.getElementById('btMinScore').value) || 55;
  const setupFil = document.getElementById('btSetup').value;

  if (!ticker) { alert('Enter a ticker first.'); return; }
  if (!currentUser) {
    openAuthModal('login');
    showError('Sign in and connect a provider key to run historical research.');
    return;
  }
  if (!await requireProviderKey({type: 'backtest'}, 'auto')) return;

  const btn = document.getElementById('btRunBtn');
  btn.disabled    = true;
  btn.textContent = '⟳ RUNNING...';
  document.getElementById('btLoading').style.display  = 'flex';
  document.getElementById('btResults').style.display  = 'none';

  try {
    const provider = directProviderFor('auto');
    document.getElementById('btLoading').textContent = `Loading completed daily history directly from ${provider === 'polygon' ? 'Polygon / Massive' : 'Twelve Data'}…`;
    const market = await fetchDirectMarketBars(ticker, period, provider, 222);
    document.getElementById('btLoading').textContent = 'Testing fixed rules with next-session entries and modeled costs…';
    const data = await API.backtest.runUploaded({
      ticker,
      period,
      min_score: minScore,
      setups: setupFil ? [setupFil] : [],
      engine_mode: PUBLIC_ANALYSIS_ENGINE,
      provider: market.provider,
      bars: market.bars,
    });
    renderBacktestResults(data);
    document.getElementById('btResults').style.display = 'block';
  } catch (e) {
    alert(`Backtest error: ${e.message || 'The research run could not finish.'}`);
  } finally {
    document.getElementById('btLoading').style.display = 'none';
    btn.disabled    = false;
    btn.innerHTML   = '<span class="btn-icon">⬡</span> RUN BACKTEST';
  }
}

function renderBacktestResults(data) {
  const stats = data.stats || {};
  const trades = data.trades || [];

  const statCards = [
    { label: 'TOTAL TRADES',    val: stats.total || 0,    color: 'var(--text-primary)', sub: `over ${data.candles_tested} days` },
    { label: 'WIN RATE',        val: `${(stats.win_rate||0).toFixed(1)}%`, color: stats.win_rate >= 50 ? 'var(--bull)' : 'var(--bear)', sub: `${stats.wins}W / ${stats.losses}L` },
    { label: 'AVG WIN',         val: `+${(stats.avg_win_pct||0).toFixed(2)}%`,  color: 'var(--bull)', sub: `max: +${(stats.max_win_pct||0).toFixed(2)}%` },
    { label: 'AVG LOSS',        val: `${(stats.avg_loss_pct||0).toFixed(2)}%`, color: 'var(--bear)', sub: `max: ${(stats.max_loss_pct||0).toFixed(2)}%` },
    { label: 'EXPECTANCY',      val: `${stats.expectancy >= 0 ? '+' : ''}${(stats.expectancy||0).toFixed(2)}%`, color: (stats.expectancy||0) >= 0 ? 'var(--bull)' : 'var(--bear)', sub: 'per trade avg' },
    { label: 'PROFIT FACTOR',   val: (stats.profit_factor||0).toFixed(2), color: (stats.profit_factor||1) >= 1.5 ? 'var(--bull)' : (stats.profit_factor||1) >= 1 ? 'var(--neutral)' : 'var(--bear)', sub: '>1.5 = edge' },
    { label: 'MIN SCORE USED',  val: data.min_score_used || '—', color: 'var(--accent-primary)', sub: 'quality threshold' },
    { label: 'AVG HOLD',        val: `${(stats.avg_hold_candles||0).toFixed(1)}d`, color: 'var(--text-primary)', sub: 'trading days' },
  ];

  document.getElementById('btStatsGrid').innerHTML = statCards.map(c => `
    <div class="bt-stat-card">
      <div class="bt-stat-label">${c.label}</div>
      <div class="bt-stat-val" style="color:${c.color}">${c.val}</div>
      <div class="bt-stat-sub">${c.sub}</div>
    </div>`).join('');

  const breakdown = stats.setup_breakdown || {};
  const bdGrid    = document.getElementById('btSetupBreakdown');
  if (Object.keys(breakdown).length) {
    bdGrid.innerHTML = Object.entries(breakdown)
      .sort(([,a],[,b]) => b.total - a.total)
      .map(([name, v]) => `
        <div class="bt-breakdown-row">
          <span class="bt-breakdown-name">${name.replace(/_/g,' ')}</span>
          <div class="bt-breakdown-stats">
            <div style="color:${v.win_rate >= 50 ? 'var(--bull)' : 'var(--bear)'};">${v.win_rate}% WR</div>
            <div style="color:var(--text-dim);font-size:10px;">${v.wins}/${v.total} trades</div>
          </div>
        </div>`).join('');
  } else {
    bdGrid.innerHTML = '<div style="color:var(--text-dim);padding:10px;">No data.</div>';
  }

  const exits  = stats.by_exit_reason || {};
  const colors = { TARGET_HIT: 'var(--bull)', STOP_HIT: 'var(--bear)', TIME_EXIT: 'var(--neutral)', END_OF_DATA: 'var(--text-dim)' };
  document.getElementById('btExitReasons').innerHTML = Object.entries(exits).map(([reason, count]) => `
    <div class="bt-exit-item">
      <div class="bt-exit-reason">${reason.replace(/_/g,' ')}</div>
      <div class="bt-exit-count" style="color:${colors[reason]||'var(--text-primary)'}">${count}</div>
    </div>`).join('');

  document.getElementById('btTradeCount').textContent = `${trades.length} trades`;
  const logEl = document.getElementById('btTradeLog');
  logEl.innerHTML = `
    <div class="bt-trade-header">
      <span>DATE IN</span><span>SETUP</span><span>DIR</span>
      <span>ENTRY</span><span>STOP</span><span>TARGET</span><span>PNL%</span><span>EXIT</span>
    </div>` + trades.map(t => {
      const pnl   = t.pnl_pct || 0;
      const color = pnl >= 0 ? 'var(--bull)' : 'var(--bear)';
      return `
        <div class="bt-trade-row ${t.winner ? 'win' : 'loss'}">
          <span class="bt-col-dim">${t.date_in || '—'}</span>
          <span style="font-size:10px;">${(t.setup_type||'—').replace(/_/g,' ')}</span>
          <span style="color:${t.direction==='LONG'?'var(--bull)':'var(--bear)'}">${t.direction}</span>
          <span>${fmt$(t.entry)}</span>
          <span style="color:var(--bear)">${fmt$(t.stop)}</span>
          <span style="color:var(--bull)">${fmt$(t.target)}</span>
          <span style="color:${color};font-weight:700;">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</span>
          <span class="bt-col-dim" style="font-size:10px;">${(t.exit_reason||'—').replace(/_/g,' ')}</span>
        </div>`;
    }).join('');
}

document.addEventListener('DOMContentLoaded', () => { const b=document.getElementById('syncBetaCounterBtn'); if (b) b.addEventListener('click', syncDocumentedBetaCounter); });
