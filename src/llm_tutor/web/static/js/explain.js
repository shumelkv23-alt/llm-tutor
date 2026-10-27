/* Вкладка «Объясни» (срезы 88, 95): ученик объясняет занятие новичку.
 *
 * Состояние — блок `state.explain` (источник правды — сервер). Ходы идут через
 * общий `act` из lesson.js; наставник — это правый чат занятия (chat.js).
 * Реплики собеседника приходят с сервера уже отрисованными (безопасный
 * Markdown) и вставляются как HTML; свой текст ученик видит до ответа сервера
 * только через textContent, отчёт рисуется только через textContent.
 * Ширину полоски покрытия ставит JS (CSSOM): CSP запрещает style="".
 */
(() => {
  const panel = document.getElementById("tab-explain");
  const dot = document.getElementById("explain-dot");
  if (!panel) return;
  const { request, notify, confirm } = window.App;

  const WHO = { student: "Ты", novice: "🐣 Новичок" };
  const PITFALLS = {
    named: "назвал сам",
    fixed: "исправил ошибку новичка",
    missed: "пропустил ошибку новичка",
    unclear: "ошибку новичка так и не разобрали",
  };
  const KINDS = { required: "обязательное", voluntary: "добровольное" };
  const SAVED = "Сохраняются реплики сессии, время ходов и отчёт.";

  // Черновик реплики и реплика «в пути» живут между перерисовками: панель
  // строится заново на каждом ходе, и без этого поле ввода сбрасывалось бы.
  let draft = "";
  let sending = null;
  // Сессия из истории, открытая для чтения (null — обычный вид вкладки).
  let viewing = null;
  // Реплика ушла из поля ввода: после ответа возвращаем туда фокус (поле на время
  // хода выключено, и браузер его теряет).
  let refocus = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, className, onClick) {
    const node = el("button", `btn ${className}`.trim(), label);
    node.type = "button";
    node.dataset.turn = "";
    node.addEventListener("click", onClick);
    return node;
  }

  function plural(count, one, few, many) {
    const rest = count % 100;
    if (rest >= 11 && rest <= 14) return many;
    const last = count % 10;
    if (last === 1) return one;
    return last >= 2 && last <= 4 ? few : many;
  }

  function bubble(message) {
    const node = el("div", `msg ${message.role === "student" ? "user" : "assistant"}`);
    node.append(el("span", "msg-who", WHO[message.role] || ""));
    const body = el("div");
    // HTML — результат web.markdown.render на сервере (сырой HTML там выключен).
    body.innerHTML = message.html;
    node.append(body);
    return node;
  }

  function pendingBubble(text) {
    const node = el("div", "msg user pending");
    node.append(el("span", "msg-who", WHO.student), el("p", "", text));
    return node;
  }

  function logOf(messages, { pendingText = null } = {}) {
    const log = el("div", "chat-log explain-log");
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-label", "Разговор с новичком");
    messages.forEach((message) => log.append(bubble(message)));
    if (pendingText !== null) {
      log.append(pendingBubble(pendingText));
      const typing = el("p", "chat-typing", "🐣 Новичок печатает…");
      log.append(typing);
    }
    return log;
  }

  function progressOf(session) {
    const { done, total } = session.progress;
    const box = el("div", "explain-progress");
    const bar = el("div", "explain-bar");
    bar.setAttribute("aria-hidden", "true");
    const fill = el("span", "explain-bar-fill");
    fill.style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
    bar.append(fill);
    const left = session.turns_left;
    box.append(
      bar,
      el(
        "span",
        "muted",
        `объяснено ${done} из ${total} · осталось ${left} ${plural(left, "реплика", "реплики", "реплик")}`
      )
    );
    return box;
  }

  /* --- Отчёт: после сессии эталон открыт ученику --- */

  function list(title, items) {
    if (!items.length) return [];
    const ul = el("ul");
    ul.append(...items);
    return [el("h3", "", title), ul];
  }

  function ideaItem(idea, lead) {
    const item = el("li");
    item.append(el("strong", "", lead));
    if (idea.quote) item.append(document.createTextNode(` «${idea.quote}»`));
    if (idea.reference) item.append(el("span", "reference", `Как правильно: ${idea.reference}`));
    return item;
  }

  function reportOf(report, names) {
    const box = el("section", "explain-report");
    box.append(
      el("h3", "", "Отчёт"),
      el(
        "p",
        "",
        `Обязательных идей объяснено верно: ${report.required_done} из ${report.required_total}; ` +
          `всего — ${report.ideas_done} из ${report.ideas_total}.`
      )
    );
    const wrong = report.ideas
      .filter((idea) => idea.state === "wrong")
      .map((idea) => ideaItem(idea, "Неверно. Ты сказал:"));
    const gaps = report.ideas
      .filter((idea) => idea.state === "missing" || idea.state === "partial")
      .map((idea) => ideaItem(idea, idea.state === "partial" ? "Не до конца." : "Не прозвучало."));
    const pitfalls = report.pitfalls.map((pitfall) => {
      const item = el("li", "", PITFALLS[pitfall.outcome]);
      if (pitfall.fix) item.append(el("span", "reference", `Почему это ошибка: ${pitfall.fix}`));
      return item;
    });
    const repeat = report.review_lessons.map((lesson) => el("li", "", names[lesson] || lesson));
    const facts = [`реплик: ${report.student_turns}`];
    if (report.mentor_used) facts.push("спрашивал наставника");
    if (report.pasted_turns) facts.push(`похоже на вставку: ${report.pasted_turns}`);
    box.append(
      ...list("Ошибки", wrong),
      ...list("Пробелы", gaps),
      ...list("Загвоздки", pitfalls),
      ...list("Повторить", repeat),
      el("p", "muted", facts.join(" · "))
    );
    return box;
  }

  /* --- Идущая сессия --- */

  async function send(text) {
    const clean = text.trim();
    if (!clean || window.Lesson.busy) return;
    draft = "";
    sending = clean;
    refocus = true;
    render(window.Lesson.state);
    const data = await window.Lesson.act("explain/turn", { text: clean }, { toChat: false, typing: false });
    sending = null;
    // Реплика не принята сервером — возвращаем её в поле, чтобы отправить снова.
    if (!data) draft = clean;
    render(window.Lesson.state);
    if (refocus) panel.querySelector("#explain-input")?.focus();
    refocus = false;
  }

  async function finishVoluntary(session) {
    if (!(await confirm("Завершить объяснение и получить отчёт?"))) return;
    const data = await window.Lesson.act("explain/stop", {}, { toChat: false });
    if (!data) return;
    if (data.report) await openSession(session.id);
    else notify("Сессия была пустой — отчёта нет.");
  }

  function sessionView(session) {
    const box = el("div", "explain");
    const required = session.kind === "required";
    const head = el("div", "explain-head");
    head.append(
      el("h2", "", `${required ? "Объясни новичку" : "Объясняешь новичку"}: «${session.title}»`),
      el(
        "p",
        "muted",
        required
          ? "Обязательное задание: пока ты не объяснишь занятие, дальше не пустим. Конспект откроется после задания. Застрял — спроси наставника в чате справа."
          : "Добровольно: курс не стоит, закончить можно в любой момент. Наставник — в чате справа."
      ),
      progressOf(session)
    );
    const form = el("form", "explain-form");
    const input = el("textarea");
    input.id = "explain-input";
    input.name = "text";
    input.rows = 3;
    input.maxLength = 8000;
    input.placeholder = "Объясняй своими словами… Enter — отправить, Shift+Enter — новая строка";
    input.setAttribute("aria-label", "Объяснение новичку");
    input.value = draft;
    input.disabled = sending !== null;
    input.addEventListener("input", () => {
      draft = input.value;
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    const submit = el("button", "btn primary", "Отправить");
    submit.type = "submit";
    submit.disabled = sending !== null;
    form.append(input, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      send(input.value);
    });
    box.append(head, logOf(session.messages, { pendingText: sending }), form);
    if (!required) {
      const actions = el("div", "explain-actions");
      actions.append(button("Закончить и получить отчёт", "ghost", () => finishVoluntary(session)));
      box.append(actions);
    }
    return box;
  }

  /* --- Задание выполнено: отчёт и «Продолжить» --- */

  function readyView(session) {
    const box = el("div", "explain");
    const head = el("div", "explain-head");
    head.append(
      el("h2", "", `Готово: «${session.title}»`),
      el("p", "muted", "Задание выполнено. Посмотри отчёт и продолжай урок.")
    );
    const actions = el("div", "explain-actions");
    const next = button("Продолжить", "primary", async () => {
      await window.Lesson.act("explain/continue");
    });
    actions.append(next);
    box.append(head, reportOf(session.report, session.lessons || {}), actions);
    box.append(el("h3", "", "Разговор с новичком"), logOf(session.messages));
    return box;
  }

  /* --- Без задания: история, «Объяснить ещё раз», согласие --- */

  async function openSession(id) {
    try {
      const data = await request(`${window.Lesson.base}/explain/sessions/${encodeURIComponent(id)}`);
      viewing = data.session;
      render(window.Lesson.state);
    } catch (problem) {
      notify(problem.message, { error: true });
    }
  }

  function historyView(session) {
    const box = el("div", "explain");
    const head = el("div", "explain-head");
    const when = new Date(session.started_at * 1000).toLocaleString("ru-RU");
    head.append(
      el("h2", "", `«${session.title}»`),
      el("p", "muted", `${KINDS[session.kind] || ""} · ${when}`)
    );
    const back = el("button", "btn ghost", "← К списку");
    back.type = "button";
    back.addEventListener("click", () => {
      viewing = null;
      render(window.Lesson.state);
    });
    box.append(back, head);
    if (session.report) box.append(reportOf(session.report, session.lessons || {}));
    box.append(el("h3", "", "Разговор с новичком"), logOf(session.messages));
    if (session.mentor.length) {
      box.append(el("h3", "", "Вопросы наставнику"));
      const log = el("div", "chat-log explain-log");
      session.mentor.forEach((message) => {
        const node = el("div", `msg ${message.role === "user" ? "user" : "assistant"}`);
        node.append(el("span", "msg-who", message.role === "user" ? WHO.student : "🧑‍🏫 Наставник"));
        const body = el("div");
        body.innerHTML = message.html;
        node.append(body);
        log.append(node);
      });
      box.append(log);
    }
    return box;
  }

  function overviewView(explain) {
    const box = el("div", "explain");
    const head = el("div", "explain-head");
    head.append(
      el("h2", "", "Объясни новичку"),
      el(
        "p",
        "muted",
        "Объясни занятие новичку своими словами — так лучше всего видно, что понято, а что нет. " +
          "После важных занятий это задание обязательное."
      )
    );
    box.append(head);

    const again = el("section", "explain-section");
    again.append(el("h3", "", "Объяснить ещё раз"));
    if (explain.lessons.length) {
      const items = el("ul", "explain-lessons");
      explain.lessons.forEach((lesson) => {
        const item = el("li");
        item.append(
          el("span", "", lesson.name),
          button("Объяснить", "", () => window.Lesson.act("explain/start", { node_id: lesson.node_id }, { toChat: false }))
        );
        items.append(item);
      });
      again.append(items);
    } else {
      again.append(el("p", "muted", "Появится после первого закрытого важного занятия."));
    }
    box.append(again);

    const past = el("section", "explain-section");
    past.append(el("h3", "", "Прошлые объяснения"));
    if (explain.history.length) {
      const items = el("ul", "explain-history");
      explain.history.forEach((row) => {
        const item = el("li");
        const link = el("button", "btn ghost", row.name);
        link.type = "button";
        link.addEventListener("click", () => openSession(row.id));
        const when = new Date(row.started_at * 1000).toLocaleString("ru-RU");
        const coverage = row.coverage ? ` · ${row.coverage.done} из ${row.coverage.total}` : "";
        item.append(link, el("span", "muted", `${KINDS[row.kind] || ""} · ${when}${coverage}`));
        items.append(item);
      });
      past.append(items);
    } else {
      past.append(el("p", "muted", "Пока ничего — первое объяснение будет после важного занятия."));
    }
    box.append(past);

    const saved = el("details", "explain-saved");
    saved.append(el("summary", "", "Что сохраняется"), el("p", "", SAVED));
    const share = el("label", "explain-share");
    const check = el("input");
    check.type = "checkbox";
    check.checked = explain.share;
    check.addEventListener("change", async () => {
      const wanted = check.checked;
      try {
        const data = await request(`${window.Lesson.base}/explain/settings`, {
          method: "POST",
          body: { share: wanted },
        });
        check.checked = data.share;
        if (window.Lesson.state?.explain) window.Lesson.state.explain.share = data.share;
      } catch (problem) {
        check.checked = !wanted;
        notify(problem.message, { error: true });
      }
    });
    share.append(check, el("span", "", "Показывать мои сессии преподавателю"));
    saved.append(share);
    box.append(saved);
    return box;
  }

  function render(state) {
    const wasInput = document.activeElement?.id === "explain-input";
    panel.replaceChildren();
    if (!state || state.survey || !state.explain) {
      dot.hidden = true;
      return;
    }
    const explain = state.explain;
    // Началось новое задание — открытая из истории сессия больше не нужна.
    if (explain.blocked) viewing = null;
    const tab = document.getElementById("tab-btn-explain");
    // Точка — «висит задание»; пока вкладка открыта, она не нужна.
    dot.hidden = !explain.blocked || tab.getAttribute("aria-selected") === "true";
    const active = explain.active;
    if (viewing) panel.append(historyView(viewing));
    else if (active && active.status === "active") panel.append(sessionView(active));
    else if (active && explain.ready) panel.append(readyView(active));
    else panel.append(overviewView(explain));
    const log = panel.querySelector(".explain-log");
    if (log) log.scrollTop = log.scrollHeight;
    if (wasInput) panel.querySelector("#explain-input")?.focus();
  }

  window.Lesson.onState(render);
  document.addEventListener("lesson:tab", (event) => {
    if (event.detail === "explain") dot.hidden = true;
  });
})();
