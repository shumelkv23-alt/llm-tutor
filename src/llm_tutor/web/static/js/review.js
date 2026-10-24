/* Вкладка «Повторение»: срочное, «освежить» выбранное и переключатель разминки.
 *
 * Состояние — блок `state.review` (источник правды — сервер). Ходы идут через
 * общий `act` из lesson.js: задание серии появляется в «Практике», а вкладка
 * показывает, что повторять и как остановиться. Весь текст — через textContent.
 */
(() => {
  const panel = document.getElementById("tab-review");
  const dot = document.getElementById("review-dot");
  // Выбранные для «Освежить» занятия живут между перерисовками: панель
  // перестраивается на каждом ходе, и без этого галочки сбрасывались бы.
  const picked = new Set();

  function el(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function turnButton(label, className, onClick) {
    const element = el("button", `btn ${className}`.trim());
    element.type = "button";
    element.dataset.turn = "";
    element.append(label);
    element.addEventListener("click", onClick);
    return element;
  }

  /** Серия начата: задание — в «Практике», туда и переключаем. */
  function showPractice() {
    window.Lesson.selectTab("practice");
    window.Lesson.selectView("lesson");
  }

  function activeNote(active, act) {
    const box = el("div", "review-active");
    box.setAttribute("role", "status");
    const text =
      active.kind === "warmup"
        ? `Идёт разминка: «${active.node_name}» · ${active.position} из ${active.total}`
        : `Идёт повторение: «${active.node_name}» · ${active.position} из ${active.total}`;
    box.append(
      el("span", "", text),
      turnButton(active.kind === "warmup" ? "Пропустить разминку" : "Закончить", "ghost", () => act("review/stop"))
    );
    return box;
  }

  function dueSection(review, act) {
    const section = el("section", "review-section");
    section.append(el("h3", "", `Срочное: ${review.due}`));
    if (review.due > 0) {
      const start = turnButton("Начать", "primary", async () => {
        const data = await act("review/start", { kind: "due" });
        if (data?.state.review.active) showPractice();
      });
      // Серия уже идёт — начать вторую нельзя (сервер ответил бы 409).
      start.disabled = Boolean(review.active);
      section.append(
        el("p", "muted", "Серия до 5 занятий, по одному заданию. Самые давние — первыми."),
        start
      );
    } else {
      section.append(el("p", "muted", "Пока нечего повторять — пройденное ещё свежо."));
    }
    return section;
  }

  function refreshSection(review, act) {
    const section = el("section", "review-section");
    section.append(el("h3", "", "Освежить"));
    if (!review.lessons.length) {
      section.append(el("p", "muted", "Закрытых занятий пока нет — освежать нечего."));
      return section;
    }
    section.append(el("p", "muted", "Выберите занятия — получите по заданию на каждое, срок не важен."));
    const known = new Set(review.lessons.map((lesson) => lesson.id));
    [...picked].forEach((id) => known.has(id) || picked.delete(id));
    const modules = new Map();
    review.lessons.forEach((lesson) => {
      if (!modules.has(lesson.module)) modules.set(lesson.module, []);
      modules.get(lesson.module).push(lesson);
    });
    const go = turnButton("Повторить выбранное", "primary", async () => {
      const data = await act("review/start", { kind: "refresh", node_ids: [...picked] });
      if (data?.state.review.active) showPractice();
    });
    const sync = () => {
      go.disabled = picked.size === 0 || Boolean(review.active);
      go.textContent = picked.size ? `Повторить выбранное (${picked.size})` : "Повторить выбранное";
    };
    modules.forEach((lessons, number) => {
      const group = el("fieldset", "review-module");
      group.append(el("legend", "", `Модуль ${number}`));
      lessons.forEach((lesson) => {
        const label = el("label", "review-lesson");
        const box = el("input");
        box.type = "checkbox";
        box.checked = picked.has(lesson.id);
        box.addEventListener("change", () => {
          if (box.checked) picked.add(lesson.id);
          else picked.delete(lesson.id);
          sync();
        });
        label.append(box, el("span", "", lesson.name));
        if (lesson.due) label.append(el("span", "badge accent", "пора повторить"));
        group.append(label);
      });
      section.append(group);
    });
    sync();
    section.append(go);
    return section;
  }

  function settingsSection(review, act) {
    const label = el("label", "review-switch");
    const box = el("input");
    box.type = "checkbox";
    box.checked = review.enabled;
    box.addEventListener("change", async () => {
      await act("review/settings", { enabled: box.checked });
      // Ответ не принят — вернуть галочку к тому, что знает сервер; панель
      // перестроилась, фокус возвращаем переключателю.
      render(window.Lesson.state);
      panel.querySelector(".review-switch input")?.focus();
    });
    label.append(
      box,
      el("span", "", "Предлагать повторение по этому курсу"),
      el("small", "muted", "Выключено — нет разминки между занятиями и числа на главной. Вкладка работает вручную.")
    );
    return label;
  }

  function renderReview(target, review, act) {
    target.replaceChildren();
    if (!review) return;
    const wrap = el("div", "review");
    wrap.append(el("h2", "", "Повторение"));
    if (review.active) wrap.append(activeNote(review.active, act));
    wrap.append(dueSection(review, act), refreshSection(review, act), settingsSection(review, act));
    target.append(wrap);
  }

  function render(state) {
    if (!state || state.survey) {
      panel.replaceChildren();
      dot.hidden = true;
      return;
    }
    renderReview(panel, state.review, window.Lesson.act);
    const tab = document.getElementById("tab-btn-review");
    // Точка — «есть что повторить»; пока вкладка открыта, она не нужна.
    dot.hidden = !(state.review.enabled && state.review.due > 0) || tab.getAttribute("aria-selected") === "true";
  }

  window.Lesson.onState(render);
  document.addEventListener("lesson:tab", (event) => {
    if (event.detail === "review") dot.hidden = true;
  });

  // Ссылка с главной — /lesson/<курс>#review.
  const openFromHash = () => {
    if (location.hash === "#review") window.Lesson.selectTab("review");
  };
  openFromHash();
  window.addEventListener("hashchange", openFromHash);

  window.Lesson.review = { renderReview };
})();
