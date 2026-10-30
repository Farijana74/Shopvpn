(function () {
  'use strict';

  /* نقشه‌ی زنده‌ی شبکه — نسخه‌ی فروشگاه (هم‌شکل با «نقشه زنده شبکه» پنل ادمین).
     نکته CSP: style-src 'self' است؛ پس هیچ style="" داخل HTML نمی‌نویسیم و
     موقعیت‌ها را با el.style (CSSOM) ست می‌کنیم. */

  var LS_SET = 'sv-netmap-settings-v2';
  var LS_LAND = 'sv-netmap-land-v1';
  var W = 1000;
  var B = { L: -120, R: 162, T: 76, B: -58 };
  var MOBILE_MQ = '(max-width:720px)';

  /* ------------------------------------------------------------ flags --- */
  var FLAGS = {
    ir: '<rect width="24" height="8" fill="#239f40"/><rect y="8" width="24" height="8" fill="#fff"/><rect y="16" width="24" height="8" fill="#da0000"/><circle cx="12" cy="12" r="2.2" fill="none" stroke="#da0000" stroke-width="1.2"/>',
    tr: '<rect width="24" height="24" fill="#e30a17"/><circle cx="10" cy="12" r="5.6" fill="#fff"/><circle cx="11.8" cy="12" r="4.5" fill="#e30a17"/><circle cx="15.6" cy="12" r="1.5" fill="#fff"/>',
    ae: '<rect width="24" height="8" fill="#00732f"/><rect y="8" width="24" height="8" fill="#fff"/><rect y="16" width="24" height="8" fill="#000"/><rect width="7" height="24" fill="#f00"/>',
    de: '<rect width="24" height="8" fill="#000"/><rect y="8" width="24" height="8" fill="#d00"/><rect y="16" width="24" height="8" fill="#ffce00"/>',
    nl: '<rect width="24" height="8" fill="#ae1c28"/><rect y="8" width="24" height="8" fill="#fff"/><rect y="16" width="24" height="8" fill="#21468b"/>',
    gb: '<rect width="24" height="24" fill="#012169"/><path d="M0 0l24 24M24 0L0 24" stroke="#fff" stroke-width="4.5"/><path d="M0 0l24 24M24 0L0 24" stroke="#c8102e" stroke-width="1.6"/><path d="M12 0v24M0 12h24" stroke="#fff" stroke-width="7"/><path d="M12 0v24M0 12h24" stroke="#c8102e" stroke-width="4"/>',
    us: '<rect width="24" height="24" fill="#fff"/><g fill="#b22234"><rect y="0" width="24" height="3.4"/><rect y="6.9" width="24" height="3.4"/><rect y="13.7" width="24" height="3.4"/><rect y="20.6" width="24" height="3.4"/></g><rect width="12" height="13.7" fill="#3c3b6e"/>',
    jp: '<rect width="24" height="24" fill="#fff"/><circle cx="12" cy="12" r="6.6" fill="#bc002d"/>',
    sg: '<rect width="24" height="12" fill="#ef3340"/><rect y="12" width="24" height="12" fill="#fff"/><circle cx="8.2" cy="6" r="3.6" fill="#fff"/><circle cx="9.8" cy="6" r="3.2" fill="#ef3340"/>',
    br: '<rect width="24" height="24" fill="#009c3b"/><path d="M12 3l10 9-10 9L2 12z" fill="#ffdf00"/><circle cx="12" cy="12" r="4.6" fill="#002776"/><path d="M7.6 11.2q4.4-1.6 8.8 1.6" fill="none" stroke="#fff" stroke-width="1"/>'
  };
  function flag(cc) {
    if (!FLAGS[cc]) return '<span class="nm-flag nm-flag-t" aria-hidden="true">' + String(cc || '?').toUpperCase().slice(0, 2) + '</span>';
    return '<span class="nm-flag"><svg viewBox="0 0 24 24" aria-hidden="true">' + FLAGS[cc] + '</svg></span>';
  }

  /* ------------------------------------------------------------ icons --- */
  var ICON = {
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.8 3.9 5.8 3.9 9s-1.3 6.2-3.9 9c-2.6-2.8-3.9-5.8-3.9-9S9.4 5.8 12 3z"/>',
    cloudUp: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4 4 0 0 1 .5 7.97"/><path d="M12 20v-7m0 0-3 3m3-3 3 3"/>',
    cloudOff: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4 4 0 0 1 .5 7.97"/><path d="m10 12 4 4m0-4-4 4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    swap: '<path d="M7 7h12m0 0-3-3m3 3-3 3M17 17H5m0 0 3-3m-3 3 3 3"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    locate: '<circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="7.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    arrow: '<path d="M5 12h14m0 0-5-5m5 5-5 5"/>'
  };
  function icon(n) {
    return '<svg class="nm-i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICON[n] + '</svg>';
  }

  /* ------------------------------------------------------------- data --- */
  var HUB = { id: 'tehran', cc: 'ir', city: 'Tehran', fa: 'تهران', lat: 35.69, lon: 51.39, st: 'ok', a: 'tr', hub: true, products: [], total: 0, stock: 0, last: '' };
  var SERVERS = [HUB];
  var BY = { tehran: HUB };
  var ANCHORS = ['tr', 'br', 'bl', 'tl'];

  function autoAnchor(s, i) {
    if (s.lon > 100) return i % 2 ? 't' : 'tl';
    if (s.lon < -30) return 'r';
    return ANCHORS[i % ANCHORS.length];
  }

  function applyData(j) {
    var list = (j && j.nodes) || [], out = [HUB], by = { tehran: HUB };
    list.forEach(function (n, i) {
      var cc = String(n.cc || '').toLowerCase(), city = n.city || cc.toUpperCase();
      var s = {
        id: String(n.id), cc: cc, city: city, fa: n.city_fa || city, lat: n.lat, lon: n.lon,
        st: n.status === 'down' ? 'down' : 'ok', products: n.products || [], total: n.products_total || 0,
        stock: n.products_in_stock || 0, last: n.last_check || ''
      };
      s.a = autoAnchor(s, i);
      out.push(s); by[s.id] = s;
    });
    SERVERS = out; BY = by;
  }
  function signature() {
    return SERVERS.map(function (s) { return s.id + ':' + s.st + ':' + s.stock + ':' + s.total; }).join('|');
  }

  /* ------------------------------------------------------------- text --- */
  var TXT = {
    fa: {
      title: 'نقشه زنده شبکه', sub: 'سرورهای واقعی فروشگاه و وضعیت هر لوکیشن | محصولات موجود روی هر مسیر',
      upd: 'آخرین بروزرسانی', sec: 'ثانیه', min: 'دقیقه', prod: 'محصولات موجود', locs: 'لوکیشن‌ها', off: 'گره‌های آفلاین', on: 'گره‌های آنلاین',
      list: 'لیست سرورها', search: 'جستجوی سرور...', empty: 'سروری پیدا نشد', nodata: 'هنوز لوکیشنی برای نمایش ثبت نشده است.', err: 'دریافت اطلاعات نقشه ممکن نشد. دوباره تلاش کن.',
      lgOk: 'مسیر فعال', lgDown: 'مسیر قطع شده', lgOn: 'گره آنلاین', lgOff: 'گره آفلاین',
      zin: 'بزرگ‌نمایی', zout: 'کوچک‌نمایی', zhome: 'مرکز نقشه',
      det: 'جزئیات مسیر انتخابی', active: 'فعال', down: 'آفلاین', status: 'وضعیت', checked: 'آخرین بررسی', products: 'محصولات', stock: 'موجود', noprod: 'محصولی روی این لوکیشن ثبت نشده است.', soldout: 'ناموجود', plist: 'محصولات این لوکیشن',
      routes: 'مسیرهای موجود', rec: 'پیشنهادی', gb: 'گیگ', days: 'روز', cur: 'تومان',
      set: 'تنظیمات نمایش', sPk: 'نمایش بسته‌های در حال انتقال', sAnim: 'سرعت انیمیشن بسته‌ها', sTour: 'گردش خودکار دوربین', sTourSec: 'فاصله‌ی گردش',
      speeds: ['کند', 'متوسط', 'سریع'], auto: 'بروزرسانی خودکار', save: 'ذخیره تنظیمات', saved: 'ذخیره شد',
      note: 'وضعیت گره‌ها از بررسی دوره‌ای سرورهای فروشگاه گرفته می‌شود.'
    },
    en: {
      title: 'Live Network Map', sub: 'Real store servers and the status of each location | In-stock products on every route',
      upd: 'Last update', sec: 's', min: 'min', prod: 'Products in stock', locs: 'Locations', off: 'Offline nodes', on: 'Online nodes',
      list: 'Servers', search: 'Search servers...', empty: 'No server found', nodata: 'No locations to show yet.', err: 'Could not load map data. Try again.',
      lgOk: 'Active route', lgDown: 'Disconnected route', lgOn: 'Online node', lgOff: 'Offline node',
      zin: 'Zoom in', zout: 'Zoom out', zhome: 'Center map',
      det: 'Selected route', active: 'Active', down: 'Offline', status: 'Status', checked: 'Last check', products: 'Products', stock: 'In stock', noprod: 'No products on this location.', soldout: 'Sold out', plist: 'Products at this location',
      routes: 'Available routes', rec: 'Recommended', gb: 'GB', days: 'days', cur: 'Toman',
      set: 'Display settings', sPk: 'Show moving packets', sAnim: 'Packet animation speed', sTour: 'Automatic camera tour', sTourSec: 'Tour interval',
      speeds: ['Slow', 'Medium', 'Fast'], auto: 'Auto refresh', save: 'Save settings', saved: 'Saved',
      note: 'Node status comes from periodic checks of the store servers.'
    }
  };

  /* ---------------------------------------------------------- helpers --- */
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function hm(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function mX(lon) { return lon * Math.PI / 180; }
  function mY(lat) { return 1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * lat * Math.PI / 180)); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function secLabel(T, n) { return n >= 120 ? (n / 60) + ' ' + T.min : n + ' ' + T.sec; }

  function loadSettings() {
    var d = { packets: true, speed: 2, refresh: 60, tour: true, tourSec: 60 };
    try {
      var o = JSON.parse(localStorage.getItem(LS_SET) || 'null');
      if (o && typeof o === 'object') {
        if (typeof o.packets === 'boolean') d.packets = o.packets;
        if (typeof o.tour === 'boolean') d.tour = o.tour;
        if (o.speed >= 1 && o.speed <= 3) d.speed = o.speed | 0;
        if ([30, 60, 120, 300].indexOf(o.refresh) > -1) d.refresh = o.refresh;
        if ([30, 60, 120].indexOf(o.tourSec) > -1) d.tourSec = o.tourSec;
      }
    } catch (e) {}
    return d;
  }

  /* -------------------------------------------------------- land (geo) --- */
  var LAND = null;
  var landPromise = null;
  function parseLand(d) {
    var rings = [], re = /([MLZ])([^MLZ]*)/g, m, cur = null;
    while ((m = re.exec(d))) {
      if (m[1] === 'M') { cur = []; rings.push(cur); }
      if (m[1] === 'Z' || !cur) continue;
      var p = m[2].split(',');
      if (p.length >= 2) cur.push([parseFloat(p[0]) * 0.36 - 180, 90 - parseFloat(p[1]) * 0.36]);
    }
    return rings;
  }
  function ensureLand() {
    if (LAND) return Promise.resolve(LAND);
    if (landPromise) return landPromise;
    landPromise = new Promise(function (resolve) {
      try {
        var c = localStorage.getItem(LS_LAND);
        if (c) { LAND = parseLand(c); resolve(LAND); return; }
      } catch (e) {}
      function get(retry) {
        fetch('/api/web/world-map', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (j) {
          if (j && j.ok && j.land_path) {
            try { localStorage.setItem(LS_LAND, j.land_path); } catch (e) {}
            LAND = parseLand(j.land_path);
            resolve(LAND);
          } else if (retry) { setTimeout(function () { get(false); }, 4000); } else { resolve(null); }
        }).catch(function () {
          if (retry) { setTimeout(function () { get(false); }, 4000); } else { resolve(null); }
        });
      }
      get(true);
    });
    return landPromise;
  }

  /* ----------------------------------------------------------- widget --- */
  function createNetworkMap() {
    var fa = document.documentElement.lang !== 'en';
    var T = fa ? TXT.fa : TXT.en;
    var set = loadSettings();
    var state = { sel: '', q: '', z: 1, tx: 0, ty: 0, H: 625, k: 0.8, last: new Date(), loaded: false, failed: false };
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var mq = window.matchMedia ? window.matchMedia(MOBILE_MQ) : { matches: false };
    var timer = null;
    var pkCircles = [];
    var tour = { i: 0, t: null, raf: 0, hold: 0, visible: true };

    var root = document.createElement('section');
    root.id = 'shopvpn-network-map';
    root.className = 'network-map-wrap home-network-map';
    root.setAttribute('aria-labelledby', 'network-map-title');

    function name(s) { return fa ? s.fa : s.city; }
    function tag(s) { return s.city + (s.cc ? ' (' + s.cc.toUpperCase() + ')' : ''); }
    function money(n) { return Number(n).toLocaleString(fa ? 'fa-IR' : 'en-US') + ' ' + T.cur; }
    function num(n) { return Number(n).toLocaleString(fa ? 'fa-IR' : 'en-US'); }
    function fmtTime(iso) {
      var d = iso ? new Date(iso) : null;
      return d && !isNaN(d.getTime()) ? hm(d) : '—';
    }
    function nodes() { return SERVERS.filter(function (s) { return !s.hub; }); }

    root.innerHTML =
      '<div class="nm-app">' +
        '<header class="nm-head"><span class="nm-globe">' + icon('globe') + '</span><div><h2 id="network-map-title">' + T.title + '</h2><p>' + T.sub + '</p></div></header>' +
        '<div class="nm-stats">' +
          '<div class="nm-upd"><button class="nm-refresh" type="button" data-act="refresh">' + icon('refresh') + '<span data-k="interval"></span></button>' +
            '<div class="nm-upd-t"><small>' + T.upd + '</small><b data-k="time">--:--</b></div></div>' +
          '<div class="nm-stat s-blue"><div class="nm-stat-t"><small>' + T.prod + '</small><b data-k="prodStat">—</b></div><span class="nm-ico">' + icon('swap') + '</span></div>' +
          '<div class="nm-stat s-purple"><div class="nm-stat-t"><small>' + T.locs + '</small><b data-k="locStat">—</b></div><span class="nm-ico">' + icon('globe') + '</span></div>' +
          '<div class="nm-stat s-red"><div class="nm-stat-t"><small>' + T.off + '</small><b data-k="offStat">—</b></div><span class="nm-ico">' + icon('cloudOff') + '</span></div>' +
          '<div class="nm-stat s-green"><div class="nm-stat-t"><small>' + T.on + '</small><b data-k="onStat">—</b></div><span class="nm-ico">' + icon('cloudUp') + '</span></div>' +
        '</div>' +
        '<div class="nm-main">' +
          '<aside class="nm-card nm-list"><h3>' + T.list + '</h3>' +
            '<label class="nm-search"><input type="search" data-k="q" placeholder="' + T.search + '" autocomplete="off" aria-label="' + T.search + '">' + icon('search') + '</label>' +
            '<div class="nm-list-scroll"><ul data-k="list"></ul></div></aside>' +
          '<div class="nm-card nm-mapcard"><div class="nm-map" data-k="map">' +
            '<div class="nm-stage" data-k="stage"><svg class="nm-svg" data-k="svg" preserveAspectRatio="none" aria-hidden="true"></svg><div class="nm-nodes" data-k="nodes"></div></div>' +
            '<div class="nm-zoom"><button type="button" data-act="zin" aria-label="' + T.zin + '">' + icon('plus') + '</button><button type="button" data-act="zout" aria-label="' + T.zout + '">' + icon('minus') + '</button>' +
              '<button type="button" class="loc" data-act="zhome" aria-label="' + T.zhome + '">' + icon('locate') + '</button></div>' +
          '</div>' +
          '<div class="nm-legend"><span><i class="lg l-ok"></i>' + T.lgOk + '</span><span><i class="lg l-down"></i>' + T.lgDown + '</span>' +
              '<span><i class="lg n-on"></i>' + T.lgOn + '</span><span><i class="lg n-off"></i>' + T.lgOff + '</span></div>' +
          '</div>' +
        '</div>' +
        '<div class="nm-bottom">' +
          '<section class="nm-card nm-settings"><h3>' + T.set + '</h3>' +
            '<label class="nm-srow"><span>' + T.sPk + '</span><span class="nm-switch"><input type="checkbox" data-set="packets"><i></i></span></label>' +
            '<div class="nm-slide"><div class="nm-slide-h"><span>' + T.sAnim + '</span><em data-k="speedLbl"></em></div><input type="range" min="1" max="3" step="1" data-set="speed" aria-label="' + T.sAnim + '"></div>' +
            '<label class="nm-srow"><span>' + T.sTour + '</span><span class="nm-switch"><input type="checkbox" data-set="tour"><i></i></span></label>' +
            '<label class="nm-srow nm-sel"><span>' + T.sTourSec + '</span><select data-set="tourSec">' +
              [30, 60, 120].map(function (n) { return '<option value="' + n + '">' + secLabel(T, n) + '</option>'; }).join('') +
            '</select></label>' +
            '<label class="nm-srow nm-sel"><span>' + T.auto + '</span><select data-set="refresh">' +
              [30, 60, 120, 300].map(function (n) { return '<option value="' + n + '">' + secLabel(T, n) + '</option>'; }).join('') +
            '</select></label>' +
            '<button class="nm-save" type="button" data-act="save">' + icon('save') + '<span data-k="saveTxt">' + T.save + '</span></button></section>' +
          '<section class="nm-card nm-routes"><h3>' + T.routes + '</h3><div data-k="routes"></div></section>' +
          '<section class="nm-card nm-details"><div class="nm-det-h"><h3>' + T.det + '</h3><span class="nm-badge" data-k="badge"></span></div>' +
            '<div class="nm-chip" data-k="chip"></div>' +
            '<div class="nm-metrics" data-k="metrics"></div>' +
            '<div class="nm-plist-wrap"><small>' + T.plist + '</small><div class="nm-plist" data-k="plist"></div></div></section>' +
        '</div>' +
        '<p class="nm-note">' + T.note + '</p>' +
      '</div>';

    function $(k) { return root.querySelector('[data-k="' + k + '"]'); }
    var el = {
      map: $('map'), stage: $('stage'), svg: $('svg'), nodes: $('nodes'), list: $('list'), q: $('q'), routes: $('routes'),
      badge: $('badge'), chip: $('chip'), metrics: $('metrics'), plist: $('plist'), time: $('time'),
      prodStat: $('prodStat'), locStat: $('locStat'), offStat: $('offStat'), onStat: $('onStat'),
      interval: $('interval'), speedLbl: $('speedLbl'), saveTxt: $('saveTxt')
    };

    var mxL = mX(B.L), mxR = mX(B.R), myT = mY(B.T), myB = mY(B.B);
    function proj(lon, lat) {
      return [(mX(lon) - mxL) / (mxR - mxL) * W, (myT - mY(lat)) / (myT - myB) * state.H];
    }
    function ctrl(a, b) {
      var mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1];
      var len = Math.sqrt(dx * dx + dy * dy) || 1, nx = -dy / len, ny = dx / len;
      if (ny > 0) { nx = -nx; ny = -ny; }
      var off = len * 0.22;
      return [mx + nx * off, my + ny * off];
    }
    function arc(a, b) {
      var c = ctrl(a, b);
      return 'M' + a[0].toFixed(1) + ',' + a[1].toFixed(1) + ' Q' + c[0].toFixed(1) + ',' + c[1].toFixed(1) + ' ' + b[0].toFixed(1) + ',' + b[1].toFixed(1);
    }

    function landPathD() {
      if (!LAND) return '';
      var out = [], i, j, ring, pts, x, y, keep;
      for (i = 0; i < LAND.length; i++) {
        ring = LAND[i]; keep = false; pts = [];
        for (j = 0; j < ring.length; j++) {
          var lon = ring[j][0], lat = ring[j][1];
          if (lon > B.L - 12 && lon < B.R + 12 && lat > B.B - 12 && lat < B.T + 12) keep = true;
          var p = proj(lon, lat); x = p[0]; y = p[1];
          pts.push((j ? 'L' : 'M') + x.toFixed(1) + ',' + y.toFixed(1));
        }
        if (keep && pts.length > 2) out.push(pts.join('') + 'Z');
      }
      return out.join('');
    }

    function drawMap() {
      state.H = mq.matches ? 760 : 625;
      el.svg.setAttribute('viewBox', '0 0 ' + W + ' ' + state.H);
      var hub = proj(HUB.lon, HUB.lat);
      var h = '<defs>' +
        '<pattern id="nm-dots" patternUnits="userSpaceOnUse" width="7" height="7"><circle cx="3.5" cy="3.5" r="1" fill="#3f7ad8" fill-opacity=".5"/></pattern>' +
        '<filter id="nm-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
      nodes().forEach(function (s) {
        var p = proj(s.lon, s.lat);
        var stops = s.st === 'down' ? '<stop offset="0" stop-color="#2de2a6"/><stop offset=".55" stop-color="#ffb23f"/><stop offset="1" stop-color="#ff4d5e"/>'
          : '<stop offset="0" stop-color="#2de2a6"/><stop offset="1" stop-color="#3ff0c0"/>';
        h += '<linearGradient id="nm-g-' + esc(s.id) + '" gradientUnits="userSpaceOnUse" x1="' + hub[0].toFixed(1) + '" y1="' + hub[1].toFixed(1) + '" x2="' + p[0].toFixed(1) + '" y2="' + p[1].toFixed(1) + '">' + stops + '</linearGradient>';
      });
      h += '</defs><g class="nm-grid">';
      var lon, lat, p0, p1;
      for (lon = -120; lon <= 160; lon += 20) { p0 = proj(lon, B.T); p1 = proj(lon, B.B); h += '<line x1="' + p0[0].toFixed(1) + '" y1="0" x2="' + p1[0].toFixed(1) + '" y2="' + state.H + '"/>'; }
      for (lat = -40; lat <= 70; lat += 20) { p0 = proj(B.L, lat); h += '<line x1="0" y1="' + p0[1].toFixed(1) + '" x2="' + W + '" y2="' + p0[1].toFixed(1) + '"/>'; }
      h += '</g>';
      var d = landPathD();
      if (d) h += '<g class="nm-land"><path class="land-fill" d="' + d + '"/><path class="land-dots" d="' + d + '" fill="url(#nm-dots)"/></g>';
      h += '<g class="nm-routes-g">';
      nodes().forEach(function (s) {
        var p = proj(s.lon, s.lat), dd = arc(hub, p);
        h += '<path class="nm-route' + (s.st === 'down' ? ' down' : '') + (s.id === state.sel ? ' sel' : '') + '" data-rid="' + esc(s.id) + '" d="' + dd + '" stroke="url(#nm-g-' + esc(s.id) + ')"/>';
      });
      h += '</g><g class="nm-packets" data-k="pkG">';
      if (!reduce) {
        var mul = [1.7, 1, 0.55][set.speed - 1];
        nodes().forEach(function (s, idx) {
          if (s.st === 'down') return;
          var p = proj(s.lon, s.lat), dd = arc(hub, p), dur = (3.4 + (idx % 4) * 0.6) * mul;
          for (var n = 0; n < 3; n++) {
            h += '<circle class="nm-pk" r="3" fill="#ffffff" filter="url(#nm-glow)"><animateMotion dur="' + dur.toFixed(2) + 's" begin="-' + (n * dur / 3 + idx * 0.37).toFixed(2) + 's" repeatCount="indefinite" path="' + dd + '"/></circle>';
          }
        });
      }
      h += '</g>';
      el.svg.innerHTML = h;
      pkCircles = Array.prototype.slice.call(el.svg.querySelectorAll('.nm-pk'));
      applySvgPrefs();
      drawNodes();
    }

    function applySvgPrefs() {
      var pk = el.svg.querySelector('.nm-packets');
      if (pk) pk.style.display = set.packets ? '' : 'none';
      sizePackets();
    }
    function sizePackets() {
      var w = el.map.clientWidth || 800;
      state.k = w / W;
      var r = (3.1 / state.k / state.z).toFixed(2);
      pkCircles.forEach(function (c) { c.setAttribute('r', r); });
    }

    function drawNodes() {
      var h = '';
      SERVERS.forEach(function (s) {
        var cls = 'nm-node' + (s.hub ? ' hub' : '') + (s.st === 'down' ? ' bad' : '') + (s.id === state.sel ? ' sel' : '');
        var lab = s.a ? '<span class="nm-label a-' + s.a + '">' + flag(s.cc) + '<span class="nm-lt"><b>' + esc(s.city) + '</b>' + (s.hub ? '' : '<small class="' + (s.st === 'down' ? 'c-bad' : 'c-ok') + '">' + num(s.stock) + '/' + num(s.total) + '</small>') + '</span></span>' : '';
        h += '<button type="button" class="' + cls + '" data-node="' + esc(s.id) + '" data-lon="' + s.lon + '" data-lat="' + s.lat + '" aria-label="' + esc(tag(s)) + '">' +
          '<i class="nm-dot"></i>' + (s.hub ? '<i class="nm-ring r1"></i><i class="nm-ring r2"></i>' : '') + lab + '</button>';
      });
      el.nodes.innerHTML = h;
      Array.prototype.forEach.call(el.nodes.querySelectorAll('.nm-node'), function (n) {
        var p = proj(parseFloat(n.getAttribute('data-lon')), parseFloat(n.getAttribute('data-lat')));
        n.style.left = (p[0] / W * 100).toFixed(3) + '%';
        n.style.top = (p[1] / state.H * 100).toFixed(3) + '%';
      });
    }

    function applyView() {
      var w = el.map.clientWidth, hh = el.map.clientHeight;
      state.tx = Math.min(0, Math.max(w - w * state.z, state.tx));
      state.ty = Math.min(0, Math.max(hh - hh * state.z, state.ty));
      el.stage.style.transform = 'translate(' + state.tx.toFixed(1) + 'px,' + state.ty.toFixed(1) + 'px) scale(' + state.z.toFixed(3) + ')';
      el.stage.style.setProperty('--iz', (1 / state.z).toFixed(4));
      sizePackets();
    }
    function zoomTo(z2, cx, cy) {
      z2 = Math.max(1, Math.min(3.2, z2));
      var r = z2 / state.z;
      state.tx = cx - (cx - state.tx) * r;
      state.ty = cy - (cy - state.ty) * r;
      state.z = z2;
      applyView();
    }

    function viewFor(stop) {
      if (stop.home) return { z: 1, tx: 0, ty: 0 };
      var w = el.map.clientWidth, hh = el.map.clientHeight, p = proj(stop.lon, stop.lat), z = stop.z;
      return { z: z, tx: w / 2 - (p[0] / W * w) * z, ty: hh / 2 - (p[1] / state.H * hh) * z };
    }
    function flyTo(v, ms) {
      cancelAnimationFrame(tour.raf);
      if (reduce || !ms) { state.z = v.z; state.tx = v.tx; state.ty = v.ty; applyView(); return; }
      var a = { z: state.z, tx: state.tx, ty: state.ty }, t0 = performance.now();
      function step(now) {
        var k = Math.min(1, (now - t0) / ms), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        state.z = a.z + (v.z - a.z) * e;
        state.tx = a.tx + (v.tx - a.tx) * e;
        state.ty = a.ty + (v.ty - a.ty) * e;
        applyView();
        if (k < 1) tour.raf = requestAnimationFrame(step);
      }
      tour.raf = requestAnimationFrame(step);
    }
    function zoomHome() {
      cancelAnimationFrame(tour.raf);
      var v = viewFor({ lon: HUB.lon, lat: HUB.lat, z: Math.max(state.z, 1.7) });
      state.z = v.z; state.tx = v.tx; state.ty = v.ty;
      applyView();
    }

    function tourStops() {
      var a = [{ lon: HUB.lon, lat: HUB.lat, z: 2.6 }];
      nodes().forEach(function (s) { a.push({ lon: s.lon, lat: s.lat, z: 2.2, id: s.id }); });
      if (a.length > 1) a.push({ home: true });
      return a;
    }
    function holdTour() { tour.hold = Date.now() + set.tourSec * 1000; }
    function tourNext() {
      if (!root.isConnected) { stopTour(); return; }
      if (document.hidden || !tour.visible || Date.now() < tour.hold || drag) return;
      var list = tourStops();
      if (list.length < 2) return;
      tour.i = (tour.i + 1) % list.length;
      var st = list[tour.i];
      if (st.id) select(st.id);
      flyTo(viewFor(st), 2200);
    }
    function stopTour() { if (tour.t) { clearInterval(tour.t); tour.t = null; } }
    function startTour() {
      stopTour();
      if (!set.tour || reduce) return;
      tour.t = setInterval(tourNext, set.tourSec * 1000);
    }

    var drag = null;
    el.map.addEventListener('pointerdown', function (e) {
      holdTour();
      if (state.z <= 1 || e.target.closest('.nm-zoom,.nm-node,.nm-legend')) return;
      cancelAnimationFrame(tour.raf);
      drag = { x: e.clientX, y: e.clientY, tx: state.tx, ty: state.ty, id: e.pointerId };
      try { el.map.setPointerCapture(e.pointerId); } catch (err) {}
      el.map.classList.add('drag');
    });
    el.map.addEventListener('pointermove', function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      state.tx = drag.tx + (e.clientX - drag.x);
      state.ty = drag.ty + (e.clientY - drag.y);
      applyView();
    });
    function endDrag(e) { if (drag && (!e || e.pointerId === drag.id)) { drag = null; el.map.classList.remove('drag'); } }
    el.map.addEventListener('pointerup', endDrag);
    el.map.addEventListener('pointercancel', endDrag);

    function renderList() {
      var q = state.q.trim().toLowerCase();
      var rows = nodes().filter(function (s) { return !q || (s.city + ' ' + s.cc + ' ' + s.fa).toLowerCase().indexOf(q) > -1; });
      el.list.innerHTML = rows.length ? rows.map(function (s) {
        return '<li><button type="button" class="nm-row' + (s.id === state.sel ? ' on' : '') + '" data-sel="' + esc(s.id) + '">' + flag(s.cc) +
          '<span class="nm-rn">' + esc(fa ? s.fa : tag(s)) + '</span><em class="c-' + (s.st === 'down' ? 'bad' : 'ok') + '">' + num(s.stock) + '/' + num(s.total) + '</em>' +
          '<i class="dot d-' + (s.st === 'down' ? 'bad' : 'ok') + '"></i></button></li>';
      }).join('') : '<li class="nm-none">' + (state.failed ? T.err : state.loaded ? T.nodata : T.empty) + '</li>';
    }
    function renderRoutes() {
      var top = nodes().slice(0, 4);
      el.routes.innerHTML = top.length ? top.map(function (s, i) {
        var on = s.id === state.sel, down = s.st === 'down';
        return '<button type="button" class="nm-route-i' + (on ? ' on' : '') + '" data-sel="' + esc(s.id) + '" role="radio" aria-checked="' + on + '">' +
          '<span class="nm-rr-top">' + flag(s.cc) + '<span class="nm-rr-name">' + esc(fa ? HUB.fa : HUB.city) + '<span class="nm-arr">' + icon('arrow') + '</span>' + esc(name(s)) + '</span>' +
          (i === 0 && !down && s.stock > 0 ? '<span class="nm-rec">' + T.rec + '</span>' : '') + '<span class="nm-radio"></span></span>' +
          '<span class="nm-rr-m"><span><b>' + num(s.total) + '</b><small>' + T.products + '</small></span>' +
          '<span><b>' + num(s.stock) + '</b><small>' + T.stock + '</small></span>' +
          '<span><b class="' + (down ? 'c-bad' : 'c-ok') + '">' + (down ? T.down : T.active) + '</b><small>' + T.status + '</small></span></span></button>';
      }).join('') : '<p class="nm-none">' + (state.failed ? T.err : T.nodata) + '</p>';
    }

    function productRow(p) {
      var meta = [];
      if (p.volume_gb) meta.push(num(p.volume_gb) + ' ' + T.gb);
      if (p.duration_days) meta.push(num(p.duration_days) + ' ' + T.days);
      return '<button type="button" class="nm-prod" data-plans="1"><span class="nm-pn"><b>' + esc(p.name) + '</b><small>' + esc(meta.join(' | ')) + '</small></span>' +
        '<span class="nm-pp">' + money(p.price) + '</span><i class="dot d-' + (p.in_stock ? 'ok' : 'bad') + '" title="' + (p.in_stock ? T.stock : T.soldout) + '"></i></button>';
    }
    function renderDetails() {
      var s = BY[state.sel];
      if (!s || s.hub) {
        el.badge.className = 'nm-badge'; el.badge.innerHTML = '';
        el.chip.innerHTML = ''; el.metrics.innerHTML = '';
        el.plist.innerHTML = '<p class="nm-none">' + (state.failed ? T.err : T.nodata) + '</p>';
        return;
      }
      var down = s.st === 'down';
      el.badge.className = 'nm-badge ' + (down ? 'down' : 'ok');
      el.badge.innerHTML = '<i></i>' + (down ? T.down : T.active);
      el.chip.innerHTML = '<span>' + flag(HUB.cc) + '<b>' + esc(fa ? HUB.fa : HUB.city) + ' (IR)</b></span><span class="nm-arr">' + icon('arrow') + '</span><span>' + flag(s.cc) + '<b>' + esc(name(s)) + (s.cc ? ' (' + s.cc.toUpperCase() + ')' : '') + '</b></span>';
      var c = down ? 'c-bad' : 'c-ok';
      el.metrics.innerHTML =
        '<div><small>' + T.status + '</small><b class="' + c + '">' + (down ? T.down : T.active) + '</b></div>' +
        '<div><small>' + T.stock + '</small><b>' + num(s.stock) + '/' + num(s.total) + '</b></div>' +
        '<div><small>' + T.checked + '</small><b>' + fmtTime(s.last) + '</b></div>';
      el.plist.innerHTML = s.products.length ? s.products.map(productRow).join('') : '<p class="nm-none">' + T.noprod + '</p>';
    }

    function select(id) {
      if (!BY[id] || BY[id].hub) return;
      state.sel = id;
      Array.prototype.forEach.call(root.querySelectorAll('[data-node]'), function (n) { n.classList.toggle('sel', n.getAttribute('data-node') === id); });
      Array.prototype.forEach.call(root.querySelectorAll('.nm-route'), function (r) { r.classList.toggle('sel', r.getAttribute('data-rid') === id); });
      renderList(); renderRoutes(); renderDetails();
    }

    function setStats() {
      var ns = nodes(), stock = 0, total = 0, off = 0;
      ns.forEach(function (s) { stock += s.stock; total += s.total; if (s.st === 'down') off++; });
      el.prodStat.textContent = state.loaded ? num(stock) + ' / ' + num(total) : '—';
      el.locStat.textContent = state.loaded ? num(ns.length) : '—';
      el.offStat.textContent = state.loaded ? num(off) : '—';
      el.onStat.textContent = state.loaded ? num(ns.length - off) + ' / ' + num(ns.length) : '—';
      el.time.textContent = hm(state.last);
    }
    function labelInterval() { el.interval.textContent = secLabel(T, set.refresh); }

    function render(redraw) {
      if (!BY[state.sel] || BY[state.sel].hub) state.sel = nodes().length ? nodes()[0].id : '';
      if (redraw) drawMap();
      setStats(); renderList(); renderRoutes(); renderDetails();
    }

    function load(first) {
      var before = signature();
      return fetch('/api/web/network-map', { credentials: 'same-origin' }).then(function (r) {
        if (!r.ok) throw new Error('http');
        return r.json();
      }).then(function (j) {
        applyData(j);
        state.loaded = true; state.failed = false;
        state.last = j && j.updated_at ? new Date(j.updated_at) : new Date();
        render(first || signature() !== before);
      }).catch(function () {
        if (!state.loaded) { state.failed = true; render(false); }
      });
    }

    function tick() {
      if (!root.isConnected) { clearInterval(timer); timer = null; stopTour(); return; }
      load(false);
    }
    function schedule() {
      if (timer) clearInterval(timer);
      timer = setInterval(tick, set.refresh * 1000);
    }

    function syncSettings() {
      Array.prototype.forEach.call(root.querySelectorAll('[data-set]'), function (inp) {
        var k = inp.getAttribute('data-set');
        if (inp.type === 'checkbox') inp.checked = !!set[k];
        else inp.value = String(set[k]);
      });
      el.speedLbl.textContent = T.speeds[set.speed - 1];
      var rg = root.querySelector('input[data-set="speed"]');
      rg.style.setProperty('--p', ((set.speed - 1) / 2 * 100) + '%');
      labelInterval();
    }
    root.addEventListener('input', function (e) {
      var inp = e.target.closest ? e.target.closest('[data-set]') : null;
      if (!inp) return;
      var k = inp.getAttribute('data-set'), redraw = false;
      if (inp.type === 'checkbox') set[k] = inp.checked;
      else if (k === 'speed') { set.speed = parseInt(inp.value, 10) || 2; redraw = true; }
      else if (k === 'refresh') { set.refresh = parseInt(inp.value, 10) || 60; schedule(); }
      else if (k === 'tourSec') { set.tourSec = parseInt(inp.value, 10) || 60; }
      if (k === 'tour' || k === 'tourSec') startTour();
      syncSettings();
      if (redraw) drawMap(); else applySvgPrefs();
    });
    el.q.addEventListener('input', function () { state.q = el.q.value; renderList(); });

    root.addEventListener('click', function (e) {
      var t = e.target;
      var node = t.closest('[data-node]');
      if (node) { holdTour(); select(node.getAttribute('data-node')); return; }
      var sel = t.closest('[data-sel]');
      if (sel) { holdTour(); select(sel.getAttribute('data-sel')); return; }
      if (t.closest('[data-plans]')) {
        var plans = document.getElementById('plans');
        if (plans) plans.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' });
        return;
      }
      var act = t.closest('[data-act]');
      if (!act) return;
      var a = act.getAttribute('data-act'), w = el.map.clientWidth, hh = el.map.clientHeight;
      if (a !== 'refresh' && a !== 'save') holdTour();
      if (a === 'zin') zoomTo(state.z * 1.4, w / 2, hh / 2);
      else if (a === 'zout') zoomTo(state.z / 1.4, w / 2, hh / 2);
      else if (a === 'zhome') zoomHome();
      else if (a === 'refresh') { tick(); schedule(); }
      else if (a === 'save') {
        try { localStorage.setItem(LS_SET, JSON.stringify(set)); } catch (err) {}
        el.saveTxt.textContent = T.saved + ' ✓';
        setTimeout(function () { el.saveTxt.textContent = T.save; }, 1600);
      }
    });

    syncSettings();
    drawMap();
    setStats(); renderList(); renderRoutes(); renderDetails();
    schedule();
    applyView();
    state.z = 1;
    load(true).then(function () {
      var first = tourStops()[0];
      state.z = 1; state.tx = 0; state.ty = 0; applyView();
      flyTo(viewFor(first), 1800);
      startTour();
    });

    ensureLand().then(function (land) { if (land) drawMap(); });
    if (mq.addEventListener) mq.addEventListener('change', function () { drawMap(); applyView(); });
    if (window.IntersectionObserver) {
      new IntersectionObserver(function (en) { tour.visible = en[0].isIntersecting; }).observe(root);
    }
    if (window.ResizeObserver) {
      var lastW = 0;
      new ResizeObserver(function () {
        var w = el.map.clientWidth;
        if (w && Math.abs(w - lastW) > 2) { lastW = w; applyView(); }
      }).observe(el.map);
    }
    return root;
  }

  window.ShopVPNNetworkMap = { create: createNetworkMap };
})();
