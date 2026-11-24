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
    ["В заметку", null, "note"],
    ["Спросить…", null],
    ["Голосом…", null, "voice"],
  ];
  const MAX_CHARS = 1500;
  // Копия core.context.FOCUS_MAX_LINES (сверяет tests/test_web_fragment.py):
  // спрашивают про кусок, а не про весь конспект (срез 84).
  const MAX_LINES = 20;
  // Разделители строк — как у str.splitlines() на сервере, пустая строка —
  // как у str.strip(): у JS trim() другой набор пробелов (аудит 84, M-1).
  const LINE_BREAK = /\r\n|[\n\v\f\r\x1c-\x1e\x85\u2028\u2029]/;
  const BLANK = /^[\t\n\v\f\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/;
  // Отступ от краёв окна и от выделения. На сенсорном экране под выделением
  // висят ручки выделения — зазор больше (аудит 78, M-4).
  const MARGIN = 8;
  const GAP = 8;
  const TOUCH_GAP = 28;
  // Где выделение поднимает панель: источник и вкладка, куда вернуть фокус.
  const SOURCES = [
    { source: "theory", selector: "#tab-theory .theory-body", panel: "#tab-theory" },
    { source: "task", selector: "#tab-practice .task-prompt", panel: "#tab-practice" },
    // Формулировка код-задания на вкладке «Код» — то же задание (аудит 78, M-6).
    { source: "task", selector: "#tab-code .code-prompt:not(.muted)", panel: "#tab-code" },
  ];

  const bar = document.createElement("div");
  bar.className = "selection-bar";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Спросить тьютора о выделенном");
  bar.setAttribute("aria-keyshortcuts", "Alt+F10");
  bar.hidden = true;
  const buttons = QUESTIONS.map(([label, question, action]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.tabIndex = -1;
    button.addEventListener("click", () => choose(question, action));
    // Браузер без распознавания речи: не показываем нерабочий пункт.
    if (action === "voice" && !window.Voice?.supported) button.hidden = true;
    return button;
  });
  // Кнопки — своим рядом, подсказка — под ним: панель не шире большего из
  // них (аудит 84, M-3).
  const actions = document.createElement("div");
  actions.className = "selection-actions";
  actions.append(...buttons);
  // Почему кнопки неактивны: выделено больше MAX_LINES строк. Alt+F10 ведёт
  // сюда, а не на неактивную кнопку.
  const hint = document.createElement("p");
  hint.className = "selection-hint";
  hint.tabIndex = -1;
  hint.hidden = true;
  bar.append(actions, hint);
  document.body.append(bar);
  // Диктору подсказку объявляет постоянная скрытая область: область, которая
  // появляется сразу с текстом, многие не озвучивают (аудит 84, Н-1).
  const status = document.createElement("p");
  status.id = "selection-status";
  status.className = "visually-hidden";
  status.setAttribute("aria-live", "polite");
  document.body.append(status);
  let statusTimer = null;

  // Что выделено сейчас: {source, text, lines, panel, restore, anchor, rect};
  // null — панели нет. `lines` — непустые строки всего выделения (до обрезки по
  // знакам). `anchor()` — где выделение сейчас, `rect` — где было при показе.
  let current = null;

  function locked() {
    return window.Lesson.busy || Boolean(window.Lesson.state?.survey);
  }

  function tooLong() {
    return Boolean(current && current.lines > MAX_LINES);
  }

  /** Текст подсказки и объявления; пишется, только если изменился: иначе
   * каждый ход (lesson:busy) повторял бы объявление (аудит 84, Н-1). */
  function say(text) {
    hint.hidden = !text;
    if (hint.textContent !== text) hint.textContent = text;
    clearTimeout(statusTimer);
    if (!text) {
      status.textContent = "";
    } else if (status.textContent !== text) {
      // Сначала очищаем, затем пишем — как у прикреплённого фрагмента (chat.js).
      status.textContent = "";
      statusTimer = setTimeout(() => {
        status.textContent = text;
      }, 100);
    }
  }

  function sync() {
    const over = tooLong();
    const off = locked() || over;
    say(over ? `Выделите не больше ${MAX_LINES} строк (сейчас ${current.lines}).` : "");
    // Кнопка с фокусом станет неактивной — фокус потерялся бы на body (I-3).
    // Фокус на подсказке оставляем: она и есть место, куда ведёт Alt+F10 (M-2).
    if (off && current && buttons.includes(document.activeElement)) current.restore();
    buttons.forEach((button) => {
      button.disabled = off;
    });
    // Roving tabindex: в панель с клавиатуры попадаем на первую кнопку.
    buttons.forEach((button, index) => {
      button.tabIndex = index === 0 ? 0 : -1;
    });
  }

  /** Непустые строки — так же считает сервер (core.context.focus_lines): пустая
   * строка между абзацами конспекта не в счёт. */
  function lineCount(text) {
    return text.split(LINE_BREAK).filter((line) => !BLANK.test(line)).length;
  }

  /** Не длиннее предела сервера; режем по кодовым точкам, не по UTF-16. */
  function clip(text) {
    const chars = Array.from(text);
    return chars.length > MAX_CHARS ? `${chars.slice(0, MAX_CHARS - 1).join("")}…` : text;
  }

  function place(rect) {
    // Ширина fixed-панели зависит от места: на прежнем, у правого края, она
    // сжалась бы в столбик. Меряем от левого края окна.
    bar.style.left = "0px";
    const width = bar.offsetWidth;
    const height = bar.offsetHeight;
    // На сенсорном экране сверху встаёт системное меню копирования — панель
    // под выделением, но если внизу места нет, всё же над ним: иначе прижим к
    // краю окна положил бы её поверх выделения (аудит 78, M-4).
    const touch = window.matchMedia("(pointer: coarse)").matches;
    const gap = touch ? TOUCH_GAP : GAP;
    const above = rect.top - height - gap;
    const under = rect.bottom + gap;
    const fitsAbove = above >= MARGIN;
    const fitsUnder = under + height <= window.innerHeight - MARGIN;
    let top = touch ? (fitsUnder || !fitsAbove ? under : above) : fitsAbove || !fitsUnder ? above : under;
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
    // Фокус в панели, а она прячется — вернуть его в источник, а не на body (I-3).
    const focused = bar.contains(document.activeElement);
    bar.hidden = true;
    say("");
    if (restore || focused) selected.restore();
  }

  function choose(question, action) {
    if (!current || locked() || tooLong()) return;
    const focus = { source: current.source, text: current.text };
    const fromKeyboard = bar.contains(document.activeElement);
    hide();
    // Узкий экран: сначала «Чат», иначе фокус уйдёт в скрытое поле ввода.
    const viewSwitch = document.querySelector(".view-switch");
    const toChat = Boolean(viewSwitch && getComputedStyle(viewSwitch).display !== "none");
    if (toChat) window.Lesson.selectView("chat");
    if (action === "voice") {
      // Фрагмент прикрепляется к полю, дальше — тот же микрофон, что в чате:
      // распознанный вопрос можно править перед отправкой.
      window.Lesson.chat.attach(focus);
      const mic = document.getElementById("chat-mic");
      if (mic && !mic.hidden && !mic.disabled) mic.click();
      else document.getElementById("chat-input")?.focus();
      return;
    }
    if (action === "note") {
      const course = document.getElementById("lesson")?.dataset.course;
      if (!course) return;
      const lesson = window.Lesson.state?.node?.lesson;
      const sectionId =
        lesson?.sections?.length && lesson.section_index
          ? lesson.sections[lesson.section_index - 1]?.id || null
          : null;
      window.App.request(`/api/courses/${encodeURIComponent(course)}/notes`, {
        method: "POST",
        body: {
          quote: focus.text,
          source: focus.source,
          node_id: window.Lesson.state?.node?.id || null,
          section_id: sectionId,
        },
      })
        .then(() => window.App.notify("Заметка сохранена."))
        .catch((problem) => window.App.notify(problem.message, { error: true }));
      return;
    }
    if (question) window.Lesson.chat.ask(question, focus);
    else window.Lesson.chat.attach(focus);
    // С клавиатуры на телефоне ответ — в «Чате»: туда и фокус (I-3).
    if (question && fromKeyboard && toChat) document.getElementById("chat-input")?.focus();
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

  /** Выделение целиком в источнике — или с пустым «хвостом» за его границей.
   *
   * Тройной клик и протяжка чуть ниже текста ставят конец выделения в
   * следующий блок (вариант ответа, кнопку «К практике»): такой хвост без
   * текста обрезаем по границе источника (аудит 78, I-2). */
  function insideSource(range) {
    const start = closestSource(range.startContainer);
    if (!start) return null;
    const end = closestSource(range.endContainer);
    if (end && end.root === start.root) return { start, range };
    const tail = document.createRange();
    tail.setStart(start.root, start.root.childNodes.length);
    try {
      tail.setEnd(range.endContainer, range.endOffset);
    } catch {
      return null;
    }
    if (tail.toString().trim()) return null;
    const clipped = range.cloneRange();
    clipped.setEnd(start.root, start.root.childNodes.length);
    return { start, range: clipped };
  }

  /** Текст выделения без служебных кнопок блоков кода («Скопировать»,
   * «Открыть в редакторе»): иначе их подписи уходили модели (аудит 78, M-1). */
  function rangeText(range) {
    const box = document.createElement("div");
    box.className = "selection-measure";
    // Выделение внутри одного <pre>: клон не несёт самого <pre>, и innerText
    // схлопнул бы переносы и отступы кода (финальное ревью, I-1).
    const common = range.commonAncestorContainer;
    const inPre = (common.nodeType === Node.ELEMENT_NODE ? common : common.parentElement)?.closest("pre");
    if (inPre) {
      const pre = document.createElement("pre");
      pre.append(range.cloneContents());
      box.append(pre);
    } else {
      box.append(range.cloneContents());
    }
    box.querySelectorAll(".code-tools").forEach((node) => node.remove());
    document.body.append(box);
    // innerText, а не textContent: переносы между абзацами и строки <pre>.
    const text = box.innerText;
    box.remove();
    return text;
  }

  function fromDocument() {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return hide();
    const found = insideSource(selection.getRangeAt(0));
    if (!found) return hide();
    const { start, range } = found;
    const text = rangeText(range).trim();
    if (!text) return hide();
    const panel = document.querySelector(start.entry.panel);
    show({
      source: start.entry.source,
      text: clip(text),
      lines: lineCount(text),
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
  const codePanel = document.getElementById("tab-code");
  function fromEditor() {
    if (!editor) return;
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
      lines: lineCount(text),
      panel: codePanel,
      restore: () => editor.focus(),
      anchor: () => {
        const at = editor.cursorCoords(start, "window");
        return { top: at.top, bottom: at.bottom, left: at.left, width: 0 };
      },
    });
  }
  if (editor) {
    editor.on("cursorActivity", fromEditor);
    editor.on("blur", () => {
      // Фокус ушёл в панель (Alt+F10) — панель нужна; ушёл в другое место — нет.
      setTimeout(() => {
        if (current?.source === "code" && !bar.contains(document.activeElement)) hide();
      }, 0);
    });
  }

  /* --- Клавиатура --- */

  function focusBar() {
    if (tooLong()) {
      hint.focus();
      return true;
    }
    const first = buttons.find((button) => !button.disabled);
    if (!first) return false;
    first.focus();
    return true;
  }

  /** Блок источника, где стоит курсор (выделение); без курсора — первый блок.
   * Блок — ребёнок корня источника: абзац, список, код, таблица. */
  function blockAt(root, selection) {
    const node = selection.rangeCount ? selection.getRangeAt(0).startContainer : null;
    let block = node && root.contains(node) && node !== root ? node : null;
    while (block && block.parentNode !== root) block = block.parentNode;
    // Пункт списка — меньше всего списка: список длиннее 20 строк не спросить.
    const item = node?.parentElement?.closest("li");
    if (block && item && block.contains(item)) return item;
    return block ?? root.firstElementChild ?? root;
  }

  function selected(selection, node) {
    if (selection.rangeCount !== 1) return false;
    const range = document.createRange();
    range.selectNodeContents(node);
    const now = selection.getRangeAt(0);
    return (
      now.compareBoundaryPoints(Range.START_TO_START, range) === 0 &&
      now.compareBoundaryPoints(Range.END_TO_END, range) === 0
    );
  }

  /** Ctrl+A в «Теории» или «Практике» выделяет блок, где курсор, а повторный —
   * весь источник, но не всю страницу: без caret browsing это единственный
   * способ выделить текст с клавиатуры (аудит 78, M-2). Сразу весь конспект
   * длиннее MAX_LINES — спросить о нём нельзя (аудит 84, I-1). */
  function selectSource(active) {
    for (const entry of SOURCES) {
      const panel = document.querySelector(entry.panel);
      const root = panel?.querySelector(entry.selector.replace(`${entry.panel} `, ""));
      if (!panel || !root || entry.source === "code" || !panel.contains(active)) continue;
      if (active.closest(".CodeMirror, input, textarea, [contenteditable]")) return false;
      const selection = document.getSelection();
      const block = blockAt(root, selection);
      const range = document.createRange();
      range.selectNodeContents(selected(selection, block) ? root : block);
      selection.removeAllRanges();
      selection.addRange(range);
      return true;
    }
    return false;
  }

  document.addEventListener(
    "keydown",
    (event) => {
      const active = document.activeElement;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "a") {
        if (selectSource(active)) {
          event.preventDefault();
          return;
        }
      }
      if (!current && event.key === "F10" && event.altKey) {
        // Esc спрятал панель, выделение осталось — Alt+F10 вернёт её (M-3).
        if (active?.closest?.(".CodeMirror")) fromEditor();
        else fromDocument();
      }
      if (!current) return;
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
      if (toBar && focusBar()) {
        event.preventDefault();
        // Иначе событие дойдёт до CodeMirror, и тот в конце своей операции
        // вернёт фокус в редактор (аудит 78, I-1).
        event.stopPropagation();
      }
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
