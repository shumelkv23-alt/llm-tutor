/* Страница занятия: состояние с сервера и единая точка для ходов.
 *
 * Источник правды — сервер: каждый ход отвечает репликами для чата и свежим
 * состоянием, страница перерисовывается по нему. Модули (чат, практика,
 * анкета, маршрут, код) подписываются на состояние через Lesson.onState.
 */
window.Lesson = (() => {
  const { $, request, notify } = window.App;
  const root = $("#lesson");
  const course = root.dataset.course;
  const base = `/api/courses/${encodeURIComponent(course)}/lesson`;
  const listeners = [];
  let state = null;
  let busy = false;
  let idleWaiters = [];

  const STATUS_LABELS = {
    closed: "закрыта",
    current: "текущая",
    claimed: "знакомая — проверим",
    available: "доступна",
    ahead: "впереди",
  };

  function onState(listener) {
    listeners.push(listener);
    if (state) listener(state);
  }

  function setState(next) {
    state = next;
    root.setAttribute("aria-busy", "false");
    root.classList.toggle("locked", Boolean(state.survey));
    renderHead();
    listeners.forEach((listener) => listener(state));
  }

  function renderHead() {
    const title = $("#topic-title");
    const meta = $("#topic-meta");
    meta.replaceChildren();
    if (state.survey) {
      title.textContent = "Знакомимся";
      meta.textContent = "Пара вопросов — и тьютор подберёт, с чего начать.";
    } else if (state.node) {
      title.textContent = state.node.name;
      const badge = document.createElement("span");
      badge.className = state.mode === "verify" ? "badge accent" : "badge primary";
      badge.textContent = state.mode === "verify" ? "Проверка темы" : "Урок";
      meta.append(badge, `Закрыто тем: ${state.closed} из ${state.topics.length}`);
    } else {
      title.textContent = "Тема не выбрана";
      meta.textContent = "Откройте маршрут и выберите тему.";
    }
    $("#route-open").disabled = Boolean(state.survey);
    // Во время хода кнопка не выключается (фокус ушёл бы на body): повтор
    // отсекает act(), а вид «занято» даёт aria-disabled у [data-turn].
    $("#verify-topic").disabled = Boolean(state.survey) || !state.node;
  }

  async function load() {
    try {
      const data = await request(`${base}/state`);
      setState(data.state);
      window.Lesson.chat?.replace(data.state.messages);
    } catch (problem) {
      const error = $("#page-error");
      error.textContent = `${problem.message} Обновите страницу.`;
      error.hidden = false;
    }
  }

  function setBusy(value) {
    busy = value;
    if (!value) {
      const waiters = idleWaiters;
      idleWaiters = [];
      waiters.forEach((resolve) => resolve());
    }
    root.classList.toggle("busy", value);
    // Не трогаем disabled: у кнопок своя логика (например, «Ответить» без
    // выбранного варианта). Повторный ход и так отсекает проверка в act().
    document.querySelectorAll("[data-turn]").forEach((element) => {
      element.setAttribute("aria-disabled", String(value));
    });
    if (state) renderHead();
    window.Lesson.chat?.typing(value);
  }

  /**
   * Ход занятия: POST к API, реплики — в чат, состояние — во все панели.
   * `echo` — что показать в чате от ученика до ответа сервера.
   */
  async function act(path, body = {}, { echo = null } = {}) {
    if (busy) return null;
    setBusy(true);
    const pending = echo ? window.Lesson.chat?.pending(echo) : null;
    try {
      const data = await request(`${base}/${path}`, { method: "POST", body });
      pending?.remove();
      window.Lesson.chat?.append(data.messages);
      setState(data.state);
      return data;
    } catch (problem) {
      if (problem.uncertain) {
        pending?.remove();
        notify(problem.message, { error: true });
        await load();
      } else {
        // Реплика не принята: убираем её из чата (текст вернётся в поле ввода),
        // причину показывает уведомление.
        pending?.remove();
        notify(problem.message, { error: true });
        if (problem.status === 409) await load();
      }
      return null;
    } finally {
      setBusy(false);
    }
  }

  /* --- Вкладки урока (роль tablist: стрелки двигают выбор) --- */

  function selectTab(name, { focus = false } = {}) {
    document.querySelectorAll(".tabs [role=tab]").forEach((tab) => {
      const selected = tab.id === `tab-btn-${name}`;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
      $(`#${tab.getAttribute("aria-controls")}`).hidden = !selected;
    });
    document.dispatchEvent(new CustomEvent("lesson:tab", { detail: name }));
  }

  function tabKeys(event, selector, select) {
    const tabs = [...document.querySelectorAll(selector)];
    const index = tabs.indexOf(event.target);
    if (index < 0) return;
    const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -index, End: tabs.length - 1 - index };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = tabs[(index + moves[event.key] + tabs.length) % tabs.length];
    select(next);
  }

  /* --- Переключатель «Урок | Чат» на узком экране --- */

  function selectView(view) {
    root.dataset.view = view;
    document.querySelectorAll(".view-switch [role=tab]").forEach((tab) => {
      const selected = tab.dataset.view === view;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
    if (view === "chat") window.Lesson.chat?.scrollToEnd();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const tabList = $(".tabs");
    tabList.addEventListener("click", (event) => {
      const tab = event.target.closest("[role=tab]");
      if (tab) selectTab(tab.id.replace("tab-btn-", ""));
    });
    tabList.addEventListener("keydown", (event) =>
      tabKeys(event, ".tabs [role=tab]", (tab) => selectTab(tab.id.replace("tab-btn-", ""), { focus: true }))
    );
    const viewSwitch = $(".view-switch");
    viewSwitch.addEventListener("click", (event) => {
      const tab = event.target.closest("[role=tab]");
      if (tab) selectView(tab.dataset.view);
    });
    viewSwitch.addEventListener("keydown", (event) =>
      tabKeys(event, ".view-switch [role=tab]", (tab) => {
        selectView(tab.dataset.view);
        tab.focus();
      })
    );
    $("#verify-topic").addEventListener("click", () => act("verify", {}, { echo: "Закрой тему — я её уже знаю." }));
    load();
  });

  /** Дождаться конца текущего хода (сразу, если хода нет). */
  function whenIdle() {
    return busy ? new Promise((resolve) => idleWaiters.push(resolve)) : Promise.resolve();
  }

  return {
    course,
    base,
    act,
    whenIdle,
    load,
    onState,
    selectTab,
    selectView,
    STATUS_LABELS,
    get state() {
      return state;
    },
    get busy() {
      return busy;
    },
  };
})();
