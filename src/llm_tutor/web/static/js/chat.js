/* Чат с тьютором.
 *
 * Реплики приходят с сервера уже отрисованными (безопасный Markdown), поэтому
 * их HTML вставляется как есть; текст ученика до ответа сервера показывается
 * только через textContent.
 */
(() => {
  const { $, icon } = window.App;
  const log = $("#chat-log");
  const form = $("#chat-form");
  const input = $("#chat-input");
  const mic = $("#chat-mic");
  const typingLine = $("#chat-typing");
  // Идёт запись с микрофона: Enter и «Отправить» сначала останавливают её,
  // а не отправляют недописанную реплику.
  let listening = false;
  const voice = window.Voice ? window.Voice.attach(mic, input, { label: "Сообщение тьютору" }) : null;
  if ((!voice || voice.supported === false) && mic) mic.hidden = true;
  const lessonNext = $("#lesson-next");
  const quoteBox = $("#chat-quote");
  const quoteStatus = $("#chat-quote-status");
  // Автопрокрутка — только если ученик и так внизу: не дёргаем того, кто читает выше.
  const NEAR_BOTTOM = 80;

  const EMPTY_TEXT = "Здесь можно спросить тьютора о теме, попросить пример или подсказку.";
  const SURVEY_TEXT = "Чат откроется после анкеты — она слева.";
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

  /** Текст ответа без кода, таблиц и служебных плашек — для чтения вслух. */
  function speechText(element) {
    const copy = element.cloneNode(true);
    copy.querySelectorAll(".msg-tools, .msg-quote, pre, code, table, .code-block").forEach((node) => {
      node.remove();
    });
    return copy.textContent || "";
  }

  function speechButton(element) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn ghost icon-only speech-btn";
    button.setAttribute("aria-label", "Прослушать ответ");
    button.title = "Прослушать ответ";
    button.setAttribute("aria-pressed", "false");
    button.append(icon("speaker"));
    button.addEventListener("click", () => {
      window.Voice.speak(speechText(element), button);
    });
    const tools = document.createElement("div");
    tools.className = "msg-tools";
    tools.append(button);
    return tools;
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
    if (message.role === "assistant" && window.Voice?.ttsSupported) {
      element.append(speechButton(element));
    }
    return element;
  }

  function placeholder() {
    log.querySelector(".chat-empty")?.remove();
    if (log.children.length) return;
    const empty = document.createElement("p");
    empty.className = "chat-empty";
    empty.textContent = window.Lesson.state?.survey ? SURVEY_TEXT : EMPTY_TEXT;
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
    window.Voice?.stopSpeaking?.();
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
    return Boolean(window.Lesson.state?.survey) || window.Lesson.busy;
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
    // Пока идёт запись, кнопка и Enter останавливают её: отправлять
    // недослушанный текст нельзя.
    if (listening) {
      voice?.stop();
      return;
    }
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
  input.addEventListener("voice:start", () => {
    listening = true;
  });
  input.addEventListener("voice:stop", () => {
    listening = false;
  });
  // Начался ход (кнопка «Дальше», чип, ответ) — запись останавливаем:
  // диктовать в поле, пока тьютор отвечает, незачем.
  document.addEventListener("lesson:busy", (event) => {
    if (event.detail) voice?.stop();
  });

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

  let attachedNode = null;
  window.Lesson.onState((state) => {
    const off = Boolean(state.survey);
    // Фрагмент — про занятие, на котором его выделили: смена занятия или
    // анкета модуля снимают его, иначе вопрос ушёл бы с чужой цитатой (M-2).
    const node = state.node?.id ?? null;
    if (attached && (off || (attachedNode !== null && node !== attachedNode))) detach();
    attachedNode = node;
    input.disabled = off;
    // Именно submit: первая кнопка формы — микрофон, её выключает Voice.
    form.querySelector('button[type="submit"]').disabled = off;
    voice?.setEnabled(!off);
    document.querySelectorAll("#chat-chips .chip").forEach((chip) => {
      chip.disabled = off;
    });
    input.placeholder = off ? SURVEY_TEXT : "Спросите тьютора… Enter — отправить, Shift+Enter — новая строка";
    renderLessonButton(state);
    placeholder();
  });
})();
