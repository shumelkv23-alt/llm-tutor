/* Формы входа, регистрации и восстановления пароля: JSON, ошибки у поля, показ пароля. */
(() => {
  const { $, request } = window.App;

  function showError(form, field, message) {
    const slot = field && form.querySelector(`[data-error-for="${field}"]`);
    const target = slot || form.querySelector(".form-error");
    target.textContent = message;
    target.hidden = false;
    const input = field && form.elements.namedItem(field);
    if (input && slot) {
      input.setAttribute("aria-invalid", "true");
      input.focus();
    }
  }

  function clearErrors(form) {
    form.querySelectorAll(".field-error, .form-error").forEach((element) => {
      element.hidden = true;
      element.textContent = "";
    });
    form.querySelectorAll("[aria-invalid]").forEach((element) => element.removeAttribute("aria-invalid"));
  }

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    clearErrors(form);
    const body = {};
    for (const [name, value] of new FormData(form)) body[name] = String(value);
    button.disabled = true;
    try {
      await request(form.dataset.endpoint, { method: "POST", body, timeout: 20000 });
      if (form.dataset.next) {
        location.assign(form.dataset.next);
        return;
      }
      // Форма без data-next — восстановление пароля: уходить со страницы некуда
      // (ученик ещё не вошёл), поэтому прячем поля и показываем подтверждение.
      const scope = form.closest(".login-card") || form.parentElement;
      scope.querySelectorAll("[data-until-success]").forEach((element) => { element.hidden = true; });
      scope.querySelectorAll("[data-success], [data-after-success]").forEach((element) => { element.hidden = false; });
      // Токен из письма уже сожжён, пароль — секрет: в полях не остаётся ничего.
      form.querySelectorAll("input").forEach((input) => { input.value = ""; });
    } catch (problem) {
      showError(form, problem.field, problem.message);
      button.disabled = false;
    }
  }

  function togglePassword(event) {
    const button = event.currentTarget;
    const input = button.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", String(show));
    button.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".auth-form").forEach((form) => form.addEventListener("submit", submit));
    document.querySelectorAll(".toggle-password").forEach((button) => button.addEventListener("click", togglePassword));
    // Скрытый токен из ссылки в фокусе не нуждается — он и не фокусируется.
    $(".auth-form input:not([type=hidden])")?.focus();
  });
})();
