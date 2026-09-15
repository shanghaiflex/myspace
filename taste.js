// Советы Claude по фильмам, книгам и лекциям — общий блок для films.html, books.html и lectures.html.
// Разметку и стили страница даёт сама (секция #recs с .rechead/.recgrid), здесь только данные,
// отрисовка карточек, вердикты и кнопка «Обновить».
//   Taste.mount({ kind: 'film'|'book'|'lecture', onAccepted(added) })
window.Taste = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmtDay = iso => {
    const d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
  };

  function mount(opts) {
    const kind = opts.kind;
    const sec = document.querySelector('#recs');
    if (!sec) return;
    const grid = sec.querySelector('#recGrid');
    const meta = sec.querySelector('#recsMeta');
    const btn = sec.querySelector('#recsRefresh');
    const toast = opts.toast || (() => {});
    const noun = { film: 'фильм', book: 'книгу', lecture: 'лекцию' }[kind] || 'это';
    // У лекций есть третий ответ: «уже слушал» — направление верное, просто я это знаю.
    // Он не отказ: лекция уезжает в каталог прослушанной и работает дальше как вкус.
    const ACTS = {
      listened: { label: 'Уже слушал', toast: 'Отметил прослушанным — учту во вкусе' },
      dismissed: { label: 'Не то', toast: 'Понял, больше такого не предлагаю' },
    };
    const acts = opts.acts || ['liked', 'dismissed'];
    let updatedAt = null;

    function render(db) {
      const items = (db && db.items) || [];
      sec.hidden = !items.length;
      meta.textContent = db && db.updatedAt ? 'обновлено ' + fmtDay(db.updatedAt) : '';
      grid.innerHTML = items.map(r => `
        <article class="rec" data-id="${esc(r.id)}">
          <div class="top">
            <div class="cv">${r.cover ? `<img src="${esc(r.cover)}" alt="" loading="lazy">` : ''}</div>
            <div>
              <div class="t">${esc(r.title)}${r.year ? ` <span class="y">${r.year}</span>` : ''}</div>
              <div class="s">${esc(r.meta || r.author || '')}${r.unverified ? ' · <span class="warn">не нашёл в каталогах</span>' : ''}</div>
            </div>
          </div>
          ${r.reason ? `<div class="why">${esc(r.reason)}</div>` : ''}
          <div class="acts">
            ${acts.map(a => a === 'liked'
              ? '<button class="take" data-act="liked">Хочу</button>'
              : `<button class="no" data-act="${a}">${ACTS[a].label}</button>`).join('')}
          </div>
        </article>`).join('');
    }

    async function load() {
      try {
        const db = await fetch(`/api/recs/${kind}`, { cache: 'no-store' }).then(r => r.ok ? r.json() : null);
        updatedAt = (db && db.updatedAt) || null;
        render(db);
        return db;
      } catch { return null; }
    }

    grid.addEventListener('click', async e => {
      const b = e.target.closest('button[data-act]');
      if (!b) return;
      const card = b.closest('.rec');
      const id = card.dataset.id;
      card.classList.add('gone');
      try {
        const out = await fetch(`/api/rec/${kind}/${encodeURIComponent(id)}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ verdict: b.dataset.act }),
        }).then(async r => { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status); return r.json(); });
        render(out.recs);
        if (b.dataset.act === 'liked') {
          toast(out.added ? `Добавил ${noun} в планы` : 'Уже в каталоге');
        } else {
          toast(ACTS[b.dataset.act].toast);
        }
        // «Уже слушал» тоже кладёт запись в каталог — страницу надо перечитать так же, как после «Хочу».
        if (out.added && opts.onAccepted) opts.onAccepted(out.added);
      } catch (err) {
        card.classList.remove('gone');
        toast('Ошибка: ' + err.message);
      }
    });

    btn.addEventListener('click', async () => {
      btn.disabled = true; btn.textContent = 'Ищу…';
      toast('Claude подбирает, это займёт минуту');
      // Завершение ловим по статусу задания на сервере: подпись показывает только день
      // и при повторе в тот же день не меняется (на миксах кнопка из-за этого висела «Ищу…»).
      const before = updatedAt;
      try {
        await fetch(`/api/recs/${kind}/refresh`, { method: 'POST' });
        for (let i = 0; i < 72; i++) {
          await new Promise(r => setTimeout(r, 5000));
          const db = await load();
          if (db && db.job && String(db.job).startsWith('error')) { toast('Не получилось: ' + String(db.job).slice(7, 120)); break; }
          if (db && db.job === 'done') { toast(db.updatedAt && db.updatedAt !== before ? 'Готово: новые советы' : 'Готово, список тот же'); break; }
        }
      } catch (e) { toast('Ошибка: ' + e.message); }
      btn.disabled = false; btn.textContent = 'Обновить';
    });

    load();
  }

  return { mount };
})();
