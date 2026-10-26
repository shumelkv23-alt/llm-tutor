/* Голосовой ввод: распознавание речи браузером (Chrome/Edge, Web Speech API).
 *
 * Модуль только заполняет поле — решение отправить текст принимает владелец
 * поля. Это важно для оценённых ответов: ошибка распознавания не должна
 * уходить на проверку без правки ученика. Звук обрабатывает браузер, своих
 * сетевых запросов у модуля нет.
 *
 * Публичный API: window.Voice.attach(button, field) — кнопка-переключатель
 * записи, field — input/textarea. Возвращает {start, stop, cancel, destroy,
 * setEnabled, supported} или null, если браузер не умеет распознавать речь.
 */
window.Voice = (() => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const secure = window.isSecureContext;
  const supported = Boolean(Recognition) && secure;
  const LANG = "ru-RU";
  // Сколько раз подряд можно перезапустить распознавание после паузы.
  // Счётчик сбрасывается на каждом финальном результате: длинный бриф не
  // обрывается на пятой паузе.
  const MAX_RESTARTS = 3;
  let active = null;

  // --- Озвучивание ответов (TTS) ---
  // Отдельно от распознавания: speechSynthesis есть и там, где микрофона нет.
  const synth = window.speechSynthesis;
  const ttsSupported = Boolean(synth) && typeof window.SpeechSynthesisUtterance === "function";
  let speaking = null;

  // Одна скрытая область на страницу: кнопка меняет aria-pressed, но диктор
  // не всегда объявляет это сам — статус говорит, что происходит.
  const status = document.createElement("p");
  status.className = "visually-hidden";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  document.body.append(status);
  let statusTimer = null;

  function say(text) {
    clearTimeout(statusTimer);
    status.textContent = "";
    if (!text) return;
    statusTimer = setTimeout(() => {
      status.textContent = text;
    }, 100);
  }

  function notify(text, options) {
    window.App?.notify(text, options);
  }

  /** Приклеить распознанное к уже набранному тексту без двойного пробела. */
  function join(base, text) {
    if (!text) return base;
    if (!base) return text;
    return /\s$/.test(base) ? base + text : `${base} ${text}`;
  }

  function attach(button, field, { label = "Голосовой ввод" } = {}) {
    if (!button || !field) return null;
    if (!Recognition) {
      button.hidden = true;
      return null;
    }
    button.hidden = false;
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    // У кнопки с видимым текстом имя уже есть; пустую подписываем сами.
    if (!button.textContent.trim() && !button.hasAttribute("aria-label")) {
      button.setAttribute("aria-label", label);
    }
    if (!button.title) button.title = "Голосовой ввод";
    // Своя подпись кнопки («Надиктовать ответ») не должна теряться после stop.
    const idleTitle = button.title;
    if (!secure) {
      button.disabled = true;
      button.title = "Для микрофона нужен HTTPS или localhost";
      return {
        supported: false,
        start() {},
        stop() {},
        cancel() {},
        destroy() {},
        setEnabled() {},
      };
    }

    let recognition = null;
    let session = null;
    let destroyed = false;

    function setButton(recording) {
      button.classList.toggle("recording", recording);
      button.setAttribute("aria-pressed", String(recording));
      button.title = recording ? "Остановить запись" : idleTitle;
    }

    function render() {
      if (!session || destroyed) return;
      const spoken = session.interim ? join(session.committed, session.interim) : session.committed;
      let value = join(session.base, spoken);
      if (field.maxLength > 0) value = value.slice(0, field.maxLength);
      field.value = value;
      // autosize у чата и черновики «Практики» слушают именно input.
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function finish({ focus = true } = {}) {
      if (!session) return;
      const current = session;
      session = null;
      recognition = null;
      if (active && active.session === current) active = null;
      document.removeEventListener("keydown", onKey);
      if (field.isConnected) {
        field.readOnly = current.readOnly;
        // Не переводим фокус, если уже пишет другая кнопка (например, ученик
        // переключился с чата на «Практику»): иначе фокус уйдёт из нового поля.
        if (focus && !active) {
          field.focus();
          const end = field.value.length;
          try {
            field.setSelectionRange(end, end);
          } catch {
            /* не все типы полей поддерживают выбор */
          }
        }
      }
      setButton(false);
      field.dispatchEvent(new Event("voice:stop", { bubbles: true }));
      // Промежуточный текст мог остаться в поле, не став финальным: он всё
      // равно распознан, поэтому «не распознана» только при пустом приросте.
      const heard = current.committed.trim() || field.value.trim() !== current.base.trim();
      // Если уже пишет другая кнопка, её «Говорите…» не перебиваем.
      if (!current.cancelled && !active) say(heard ? "Запись остановлена" : "Речь не распознана");
      current.resolveDone?.();
    }

    function startRecognition() {
      // Каждый запуск запоминает свою сессию: события старого recognition
      // после отмены не должны трогать уже новую запись.
      const current = session;
      // stopping: между onend и отложенным перезапуском ученик нажал «стоп».
      if (!current || destroyed || current.stopping) return;
      try {
        recognition = new Recognition();
      } catch {
        notify("Не удалось запустить распознавание речи.", { error: true });
        finish();
        return;
      }
      recognition.lang = LANG;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        if (session !== current || destroyed || current.cancelled) return;
        setButton(true);
        say("Говорите — идёт запись");
      };

      recognition.onresult = (event) => {
        if (session !== current || destroyed || current.cancelled) return;
        let interim = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const text = result[0]?.transcript || "";
          if (result.isFinal) {
            // Между сессиями распознавания пробел не приходит: склеиваем
            // слова сами, иначе получится «DataFrame?Series».
            current.committed = join(current.committed, text.trimStart());
            current.restarts = 0;
          } else {
            interim += text;
          }
        }
        current.interim = interim.trimStart();
        render();
      };

      recognition.onerror = (event) => {
        if (session !== current || current.cancelled) return;
        if (event.error === "aborted") {
          finish();
          return;
        }
        if (event.error === "no-speech") {
          if (!current.committed.trim()) say("Не услышал — попробуйте ещё раз");
          finish();
          return;
        }
        const messages = {
          "not-allowed": "Браузер не разрешил микрофон. Разрешите доступ в настройках сайта.",
          "service-not-allowed": "Браузер запретил сервис распознавания речи.",
          "audio-capture": "Микрофон не найден. Проверьте, что он подключён.",
          network: "Нет связи с сервисом распознавания речи.",
        };
        notify(messages[event.error] || "Не удалось распознать речь.", { error: true });
        finish();
      };

      recognition.onend = () => {
        if (session !== current) return;
        if (current.stopping || destroyed) {
          finish();
          return;
        }
        // Браузер сам завершает сессию после паузы. Продолжаем, пока
        // ученик не нажал «стоп», но не бесконечно.
        if (current.restarts >= MAX_RESTARTS) {
          finish();
          return;
        }
        current.restarts += 1;
        current.interim = "";
        // Перезапуск — только этой же сессии: за 150 мс ученик мог начать
        // новую запись, и старая не должна запускать вторую recognition.
        const retryFor = current;
        setTimeout(() => {
          if (session === retryFor) startRecognition();
        }, 150);
      };

      try {
        recognition.start();
      } catch {
        if (session !== current) return;
        notify("Распознавание уже запущено.", { error: true });
        finish();
      }
    }

    function start() {
      // Начали диктовать — чтение вслух больше не нужно, иначе два голоса.
      if (speaking) stopSpeaking();
      if (session) {
        stop();
        return;
      }
      if (field.disabled || button.disabled || button.getAttribute("aria-disabled") === "true") return;
      if (active) active.stop();
      session = {
        base: field.value,
        committed: "",
        interim: "",
        restarts: 0,
        stopping: false,
        cancelled: false,
        readOnly: field.readOnly,
        resolveDone: null,
      };
      // stop() отдаёт этот Promise: админке нужно дождаться финального result,
      // прежде чем читать поле и очищать его.
      session.done = new Promise((resolve) => {
        session.resolveDone = resolve;
      });
      field.readOnly = true;
      field.dispatchEvent(new Event("voice:start", { bubbles: true }));
      active = { session, stop };
      document.addEventListener("keydown", onKey);
      startRecognition();
    }

    function stop() {
      if (!session) return Promise.resolve();
      const current = session;
      if (current.stopping) return current.done;
      current.stopping = true;
      current.interim = "";
      say("Распознаю…");
      if (!recognition) {
        finish();
        return current.done;
      }
      try {
        recognition.stop();
      } catch {
        finish();
        return current.done;
      }
      // Некоторые браузеры не присылают onend после stop(); не оставляем
      // кнопку в состоянии записи навсегда и принудительно гасим сервис.
      const stopping = current;
      setTimeout(() => {
        if (session !== stopping || !stopping.stopping) return;
        if (recognition) {
          try {
            recognition.abort();
          } catch {
            /* уже остановлен */
          }
        }
        finish();
      }, 1000);
      return current.done;
    }

    function cancel() {
      if (!session) return;
      const current = session;
      current.cancelled = true;
      current.stopping = true;
      if (field.isConnected) {
        field.value = current.base;
        field.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (recognition) {
        try {
          recognition.abort();
        } catch {
          /* уже остановлен */
        }
      }
      finish({ focus: false });
    }

    function onKey(event) {
      if (!session) return;
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    }

    function setEnabled(enabled) {
      if (!supported) return;
      button.disabled = !enabled;
      if (!enabled) stop();
    }

    function destroy() {
      // Перерисовка панели: распознанное уже легло в поле и в черновик —
      // обрываем запись, но текст не откатываем (в отличие от Escape).
      destroyed = true;
      if (!session) return;
      session.stopping = true;
      if (recognition) {
        try {
          recognition.abort();
        } catch {
          /* уже остановлен */
        }
      }
      finish({ focus: false });
    }

    button.addEventListener("click", (event) => {
      event.preventDefault();
      if (button.disabled || button.getAttribute("aria-disabled") === "true") return;
      start();
    });

    return { supported, start, stop, cancel, destroy, setEnabled };
  }

  function resetSpeechButton(button) {
    if (!button) return;
    button.classList.remove("speaking");
    button.setAttribute("aria-pressed", "false");
    if (button.dataset.speechTitle) button.title = button.dataset.speechTitle;
    if (button.dataset.speechLabel) button.setAttribute("aria-label", button.dataset.speechLabel);
  }

  /** Сбросить состояние кнопки, не трогая статус диктора. */
  function clearSpeech() {
    const current = speaking;
    speaking = null;
    resetSpeechButton(current?.button);
  }

  /** Остановить чтение; повторный вызов безопасен. */
  function stopSpeaking() {
    if (!synth) return;
    const current = speaking;
    // Снимаем состояние до cancel(): старый onerror/onend не должен
    // объявить ошибку уже после остановки или начала новой фразы.
    speaking = null;
    resetSpeechButton(current?.button);
    try {
      synth.cancel();
    } catch {
      /* синтез уже закрыт */
    }
    if (current) say("Чтение остановлено");
  }

  /**
   * Прочитать текст кнопкой-переключателем. Возвращает false, если TTS нет или
   * текст пустой. Повторный клик по той же кнопке останавливает чтение.
   */
  function speak(text, button) {
    if (!ttsSupported) return false;
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean) return false;
    if (speaking && speaking.button === button) {
      stopSpeaking();
      return true;
    }
    stopSpeaking();
    const utterance = new SpeechSynthesisUtterance(clean);
    utterance.lang = "ru-RU";
    const voices = synth.getVoices ? synth.getVoices() : [];
    const russian = voices.find((voice) => (voice.lang || "").toLowerCase().startsWith("ru"));
    if (russian) utterance.voice = russian;
    speaking = { utterance, button };
    if (button) {
      if (!button.dataset.speechTitle) button.dataset.speechTitle = button.title || "";
      if (!button.dataset.speechLabel) {
        button.dataset.speechLabel = button.getAttribute("aria-label") || "";
      }
      button.classList.add("speaking");
      button.setAttribute("aria-pressed", "true");
      button.title = "Остановить чтение";
      button.setAttribute("aria-label", "Остановить чтение");
    }
    utterance.onend = () => {
      if (speaking && speaking.utterance === utterance) clearSpeech();
    };
    utterance.onerror = () => {
      if (!speaking || speaking.utterance !== utterance) return;
      clearSpeech();
      say("Не удалось озвучить ответ");
    };
    try {
      synth.cancel();
      synth.speak(utterance);
    } catch {
      clearSpeech();
      return false;
    }
    say("Читаю ответ");
    return true;
  }

  return { supported, attach, ttsSupported, speak, stopSpeaking };
})();
