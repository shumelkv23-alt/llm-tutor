/* Конспект: личная заметка к записи — правка и сохранение без перезагрузки.
 * Не путать с notes.js: там заметки-фрагменты в профиле (ветка golos). */
(() => {
  const { request, notify } = window.App;

  function blockOf(node) {
    return node.closest("[data-note-endpoint]");
  }

  function showForm(block, open) {
    const form = block.querySelector("[data-note-form]");
    const button = block.querySelector("[data-note-edit]");
    form.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) {
      const area = form.querySelector("textarea");
      area.focus();
      area.setSelectionRange(area.value.length, area.value.length);
    }
  }

  function showSaved(block, text) {
    const display = block.querySelector("[data-note-text]");
    const edit = block.querySelector("[data-note-edit]");
    // textContent, а не innerHTML: заметка ученика — обычный текст.
    display.textContent = text;
    display.hidden = !text;
    edit.textContent = text ? "Изменить" : "Добавить заметку";
  }

  async function save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const block = blockOf(form);
    const area = form.querySelector("textarea");
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector("[data-note-status]");
    status.textContent = "";
    button.disabled = true;
    try {
      const data = await request(block.dataset.noteEndpoint, {
        method: "PUT",
        body: { text: area.value },
        timeout: 20000,
      });
      const text = typeof data.text === "string" ? data.text : "";
      area.value = text;
      showSaved(block, text);
      showForm(block, false);
      notify(text ? "Заметка сохранена." : "Заметка удалена.");
    } catch (problem) {
      status.textContent = problem.message || "Не удалось сохранить заметку.";
    } finally {
      button.disabled = false;
    }
  }

  function cancel(event) {
    const form = event.currentTarget.closest("[data-note-form]");
    const block = blockOf(form);
    form.querySelector("textarea").value = block.querySelector("[data-note-text]").textContent;
    form.querySelector("[data-note-status]").textContent = "";
    showForm(block, false);
    block.querySelector("[data-note-edit]").focus();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-note-endpoint]").forEach((block) => {
      const form = block.querySelector("[data-note-form]");
      block.querySelector("[data-note-edit]").addEventListener("click", () => {
        showForm(block, form.hidden);
      });
      block.querySelector("[data-note-cancel]").addEventListener("click", cancel);
      form.addEventListener("submit", save);
      form.querySelector("textarea").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
          form.requestSubmit();
        }
      });
    });
  });
})();
