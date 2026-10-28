/* Рабочая страница проекта: слева разговор с наставником, справа сводка проекта.
 *
 * Карточку идеи и итог этапа предлагает наставник прямо в чате: ученик берёт
 * предложение в карточку или подтверждает итог кнопкой, а правит в окне. Руками
 * заполняются только окна «Изменить» и «Поправить»: кнопки в них решает сервер
 * (422 со списком полей), устаревшая вкладка получит 409 и перечитает состояние.
 * Текст ученика и предложения наставника рисуются через textContent; HTML
 * приходит только отрендеренным сервером (реплики чата).
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
  const quick = $("#chat-quick");
  const quickButton = $("#quick-reply");
  const restart = $("#project-restart");
  const titleNode = $("#project-title");
  const metaNode = $("#project-meta");
  const stepsNode = $("#project-steps");
  const pane = $("#pane-summary");
  const cardDialog = $("#card-dialog");
  const stageDialog = $("#stage-dialog");

  const STATUS = { idea: "Идея", build: "Реализация", done: "Готов" };
  const EMPTY_CHAT = "Расскажите наставнику, что вам интересно, — вместе сделаем из описания вашу задачу.";
  const NOT_SET = "пока не задано";

  let project = null;
  let briefs = [];
  let unlocked = false;
  let busy = false;

  /* --- Запросы --- */

  async function act(button, task) {
    if (busy) return;
    busy = true;
    if (button) button.disabled = true;
    try {
      await task();
    } catch (problem) {
      if (!problem.handled) notify(problem.message, { error: true });
      if (problem.status === 409 || problem.status === 404) await load();
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  function setProject(next) {
    project = next;
    render();
    renderLog();
  }

  /* --- Поля в окнах --- */

  function fieldControl(field, value) {
    const id = `dlg-${field.key}`;
    const multiline = field.key !== "title";
    const control = el(multiline ? "textarea" : "input", {
      id,
      name: field.key,
      maxlength: field.max_chars,
      rows: multiline ? (field.key === "code" ? 5 : 3) : undefined,
      type: multiline ? undefined : "text",
      class: field.key === "code" ? "code-field" : undefined,
      "aria-describedby": `${id}-hint`,
    });
    control.value = value;
    control.addEventListener("input", () => {
      control.removeAttribute("aria-invalid");
      control.closest(".project-field").querySelector(".field-error").hidden = true;
    });
    const hint = field.required ? `обязательно, не короче ${field.min_chars} знаков` : "по желанию";
    return el(
      "div",
      { class: "project-field", "data-key": field.key },
      el("label", { for: id, text: field.label }),
      control,
      el("p", { class: "field-hint muted", id: `${id}-hint`, text: hint }),
      el("p", { class: "field-error", hidden: true })
    );
  }

  function fillFields(host, fields, values) {
    host.replaceChildren(...fields.map((field) => fieldControl(field, values[field.key] ?? field.value ?? "")));
  }

  function readFields(host, fields) {
    return Object.fromEntries(fields.map((field) => [field.key, host.querySelector(`[name="${field.key}"]`).value]));
  }

  function markMissing(host, missing) {
    for (const key of missing) {
      const wrap = host.querySelector(`[data-key="${key}"]`);
      if (!wrap) continue;
      wrap.querySelector("input, textarea").setAttribute("aria-invalid", "true");
      const error = wrap.querySelector(".field-error");
      error.textContent = "Заполните это поле по правилам.";
      error.hidden = false;
    }
    host.querySelector('[aria-invalid="true"]')?.focus();
  }

  function needMissing(problem, host) {
    if (problem.status === 422 && problem.detail?.missing) {
      markMissing(host, problem.detail.missing);
      const stop = new Error(problem.message);
      stop.handled = true;
      notify(problem.message, { error: true });
      return stop;
    }
    return problem;
  }

  for (const dialog of [cardDialog, stageDialog]) {
    dialog.querySelector('[data-action="cancel"]').addEventListener("click", () => dialog.close());
  }

  /* --- Окно карточки («Изменить») --- */

  function openCard(missing = []) {
    fillFields($("#card-fields"), project.card, {});
    cardDialog.showModal();
    if (missing.length) markMissing($("#card-fields"), missing);
  }

  $("#card-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act(null, async () => {
      try {
        const data = await request(`${api}/card`, {
          method: "PUT",
          body: { fields: readFields($("#card-fields"), project.card) },
        });
        cardDialog.close();
        setProject(data.project);
        notify("Карточка сохранена.");
      } catch (problem) {
        throw needMissing(problem, $("#card-fields"));
      }
    });
  });

  /* --- Окно итога этапа («Поправить», «Записать итог самому») --- */

  let dialogStage = null;

  function openStage(stage, values = {}, missing = []) {
    dialogStage = stage;
    $("#stage-dialog-title").textContent = `Итог этапа «${stage.title}»`;
    $("#stage-dialog-goal").textContent = stage.goal;
    fillFields($("#stage-fields"), stage.fields, values);
    stageDialog.showModal();
    if (missing.length) markMissing($("#stage-fields"), missing);
  }

  $("#stage-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const stage = dialogStage;
    act(null, async () => {
      try {
        await request(`${api}/notes`, {
          method: "PUT",
          body: { stage: stage.id, fields: readFields($("#stage-fields"), stage.fields) },
        });
        const data = await request(`${api}/complete`, { method: "POST", body: { stage: stage.id } });
        stageDialog.close();
        setProject(data.project);
        titleNode.focus();
        notify(data.project.status === "done" ? "Проект готов!" : "Этап выполнен.");
      } catch (problem) {
        throw needMissing(problem, $("#stage-fields"));
      }
    });
  });

  /* --- Шапка: полоса этапов --- */

  function stepItem(title, status, label) {
    return el(
      "li",
      { class: "project-step", "data-status": status, "aria-current": status === "current" ? "step" : undefined },
      el("span", { class: "step-dot", "aria-hidden": "true" }),
      el("span", { class: "step-title", text: title }),
      el("span", { class: "visually-hidden", text: ` — ${label}` })
    );
  }

  function renderSteps() {
    stepsNode.replaceChildren();
    stepsNode.hidden = project === null;
    if (project === null) return;
    const label = { done: "пройдено", current: "сейчас", ahead: "впереди" };
    const idea = project.status === "idea" ? "current" : "done";
    stepsNode.append(stepItem("Идея", idea, label[idea]));
    for (const stage of project.stages) {
      stepsNode.append(stepItem(stage.title, stage.status, label[stage.status]));
    }
  }

  /* --- Сводка справа --- */

  function factRow(label, value, code = false) {
    return [
      el("dt", { text: label }),
      el("dd", { class: `${value ? "" : "muted"}${code ? " code-field" : ""}`.trim(), text: value || NOT_SET }),
    ];
  }

  function cardSection() {
    const section = el(
      "section",
      { class: "summary-block project-card", "aria-labelledby": "card-title" },
      el("h2", { id: "card-title", text: "Карточка проекта" })
    );
    const list = el("dl", { class: "project-facts" });
    for (const field of project.card) list.append(...factRow(field.label, field.value));
    section.append(list);
    if (project.status === "idea") {
      const edit = el("button", { class: "btn", type: "button", "data-action": "edit-card" }, "Изменить");
      const approve = el("button", { class: "btn primary", type: "button", "data-action": "approve" }, "Утвердить идею");
      approve.disabled = !project.can_approve;
      edit.addEventListener("click", () => openCard());
      approve.addEventListener("click", () =>
        act(approve, async () => {
          try {
            const data = await request(`${api}/approve`, { method: "POST" });
            setProject(data.project);
            titleNode.focus();
            notify("Идея утверждена. Можно переходить к этапам.");
          } catch (problem) {
            if (problem.status === 422 && problem.detail?.missing) {
              notify(problem.message, { error: true });
              openCard(problem.detail.missing);
              problem.handled = true;
            }
            throw problem;
          }
        })
      );
      section.append(el("div", { class: "project-buttons" }, edit, approve));
    }
    return section;
  }

  function currentStageSection(stage) {
    const section = el(
      "section",
      { class: "summary-block project-stage", "data-status": "current", "aria-labelledby": `stage-${stage.id}` },
      el("h2", { id: `stage-${stage.id}`, text: `Сейчас: ${stage.title}` }),
      el("p", { class: "stage-goal", text: stage.goal })
    );
    const checklist = el("ul", { class: "stage-checklist" });
    for (const item of stage.checklist) checklist.append(el("li", { text: item }));
    const own = el("button", { class: "btn", type: "button", "data-action": "own-summary" }, "Записать итог самому");
    own.addEventListener("click", () => openStage(stage));
    section.append(checklist, el("p", { class: "muted stage-hint", text: "Когда этап сделан, наставник предложит итог — подтвердите его в чате." }), el("div", { class: "project-buttons" }, own));
    return section;
  }

  function doneStageBlock(stage) {
    const details = el(
      "details",
      { class: "stage-done" },
      el("summary", {}, el("span", { class: "stage-mark", "aria-hidden": "true", text: "✔" }), ` ${stage.title}`)
    );
    const list = el("dl", { class: "project-facts" });
    for (const field of stage.fields) {
      if (field.value) list.append(...factRow(field.label, field.value, field.key === "code"));
    }
    details.append(list.children.length ? list : el("p", { class: "muted", text: "Заметок нет." }));
    return details;
  }

  function stagesSection() {
    const section = el("section", { class: "summary-block project-stages", "aria-label": "Этапы" });
    for (const stage of project.stages) {
      if (stage.status === "done") section.append(doneStageBlock(stage));
      else if (stage.status === "ahead") {
        section.append(el("p", { class: "stage-ahead muted" }, el("span", { class: "stage-mark", "aria-hidden": "true", text: "○" }), ` ${stage.title}`));
      }
    }
    return section.children.length ? section : null;
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
            setProject(data.project);
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
    root.dataset.mode = project === null ? "choose" : "work";
    if (project === null) {
      titleNode.textContent = "Проект";
      metaNode.replaceChildren();
      restart.hidden = true;
      content.append(chooser());
    } else {
      titleNode.textContent = project.title;
      const current = project.stages.find((s) => s.id === project.stage);
      metaNode.replaceChildren(
        el("span", { class: `badge${project.status === "done" ? " success" : " primary"}`, text: STATUS[project.status] }),
        current ? ` Этап ${project.stages.indexOf(current) + 1} из ${project.stages.length}` : ""
      );
      // Готовый проект на непройденном курсе остаётся: новый выбрать нельзя, сервер архив не даст.
      restart.hidden = project.status === "done" && !unlocked;
      restart.querySelector("span").textContent = project.status === "done" ? "Начать новый проект" : "Начать заново";
      content.append(
        el("details", { class: "project-brief" }, el("summary", { text: `Описание: ${project.brief.title}` }), briefBody(project.brief)),
        cardSection()
      );
      const active = project.status === "build" ? current : null;
      if (active) content.append(currentStageSection(active));
      const stages = stagesSection();
      if (stages) content.append(stages);
    }
    renderSteps();
    renderQuick();
    const chatOn = project !== null;
    input.disabled = !chatOn;
    form.querySelector("button[type=submit]").disabled = !chatOn;
    pane.scrollTop = scroll;
  }

  /* --- Чат --- */

  function renderQuick() {
    const text = project?.quick_reply ?? null;
    quick.hidden = !text;
    quickButton.textContent = text ?? "";
    quickButton.dataset.text = text ?? "";
  }

  quickButton.addEventListener("click", () => {
    const text = quickButton.dataset.text;
    if (text && !busy && project !== null) send(text);
  });

  function proposalBlock(message) {
    const proposal = message.proposal;
    const block = el("div", { class: "proposal", "data-kind": proposal.kind }, el("strong", { text: proposal.title }));
    const list = el("dl", { class: "project-facts" });
    for (const field of proposal.fields) list.append(el("dt", { text: field.label }), el("dd", { text: field.value }));
    block.append(list);
    if (!proposal.actionable) {
      block.append(el("p", { class: "muted proposal-stale", text: "Уже неактуально." }));
      return block;
    }
    const actions = el("div", { class: "proposal-actions" });
    if (proposal.kind === "card") {
      const take = el("button", { class: "btn", type: "button", "data-action": "take" }, "Взять в карточку");
      take.addEventListener("click", () =>
        act(take, async () => {
          const data = await request(`${api}/take`, { method: "POST", body: { turn_id: message.id } });
          setProject(data.project);
          notify("Перенесено в карточку. Проверьте и при необходимости поправьте.");
        })
      );
      actions.append(take);
    } else {
      const stage = project.stages.find((item) => item.id === proposal.stage);
      const values = Object.fromEntries(proposal.fields.map((field) => [field.key, field.value]));
      const accept = el("button", { class: "btn primary", type: "button", "data-action": "accept" }, "Подтвердить и дальше");
      const edit = el("button", { class: "btn", type: "button", "data-action": "edit" }, "Поправить");
      accept.addEventListener("click", () =>
        act(accept, async () => {
          try {
            const data = await request(`${api}/accept`, { method: "POST", body: { turn_id: message.id } });
            setProject(data.project);
            titleNode.focus();
            notify(data.project.status === "done" ? "Проект готов!" : "Этап выполнен.");
          } catch (problem) {
            if (problem.status === 422 && problem.detail?.missing && stage) {
              notify(problem.message, { error: true });
              openStage(stage, values, problem.detail.missing);
              problem.handled = true;
            }
            throw problem;
          }
        })
      );
      edit.addEventListener("click", () => stage && openStage(stage, values));
      actions.append(accept, edit);
    }
    block.append(actions);
    return block;
  }

  function messageNode(message) {
    const mine = message.role === "student";
    const bubble = el("div", { class: `msg ${mine ? "user" : "assistant"}` });
    const body = el("div", { class: "msg-body" });
    // HTML приходит только от сервера (безопасный Markdown, теги ученика экранированы).
    body.innerHTML = message.html;
    bubble.append(body);
    if (!mine && message.proposal && project) bubble.append(proposalBlock(message));
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
    quickButton.disabled = true;
    typing.hidden = false;
    try {
      const data = await request(`${api}/chat`, { method: "POST", body: { text } });
      // Свежее состояние целиком: старые предложения теряют кнопки, сводка не отстаёт.
      setProject(data.project);
    } catch (problem) {
      echo.classList.replace("pending", "failed");
      echo.append(el("p", { class: "msg-error", text: problem.message }));
      input.value = text;
      notify(problem.message, { error: true });
      if (problem.status === 409 || problem.status === 404) await load();
    } finally {
      typing.hidden = true;
      quickButton.disabled = false;
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
