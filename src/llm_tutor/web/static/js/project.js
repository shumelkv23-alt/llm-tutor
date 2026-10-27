/* Рабочая страница проекта: карточка и этапы слева, чат с наставником справа.
 *
 * Кнопки «Утвердить идею» и «Этап выполнен» активны по правилам из ответа
 * сервера (`min_chars`), но решает всё равно сервер: устаревшая вкладка получит
 * 409 и перечитает состояние. Текст ученика и предложения наставника рисуются
 * через textContent; HTML приходит только отрендеренным сервером (реплики чата).
 */
(() => {
  const { $, request, notify, confirm } = window.App;
  const { el, briefBody, briefCard } = window.ProjectUI;

  const root = $("#project");
  const api = `/api/courses/${encodeURIComponent(root.dataset.course)}/project`;
  const content = $("#project-content");
  const log = $("#chat-log");
  const form = $("#chat-form");
  const input = $("#chat-input");
  const typing = $("#chat-typing");
  const restart = $("#project-restart");
  const titleNode = $("#project-title");
  const metaNode = $("#project-meta");
  const pane = $("#pane-left");

  const STATUS = { idea: "Идея", build: "Реализация", done: "Готов" };
  const STAGE_STATUS = { done: "выполнен", current: "текущий", ahead: "впереди" };
  const EMPTY_CHAT = "Расскажите наставнику, что вам интересно, — вместе сделаем из описания вашу задачу.";

  let project = null;
  let briefs = [];
  let unlocked = false;
  let busy = false;
  // Несохранённые правки полей: переживают перерисовку страницы («Взять в карточку»).
  const drafts = new Map();

  const draftKey = (scope, field) => `${scope}.${field.key}`;
  const valueOf = (scope, field) => (drafts.has(draftKey(scope, field)) ? drafts.get(draftKey(scope, field)) : field.value);
  const textLength = (value) => value.trim().length;

  function fieldsOk(scope, fields) {
    return fields.every((field) => {
      const length = textLength(valueOf(scope, field));
      return length >= (field.required ? field.min_chars : 0) && length <= field.max_chars;
    });
  }

  function dropDrafts(scope, fields) {
    for (const field of fields) drafts.delete(draftKey(scope, field));
  }

  function valuesOf(scope, fields) {
    return Object.fromEntries(fields.map((field) => [field.key, valueOf(scope, field)]));
  }

  /* --- Поля формы --- */

  function fieldControl(scope, field, refresh) {
    const id = `f-${scope.replace(/\./g, "-")}-${field.key}`;
    const multiline = field.key !== "title";
    const control = el(multiline ? "textarea" : "input", {
      id,
      name: field.key,
      maxlength: field.max_chars,
      rows: multiline ? (field.key === "code" ? 6 : 3) : undefined,
      type: multiline ? undefined : "text",
      class: field.key === "code" ? "code-field" : undefined,
      "aria-describedby": `${id}-hint`,
    });
    control.value = valueOf(scope, field);
    control.addEventListener("input", () => {
      drafts.set(draftKey(scope, field), control.value);
      control.removeAttribute("aria-invalid");
      refresh();
    });
    const hint = field.required ? `обязательно, не короче ${field.min_chars} знаков` : "по желанию";
    const wrap = el(
      "div",
      { class: "project-field", "data-key": field.key },
      el("label", { for: id, text: field.label }),
      control,
      el("p", { class: "field-hint muted", id: `${id}-hint`, text: hint }),
      el("p", { class: "field-error", id: `${id}-error`, hidden: true })
    );
    return wrap;
  }

  function markMissing(formNode, missing) {
    for (const key of missing) {
      const wrap = formNode.querySelector(`[data-key="${key}"]`);
      if (!wrap) continue;
      wrap.querySelector("input, textarea").setAttribute("aria-invalid", "true");
      const error = wrap.querySelector(".field-error");
      error.textContent = "Заполните это поле по правилам.";
      error.hidden = false;
    }
    formNode.querySelector('[aria-invalid="true"]')?.focus();
  }

  function readOnlyFields(fields) {
    const list = el("dl", { class: "project-facts" });
    for (const field of fields) {
      if (!field.value) continue;
      list.append(el("dt", { text: field.label }), el("dd", { class: field.key === "code" ? "code-field" : "", text: field.value }));
    }
    return list.children.length ? list : el("p", { class: "muted", text: "Заметок нет." });
  }

  /* --- Запросы --- */

  async function act(button, task) {
    if (busy) return;
    busy = true;
    button.disabled = true;
    try {
      await task();
    } catch (problem) {
      notify(problem.message, { error: true });
      if (problem.status === 409 || problem.status === 404) await load();
    } finally {
      busy = false;
      button.disabled = false;
    }
  }

  async function missingOr(formNode, call) {
    try {
      return await call();
    } catch (problem) {
      if (problem.status === 422 && problem.detail?.missing) {
        markMissing(formNode, problem.detail.missing);
        const stop = new Error(problem.message);
        stop.handled = true;
        throw stop;
      }
      throw problem;
    }
  }

  function setProject(next) {
    project = next;
    render();
    renderLog();
  }

  /* --- Левая панель --- */

  function briefSection() {
    return el(
      "details",
      { class: "project-brief" },
      el("summary", { text: `Описание: ${project.brief.title}` }),
      briefBody(project.brief)
    );
  }

  function cardSection() {
    const section = el("section", { class: "project-card", "aria-labelledby": "card-title" }, el("h2", { id: "card-title", text: "Карточка проекта" }));
    if (project.status !== "idea") {
      section.append(readOnlyFields(project.card));
      return section;
    }
    const formNode = el("form", { class: "card-form", novalidate: true });
    const save = el("button", { class: "btn", type: "button" }, "Сохранить карточку");
    const approve = el("button", { class: "btn primary", type: "button" }, "Утвердить идею");
    const refresh = () => {
      approve.disabled = !fieldsOk("card", project.card);
    };
    for (const field of project.card) formNode.append(fieldControl("card", field, refresh));
    refresh();
    const persist = () => request(`${api}/card`, { method: "PUT", body: { fields: valuesOf("card", project.card) } });
    save.addEventListener("click", () =>
      act(save, async () => {
        const data = await persist();
        dropDrafts("card", project.card);
        setProject(data.project);
        notify("Карточка сохранена.");
      })
    );
    approve.addEventListener("click", () =>
      act(approve, async () => {
        try {
          await persist();
          const data = await missingOr(formNode, () => request(`${api}/approve`, { method: "POST" }));
          dropDrafts("card", project.card);
          setProject(data.project);
          titleNode.focus();
          notify("Идея утверждена. Можно переходить к этапам.");
        } catch (problem) {
          if (!problem.handled) throw problem;
          notify(problem.message, { error: true });
        }
      })
    );
    formNode.append(el("div", { class: "project-buttons" }, save, approve));
    section.append(formNode);
    return section;
  }

  function stageSection(stage) {
    const section = el(
      "section",
      { class: "project-stage", "data-status": stage.status, "aria-labelledby": `stage-${stage.id}` },
      el(
        "h3",
        { id: `stage-${stage.id}` },
        stage.title,
        el("span", { class: `badge${stage.status === "done" ? " success" : stage.status === "current" ? " primary" : ""}`, text: STAGE_STATUS[stage.status] })
      )
    );
    if (stage.status === "done") {
      section.append(readOnlyFields(stage.fields));
    } else if (stage.status === "current" && project.status === "build") {
      section.append(currentStage(stage));
    }
    return section;
  }

  function currentStage(stage) {
    const scope = `notes.${stage.id}`;
    const wrap = el("div", { class: "stage-body" }, el("p", { class: "stage-goal", text: stage.goal }));
    const checklist = el("ul", { class: "stage-checklist" });
    for (const item of stage.checklist) checklist.append(el("li", { text: item }));
    wrap.append(checklist);
    const formNode = el("form", { class: "notes-form", novalidate: true });
    const save = el("button", { class: "btn", type: "button" }, "Сохранить");
    const complete = el("button", { class: "btn primary", type: "button" }, "Этап выполнен");
    const refresh = () => {
      complete.disabled = !fieldsOk(scope, stage.fields);
    };
    for (const field of stage.fields) formNode.append(fieldControl(scope, field, refresh));
    refresh();
    const persist = () =>
      request(`${api}/notes`, { method: "PUT", body: { stage: stage.id, fields: valuesOf(scope, stage.fields) } });
    save.addEventListener("click", () =>
      act(save, async () => {
        const data = await persist();
        dropDrafts(scope, stage.fields);
        setProject(data.project);
        notify("Заметки сохранены.");
      })
    );
    complete.addEventListener("click", () =>
      act(complete, async () => {
        try {
          await persist();
          const data = await missingOr(formNode, () =>
            request(`${api}/complete`, { method: "POST", body: { stage: stage.id } })
          );
          dropDrafts(scope, stage.fields);
          setProject(data.project);
          titleNode.focus();
          notify(data.project.status === "done" ? "Проект готов!" : "Этап выполнен.");
        } catch (problem) {
          if (!problem.handled) throw problem;
          notify(problem.message, { error: true });
        }
      })
    );
    formNode.append(el("div", { class: "project-buttons" }, save, complete));
    wrap.append(formNode);
    return wrap;
  }

  function chooser() {
    const wrap = el("div", { class: "project-choose" });
    if (!unlocked) {
      wrap.append(el("p", { class: "notice", text: "Выбрать проект можно после прохождения курса." }));
      return wrap;
    }
    wrap.append(
      el("h2", { text: "Выберите описание проекта" }),
      el("p", { class: "muted", text: "Идею вы доведёте до своей вместе с наставником." })
    );
    const grid = el("div", { class: "brief-grid" });
    for (const brief of briefs) {
      grid.append(
        briefCard(brief, (item, button) =>
          act(button, async () => {
            const data = await request(`${api}/start`, { method: "POST", body: { brief_id: item.id } });
            project = data.project;
            render();
            renderLog();
            titleNode.focus();
          })
        )
      );
    }
    wrap.append(grid);
    return wrap;
  }

  function render() {
    const scroll = pane.scrollTop;
    content.replaceChildren();
    if (project === null) {
      titleNode.textContent = "Проект";
      metaNode.replaceChildren();
      restart.hidden = true;
      content.append(chooser());
    } else {
      titleNode.textContent = project.title;
      metaNode.replaceChildren(
        el("span", { class: `badge${project.status === "done" ? " success" : " primary"}`, text: STATUS[project.status] }),
        project.stage ? ` Этап: ${project.stages.find((s) => s.id === project.stage).title}` : ""
      );
      // Готовый проект на непройденном курсе остаётся: новый выбрать нельзя, сервер архив не даст.
      restart.hidden = project.status === "done" && !unlocked;
      restart.querySelector("span").textContent = project.status === "done" ? "Начать новый проект" : "Начать заново";
      content.append(briefSection(), cardSection());
      for (const stage of project.stages) {
        content.append(stageSection(stage));
      }
    }
    const chatOn = project !== null;
    input.disabled = !chatOn;
    form.querySelector("button[type=submit]").disabled = !chatOn;
    pane.scrollTop = scroll;
  }

  /* --- Чат --- */

  function proposalBlock(message) {
    const block = el("div", { class: "proposal" }, el("strong", { text: "Наставник предлагает для карточки:" }));
    const list = el("dl", { class: "project-facts" });
    for (const [key, value] of Object.entries(message.proposal)) {
      const field = project.card.find((item) => item.key === key);
      if (field) list.append(el("dt", { text: field.label }), el("dd", { text: value }));
    }
    const take = el("button", { class: "btn", type: "button" }, "Взять в карточку");
    take.addEventListener("click", () =>
      act(take, async () => {
        const data = await request(`${api}/take`, { method: "POST", body: { turn_id: message.id } });
        for (const key of Object.keys(message.proposal)) drafts.delete(`card.${key}`);
        setProject(data.project);
        notify("Перенесено в карточку. Проверьте и при необходимости поправьте.");
      })
    );
    block.append(list, take);
    return block;
  }

  function messageNode(message) {
    const mine = message.role === "student";
    const bubble = el("div", { class: `msg ${mine ? "user" : "assistant"}` });
    const body = el("div", { class: "msg-body" });
    // HTML приходит только от сервера (безопасный Markdown, теги ученика экранированы).
    body.innerHTML = message.html;
    bubble.append(body);
    if (!mine && message.proposal && project?.status === "idea") bubble.append(proposalBlock(message));
    return bubble;
  }

  function pendingNode(text) {
    const bubble = el("div", { class: "msg user pending" });
    bubble.append(el("div", { class: "msg-body", text }));
    return bubble;
  }

  function renderLog() {
    log.replaceChildren();
    const messages = project?.messages ?? [];
    if (!messages.length) {
      log.append(el("p", { class: "chat-empty", text: project ? EMPTY_CHAT : "Чат откроется после выбора проекта." }));
      return;
    }
    for (const message of messages) log.append(messageNode(message));
    log.scrollTop = log.scrollHeight;
  }

  async function send(text) {
    const echo = pendingNode(text);
    log.querySelector(".chat-empty")?.remove();
    log.append(echo);
    log.scrollTop = log.scrollHeight;
    input.value = "";
    input.disabled = true;
    typing.hidden = false;
    try {
      const data = await request(`${api}/chat`, { method: "POST", body: { text } });
      echo.remove();
      project = data.project;
      for (const message of data.messages) log.append(messageNode(message));
      log.scrollTop = log.scrollHeight;
    } catch (problem) {
      echo.classList.replace("pending", "failed");
      echo.append(el("p", { class: "msg-error", text: problem.message }));
      input.value = text;
      notify(problem.message, { error: true });
      if (problem.status === 409 || problem.status === 404) await load();
    } finally {
      typing.hidden = true;
      input.disabled = project === null;
      if (project !== null) input.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value;
    if (!text.trim() || busy || project === null) return;
    send(text);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  /* --- Начать заново, переключатель на узком экране --- */

  restart.addEventListener("click", async () => {
    const done = project?.status === "done";
    const ok = await confirm(
      done ? "Начать новый проект?" : "Начать проект заново?",
      done
        ? "Готовый проект уйдёт в архив, а вы выберете описание заново."
        : unlocked
          ? "Текущий проект уйдёт в архив: карточка и заметки не сохранятся на странице."
          : "Текущий проект уйдёт в архив, а новый можно будет выбрать только после прохождения курса.",
      { label: done ? "Начать новый" : "Начать заново", danger: !done }
    );
    if (!ok) return;
    await act(restart, async () => {
      await request(`${api}/archive`, { method: "POST" });
      drafts.clear();
      await load();
    });
  });

  const tabs = Array.from(document.querySelectorAll(".view-switch [role=tab]"));

  function showView(name) {
    root.dataset.view = name;
    for (const tab of tabs) {
      const on = tab.dataset.view === name;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    }
  }

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => showView(tab.dataset.view));
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      showView(next.dataset.view);
      next.focus();
    });
  });

  /* --- Загрузка --- */

  async function load() {
    try {
      const data = await request(api);
      project = data.project;
      briefs = data.briefs;
      unlocked = data.unlocked;
      render();
      renderLog();
    } catch (problem) {
      content.replaceChildren(el("p", { class: "notice error", role: "alert", text: problem.message }));
    } finally {
      root.setAttribute("aria-busy", "false");
    }
  }

  document.addEventListener("DOMContentLoaded", load);
})();
