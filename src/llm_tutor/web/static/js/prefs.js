/* Настройки вида до первой отрисовки: тема и свёрнутый сайдбар.
 *
 * Подключается в <head> без defer — иначе страница мигнула бы светлой темой
 * или развёрнутым сайдбаром. Встроенный скрипт для этого не годится: CSP.
 * Без localStorage — тема системная, сайдбар по умолчанию для страницы.
 */
(() => {
  const root = document.documentElement;
  const read = (key) => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  };
  const theme = read("llmTutor.theme");
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  const sidebar = read("llmTutor.sidebar");
  // На занятии сайдбар свёрнут, пока ученик сам его не развернул: ширина — уроку.
  const collapsed = sidebar ? sidebar === "collapsed" : root.dataset.page === "lesson";
  if (collapsed) root.dataset.sidebar = "collapsed";
})();
