/* Страница «Проект»: курсы с проектной работой и состояние проекта в каждом. */
(() => {
  const { $, request, notify, confirm } = window.App;
  const { el, briefCard } = window.ProjectUI;

  const root = $("#projects");
  const courseUrl = (id) => `/lesson/${encodeURIComponent(id)}`;
  const projectUrl = (id) => `/project/${encodeURIComponent(id)}`;
  const apiUrl = (id) => `/api/courses/${encodeURIComponent(id)}/project`;

  async function choose(course, brief, button) {
    button.disabled = true;
    try {
      await request(`${apiUrl(course.id)}/start`, { method: "POST", body: { brief_id: brief.id } });
      location.assign(projectUrl(course.id));
    } catch (problem) {
      notify(problem.message, { error: true });
      button.disabled = false;
    }
  }

  async function startNew(course, button) {
    const ok = await confirm("Начать новый проект?", "Готовый проект уйдёт в архив, а вы выберете описание заново.", {
      label: "Начать новый",
    });
    if (!ok) return;
    button.disabled = true;
    try {
      await request(`${apiUrl(course.id)}/archive`, { method: "POST" });
      location.assign(projectUrl(course.id));
    } catch (problem) {
      notify(problem.message, { error: true });
      button.disabled = false;
    }
  }

  function link(href, text, primary = true) {
    return el("a", { class: primary ? "btn primary" : "btn", href }, text);
  }

  function body(course) {
    const { progress, project } = course;
    const wrap = el("div", { class: "project-course-body" });
    if (course.state === "not_enrolled") {
      wrap.append(el("p", { class: "muted", text: "Сначала запишитесь на курс и пройдите его." }));
      wrap.append(el("div", { class: "brief-actions" }, link(courseUrl(course.id), "К курсу", false)));
    } else if (course.state === "locked") {
      wrap.append(
        el("p", { class: "project-locked", text: `Откроется после курса: пройдено ${progress.closed} из ${progress.total} занятий.` })
      );
      wrap.append(el("div", { class: "brief-actions" }, link(courseUrl(course.id), "К курсу", false)));
    } else if (course.state === "choose") {
      const grid = el("div", { class: "brief-grid" });
      for (const brief of course.briefs) grid.append(briefCard(brief, (item, button) => choose(course, item, button)));
      wrap.append(el("p", { class: "muted", text: "Выберите описание — идею вы доведёте до своей вместе с наставником." }), grid);
    } else if (course.state === "active") {
      wrap.append(
        el("p", {}, el("strong", { text: project.title }), el("span", { class: "muted", text: ` · ${project.status === "idea" ? "Идея" : `этап «${project.stage_title}»`}` }))
      );
      wrap.append(el("div", { class: "brief-actions" }, link(projectUrl(course.id), "Продолжить")));
    } else if (course.state === "done") {
      const actions = el("div", { class: "brief-actions" }, link(projectUrl(course.id), "Открыть", false));
      if (progress.total > 0 && progress.closed === progress.total) {
        const again = el("button", { class: "btn primary", type: "button" }, "Начать новый");
        again.addEventListener("click", () => startNew(course, again));
        actions.append(again);
      }
      wrap.append(el("p", {}, el("span", { class: "badge success", text: "Готов" }), ` ${project.title}`), actions);
    } else {
      wrap.append(el("p", { class: "notice error", text: "Курс сейчас недоступен. Сообщите администратору." }));
    }
    return wrap;
  }

  function render(courses) {
    root.replaceChildren();
    if (!courses.length) {
      root.append(el("div", { class: "panel empty" }, el("strong", { text: "Пока нет курсов с проектом" }), "Они появятся здесь."));
      return;
    }
    for (const course of courses) {
      const heading = el("h2", { class: "section-title", id: `project-course-${course.id}`, text: course.title });
      root.append(el("section", { class: "project-course panel", "aria-labelledby": heading.id }, heading, body(course)));
    }
  }

  async function load() {
    try {
      const data = await request("/api/projects");
      render(data.courses);
    } catch (problem) {
      root.replaceChildren(el("p", { class: "notice error", role: "alert", text: problem.message }));
    } finally {
      root.setAttribute("aria-busy", "false");
    }
  }

  document.addEventListener("DOMContentLoaded", load);
})();
