/* «Маршрут»: выезжающая панель — модули, внутри темы со статусами; переход к теме. */
(() => {
  const { confirm } = window.App;
  const dialog = document.getElementById("route-dialog");
  const list = document.getElementById("route-list");
  const summary = document.getElementById("route-summary");
  const MARKS = { closed: "✓", current: "▶", claimed: "🔍", available: "○", ahead: "·" };
  // Тема закрыта после двух ошибок (срез 28): засчитана, но к ней вернёмся.
  const WEAK_MARK = "⚠️";
  const WEAK_LABEL = "Слабое место";

  function moduleHeader(module) {
    const item = document.createElement("li");
    item.className = "route-module";
    const title = document.createElement("strong");
    title.textContent = `Модуль ${module.number} · ${module.title}`;
    const count = document.createElement("span");
    count.textContent = module.survey_done
      ? `закрыто ${module.closed} из ${module.total}`
      : `${module.total} тем · начнём с короткой анкеты`;
    item.append(title, count);
    return item;
  }

  function topicRow(topic, number) {
    const item = document.createElement("li");
    const row = document.createElement("button");
    row.type = "button";
    row.className = `route-topic ${topic.status}${topic.weak ? " weak" : ""}`;
    row.dataset.node = topic.id;
    row.dataset.turn = "";
    if (topic.status === "current") row.setAttribute("aria-current", "step");
    const mark = document.createElement("span");
    mark.className = "route-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = topic.weak ? WEAK_MARK : MARKS[topic.status] || "·";
    const name = document.createElement("span");
    name.className = "route-name";
    name.textContent = `${number}. ${topic.name}`;
    const status = document.createElement("span");
    status.className = "route-status";
    status.textContent = topic.weak ? WEAK_LABEL : window.Lesson.STATUS_LABELS[topic.status] || "";
    row.append(mark, name, status);
    item.append(row);
    return item;
  }

  function render(state) {
    list.replaceChildren();
    if (state.survey) return;
    summary.textContent = `Закрыто ${state.closed} из ${state.topics.length}`;
    const modules = state.modules || [];
    if (!modules.length) {
      state.topics.forEach((topic, index) => list.append(topicRow(topic, index + 1)));
      return;
    }
    modules.forEach((module) => {
      list.append(moduleHeader(module));
      state.topics
        .filter((topic) => topic.module === module.number)
        .forEach((topic, index) => list.append(topicRow(topic, index + 1)));
    });
  }

  list.addEventListener("click", async (event) => {
    const row = event.target.closest(".route-topic");
    if (!row || window.Lesson.busy) return;
    const state = window.Lesson.state;
    if (row.dataset.node === state.node?.id) {
      dialog.close();
      return;
    }
    if (state.item) {
      const ok = await confirm(
        "Перейти к другой теме?",
        "Задание, которое ждёт ответа, будет снято.",
        { label: "Перейти" }
      );
      if (!ok) return;
    }
    dialog.close();
    const data = await window.Lesson.act("switch", { node_id: row.dataset.node });
    if (data) {
      window.Lesson.selectTab("theory");
      window.Lesson.selectView("lesson");
    }
  });

  document.getElementById("route-open").addEventListener("click", () => {
    dialog.showModal();
    list.querySelector("[aria-current=step]")?.focus();
  });
  document.getElementById("route-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    // Клик по подложке закрывает панель.
    if (event.target === dialog) dialog.close();
  });

  window.Lesson.onState(render);
})();
