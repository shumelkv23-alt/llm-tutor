/* Вкладка «Код»: редактор (CodeMirror 5) и запуск Python в браузере (Pyodide).
 *
 * «Запустить» — подготовка данных задания + код, вывод в консоль.
 * «Проверить» — то же и проверки задания; итог уходит на сервер, вердикт
 * записывает ядро. «Показать тьютору» — код уходит в чат вопросом.
 * Python грузится лениво — при первом запуске; зависший код останавливается
 * через 10 секунд уничтожением воркера.
 */
(() => {
  const { $, icon, notify } = window.App;
  const panel = $("#tab-code");
  const lesson = $("#lesson");
  const indexURL = lesson.dataset.pyodide;
  const TIMEOUT_MS = 10000;
  const DRAFT_KEY = `llmTutor.code.${window.Lesson.course}`;
  const SANDBOX_TEXT = "import pandas as pd\n\ndf = pd.DataFrame({\"city\": [\"Казань\", \"Москва\", \"Казань\"], \"age\": [31, 45, 27]})\nprint(df.groupby(\"city\")[\"age\"].mean())\n";

  let editor = null;
  let worker = null;
  let runId = 0;
  let running = null;
  let item = null;          // задание с кодом, которое ждёт ответа (или null — песочница)
  let fatal = null;

  const el = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  /* --- Черновики: по заданию и для песочницы --- */

  function draftKey() {
    return `${DRAFT_KEY}.${item ? item.id : "sandbox"}`;
  }

  function loadDraft() {
    try {
      return localStorage.getItem(draftKey());
    } catch {
      return null;
    }
  }

  function saveDraft() {
    try {
      localStorage.setItem(draftKey(), editor.getValue());
    } catch {
      /* без хранилища черновик живёт до перезагрузки */
    }
  }

  /* --- Разметка вкладки --- */

  const head = el("div", "code-head");
  const title = el("div", "code-title");
  const actions = el("div", "code-actions");
  const host = el("div", "code-editor");
  const status = el("p", "code-status");
  status.setAttribute("role", "status");
  const consoleBox = el("pre", "code-console");
  consoleBox.setAttribute("aria-label", "Вывод программы");
  consoleBox.setAttribute("aria-live", "polite");

  function button(label, iconName, className, onClick, hint) {
    const element = el("button", `btn ${className}`.trim());
    element.type = "button";
    element.append(icon(iconName), label);
    if (hint) element.title = hint;
    element.addEventListener("click", onClick);
    return element;
  }

  const runButton = button("Запустить", "play", "", () => run(false), "Ctrl+Enter");
  const checkButton = button("Проверить", "check", "primary", () => run(true), "Ctrl+Shift+Enter");
  checkButton.dataset.turn = "";
  const tutorButton = button("Показать тьютору", "send", "ghost", showTutor);
  const resetButton = button("К заготовке", "refresh", "ghost", reset);

  function mount() {
    panel.replaceChildren();
    actions.append(runButton, checkButton, tutorButton, resetButton);
    head.append(title, actions);
    panel.append(head, host, status, consoleBox);
    editor = window.CodeMirror(host, {
      value: "",
      mode: "python",
      lineNumbers: true,
      indentUnit: 4,
      tabSize: 4,
      indentWithTabs: false,
      matchBrackets: true,
      autoCloseBrackets: true,
      viewportMargin: Infinity,
      extraKeys: {
        "Ctrl-Enter": () => run(false),
        "Cmd-Enter": () => run(false),
        "Shift-Ctrl-Enter": () => run(true),
        "Shift-Cmd-Enter": () => run(true),
        Tab: (cm) => cm.execCommand(cm.somethingSelected() ? "indentMore" : "insertSoftTab"),
      },
    });
    editor.getWrapperElement().setAttribute("aria-label", "Редактор кода на Python");
    editor.getInputField().setAttribute("aria-label", "Код на Python");
    editor.on("change", saveDraft);
  }

  function renderHead() {
    title.replaceChildren();
    if (item) {
      title.append(el("span", "badge accent", "Задание"));
      const prompt = el("div", "code-prompt");
      // Готовый HTML: задание прошло web.markdown.render на сервере.
      prompt.innerHTML = item.prompt_html;
      title.append(prompt);
    } else {
      title.append(el("span", "badge", "Песочница"));
      title.append(el("p", "code-prompt muted", "Пробуйте свой код. Задание с кодом откроется здесь само."));
    }
    checkButton.hidden = !item;
    resetButton.hidden = !item;
  }

  function setText(text) {
    editor.setValue(text);
    editor.clearHistory();
    editor.refresh();
  }

  function sync(state) {
    const next = state.item && state.item.runnable ? state.item : null;
    if ((next && next.id) === (item && item.id)) return;
    item = next;
    renderHead();
    setText(loadDraft() ?? (item ? item.starter : SANDBOX_TEXT));
    consoleBox.textContent = "";
    status.textContent = fatal || "";
  }

  /* --- Запуск --- */

  function ensureWorker() {
    if (!worker) {
      worker = new Worker(lesson.dataset.worker);
      worker.onmessage = onMessage;
      worker.onerror = () => finish({ ok: false, error: "Воркер Python упал. Попробуйте ещё раз." });
    }
    return worker;
  }

  function setRunning(value) {
    runButton.disabled = value;
    checkButton.disabled = value;
    lesson.classList.toggle("code-running", value);
  }

  function run(check) {
    if (running || fatal) {
      if (fatal) notify(fatal, { error: true });
      return;
    }
    if (check && !item) return;
    runId += 1;
    running = { id: runId, check, code: editor.getValue(), timer: null };
    setRunning(true);
    consoleBox.textContent = "";
    consoleBox.classList.remove("failed", "passed");
    status.textContent = "Запускаем…";
    ensureWorker().postMessage({
      type: "run",
      id: runId,
      indexURL,
      setup: item ? item.setup : "",
      code: running.code,
      tests: check ? item.tests : "",
    });
  }

  function onMessage(event) {
    const message = event.data;
    if (message.type === "status") {
      status.textContent = message.text;
    } else if (message.type === "fatal") {
      fatal = `${message.text}. Без Python код можно отправить тьютору.`;
      finish({ ok: false, error: fatal });
      worker.terminate();
      worker = null;
    } else if (!running || message.id !== running.id) {
      return;
    } else if (message.type === "started") {
      status.textContent = "Выполняется…";
      running.timer = setTimeout(timeout, TIMEOUT_MS);
    } else if (message.type === "result") {
      finish(message);
    }
  }

  function timeout() {
    // Зависший код не остановить изнутри — только уничтожить воркер.
    worker?.terminate();
    worker = null;
    finish({
      ok: false,
      stage: "code",
      error: `Код выполнялся дольше ${TIMEOUT_MS / 1000} секунд и был остановлен. Нет ли бесконечного цикла?`,
    });
  }

  const STAGE_LABELS = {
    setup: "Не удалось подготовить данные задания",
    code: "Ошибка в коде",
    tests: "Проверка не пройдена",
    packages: "Не удалось загрузить библиотеки",
  };

  function finish(result) {
    if (!running) return;
    const { check, code } = running;
    clearTimeout(running.timer);
    running = null;
    setRunning(false);
    const lines = [];
    if (result.stdout) lines.push(result.stdout.trimEnd());
    if (!result.ok) lines.push(`${STAGE_LABELS[result.stage] || "Ошибка"}: ${result.error}`);
    else if (check) lines.push("✓ Все проверки пройдены.");
    consoleBox.textContent = lines.join("\n\n") || "(вывода нет)";
    consoleBox.classList.toggle("failed", !result.ok);
    consoleBox.classList.toggle("passed", Boolean(result.ok && check));
    status.textContent = result.ok ? "Готово." : "";
    // Ошибка самого кода — тоже результат проверки: ученик отвечал, ответ неверен.
    if (check && item && result.stage !== "packages" && !fatal) submit(code, result);
  }

  async function submit(code, result) {
    const passed = Boolean(result.ok && result.passed);
    const report = passed ? "" : `${STAGE_LABELS[result.stage] || "Ошибка"}: ${result.error}`;
    const data = await window.Lesson.act(
      "code-result",
      { item_id: item.id, code, passed, report: report.slice(0, 4000) },
      { echo: passed ? "Проверил код — все проверки пройдены." : "Проверил код — есть ошибки." }
    );
    if (data?.verdict) {
      try {
        localStorage.removeItem(`${DRAFT_KEY}.${data.verdict.item_id}`);
      } catch {
        /* нечего чистить */
      }
    }
  }

  function showTutor() {
    const code = editor.getValue().trim();
    if (!code || window.Lesson.busy || window.Lesson.state?.survey) return;
    const output = consoleBox.textContent.trim();
    const text = output
      ? `Посмотри мой код:\n\n\`\`\`python\n${code}\n\`\`\`\n\nВывод:\n\n\`\`\`\n${output.slice(0, 1500)}\n\`\`\``
      : `Посмотри мой код:\n\n\`\`\`python\n${code}\n\`\`\``;
    window.Lesson.act("chat", { text: text.slice(0, 8000) }, { echo: "Посмотри мой код" });
    window.Lesson.selectView("chat");
  }

  async function reset() {
    if (!item) return;
    const ok = await window.App.confirm("Вернуть заготовку?", "Ваш текущий код в редакторе будет заменён.", { label: "Вернуть" });
    if (ok) setText(item.starter);
  }

  /** Открыть код в редакторе (из теории или практики). */
  function open(code) {
    window.Lesson.selectTab("code");
    if (code !== undefined) setText(code);
    editor.focus();
  }

  if (!window.CodeMirror) {
    panel.replaceChildren(el("div", "empty", "Редактор не загрузился. Обновите страницу."));
    return;
  }
  mount();
  renderHead();
  setText(loadDraft() ?? SANDBOX_TEXT);
  window.Lesson.code = { open };
  window.Lesson.onState((state) => {
    if (!state.survey) sync(state);
  });
  // CodeMirror в скрытой вкладке не знает своих размеров — пересчитываем при показе.
  document.addEventListener("lesson:tab", (event) => {
    if (event.detail === "code") setTimeout(() => editor.refresh(), 0);
  });
})();
