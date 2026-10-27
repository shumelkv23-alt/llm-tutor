/* Чат с тьютором.
 *
 * Реплики приходят с сервера уже отрисованными (безопасный Markdown), поэтому
 * их HTML вставляется как есть; текст ученика до ответа сервера показывается
 * только через textContent.
 */
(() => {
  const { $ } = window.App;
  const log = $("#chat-log");
  const form = $("#chat-form");
  const input = $("#chat-input");
  const typingLine = $("#chat-typing");
  const lessonNext = $("#lesson-next");
  const quoteBox = $("#chat-quote");
  const quoteStatus = $("#chat-quote-status");
  // Автопрокрутка — только если ученик и так внизу: не дёргаем того, кто читает выше.
  const NEAR_BOTTOM = 80;

  const EMPTY_TEXT = "Здесь можно спросить тьютора о теме, попросить пример или подсказку.";
  const SURVEY_TEXT = "Чат откроется после анкеты — она слева.";
  // «Объясни новичку» (срез 95): пока идёт объяснение, правый чат — наставник
  // со своей веткой разговора; история урока в нём не показывается.
  const MENTOR_EMPTY_TEXT = "Застрял? Спроси наставника — он подскажет, но объяснять новичку придётся тебе.";
  const MENTOR_DONE_TEXT = "Задание выполнено — нажми «Продолжить» во вкладке «Объясни».";
  const TUTOR_PLACEHOLDER = "Спросите тьютора… Enter — отправить, Shift+Enter — новая строка";
  const MENTOR_PLACEHOLDER = "Спроси наставника… Enter — отправить, Shift+Enter — новая строка";
  // Какая ветка сейчас в правом чате: "tutor" или "mentor:<id сессии>".
  let branch = null;
  // Режим чата: "tutor" | "mentor" | "done" (задание выполнено, ждём «Продолжить»).
  let mode = "tutor";
  // Подписи плашки фрагмента (срез 77); в истории их присылает сервер.
  const QUOTE_LABELS = { theory: "Из теории", task: "Из задания", code: "Из моего кода" };
  // Фрагмент, прикреплённый «Спросить…» к полю ввода: {source, text} или null.
  let attached = null;
  // Отложенное объявление диктору (см. attach).
  let announceTimer = null;

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  /**
   * Плашка фрагмента над репликой ученика. `quote.html` — с сервера (безопасный
   * Markdown); без него (эхо до ответа) текст ставится только через textContent.
   */
  function quoteFigure(quote) {
    const figure = document.createElement("figure");
    figure.className = `msg-quote${quote.source === "code" ? " code" : ""}`;
    const caption = document.createElement("figcaption");
    caption.textContent = quote.label || QUOTE_LABELS[quote.source] || "";
    const body = document.createElement("div");
    body.className = "msg-quote-body";
    if (quote.html !== undefined) {
      body.innerHTML = quote.html;
    } else if (quote.source === "code") {
      const pre = document.createElement("pre");
      pre.textContent = quote.text;
      body.append(pre);
    } else {
      body.textContent = quote.text;
    }
    figure.append(caption, body);
    return figure;
  }

  function bubble(message) {
    const element = document.createElement("div");
    element.className = `msg ${message.role === "user" ? "user" : "assistant"}`;
    // HTML — результат web.markdown.render на сервере (сырой HTML там выключен).
    if (message.quote) {
      const text = document.createElement("div");
      text.innerHTML = message.html;
      element.append(quoteFigure(message.quote), text);
    } else {
      element.innerHTML = message.html;
    }
    return element;
  }

  function placeholder() {
    log.querySelector(".chat-empty")?.remove();
    if (log.children.length) return;
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = window.Lesson.state?.survey
      ? SURVEY_TEXT
      : mode === "tutor"
        ? EMPTY_TEXT
        : mode === "done"
          ? MENTOR_DONE_TEXT
          : MENTOR_EMPTY_TEXT;
    log.append(empty);
  }

  function append(messages) {
    const stick = nearBottom();
    log.querySelector(".chat-empty")?.remove();
    messages.forEach((message) => log.append(bubble(message)));
    if (stick || messages.some((message) => message.role === "user")) scrollToEnd();
    placeholder();
  }

  function replace(messages) {
    log.replaceChildren();
    append(messages);
    scrollToEnd();
  }

  /** Реплика ученика до ответа сервера: убрать при успехе, пометить при ошибке. */
  function pending(text, quote = null) {
    log.querySelector(".chat-empty")?.remove();
    const element = document.createElement("div");
    element.className = "msg user pending";
    if (quote) {
      const line = document.createElement("div");
      line.textContent = text;
      element.append(quoteFigure({ source: quote.source, text: quote.text }), line);
    } else {
      element.textContent = text;
    }
    log.append(element);
    scrollToEnd();
    return {
      remove: () => element.remove(),
      fail: (reason) => {
        element.classList.remove("pending");
        element.classList.add("failed");
        element.title = reason;
      },
    };
  }

  function typing(on) {
    typingLine.hidden = !on;
    if (on && nearBottom()) scrollToEnd();
  }

  function locked() {
    return Boolean(window.Lesson.state?.survey) || mode === "done" || window.Lesson.busy;
  }

  /* --- Фрагмент над полем ввода («Спросить…», срез 77) --- */

  function renderAttached() {
    quoteBox.hidden = !attached;
    quoteBox.classList.toggle("code", attached?.source === "code");
    $("#chat-quote-label").textContent = attached ? `${QUOTE_LABELS[attached.source]}:` : "";
    $("#chat-quote-body").textContent = attached ? `„${attached.text}“` : "";
  }

  /** Прикрепить фрагмент к полю ввода (заменяет прежний) и перейти к вопросу. */
  function attach(focus, { announce = true } = {}) {
    // До анкеты чат закрыт: поле выключено, вопрос не уйдёт.
    if (window.Lesson.state?.survey) return;
    attached = { source: focus.source, text: focus.text };
    renderAttached();
    // Диктор повторяет объявление, только если текст области изменился:
    // сначала очищаем, затем пишем — так слышна и замена фрагмента тем же.
    clearTimeout(announceTimer);
    quoteStatus.textContent = "";
    if (announce) {
      const text = `Фрагмент прикреплён: ${QUOTE_LABELS[focus.source].toLowerCase()}`;
      announceTimer = setTimeout(() => {
        quoteStatus.textContent = text;
      }, 100);
    }
    input.focus();
  }

  function detach() {
    attached = null;
    renderAttached();
    clearTimeout(announceTimer);
    quoteStatus.textContent = "";
  }

  /** Время последней реплики ученика в истории (null — реплик нет). */
  function lastUserTs() {
    const users = (window.Lesson.state?.messages || []).filter((message) => message.role === "user");
    return users.length ? users[users.length - 1].ts : null;
  }

  /** Ход чата с вопросом и (необязательно) фрагментом. */
  function ask(question, focus = null) {
    if (window.Lesson.state?.survey) return Promise.resolve(null);
    const quote = focus ? { source: focus.source, text: focus.text } : null;
    const body = quote ? { text: question, quote } : { text: question };
    return window.Lesson.act("chat", body, { echo: question, quote });
  }

  /** Реплика из поля ввода вместе с прикреплённым фрагментом. */
  async function send(text) {
    const clean = text.trim();
    if (!clean || locked()) return;
    const quote = attached;
    const before = lastUserTs();
    input.value = "";
    autosize();
    if (quote) detach();
    const result = await ask(clean, quote);
    // Текст и плашку возвращаем, только если реплика не легла в журнал. После
    // «результат неизвестен» состояние перечитано: новая реплика ученика в нём —
    // ход дошёл. Сверяем время реплики, а не текст: тот же вопрос мог быть и
    // раньше, а HTML реплики экранирован иначе, чем textContent.
    const delivered = lastUserTs() !== before;
    if (!result && !input.value && !delivered) {
      input.value = clean;
      if (quote && !attached) attach(quote, { announce: false });
    }
    input.focus();
  }

  $("#chat-quote-remove").addEventListener("click", () => {
    detach();
    input.focus();
  });

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });

  input.addEventListener("keydown", (event) => {
    // Enter отправляет, Shift+Enter переносит; при наборе через IME не мешаем.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(input.value);
    }
  });

  input.addEventListener("input", autosize);

  $("#chat-chips").addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    // У кнопки урока свой обработчик (ниже).
    if (!chip || chip === lessonNext || locked()) return;
    if (chip.dataset.action === "task") {
      window.Lesson.act("task", {}, { echo: "Дай задание" });
      window.Lesson.selectTab("practice");
      window.Lesson.selectView("lesson");
    } else {
      // Чип — готовая реплика: черновик в поле и прикреплённый фрагмент не трогаем.
      ask(chip.dataset.say);
    }
  });

  /* --- Урок частями: «Дальше» к следующей части, «Проверим» — вопрос по теме --- */


  lessonNext.addEventListener("click", async () => {
    const button = window.Lesson.state?.lesson_button;
    if (!button || locked()) return;
    const data = await window.Lesson.act(
      "next",
      { node_id: button.node_id, part: button.part },
      { echo: button.check ? "Проверим" : "Дальше" }
    );
    // Вопрос выдан — он во вкладке «Практика».
    if (data?.state?.item) {
      window.Lesson.selectTab("practice");
      window.Lesson.selectView("lesson");
    }
  });

  function renderLessonButton(state) {
    const button = state.lesson_button;
    lessonNext.hidden = !button;
    if (button) lessonNext.textContent = button.label;
  }

  window.Lesson.chat = { append, replace, pending, typing, scrollToEnd, attach, ask, QUOTE_LABELS };

  /** Ветка правого чата по состоянию: тьютор урока или наставник «Объясни новичку». */
  function branchOf(state) {
    const active = state.explain?.active;
    if (active && (state.explain.blocked || active.status === "active")) {
      return { key: `mentor:${active.id}`, mode: active.status === "active" ? "mentor" : "done" };
    }
    return { key: "tutor", mode: "tutor" };
  }

  function renderBranch(state) {
    const next = branchOf(state);
    mode = next.mode;
    const mentor = mode !== "tutor";
    $("#chat-title").textContent = mentor ? "Наставник" : "Тьютор";
    $("#chat-subtitle").textContent = mentor
      ? "Помогает тебе объяснить, но не объясняет за тебя"
      : "Объясняет, подсказывает, проверяет";
    $("#view-chat-label").textContent = mentor ? "Наставник" : "Чат";
    typingLine.lastChild.textContent = mentor ? "Наставник печатает…" : "Тьютор печатает…";
    $("#chat-chips").hidden = mentor;
    form.setAttribute("aria-label", mentor ? "Вопрос наставнику" : "Сообщение тьютору");
    $("#chat-log").setAttribute("aria-label", mentor ? "Переписка с наставником" : "Переписка с тьютором");
    if (next.key !== branch) {
      // Ветка сменилась (задание началось или закончилось): история — с сервера.
      const first = branch === null;
      branch = next.key;
      if (!first) replace(state.messages);
    }
  }

  let attachedNode = null;
  window.Lesson.onState((state) => {
    renderBranch(state);
    const off = Boolean(state.survey) || mode === "done";
    // Фрагмент — про занятие, на котором его выделили: смена занятия или
    // анкета модуля снимают его, иначе вопрос ушёл бы с чужой цитатой (M-2).
    const node = state.node?.id ?? null;
    if (attached && (off || (attachedNode !== null && node !== attachedNode))) detach();
    attachedNode = node;
    input.disabled = off;
    form.querySelector("button").disabled = off;
    document.querySelectorAll("#chat-chips .chip").forEach((chip) => {
      chip.disabled = off;
    });
    input.placeholder = state.survey
      ? SURVEY_TEXT
      : mode === "done"
        ? MENTOR_DONE_TEXT
        : mode === "mentor"
          ? MENTOR_PLACEHOLDER
          : TUTOR_PLACEHOLDER;
    renderLessonButton(state);
    placeholder();
  });
})();
