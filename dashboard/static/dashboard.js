/* FitCoach dashboard – rendrer alt fra #fitcoach-data (se dashboard/SCHEMA.md).
   Ren vanilla JS og inline SVG, ingen biblioteker. */
(function () {
  'use strict';

  /* ───────────────────────── Hjelpere ───────────────────────── */
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function has(v) {
    if (v === null || v === undefined) return false;
    if (typeof v === 'number') return isFinite(v);
    if (typeof v === 'string') return v.trim() !== '' && v !== 'None' && v !== 'null' && v !== 'undefined' && v !== 'NaN';
    return true;
  }
  function numv(v) { var n = typeof v === 'string' ? parseFloat(v) : v; return typeof n === 'number' && isFinite(n) ? n : null; }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function esc(s) {
    return String(has(s) ? s : '').replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  var NF = {};
  function num(v, dec) {
    v = numv(v);
    if (v === null) return '';
    dec = dec || 0;
    if (!NF[dec]) NF[dec] = new Intl.NumberFormat('nb-NO', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    var out = NF[dec].format(v);
    return out.replace(/^[−-]0(,0+)?$/, '0$1');
  }
  /* 0–1 desimaler, uten ",0" */
  function numS(v, maxDec) {
    v = numv(v);
    if (v === null) return '';
    var d = maxDec === undefined ? 1 : maxDec;
    var r = Math.round(v * Math.pow(10, d)) / Math.pow(10, d);
    return num(r, Number.isInteger(r) ? 0 : d);
  }
  function signed(v, dec) { v = numv(v); if (v === null) return ''; return (v > 0 ? '+' : '') + num(v, dec); }
  function tonnes(kg) { kg = numv(kg); if (kg === null) return ''; return kg >= 1000 ? numS(kg / 1000, 1) + ' t' : num(kg) + ' kg'; }

  var MONTHS = ['jan', 'feb', 'mar', 'apr', 'mai', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'des'];
  var MONTHS_LONG = ['januar', 'februar', 'mars', 'april', 'mai', 'juni', 'juli', 'august', 'september', 'oktober', 'november', 'desember'];
  var WEEKDAYS = ['søndag', 'mandag', 'tirsdag', 'onsdag', 'torsdag', 'fredag', 'lørdag'];
  var WD_SHORT = ['søn', 'man', 'tir', 'ons', 'tor', 'fre', 'lør'];

  function parseDate(s) {
    if (typeof s !== 'string') return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3]);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtDate(s, withYear) {
    var d = parseDate(s);
    if (!d) return '';
    var t = parseDate(DATA.today);
    var out = d.getDate() + '. ' + MONTHS[d.getMonth()];
    if (withYear === true || (withYear !== false && t && d.getFullYear() !== t.getFullYear())) out += ' ' + d.getFullYear();
    return out;
  }
  function dayDiff(a, b) { return Math.round((b.getTime() - a.getTime()) / 86400000); }
  function relDays(n) {
    n = numv(n);
    if (n === null) return '';
    if (n <= 0) return 'i dag';
    if (n === 1) return 'i går';
    return 'for ' + num(n) + ' dager siden';
  }
  function plural(n, one, many) { return num(n) + ' ' + (numv(n) === 1 ? one : many); }
  function minutesText(m) {
    m = numv(m);
    if (m === null) return '';
    m = Math.round(m);
    var h = Math.floor(m / 60), r = m % 60;
    if (!h) return r + ' min';
    return h + ' t' + (r ? ' ' + r + ' min' : '');
  }
  function timeOf(iso) {
    var m = /T(\d{2}):(\d{2})/.exec(iso || '');
    return m ? m[1] + ':' + m[2] : '';
  }

  var GROUP_LABELS = {
    chest: 'Bryst', quadriceps: 'Forside lår', hamstrings: 'Bakside lår', glutes: 'Sete', lats: 'Latissimus',
    upper_back: 'Øvre rygg', lower_back: 'Korsrygg', middle_back: 'Midtre rygg', shoulders: 'Skuldre', biceps: 'Biceps',
    triceps: 'Triceps', abdominals: 'Mage', abductors: 'Hofte', adductors: 'Innside lår', calves: 'Legger',
    forearms: 'Underarmer', traps: 'Trapes', neck: 'Nakke', full_body: 'Helkropp', cardio: 'Kondis', other: 'Annet'
  };
  function groupLabel(g) { return has(g) ? (GROUP_LABELS[g] || cap(String(g).replace(/_/g, ' '))) : ''; }
  var KIND_LABEL = { strength: 'Styrke', cardio: 'Kondis', recovery: 'Restitusjon', mixed: 'Styrke og kondis' };

  /* ───────────────────────── Ikoner ───────────────────────── */
  var ICONS = {
    scale: '<rect x="3.5" y="3.5" width="17" height="17" rx="5"/><path d="M8 10.2a5.6 5.6 0 0 1 8 0"/><path d="m12 12.2 1.8-2.4"/>',
    pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6H21"/>',
    flame: '<path d="M12 21.5c3.9 0 7-2.6 7-6.6 0-4.1-3.1-6.2-4.3-9.9-.3-.9-1.5-1.1-2-.3-1.2 2.1-2.4 2.9-3.6 2.4-.6-.3-1.4.1-1.5.8-.4 1.9-2.6 3.3-2.6 7 0 4 3.1 6.6 7 6.6z"/><path d="M12 18.5a2.6 2.6 0 0 1-2.6-2.6c0-1.8 1.6-2.4 2.3-4 1 1.1 2.9 2.2 2.9 4a2.6 2.6 0 0 1-2.6 2.6z"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="3.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    dumbbell: '<path d="M6.5 7v10M17.5 7v10M3.5 9.5v5M20.5 9.5v5M6.5 12h11"/>',
    moon: '<path d="M19.5 14.6A7.8 7.8 0 0 1 9.4 4.5a7.8 7.8 0 1 0 10.1 10.1z"/>',
    battery: '<rect x="3" y="7" width="15.5" height="10" rx="3"/><path d="M21 10.5v3M6.5 10v4M10 10v4"/>',
    heart: '<path d="M19.6 5.6a4.9 4.9 0 0 0-7 0L12 6.3l-.6-.7a4.9 4.9 0 0 0-7 7L12 20.2l7.6-7.6a4.9 4.9 0 0 0 0-7z"/>',
    wave: '<path d="M3 12c1.5 0 1.5-4 3-4s1.5 8 3 8 1.5-8 3-8 1.5 8 3 8 1.5-4 3-4 1.5 0 3 0"/>',
    steps: '<path d="M8.2 3c1.6 0 2.4 1.9 2.4 4.2S9.7 11.8 8.2 11.8 5.8 9.5 5.8 7.2 6.6 3 8.2 3z"/><path d="M6.2 14.6h4v1.6a2 2 0 0 1-4 0z"/><path d="M15.8 6.6c1.6 0 2.4 1.9 2.4 4.2s-.9 4.6-2.4 4.6-2.4-2.3-2.4-4.6.8-4.2 2.4-4.2z"/><path d="M13.8 18.2h4v.8a2 2 0 0 1-4 0z"/>',
    route: '<circle cx="6" cy="18" r="2.4"/><circle cx="18" cy="6" r="2.4"/><path d="M8.4 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.6"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    bolt: '<path d="M13 2.8 5 13.2h6.4l-1 8 8.1-10.6h-6.4z"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    trophy: '<path d="M8 4h8v5.2a4 4 0 0 1-8 0z"/><path d="M8 5.8H5.2A3 3 0 0 0 8 10M16 5.8h2.8A3 3 0 0 1 16 10"/><path d="M12 13.2v3.3M8.5 20.5h7M10 20.5c0-2.2.8-4 2-4s2 1.8 2 4"/>',
    target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
    sunrise: '<path d="M3.5 18h17M7 18a5 5 0 0 1 10 0M12 4.5v4M5.2 9.6l1.9 1.3M18.8 9.6l-1.9 1.3M8.5 21h7"/>',
    watch: '<rect x="6" y="6.5" width="12" height="11" rx="3.5"/><path d="M8.8 6.5 9.5 3h5l.7 3.5M8.8 17.5 9.5 21h5l.7-3.5M12 9.8v2.4l1.5 1.1"/>',
    arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    arrowDown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    trendUp: '<path d="m3 17 6.5-6.5 4 4L21 7"/><path d="M15 7h6v6"/>',
    trendDown: '<path d="m3 7 6.5 6.5 4-4L21 17"/><path d="M15 17h6v-6"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="3"/><path d="M15.5 8.5V6.5a2.5 2.5 0 0 0-2.5-2.5H6.5A2.5 2.5 0 0 0 4 6.5V13a2.5 2.5 0 0 0 2.5 2.5h2"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    sparkle: '<path d="M12 3.5 13.9 9l5.6 2-5.6 2L12 18.5 10.1 13l-5.6-2 5.6-2z"/><path d="M19 3.5v3M17.5 5h3"/>',
    layers: '<path d="m12 3.5 8.5 4.6-8.5 4.6-8.5-4.6z"/><path d="m3.5 12.3 8.5 4.6 8.5-4.6"/><path d="m3.5 16.2 8.5 4.6 8.5-4.6"/>',
    balance: '<path d="M12 4v16M8 20h8M5 7.5h14M7.5 7.5 4.5 14a3 3 0 0 0 6 0zM16.5 7.5l-3 6.5a3 3 0 0 0 6 0z"/>',
    hourglass: '<path d="M6.5 3.5h11M6.5 20.5h11M8 3.5c0 4.3 4 5.2 4 8.5s-4 4.2-4 8.5M16 3.5c0 4.3-4 5.2-4 8.5s4 4.2 4 8.5"/>',
    ladder: '<path d="M4 19h4v-4h4v-4h4V7h4"/><path d="M16 3.5h4v4"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16.5 6.5l2.5 2.5M14.5 8.5 16 10"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.2M12 17h.01"/>'
  };
  function icon(name, cls) {
    return '<svg class="' + (cls || 'icon') + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (ICONS[name] || '') + '</svg>';
  }

  /* ───────────────────────── Byggeklosser ───────────────────────── */
  function cardHead(id, iconName, tone, title, sub, extra) {
    return '<header class="card__head">' +
      '<span class="card__icon card__icon--' + tone + '">' + icon(iconName) + '</span>' +
      '<div class="card__titles"><h2 class="card__title" id="' + id + '">' + esc(title) + '</h2>' +
      (sub ? '<p class="card__sub">' + sub + '</p>' : '') + '</div>' +
      (extra ? '<div class="card__extra">' + extra + '</div>' : '') +
      '</header>';
  }

  /* Liten, vennlig illustrasjon: myke ringer, ikon i midten og noen «gnister» */
  function emptyArt(iconName, tone) {
    return '<div class="empty__art empty__art--' + tone + '" aria-hidden="true">' +
      '<svg viewBox="0 0 120 96" class="empty__svg">' +
      '<circle cx="60" cy="48" r="44" class="ea-1"/><circle cx="60" cy="48" r="32" class="ea-2"/>' +
      '<circle cx="60" cy="48" r="21" class="ea-3"/>' +
      '<circle cx="13" cy="22" r="3" class="ea-dot"/><circle cx="107" cy="70" r="4" class="ea-dot"/>' +
      '<circle cx="104" cy="18" r="2" class="ea-dot"/><circle cx="18" cy="76" r="2" class="ea-dot"/>' +
      '<g transform="translate(48 36)" class="ea-icon"><svg width="24" height="24" viewBox="0 0 24 24">' + (ICONS[iconName] || '') + '</svg></g>' +
      '</svg></div>';
  }
  function emptyState(o) {
    return '<div class="empty' + (o.compact ? ' empty--compact' : '') + '">' + emptyArt(o.icon, o.tone || 'coral') +
      '<div class="empty__body"><h3 class="empty__title">' + esc(o.title) + '</h3>' +
      (o.text ? '<p class="empty__text">' + o.text + '</p>' : '') + (o.action || '') + '</div></div>';
  }

  function ring(o) {
    var size = o.size || 120, sw = o.stroke || 12, r = (size - sw) / 2, c = 2 * Math.PI * r;
    var p = clamp(numv(o.pct) || 0, 0, 100) / 100;
    var arc = p > 0 ? '<circle class="ring__arc" cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" fill="none" stroke="' + o.color +
      '" stroke-width="' + sw + '" stroke-linecap="round" stroke-dasharray="' + c.toFixed(2) + '" stroke-dashoffset="' + (c * (1 - p)).toFixed(2) +
      '" transform="rotate(-90 ' + size / 2 + ' ' + size / 2 + ')" style="--c:' + c.toFixed(2) + '"/>' : '';
    return '<svg class="ring" viewBox="0 0 ' + size + ' ' + size + '" width="' + size + '" height="' + size + '" role="img" aria-label="' + esc(o.label) + '">' +
      '<circle cx="' + size / 2 + '" cy="' + size / 2 + '" r="' + r + '" fill="none" stroke="' + (o.track || 'var(--track)') + '" stroke-width="' + sw + '"/>' +
      arc + '</svg>';
  }

  /* Stolpe med avrundet topp (4 px) og rett fot */
  function barPath(x, y, w, h, r) {
    if (h <= 0) return '';
    r = Math.min(r === undefined ? 4 : r, w / 2, h);
    var b = y + h;
    return 'M' + x + ',' + b + 'V' + (y + r) + 'Q' + x + ',' + y + ' ' + (x + r) + ',' + y + 'H' + (x + w - r) +
      'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + 'V' + b + 'Z';
  }
  function svgOpen(w, h, label, cls) {
    return '<svg class="' + (cls || 'viz') + '" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="' + esc(label) + '">';
  }

  /* ───────────────────────── Tilstand ───────────────────────── */
  var DATA = {};
  var firstRender = true;

  function readData() {
    var el = document.getElementById('fitcoach-data');
    try { return obj(JSON.parse(el ? el.textContent : '{}')); } catch (e) { return {}; }
  }

  /* ───────────────────────── Hero ───────────────────────── */
  function heatWeeks() {
    var hm = arr(obj(DATA.consistency).heatmap);
    var weeks = [];
    for (var i = 0; i < hm.length; i += 7) weeks.push(hm.slice(i, i + 7));
    return weeks;
  }

  function renderHero() {
    var c = obj(DATA.consistency), cb = obj(DATA.comeback), w = obj(DATA.weight), r = obj(DATA.recovery), rec = obj(DATA.recommendation);
    var t = parseDate(DATA.today);
    var dateText = t ? cap(WEEKDAYS[t.getDay()]) + ' ' + t.getDate() + '. ' + MONTHS_LONG[t.getMonth()] : '';
    var greet = has(DATA.greeting) ? DATA.greeting : 'Hei';
    var title = has(DATA.name) ? greet + ', ' + DATA.name : greet + '!';

    // status-setning
    var parts = [];
    var ds = numv(c.days_since_last);
    if (cb.active) {
      parts.push('Det er ' + plural(numv(cb.days_since_last) !== null ? cb.days_since_last : ds, 'dag', 'dager') + ' siden sist. I dag handler det bare om å komme i gang igjen.');
    } else if (ds === null) {
      parts.push('Ingen økter logget ennå. Den første økta er den viktigste.');
    } else {
      var s = ds === 0 ? 'Du har allerede trent i dag. Sterkt!' : 'Sist trent ' + relDays(ds) + (has(c.last_workout_title) ? ' (' + esc(c.last_workout_title) + ')' : '') + '.';
      parts.push(s);
      if (r.connected && has(r.readiness_label)) parts.push('Garmin sier: ' + esc(String(r.readiness_label).toLowerCase()) + '.');
    }

    var html = '<div class="hero__intro">' +
      '<p class="hero__date">' + icon('calendar', 'icon icon--sm') + '<span>' + esc(dateText) + '</span></p>' +
      '<h1 class="hero__title" id="hero-title">' + esc(title) + '</h1>' +
      '<p class="hero__status">' + parts.join(' ') + '</p></div>';

    if (cb.active) {
      html += '<div class="comeback" role="note">' +
        '<div class="comeback__art" aria-hidden="true">' + comebackArt() + '</div>' +
        '<div class="comeback__body"><p class="comeback__kicker">' + icon('sunrise', 'icon icon--sm') +
        '<span>' + plural(cb.days_since_last, 'dag', 'dager') + ' siden sist</span></p>' +
        '<h2 class="comeback__title">' + esc(has(cb.title) ? cb.title : 'Velkommen tilbake!') + '</h2>' +
        (has(cb.message) ? '<p class="comeback__text">' + esc(cb.message) + '</p>' : '') + '</div>' +
        '<a class="btn btn--primary comeback__cta" href="#card-rec">Se comeback-økta' + icon('arrowRight', 'icon icon--sm') + '</a>' +
        '</div>';
    }

    html += '<div class="kpis">' + kpiWeight(w) + kpiReady(r) + kpiStreak(c, cb) + kpiNext(rec) + '</div>';
    $('#hero').innerHTML = html;
  }

  function comebackArt() {
    return '<svg viewBox="0 0 96 96" width="88" height="88">' +
      '<circle cx="48" cy="48" r="46" fill="#FFE3D9"/>' +
      '<circle cx="48" cy="60" r="20" fill="#FF8F73"/>' +
      '<rect x="0" y="60" width="96" height="36" fill="#FFE3D9"/>' +
      '<path d="M16 60h64" stroke="#E0502E" stroke-width="3" stroke-linecap="round"/>' +
      '<path d="M48 22v10M26 32l6 6M70 32l-6 6M22 50h7M67 50h7" stroke="#FF8F73" stroke-width="3" stroke-linecap="round"/>' +
      '<path d="M28 70h40M36 78h24" stroke="#FFB49F" stroke-width="3" stroke-linecap="round"/>' +
      '</svg>';
  }

  function kpi(tone, iconName, label, value, foot, extraCls, attrs) {
    return '<article class="kpi kpi--' + tone + (extraCls ? ' ' + extraCls : '') + '"' + (attrs || '') + '>' +
      '<div class="kpi__head"><span class="kpi__icon">' + icon(iconName) + '</span><span class="kpi__label">' + label + '</span></div>' +
      '<div class="kpi__value">' + value + '</div>' +
      '<div class="kpi__foot">' + foot + '</div></article>';
  }

  function kpiWeight(w) {
    if (!has(w.current_kg)) {
      return kpi('coral', 'scale', 'Vekt', '<span class="kpi__empty">Ingen veiing ennå</span>',
        '<a class="link" href="#weigh-kg" data-focus="weigh-kg">Legg inn første veiing' + icon('arrowRight', 'icon icon--sm') + '</a>');
    }
    var pct = numv(w.progress_pct);
    var foot = '';
    if (pct !== null) {
      foot = '<div class="meter" role="img" aria-label="' + esc(numS(pct) + ' prosent av vektmålet nådd') + '"><span style="width:' + clamp(pct, 0, 100) + '%"></span></div>';
    }
    var sub = [];
    if (has(w.lost_kg)) sub.push(numv(w.lost_kg) >= 0 ? '<strong>' + numS(w.lost_kg) + ' kg</strong> ned' : '<strong>' + numS(-w.lost_kg) + ' kg</strong> opp');
    if (has(w.goal_kg)) sub.push('mål ' + numS(w.goal_kg) + ' kg');
    foot += '<span class="kpi__note">' + sub.join(' · ') + '</span>';
    return kpi('coral', 'scale', 'Vekt', '<span class="num">' + num(w.current_kg, 1) + '</span><span class="kpi__unit">kg</span>', foot);
  }

  function kpiReady(r) {
    if (!r.connected || !has(r.readiness)) {
      return kpi('teal', 'heart', 'Klar for trening', '<span class="kpi__empty">Garmin ikke tilkoblet</span>',
        '<a class="link link--teal" href="#card-recovery">Slik kobler du til' + icon('arrowRight', 'icon icon--sm') + '</a>');
    }
    var v = '<span class="num">' + num(r.readiness) + '</span><span class="kpi__unit">/100</span>' +
      '<span class="kpi__ring">' + ring({ pct: r.readiness, size: 52, stroke: 7, color: 'var(--teal)', label: 'Readiness ' + num(r.readiness) + ' av 100' }) + '</span>';
    var foot = has(r.readiness_label) ? '<span class="chip chip--teal">' + esc(r.readiness_label) + '</span>' : '';
    if (has(r.sleep_hours)) foot += '<span class="kpi__note">Søvn ' + numS(r.sleep_hours) + ' t</span>';
    return kpi('teal', 'heart', 'Klar for trening', v, foot);
  }

  function kpiStreak(c, cb) {
    var sw = numv(c.streak_weeks) || 0;
    var ds = numv(c.days_since_last);
    var weeks = heatWeeks().slice(-8);
    var dots = weeks.length ? '<div class="weekdots" role="img" aria-label="Treningsuker de siste 8 ukene">' + weeks.map(function (wk) {
      var on = wk.some(function (d) { return (numv(d.count) || 0) > 0; });
      return '<span class="' + (on ? 'on' : '') + '"></span>';
    }).join('') + '</div>' : '';
    if (cb.active || sw === 0) {
      if (ds === null) {
        return kpi('coral', 'calendar', 'Siste økt', '<span class="kpi__empty">Ingen økter ennå</span>', '<span class="kpi__note">Første økt starter rekka</span>');
      }
      return kpi('coral', 'hourglass', 'Dager siden sist', '<span class="num">' + num(ds) + '</span><span class="kpi__unit">' + (ds === 1 ? 'dag' : 'dager') + '</span>',
        dots + '<span class="kpi__note">Én økt denne uka starter en ny rekke</span>');
    }
    return kpi('coral', 'flame', 'Treningsrekke', '<span class="num">' + num(sw) + '</span><span class="kpi__unit">' + (sw === 1 ? 'uke' : 'uker') + '</span>',
      dots + '<span class="kpi__note">' + (ds !== null ? 'Sist trent ' + relDays(ds) : '') +
      (has(c.longest_streak_weeks) ? ' · rekord ' + num(c.longest_streak_weeks) : '') + '</span>');
  }

  function kpiNext(rec) {
    if (!has(rec.title)) {
      return kpi('coral', 'sparkle', 'Neste økt', '<span class="kpi__empty">Ingen forslag ennå</span>', '', 'kpi--next');
    }
    var meta = [];
    if (has(rec.duration_min)) meta.push(icon('clock', 'icon icon--sm') + num(rec.duration_min) + ' min');
    if (has(rec.intensity)) meta.push(icon('bolt', 'icon icon--sm') + esc(rec.intensity));
    return kpi('coral', 'sparkle', 'Neste økt',
      '<span class="kpi__title">' + esc(rec.title) + '</span>',
      '<span class="kpi__meta">' + meta.map(function (m) { return '<span>' + m + '</span>'; }).join('') + '</span>' +
      '<a class="btn btn--primary btn--sm" href="#card-rec">Se økta' + icon('arrowDown', 'icon icon--sm') + '</a>', 'kpi--next');
  }

  /* ───────────────────────── Anbefalt økt ───────────────────────── */
  function renderRec() {
    var rec = obj(DATA.recommendation);
    var el = $('#card-rec');
    if (!has(rec.title)) {
      el.innerHTML = cardHead('rec-title', 'sparkle', 'coral', 'Anbefalt neste økt', '') +
        emptyState({ icon: 'sparkle', title: 'Ingen anbefaling akkurat nå', text: 'Logg en økt i Hevy, så foreslår FitCoach neste steg.' });
      return;
    }
    var kind = rec.kind === 'cardio' || rec.kind === 'recovery' ? 'teal' : 'coral';
    var meta = [];
    if (has(rec.duration_min)) meta.push('<span class="pill">' + icon('clock', 'icon icon--sm') + num(rec.duration_min) + ' min</span>');
    if (has(rec.intensity)) meta.push('<span class="pill">' + icon('bolt', 'icon icon--sm') + esc(rec.intensity) + '</span>');
    if (KIND_LABEL[rec.kind]) meta.push('<span class="pill pill--' + kind + '">' + icon(rec.kind === 'strength' ? 'dumbbell' : rec.kind === 'cardio' ? 'route' : 'moon', 'icon icon--sm') + KIND_LABEL[rec.kind] + '</span>');

    var why = arr(rec.why).filter(has);
    var exs = arr(rec.exercises).filter(function (e) { return e && (has(e.label) || has(e.name)); });

    var html = '<div class="rec__top">' +
      '<p class="rec__kicker">' + icon('sparkle', 'icon icon--sm') + '<span>Anbefalt neste økt</span></p>' +
      '<h2 class="rec__title" id="rec-title">' + esc(rec.title) + '</h2>' +
      '<div class="rec__meta">' + meta.join('') + '</div></div>';

    html += '<div class="rec__body">';
    if (why.length) {
      html += '<div class="rec__why"><h3 class="rec__h">Hvorfor denne økta</h3><ul>' + why.map(function (y) {
        return '<li><span class="rec__check">' + icon('check', 'icon icon--xs') + '</span><span>' + esc(y) + '</span></li>';
      }).join('') + '</ul></div>';
    }
    if (exs.length) {
      html += '<div class="rec__ex"><h3 class="rec__h">Øvelser <span class="rec__count">' + exs.length + '</span></h3><ol class="exlist">' + exs.map(function (e, i) {
        var dose = [];
        if (has(e.sets) && has(e.reps)) dose.push(num(e.sets) + ' × ' + esc(e.reps));
        else if (has(e.reps)) dose.push(esc(e.reps));
        else if (has(e.sets)) dose.push(plural(e.sets, 'sett', 'sett'));
        var wt = has(e.weight_kg) ? '<span class="exlist__w">@ ' + numS(e.weight_kg, 1) + ' kg</span>' : '';
        return '<li class="exlist__item"><span class="exlist__n">' + (i + 1) + '</span>' +
          '<div class="exlist__main"><span class="exlist__name">' + esc(has(e.label) ? e.label : e.name) + '</span>' +
          (has(e.note) ? '<span class="exlist__note">' + esc(e.note) + '</span>' : '') + '</div>' +
          '<span class="exlist__dose">' + dose.join('') + wt + '</span></li>';
      }).join('') + '</ol></div>';
    }
    html += '</div>';
    if (exs.length) {
      html += '<div class="rec__foot"><button type="button" class="btn btn--primary" id="copy-rec">' + icon('copy', 'icon icon--sm') + '<span>Kopier økta</span></button>' +
        '<span class="rec__hint">Lim den inn i Hevy eller notatene dine.</span></div>';
    }
    el.className = 'card card--rec card--rec-' + kind + ' span-2';
    el.innerHTML = html;
  }

  function recText() {
    var rec = obj(DATA.recommendation);
    var lines = [rec.title + (has(rec.duration_min) ? ' (' + num(rec.duration_min) + ' min' + (has(rec.intensity) ? ', ' + rec.intensity : '') + ')' : '')];
    arr(rec.exercises).forEach(function (e) {
      var s = '• ' + (has(e.label) ? e.label : e.name) + ': ' + (has(e.sets) ? e.sets + ' × ' : '') + (has(e.reps) ? e.reps : '') +
        (has(e.weight_kg) ? ' @ ' + numS(e.weight_kg, 1) + ' kg' : '');
      if (has(e.note)) s += ' – ' + e.note;
      lines.push(s);
    });
    return lines.join('\n');
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('copy'));
    });
  }

  /* ───────────────────────── Vekt ───────────────────────── */
  function renderWeight() {
    var w = obj(DATA.weight);
    var el = $('#card-weight');
    var entries = arr(w.entries).filter(function (e) { return e && has(numv(e.kg)) && parseDate(e.date); });
    var sub = (has(w.start_kg) && has(w.goal_kg)) ? 'Fra ' + numS(w.start_kg) + ' kg mot ' + numS(w.goal_kg) + ' kg' : '';
    var html = cardHead('weight-title', 'scale', 'coral', 'Vektreise', sub,
      has(w.bmi) ? '<span class="chip chip--neutral" title="Kroppsmasseindeks">BMI ' + numS(w.bmi) + '</span>' : '');

    if (!entries.length || !has(w.current_kg)) {
      html += emptyState({
        icon: 'scale', tone: 'coral', title: 'Ingen vektmålinger ennå',
        text: 'Legg inn din første veiing under, så tegner vi reisen' + (sub ? ' ' + sub.charAt(0).toLowerCase() + sub.slice(1) : '') + ' her.'
      });
    } else {
      var pct = numv(w.progress_pct);
      var total = has(w.start_kg) && has(w.goal_kg) ? w.start_kg - w.goal_kg : null;
      var ringHtml = '<div class="wring">' + ring({ pct: pct || 0, size: 132, stroke: 12, color: 'var(--coral)', label: (pct !== null ? numS(pct) + ' prosent' : 'Ukjent andel') + ' av vektmålet nådd' }) +
        '<div class="wring__center"><span class="wring__pct">' + (pct !== null ? numS(pct) + '<small>%</small>' : '–') + '</span>' +
        '<span class="wring__lbl">av målet</span></div></div>';

      var trend = numv(w.trend_kg_per_week);
      var trendHtml = trend !== null
        ? '<span class="stat__v">' + signed(trend, 2) + '<small> kg/uke</small></span><span class="stat__hint">' + (trend < 0 ? icon('trendDown', 'icon icon--xs') + 'Nedover – bra!' : trend > 0 ? icon('trendUp', 'icon icon--xs') + 'Litt opp siste uker' : 'Stabil') + '</span>'
        : '<span class="stat__v stat__v--soft">Trenger flere veiinger</span>';
      var eta = has(w.eta_date) ? '<span class="stat__v">' + esc(fmtDate(w.eta_date, true)) + '</span><span class="stat__hint">med dagens tempo</span>'
        : '<span class="stat__v stat__v--soft">Kommer når trenden peker nedover</span>';
      var lost = numv(w.lost_kg);
      var stats = '<dl class="stats">' +
        '<div class="stat"><dt>Nå</dt><dd><span class="stat__v">' + num(w.current_kg, 1) + '<small> kg</small></span>' +
        '<span class="stat__hint">' + esc(fmtDate(entries[entries.length - 1].date)) + '</span></dd></div>' +
        '<div class="stat"><dt>' + (lost !== null && lost < 0 ? 'Opp' : 'Ned så langt') + '</dt><dd><span class="stat__v stat__v--coral">' + (lost !== null ? numS(Math.abs(lost)) : '–') + '<small> kg</small></span>' +
        (total !== null && lost !== null ? '<span class="stat__hint">av ' + numS(total) + ' kg</span>' : '') + '</dd></div>' +
        '<div class="stat"><dt>Trend</dt><dd>' + trendHtml + '</dd></div>' +
        '<div class="stat"><dt>Estimert mål</dt><dd>' + eta + '</dd></div></dl>';

      html += '<div class="weight__top">' + ringHtml + stats + '</div>';
      html += '<div class="chart chart--weight" data-chart="weight"></div>';
      if (entries.length === 1) html += '<p class="note">' + icon('info', 'icon icon--xs') + 'Én måling så langt – kurven og trenden kommer etter neste veiing.</p>';
    }

    html += '<form class="weigh" id="weigh-form" novalidate>' +
      '<div class="weigh__row">' +
      '<label class="field field--kg"><span class="field__lbl">Ny veiing</span><span class="field__box"><input id="weigh-kg" name="kg" type="text" inputmode="decimal" autocomplete="off" placeholder="' +
      (has(w.current_kg) ? num(w.current_kg, 1) : '98,0') + '" aria-describedby="weigh-msg" required><span class="field__suffix">kg</span></span></label>' +
      '<label class="field field--date"><span class="field__lbl">Dato</span><span class="field__box"><input id="weigh-date" name="date" type="date" value="' + esc(DATA.today) + '" max="' + esc(DATA.today) + '"></span></label>' +
      '<button type="submit" class="btn btn--primary weigh__btn">' + icon('plus', 'icon icon--sm') + '<span>Lagre veiing</span></button>' +
      '</div><p class="weigh__msg" id="weigh-msg" role="status" aria-live="polite"></p></form>';
    el.innerHTML = html;
  }

  function drawWeight(el, W) {
    var w = obj(DATA.weight);
    var pts = arr(w.entries).filter(function (e) { return e && numv(e.kg) !== null && parseDate(e.date); })
      .map(function (e) { return { d: parseDate(e.date), kg: numv(e.kg), date: e.date }; })
      .sort(function (a, b) { return a.d - b.d; });
    if (!pts.length) { el.innerHTML = ''; return; }
    var H = W < 480 ? 180 : 210;
    var pad = { l: 34, r: 48, t: 16, b: 28 };
    var goal = numv(w.goal_kg), start = numv(w.start_kg);
    var kgs = pts.map(function (p) { return p.kg; });
    var lo = Math.min.apply(null, kgs.concat(goal !== null ? [goal] : []));
    var hi = Math.max.apply(null, kgs.concat(start !== null ? [start] : []));
    lo = Math.floor(lo - 0.8); hi = Math.ceil(hi + 0.6);
    var span = hi - lo, step = span > 12 ? 5 : span > 6 ? 2 : 1;
    var t0 = pts[0].d.getTime(), t1 = pts[pts.length - 1].d.getTime();
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var x = function (t) { return t1 === t0 ? pad.l + iw / 2 : pad.l + (t - t0) / (t1 - t0) * iw; };
    var y = function (v) { return pad.t + (hi - v) / (hi - lo) * ih; };
    var s = svgOpen(W, H, 'Vektkurve: fra ' + numS(pts[0].kg) + ' kg ' + fmtDate(pts[0].date) + ' til ' + numS(pts[pts.length - 1].kg) + ' kg ' + fmtDate(pts[pts.length - 1].date) + (goal !== null ? ', mål ' + numS(goal) + ' kg' : ''));
    s += '<defs><linearGradient id="wgrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#FF6B4A" stop-opacity=".16"/><stop offset="1" stop-color="#FF6B4A" stop-opacity="0"/></linearGradient></defs>';
    for (var v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      s += '<line class="grid" x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + y(v).toFixed(1) + '" y2="' + y(v).toFixed(1) + '"/>';
      s += '<text class="axis" x="' + (pad.l - 8) + '" y="' + (y(v) + 4).toFixed(1) + '" text-anchor="end">' + num(v) + '</text>';
    }
    // måneder
    var lastX = -99, md = new Date(pts[0].d.getFullYear(), pts[0].d.getMonth(), 1);
    if (t1 !== t0) {
      while (md.getTime() <= t1) {
        var mt = Math.max(md.getTime(), t0), mx = x(mt);
        if (mx - lastX > 38 && mx < W - pad.r - 10) {
          s += '<text class="axis" x="' + mx.toFixed(1) + '" y="' + (H - 8) + '" text-anchor="' + (mt === t0 ? 'start' : 'middle') + '">' + MONTHS[md.getMonth()] + '</text>';
          lastX = mx;
        }
        md = new Date(md.getFullYear(), md.getMonth() + 1, 1);
      }
    } else {
      s += '<text class="axis" x="' + x(t0) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(fmtDate(pts[0].date)) + '</text>';
    }
    // mållinje
    if (goal !== null) {
      var gy = y(goal).toFixed(1);
      s += '<line class="goal" x1="' + pad.l + '" x2="' + (W - pad.r) + '" y1="' + gy + '" y2="' + gy + '"/>';
      s += '<g class="goal-tag" transform="translate(' + (W - pad.r + 6) + ' ' + gy + ')">' + '<text x="0" y="-2" class="goal-lbl">Mål</text><text x="0" y="11" class="goal-lbl goal-lbl--v">' + numS(goal) + ' kg</text></g>';
    }
    var line = pts.map(function (p, i) { return (i ? 'L' : 'M') + x(p.d.getTime()).toFixed(1) + ',' + y(p.kg).toFixed(1); }).join('');
    if (pts.length > 1) {
      s += '<path class="area" d="' + line + 'L' + x(t1).toFixed(1) + ',' + (H - pad.b) + 'L' + x(t0).toFixed(1) + ',' + (H - pad.b) + 'Z" fill="url(#wgrad)"/>';
      s += '<path class="line line--coral draw" d="' + line + '" pathLength="1"/>';
      s += '<circle cx="' + x(t0).toFixed(1) + '" cy="' + y(pts[0].kg).toFixed(1) + '" r="3.5" class="dot-start"/>';
    }
    var lp = pts[pts.length - 1], lx = x(t1), ly = y(lp.kg);
    s += '<circle cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="5.5" class="dot-end"/>';
    var labelAbove = goal === null || Math.abs(ly - y(goal)) > 22 || ly < y(goal);
    s += '<text class="val-lbl" x="' + (lx + 9).toFixed(1) + '" y="' + (labelAbove ? ly - 8 : ly + 18).toFixed(1) + '">' + num(lp.kg, 1) + '</text>';
    // hover-lag
    s += '<line class="xhair" x1="0" x2="0" y1="' + pad.t + '" y2="' + (H - pad.b) + '" visibility="hidden"/>';
    s += '<circle class="xdot" r="5" visibility="hidden"/>';
    s += '<rect class="hit" x="' + pad.l + '" y="0" width="' + iw + '" height="' + H + '" fill="transparent"/>';
    s += '</svg>';
    el.innerHTML = s;

    var svg = el.firstChild, hit = $('.hit', svg), xh = $('.xhair', svg), xd = $('.xdot', svg);
    function move(ev) {
      var rect = svg.getBoundingClientRect();
      var px = (ev.clientX - rect.left) * (W / rect.width);
      var best = pts[0], bd = Infinity;
      pts.forEach(function (p) { var d = Math.abs(x(p.d.getTime()) - px); if (d < bd) { bd = d; best = p; } });
      var bx = x(best.d.getTime()), by = y(best.kg);
      xh.setAttribute('x1', bx); xh.setAttribute('x2', bx); xh.setAttribute('visibility', 'visible');
      xd.setAttribute('cx', bx); xd.setAttribute('cy', by); xd.setAttribute('visibility', 'visible');
      showTip('<strong>' + num(best.kg, 1) + ' kg</strong><span>' + esc(fmtDate(best.date, true)) + '</span>', rect.left + bx * rect.width / W, rect.top + by * rect.height / H);
    }
    function leave() { xh.setAttribute('visibility', 'hidden'); xd.setAttribute('visibility', 'hidden'); hideTip(); }
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerdown', move);
    hit.addEventListener('pointerleave', leave);
  }

  /* ───────────────────────── Restitusjon ───────────────────────── */
  function renderRecovery() {
    var r = obj(DATA.recovery);
    var el = $('#card-recovery');
    var connected = !!r.connected;
    var sub = connected ? esc(has(r.status_text) ? r.status_text : 'Fra Garmin') + (has(r.date) ? ' · ' + esc(fmtDate(r.date)) : '') : 'Søvn, puls og energi fra Garmin';
    var html = cardHead('recovery-title', 'moon', 'teal', 'Restitusjon', sub);
    if (!connected) {
      html += emptyState({
        icon: 'watch', tone: 'teal', title: has(r.status_text) ? r.status_text : 'Garmin er ikke tilkoblet',
        text: 'Koble til klokka, så får du readiness, søvn, body battery, hvilepuls og HRV her hver morgen.',
        action: '<div class="hint">' + icon('key', 'icon icon--sm') + '<span>Legg <code>GARMIN_EMAIL</code> og <code>GARMIN_PASSWORD</code> i <code>.hevy_env</code>, og start tjenesten på nytt.</span></div>'
      });
      el.innerHTML = html;
      return;
    }
    var rd = numv(r.readiness);
    var ringHtml = '<div class="rring">' + (rd !== null
      ? ring({ pct: rd, size: 148, stroke: 13, color: 'var(--teal)', track: 'var(--teal-100)', label: 'Readiness ' + num(rd) + ' av 100' }) +
        '<div class="rring__center"><span class="rring__v">' + num(rd) + '</span><span class="rring__lbl">readiness</span></div>'
      : ring({ pct: 0, size: 148, stroke: 13, color: 'var(--teal)', track: 'var(--teal-100)', label: 'Readiness mangler' }) +
        '<div class="rring__center"><span class="rring__lbl">Ingen readiness i dag</span></div>') +
      '</div>';
    var lbl = has(r.readiness_label) ? '<span class="chip chip--teal">' + esc(r.readiness_label) + '</span>' : '';

    // søvn
    var sleep = '';
    if (has(r.sleep_hours)) {
      var tot = numv(r.sleep_hours) * 60, deep = numv(r.deep_min) || 0, rem = numv(r.rem_min) || 0;
      var other = Math.max(0, tot - deep - rem);
      var seg = function (v, cls, t) { return v > 0 ? '<span class="' + cls + '" style="flex-grow:' + v.toFixed(0) + '" data-tip="' + esc(t) + '"></span>' : ''; };
      sleep = '<div class="rmetric rmetric--wide"><div class="rmetric__head">' + icon('moon', 'icon icon--sm') + '<span>Søvn</span></div>' +
        '<div class="rmetric__v">' + numS(r.sleep_hours) + '<small> t</small></div>' +
        '<div class="sleepbar" role="img" aria-label="' + esc('Dyp søvn ' + minutesText(deep) + ', REM ' + minutesText(rem)) + '">' +
        seg(deep, 's-deep', 'Dyp søvn: ' + minutesText(deep)) + seg(rem, 's-rem', 'REM: ' + minutesText(rem)) + seg(other, 's-light', 'Lett/våken: ' + minutesText(other)) + '</div>' +
        '<div class="legend">' + (has(r.deep_min) ? '<span><i class="sw s-deep"></i>Dyp ' + minutesText(r.deep_min) + '</span>' : '') +
        (has(r.rem_min) ? '<span><i class="sw s-rem"></i>REM ' + minutesText(r.rem_min) + '</span>' : '') + '</div></div>';
    } else {
      sleep = '<div class="rmetric rmetric--wide"><div class="rmetric__head">' + icon('moon', 'icon icon--sm') + '<span>Søvn</span></div><div class="rmetric__v rmetric__v--soft">Ingen søvndata i natt</div></div>';
    }
    // body battery
    var bb = '';
    if (has(r.body_battery_peak)) {
      var lo = numv(r.body_battery_low), pk = numv(r.body_battery_peak);
      bb = '<div class="rmetric"><div class="rmetric__head">' + icon('battery', 'icon icon--sm') + '<span>Body battery</span></div>' +
        '<div class="rmetric__v">' + num(pk) + '<small>' + (lo !== null ? ' / lavest ' + num(lo) : ' topp') + '</small></div>' +
        '<div class="range" role="img" aria-label="' + esc('Body battery fra ' + (lo !== null ? num(lo) : '0') + ' til ' + num(pk)) + '"><span style="left:' + clamp(lo || 0, 0, 100) + '%;width:' + clamp(pk - (lo || 0), 2, 100) + '%"></span></div></div>';
    }
    var small = function (ic, label, v, unit) {
      return has(v) ? '<div class="rmetric"><div class="rmetric__head">' + icon(ic, 'icon icon--sm') + '<span>' + label + '</span></div><div class="rmetric__v">' + num(v) + '<small> ' + unit + '</small></div></div>' : '';
    };
    var metrics = sleep + bb + small('heart', 'Hvilepuls', r.resting_hr, 'bpm') + small('wave', 'HRV', r.hrv_ms, 'ms');

    html += '<div class="recovery__top">' + '<div class="recovery__ring">' + ringHtml + lbl + '</div>' +
      '<div class="rmetrics">' + metrics + '</div></div>';

    var hist = arr(r.history).filter(function (h) { return h && parseDate(h.date); });
    if (hist.length) {
      html += '<div class="minis"><h3 class="minis__h">Siste 7 dager</h3><div class="minis__grid">' +
        '<div class="mini"><span class="mini__lbl">Søvn (t)</span><div class="chart" data-chart="mini" data-key="sleep_hours"></div></div>' +
        '<div class="mini"><span class="mini__lbl">Body battery</span><div class="chart" data-chart="mini" data-key="body_battery_peak"></div></div>' +
        '<div class="mini"><span class="mini__lbl">Hvilepuls</span><div class="chart" data-chart="mini" data-key="resting_hr" data-type="line"></div></div>' +
        '</div></div>';
    }
    el.innerHTML = html;
  }

  function drawMini(el, W) {
    var key = el.getAttribute('data-key'), type = el.getAttribute('data-type') || 'bar';
    var hist = arr(obj(DATA.recovery).history).filter(function (h) { return h && parseDate(h.date); }).slice(-7);
    var H = 56, padT = 16, padB = 14;
    var vals = hist.map(function (h) { return numv(h[key]); });
    var ok = vals.filter(function (v) { return v !== null; });
    if (!ok.length) { el.innerHTML = '<p class="mini__none">Ingen data</p>'; return; }
    var unit = key === 'sleep_hours' ? ' t' : key === 'resting_hr' ? ' bpm' : '';
    var fmtV = function (v) { return key === 'sleep_hours' ? numS(v) : num(v); };
    var n = hist.length, slot = W / n;
    var s = svgOpen(W, H, 'Siste 7 dager: ' + ok.map(fmtV).join(', ') + unit, 'viz viz--mini');
    if (type === 'bar') {
      var max = Math.max.apply(null, ok) || 1;
      var bw = Math.min(14, slot * 0.55);
      hist.forEach(function (h, i) {
        var v = vals[i], bx = i * slot + (slot - bw) / 2, last = i === n - 1;
        var tip = esc(fmtDate(h.date) + ': ' + (v === null ? 'ingen data' : fmtV(v) + unit));
        if (v === null) { s += '<circle cx="' + (bx + bw / 2) + '" cy="' + (H - padB - 2) + '" r="2" class="nodata" data-tip="' + tip + '"/>'; return; }
        var bh = Math.max(3, (v / max) * (H - padT - padB)), by = H - padB - bh;
        s += '<path d="' + barPath(bx, by, bw, bh, 3) + '" class="' + (last ? 'bar-teal' : 'bar-teal-soft') + '" data-tip="' + tip + '"/>';
        if (last) s += '<text class="val-sm" x="' + (bx + bw / 2) + '" y="' + (by - 4) + '" text-anchor="middle">' + fmtV(v) + '</text>';
      });
    } else {
      var mn = Math.min.apply(null, ok), mx = Math.max.apply(null, ok);
      if (mx === mn) { mx += 1; mn -= 1; }
      var yy = function (v) { return padT + (mx - v) / (mx - mn) * (H - padT - padB); };
      var xx = function (i) { return i * slot + slot / 2; };
      var d = '', started = false;
      hist.forEach(function (h, i) { var v = vals[i]; if (v === null) return; d += (started ? 'L' : 'M') + xx(i).toFixed(1) + ',' + yy(v).toFixed(1); started = true; });
      s += '<path class="line line--teal" d="' + d + '"/>';
      hist.forEach(function (h, i) {
        var v = vals[i]; if (v === null) return;
        var last = i === n - 1;
        s += '<circle cx="' + xx(i).toFixed(1) + '" cy="' + yy(v).toFixed(1) + '" r="' + (last ? 4 : 8) + '" class="' + (last ? 'dot-teal' : 'hitdot') + '" data-tip="' + esc(fmtDate(h.date) + ': ' + fmtV(v) + unit) + '"/>';
        if (last) s += '<text class="val-sm" x="' + (xx(i) - 8).toFixed(1) + '" y="' + (yy(v) - 7).toFixed(1) + '" text-anchor="middle">' + fmtV(v) + '</text>';
      });
    }
    hist.forEach(function (h, i) {
      var d = parseDate(h.date);
      s += '<text class="axis axis--xs" x="' + (i * slot + slot / 2).toFixed(1) + '" y="' + (H - 2) + '" text-anchor="middle">' + WD_SHORT[d.getDay()].charAt(0).toUpperCase() + '</text>';
    });
    el.innerHTML = s + '</svg>';
  }

  /* ───────────────────────── Aktivitet ───────────────────────── */
  function renderActivity() {
    var a = obj(DATA.activity), run = obj(a.running);
    var el = $('#card-activity');
    var html = cardHead('activity-title', 'steps', 'teal', 'Aktivitet', 'Steg, kalorier og løping');
    if (a.connected) {
      html += '<div class="minikpis">' +
        miniKpi('Steg i dag', has(a.steps_today) ? num(a.steps_today) : null, '') +
        miniKpi('Snitt 7 dager', has(a.steps_avg_7d) ? num(a.steps_avg_7d) : null, 'steg') +
        miniKpi('Kalorier', has(a.calories_avg_7d) ? num(a.calories_avg_7d) : null, 'kcal/dag') + '</div>';
      var hasSteps = arr(a.steps_7d).some(function (d) { return d && numv(d.steps) !== null; });
      html += hasSteps ? '<div class="chart chart--steps" data-chart="steps"></div>' : '<p class="note">' + icon('info', 'icon icon--xs') + 'Ingen skrittdata de siste 7 dagene.</p>';
    } else {
      html += '<div class="inline-empty">' + '<span class="inline-empty__icon">' + icon('watch') + '</span>' +
        '<div><strong>Steg og kalorier kommer fra Garmin</strong><span>Koble til Garmin for daglige skritt, kaloriforbruk og løpeturer fra klokka. Se Restitusjon for oppsett.</span></div></div>';
    }
    var runs = arr(run.runs).filter(function (r) { return r && parseDate(r.date); }).slice(0, 8);
    var km = numv(run.km_last_30d);
    html += '<div class="runs"><div class="runs__head"><div><h3 class="runs__h">Løping siste 30 dager</h3>' +
      '<p class="runs__big">' + (km !== null ? numS(km) : '0') + '<small> km</small></p></div>' +
      (runs.length ? '<span class="chip chip--teal">' + plural(runs.filter(function (r) { var d = parseDate(r.date), t = parseDate(DATA.today); return t && dayDiff(d, t) <= 30; }).length, 'tur', 'turer') + '</span>' : '') + '</div>';
    if (runs.length) {
      html += '<ul class="runlist">' + runs.map(function (r) {
        return '<li><span class="runlist__icon">' + icon('route', 'icon icon--sm') + '</span>' +
          '<span class="runlist__date">' + esc(fmtDate(r.date)) + '</span>' +
          '<span class="runlist__km">' + (has(r.km) ? numS(r.km) + ' km' : '') + '</span>' +
          '<span class="runlist__min">' + (has(r.minutes) ? minutesText(r.minutes) : '') + '</span>' +
          '<span class="runlist__pace">' + (has(r.pace) ? esc(r.pace) + '<small> /km</small>' : '') + '</span></li>';
      }).join('') + '</ul>';
    } else {
      html += '<div class="inline-empty inline-empty--soft"><span class="inline-empty__icon">' + icon('route') + '</span><div><strong>Ingen løpeturer ennå</strong><span>Logg en løpetur i Hevy' + (a.connected ? ' eller på klokka' : '') + ', så dukker den opp her med tempo.</span></div></div>';
    }
    html += '</div>';
    el.innerHTML = html;
  }
  function miniKpi(label, value, unit) {
    return '<div class="minikpi"><span class="minikpi__lbl">' + label + '</span><span class="minikpi__v">' +
      (value !== null ? value + (unit ? '<small> ' + unit + '</small>' : '') : '<span class="soft">Ingen data</span>') + '</span></div>';
  }

  function drawSteps(el, W) {
    var a = obj(DATA.activity);
    var days = arr(a.steps_7d).filter(function (d) { return d && parseDate(d.date); }).slice(-7);
    var H = 150, padT = 24, padB = 26;
    var vals = days.map(function (d) { return numv(d.steps); });
    var ok = vals.filter(function (v) { return v !== null; });
    var avg = numv(a.steps_avg_7d);
    var max = Math.max.apply(null, ok.concat(avg !== null ? [avg] : [])) * 1.08 || 1;
    var n = days.length, slot = W / n, bw = Math.min(26, slot * 0.5);
    var yy = function (v) { return H - padB - v / max * (H - padT - padB); };
    var s = svgOpen(W, H, 'Skritt siste 7 dager: ' + days.map(function (d, i) { return fmtDate(d.date) + ' ' + (vals[i] === null ? 'ingen data' : num(vals[i])); }).join(', '));
    s += '<line class="baseline" x1="0" x2="' + W + '" y1="' + (H - padB) + '" y2="' + (H - padB) + '"/>';
    if (avg !== null) {
      s += '<line class="avgline" x1="0" x2="' + W + '" y1="' + yy(avg).toFixed(1) + '" y2="' + yy(avg).toFixed(1) + '"/>';
    }
    var t = parseDate(DATA.today);
    days.forEach(function (d, i) {
      var v = vals[i], bx = i * slot + (slot - bw) / 2, dd = parseDate(d.date);
      var isToday = t && dd.getTime() === t.getTime();
      var tip = esc(cap(WD_SHORT[dd.getDay()]) + ' ' + fmtDate(d.date) + ': ' + (v === null ? 'ingen data' : num(v) + ' steg'));
      if (v === null) {
        s += '<circle cx="' + (bx + bw / 2) + '" cy="' + (H - padB - 4) + '" r="2.5" class="nodata" data-tip="' + tip + '"/>';
      } else {
        var y0 = yy(v);
        s += '<path d="' + barPath(bx, y0, bw, H - padB - y0, 4) + '" class="' + (isToday || i === n - 1 ? 'bar-teal' : 'bar-teal-soft') + '" data-tip="' + tip + '"/>';
        s += '<rect x="' + (i * slot) + '" y="' + padT + '" width="' + slot + '" height="' + (H - padT - padB) + '" fill="transparent" data-tip="' + tip + '"/>';
        if (i === n - 1 || v === Math.max.apply(null, ok)) {
          s += '<text class="val-sm" x="' + (bx + bw / 2) + '" y="' + (y0 - 6) + '" text-anchor="middle">' + (v >= 10000 ? numS(v / 1000) + 'k' : num(v)) + '</text>';
        }
      }
      s += '<text class="axis' + (isToday ? ' axis--strong' : '') + '" x="' + (bx + bw / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + (isToday ? 'i dag' : WD_SHORT[dd.getDay()]) + '</text>';
    });
    if (avg !== null) {
      s += '<text class="axis avg-lbl" x="' + (W - 2) + '" y="' + (yy(avg) - 5).toFixed(1) + '" text-anchor="end">snitt ' + num(avg) + '</text>';
    }
    el.innerHTML = s + '</svg>';
  }

  /* ───────────────────────── Konsistens ───────────────────────── */
  function renderConsistency() {
    var c = obj(DATA.consistency), cb = obj(DATA.comeback);
    var el = $('#card-consistency');
    var sw = numv(c.streak_weeks) || 0, ds = numv(c.days_since_last);
    var html = cardHead('consistency-title', 'calendar', 'coral', 'Konsistens', 'Treningsdager siste 26 uker');
    var big, bigLbl, lines = [];
    if (sw > 0 && !cb.active) {
      big = num(sw); bigLbl = sw === 1 ? 'uke på rad' : 'uker på rad';
      if (has(c.longest_streak_weeks)) lines.push(icon('trophy', 'icon icon--xs') + '<span>Lengste rekke: <strong>' + plural(c.longest_streak_weeks, 'uke', 'uker') + '</strong></span>');
    } else if (ds !== null) {
      big = num(ds); bigLbl = (ds === 1 ? 'dag' : 'dager') + ' siden sist';
      if (has(c.longest_streak_weeks) && numv(c.longest_streak_weeks) > 0) lines.push(icon('trophy', 'icon icon--xs') + '<span>Rekorden din er <strong>' + plural(c.longest_streak_weeks, 'uke', 'uker') + '</strong> – den kan slås igjen</span>');
    } else {
      big = '0'; bigLbl = 'økter så langt';
    }
    lines.push(icon('dumbbell', 'icon icon--xs') + '<span><strong>' + num(numv(c.workouts_last_30d) || 0) + '</strong> ' + (numv(c.workouts_last_30d) === 1 ? 'økt' : 'økter') + ' siste 30 dager</span>');
    if (has(c.last_workout_date)) lines.push(icon('clock', 'icon icon--xs') + '<span>Sist: ' + esc(fmtDate(c.last_workout_date)) + (has(c.last_workout_title) ? ' · ' + esc(c.last_workout_title) : '') + '</span>');

    html += '<div class="consist"><div class="consist__stats"><p class="bignum"><span class="num">' + big + '</span><span class="bignum__lbl">' + bigLbl + '</span></p>' +
      '<ul class="facts">' + lines.map(function (l) { return '<li>' + l + '</li>'; }).join('') + '</ul></div>';
    if (arr(c.heatmap).length) {
      html += '<div class="consist__heat"><div class="chart chart--heat" data-chart="heat"></div>' +
        '<div class="heatlegend" aria-hidden="true"><span>Mindre</span><i class="h0"></i><i class="h1"></i><i class="h2"></i><i class="h3"></i><span>Mer</span><i class="hf"></i><span>Kommende</span></div></div>';
    } else {
      html += '<div class="consist__heat">' + emptyState({ icon: 'calendar', title: 'Ingen treningshistorikk ennå', text: 'Når du logger økter i Hevy, fylles kalenderen her.', compact: true }) + '</div>';
    }
    html += '</div>';
    el.innerHTML = html;
  }

  function drawHeat(el, W) {
    var hm = arr(obj(DATA.consistency).heatmap);
    var weeks = heatWeeks();
    var cols = weeks.length || 1;
    var labelW = 30, top = 18, gap = 3;
    var cell = clamp(Math.floor((Math.min(W, 640) - labelW) / cols) - gap, 7, 18);
    if (cell >= 14) gap = 4;
    var step = cell + gap;
    var w = labelW + cols * step, h = top + 7 * step;
    var t = parseDate(DATA.today);
    var total = hm.reduce(function (a, d) { return a + ((numv(d.count) || 0) > 0 ? 1 : 0); }, 0);
    var s = svgOpen(w, h, 'Treningskalender siste 26 uker: ' + total + ' dager med trening', 'viz viz--heat');
    ['man', '', 'ons', '', 'fre', '', ''].forEach(function (l, i) {
      if (l) s += '<text class="axis axis--xs" x="0" y="' + (top + i * step + cell * 0.78).toFixed(1) + '">' + l + '</text>';
    });
    var lastLabel = -10;
    weeks.forEach(function (wk, ci) {
      wk.forEach(function (d) {
        var dd = parseDate(d && d.date);
        if (dd && dd.getDate() === 1 && ci - lastLabel >= 3) {
          s += '<text class="axis axis--xs" x="' + (labelW + ci * step) + '" y="11">' + MONTHS[dd.getMonth()] + '</text>';
          lastLabel = ci;
        }
      });
      if (ci === 0 && lastLabel < 0) {
        var d0 = parseDate(wk[0] && wk[0].date);
        if (d0 && (!weeks[2] || !weeks.slice(1, 3).some(function (w2) { return w2.some(function (x) { var q = parseDate(x.date); return q && q.getDate() === 1; }); }))) {
          s += '<text class="axis axis--xs" x="' + labelW + '" y="11">' + MONTHS[d0.getMonth()] + '</text>';
          lastLabel = 0;
        }
      }
      wk.forEach(function (d, ri) {
        var dd = parseDate(d && d.date);
        if (!dd) return;
        var cnt = numv(d.count) || 0;
        var future = t && dd > t, isToday = t && dd.getTime() === t.getTime();
        var cls = future ? 'hf' : cnt >= 3 ? 'h3' : cnt === 2 ? 'h2' : cnt === 1 ? 'h1' : 'h0';
        var kind = d.kind === 'strength' ? 'styrke' : d.kind === 'cardio' ? 'kondis' : d.kind === 'mixed' ? 'styrke og kondis' : '';
        var tip = cap(WD_SHORT[dd.getDay()]) + ' ' + fmtDate(d.date) + ': ' + (future ? 'kommer' : cnt ? plural(cnt, 'økt', 'økter') + (kind ? ' (' + kind + ')' : '') : 'hviledag');
        s += '<rect x="' + (labelW + ci * step) + '" y="' + (top + ri * step) + '" width="' + cell + '" height="' + cell + '" rx="' + Math.round(cell * 0.3) + '" class="' + cls + (isToday ? ' today' : '') + '" data-tip="' + esc(tip) + '"/>';
      });
    });
    el.innerHTML = s + '</svg>';
  }

  /* ───────────────────────── Balanse ───────────────────────── */
  function renderBalance() {
    var b = obj(DATA.balance);
    var el = $('#card-balance');
    var st = numv(b.strength) || 0, ca = numv(b.cardio) || 0;
    var pct = numv(b.strength_pct);
    if (pct === null) pct = st + ca ? Math.round(st / (st + ca) * 100) : 50;
    pct = clamp(pct, 0, 100);
    var html = cardHead('balance-title', 'balance', 'neutral', 'Styrke og kondis', 'Siste 28 dager');
    if (st + ca === 0) {
      html += '<div class="split split--empty" role="img" aria-label="Ingen økter siste 28 dager"><span></span></div>';
    } else {
      html += '<div class="split" role="img" aria-label="' + esc('Styrke ' + num(pct) + ' prosent, kondis ' + num(100 - pct) + ' prosent') + '">' +
        (pct > 0 ? '<span class="split__s" style="flex-grow:' + pct + '"></span>' : '') + (pct < 100 ? '<span class="split__c" style="flex-grow:' + (100 - pct) + '"></span>' : '') + '</div>';
    }
    html += '<div class="split__legend"><div><span class="lg"><i class="sw sw--coral"></i>Styrke</span><strong class="split__n">' + num(st) + '</strong><span class="split__p">' + (st + ca ? num(pct) + ' %' : 'økter') + '</span></div>' +
      '<div class="split__right"><span class="lg"><i class="sw sw--teal"></i>Kondis</span><strong class="split__n">' + num(ca) + '</strong><span class="split__p">' + (st + ca ? num(100 - pct) + ' %' : 'økter') + '</span></div></div>';
    if (has(b.message)) html += '<p class="balance__msg">' + esc(b.message) + '</p>';
    if (KIND_LABEL[b.next_kind]) {
      var tone = b.next_kind === 'strength' ? 'coral' : 'teal';
      html += '<p class="balance__next"><span>Neste</span><span class="chip chip--' + tone + '">' + icon(b.next_kind === 'strength' ? 'dumbbell' : 'route', 'icon icon--xs') + KIND_LABEL[b.next_kind] + '</span></p>';
    }
    el.innerHTML = html;
  }

  /* ───────────────────────── Styrke ───────────────────────── */
  function renderStrength() {
    var s = obj(DATA.strength);
    var el = $('#card-strength');
    var lifts = arr(s.lifts).filter(function (l) { return l && (has(l.label) || has(l.exercise)); }).slice(0, 6);
    var prSet = {};
    arr(s.recent_prs).forEach(function (p) { if (p && has(p.exercise)) prSet[p.exercise] = p; });
    var html = cardHead('strength-title', 'ladder', 'coral', 'Styrkeprogresjon', 'Estimert 1RM (Epley) per løft, siste økter');
    if (!lifts.length) {
      html += emptyState({ icon: 'dumbbell', title: 'Ingen løft logget ennå', text: 'Logg styrkeøkter i Hevy, så viser vi utviklingen for de viktigste løftene dine her.' });
      el.innerHTML = html;
      return;
    }
    html += '<div class="lifts">' + lifts.map(function (l, i) {
      var ch = numv(l.change_pct);
      var chHtml = ch === null ? '<span class="delta delta--none">Første økt</span>'
        : ch > 0 ? '<span class="delta delta--up">' + icon('trendUp', 'icon icon--xs') + signed(ch, 1) + ' %</span>'
          : ch < 0 ? '<span class="delta delta--down">' + icon('trendDown', 'icon icon--xs') + num(ch, 1) + ' %</span>'
            : '<span class="delta delta--none">± 0 %</span>';
      var pr = prSet[l.exercise];
      var cur = has(l.current_e1rm_kg) ? l.current_e1rm_kg : l.best_e1rm_kg;
      return '<article class="lift">' +
        '<div class="lift__top"><div><h3 class="lift__name">' + esc(has(l.label) ? l.label : l.exercise) + '</h3>' +
        (has(l.muscle_group) ? '<p class="lift__group">' + esc(groupLabel(l.muscle_group)) + '</p>' : '') + '</div>' +
        (pr ? '<span class="badge-pr" title="' + esc('Ny PR ' + fmtDate(pr.date)) + '">' + icon('trophy', 'icon icon--xs') + 'PR</span>' : '') + '</div>' +
        '<div class="lift__mid"><p class="lift__v">' + (has(cur) ? '<span class="num">' + numS(cur) + '</span><small>kg</small>' : '<span class="soft">Ingen e1RM ennå</span>') + '</p>' + chHtml + '</div>' +
        '<div class="chart chart--spark" data-chart="spark" data-idx="' + i + '"></div>' +
        '<p class="lift__foot">' + (has(l.last_top_set) ? '<span>Toppsett <strong>' + esc(l.last_top_set) + '</strong></span>' : '') +
        (has(l.best_e1rm_kg) ? '<span>Beste ' + numS(l.best_e1rm_kg) + ' kg</span>' : '') + '</p>' +
        '</article>';
    }).join('') + '</div>';
    el.innerHTML = html;
  }

  function drawSpark(el, W) {
    var lifts = arr(obj(DATA.strength).lifts).filter(function (l) { return l && (has(l.label) || has(l.exercise)); });
    var l = lifts[+el.getAttribute('data-idx')];
    var hist = arr(l && l.history).filter(function (h) { return h && numv(h.e1rm_kg) !== null && parseDate(h.date); });
    var H = 56, pad = { l: 6, r: 8, t: 8, b: 8 };
    if (!hist.length) { el.innerHTML = '<p class="mini__none">Historikk kommer etter neste økt</p>'; return; }
    var vs = hist.map(function (h) { return numv(h.e1rm_kg); });
    var mn = Math.min.apply(null, vs), mx = Math.max.apply(null, vs);
    if (mx - mn < 2) { mx += 1; mn -= 1; }
    var n = hist.length;
    var xx = function (i) { return n === 1 ? W / 2 : pad.l + i / (n - 1) * (W - pad.l - pad.r); };
    var yy = function (v) { return pad.t + (mx - v) / (mx - mn) * (H - pad.t - pad.b); };
    var s = svgOpen(W, H, (l.label || l.exercise) + ': e1RM fra ' + numS(vs[0]) + ' til ' + numS(vs[n - 1]) + ' kg over ' + n + ' økter', 'viz viz--spark');
    var d = hist.map(function (h, i) { return (i ? 'L' : 'M') + xx(i).toFixed(1) + ',' + yy(vs[i]).toFixed(1); }).join('');
    var gid = 'sg' + el.getAttribute('data-idx');
    if (n > 1) {
      s += '<defs><linearGradient id="' + gid + '" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#FF6B4A" stop-opacity=".14"/><stop offset="1" stop-color="#FF6B4A" stop-opacity="0"/></linearGradient></defs>';
      s += '<path d="' + d + 'L' + xx(n - 1).toFixed(1) + ',' + H + 'L' + xx(0).toFixed(1) + ',' + H + 'Z" fill="url(#' + gid + ')"/>';
      s += '<path class="line line--coral draw" d="' + d + '" pathLength="1"/>';
    } else {
      s += '<line class="grid" x1="0" x2="' + W + '" y1="' + yy(vs[0]) + '" y2="' + yy(vs[0]) + '"/>';
    }
    var bestI = vs.indexOf(Math.max.apply(null, vs));
    hist.forEach(function (h, i) {
      var tip = esc(fmtDate(h.date) + ': ' + numS(vs[i]) + ' kg e1RM');
      var isEnd = i === n - 1;
      var cls = isEnd ? 'dot-end dot-end--sm' : i === bestI ? 'dot-best' : 'hitdot';
      s += '<circle cx="' + xx(i).toFixed(1) + '" cy="' + yy(vs[i]).toFixed(1) + '" r="' + (isEnd ? 4.5 : i === bestI ? 3.5 : 7) + '" class="' + cls + '" data-tip="' + tip + '"/>';
    });
    el.innerHTML = s + '</svg>';
  }

  function renderPRs() {
    var prs = arr(obj(DATA.strength).recent_prs).filter(function (p) { return p && (has(p.label) || has(p.exercise)); }).slice(0, 5);
    var el = $('#card-prs');
    var html = cardHead('prs-title', 'trophy', 'coral', 'Nylige PR-er', 'Siste 60 dager');
    if (!prs.length) {
      html += emptyState({ icon: 'trophy', title: 'Ingen nye PR-er ennå', text: 'De kommer når du er i rytmen igjen. Hver økt bygger mot neste rekord.', compact: true });
    } else {
      html += '<ul class="prlist">' + prs.map(function (p) {
        return '<li><span class="prlist__icon">' + icon('trophy', 'icon icon--sm') + '</span><div class="prlist__main"><span class="prlist__name">' + esc(has(p.label) ? p.label : p.exercise) + '</span>' +
          '<span class="prlist__sub">' + esc(p.value) + (has(p.e1rm_kg) ? ' · e1RM ' + numS(p.e1rm_kg) + ' kg' : '') + '</span></div>' +
          '<span class="prlist__date">' + esc(fmtDate(p.date)) + '</span></li>';
      }).join('') + '</ul>';
    }
    el.innerHTML = html;
  }

  /* ───────────────────────── Volum ───────────────────────── */
  function renderVolume() {
    var v = obj(DATA.volume);
    var el = $('#card-volume');
    var weeks = arr(v.weeks).filter(function (w) { return w && parseDate(w.week_start); });
    var groups = arr(v.groups).length ? arr(v.groups) : Object.keys(obj(v.last_28d_sets));
    var sets = obj(v.last_28d_sets);
    var anyVol = weeks.some(function (w) { return (numv(w.total_kg) || 0) > 0; });
    var anySets = groups.some(function (g) { return (numv(sets[g]) || 0) > 0; });
    var html = cardHead('volume-title', 'layers', 'coral', 'Volum per muskelgruppe', 'Totalvolum per uke og sett per gruppe');
    if (!anyVol && !anySets) {
      html += emptyState({ icon: 'layers', title: 'Ingen styrkevolum de siste 8 ukene', text: 'Første økt tilbake fyller grafen. Start lett – volumet bygger seg opp igjen av seg selv.' });
      el.innerHTML = html;
      return;
    }
    html += '<div class="vol">';
    if (weeks.length) {
      var lastW = weeks[weeks.length - 1];
      html += '<div class="vol__weeks"><div class="vol__head"><h3 class="sub-h">Totalvolum per uke</h3><span class="vol__now">Denne uka <strong>' + tonnes(lastW.total_kg || 0) + '</strong></span></div>' +
        '<div class="chart chart--vol" data-chart="vol"></div></div>';
    }
    var maxSet = Math.max.apply(null, groups.map(function (g) { return numv(sets[g]) || 0; }).concat([1]));
    var sorted = groups.slice().sort(function (a, b) { return (numv(sets[b]) || 0) - (numv(sets[a]) || 0); });
    var neglected = {};
    arr(DATA.neglected).forEach(function (n) { if (n && n.group) neglected[n.group] = true; });
    html += '<div class="vol__sets"><h3 class="sub-h">Sett siste 28 dager</h3><ul class="hbars">' + sorted.map(function (g) {
      var n = numv(sets[g]) || 0;
      return '<li class="hbar' + (n === 0 ? ' hbar--zero' : '') + '"><span class="hbar__lbl">' + esc(cap(g)) + (neglected[g] ? '<i class="hbar__flag" title="Forsømt">' + icon('hourglass', 'icon icon--xs') + '</i>' : '') + '</span>' +
        '<span class="hbar__track"><span class="hbar__fill" style="width:' + (n ? Math.max(3, n / maxSet * 100) : 0) + '%"></span></span>' +
        '<span class="hbar__v">' + num(n) + '</span></li>';
    }).join('') + '</ul></div></div>';
    el.innerHTML = html;
  }

  function drawVol(el, W) {
    var weeks = arr(obj(DATA.volume).weeks).filter(function (w) { return w && parseDate(w.week_start); });
    var H = 150, padT = 24, padB = 26;
    var vals = weeks.map(function (w) { return numv(w.total_kg) || 0; });
    var max = Math.max.apply(null, vals.concat([1])) * 1.05;
    var n = weeks.length, slot = W / n, bw = Math.min(24, slot * 0.55);
    var maxI = vals.indexOf(Math.max.apply(null, vals));
    var s = svgOpen(W, H, 'Totalvolum per uke: ' + weeks.map(function (w, i) { return 'uke fra ' + fmtDate(w.week_start) + ' ' + tonnes(vals[i]); }).join(', '));
    s += '<line class="baseline" x1="0" x2="' + W + '" y1="' + (H - padB) + '" y2="' + (H - padB) + '"/>';
    var every = slot < 44 ? 2 : 1;
    weeks.forEach(function (w, i) {
      var v = vals[i], bx = i * slot + (slot - bw) / 2, last = i === n - 1;
      var tip = esc('Uka fra ' + fmtDate(w.week_start) + ': ' + (v ? tonnes(v) : 'ingen styrkeøkter'));
      if (v > 0) {
        var bh = Math.max(3, v / max * (H - padT - padB)), by = H - padB - bh;
        s += '<path d="' + barPath(bx, by, bw, bh, 4) + '" class="' + (last ? 'bar-coral' : 'bar-coral-soft') + '" data-tip="' + tip + '"/>';
        if (last || i === maxI) s += '<text class="val-sm" x="' + (bx + bw / 2) + '" y="' + (by - 6) + '" text-anchor="middle">' + tonnes(v) + '</text>';
      } else {
        s += '<rect x="' + (bx + bw / 2 - 5) + '" y="' + (H - padB - 3) + '" width="10" height="3" rx="1.5" class="nodata-bar" data-tip="' + tip + '"/>';
      }
      s += '<rect x="' + (i * slot) + '" y="' + padT + '" width="' + slot + '" height="' + (H - padT - padB) + '" fill="transparent" data-tip="' + tip + '"/>';
      if ((n - 1 - i) % every === 0) {
        s += '<text class="axis' + (last ? ' axis--strong' : '') + '" x="' + (bx + bw / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + (last ? 'nå' : esc(fmtDate(w.week_start, false))) + '</text>';
      }
    });
    el.innerHTML = s + '</svg>';
  }

  /* ───────────────────────── Forsømt ───────────────────────── */
  function renderNeglected() {
    var list = arr(DATA.neglected).filter(function (n) { return n && has(n.group); });
    var el = $('#card-neglected');
    var html = cardHead('neglected-title', 'hourglass', 'coral', 'Forsømte muskelgrupper', 'Ikke trent på 7+ dager');
    if (!list.length) {
      html += emptyState({ icon: 'check', tone: 'teal', title: 'Alt i balanse', text: 'Alle muskelgrupper er trent den siste uka. Godt jobbet!', compact: true });
    } else {
      html += '<ul class="neglist">' + list.slice(0, 6).map(function (n) {
        var d = numv(n.days_since);
        return '<li><div class="neglist__top"><span class="neglist__name">' + esc(cap(n.group)) + '</span>' +
          '<span class="chip ' + (d === null ? 'chip--neutral' : 'chip--coral') + '">' + (d === null ? 'aldri trent' : plural(d, 'dag', 'dager')) + '</span></div>' +
          (has(n.message) ? '<p class="neglist__msg">' + esc(n.message) + '</p>' : '') + '</li>';
      }).join('') + '</ul>';
    }
    el.innerHTML = html;
  }

  /* ───────────────────────── Progresjonsforslag ───────────────────────── */
  var PROG_KIND = {
    weight: ['Mer vekt', 'coral', 'trendUp'], reps: ['Flere reps', 'coral', 'plus'], sets: ['Flere sett', 'coral', 'layers'],
    hold: ['Hold', 'neutral', 'target'], deload: ['Deload', 'teal', 'moon']
  };
  function renderProgression() {
    var list = arr(DATA.progression).filter(function (p) { return p && (has(p.label) || has(p.exercise)); }).slice(0, 6);
    var el = $('#card-progression');
    var html = cardHead('progression-title', 'trendUp', 'coral', 'Progresjonsforslag', 'Fra forrige økt til neste');
    if (!list.length) {
      html += emptyState({ icon: 'trendUp', title: 'Ingen forslag ennå', text: 'Etter et par økter med samme øvelse foreslår vi neste steg her.', compact: true });
    } else {
      html += '<ul class="proglist">' + list.map(function (p) {
        var k = PROG_KIND[p.kind] || ['Forslag', 'neutral', 'sparkle'];
        return '<li class="prog"><div class="prog__top"><h3 class="prog__name">' + esc(has(p.label) ? p.label : p.exercise) + '</h3>' +
          '<span class="chip chip--' + k[1] + '">' + icon(k[2], 'icon icon--xs') + k[0] + '</span></div>' +
          '<p class="prog__change">' + (has(p.last) ? '<span class="prog__last">' + esc(p.last) + '</span>' : '<span class="prog__last soft">Ny øvelse</span>') +
          '<span class="prog__arrow">' + icon('arrowRight', 'icon icon--sm') + '<span class="sr-only">neste:</span></span>' +
          '<span class="prog__next prog__next--' + k[1] + '">' + esc(has(p.next) ? p.next : '') + '</span></p>' +
          (has(p.reason) ? '<p class="prog__reason">' + esc(p.reason) + '</p>' : '') + '</li>';
      }).join('') + '</ul>';
    }
    el.innerHTML = html;
  }

  /* ───────────────────────── Sitat ───────────────────────── */
  function renderQuote() {
    var q = obj(DATA.quote);
    var text = has(q.text) ? q.text : 'Små steg hver uke blir store resultater over tid.';
    var author = has(q.text) ? (has(q.author) ? q.author : '') : 'FitCoach';
    $('#card-quote').innerHTML = '<figure class="quote"><span class="quote__label">Dagens sitat</span>' +
      '<svg class="quote__mark" viewBox="0 0 48 40" aria-hidden="true"><path d="M0 40V22C0 9 7 1.5 19 0l2 6c-6.5 1.8-9.6 6-9.8 12H20v22zm27 0V22c0-13 7-20.5 19-22l2 6c-6.5 1.8-9.6 6-9.8 12H47v22z"/></svg>' +
      '<blockquote class="quote__text"><p>' + esc(text) + '</p></blockquote>' +
      (author ? '<figcaption class="quote__author"><span class="quote__rule"></span>' + esc(author) + '</figcaption>' : '') + '</figure>';
  }

  /* ───────────────────────── Footer ───────────────────────── */
  var SRC_TEXT = { ok: 'tilkoblet', error: 'feil', demo: 'demodata', not_configured: 'ikke konfigurert' };
  function renderFooter() {
    var src = obj(DATA.sources);
    var errs = arr(src.errors).filter(has);
    var pill = function (name, st) {
      var cls = st === 'ok' ? 'ok' : st === 'error' ? 'err' : st === 'demo' ? 'demo' : 'off';
      return '<span class="src src--' + cls + '">' + (st === 'error' ? icon('alert', 'icon icon--xs') : '<i class="src__dot"></i>') + '<span>' + name + ': ' + esc(SRC_TEXT[st] || (has(st) ? st : 'ukjent')) + '</span></span>';
    };
    var when = DATA.generated_at ? fmtDate(DATA.generated_at.slice(0, 10)) + (timeOf(DATA.generated_at) ? ' kl. ' + timeOf(DATA.generated_at) : '') : '';
    var html = '<div class="footer__row"><div class="footer__sources"><span class="footer__lbl">Kilder</span>' + pill('Hevy', src.hevy) + pill('Garmin', src.garmin) +
      (DATA.demo ? '<span class="chip chip--demo">Demodata</span>' : '') + '</div>' +
      '<p class="footer__meta">' + (when ? 'Sist oppdatert ' + esc(when) + ' · ' : '') + 'FitCoach</p></div>';
    if (errs.length) {
      html += '<details class="footer__errors"><summary>' + icon('info', 'icon icon--xs') + (errs.length === 1 ? '1 melding' : errs.length + ' meldinger') + ' fra datakildene</summary><ul>' +
        errs.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul></details>';
    }
    $('#footer').innerHTML = html;
    var up = $('#updated-at');
    if (up) up.textContent = timeOf(DATA.generated_at) ? 'Oppdatert ' + timeOf(DATA.generated_at) : '';
    var badge = $('#demo-badge');
    if (badge) badge.hidden = !DATA.demo;
  }

  /* ───────────────────────── Tegning av grafer ───────────────────────── */
  var DRAW = { weight: drawWeight, steps: drawSteps, heat: drawHeat, spark: drawSpark, vol: drawVol, mini: drawMini };
  function drawCharts(root) {
    $$('[data-chart]', root).forEach(function (el) {
      var fn = DRAW[el.getAttribute('data-chart')];
      var w = Math.floor(el.clientWidth);
      if (!fn || w < 40) return;
      el.setAttribute('data-w', w);
      try { fn(el, w); } catch (e) { el.innerHTML = ''; if (window.console) console.warn('Kunne ikke tegne graf', e); }
    });
  }

  function renderAll() {
    var sections = [
      ['hero', renderHero], ['rec', renderRec], ['weight', renderWeight], ['recovery', renderRecovery], ['activity', renderActivity],
      ['consistency', renderConsistency], ['balance', renderBalance], ['strength', renderStrength], ['prs', renderPRs],
      ['volume', renderVolume], ['neglected', renderNeglected], ['progression', renderProgression], ['quote', renderQuote], ['footer', renderFooter]
    ];
    sections.forEach(function (s) {
      try { s[1](); } catch (e) { if (window.console) console.warn('Kunne ikke vise ' + s[0], e); }
    });
    drawCharts(document);
    if (firstRender) {
      document.body.classList.add('is-loaded');
      $$('.bento > .card').forEach(function (c, i) { c.style.setProperty('--i', i); });
      firstRender = false;
    }
  }

  /* ───────────────────────── Tooltip ───────────────────────── */
  var tipEl;
  function showTip(html, x, y) {
    tipEl = tipEl || $('#tooltip');
    if (!tipEl) return;
    tipEl.innerHTML = html;
    tipEl.hidden = false;
    var r = tipEl.getBoundingClientRect();
    var left = clamp(x - r.width / 2, 8, window.innerWidth - r.width - 8);
    var top = y - r.height - 12;
    if (top < 8) top = y + 16;
    tipEl.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(top) + 'px)';
  }
  function hideTip() { if (tipEl) tipEl.hidden = true; }
  document.addEventListener('pointerover', function (ev) {
    var t = ev.target.closest && ev.target.closest('[data-tip]');
    if (!t) return;
    var r = t.getBoundingClientRect();
    showTip(esc(t.getAttribute('data-tip')), r.left + r.width / 2, r.top);
  });
  document.addEventListener('pointerout', function (ev) {
    var t = ev.target.closest && ev.target.closest('[data-tip]');
    if (t && !(ev.relatedTarget && t.contains(ev.relatedTarget))) hideTip();
  });
  window.addEventListener('scroll', hideTip, { passive: true });

  /* ───────────────────────── Toast ───────────────────────── */
  var toastTimer;
  function toast(msg, tone) {
    var el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'toast' + (tone ? ' toast--' + tone : '');
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 3200);
  }

  /* ───────────────────────── Handlinger ───────────────────────── */
  function refresh() {
    var btn = $('#refresh-btn');
    if (!btn || btn.getAttribute('aria-busy') === 'true') return;
    btn.setAttribute('aria-busy', 'true');
    btn.disabled = true;
    fetch('/dashboard/api?refresh=1', { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) { DATA = obj(d); renderAll(); toast('Dashboardet er oppdatert', 'ok'); })
      .catch(function () { toast('Kunne ikke oppdatere akkurat nå. Prøv igjen om litt.', 'warn'); })
      .then(function () { btn.removeAttribute('aria-busy'); btn.disabled = false; });
  }

  function errorText(body, status) {
    var d = body && body.detail;
    if (typeof d === 'string' && d.trim()) return d;
    if (Array.isArray(d)) return 'Sjekk at vekten er et tall, for eksempel 96,4.';
    return status >= 500 ? 'Noe gikk galt på serveren. Prøv igjen om litt.' : 'Kunne ikke lagre veiingen.';
  }

  function submitWeight(form) {
    var input = $('#weigh-kg', form), dateEl = $('#weigh-date', form), msg = $('#weigh-msg', form), btn = $('button[type=submit]', form);
    var raw = (input.value || '').trim().replace(/\s/g, '').replace(',', '.');
    var kg = parseFloat(raw);
    var setMsg = function (text, tone) { msg.textContent = text; msg.className = 'weigh__msg' + (tone ? ' weigh__msg--' + tone : ''); };
    if (!raw || !/^\d{2,3}(\.\d{1,2})?$/.test(raw) || !isFinite(kg)) {
      input.setAttribute('aria-invalid', 'true');
      setMsg('Skriv vekten i kilo, for eksempel 96,4.', 'err');
      input.focus();
      return;
    }
    if (kg < 30 || kg > 300) {
      input.setAttribute('aria-invalid', 'true');
      setMsg('Det ser ikke helt riktig ut. Vekten må være mellom 30 og 300 kg.', 'err');
      input.focus();
      return;
    }
    input.removeAttribute('aria-invalid');
    var payload = { kg: Math.round(kg * 10) / 10 };
    if (dateEl && dateEl.value && dateEl.value !== DATA.today) payload.date = dateEl.value;
    btn.disabled = true; btn.setAttribute('aria-busy', 'true');
    setMsg('Lagrer …', '');
    fetch('/dashboard/weight', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(payload) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
      })
      .then(function (res) {
        if (res.ok && res.body && res.body.ok && res.body.weight) {
          DATA.weight = res.body.weight;
          renderWeight(); renderHero();
          drawCharts($('#card-weight'));
          var m = $('#weigh-msg');
          if (m) { m.textContent = 'Lagret ' + num(payload.kg, 1) + ' kg. Bra jobbet med å følge opp!'; m.className = 'weigh__msg weigh__msg--ok'; }
          toast('Veiingen er lagret', 'ok');
        } else {
          btn.disabled = false; btn.removeAttribute('aria-busy');
          input.setAttribute('aria-invalid', 'true');
          setMsg(errorText(res.body, res.status), 'err');
        }
      })
      .catch(function () {
        btn.disabled = false; btn.removeAttribute('aria-busy');
        setMsg('Fikk ikke kontakt med serveren. Sjekk nettet og prøv igjen.', 'err');
      });
  }

  document.addEventListener('submit', function (ev) {
    if (ev.target && ev.target.id === 'weigh-form') { ev.preventDefault(); submitWeight(ev.target); }
  });
  document.addEventListener('click', function (ev) {
    var t = ev.target.closest && ev.target.closest('#refresh-btn, #copy-rec, [data-focus]');
    if (!t) return;
    if (t.id === 'refresh-btn') refresh();
    else if (t.id === 'copy-rec') {
      copyText(recText()).then(function () { toast('Økta er kopiert', 'ok'); }, function () { toast('Kunne ikke kopiere. Marker teksten manuelt.', 'warn'); });
    } else if (t.hasAttribute('data-focus')) {
      var target = document.getElementById(t.getAttribute('data-focus'));
      if (target) { ev.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(function () { target.focus({ preventScroll: true }); }, 350); }
    }
  });

  var resizeTimer;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      $$('[data-chart]').forEach(function (el) {
        if (Math.floor(el.clientWidth) !== +el.getAttribute('data-w')) {
          var fn = DRAW[el.getAttribute('data-chart')];
          if (fn) { el.setAttribute('data-w', Math.floor(el.clientWidth)); try { fn(el, Math.floor(el.clientWidth)); } catch (e) { el.innerHTML = ''; } }
        }
      });
    }, 150);
  });

  function init() {
    DATA = readData();
    renderAll();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
