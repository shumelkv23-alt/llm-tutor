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
    let response;
    try {
      response = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
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
      problem.uncertain = changing && response.status >= 500;
      if (problem.uncertain) {
        problem.message = "Сервер вернул ошибку. Результат неизвестен — обновляем данные, прежде чем повторять.";
      }
      if (response.status === 401 && !location.pathname.startsWith("/login")) {
        location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
      }
      throw problem;
    }
    return response.status === 204 ? null : response.json();
  }

  /** Подтверждение через общий <dialog>: resolve(true | false). */
  function confirm(title, description, { label = "Подтвердить", danger = false } = {}) {
    const dialog = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-description").textContent = description;
    const submit = $("#confirm-submit");
    submit.textContent = label;
    submit.className = danger ? "btn danger" : "btn primary";
    dialog.returnValue = "";
    dialog.showModal();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    });
  }

  async function checkConnection() {
    try {
      await request("/healthz", { timeout: 10000 });
    } catch {
      /* статус уже выставлен в request */
    }
  }

  document.addEventListener("DOMContentLoaded", checkConnection);

  return { $, icon, connection, notify, request, confirm };
})();
