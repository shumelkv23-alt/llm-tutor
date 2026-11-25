/* Сохранённые фрагменты в конспекте: комментарий и удаление (срез 109).
 *
 * Создание фрагмента — в панели выделения занятия (selection.js); здесь только
 * правка комментария и удаление. Запись — `.note-row`: цитата, комментарий
 * `.note-comment` и форма `.note-comment-form` (в конспекте — под «изменить»).
 */
(() => {
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest(".note-comment-form");
    if (!form) return;
    event.preventDefault();
    const input = form.elements.namedItem("comment");
    const row = form.closest(".note-row") || form.parentElement;
    try {
      await window.App.request(
        `/api/courses/${encodeURIComponent(form.dataset.course)}/notes/${form.dataset.note}`,
        { method: "PUT", body: { comment: input.value } }
      );
      let label = row.querySelector(".note-comment");
      if (input.value.trim()) {
        if (!label) {
          label = document.createElement("p");
          label.className = "note-comment";
          const quote = row.querySelector("blockquote");
          if (quote) quote.after(label);
          else form.before(label);
        }
        label.textContent = input.value.trim();
      } else if (label) {
        label.remove();
      }
      const details = form.closest("details");
      if (details) details.open = false;
      window.App.notify("Комментарий сохранён.");
    } catch (problem) {
      window.App.notify(problem.message, { error: true });
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-note]");
    if (!button) return;
    const confirmed = await window.App.confirm(
      "Удалить фрагмент?",
      "Он исчезнет из конспекта и поиска. Это действие нельзя отменить.",
      { label: "Удалить", danger: true }
    );
    if (!confirmed) return;
    button.disabled = true;
    try {
      await window.App.request(
        `/api/courses/${encodeURIComponent(button.dataset.course)}/notes/${button.dataset.deleteNote}`,
        { method: "DELETE" }
      );
      button.closest(".note-row")?.remove();
      window.App.notify("Фрагмент удалён.");
    } catch (problem) {
      window.App.notify(problem.message, { error: true });
      button.disabled = false;
    }
  });
})();
