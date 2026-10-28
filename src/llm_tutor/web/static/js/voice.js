/* Голосовой ввод: браузер распознаёт сам, сервер — только если браузер не умеет.
 *
 * Первый путь — Web Speech API: запись не покидает устройство, текст растёт
 * по ходу речи, ответ приходит сразу. Firefox и часть мобильных браузеров его
 * не поддерживают (или Chrome не смог достучаться до своего сервера) — тогда
 * пишем MediaRecorder-ом и отправляем звук в POST /api/lesson/transcribe.
 *
 * Полученный текст не отправляется сам: он вставляется в поле, ученик его
 * видит и может поправить — расшифровка ошибается на терминах.
 */
window.Voice = (() => {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const MAX_MS = 120_000;
  const MAX_BYTES = 8 * 1024 * 1024;
  const BROWSER_TEXT = "Говорю… нажмите ещё раз, чтобы закончить.";
  const SERVER_TEXT = "Записываю…";

  // Браузер пишет webm (Chrome, Firefox) или mp4 (Safari); сервер знает ещё
  // wav/mp3/flac/ogg/aac — см. llm/stt.py.
  const FORMATS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/aac": "aac",
  };

  function browserRecognition() {
    return Boolean(Recognition);
  }

  function serverRecording() {
    return Boolean(window.MediaRecorder && navigator.mediaDevices?.getUserMedia);
  }

  function available() {
    return browserRecognition() || serverRecording();
  }

  function lang() {
    return document.documentElement.lang || "ru-RU";
  }

  function clock(ms) {
    const total = Math.min(Math.floor(ms / 1000), 999);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function formatOf(type) {
    const base = (type || "").split(";")[0].trim().toLowerCase();
    return FORMATS[base] || "webm";
  }

  function recorderType() {
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    return candidates.find((type) => window.MediaRecorder.isTypeSupported?.(type)) || "";
  }

  function readBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  function errorText(code) {
    switch (code) {
      case "not-allowed":
      case "service-not-allowed":
        return "Микрофон выключен: разрешите доступ к микрофону в настройках браузера.";
      case "no-speech":
        return "Речь не слышна — попробуйте ещё раз ближе к микрофону.";
      case "audio-capture":
        return "Микрофон не найден — проверьте, что он подключён.";
      default:
        return "Не удалось распознать речь — попробуйте ещё раз.";
    }
  }

  /**
   * Кнопка-микрофон. `button` переключает запись, `status` показывает, что
   * происходит (живой регион), `onText` получает расшифровку.
   */
  function create({ button, status, onText, label = "Говорить" }) {
    const caption = button.querySelector(".voice-label");
    let mode = null;
    let recognition = null;
    let recorder = null;
    let stream = null;
    let chunks = [];
    let stopTimer = null;
    let ticker = null;
    let startedAt = 0;
    let transcript = "";
    let delivered = false;
    let cancelled = false;

    function setStatus(text) {
      status.textContent = text || "";
      status.hidden = !text;
    }

    function setActive(on) {
      button.classList.toggle("recording", on);
      button.setAttribute("aria-pressed", String(on));
      const text = on ? "Закончить запись" : label;
      button.setAttribute("aria-label", text);
      button.title = text;
      if (caption) caption.textContent = on ? "Стоп" : label;
    }

    function cleanup() {
      clearTimeout(stopTimer);
      clearInterval(ticker);
      stopTimer = null;
      ticker = null;
      if (recognition) {
        const instance = recognition;
        recognition = null;
        instance.onresult = null;
        instance.onerror = null;
        instance.onend = null;
        try {
          instance.stop();
        } catch {
          /* уже остановлено */
        }
      }
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          /* уже остановлен */
        }
      }
      recorder = null;
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        stream = null;
      }
      mode = null;
      setActive(false);
    }

    /** Расшифровка дошла до поля (один раз за запись). */
    function finish(text) {
      // Панель уже перерисовали (destroy): текст вставлять некуда.
      if (delivered || cancelled) return;
      delivered = true;
      const clean = (text || "").trim();
      cleanup();
      if (clean) {
        setStatus("");
        onText(clean);
      } else {
        setStatus("Речь не распознана — попробуйте ещё раз.");
      }
    }

    function fail(message) {
      if (cancelled) return;
      delivered = true;
      cleanup();
      setStatus(message);
    }

    /* --- путь 1: Web Speech API --- */

    function startBrowser() {
      mode = "browser";
      delivered = false;
      cancelled = false;
      transcript = "";
      recognition = new Recognition();
      recognition.lang = lang();
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.maxAlternatives = 1;
      recognition.onresult = (event) => {
        let interim = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const text = result[0]?.transcript || "";
          if (result.isFinal) transcript += `${text} `;
          else interim += text;
        }
        setStatus(interim.trim() ? `…${interim.trim()}` : BROWSER_TEXT);
      };
      recognition.onerror = (event) => {
        if (delivered || cancelled || event.error === "aborted") return;
        // Часть речи уже распознана — отдаём её, а не теряем из-за сбоя в конце.
        if (transcript.trim() && !["not-allowed", "service-not-allowed", "audio-capture"].includes(event.error)) {
          finish(transcript);
          return;
        }
        // Снимаем обработчики со старого распознавателя: его onend не должен
        // ни отдать пустую расшифровку, ни остановить уже начатую запись.
        const instance = recognition;
        recognition = null;
        if (instance) {
          instance.onresult = null;
          instance.onerror = null;
          instance.onend = null;
          try {
            instance.stop();
          } catch {
            /* уже остановлено */
          }
        }
        // Chrome распознаёт речь на своём сервере: пропала сеть — пробуем наш.
        if (event.error === "network" && serverRecording()) {
          startServer();
          return;
        }
        fail(errorText(event.error));
      };
      recognition.onend = () => {
        if (!delivered && !cancelled) finish(transcript);
      };
      try {
        recognition.start();
      } catch {
        recognition = null;
        if (serverRecording()) startServer();
        else fail("Микрофон недоступен в этом браузере.");
        return;
      }
      setActive(true);
      setStatus(BROWSER_TEXT);
      stopTimer = setTimeout(() => stop(), MAX_MS);
    }

    /* --- путь 2: запись и серверное распознавание --- */

    async function startServer() {
      mode = "server";
      delivered = false;
      cancelled = false;
      setActive(true);
      setStatus("Готовлю микрофон…");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        const message =
          error && error.name === "NotAllowedError"
            ? "Микрофон выключен: разрешите доступ к микрофону в настройках браузера."
            : "Микрофон недоступен — проверьте, что он подключён и разрешён.";
        fail(message);
        return;
      }
      if (cancelled) {
        cleanup();
        return;
      }
      chunks = [];
      const type = recorderType();
      try {
        recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
      } catch {
        recorder = new MediaRecorder(stream);
      }
      const mime = recorder.mimeType || type || "audio/webm";
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        const blob = new Blob(chunks, { type: mime });
        chunks = [];
        send(blob, formatOf(mime));
      });
      recorder.start();
      startedAt = Date.now();
      setStatus(`${SERVER_TEXT} 0:00 — нажмите «Стоп», когда закончите.`);
      ticker = setInterval(() => {
        setStatus(`${SERVER_TEXT} ${clock(Date.now() - startedAt)} — нажмите «Стоп», когда закончите.`);
      }, 1000);
      stopTimer = setTimeout(() => stop(), MAX_MS);
    }

    async function send(blob, format) {
      cleanup();
      if (cancelled) return;
      if (!blob || !blob.size) {
        setStatus("Ничего не записалось — попробуйте ещё раз.");
        return;
      }
      if (blob.size > MAX_BYTES) {
        setStatus("Запись слишком длинная — говорите короче.");
        return;
      }
      setStatus("Распознаю запись…");
      try {
        const audio = await readBase64(blob);
        const result = await window.App.request("/api/lesson/transcribe", {
          method: "POST",
          body: { audio, format },
          timeout: 90_000,
        });
        finish(result?.text);
      } catch (problem) {
        fail(problem?.message || "Не удалось распознать запись — попробуйте ещё раз.");
      }
    }

    /* --- общее --- */

    function start() {
      if (mode) return;
      cancelled = false;
      if (browserRecognition()) startBrowser();
      else if (serverRecording()) startServer();
      else fail("Голосовой ввод в этом браузере недоступен — ответьте текстом.");
    }

    function stop() {
      if (!mode) return;
      if (mode === "browser" && recognition) {
        // onend придёт следом и отдаст накопленное.
        try {
          recognition.stop();
        } catch {
          finish(transcript);
        }
        return;
      }
      if (mode === "server" && recorder && recorder.state === "recording") {
        setStatus("Распознаю запись…");
        try {
          recorder.stop();
        } catch {
          cleanup();
        }
        return;
      }
      // Разрешение на микрофон ещё спрашивают: запись не начнётся.
      if (mode === "server") {
        cancelled = true;
        cleanup();
        setStatus("");
      }
    }

    function toggle() {
      if (mode) stop();
      else start();
    }

    /** Отменить запись без расшифровки (панель перерисовали, ученик ушёл). */
    function destroy() {
      cancelled = true;
      const wasActive = Boolean(mode);
      cleanup();
      if (wasActive) setStatus("");
    }

    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", toggle);
    setActive(false);

    return { start, stop, toggle, destroy, get active() { return Boolean(mode); } };
  }

  return { available, create, browserRecognition, serverRecording };
})();
