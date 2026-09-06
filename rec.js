// Shared helpers for lectures (used by index.html and lectures.html).
window.Lect = (() => {
  const cyr = s => /[а-яё]/i.test(s || '');
  const stems = title => new Set((title || '').toLowerCase().replace(/[^a-zа-яё0-9 ]/gi, ' ').split(/\s+/)
    .filter(w => w.length >= 5).map(w => w.slice(0, 5)));
  const STOP = new Set(['средн', 'веках', 'истор', 'часть', 'европ', 'жизнь', 'повсе']);

  // Rank unlistened lectures by similarity to what the user listened to / is listening.
  function recommend(db, limit = 12, channel = null) {
    const all = (db.lectures || []).filter(l => !channel || l.channel === channel);
    const ls = all;
    const done = ls.filter(l => l.status === 'listening' || l.status === 'listened');
    const doneSeries = new Map();
    done.forEach(l => (l.series || []).forEach(s => doneSeries.set(s, (doneSeries.get(s) || 0) + 1)));
    const doneStems = new Set();
    done.forEach(l => stems(l.title).forEach(w => { if (!STOP.has(w)) doneStems.add(w); }));
    const scored = ls.filter(l => (l.status === 'new' || l.status === 'queued') && !l.live).map(l => {
      let score = l.status === 'queued' ? 100 : 0;
      (l.series || []).forEach(s => { score += 3 * (doneSeries.get(s) || 0); });
      let kw = 0; stems(l.title).forEach(w => { if (doneStems.has(w)) kw++; });
      score += Math.min(kw, 3);
      if (/средн|medieval|middle ages|dark ages/i.test(l.title)) score += 2;
      if (cyr(l.title)) score += 2; else score -= 3;
      return { l, score };
    });
    scored.sort((a, b) => b.score - a.score || (a.l.queuedAt || 0) - (b.l.queuedAt || 0) || a.l.order - b.l.order);
    return scored.slice(0, limit).map(x => x.l);
  }

  const fmtDur = s => { if (!s) return ''; const t = Math.round(s / 60), h = Math.floor(t / 60), m = t % 60; return h ? `${h} ч ${m} мин` : `${m} мин`; };
  const fmtTime = s => { s = Math.max(0, Math.floor(s || 0)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60; return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(sec).padStart(2, '0'); };
  const thumb = l => typeof l === 'string' ? `https://i.ytimg.com/vi/${l}/mqdefault.jpg` : (l.artwork || (l.source === 'soundcloud' ? '' : `https://i.ytimg.com/vi/${l.id}/mqdefault.jpg`));
  const bigThumb = l => l.artwork || `https://i.ytimg.com/vi/${l.id}/hqdefault.jpg`;
  const channelName = (db, l) => ((db.channels || []).find(c => c.id === l.channel) || {}).name || '';
  const seriesName = (db, l) => ((l.series || []).map(id => db.series[id]).filter(Boolean)[0] || '').replace(/\.$/, '');
  return { recommend, fmtDur, fmtTime, thumb, bigThumb, seriesName, channelName, cyr };
})();
