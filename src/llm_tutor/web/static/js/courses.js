/* Запись на курс: кнопка «Записаться» → API → страница занятия. */
(() => {
  const { request, notify } = window.App;

  async function enroll(event) {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const { course } = await request(`/api/courses/${encodeURIComponent(button.dataset.course)}/enroll`, {
        method: "POST",
      });
      location.assign(course.href);
    } catch (problem) {
      notify(problem.message, { error: true });
      button.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".enroll").forEach((button) => button.addEventListener("click", enroll));
    // Ширина полосы прогресса — через CSSOM: встроенные style запрещены CSP.
    document.querySelectorAll(".progress-fill").forEach((bar) => {
      bar.style.width = `${Number(bar.dataset.percent) || 0}%`;
    });
  });
})();
