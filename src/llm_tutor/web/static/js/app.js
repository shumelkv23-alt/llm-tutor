/* Общая оболочка страниц: запросы к API, уведомления, подтверждения, статус связи.
 *
 * По образцу panels.js из bar_order_bot. Данные в DOM попадают только через
 * textContent: HTML приходит готовым лишь от сервера (безопасный Markdown).
 */
window.App = (() => {
  const $ = (selector, root = document) => root.querySelector(selector);

  const ICONS = document.documentElement.dataset.icons || "/static/icons.svg";

  /** SVG-иконка из спрайта (элемент, а не строка HTML). */
  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", `${ICONS}#${name}`);
    svg.append(use);
    return svg;
  }

  function connection(message, state = "") {
    const element = $("#connection");
    if (!element) return;
    element.textContent = message;
    element.className = `connection ${state}`.trim();
  }

  function notify(message, { error = false } = {}) {
    const element = $("#toast");
    if (!element) return;
    element.textContent = message;
    element.className = error ? "toast error" : "toast";
    element.hidden = false;
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => {
      element.hidden = true;
    }, error ? 8000 : 4000);
  }

  /**
   * Запрос к API. Ошибка несёт `status` и `uncertain`: для изменяющего
   * запроса обрыв связи или 5xx означает «результат неизвестен» — повторять
   * вслепую нельзя, сначала перечитать состояние (ТЗ панелей бара, §3).
   */
  async function request(path, { method = "GET", body, timeout = 90000 } = {}) {
    const changing = method !== "GET";
    // Изменяющий запрос всегда идёт JSON-ом: сервер отвергает остальное (CSRF).
    const payload = body === undefined && changing ? {} : body;
    let response;
    try {
      response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: payload === undefined ? {} : { "Content-Type": "application/json" },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: AbortSignal.timeout(timeout),
      });
    } catch (cause) {
      connection("Нет связи", "error");
      const problem = new Error(
        changing
          ? "Связь прервалась. Результат неизвестен — проверьте, прошло ли действие, прежде чем повторять."
          : "Нет связи с сервером. Проверьте подключение и попробуйте снова."
      );
      problem.uncertain = changing;
      problem.cause = cause;
      throw problem;
    }
    connection("На связи", "online");
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
      const problem = new Error(detail || `Сервер ответил ошибкой ${response.status}.`);
      problem.status = response.status;
      problem.detail = data.detail;
      problem.field = data.detail?.field || null;
      problem.uncertain = changing && response.status >= 500;
      if (problem.uncertain) {
        problem.message = "Сервер вернул ошибку. Результат неизвестен — проверьте, прошло ли действие, прежде чем повторять.";
      }
      const authPage = ["/login", "/register"].includes(location.pathname);
      if (response.status === 401 && !authPage) {
        location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
      }
      throw problem;
    }
    if (response.status === 204) return null;
    try {
      return await response.json();
    } catch (cause) {
      // Ответ пришёл, но тело не прочиталось (обрыв, таймаут, не JSON):
      // изменение могло примениться — повторять вслепую нельзя.
      const problem = new Error(
        changing
          ? "Ответ сервера не дочитан. Результат неизвестен — проверьте, прошло ли действие, прежде чем повторять."
          : "Сервер ответил неожиданно. Попробуйте ещё раз."
      );
      problem.status = response.status;
      problem.uncertain = changing;
      problem.cause = cause;
      throw problem;
    }
  }

  /** Подтверждение через общий <dialog>: resolve(true | false).
   *
   * Диалог один на страницу. Новый вызов, пока открыт прежний, сначала
   * отвечает прежнему «нет»: иначе одно нажатие подтвердило бы оба действия,
   * а ученик видел бы текст только второго.
   */
  let pendingConfirm = null;

  function confirm(title, description, { label = "Подтвердить", danger = false } = {}) {
    const dialog = $("#confirm-dialog");
    if (pendingConfirm) {
      // Открытый диалог не закрываем: событие close асинхронное и досталось
      // бы новому вызову. Прежнему — «нет», тексты — новые.
      pendingConfirm(false);
      pendingConfirm = null;
    }
    $("#confirm-title").textContent = title;
    $("#confirm-description").textContent = description;
    const submit = $("#confirm-submit");
    submit.textContent = label;
    submit.className = danger ? "btn danger" : "btn primary";
    dialog.returnValue = "";
    if (!dialog.open) dialog.showModal();
    return new Promise((resolve) => {
      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        dialog.removeEventListener("close", onClose);
        if (pendingConfirm === finish) pendingConfirm = null;
        resolve(value);
      };
      const onClose = () => finish(dialog.returnValue === "confirm");
      pendingConfirm = finish;
      dialog.addEventListener("close", onClose);
    });
  }

  async function checkConnection() {
    try {
      await request("/healthz", { timeout: 10000 });
    } catch {
      /* статус уже выставлен в request */
    }
  }

  async function logout() {
    try {
      await request("/api/auth/logout", { method: "POST", timeout: 10000 });
    } finally {
      location.assign("/");
    }
  }

  /* --- Вид: сайдбар и тема (начальное состояние ставит prefs.js) --- */

  function store(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      /* без хранилища настройка живёт до перезагрузки */
    }
  }

  function syncSidebarButton() {
    const button = $("#sidebar-toggle");
    if (!button) return;
    const collapsed = document.documentElement.dataset.sidebar === "collapsed";
    button.setAttribute("aria-expanded", String(!collapsed));
    button.querySelector("span").textContent = collapsed ? "Развернуть меню" : "Свернуть меню";
    button.title = collapsed ? "Развернуть меню" : "Свернуть меню";
  }

  function applySidebar(collapsed) {
    const root = document.documentElement;
    if (collapsed) root.dataset.sidebar = "collapsed";
    else delete root.dataset.sidebar;
    syncSidebarButton();
    // Ширина урока поменялась — пусть ползунок пересчитает пределы.
    window.dispatchEvent(new Event("resize"));
  }

  function toggleSidebar() {
    const root = document.documentElement;
    const collapsed = root.dataset.sidebar !== "collapsed";
    applySidebar(collapsed);
    // Совпало с умолчанием страницы — выбор забываем: иначе «развернул на главной»
    // навсегда отменило бы свёрнутый сайдбар на занятии.
    const byDefault = root.dataset.page === "lesson";
    store("llmTutor.sidebar", collapsed === byDefault ? null : collapsed ? "collapsed" : "expanded");
  }

  /** Тема: "light", "dark" или "system" (как в системе). */
  function setTheme(theme) {
    const root = document.documentElement;
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    else delete root.dataset.theme;
    store("llmTutor.theme", theme === "system" ? null : theme);
  }

  function currentTheme() {
    return document.documentElement.dataset.theme || "system";
  }

  document.addEventListener("DOMContentLoaded", () => {
    checkConnection();
    $("#logout")?.addEventListener("click", logout);
    $("#sidebar-toggle")?.addEventListener("click", toggleSidebar);
    syncSidebarButton();
  });

  // Настройки вида сменили в другой вкладке — применяем здесь без перезагрузки.
  window.addEventListener("storage", (event) => {
    const root = document.documentElement;
    if (event.key === "llmTutor.theme") {
      if (event.newValue === "light" || event.newValue === "dark") root.dataset.theme = event.newValue;
      else delete root.dataset.theme;
    } else if (event.key === "llmTutor.sidebar") {
      applySidebar(event.newValue ? event.newValue === "collapsed" : root.dataset.page === "lesson");
    }
  });

  return { $, icon, connection, notify, request, confirm, setTheme, currentTheme };
})();
