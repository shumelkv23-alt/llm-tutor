/* «Маршрут»: выезжающая панель — модули, внутри занятия со статусами; переход.
 *
 * Модуль сворачивается (по умолчанию раскрыт только модуль текущего занятия),
 * пройденный модуль — «🎉», неопубликованный — «скоро» без занятий (срез 55).
 */
(() => {
  const { confirm } = window.App;
  const dialog = document.getElementById("route-dialog");
  const list = document.getElementById("route-list");
  const summary = document.getElementById("route-summary");
  const MARKS = { closed: "✓", current: "▶", claimed: "🔍", available: "○", ahead: "·" };
  // Тема закрыта после двух ошибок (срез 28): засчитана, но к ней вернёмся.
  const WEAK_MARK = "⚠️";
  const WEAK_LABEL = "Слабое место";

  // Модули, которые ученик раскрыл или свернул сам: его выбор сильнее умолчания.
  const toggled = new Map();

  function finished(module) {
    return module.published && module.total > 0 && module.closed >= module.total;
  }

  function moduleHeader(module, open) {
    const item = document.createElement("li");
    item.className = `route-module${module.published ? "" : " soon"}`;
    const title = document.createElement("strong");
    title.textContent = `Модуль ${module.number} · ${module.title}`;
    const count = document.createElement("span");
    if (!module.published) {
      count.textContent = "скоро";
      item.append(title, count);
      return item;
    }
    if (finished(module)) count.textContent = `🎉 Модуль ${module.number} пройден`;
    else if (module.survey_done) count.textContent = `закрыто ${module.closed} из ${module.total}`;
    else count.textContent = `${module.total} занятий · начнём с короткой анкеты`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "route-module-toggle";
    toggle.dataset.module = String(module.number);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.append(title, count);
    item.append(toggle);
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
    const modules = state.modules || [];
    if (!modules.length) {
      summary.textContent = `Закрыто ${state.closed} из ${state.topics.length}`;
      state.topics.forEach((topic, index) => list.append(topicRow(topic, index + 1)));
      return;
    }
    const open = modules.filter((module) => module.published);
    const here = state.node?.lesson?.module.number;
    const working = modules.find((module) => module.number === here);
    if (open.length && open.every(finished)) {
      summary.textContent = modules.length > open.length
        ? "🎉 Открытые модули пройдены — следующие скоро"
        : "🎓 Курс пройден";
    } else if (working) {
      summary.textContent = `Модуль ${working.number}: закрыто ${working.closed} из ${working.total}`;
    } else {
      summary.textContent = `Закрыто ${state.closed} из ${state.topics.length}`;
    }
    modules.forEach((module) => {
      const expanded = toggled.has(module.number)
        ? toggled.get(module.number)
        : module.number === (here ?? open[0]?.number);
      list.append(moduleHeader(module, expanded));
      if (!module.published || !expanded) return;
      state.topics
        .filter((topic) => topic.module === module.number)
        .forEach((topic, index) => list.append(topicRow(topic, index + 1)));
    });
  }

  list.addEventListener("click", async (event) => {
    const toggle = event.target.closest(".route-module-toggle");
    if (toggle) {
      const number = Number(toggle.dataset.module);
      toggled.set(number, toggle.getAttribute("aria-expanded") !== "true");
      render(window.Lesson.state);
      list.querySelector(`.route-module-toggle[data-module="${number}"]`)?.focus();
      return;
    }
    const row = event.target.closest(".route-topic");
    if (!row || window.Lesson.busy) return;
    const state = window.Lesson.state;
    if (row.dataset.node === state.node?.id) {
      dialog.close();
      return;
    }
    if (state.item) {
      const ok = await confirm(
        "Перейти к другому занятию?",
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
