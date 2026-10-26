/* Заметки в профиле: удаление сохранённого фрагмента.
 *
 * Создание заметки — в панели выделения занятия (selection.js); здесь только
 * список и удаление.
 */
(() => {
  document.addEventListener("submit", async (event) => {
    const form = event.target.closest(".note-comment-form");
    if (!form) return;
    event.preventDefault();
    const input = form.elements.namedItem("comment");
    try {
      await window.App.request(
        `/api/courses/${encodeURIComponent(form.dataset.course)}/notes/${form.dataset.note}`,
        { method: "PUT", body: { comment: input.value } }
      );
      let label = form.parentElement.querySelector(".note-comment");
      if (input.value.trim()) {
        if (!label) {
          label = document.createElement("p");
          label.className = "note-comment";
          form.before(label);
        }
        label.textContent = input.value.trim();
      } else if (label) {
        label.remove();
      }
      window.App.notify("Комментарий сохранён.");
    } catch (problem) {
      window.App.notify(problem.message, { error: true });
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-note]");
    if (!button) return;
    const confirmed = await window.App.confirm(
      "Удалить заметку?",
      "Она исчезнет из профиля и поиска. Это действие нельзя отменить.",
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
      window.App.notify("Заметка удалена.");
    } catch (problem) {
      window.App.notify(problem.message, { error: true });
      button.disabled = false;
    }
  });
})();
