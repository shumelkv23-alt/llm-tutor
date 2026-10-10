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
          ? "Связь прервалась. Результат неизвестен — обновляем данные, прежде чем повторять."
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
        problem.message = "Сервер вернул ошибку. Результат неизвестен — обновляем данные, прежде чем повторять.";
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
          ? "Ответ сервера не дочитан. Результат неизвестен — обновляем данные, прежде чем повторять."
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

  document.addEventListener("DOMContentLoaded", () => {
    checkConnection();
    $("#logout")?.addEventListener("click", logout);
  });

  return { $, icon, connection, notify, request, confirm };
})();
