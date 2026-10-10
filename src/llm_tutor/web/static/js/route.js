/* «Маршрут»: выезжающая панель со списком тем и статусами, переход к теме. */
(() => {
  const { confirm } = window.App;
  const dialog = document.getElementById("route-dialog");
  const list = document.getElementById("route-list");
  const summary = document.getElementById("route-summary");
  const MARKS = { closed: "✓", current: "▶", claimed: "🔍", available: "○", ahead: "·" };

  function render(state) {
    list.replaceChildren();
    if (state.survey) return;
    summary.textContent = `Закрыто ${state.closed} из ${state.topics.length}`;
    state.topics.forEach((topic, index) => {
      const item = document.createElement("li");
      const row = document.createElement("button");
      row.type = "button";
      row.className = `route-topic ${topic.status}`;
      row.dataset.node = topic.id;
      row.dataset.turn = "";
      if (topic.status === "current") row.setAttribute("aria-current", "step");
      const mark = document.createElement("span");
      mark.className = "route-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = MARKS[topic.status] || "·";
      const name = document.createElement("span");
      name.className = "route-name";
      name.textContent = `${index + 1}. ${topic.name}`;
      const status = document.createElement("span");
      status.className = "route-status";
      status.textContent = window.Lesson.STATUS_LABELS[topic.status] || "";
      row.append(mark, name, status);
      item.append(row);
      list.append(item);
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
    if (data) window.Lesson.selectTab("theory");
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
