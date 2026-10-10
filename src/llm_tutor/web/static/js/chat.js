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
  // Автопрокрутка — только если ученик и так внизу: не дёргаем того, кто читает выше.
  const NEAR_BOTTOM = 80;

  const EMPTY_TEXT = "Здесь можно спросить тьютора о теме, попросить пример или подсказку.";
  const SURVEY_TEXT = "Чат откроется после анкеты — она слева.";

  function escapeHtml(text) {
    const box = document.createElement("div");
    box.textContent = text;
    return box.innerHTML;
  }

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function bubble(message) {
    const element = document.createElement("div");
    element.className = `msg ${message.role === "user" ? "user" : "assistant"}`;
    // HTML — результат web.markdown.render на сервере (сырой HTML там выключен).
    element.innerHTML = message.html;
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
    log.replaceChildren();
    append(messages);
    scrollToEnd();
  }

  /** Реплика ученика до ответа сервера: убрать при успехе, пометить при ошибке. */
  function pending(text) {
    log.querySelector(".chat-empty")?.remove();
    const element = document.createElement("div");
    element.className = "msg user pending";
    element.textContent = text;
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

  async function send(text) {
    const clean = text.trim();
    if (!clean || locked()) return;
    input.value = "";
    autosize();
    const result = await window.Lesson.act("chat", { text: clean }, { echo: clean });
    // Текст возвращаем, только если сервер реплику не принял: после «результат
    // неизвестен» состояние перечитано, и реплика может быть уже в журнале.
    const users = (window.Lesson.state?.messages || []).filter((message) => message.role === "user");
    const delivered = Boolean(users.length && users[users.length - 1].html.includes(escapeHtml(clean)));
    if (!result && !input.value && !delivered) input.value = clean;
    input.focus();
  }

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
    if (!chip || locked()) return;
    if (chip.dataset.action === "task") {
      window.Lesson.act("task", {}, { echo: "Дай задание" });
      window.Lesson.selectTab("practice");
      window.Lesson.selectView("lesson");
    } else {
      send(chip.dataset.say);
    }
  });

  window.Lesson.chat = { append, replace, pending, typing, scrollToEnd };

  window.Lesson.onState((state) => {
    const off = Boolean(state.survey);
    input.disabled = off;
    form.querySelector("button").disabled = off;
    document.querySelectorAll("#chat-chips .chip").forEach((chip) => {
      chip.disabled = off;
    });
    input.placeholder = off ? SURVEY_TEXT : "Спросите тьютора… Enter — отправить, Shift+Enter — новая строка";
    placeholder();
  });
})();
