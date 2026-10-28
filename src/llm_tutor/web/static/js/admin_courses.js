/* Раздел админки «Курсы»: список курсов, обзор курса и редактор модуля.
 *
 * Страница отдаёт контейнер с `data-screen`, остальное собирает этот файл:
 * модуль правится целиком и уходит на сервер тем же объектом, что пришёл
 * (незнакомые формы поля он не теряет). Текст попадает в DOM только через
 * textContent — встроенный HTML запрещён CSP; проценты — через CSSOM
 * (`element.style.width`), а не атрибутом style.
 */
(() => {
  const { request, notify, icon } = window.App;

  const root = document.querySelector("[data-screen]");
  if (!root) return;

  const POLL_MS = 2000; // как часто спрашиваем прогресс генерации
  const TERMINAL = ["done", "failed", "cancelled"];
  // Структуру составляет один вызов модели — ответа ждём в самом запросе.
  const STRUCTURE_TIMEOUT = 180000;
  const RUN_LABELS = {
    running: "Идёт генерация",
    done: "Готово",
    failed: "Не получилось",
    cancelled: "Отменено",
  };
  const SOURCE_KINDS = { file: "файл", url: "страница", github: "репозиторий" };
  const MAX_SOURCE_BYTES = 512 * 1024; // больше сервер не примет
  const MAX_SECTIONS = 4; // правил занятий: 2–4 раздела
  const ANSWER_TYPES = { choice: "Выбор варианта", short: "Короткий ответ", code: "Код", open: "Открытый ответ", spoken: "Голосовой ответ" };
  // Имя поля приходит от сервера — в селектор пускаем только безопасные символы.
  const UNSAFE = /[^a-zA-Z0-9_-]/g;

  /* --- сборка разметки --- */

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function attrs(node, spec) {
    for (const [key, value] of Object.entries(spec)) {
      if (value === undefined || value === null) continue;
      if (key === "field") node.dataset.field = value;
      else if (key === "label") node.setAttribute("aria-label", value);
      else node.setAttribute(key, String(value));
    }
    return node;
  }

  function field(labelText, control, className = "") {
    const label = make("label", className);
    label.append(document.createTextNode(labelText), control);
    return label;
  }

  function inputNode(kind, value, spec = {}) {
    const { className = "", ...rest } = spec;
    const node = make("input", className);
    node.type = kind;
    node.value = value === undefined || value === null ? "" : String(value);
    return attrs(node, rest);
  }

  function areaNode(value, spec = {}) {
    const { className = "", rows = 4, ...rest } = spec;
    const node = make("textarea", className);
    node.value = value === undefined || value === null ? "" : String(value);
    node.setAttribute("rows", String(rows));
    return attrs(node, rest);
  }

  function selectNode(entries, value, spec = {}) {
    const node = make("select");
    entries.forEach(([optionValue, label]) => node.append(new Option(label, optionValue)));
    node.value = value === undefined || value === null ? "" : String(value);
    return attrs(node, spec);
  }

  function buttonNode(label, className, action, data = {}) {
    const node = make("button", `btn ${className}`.trim(), label);
    node.type = "button";
    node.dataset.action = action;
    for (const [key, value] of Object.entries(data)) node.dataset[key] = String(value);
    return node;
  }

  /* --- общее для экранов --- */

  function clearErrors(form) {
    form.querySelectorAll(".field-error, .form-error").forEach((node) => {
      node.textContent = "";
      node.hidden = true;
    });
  }

  /** Ошибка формы: текст — у поля (если сервер его назвал), и всегда — в тосте. */
  function showProblem(form, problem) {
    const name = problem.field ? String(problem.field).replace(UNSAFE, "") : "";
    const box = name ? form.querySelector(`[data-error-for="${name}"]`) : null;
    if (box) {
      box.textContent = problem.message;
      box.hidden = false;
    }
    notify(problem.message, { error: true });
  }

  /** Действие над кнопкой: кнопка заблокирована, ошибка — в тосте. */
  async function guard(node, work) {
    node.disabled = true;
    try {
      await work();
    } catch (problem) {
      notify(problem.message, { error: true });
    } finally {
      node.disabled = false;
    }
  }

  function onActions(scope, actions) {
    scope.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button || !scope.contains(button)) return;
      const handler = actions[button.dataset.action];
      if (!handler) return;
      event.preventDefault();
      Promise.resolve(handler(button)).catch((problem) => notify(problem.message, { error: true }));
    });
  }

  function sizeOf(bytes) {
    return `${Math.round((Number(bytes || 0) / 1024) * 10) / 10} КБ`;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** Список строк в готовом `.notice`: пусто — блок прячется целиком. */
  function showList(box, title, items) {
    if (!box) return;
    box.replaceChildren();
    if (!items.length) {
      box.hidden = true;
      return;
    }
    box.append(make("strong", "", title));
    const list = make("ul", "warn-list");
    items.forEach((entry) => list.append(make("li", "", entry)));
    box.append(list);
    box.hidden = false;
  }

  /* --- список курсов --- */

  function listScreen(node) {
    const form = node.querySelector("#create-course");
    const endpoint = node.dataset.create;
    // Встроенный курс описан кодом — правится только его копия. Копия
    // заводится черновиком, поэтому ученики её не увидят до публикации.
    onActions(node, {
      copy: (button) =>
        guard(button, async () => {
          const { course } = await request(button.dataset.copy, { method: "POST" });
          notify(`Копия «${course.title}» создана — правьте её, оригинал не меняется.`);
          location.assign(`/admin/courses/${encodeURIComponent(course.id)}`);
        }),
    });
    if (!form) return;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      clearErrors(form);
      const data = new FormData(form);
      const body = {
        title: String(data.get("title") || "").trim(),
        course_id: String(data.get("course_id") || "").trim(),
        subtitle: String(data.get("subtitle") || "").trim(),
        description: String(data.get("description") || "").trim(),
        source_url: String(data.get("source_url") || "").trim(),
      };
      const submit = form.querySelector('button[type="submit"]');
      guard(submit, async () => {
        try {
          const { course } = await request(endpoint, { method: "POST", body });
          notify(`Курс «${course.title}» создан.`);
          location.assign(`/admin/courses/${encodeURIComponent(course.id)}`);
        } catch (problem) {
          showProblem(form, problem);
        }
      });
    });
  }

  /* --- обзор курса --- */

  function courseScreen(node) {
    const api = node.dataset.api;
    const form = node.querySelector("#course-meta");
    const errorBox = node.querySelector("#course-error");
    const list = node.querySelector("#source-list");
    const picker = node.querySelector("#source-file");
    const urlInput = node.querySelector("#source-url");
    const githubInput = node.querySelector("#source-github");
    const genButton = node.querySelector('[data-action="generate"]');
    // «Перегенерировать» — та же генерация, но с data-force: показываем и прячем вместе.
    const regenButton = node.querySelector('[data-action="regenerate"]');
    const batchButton = node.querySelector('[data-action="generate-missing"]');
    const cancelButton = node.querySelector('[data-action="cancel-run"]');
    // Структура курса: форма есть всегда, строки — из неё же (шаблон или API).
    const structureForm = node.querySelector("#course-structure");
    const structureList = node.querySelector("#structure-list");
    const structureNone = node.querySelector("#structure-none");
    const hintsInput = node.querySelector("#struct-hints");
    const makeButton = node.querySelector('[data-action="make-structure"]');
    const runBox = node.querySelector("#gen-run");
    const stepCell = node.querySelector("#gen-step");
    const countCell = node.querySelector("#gen-count");
    const fill = node.querySelector("#gen-fill");
    const runError = node.querySelector("#gen-error");
    const runWarnings = node.querySelector("#gen-warnings");
    let watching = false;

    function fail(problem) {
      errorBox.textContent = problem.message;
      errorBox.hidden = false;
      notify(problem.message, { error: true });
    }

    /* метаданные */

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      clearErrors(form);
      const data = new FormData(form);
      const body = {
        title: String(data.get("title") || "").trim(),
        subtitle: String(data.get("subtitle") || "").trim(),
        description: String(data.get("description") || "").trim(),
        source_url: String(data.get("source_url") || "").trim(),
      };
      guard(form.querySelector('button[type="submit"]'), async () => {
        try {
          await request(api, { method: "PUT", body });
          const heading = document.querySelector(".page-heading h1");
          if (heading) heading.textContent = body.title;
          errorBox.hidden = true;
          notify("Курс сохранён.");
        } catch (problem) {
          showProblem(form, problem);
        }
      });
    });

    async function publish(button) {
      await guard(button, async () => {
        try {
          await request(`${api}/publish`, { method: "POST" });
          location.reload();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function unpublish(button) {
      const confirmed = await window.App.confirm(
        "Снять с публикации?",
        "Ученики потеряют доступ к курсу. Их запись и прогресс останутся и вернутся при повторной публикации.",
        { label: "Снять", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          await request(`${api}/unpublish`, { method: "POST" });
          location.reload();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function remove(button) {
      const confirmed = await window.App.confirm(
        "Удалить курс?",
        "Курс, материалы, модули и конспекты удалятся безвозвратно. Опубликованный курс сначала снимите с публикации.",
        { label: "Удалить курс", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          await request(api, { method: "DELETE" });
          location.assign("/admin/courses");
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /* материалы */

    function sourceRow(source) {
      const row = make("li", "source-row");
      row.dataset.source = String(source.id);
      const main = make("div", "source-main");
      main.append(make("span", "source-title", source.title));
      main.append(make("span", "badge", SOURCE_KINDS[source.kind] || source.kind));
      main.append(make("span", "source-meta muted", `${sizeOf(source.bytes)} · ${source.origin}`));
      row.append(main, buttonNode("Убрать", "ghost", "remove-source", { source: source.id }));
      return row;
    }

    function renderSources(sources) {
      list.replaceChildren();
      if (!sources.length) {
        list.append(make("li", "muted", "Материалов пока нет."));
        return;
      }
      sources.forEach((source) => list.append(sourceRow(source)));
    }

    async function refreshSources() {
      const data = await request(`${api}/sources`);
      renderSources(data.sources || []);
    }

    async function addFiles() {
      const files = [...picker.files];
      if (!files.length) {
        notify("Выберите хотя бы один файл.", { error: true });
        return;
      }
      await guard(picker, async () => {
        let added = 0;
        for (const file of files) {
          if (file.size > MAX_SOURCE_BYTES) {
            notify(`${file.name}: больше 512 КБ — такой файл не примут.`, { error: true });
            continue;
          }
          try {
            const text = await file.text();
            await request(`${api}/sources/file`, { method: "POST", body: { name: file.name, text } });
            added += 1;
          } catch (problem) {
            notify(`${file.name}: ${problem.message}`, { error: true });
          }
        }
        picker.value = "";
        if (!added) return;
        try {
          await refreshSources();
        } catch (problem) {
          fail(problem);
          return;
        }
        notify(added === 1 ? "Материал добавлен." : `Добавлено материалов: ${added}.`);
      });
    }

    async function addUrl(button) {
      const url = urlInput.value.trim();
      if (!url) {
        notify("Вставьте ссылку на страницу.", { error: true });
        urlInput.focus();
        return;
      }
      await guard(button, async () => {
        try {
          await request(`${api}/sources/url`, { method: "POST", body: { url } });
          urlInput.value = "";
          await refreshSources();
          notify("Страница скачана и разобрана.");
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function addGithub(button) {
      const url = githubInput.value.trim();
      if (!url) {
        notify("Вставьте ссылку на репозиторий.", { error: true });
        githubInput.focus();
        return;
      }
      await guard(button, async () => {
        try {
          const data = await request(`${api}/sources/github`, { method: "POST", body: { url } });
          githubInput.value = "";
          await refreshSources();
          notify(`Из репозитория добавлено файлов: ${data.count}.`);
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function removeSource(button) {
      const row = button.closest(".source-row");
      const title = (row && row.querySelector(".source-title")?.textContent) || "материал";
      const confirmed = await window.App.confirm(
        "Убрать материал?",
        `«${title}» больше не будет учитываться при генерации. Уже сгенерированные модули останутся.`,
        { label: "Убрать", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          await request(`${api}/sources/${encodeURIComponent(button.dataset.source)}`, { method: "DELETE" });
          await refreshSources();
          notify("Материал убран.");
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /* структура курса */

    /** Номера собранных модулей: у них в строке нет ни «Сгенерировать», ни «Убрать». */
    function generatedModules() {
      const table = node.querySelector(".admin-table");
      const rows = table ? table.querySelectorAll("tbody tr[data-module]") : [];
      return new Set([...rows].map((row) => Number(row.dataset.module)));
    }

    function structureRows() {
      return [...structureList.querySelectorAll(".structure-row")];
    }

    /** Номер модуля — это его место в списке: после добавления и удаления строк
     *  перенумеровываем подписи, кнопки перестановки и генерации. */
    function syncStructure() {
      const rows = structureRows();
      rows.forEach((row, index) => {
        const number = index + 1;
        const label = row.querySelector(".structure-number");
        if (label) label.textContent = String(number);
        const generate = row.querySelector('[data-action="generate-module"]');
        if (generate) generate.dataset.module = String(number);
        const manual = row.querySelector('[data-action="create-module"]');
        if (manual) manual.dataset.module = String(number);
        const [up, down] = row.querySelectorAll('[data-action="swap-module"]');
        if (up) {
          up.dataset.a = String(number - 1);
          up.dataset.b = String(number);
          up.disabled = index === 0;
          up.setAttribute("aria-label", `Поднять модуль ${number} выше`);
        }
        if (down) {
          down.dataset.a = String(number);
          down.dataset.b = String(number + 1);
          down.disabled = index === rows.length - 1;
          down.setAttribute("aria-label", `Опустить модуль ${number} ниже`);
        }
      });
      if (structureNone) structureNone.hidden = rows.length > 0;
    }

    /** Строка структуры: «номер · название · тема · занятий · порядок · кнопки». */
    function structureRow() {
      const row = make("li", "structure-row");
      row.append(make("span", "structure-number"));
      row.append(field("Название", inputNode("text", "", { field: "title", maxlength: "200" }), "structure-cell"));
      row.append(field("Тема", inputNode("text", "", { field: "theme", maxlength: "200" }), "structure-cell"));
      row.append(
        field("Занятий", inputNode("number", 4, { field: "lessons", min: "2", max: "10", step: "1" }), "structure-cell")
      );
      const order = make("div", "structure-order");
      order.append(buttonNode("↑", "ghost icon-only", "swap-module"));
      order.append(buttonNode("↓", "ghost icon-only", "swap-module"));
      row.append(order);
      const actions = make("div", "structure-actions");
      actions.append(buttonNode("Сгенерировать", "", "generate-module"));
      actions.append(buttonNode("Создать вручную", "", "create-module"));
      const remove = buttonNode("", "ghost icon-only", "remove-module");
      remove.append(icon("close"));
      actions.append(remove);
      row.append(actions);
      return row;
    }

    /** Строки с диска: страница могла прийти без них, а структура уже составлена. */
    async function loadStructure() {
      if (structureRows().length) return;
      let plan;
      try {
        ({ plan } = await request(`${api}/structure`));
      } catch {
        return; // структура необязательна: строки добавляются руками
      }
      if (!plan || !(plan.modules || []).length) return;
      const generated = generatedModules();
      plan.modules.forEach((module) => {
        const row = structureRow();
        row.querySelector('[data-field="title"]').value = module.title || "";
        row.querySelector('[data-field="theme"]').value = module.theme || "";
        row.querySelector('[data-field="lessons"]').value = String(module.lessons || 4);
        if (generated.has(module.number)) {
          // У собранного модуля название и число занятий — из его файла:
          // структура их только показывает, иначе сохранение разошлось бы с seed.
          row.dataset.built = "1";
          row.querySelectorAll("input").forEach((input) => {
            input.readOnly = true;
            input.tabIndex = -1;
          });
          const actions = row.querySelector(".structure-actions");
          actions.replaceChildren(make("span", "badge success", "готов"));
        }
        structureList.append(row);
      });
      // Вводная курса лежит в атрибуте корня: без неё сохранение её бы стёрло.
      node.dataset.planIntro = plan.intro || "";
      if (makeButton) {
        makeButton.textContent = "Составить структуру заново";
        makeButton.disabled = generated.size > 0;
        if (generated.size) {
          makeButton.title = "Модули уже собраны: структуру правьте строками, а не собирайте заново.";
        }
      }
      syncStructure();
    }

    /** Структура из формы: номер — место строки; название курса и вводная — не
     *  правятся в строках, поэтому лежат в data-атрибутах корня. */
    function structurePlan() {
      const modules = structureRows().map((row, index) => ({
        number: index + 1,
        title: row.querySelector('[data-field="title"]').value.trim(),
        theme: row.querySelector('[data-field="theme"]').value.trim(),
        lessons: Number(row.querySelector('[data-field="lessons"]').value),
      }));
      return { title: node.dataset.planTitle || "", intro: node.dataset.planIntro || "", modules };
    }

    /** Ошибки структуры — рядом со строками: текст сервера объясняет, что не так.
     *
     *  5xx ``App.request`` закрывает общим «результат неизвестен» — у отказа
     *  модели причина понятнее («Модель не составила структуру: …»), поэтому у
     *  строк показываем её, а в тосте остаётся принятое в проекте предупреждение.
     */
    function showStructureProblem(problem) {
      const detail = problem.detail && typeof problem.detail.message === "string" ? problem.detail.message : "";
      const box = structureForm.querySelector('[data-error-for="modules"]');
      if (box) {
        box.textContent = detail || problem.message;
        box.hidden = false;
      }
      notify(problem.message, { error: true });
    }

    function addModule() {
      const row = structureRow();
      structureList.append(row);
      syncStructure();
      row.querySelector('[data-field="title"]').focus();
    }

    function removeModule(button) {
      const row = button.closest(".structure-row");
      if (!row) return;
      const rows = structureRows();
      const index = rows.indexOf(row);
      const generated = generatedModules();
      // Убрать строку перед собранным модулем нельзя: он сдвинется на её
      // номер, а файл останется на прежнем — структура разойдётся с курсом.
      const lastBuilt = rows.reduce(
        (last, entry, position) => (generated.has(position + 1) ? position : last),
        -1
      );
      if (index < lastBuilt) {
        notify(
          "Перед собранным модулем строку не убрать: его номер сдвинется. Переставьте модули стрелками.",
          { error: true }
        );
        return;
      }
      row.remove();
      syncStructure();
    }

    /** Составить структуру: один вызов модели, ответ длинный — до минуты. */
    async function makeStructure(button) {
      if (generatedModules().size) {
        notify(
          "Модули уже собраны: структуру правьте строками, а не собирайте заново — иначе модель переименует готовые модули.",
          { error: true }
        );
        return;
      }
      if (structureRows().length) {
        const confirmed = await window.App.confirm(
          "Составить структуру заново?",
          "Модель предложит структуру с нуля, и она перезапишет текущую: строки, названия, темы и число занятий потеряются. Уже собранные модули останутся.",
          { label: "Составить заново", danger: true }
        );
        if (!confirmed) return;
      }
      const label = button.textContent;
      button.disabled = true;
      button.textContent = "Составляем структуру…";
      try {
        await request(`${api}/structure`, {
          method: "POST",
          body: { hints: hintsInput ? hintsInput.value.trim() : "" },
          timeout: STRUCTURE_TIMEOUT,
        });
        location.reload();
      } catch (problem) {
        showStructureProblem(problem);
      } finally {
        button.textContent = label;
        button.disabled = false;
      }
    }

    async function saveStructure(button) {
      clearErrors(structureForm);
      await guard(button, async () => {
        try {
          await request(`${api}/structure`, { method: "PUT", body: { plan: structurePlan() } });
          notify("Структура сохранена.");
        } catch (problem) {
          // 422 приходит с полем modules: текст сервера показываем у строк как есть.
          showStructureProblem(problem);
        }
      });
    }

    /** Перестановка: модули двигает сервер — вместе с файлами и конспектами. */
    async function swapModule(button) {
      const body = { a: Number(button.dataset.a), b: Number(button.dataset.b) };
      clearErrors(structureForm);
      await guard(button, async () => {
        try {
          await request(`${api}/structure/swap`, { method: "POST", body });
          location.reload();
        } catch (problem) {
          showStructureProblem(problem);
        }
      });
    }

    /** Подсказки генерации: поле у кнопок ниже, иначе — подсказки структуры. */
    function generationHints() {
      const own = node.querySelector("#gen-hints");
      return (own ? own.value.trim() : "") || (hintsInput ? hintsInput.value.trim() : "");
    }

    /** Пустой модуль руками: занятия-заготовки, дальше — редактор модуля.
     *  Название и число занятий берём из строки структуры как есть: сервер
     *  синхронизирует их со структурой после создания. */
    async function createModule(button) {
      const number = Number(button.dataset.module);
      const row = button.closest(".structure-row");
      const title = row ? row.querySelector('[data-field="title"]').value.trim() : "";
      const lessons = row ? Number(row.querySelector('[data-field="lessons"]').value) : 4;
      await guard(button, async () => {
        try {
          await request(`${api}/modules`, {
            method: "POST",
            body: { module: number, title, lessons },
          });
          notify(`Модуль ${number} создан — заполните занятия и конспекты.`);
          location.assign(
            `/admin/courses/${encodeURIComponent(node.dataset.course)}/modules/${number}`
          );
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /** Модуль по структуре — та же генерация, что и из формы ниже. */
    async function generateModule(button) {
      const moduleNumber = Number(button.dataset.module);
      const body = { module: moduleNumber, hints: generationHints() };
      await guard(button, async () => {
        try {
          const data = await request(`${api}/generate`, { method: "POST", body });
          showRun(data.run, true);
          notify(`Генерация модуля ${moduleNumber} пошла.`);
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /** Все модули структуры, которых ещё нет, — одним прогоном по очереди. */
    async function generateMissing(button) {
      const confirmed = await window.App.confirm(
        "Сгенерировать недостающие модули?",
        "Модель соберёт все модули из структуры, которых ещё нет, по очереди. Это десятки вызовов и минуты на каждый модуль; готовые модули не трогаются.",
        { label: "Сгенерировать" }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          const data = await request(`${api}/generate-missing`, {
            method: "POST",
            body: { hints: generationHints() },
          });
          showRun(data.run, true);
          notify(`Генерация модулей пошла: ${data.modules.join(", ")}.`);
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /* генерация */

    function showRun(run, generating) {
      genButton.hidden = generating;
      if (regenButton) regenButton.hidden = generating;
      if (batchButton) batchButton.hidden = generating;
      cancelButton.hidden = !generating;
      if (!run) {
        runBox.hidden = true;
        return;
      }
      runBox.hidden = false;
      const label = RUN_LABELS[run.status] || run.status;
      const moduleLabel = run.module ? `модуль ${run.module}` : "";
      if (run.current) {
        stepCell.textContent = `${label}: ${moduleLabel ? `${moduleLabel}, ` : ""}${run.current}`;
      } else {
        stepCell.textContent = moduleLabel ? `${label} · ${moduleLabel}` : label;
      }
      const total = Number(run.total || 0);
      const doneCount = Number(run.done || 0);
      countCell.textContent = total ? `сделано ${doneCount} из ${total}` : "шагов пока нет";
      fill.style.width = total ? `${Math.round((100 * doneCount) / total)}%` : "0%";
      runError.hidden = !run.error;
      runError.textContent = run.error || "";
      // Итог автопроверки код-заданий: какие задания придётся переписать.
      showList(runWarnings, "Эти задания надо переписать:", run.warnings || []);
    }

    async function watch() {
      if (watching) return;
      watching = true;
      try {
        for (;;) {
          await sleep(POLL_MS);
          let data;
          try {
            data = await request(`${api}/generation`);
          } catch (problem) {
            notify(problem.message, { error: true });
            return;
          }
          showRun(data.run, data.generating);
          // Прогон кончился: перечитываем страницу — модуль и предупреждения
          // могли поменяться. Ошибку шага страница покажет из состояния на диске.
          // Пока задача жива, «done» означает лишь готовый модуль: в пакетном
          // прогоне следом идёт следующий, и перезагружаться рано.
          if (data.run && TERMINAL.includes(data.run.status) && !data.generating) {
            location.reload();
            return;
          }
          if (!data.generating) {
            notify("Прогон прервался — запустите генерацию заново.", { error: true });
            return;
          }
        }
      } finally {
        watching = false;
      }
    }

    async function initRun() {
      let data;
      try {
        data = await request(`${api}/generation`);
      } catch {
        return; // прогресс не обязателен: страница работает и без него
      }
      showRun(data.run, data.generating);
      if (data.run && data.run.status === "running" && !data.generating) {
        stepCell.textContent = "Прогон прервался — запустите генерацию заново";
      }
      if (data.generating) watch();
    }

    /** Запуск генерации модуля: кнопка «Сгенерировать» и «Перегенерировать» —
     *  одно и то же действие, разница только в data-force (собрать с нуля). */
    async function generate(button) {
      const moduleNumber = Number(node.querySelector("#gen-module").value);
      if (!Number.isInteger(moduleNumber) || moduleNumber < 1 || moduleNumber > 10) {
        notify("Номер модуля — целое число от 1 до 10.", { error: true });
        return;
      }
      const body = {
        module: moduleNumber,
        title: node.querySelector("#gen-name").value.trim(),
        hints: node.querySelector("#gen-hints").value.trim(),
        force: button.dataset.force === "true",
      };
      await guard(button, async () => {
        try {
          const data = await request(`${api}/generate`, { method: "POST", body });
          showRun(data.run, true);
          notify(
            body.force
              ? `Модуль ${moduleNumber} собирается заново.`
              : `Генерация модуля ${moduleNumber} пошла.`
          );
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function cancelRun(button) {
      const confirmed = await window.App.confirm(
        "Отменить генерацию?",
        "Прогон остановится на границе текущего шага. Готовые файлы модуля останутся — модуль можно догенерировать заново.",
        { label: "Отменить", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          const data = await request(`${api}/generation/cancel`, { method: "POST" });
          showRun(data.run, true);
          notify("Просим прогон остановиться…");
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    onActions(node, {
      publish,
      unpublish,
      delete: remove,
      "remove-source": removeSource,
      "add-url": addUrl,
      "add-github": addGithub,
      generate,
      regenerate: generate,   // та же генерация, но с data-force="true"
      "generate-missing": generateMissing,
      "cancel-run": cancelRun,
      "make-structure": makeStructure,
      "save-structure": saveStructure,
      "generate-module": generateModule,
      "create-module": createModule,
      "add-module": addModule,
      "remove-module": removeModule,
      "swap-module": swapModule,
    });
    // Enter в строке структуры не должен перезагружать страницу: структуру
    // сохраняет кнопка, а не отправка формы.
    node.addEventListener("submit", (event) => {
      if (event.target.id === "course-structure") event.preventDefault();
    });
    picker.addEventListener("change", addFiles);
    initRun();
    loadStructure();
  }

  /* --- редактор модуля --- */

  function moduleScreen(node) {
    const api = node.dataset.api;
    const courseApi = node.dataset.courseApi;
    const moduleNumber = Number(node.dataset.number);
    const theoryUrl = node.dataset.theory;
    const idsUrl = node.dataset.ids;
    // Переделка кусков требует плана прогона: у модуля, собранного вручную,
    // его нет, и такие кнопки показывать нечего.
    const partsEnabled = node.dataset.parts === "1";
    const editor = node.querySelector("#module-editor");
    const loading = node.querySelector("#module-loading");
    const errorBox = node.querySelector("#module-error");
    const warningsBox = node.querySelector("#module-warnings");
    const checkBox = node.querySelector("#module-check");
    const runBox = node.querySelector("#module-run");
    const runStep = node.querySelector("#module-run-step");
    const runCount = node.querySelector("#module-run-count");
    const runFill = node.querySelector("#module-run-fill");
    const runError = node.querySelector("#module-run-error");
    const runWarnings = node.querySelector("#module-run-warnings");
    let model = null;
    let watching = false;
    // Тело панели анкеты: перерисовывается, когда меняется состав блоков.
    let surveyHost = null;

    function fail(problem) {
      errorBox.textContent = problem.message;
      errorBox.hidden = false;
      notify(problem.message, { error: true });
    }

    const lessonOf = (id) => model.nodes.find((lesson) => lesson.id === id) || null;
    const itemOf = (id) => model.items.find((item) => item.id === id) || null;

    /** Главный концепт задания: с наибольшим весом, при равенстве — первый (как в ядре). */
    function mainConcept(item) {
      const entries = Object.entries(item.concept_weights || {});
      if (!entries.length) return null;
      return entries.reduce((best, entry) => (entry[1] > best[1] ? entry : best), entries[0])[0];
    }

    function itemsOf(lesson, sectionId, final) {
      return model.items.filter(
        (item) =>
          Boolean(item.final) === final &&
          mainConcept(item) === lesson.id &&
          (final || item.section === sectionId)
      );
    }

    function orphanItems() {
      const known = new Set(model.nodes.map((lesson) => lesson.id));
      return model.items.filter((item) => !known.has(mainConcept(item)));
    }

    function number(value, fallback) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : fallback;
    }

    /* разметка редактора */

    function toolbar() {
      const bar = make("div", "admin-toolbar");
      bar.append(buttonNode("Сохранить модуль", "primary", "save-module"));
      bar.append(make("span", "form-hint", "Правки уходят целиком: сервер проверит весь курс и скажет, что не сходится."));
      return bar;
    }

    function lessonCard(lesson, index) {
      const card = make("article", "lesson-card");
      card.dataset.lesson = lesson.id;
      const head = make("header", "lesson-head");
      head.append(make("h3", "", `Занятие ${index + 1}`));
      head.append(make("span", "lesson-id muted", lesson.id));
      if (partsEnabled) {
        head.append(
          buttonNode("Перегенерировать конспект", "ghost part-action", "regenerate-theory", { lesson: lesson.id })
        );
        head.append(
          buttonNode("Перегенерировать итоговое", "ghost part-action", "regenerate-final", { lesson: lesson.id })
        );
      }
      // Порядок карточек — порядок занятий в графе: после сохранения цепочка
      // связей модуля пересоберётся заново.
      const order = make("span", "order-controls");
      const up = buttonNode("↑", "ghost icon-only", "move-lesson", { direction: "up" });
      up.disabled = index === 0;
      up.setAttribute("aria-label", `Поднять занятие ${index + 1} выше`);
      const down = buttonNode("↓", "ghost icon-only", "move-lesson", { direction: "down" });
      down.disabled = index === model.nodes.length - 1;
      down.setAttribute("aria-label", `Опустить занятие ${index + 1} ниже`);
      order.append(up, down);
      head.append(order);
      head.append(buttonNode("Удалить занятие", "danger", "remove-lesson"));
      card.append(head);

      const grid = make("div", "form-row");
      grid.append(field("Название", inputNode("text", lesson.name, { field: "name", maxlength: 200 })));
      grid.append(
        field("Сложность (0–1)", inputNode("number", lesson.difficulty, { field: "difficulty", min: "0", max: "1", step: "0.05" }))
      );
      grid.append(field("Описание", inputNode("text", lesson.description || "", { field: "description", maxlength: 400 }), "wide"));
      card.append(grid);

      card.append(make("h4", "section-title small", "Разделы"));
      const list = make("ul", "section-list");
      lesson.sections.forEach((section) => list.append(sectionRow(section)));
      card.append(list);
      card.append(buttonNode("Добавить раздел", "", "add-section"));

      lesson.sections.forEach((section) => {
        card.append(itemGroup(lesson, section.id, `Задания раздела «${section.title}»`, false));
      });
      card.append(itemGroup(lesson, null, "Итоговое задание", true));
      card.append(theoryBlock(lesson));
      return card;
    }

    function sectionRow(section) {
      const row = make("li", "section-row");
      row.dataset.sectionId = section.id;
      row.append(make("span", "section-id muted", section.id));
      row.append(inputNode("text", section.title, { field: "title", maxlength: 200, label: "Название раздела" }));
      row.append(buttonNode("Удалить", "ghost", "remove-section"));
      return row;
    }

    function itemGroup(lesson, sectionId, title, final) {
      const group = make("div", "item-group");
      group.dataset.group = final ? "final" : sectionId;
      group.append(make("h4", "section-title small", title));
      if (partsEnabled && !final) {
        group.append(
          buttonNode("Перегенерировать задания", "ghost part-action", "regenerate-items", {
            lesson: lesson.id,
            section: sectionId,
          })
        );
      }
      const items = itemsOf(lesson, sectionId, final);
      if (!items.length) {
        group.append(
          make(
            "p",
            "muted",
            final
              ? "Итогового задания нет — у опубликованного модуля оно обязательно."
              : "В разделе нет заданий — у опубликованного модуля их нужно не меньше двух."
          )
        );
      }
      items.forEach((item) => group.append(itemCard(item)));
      const add = buttonNode(final ? "Добавить итоговое задание" : "Добавить задание", "", "add-item");
      add.classList.add("item-add");
      group.append(add);
      return group;
    }

    function rubricSelect(item) {
      const entries = [["", "Без рубрики"], ...model.rubrics.map((rubric) => [String(rubric.id), `#${rubric.id} — ${rubric.name}`])];
      const known = item.rubric_id === null || item.rubric_id === undefined || entries.some(([value]) => Number(value) === item.rubric_id);
      if (!known) entries.push([String(item.rubric_id), `#${item.rubric_id} — нет в модуле`]);
      return selectNode(entries, item.rubric_id === null || item.rubric_id === undefined ? "" : String(item.rubric_id), {
        field: "rubric_id",
        label: "Рубрика задания",
      });
    }

    function criterionRow(criterion) {
      const row = make("div", "criterion-row");
      row.dataset.criterion = String(criterion.id);
      row.append(make("span", "criterion-id muted", `#${criterion.id}`));
      row.append(
        areaNode(criterion.criterion, {
          field: "criterion",
          rows: 2,
          className: "criterion-text",
          label: `Критерий #${criterion.id}`,
        })
      );
      row.append(
        inputNode("number", criterion.weight, {
          field: "weight",
          min: "0",
          step: "0.1",
          className: "criterion-weight",
          label: `Вес критерия #${criterion.id}`,
        })
      );
      return row;
    }

    function criteriaBox(item) {
      const box = make("div", "criteria-box");
      box.dataset.only = "rubric";
      box.append(make("h4", "section-title small", "Критерии рубрики"));
      if (item.rubric_id === null || item.rubric_id === undefined) {
        box.append(make("p", "muted", "Рубрика не выбрана: у открытого, голосового ответа и кода она нужна."));
        return box;
      }
      const criteria = model.criteria.filter((criterion) => criterion.rubric_id === item.rubric_id);
      if (!criteria.length) {
        box.append(make("p", "muted", `У рубрики #${item.rubric_id} нет критериев — сервер такое задание не примет.`));
        return box;
      }
      criteria.forEach((criterion) => box.append(criterionRow(criterion)));
      return box;
    }

    function itemCard(item) {
      const card = make("article", "item-card");
      card.dataset.item = String(item.id);
      const head = make("header", "item-head");
      head.append(make("span", "badge", `#${item.id}`));
      head.append(make("span", "muted", item.final ? "итоговое" : `раздел ${item.section || "—"}`));
      head.append(buttonNode("Удалить задание", "ghost", "remove-item"));
      card.append(head);

      card.append(field("Условие", areaNode(item.prompt, { field: "prompt", rows: 3, maxlength: 2000 }), "wide"));

      const grid = make("div", "form-row");
      grid.append(
        field(
          "Тип ответа",
          selectNode(
            Object.entries(ANSWER_TYPES).map(([value, label]) => [value, label]),
            item.answer_type,
            { field: "answer_type", label: "Тип ответа" }
          )
        )
      );
      const answerField = field("Эталон / номер варианта", inputNode("text", item.answer || "", { field: "answer", maxlength: 500 }));
      // Эталон нужен только автопроверке: выбору и короткому ответу.
      answerField.dataset.only = "auto";
      grid.append(answerField);
      grid.append(
        field("Сложность (0–1)", inputNode("number", item.difficulty, { field: "difficulty", min: "0", max: "1", step: "0.05" }))
      );
      const rubricField = field("Рубрика", rubricSelect(item));
      rubricField.dataset.only = "rubric";
      grid.append(rubricField);
      card.append(grid);

      const options = field("Варианты — по строке на вариант", areaNode((item.options || []).join("\n"), { field: "options", rows: 4 }), "wide");
      options.dataset.only = "choice";
      card.append(options);

      for (const [name, label] of [
        ["starter", "Заготовка (код)"],
        ["setup", "Подготовка данных (код)"],
        ["tests", "Проверки (assert)"],
      ]) {
        const wrapper = field(
          label,
          areaNode(item[name], { field: name, rows: 6, className: "code-input", spellcheck: "false" }),
          "wide"
        );
        wrapper.dataset.only = "code";
        card.append(wrapper);
      }

      card.append(criteriaBox(item));
      syncItemCard(card, item);
      return card;
    }

    function theoryBlock(lesson) {
      const details = make("details", "theory-block");
      details.append(make("summary", "", "Конспект занятия"));
      const order = lesson.sections.map((section) => section.title).join(" → ");
      details.append(
        make("p", "form-hint", `Заголовки «## <название раздела>» должны идти в конспекте в том же порядке, что разделы: ${order}.`)
      );
      const area = areaNode("", { rows: 18, className: "code-input", label: `Конспект занятия ${lesson.name}` });
      const save = buttonNode("Сохранить конспект", "", "save-theory", { lesson: lesson.id });
      const state = make("p", "muted theory-state", "");
      details.append(area, save, state);
      details.addEventListener("toggle", () => {
        if (details.open) loadTheory(lesson.id, area, state);
      });
      return details;
    }

    /* анкета модуля */

    /** Анкета модуля: сервер требует её от каждого модуля, поэтому без неё
     *  редактор собирает заготовку — дальше её заполняют руками. */
    function survey() {
      if (!model.topic.survey) {
        model.topic.survey = {
          level_key: `t${String(moduleNumber).padStart(2, "0")}_level`,
          level_question: "",
          level_options: ["", "", ""],
          blocks: [],
          assumed_by_confident: [],
          foundation: [],
        };
      }
      const data = model.topic.survey;
      // Вариантов ровно три — столько ждёт сервер: лишние отбрасываем, недостающие добиваем пустыми.
      const options = Array.isArray(data.level_options) ? data.level_options : [];
      data.level_options = [0, 1, 2].map((index) => (options[index] === undefined ? "" : String(options[index])));
      if (!Array.isArray(data.blocks)) data.blocks = [];
      data.blocks.forEach((block) => {
        if (!Array.isArray(block.concepts)) block.concepts = [];
      });
      // Блок без ключа сервер не соберёт, а ключ не меняется после создания.
      data.blocks.forEach((block) => {
        if (!block.key) block.key = nextBlockKey(data);
      });
      if (!data.level_key) data.level_key = `t${String(moduleNumber).padStart(2, "0")}_level`;
      for (const name of ["assumed_by_confident", "foundation"]) {
        if (!Array.isArray(data[name])) data[name] = [];
      }
      return data;
    }

    /** Ключ нового блока — `tNN_blockK`, K первый незанятый в модуле. Ключи
     *  блоков и вопроса об уровне уникальны во всём курсе: сервер откажет,
     *  если такой уже занят другим модулем. */
    function nextBlockKey(data) {
      const taken = new Set([data.level_key, ...data.blocks.map((block) => block.key)]);
      const prefix = `t${String(moduleNumber).padStart(2, "0")}_block`;
      let index = 1;
      while (taken.has(`${prefix}${index}`)) index += 1;
      return `${prefix}${index}`;
    }

    /** Блок анкеты, в карточке которого нажата кнопка или правят поле. */
    function blockOf(node) {
      const card = node.closest("[data-block-key]");
      if (!card) return null;
      return survey().blocks.find((block) => block.key === card.dataset.blockKey) || null;
    }

    /** Блоки анкеты, в которые входит занятие. */
    function blocksWith(lessonId) {
      const data = survey();
      return data.blocks.filter((block) => block.concepts.includes(lessonId));
    }

    /** Чекбоксы занятий блока: значение — id занятия из графа модуля. Занятие,
     *  которого в модуле уже нет, показывается отдельной строкой: сервер
     *  ругнётся на чужую тему, и её надо снять галочкой. */
    function lessonChecks(block) {
      const known = model.nodes.map((lesson) => lesson.id);
      const foreign = block.concepts.filter((id) => !known.includes(id));
      const box = make("div", "survey-lessons");
      [...known, ...foreign].forEach((id) => {
        const lesson = lessonOf(id);
        const check = inputNode("checkbox", id, {
          field: "survey_block_lesson",
          "data-concept": id,
          label: `Занятие ${id} в блоке ${block.key}`,
        });
        check.checked = block.concepts.includes(id);
        const label = make("label", "check-field");
        label.append(check, document.createTextNode(lesson ? lesson.name : `${id} — нет в модуле`));
        box.append(label);
      });
      if (!box.children.length) box.append(make("span", "muted", "В модуле нет занятий."));
      return box;
    }

    function blockCard(block, index, total) {
      const card = make("article", "survey-block");
      card.dataset.blockKey = block.key;
      const head = make("header", "survey-block-head");
      head.append(make("span", "badge", block.key));
      // Порядок блоков — порядок вопросов анкеты у ученика: он сохраняется
      // как есть, ключи от перестановки не меняются.
      const order = make("span", "order-controls");
      const up = buttonNode("↑", "ghost icon-only", "move-block", { direction: "up" });
      up.disabled = index === 0;
      up.setAttribute("aria-label", `Поднять блок ${block.key} выше`);
      const down = buttonNode("↓", "ghost icon-only", "move-block", { direction: "down" });
      down.disabled = index === total - 1;
      down.setAttribute("aria-label", `Опустить блок ${block.key} ниже`);
      order.append(up, down);
      head.append(order);
      head.append(buttonNode("Убрать блок", "ghost", "remove-block"));
      card.append(head);

      const grid = make("div", "form-row");
      grid.append(field("Название", inputNode("text", block.title, { field: "survey_block_title", maxlength: 200 })));
      grid.append(
        field("Вопрос ученику", inputNode("text", block.question, { field: "survey_block_question", maxlength: 400 }), "wide")
      );
      grid.append(field("Пример", areaNode(block.example, { field: "survey_block_example", rows: 2 }), "wide"));
      card.append(grid);

      card.append(make("h4", "section-title small", "Занятия блока"));
      card.append(lessonChecks(block));
      return card;
    }

    /** Тело панели: вопрос об уровне, три его варианта и блоки с занятиями.
     *  Ключи и занятия держат анкету в согласии с графом модуля — сервер это
     *  и проверяет при сохранении. */
    function renderSurvey(host) {
      const data = survey();
      host.replaceChildren();

      const level = make("div", "form-row");
      level.append(
        field(
          "Вопрос об общем уровне",
          inputNode("text", data.level_question, { field: "survey_level_question", maxlength: 400 }),
          "wide"
        )
      );
      host.append(level);

      const options = make("div", "form-row");
      data.level_options.forEach((value, index) => {
        const spec = { field: "survey_level_option", maxlength: 100, "data-index": index };
        if (index === 2) spec.placeholder = "Уверенно";
        options.append(field(`Вариант ${index + 1}`, inputNode("text", value, spec)));
      });
      host.append(options);
      host.append(
        make(
          "p",
          "form-hint",
          "Вариантов ровно три, и третий — «Уверенно»: по нему узлы получают статус «знакомая — проверим»."
        )
      );

      host.append(make("h4", "section-title small", `Блоки (${data.blocks.length})`));
      const list = make("div", "survey-list");
      if (!data.blocks.length) {
        list.append(
          make("p", "muted", "В анкете нет блоков: добавьте блок и отметьте в нём занятия — без этого модуль не сохранить.")
        );
      }
      data.blocks.forEach((block, index) => list.append(blockCard(block, index, data.blocks.length)));
      host.append(list);
      host.append(buttonNode("Добавить блок", "", "add-block"));
    }

    function surveyPanel() {
      const panel = make("section", "panel");
      panel.append(make("h2", "section-title", "Анкета модуля"));
      panel.append(
        make(
          "p",
          "form-hint",
          "Анкета открывает модуль: по ответам строится маршрут. Каждое занятие должно входить ровно в один блок, а ключи блоков не правятся — по ним в журнале остаются ответы. Стрелки ↑/↓ меняют порядок вопросов, он сохраняется вместе с модулем."
        )
      );
      if (partsEnabled) {
        panel.append(buttonNode("Перегенерировать анкету", "ghost", "regenerate-survey"));
      }
      surveyHost = make("div", "survey-form");
      panel.append(surveyHost);
      renderSurvey(surveyHost);
      return panel;
    }

    function render() {
      const box = make("div", "module-form");
      box.append(toolbar());

      const basics = make("section", "panel");
      basics.append(make("h2", "section-title", "Модуль"));
      const grid = make("div", "form-row");
      grid.append(field("Название", inputNode("text", model.topic.title, { field: "topic_title", maxlength: 200 })));
      grid.append(field("Вводная", inputNode("text", model.topic.intro, { field: "topic_intro", maxlength: 2000 }), "wide"));
      basics.append(grid);
      basics.append(
        make(
          "p",
          "form-hint",
          "Номер модуля здесь не правят. Граф занятий строится по их порядку: переставьте занятия стрелками или добавьте новое — связи пересоберутся при сохранении."
        )
      );
      box.append(basics);

      const lessons = make("section", "panel");
      lessons.append(make("h2", "section-title", `Занятия (${model.nodes.length})`));
      if (!model.nodes.length) lessons.append(make("p", "muted", "В модуле нет ни одного занятия."));
      model.nodes.forEach((lesson, index) => lessons.append(lessonCard(lesson, index)));
      lessons.append(buttonNode("Добавить занятие", "", "add-lesson"));
      lessons.append(
        make(
          "p",
          "form-hint",
          "Порядок занятий — это граф модуля: каждое следующее мягко опирается на предыдущее. Стрелки меняют порядок, новое занятие встаёт в конец; связи графа пересобираются при сохранении."
        )
      );
      box.append(lessons);

      box.append(surveyPanel());

      const orphans = orphanItems();
      if (orphans.length) {
        const panel = make("section", "panel");
        panel.append(make("h2", "section-title", `Задания без занятия (${orphans.length})`));
        panel.append(
          make(
            "p",
            "form-hint",
            "Главный концепт этих заданий — не занятие модуля: так модуль не соберётся. Поправьте задание или удалите его."
          )
        );
        orphans.forEach((item) => panel.append(itemCard(item)));
        box.append(panel);
      }

      editor.replaceChildren(box);
    }

    /* правка модели */

    /** Показать/скрыть поля по типу ответа и вернуть в них значения модели. */
    function syncItemCard(card, item) {
      card.querySelectorAll("[data-only]").forEach((node) => {
        const only = node.dataset.only;
        const visible =
          only === "choice"
            ? item.answer_type === "choice"
            : only === "code"
              ? item.answer_type === "code"
              : only === "auto"
                ? item.answer_type === "choice" || item.answer_type === "short"
                : item.answer_type === "open" ||
                  item.answer_type === "spoken" ||
                  item.answer_type === "code";
        node.hidden = !visible;
      });
      const options = card.querySelector('[data-field="options"]');
      if (options) options.value = (item.options || []).join("\n");
      for (const name of ["starter", "setup", "tests"]) {
        const node = card.querySelector(`[data-field="${name}"]`);
        if (node) node.value = item[name] || "";
      }
      const rubric = card.querySelector('[data-field="rubric_id"]');
      if (rubric) {
        const value = item.rubric_id === null || item.rubric_id === undefined ? "" : String(item.rubric_id);
        rubric.value = value;
      }
    }

    function setCriterion(row, name, target) {
      const id = Number(row.dataset.criterion);
      const criterion = model.criteria.find((entry) => entry.id === id);
      if (!criterion) return;
      if (name === "weight") criterion.weight = number(target.value, criterion.weight);
      else criterion.criterion = target.value;
      // Критерий показывается в карточках всех заданий своей рубрики — держим их синхронно.
      editor.querySelectorAll(`[data-criterion="${id}"]`).forEach((other) => {
        if (other === row) return;
        other.querySelectorAll("[data-field]").forEach((node) => {
          node.value = name === "weight" ? String(criterion.weight) : criterion.criterion;
        });
      });
    }

    function setItem(card, name, target) {
      const item = itemOf(Number(card.dataset.item));
      if (!item) return;
      if (name === "options") {
        item.options = target.value
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        return;
      }
      if (name === "difficulty") {
        item.difficulty = number(target.value, item.difficulty);
        return;
      }
      if (name === "rubric_id") {
        item.rubric_id = target.value ? Number(target.value) : null;
        const box = card.querySelector(".criteria-box");
        if (box) box.replaceWith(criteriaBox(item));
        syncItemCard(card, item);
        return;
      }
      if (name === "answer") {
        item.answer = target.value === "" ? null : target.value;
        return;
      }
      if (name === "starter" || name === "setup" || name === "tests") {
        item[name] = target.value === "" ? null : target.value;
        return;
      }
      if (name === "prompt") item.prompt = target.value;
    }

    function onChangeType(card, item, value) {
      item.answer_type = value;
      const dropped = [];
      if (value !== "choice" && (item.options || []).length) {
        item.options = [];
        dropped.push("варианты");
      }
      if (value !== "code" && (item.starter || item.setup || item.tests)) {
        item.starter = null;
        item.setup = null;
        item.tests = null;
        dropped.push("код");
      }
      if ((value === "choice" || value === "short") && item.rubric_id !== null && item.rubric_id !== undefined) {
        item.rubric_id = null;
        dropped.push("рубрику");
      }
      const box = card.querySelector(".criteria-box");
      if (box) box.replaceWith(criteriaBox(item));
      syncItemCard(card, item);
      if (dropped.length) notify(`Поля, которых нет у этого типа ответа, очищены: ${dropped.join(", ")}.`);
    }

    function syncGroupTitles(card, lesson) {
      lesson.sections.forEach((section) => {
        const group = card.querySelector(`[data-group="${window.CSS.escape(section.id)}"]`);
        const heading = group && group.querySelector("h4");
        if (heading) heading.textContent = `Задания раздела «${section.title}»`;
      });
    }

    function onEdit(event) {
      if (!model) return;
      const target = event.target;
      if (!target.dataset || !target.dataset.field) return;
      const name = target.dataset.field;
      if (name === "topic_title") {
        model.topic.title = target.value;
        return;
      }
      if (name === "topic_intro") {
        model.topic.intro = target.value;
        return;
      }
      if (name === "survey_level_question") {
        survey().level_question = target.value;
        return;
      }
      if (name === "survey_level_option") {
        const index = Number(target.dataset.index);
        const data = survey();
        if (index >= 0 && index < data.level_options.length) data.level_options[index] = target.value;
        return;
      }
      if (name === "survey_block_lesson") {
        const block = blockOf(target);
        if (!block) return;
        const id = target.dataset.concept;
        if (target.checked) {
          if (!block.concepts.includes(id)) block.concepts.push(id);
        } else {
          block.concepts = block.concepts.filter((entry) => entry !== id);
        }
        return;
      }
      if (name.startsWith("survey_block_")) {
        const block = blockOf(target);
        if (!block) return;
        if (name === "survey_block_title") block.title = target.value;
        else if (name === "survey_block_question") block.question = target.value;
        else if (name === "survey_block_example") block.example = target.value;
        return;
      }
      const criterionRowNode = target.closest("[data-criterion]");
      if (criterionRowNode) {
        setCriterion(criterionRowNode, name, target);
        return;
      }
      const itemCardNode = target.closest("[data-item]");
      if (itemCardNode) {
        const item = itemOf(Number(itemCardNode.dataset.item));
        if (!item) return;
        if (name === "answer_type" && target.tagName === "SELECT") {
          onChangeType(itemCardNode, item, target.value);
          return;
        }
        setItem(itemCardNode, name, target);
        return;
      }
      const sectionRowNode = target.closest("[data-section-id]");
      if (sectionRowNode) {
        const card = sectionRowNode.closest("[data-lesson]");
        const lesson = lessonOf(card.dataset.lesson);
        const section = lesson && lesson.sections.find((entry) => entry.id === sectionRowNode.dataset.sectionId);
        if (!section) return;
        section.title = target.value;
        syncGroupTitles(card, lesson);
        return;
      }
      const lessonCardNode = target.closest("[data-lesson]");
      if (lessonCardNode) {
        const lesson = lessonOf(lessonCardNode.dataset.lesson);
        if (!lesson) return;
        if (name === "difficulty") lesson.difficulty = number(target.value, lesson.difficulty);
        else if (name === "name") lesson.name = target.value;
        else if (name === "description") lesson.description = target.value;
      }
    }

    /* действия */

    function nextSectionId(lesson) {
      let index = lesson.sections.length + 1;
      let id = `sec${index}`;
      while (lesson.sections.some((section) => section.id === id)) {
        index += 1;
        id = `sec${index}`;
      }
      return id;
    }

    function addSection(button) {
      const card = button.closest("[data-lesson]");
      const lesson = lessonOf(card.dataset.lesson);
      if (!lesson) return;
      if (lesson.sections.length >= MAX_SECTIONS) {
        notify(`В занятии не больше ${MAX_SECTIONS} разделов.`, { error: true });
        return;
      }
      const section = { id: nextSectionId(lesson), title: "Новый раздел" };
      lesson.sections.push(section);
      card.querySelector(".section-list").append(sectionRow(section));
      const group = itemGroup(lesson, section.id, `Задания раздела «${section.title}»`, false);
      const finalGroup = card.querySelector('[data-group="final"]');
      card.insertBefore(group, finalGroup || null);
      notify(`Раздел ${section.id} добавлен: заполните название и конспект.`);
    }

    async function removeSection(button) {
      const row = button.closest("[data-section-id]");
      const card = row.closest("[data-lesson]");
      const lesson = lessonOf(card.dataset.lesson);
      if (!lesson) return;
      const sectionId = row.dataset.sectionId;
      const section = lesson.sections.find((entry) => entry.id === sectionId);
      const doomed = itemsOf(lesson, sectionId, false);
      const confirmed = await window.App.confirm(
        "Удалить раздел?",
        `Раздел «${section ? section.title : sectionId}» и его задания (${doomed.length} шт.) уйдут из модуля после сохранения. У занятия должно остаться 2–4 раздела, а в каждом разделе опубликованного модуля — не меньше двух заданий.`,
        { label: "Удалить", danger: true }
      );
      if (!confirmed) return;
      lesson.sections = lesson.sections.filter((entry) => entry.id !== sectionId);
      model.items = model.items.filter((item) => !doomed.includes(item));
      row.remove();
      const group = card.querySelector(`[data-group="${window.CSS.escape(sectionId)}"]`);
      if (group) group.remove();
      notify("Раздел удалён — не забудьте сохранить модуль.");
    }

    /** Убрать занятие из блоков анкеты: блок без занятий сервер не примет,
     *  поэтому удаление последнего занятия в блоке запрещено раньше. */
    function pruneSurvey(lessonId) {
      survey().blocks.forEach((block) => {
        block.concepts = block.concepts.filter((id) => id !== lessonId);
      });
    }

    async function removeLesson(button) {
      const card = button.closest("[data-lesson]");
      const lesson = lessonOf(card.dataset.lesson);
      if (!lesson) return;
      const doomed = model.items.filter((item) => mainConcept(item) === lesson.id);
      const blocks = blocksWith(lesson.id);
      // Блок без занятий анкета не переживёт: просим сначала переназначить тему.
      const sole = blocks.filter((block) => block.concepts.length <= 1);
      if (sole.length) {
        notify(
          `«${lesson.name}» — единственное занятие в блоке анкеты «${sole[0].title || sole[0].key}»: сначала отметьте в блоке другое занятие или уберите блок.`,
          { error: true }
        );
        return;
      }
      const confirmed = await window.App.confirm(
        "Удалить занятие?",
        `Занятие «${lesson.name}», его ${lesson.sections.length} раздела и ${doomed.length} заданий уйдут из модуля после сохранения.${
          blocks.length ? ` Из анкеты модуля пропадут вопросы про него (${blocks.length} шт.).` : ""
        }`,
        { label: "Удалить занятие", danger: true }
      );
      if (!confirmed) return;
      model.nodes = model.nodes.filter((entry) => entry.id !== lesson.id);
      model.items = model.items.filter((item) => !doomed.includes(item));
      model.edges = (model.edges || []).filter((edge) => edge.from_id !== lesson.id && edge.to_id !== lesson.id);
      pruneSurvey(lesson.id);
      card.remove();
      if (surveyHost) renderSurvey(surveyHost);
      notify("Занятие удалено — не забудьте сохранить модуль.");
    }

    /** Новое занятие: два раздела-заготовки, как требует формат, и место в
     *  анкете — без него модуль не собрать. Номер id выдаёт сервер: повтор
     *  id унаследовал бы журнал и mastery прежнего занятия. */
    async function addLesson() {
      let number;
      try {
        const data = await request(idsUrl, { method: "POST", body: { kind: "lesson", count: 1 } });
        number = data.ids[0];
      } catch (problem) {
        notify(problem.message, { error: true });
        return;
      }
      // Сгенерированные занятия называются `tNN_<ключ модели>`, поэтому
      // префикс `lesson` с ними не столкнётся; латинский id безопаснее
      // кириллицы из названия (он же имя файла конспекта).
      const id = `t${String(moduleNumber).padStart(2, "0")}_lesson${number}`;
      model.nodes.push({
        id,
        name: "Новое занятие",
        difficulty: 0.5,
        description: null,
        source_url: null,
        topic_id: moduleNumber,
        active: true,
        sections: [
          { id: "sec1", title: "Раздел 1" },
          { id: "sec2", title: "Раздел 2" },
        ],
      });
      const data = survey();
      if (data.blocks.length) {
        // Блок без занятий анкета не переживёт, поэтому новый узел сразу
        // попадает в последний блок: дальше преподаватель перенесёт его
        // галочками в подходящий.
        data.blocks[data.blocks.length - 1].concepts.push(id);
      } else {
        data.blocks.push({
          key: nextBlockKey(data),
          title: "Новый блок",
          question: "",
          example: "",
          concepts: [id],
        });
      }
      render();
      notify(`Занятие ${id} добавлено: заполните разделы и проверьте блок анкеты. Граф перестроится при сохранении.`);
    }

    /** Перестановка занятия: порядок карточек задаёт цепочку рёбер модуля. */
    function moveLesson(button) {
      const card = button.closest("[data-lesson]");
      if (!card) return;
      const index = model.nodes.findIndex((lesson) => lesson.id === card.dataset.lesson);
      const target = button.dataset.direction === "up" ? index - 1 : index + 1;
      if (index < 0 || target < 0 || target >= model.nodes.length) return;
      [model.nodes[index], model.nodes[target]] = [model.nodes[target], model.nodes[index]];
      render();
      notify("Порядок занятий изменён — граф курса перестроится при сохранении модуля.");
    }

    /* блоки анкеты */

    /** Перестановка блоков: порядок карточек — порядок вопросов у ученика. */
    function moveBlock(button) {
      const block = blockOf(button);
      if (!block) return;
      const data = survey();
      const index = data.blocks.findIndex((entry) => entry.key === block.key);
      const target = button.dataset.direction === "up" ? index - 1 : index + 1;
      if (index < 0 || target < 0 || target >= data.blocks.length) return;
      [data.blocks[index], data.blocks[target]] = [data.blocks[target], data.blocks[index]];
      renderSurvey(surveyHost);
      notify("Порядок блоков изменён — вопросы анкеты пойдут в новом порядке. Не забудьте сохранить модуль.");
    }

    function addBlock() {
      const data = survey();
      const key = nextBlockKey(data);
      data.blocks.push({ key, title: "Новый блок", question: "", example: "", concepts: [] });
      renderSurvey(surveyHost);
      notify(`Блок ${key} добавлен: отметьте в нём занятия.`);
    }

    /** Убрать блок: его ключ обязан уйти и из «знакомого без вопроса», и из
     *  «фундамента» — иначе сохранение упадёт на ссылке на неизвестный блок. */
    async function removeBlock(button) {
      const block = blockOf(button);
      if (!block) return;
      const data = survey();
      if (data.blocks.length <= 1) {
        notify("В анкете нужен хотя бы один блок: последний не убрать.", { error: true });
        return;
      }
      const confirmed = await window.App.confirm(
        "Убрать блок анкеты?",
        `Блок «${block.title || block.key}» и его ${block.concepts.length} занятий пропадут из анкеты после сохранения, а ключ ${block.key} — из списков «знакомое без вопроса» и «фундамент». Отмеченные в нём занятия надо будет развести по другим блокам.`,
        { label: "Убрать блок", danger: true }
      );
      if (!confirmed) return;
      data.blocks = data.blocks.filter((entry) => entry.key !== block.key);
      for (const name of ["assumed_by_confident", "foundation"]) {
        data[name] = data[name].filter((key) => key !== block.key);
      }
      renderSurvey(surveyHost);
      notify("Блок убран — не забудьте сохранить модуль.");
    }

    async function addItem(button) {
      const group = button.closest("[data-group]");
      const card = group.closest("[data-lesson]");
      const lesson = lessonOf(card.dataset.lesson);
      if (!lesson) return;
      const final = group.dataset.group === "final";
      await guard(button, async () => {
        try {
          const data = await request(idsUrl, { method: "POST", body: { kind: "item", count: 1 } });
          const id = data.ids[0];
          const item = {
            id,
            prompt: "",
            answer_type: "open",
            concept_weights: { [lesson.id]: 1.0 },
            difficulty: 0.5,
            options: [],
            answer: null,
            rubric_id: null,
            starter: null,
            setup: null,
            tests: null,
            active: true,
            section: final ? null : group.dataset.group,
            final,
          };
          model.items.push(item);
          const empty = group.querySelector("p.muted");
          if (empty) empty.remove();
          group.insertBefore(itemCard(item), group.querySelector(".item-add"));
          notify(`Задание #${id} добавлено: заполните условие.`);
        } catch (problem) {
          fail(problem);
        }
      });
    }

    async function removeItem(button) {
      const card = button.closest("[data-item]");
      const id = Number(card.dataset.item);
      const item = itemOf(id);
      if (!item) return;
      const confirmed = await window.App.confirm(
        "Удалить задание?",
        `Задание #${id} исчезнет из модуля после сохранения. Ответы учеников на него останутся в журнале, а в разделе должно остаться не меньше двух заданий.`,
        { label: "Удалить", danger: true }
      );
      if (!confirmed) return;
      model.items = model.items.filter((entry) => entry.id !== id);
      card.remove();
      notify(`Задание #${id} удалено — не забудьте сохранить модуль.`);
    }

    async function saveModule(button) {
      // Вопрос об уровне — единственный обязательный текст анкеты: без него
      // ученику нечего показать в первом вопросе.
      const question = surveyHost && surveyHost.querySelector('[data-field="survey_level_question"]');
      if (question && !question.value.trim()) {
        notify("Заполните вопрос об общем уровне анкеты.", { error: true });
        question.focus();
        return;
      }
      await guard(button, async () => {
        try {
          const data = await request(api, { method: "PUT", body: model });
          errorBox.hidden = true;
          showWarnings(data.warnings || []);
          notify(data.warnings && data.warnings.length ? "Модуль сохранён, но есть замечания." : "Модуль сохранён.");
        } catch (problem) {
          fail(problem);
        }
      });
    }

    function showWarnings(list) {
      showList(warningsBox, "Чего не хватает модулю:", list);
    }

    /* переделка, проверка и удаление модуля */

    function showRun(run) {
      if (!run) {
        runBox.hidden = true;
        return;
      }
      runBox.hidden = false;
      const label = RUN_LABELS[run.status] || run.status;
      runStep.textContent = run.current ? `${label}: ${run.current}` : label;
      const total = Number(run.total || 0);
      const doneCount = Number(run.done || 0);
      runCount.textContent = total ? `сделано ${doneCount} из ${total}` : "шагов пока нет";
      runFill.style.width = total ? `${Math.round((100 * doneCount) / total)}%` : "0%";
      runError.hidden = !run.error;
      runError.textContent = run.error || "";
      // Итог автопроверки код-заданий: какие задания придётся переписать.
      showList(runWarnings, "Эти задания надо переписать:", run.warnings || []);
    }

    /** Опрос прогона: пока он идёт, показываем текущий шаг; кончился — редактор
     *  перечитывает модуль. Упавший прогон показывает свой текст ошибки. */
    async function watch() {
      if (watching) return;
      watching = true;
      try {
        for (;;) {
          await sleep(POLL_MS);
          let data;
          try {
            data = await request(`${courseApi}/generation`);
          } catch (problem) {
            notify(problem.message, { error: true });
            return;
          }
          // Пакетный прогон ушёл на следующий модуль: этот уже готов —
          // перечитываем страницу, иначе редактор останется пустым.
          if (data.run && Number(data.run.module) !== moduleNumber) {
            location.reload();
            return;
          }
          showRun(data.run);
          if (data.run && TERMINAL.includes(data.run.status) && !data.generating) {
            location.reload();
            return;
          }
          if (!data.generating) {
            notify("Прогон прервался — запустите переделку заново.", { error: true });
            return;
          }
        }
      } finally {
        watching = false;
      }
    }

    /** Переделка части модуля: остальные шаги сервер берёт готовыми с диска. */
    async function regenerate(button, part, extra = {}) {
      const body = { module: moduleNumber, part, ...extra };
      await guard(button, async () => {
        try {
          const data = await request(`${courseApi}/generate`, { method: "POST", body });
          showRun(data.run);
          notify("Переделка пошла — остальное берётся готовым.");
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    const PART_TITLES = {
      theory: "конспект занятия",
      items: "задания раздела",
      final: "итоговое задание",
      survey: "анкету модуля",
    };

    async function regeneratePart(button, part, extra = {}) {
      const what = PART_TITLES[part] || "часть модуля";
      const confirmed = await window.App.confirm(
        `Перегенерировать ${what}?`,
        "Модель перепишет только эту часть, остальное возьмёт с диска. Несохранённые правки в редакторе потеряются.",
        { label: "Перегенерировать" }
      );
      if (!confirmed) return;
      await regenerate(button, part, extra);
    }

    /** Полная пересборка модуля: работает и без плана прогона (ручной модуль). */
    async function regenerateModule(button) {
      const confirmed = await window.App.confirm(
        "Перегенерировать модуль?",
        "Модель перепишет план модуля и всё его содержимое: занятия, разделы, задания и конспекты соберутся с нуля. Ваши правки в этом модуле потеряются.",
        { label: "Перегенерировать", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          const data = await request(`${courseApi}/generate`, {
            method: "POST",
            body: { module: moduleNumber, force: true },
          });
          showRun(data.run);
          notify("Перегенерация модуля пошла.");
          watch();
        } catch (problem) {
          fail(problem);
        }
      });
    }

    function problemRow(problem) {
      const row = make("li", "check-problem");
      const head = make("div", "check-head");
      head.append(make("strong", "", `Задание ${problem.item_id}`));
      head.append(make("span", "check-prompt", `«${problem.prompt}»`));
      row.append(head);
      row.append(make("p", "check-detail", problem.detail ? `${problem.kind} — ${problem.detail}` : problem.kind));
      return row;
    }

    function showCheck(problems) {
      checkBox.replaceChildren();
      checkBox.className = "notice";
      checkBox.hidden = false;
      if (!problems.length) {
        checkBox.classList.add("success");
        checkBox.append(make("strong", "", "Все код-задания прошли проверку."));
        return;
      }
      checkBox.append(make("strong", "", `Проверка код-заданий нашла проблемы (${problems.length}):`));
      const list = make("ul", "warn-list");
      problems.forEach((problem) => list.append(problemRow(problem)));
      checkBox.append(list);
    }

    /** Проверка считает сервер (запуском), поэтому кнопка ждёт и говорит об этом. */
    async function checkModule(button) {
      const label = button.textContent;
      button.textContent = "Проверяем…";
      await guard(button, async () => {
        try {
          const data = await request(`${api}/check`);
          showCheck(data.problems || []);
        } catch (problem) {
          fail(problem);
        }
      });
      button.textContent = label;
    }

    async function deleteModule(button) {
      const confirmed = await window.App.confirm(
        "Удалить модуль?",
        "Модуль удалится из курса, остальные сдвинутся. Прогресс учеников по нему останется в журнале.",
        { label: "Удалить модуль", danger: true }
      );
      if (!confirmed) return;
      await guard(button, async () => {
        try {
          await request(api, { method: "DELETE" });
          location.assign(`/admin/courses/${encodeURIComponent(node.dataset.course)}`);
        } catch (problem) {
          fail(problem);
        }
      });
    }

    /** Последний прогон курса, если он про этот модуль: после перезагрузки
     *  страницы предупреждения автопроверки должны остаться на виду. */
    async function initRun() {
      let data;
      try {
        data = await request(`${courseApi}/generation`);
      } catch {
        return; // прогресс не обязателен: редактор работает и без него
      }
      const run = data.run;
      if (!run || Number(run.module) !== moduleNumber) return;
      showRun(run);
      if (data.generating) watch();
    }

    async function loadTheory(lessonId, area, state) {
      if (area.dataset.loaded === "1") return;
      state.textContent = "Загружаю конспект…";
      try {
        const data = await request(`${theoryUrl}/${encodeURIComponent(lessonId)}`);
        area.value = data.markdown || "";
        area.dataset.loaded = "1";
        state.textContent = "";
      } catch (problem) {
        state.textContent = problem.message;
      }
    }

    async function saveTheory(button) {
      const lessonId = button.dataset.lesson;
      const details = button.closest("details");
      const area = details.querySelector("textarea");
      const state = details.querySelector(".theory-state");
      await guard(button, async () => {
        try {
          const data = await request(`${theoryUrl}/${encodeURIComponent(lessonId)}`, {
            method: "PUT",
            body: { markdown: area.value },
          });
          area.dataset.loaded = "1";
          const headings = data.headings || [];
          state.textContent = headings.length
            ? `Сохранено. Заголовки разделов: ${headings.join(", ")}.`
            : "Сохранено, но заголовков «## » в конспекте нет.";
          notify("Конспект сохранён.");
        } catch (problem) {
          state.textContent = problem.message;
          notify(problem.message, { error: true });
        }
      });
    }

    async function load() {
      try {
        const data = await request(api);
        model = data.module;
        render();
        loading.hidden = true;
        editor.hidden = false;
      } catch (problem) {
        loading.hidden = true;
        fail(problem);
      }
    }

    editor.addEventListener("input", onEdit);
    editor.addEventListener("change", onEdit);
    // Один слушатель на весь экран: кнопки модуля стоят рядом с редактором.
    onActions(node, {
      "save-module": saveModule,
      "add-lesson": addLesson,
      "move-lesson": moveLesson,
      "add-section": addSection,
      "remove-section": removeSection,
      "remove-lesson": removeLesson,
      "add-item": addItem,
      "remove-item": removeItem,
      "add-block": addBlock,
      "remove-block": removeBlock,
      "move-block": moveBlock,
      "save-theory": saveTheory,
      "regenerate-plan": regenerateModule,
      "regenerate-theory": (button) => regeneratePart(button, "theory", { lesson: button.dataset.lesson }),
      "regenerate-final": (button) => regeneratePart(button, "final", { lesson: button.dataset.lesson }),
      "regenerate-items": (button) =>
        regeneratePart(button, "items", { lesson: button.dataset.lesson, section: button.dataset.section }),
      "regenerate-survey": (button) => regeneratePart(button, "survey"),
      "check-module": checkModule,
      "delete-module": deleteModule,
    });
    load();
    initRun();
  }

  const SCREENS = { list: listScreen, course: courseScreen, module: moduleScreen };
  const start = SCREENS[root.dataset.screen];
  if (start) start(root);
})();
