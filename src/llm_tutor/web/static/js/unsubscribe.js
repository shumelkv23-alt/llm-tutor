/* Отписка от напоминаний: кнопка на странице из письма.
 *
 * Токен подписан сервером и лежит в data-token; запрос — JSON на /api/unsubscribe,
 * чтобы почтовый клиент не отписал ученика простым открытием ссылки.
 */
(() => {
  const buttons = [...document.querySelectorAll("[data-reminder-toggle]")];
  const status = document.getElementById("unsubscribe-status");
  if (!buttons.length || !status) return;

  buttons.forEach((button) => {
    button.addEventListener("click", async () => {
      const enabled = button.dataset.reminderToggle === "true";
      buttons.forEach((other) => {
        other.disabled = true;
      });
      try {
        await window.App.request("/api/unsubscribe", {
          method: "POST",
          body: { token: button.dataset.token, enabled },
        });
        button.textContent = enabled ? "Письма включены" : "Вы отписались";
        status.hidden = true;
      } catch (problem) {
        status.textContent = problem.message;
        status.hidden = false;
        buttons.forEach((other) => {
          other.disabled = false;
        });
      }
    });
  });
})();
