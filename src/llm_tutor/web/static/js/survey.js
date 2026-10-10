/* Анкета в левой панели: приветствие → вопросы → сводка → «Начать урок».
 *
 * Ответы живут в памяти страницы; сервер по ним считает следующий вопрос и
 * сам проверяет последовательность (состояния анкеты на сервере нет).
 */
(() => {
  const { request, notify } = window.App;
  const pane = document.getElementById("pane-left");
  const box = document.getElementById("survey");
  let answers = [];
  let started = false;
  let loading = false;

  const INTRO_TITLE = "Привет! Я тьютор по теме «Pandas / EDA».";
  const INTRO_TEXT = "Пара коротких вопросов — и подберу, с чего начать. Это не экзамен: отвечайте как есть.";
  const CLAIMED_NOTE = "Знакомое не пропускаю — в конце проверим коротким тестом.";

  function el(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function button(label, className, onClick) {
    const element = el("button", `btn ${className}`.trim(), label);
    element.type = "button";
    element.addEventListener("click", onClick);
    return element;
  }

  function intro() {
    const card = el("div", "survey-card");
    card.append(el("p", "eyebrow", "Знакомство"), el("h2", "", INTRO_TITLE), el("p", "survey-lead", INTRO_TEXT));
    const go = button("Поехали", "primary", () => {
      started = true;
      step();
    });
    go.append(window.App.icon("arrow"));
    card.append(go);
    box.replaceChildren(card);
    go.focus();
  }

  function question(data) {
    const card = el("div", "survey-card");
    const head = el("div", "survey-progress");
    if (data.number) {
      head.append(el("span", "", `Вопрос ${data.number} из ${data.total}`));
      const track = el("div", "progress-track");
      const fill = el("span", "progress-fill");
      fill.style.width = `${Math.round((100 * data.number) / data.total)}%`;
      track.append(fill);
      head.append(track);
    } else {
      head.append(el("span", "", "Для начала"));
    }
    const title = el("h2", "survey-question", data.question);
    title.id = "survey-question";
    card.append(head, title);
    if (data.example) card.append(el("p", "survey-example", data.example));
    const grid = el("div", data.options.length === 4 ? "survey-options grid" : "survey-options");
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-labelledby", "survey-question");
    data.options.forEach((label, index) => {
      grid.append(button(label, "survey-option", () => answer(index)));
    });
    card.append(grid);
    if (data.can_back) {
      card.append(button("‹ Назад", "ghost survey-back", back));
    }
    box.replaceChildren(card);
    grid.querySelector("button")?.focus();
  }

  function summary(data) {
    const card = el("div", "survey-card");
    card.append(el("p", "eyebrow", "Готово"), el("h2", "", "Понял тебя"));
    const list = el("ul", "survey-summary");
    data.items.forEach((item) => {
      const row = el("li");
      row.append(el("span", "", item.title), el("b", "", item.answer));
      list.append(row);
    });
    card.append(list);
    if (data.claimed) card.append(el("p", "survey-lead", CLAIMED_NOTE));
    const actions = el("div", "survey-actions");
    const begin = button("Начать урок", "primary", finish);
    begin.append(window.App.icon("arrow"));
    begin.dataset.turn = "";
    actions.append(begin, button("‹ Изменить ответ", "ghost", back));
    card.append(actions);
    box.replaceChildren(card);
    begin.focus();
  }

  /**
   * Следующий шаг анкеты по текущим ответам. ``undo`` откатывает изменение
   * ответов, которое сделал вызвавший (ответ или «Назад»), если сервер не
   * ответил: ответы должны совпадать с вопросом на экране.
   */
  async function step(undo = () => {}) {
    if (loading) return;
    loading = true;
    box.setAttribute("aria-busy", "true");
    try {
      const data = await request(`${window.Lesson.base}/survey/next`, { method: "POST", body: { answers } });
      if (data.question) question(data.question);
      else summary(data.summary);
    } catch (problem) {
      notify(problem.message, { error: true });
      if (problem.status === 422) {
        answers = [];
        intro();
      } else if (problem.status === 409) {
        await window.Lesson.load();
      } else {
        undo();
      }
    } finally {
      loading = false;
      box.setAttribute("aria-busy", "false");
    }
  }

  function answer(index) {
    if (loading) return;
    answers.push(index);
    step(() => answers.pop());
  }

  function back() {
    if (loading || !answers.length) return;
    const removed = answers.pop();
    step(() => answers.push(removed));
  }

  async function finish() {
    const data = await window.Lesson.act("survey/finish", { answers });
    if (data) {
      answers = [];
      window.Lesson.selectTab("theory");
    }
  }

  window.Lesson.onState((state) => {
    pane.classList.toggle("surveying", Boolean(state.survey));
    if (state.survey && !started && !answers.length) intro();
  });
})();
