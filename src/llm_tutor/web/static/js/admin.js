/* Админка: ширина полос прогресса и действия над аккаунтом.
 *
 * Встроенные style запрещены CSP, поэтому проценты ставятся через CSSOM из
 * data-percent. Временный пароль живёт только в поле диалога: при закрытии
 * диалога поле очищается, ни в localStorage, ни в переменные он не уходит.
 */
(() => {
  const { request, notify, confirm } = window.App;

  const sessionCount = () => document.getElementById("session-count");

  function showSessions(count) {
    const cell = sessionCount();
    if (!cell) return;
    cell.textContent = String(count);
    cell.dataset.sessions = String(count);
  }

  function showPassword(password) {
    const dialog = document.getElementById("password-dialog");
    const field = document.getElementById("password-value");
    const clear = () => {
      field.value = "";
    };
    field.value = password;
    // «Закрыть» очищает поле сразу, до закрытия диалога; событие close — для
    // Esc: оно приходит на такт позже, но пароль уходит в любом случае.
    dialog.querySelector("form").addEventListener("submit", clear, { once: true });
    dialog.addEventListener("close", clear, { once: true });
    dialog.showModal();
    field.select();
  }

  async function copyPassword() {
    const field = document.getElementById("password-value");
    try {
      await navigator.clipboard.writeText(field.value);
      notify("Пароль скопирован.");
    } catch {
      field.select();
      notify("Не удалось скопировать — пароль выделен, скопируйте вручную.", { error: true });
    }
  }

  const actions = {
    async "reset-password"(button) {
      const name = button.dataset.name;
      const ok = await confirm(
        "Сбросить пароль?",
        `Ученик «${name}» потеряет доступ: все его сессии закроются, а новый временный пароль будет показан один раз.`,
        { label: "Сбросить", danger: true }
      );
      if (!ok) return;
      const { password } = await request(
        `/api/admin/students/${encodeURIComponent(button.dataset.user)}/reset-password`,
        { method: "POST" }
      );
      showSessions(0);
      showPassword(password);
    },

    async "close-sessions"(button) {
      const name = button.dataset.name;
      const ok = await confirm(
        "Закрыть все сессии?",
        `Ученику «${name}» придётся войти заново на всех устройствах. Пароль не меняется.`,
        { label: "Закрыть", danger: true }
      );
      if (!ok) return;
      const { closed } = await request(
        `/api/admin/students/${encodeURIComponent(button.dataset.user)}/close-sessions`,
        { method: "POST" }
      );
      showSessions(0);
      notify(`Закрыто сессий: ${closed}.`);
    },
  };

  async function run(event) {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await actions[button.dataset.action](button);
    } catch (problem) {
      notify(problem.message, { error: true });
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".progress-fill").forEach((bar) => {
      bar.style.width = `${Number(bar.dataset.percent) || 0}%`;
    });
    document.querySelectorAll("[data-action]").forEach((button) => {
      if (actions[button.dataset.action]) button.addEventListener("click", run);
    });
    const copy = document.getElementById("password-copy");
    if (copy) copy.addEventListener("click", copyPassword);
  });
})();
