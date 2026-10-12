/* Админка: ширина полос прогресса. Встроенные style запрещены CSP, поэтому
 * проценты ставятся через CSSOM из data-percent. */
(() => {
  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".progress-fill").forEach((bar) => {
      bar.style.width = `${Number(bar.dataset.percent) || 0}%`;
    });
  });
})();
