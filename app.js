// Внутри приложения BoW (window.BoW) у каждой страницы сайта свой таб снизу, поэтому меню сайта в нём
// лишнее: оно ведёт туда же, но мимо табов — и показывает «Еду», которой в приложении нет вовсе.
// Стиль добавляется синхронно, до отрисовки, иначе меню успевает мигнуть; полосу, в которой кроме меню
// ничего не было (главная, Well-being, Почитать, Дом), убираем целиком — пустая планка ест высоту экрана.
if (window.BoW) {
  document.documentElement.classList.add('bow');
  document.head.appendChild(Object.assign(document.createElement('style'), { textContent: '.bow .nav { display: none }' }));
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.bar').forEach(bar => {
      const inner = bar.querySelector('.bar-inner') || bar;
      inner.querySelectorAll('.nav').forEach(nav => nav.remove());
      if (!inner.querySelector('*')) {
        bar.remove();
        document.querySelectorAll('.wrap').forEach(wrap => wrap.style.paddingTop = '20px');
      }
    });
  });
}
