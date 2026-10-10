/* Вкладка «Практика»: задание, которое ждёт ответа, и вердикт по нему.
 *
 * Задание берётся из состояния сессии (источник правды — сервер), вердикт
 * ✓/✗ — из журнала (его посчитал код). Разбор тьютора приходит в чат.
 */
(() => {
  const { confirm, icon } = window.App;
  const panel = document.getElementById("tab-practice");
  const dot = document.getElementById("practice-dot");
  const TYPE_LABELS = {
    choice: "Выбор ответа",
    short: "Короткий ответ",
    open: "Ответ своими словами",
    code: "Код",
  };
  // Живут до следующего состояния: вердикт ответа и «тема закрыта».
  let verdict = null;
  let closedNote = null;
  let previous = null;
  let selected = null;

  function el(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function button(label, className, onClick, iconName) {
    const element = el("button", `btn ${className}`.trim());
    element.type = "button";
    if (iconName) element.append(icon(iconName));
    element.append(label);
    element.dataset.turn = "";
    element.addEventListener("click", onClick);
    return element;
  }

  async function submit(item, answer) {
    const data = await window.Lesson.act("answer", { item_id: item.id, answer }, { echo: typeof answer === "number" ? item.options[answer] : answer });
    if (data?.verdict) {
      verdict = data.verdict;
      render(window.Lesson.state);
    }
  }

  function answerForm(item) {
    const form = el("form", "practice-answer");
    form.noValidate = true;
    if (item.type === "choice") {
      const list = el("div", "practice-options");
      list.setAttribute("role", "radiogroup");
      list.setAttribute("aria-label", "Варианты ответа");
      item.options.forEach((label, index) => {
        const option = el("button", "practice-option");
        option.type = "button";
        option.setAttribute("role", "radio");
        option.setAttribute("aria-checked", String(selected === index));
        option.append(el("span", "option-mark", String.fromCharCode(65 + index)), el("span", "", label));
        option.addEventListener("click", () => {
          selected = index;
          list.querySelectorAll("[role=radio]").forEach((other, otherIndex) => {
            other.setAttribute("aria-checked", String(otherIndex === index));
          });
          form.querySelector("button[type=submit]").disabled = false;
        });
        list.append(option);
      });
      form.append(list);
    } else {
      const field = item.type === "short" ? el("input", "practice-input") : el("textarea", "practice-input");
      field.name = "answer";
      field.maxLength = 8000;
      field.setAttribute("aria-label", "Ваш ответ");
      if (item.type === "short") {
        field.placeholder = "Короткий ответ";
        field.autocomplete = "off";
        field.spellcheck = false;
      } else {
        field.rows = item.type === "code" ? 8 : 5;
        field.placeholder = item.type === "code" ? "# Ваш код на Python" : "Объясните своими словами";
        if (item.type === "code") field.classList.add("mono");
      }
      form.append(field);
      if (item.type === "code") {
        form.append(el("p", "form-hint", "Скоро код можно будет запустить и проверить тестами во вкладке «Код»."));
      }
    }
    const actions = el("div", "practice-actions");
    const send = el("button", "btn primary");
    send.type = "submit";
    send.dataset.turn = "";
    send.append("Ответить", icon("arrow"));
    send.disabled = item.type === "choice" && selected === null;
    actions.append(
      send,
      button("Не понимаю", "ghost", () => window.Lesson.act("stuck", {}, { echo: "Не понял, давай подробнее." })),
      button("Пропустить", "ghost", async () => {
        const ok = await confirm("Пропустить задание?", "Ответ не запишется, к теме вернёмся позже.", { label: "Пропустить" });
        if (ok) await window.Lesson.act("skip", {}, { echo: "Пропустить задание." });
      })
    );
    form.append(actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (item.type === "choice") {
        if (selected !== null) submit(item, selected);
        return;
      }
      const value = form.elements.namedItem("answer").value.trim();
      if (value) submit(item, value);
      else form.elements.namedItem("answer").focus();
    });
    return form;
  }

  function verdictBanner() {
    if (!verdict) return null;
    const banner = el("div", `verdict ${verdict.correct ? "ok" : verdict.correct === false ? "bad" : "unknown"}`);
    banner.setAttribute("role", "status");
    banner.append(icon(verdict.correct ? "check" : "close"));
    const text = verdict.correct
      ? "Верно. Разбор — в чате."
      : verdict.correct === false
        ? "Пока не так. Тьютор объясняет в чате."
        : "Это задание не удалось проверить.";
    banner.append(text);
    return banner;
  }

  function render(state) {
    if (!state || state.survey) return;
    panel.replaceChildren();
    const wrap = el("div", "practice");

    if (closedNote) {
      const banner = el("div", "closed-banner");
      banner.setAttribute("role", "status");
      banner.append(icon("check"), closedNote);
      wrap.append(banner);
    }
    if (state.mode === "verify" && state.node) {
      wrap.append(el("p", "verify-note", `Проверка темы «${state.node.name}»: ответьте без подсказок — так тема закроется.`));
    }
    const banner = verdictBanner();
    if (banner) wrap.append(banner);

    if (state.item) {
      const card = el("article", `task-card${state.mode === "verify" ? " verify" : ""}`);
      const head = el("div", "task-head");
      head.append(el("span", "badge primary", TYPE_LABELS[state.item.type] || "Задание"));
      head.append(el("span", "task-streak", `Верно подряд: ${state.streak} из ${state.streak_target}`));
      const prompt = el("div", "task-prompt");
      // Готовый HTML: задание прошло web.markdown.render на сервере.
      prompt.innerHTML = state.item.prompt_html;
      card.append(head, prompt, answerForm(state.item));
      wrap.append(card);
    } else if (state.node) {
      const empty = el("div", "empty");
      empty.append(el("strong", "", "Задания сейчас нет"), "Возьмите задание по теме — проверим, как она усвоилась.");
      const take = button("Взять задание", "primary", () => window.Lesson.act("task", {}, { echo: "Дай задание" }), "check");
      empty.append(el("br"), take);
      wrap.append(empty);
    } else {
      const empty = el("div", "empty");
      empty.append(el("strong", "", "Тема не выбрана"), "Откройте «Маршрут» и выберите тему.");
      wrap.append(empty);
    }
    panel.append(wrap);
    const practiceTab = document.getElementById("tab-btn-practice");
    dot.hidden = !state.item || practiceTab.getAttribute("aria-selected") === "true";
  }

  window.Lesson.onState((state) => {
    verdict = null;
    const closed = previous && !previous.survey && !state.survey && state.closed > previous.closed;
    closedNote = closed ? `Тема закрыта!${state.node ? ` Дальше — «${state.node.name}».` : ""}` : null;
    if (state.item?.id !== previous?.item?.id) selected = null;
    previous = state;
    render(state);
  });

  document.addEventListener("lesson:tab", (event) => {
    if (event.detail === "practice") dot.hidden = true;
  });
})();
