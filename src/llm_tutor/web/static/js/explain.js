/* «Объясни новичку» (срез 88): ученик объясняет пройденный модуль новичку.
 *
 * Реплики собеседника приходят с сервера уже отрисованными (безопасный
 * Markdown) и вставляются как HTML; свой текст ученик видит до ответа сервера
 * только через textContent. Отчёт рисуется только через textContent.
 * Ширину полоски покрытия ставит JS (CSSOM): CSP запрещает style="".
 */
(() => {
  const { $, request, notify, confirm } = window.App;
  const root = $("#explain");
  if (!root) return;

  const course = root.dataset.course;
  const API = `/api/courses/${encodeURIComponent(course)}/explain`;
  const WHO = { student: "Ты", novice: "🐣 Новичок", mentor: "🧑‍🏫 Наставник" };
  const REASONS = {
    not_passed: "сначала пройди модуль целиком",
    no_reference: "для модуля ещё нет эталона",
    soon: "модуль скоро откроется",
  };
  const PITFALLS = {
    named: "назвал сам",
    fixed: "исправил ошибку новичка",
    missed: "пропустил ошибку новичка",
  };

  const pick = $("#explain-pick");
  const modules = $("#explain-modules");
  const chat = $("#explain-chat");
  const lead = $("#explain-lead");
  const progressLabel = $("#explain-progress-label");
  const barFill = $("#explain-bar-fill");
  const log = $("#explain-log");
  const form = $("#explain-form");
  const input = $("#explain-input");
  const mentorButton = $("#explain-mentor");
  const finishButton = $("#explain-finish");
  const reportPanel = $("#explain-report");
  const reportBody = $("#explain-report-body");
  const historyPanel = $("#explain-history-panel");
  const history = $("#explain-history");
  const share = $("#explain-share");

  let session = null;
  let busy = false;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function bubble(message) {
    const node = element("div", undefined, `msg ${message.role === "student" ? "user" : "assistant"}`);
    node.append(element("span", WHO[message.role] || "", "msg-who"));
    const body = document.createElement("div");
    // HTML — результат web.markdown.render на сервере (сырой HTML там выключен).
    body.innerHTML = message.html;
    node.append(body);
    return node;
  }

  function pendingBubble(text) {
    const node = element("div", undefined, "msg user pending");
    node.append(element("span", WHO.student, "msg-who"), element("p", text));
    return node;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function setBusy(flag) {
    busy = flag;
    root.setAttribute("aria-busy", String(flag));
    for (const control of [input, mentorButton, finishButton, form.querySelector("button")]) {
      control.disabled = flag;
    }
  }

  function renderProgress(view) {
    const { done, total } = view.progress;
    progressLabel.textContent = `объяснено ${done} из ${total} ключевых идей`;
    barFill.style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
  }

  function renderSession(view) {
    session = view;
    pick.hidden = true;
    chat.hidden = false;
    lead.textContent = `Ты объясняешь модуль ${view.topic} «${view.title}» новичку. Он знает только прошлые модули.`;
    log.replaceChildren(...view.messages.map(bubble));
    renderProgress(view);
    const active = view.status === "active";
    form.hidden = !active;
    mentorButton.hidden = !active;
    finishButton.hidden = !active;
    mentorButton.textContent = view.mode === "mentor" ? "Вернуться к новичку" : "Позвать наставника";
    finishButton.classList.toggle("highlight", Boolean(view.wrap_up));
    if (view.report) renderReport(view.report, view.lessons || {});
    scrollToEnd();
  }

  function list(title, items) {
    if (!items.length) return [];
    const ul = document.createElement("ul");
    ul.append(...items);
    return [element("h3", title), ul];
  }

  function ideaItem(idea, lessons) {
    const li = element("li");
    li.append(element("strong", lessons[idea.lesson] || idea.lesson));
    if (idea.quote) li.append(document.createTextNode(` — ты сказал: «${idea.quote}»`));
    if (idea.reference) li.append(element("span", `Как правильно: ${idea.reference}`, "reference"));
    return li;
  }

  function renderReport(report, lessons) {
    reportPanel.hidden = false;
    const summary = element(
      "p",
      `Обязательных идей объяснено верно: ${report.required_done} из ${report.required_total}; ` +
        `всего — ${report.ideas_done} из ${report.ideas_total}.`
    );
    const wrong = report.ideas.filter((idea) => idea.state === "wrong").map((i) => ideaItem(i, lessons));
    const gaps = report.ideas
      .filter((idea) => idea.state === "missing" || idea.state === "partial")
      .map((i) => ideaItem(i, lessons));
    const pitfalls = report.pitfalls.map((pitfall) => {
      const li = element("li", `${lessons[pitfall.lesson] || pitfall.lesson}: ${PITFALLS[pitfall.outcome]}`);
      if (pitfall.fix) li.append(element("span", `Почему это ошибка: ${pitfall.fix}`, "reference"));
      return li;
    });
    const repeat = report.review_lessons.map((lesson) => {
      const li = element("li");
      const link = element("a", lessons[lesson] || lesson);
      link.href = `/lesson/${encodeURIComponent(course)}`;
      li.append(link);
      return li;
    });
    const facts = [`реплик: ${report.student_turns}`];
    if (report.mentor_used) facts.push("звал наставника");
    if (report.pasted_turns) facts.push(`похоже на вставку: ${report.pasted_turns}`);
    reportBody.replaceChildren(
      summary,
      ...list("Ошибки", wrong),
      ...list("Пробелы", gaps),
      ...list("Загвоздки", pitfalls),
      ...list("Повторить", repeat),
      element("p", facts.join(" · "), "muted")
    );
  }

  function renderModules(rows) {
    const wanted = Number(root.dataset.topic) || null;
    modules.replaceChildren(
      ...rows.map((row) => {
        const li = element("li");
        li.append(element("span", `Модуль ${row.topic} · ${row.title}`));
        if (row.available) {
          const button = element("button", "Начать", `btn${row.topic === wanted ? " primary" : ""}`);
          button.type = "button";
          button.addEventListener("click", () => start(row.topic));
          li.append(button);
        } else {
          li.append(element("span", REASONS[row.reason] || "", "muted"));
        }
        return li;
      })
    );
    pick.hidden = false;
    chat.hidden = true;
  }

  function renderHistory(rows) {
    historyPanel.hidden = !rows.length;
    history.replaceChildren(
      ...rows.map((row) => {
        const li = element("li");
        const link = element("a", `Модуль ${row.topic} · ${row.title}`);
        link.href = `/lesson/${encodeURIComponent(course)}/explain/${row.id}`;
        const when = new Date(row.started_at * 1000).toLocaleString("ru-RU");
        const coverage = row.coverage ? ` · ${row.coverage.done} из ${row.coverage.total}` : "";
        li.append(link, element("span", `${when}${coverage}`, "muted"));
        return li;
      })
    );
  }

  async function guarded(action) {
    if (busy) return null;
    setBusy(true);
    try {
      return await action();
    } catch (problem) {
      notify(problem.message, { error: true });
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function start(topic) {
    const data = await guarded(() => request(`${API}/start`, { method: "POST", body: { topic } }));
    if (data) renderSession(data.session);
  }

  async function send(text) {
    if (busy || !session) return;
    const echo = pendingBubble(text);
    log.append(echo);
    scrollToEnd();
    input.value = "";
    setBusy(true);
    try {
      const data = await request(`${API}/turn`, {
        method: "POST",
        body: { session_id: session.id, text },
      });
      renderSession(data.session);
    } catch (problem) {
      echo.remove();
      // Реплика не записана: возвращаем её в поле, чтобы отправить снова.
      if (!input.value) input.value = text;
      notify(problem.message, { error: true });
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (text) send(text);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  mentorButton.addEventListener("click", async () => {
    if (!session) return;
    const on = session.mode !== "mentor";
    const data = await guarded(() =>
      request(`${API}/mentor`, { method: "POST", body: { session_id: session.id, on } })
    );
    if (data) renderSession(data.session);
  });

  finishButton.addEventListener("click", async () => {
    if (!session) return;
    if (!session.wrap_up && !(await confirm("Завершить объяснение и получить отчёт?"))) return;
    const data = await guarded(() =>
      request(`${API}/finish`, { method: "POST", body: { session_id: session.id } })
    );
    if (!data) return;
    if (data.session) renderSession(data.session);
    else {
      notify("Сессия была пустой — отчёта нет.");
      await load();
    }
  });

  share.addEventListener("change", async () => {
    const wanted = share.checked;
    const data = await guarded(() =>
      request(`${API}/settings`, { method: "POST", body: { share: wanted } })
    );
    share.checked = data ? data.share : !wanted;
  });

  async function load() {
    try {
      const overview = await request(API);
      share.checked = overview.share;
      renderHistory(overview.history);
      const sessionId = root.dataset.session;
      if (sessionId) {
        const data = await request(`${API}/sessions/${encodeURIComponent(sessionId)}`);
        renderSession(data.session);
      } else if (overview.active) {
        renderSession(overview.active);
      } else {
        reportPanel.hidden = true;
        renderModules(overview.modules);
      }
    } catch (problem) {
      notify(problem.message, { error: true });
    } finally {
      root.setAttribute("aria-busy", "false");
    }
  }

  load();
})();
