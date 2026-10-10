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

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".profile-form").forEach((form) => form.addEventListener("submit", submit));
  });
})();
