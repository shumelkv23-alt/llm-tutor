/* Профиль: смена имени и пароля (ошибки — рядом с полем). */
(() => {
  const { request, notify } = window.App;

  function clear(form) {
    form.querySelectorAll(".field-error, .form-error").forEach((element) => {
      element.hidden = true;
      element.textContent = "";
    });
  }

  function show(form, field, message) {
    const slot = (field && form.querySelector(`[data-error-for="${field}"]`)) || form.querySelector(".form-error");
    slot.textContent = message;
    slot.hidden = false;
  }

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    clear(form);
    const body = {};
    for (const [name, value] of new FormData(form)) body[name] = String(value);
    button.disabled = true;
    try {
      const data = await request(form.dataset.endpoint, { method: "POST", body, timeout: 20000 });
      if (form.id === "password-form") {
        form.reset();
        notify("Пароль изменён. Другие входы закрыты.");
      } else {
        notify("Имя сохранено.");
        document.querySelectorAll(".session-info strong").forEach((element) => {
          element.textContent = data.user.name;
        });
      }
    } catch (problem) {
      show(form, problem.field, problem.message);
    } finally {
      button.disabled = false;
    }
  }

  function syncTheme() {
    const current = window.App.currentTheme();
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      const checked = button.dataset.themeChoice === current;
      button.setAttribute("aria-checked", String(checked));
      button.tabIndex = checked ? 0 : -1;
    });
  }

  function pickTheme(choice, { focus = false } = {}) {
    window.App.setTheme(choice);
    syncTheme();
    if (focus) document.querySelector(`[data-theme-choice="${choice}"]`)?.focus();
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form.profile-form").forEach((form) => form.addEventListener("submit", submit));
    const group = document.querySelector(".theme-picker [role=radiogroup]");
    if (group) {
      group.addEventListener("click", (event) => {
        const button = event.target.closest("[data-theme-choice]");
        if (button) pickTheme(button.dataset.themeChoice);
      });
      group.addEventListener("keydown", (event) => {
        const choices = ["light", "dark", "system"];
        const index = choices.indexOf(window.App.currentTheme());
        const moves = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
        let next;
        if (event.key === "Home") next = 0;
        else if (event.key === "End") next = choices.length - 1;
        else if (event.key in moves) next = (index + moves[event.key] + choices.length) % choices.length;
        else return;
        event.preventDefault();
        pickTheme(choices[next], { focus: true });
      });
      syncTheme();
      // Тему сменили в другой вкладке — app.js уже применил её, сверяем переключатель.
      window.addEventListener("storage", (event) => {
        if (event.key === "llmTutor.theme") syncTheme();
      });
    }
  });
})();
