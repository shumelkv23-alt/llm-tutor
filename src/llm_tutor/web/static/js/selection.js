/* Панель вопросов над выделенным фрагментом (срез 78).
 *
 * Выделение в конспекте («Теория»), в формулировке задания («Практика») или
 * в редакторе («Код») поднимает над собой панель: готовые вопросы сразу
 * уходят тьютору с фрагментом, «Спросить…» прикрепляет фрагмент к полю чата.
 * Фрагмент — текст выделения; в DOM он не вставляется (плашки рисует chat.js).
 */
(() => {
  // Копия core.context.FOCUS_QUESTIONS (сверяет tests/test_web_fragment.py):
  // ни один вопрос не должен ловиться как намерение «не понял».
  const QUESTIONS = [
    ["Объясни", "Объясни этот фрагмент подробнее."],
    ["Проще", "Объясни этот фрагмент проще."],
    ["Пример", "Приведи пример к этому фрагменту."],
    ["Спросить…", null],
  ];
  const MAX_CHARS = 1500;
  // Отступ от краёв окна и от выделения.
  const MARGIN = 8;
  const GAP = 8;
  // Где выделение поднимает панель: источник и вкладка, куда вернуть фокус.
  const SOURCES = [
    { source: "theory", selector: "#tab-theory .theory-body", panel: "#tab-theory" },
    { source: "task", selector: "#tab-practice .task-prompt", panel: "#tab-practice" },
  ];

  const bar = document.createElement("div");
  bar.className = "selection-bar";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Спросить тьютора о выделенном");
  bar.setAttribute("aria-keyshortcuts", "Alt+F10");
  bar.hidden = true;
  const buttons = QUESTIONS.map(([label, question]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.tabIndex = -1;
    button.addEventListener("click", () => choose(question));
    return button;
  });
  bar.append(...buttons);
  document.body.append(bar);

  // Что выделено сейчас: {source, text, panel, restore, anchor, rect}; null —
  // панели нет. `anchor()` — где выделение сейчас, `rect` — где было при показе.
  let current = null;

  function locked() {
    return window.Lesson.busy || Boolean(window.Lesson.state?.survey);
  }

  function sync() {
    const off = locked();
    buttons.forEach((button) => {
      button.disabled = off;
    });
    // Roving tabindex: в панель с клавиатуры попадаем на первую кнопку.
    buttons.forEach((button, index) => {
      button.tabIndex = index === 0 ? 0 : -1;
    });
  }

  /** Не длиннее предела сервера; режем по кодовым точкам, не по UTF-16. */
  function clip(text) {
    const chars = Array.from(text);
    return chars.length > MAX_CHARS ? `${chars.slice(0, MAX_CHARS - 1).join("")}…` : text;
  }

  function place(rect) {
    const width = bar.offsetWidth;
    const height = bar.offsetHeight;
    // На сенсорном экране сверху встаёт системное меню копирования.
    const below = window.matchMedia("(pointer: coarse)").matches;
    let top = rect.top - height - GAP;
    if (below || top < MARGIN) top = rect.bottom + GAP;
    top = Math.min(Math.max(top, MARGIN), window.innerHeight - height - MARGIN);
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.min(Math.max(left, MARGIN), window.innerWidth - width - MARGIN);
    bar.style.left = `${Math.round(left)}px`;
    bar.style.top = `${Math.round(top)}px`;
  }

  function show(selected) {
    const rect = selected.anchor();
    current = { ...selected, rect };
    // Сразу за источником в порядке Tab: из панели Tab идёт дальше по странице.
    if (selected.panel.nextElementSibling !== bar) selected.panel.after(bar);
    sync();
    bar.hidden = false;
    place(rect);
  }

  function hide({ restore = false } = {}) {
    if (!current) return;
    const selected = current;
    current = null;
    bar.hidden = true;
    if (restore) selected.restore();
  }

  function choose(question) {
    if (!current || locked()) return;
    const focus = { source: current.source, text: current.text };
    hide();
    // Узкий экран: сначала «Чат», иначе фокус уйдёт в скрытое поле ввода.
    const viewSwitch = document.querySelector(".view-switch");
    if (viewSwitch && getComputedStyle(viewSwitch).display !== "none") {
      window.Lesson.selectView("chat");
    }
    if (question) window.Lesson.chat.ask(question, focus);
    else window.Lesson.chat.attach(focus);
  }

  /* --- Выделение в тексте страницы --- */

  function closestSource(node) {
    const element = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    if (!element) return null;
    for (const entry of SOURCES) {
      const root = element.closest(entry.selector);
      if (root) return { entry, root };
    }
    return null;
  }

  function fromDocument() {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return hide();
    const range = selection.getRangeAt(0);
    const start = closestSource(range.startContainer);
    const end = closestSource(range.endContainer);
    // Только выделение целиком внутри одного источника.
    if (!start || !end || start.root !== end.root) return hide();
    const text = selection.toString().trim();
    if (!text) return hide();
    const panel = document.querySelector(start.entry.panel);
    show({
      source: start.entry.source,
      text: clip(text),
      panel,
      restore: () => panel.focus(),
      anchor: () => range.getBoundingClientRect(),
    });
  }

  document.addEventListener("selectionchange", () => {
    const active = document.activeElement;
    // Фокус в панели или в редакторе: у редактора своё выделение (ниже).
    if (bar.contains(active) || active?.closest?.(".CodeMirror")) return;
    fromDocument();
  });

  /* --- Выделение в редакторе (CodeMirror) --- */

  const editor = window.Lesson.code?.editor;
  if (editor) {
    const codePanel = document.getElementById("tab-code");
    editor.on("cursorActivity", () => {
      const text = editor.somethingSelected() ? editor.getSelection() : "";
      if (!text.trim()) {
        if (current?.source === "code") hide();
        return;
      }
      const start = editor.getCursor("from");
      // Код не обрезаем по краям: отступы строк выравнивает сервер.
      show({
        source: "code",
        text: clip(text),
        panel: codePanel,
        restore: () => editor.focus(),
        anchor: () => {
          const at = editor.cursorCoords(start, "window");
          return { top: at.top, bottom: at.bottom, left: at.left, width: 0 };
        },
      });
    });
    editor.on("blur", () => {
      // Фокус ушёл в панель (Alt+F10) — панель нужна; ушёл в другое место — нет.
      setTimeout(() => {
        if (current?.source === "code" && !bar.contains(document.activeElement)) hide();
      }, 0);
    });
  }

  /* --- Клавиатура --- */

  function focusBar() {
    const first = buttons.find((button) => !button.disabled);
    if (!first) return false;
    first.focus();
    return true;
  }

  document.addEventListener(
    "keydown",
    (event) => {
      if (!current) return;
      const active = document.activeElement;
      const inBar = bar.contains(active);
      if (event.key === "Escape") {
        if (inBar) event.preventDefault();
        hide({ restore: inBar });
        return;
      }
      if (inBar) return;
      // Tab в редакторе — отступ, поэтому к панели из кода ведёт Alt+F10.
      const toBar =
        (event.key === "F10" && event.altKey) ||
        (event.key === "Tab" &&
          !event.shiftKey &&
          current.source !== "code" &&
          current.panel.contains(active));
      if (toBar && focusBar()) event.preventDefault();
    },
    true
  );

  bar.addEventListener("keydown", (event) => {
    const enabled = buttons.filter((button) => !button.disabled);
    const index = enabled.indexOf(document.activeElement);
    const moves = { ArrowRight: 1, ArrowLeft: -1 };
    if (index < 0) return;
    let next = null;
    if (event.key in moves) next = enabled[(index + moves[event.key] + enabled.length) % enabled.length];
    if (event.key === "Home") next = enabled[0];
    if (event.key === "End") next = enabled[enabled.length - 1];
    if (!next) return;
    event.preventDefault();
    next.focus();
  });

  // Нажатие мышью не снимает выделение и не уводит фокус из источника.
  bar.addEventListener("mousedown", (event) => event.preventDefault());

  /* --- Когда панель прячется сама --- */

  // Прокрутка уводит выделение — панель прячется. Только если оно правда
  // сдвинулось: чат прокручивается сам (новые реплики), а редактор шлёт scroll
  // и тогда, когда видимое место не изменилось.
  document.addEventListener(
    "scroll",
    () => {
      if (!current) return;
      const now = current.anchor();
      if (Math.abs(now.top - current.rect.top) > 2 || Math.abs(now.left - current.rect.left) > 2) {
        hide();
      }
    },
    true
  );
  window.addEventListener("resize", () => hide());
  document.addEventListener("lesson:tab", () => hide());
  document.addEventListener("lesson:busy", () => {
    if (current) sync();
  });
  // Ход перерисовал теорию или задание: выделение могло исчезнуть вместе с ними.
  window.Lesson.onState(() => {
    if (current && current.source !== "code") requestAnimationFrame(fromDocument);
    else if (current) sync();
  });
})();
