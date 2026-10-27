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
  // Курс стоит, пока не выполнено «Объясни новичку» (срез 95): запоминаем прошлое
  // значение, чтобы выбирать вкладку только в момент перехода.
  let wasBlocked = false;

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
    applyLock();
    listeners.forEach((listener) => listener(state));
  }

  function isBlocked() {
    return Boolean(state?.explain?.blocked);
  }

  /** Вкладки урока закрыты, пока висит «Объясни новичку»; открыта только она. */
  function applyLock() {
    const blocked = isBlocked();
    document.querySelectorAll(".tabs [role=tab]").forEach((tab) => {
      const locked = blocked && tab.id !== "tab-btn-explain";
      if (locked) tab.setAttribute("aria-disabled", "true");
      else tab.removeAttribute("aria-disabled");
      tab.querySelector(".tab-lock")?.toggleAttribute("hidden", !locked);
    });
    if (blocked && !wasBlocked) selectTab("explain");
    // Задание выполнено, курс пошёл дальше: к уроку или к заданию разминки.
    else if (!blocked && wasBlocked) selectTab(state.item ? "practice" : "theory");
    wasBlocked = blocked;
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
      badge.textContent = state.mode === "verify" ? "Проверка занятия" : "Урок";
      meta.append(badge, place(state.node.lesson));
    } else if (isBlocked() && state.explain.active) {
      title.textContent = `Объясни новичку: «${state.explain.active.title}»`;
      const badge = document.createElement("span");
      badge.className = "badge accent";
      badge.textContent = "Обязательное задание";
      meta.append(badge, "Дальше — после объяснения.");
    } else {
      title.textContent = "Занятие не выбрано";
      meta.textContent = "Откройте маршрут и выберите занятие.";
    }
    renderSections(state);
    $("#route-open").disabled = Boolean(state.survey) || isBlocked();
    // Во время хода кнопка не выключается (фокус ушёл бы на body): повтор
    // отсекает act(), а вид «занято» даёт aria-disabled у [data-turn].
    $("#verify-topic").disabled = Boolean(state.survey) || !state.node;
  }

  /** «Модуль N · Занятие k из m · Раздел j из r: …» — где ученик в курсе (срез 55). */
  function place(lesson) {
    if (!lesson) return "";
    const parts = [`Модуль ${lesson.module.number}`, `Занятие ${lesson.index} из ${lesson.total}`];
    if (lesson.sections.length) {
      const current = lesson.sections[lesson.section_index - 1];
      parts.push(
        current
          ? `Раздел ${lesson.section_index} из ${lesson.sections.length}: ${current.title}`
          : lesson.final.title
      );
    }
    return parts.join(" · ");
  }

  // «Без заданий» — раздел, по которому заданий в банке ещё нет:
  // занятие он не держит, но и «пройденным» не считается.
  const SECTION_MARKS = { passed: "✓", weak: "⚠️", current: "▶", ahead: "·", skipped: "–" };
  const SECTION_LABELS = { passed: "пройден", weak: "слабое место", current: "сейчас", ahead: "впереди", skipped: "без заданий" };

  /** Полоса разделов над вкладками: разделы занятия и итоговое со статусами. */
  function renderSections(current) {
    const strip = $("#section-strip");
    strip.replaceChildren();
    const lesson = current.survey ? null : current.node?.lesson;
    strip.hidden = !lesson || !lesson.sections.length;
    if (strip.hidden) return;
    [...lesson.sections, lesson.final].forEach((part, index) => {
      const item = document.createElement("li");
      item.className = `section-step ${part.status}`;
      if (part.status === "current") item.setAttribute("aria-current", "step");
      const mark = document.createElement("span");
      mark.className = "section-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = SECTION_MARKS[part.status] || "·";
      const name = document.createElement("span");
      name.className = "section-name";
      name.textContent = index < lesson.sections.length ? `${index + 1}. ${part.title}` : part.title;
      const label = document.createElement("span");
      label.className = "visually-hidden";
      label.textContent = ` — ${SECTION_LABELS[part.status] || ""}`;
      item.append(mark, name, label);
      strip.append(item);
    });
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

  function setBusy(value, typing = true) {
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
    window.Lesson.chat?.typing(value && typing);
    document.dispatchEvent(new CustomEvent("lesson:busy", { detail: value }));
  }

  /**
   * Ход занятия: POST к API, реплики — в чат, состояние — во все панели.
   * `echo` — что показать в чате от ученика до ответа сервера, `quote` —
   * фрагмент над ним (плашка, срез 77). `toChat: false` — реплики хода не для
   * правого чата (вкладка «Объясни» рисует разговор с новичком сама),
   * `typing: false` — «печатает…» в чате не показывать.
   */
  async function act(path, body = {}, { echo = null, quote = null, toChat = true, typing = true } = {}) {
    if (busy) return null;
    setBusy(true, typing);
    const pending = echo ? window.Lesson.chat?.pending(echo, quote) : null;
    try {
      const data = await request(`${base}/${path}`, { method: "POST", body });
      pending?.remove();
      if (toChat) window.Lesson.chat?.append(data.messages || []);
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
    // Пока висит «Объясни новичку», остальные вкладки закрыты.
    if (isBlocked() && name !== "explain") return;
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
      if (tab && tab.getAttribute("aria-disabled") !== "true") selectTab(tab.id.replace("tab-btn-", ""));
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
    isBlocked,
    STATUS_LABELS,
    get state() {
      return state;
    },
    get busy() {
      return busy;
    },
  };
})();
