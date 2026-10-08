"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const ui = {
    grid: $("timeline-grid"),
    template: $("timeline-template"),
    count: $("timeline-count"),
    timezone: $("timezone"),
    dialog: $("timeline-dialog"),
    form: $("timeline-form"),
    name: $("timeline-name"),
    speed: $("timeline-speed"),
    status: $("timeline-status"),
    time: $("timeline-start-time"),
    changeTime: $("change-time"),
    deleteDialog: $("delete-dialog"),
  };
  const state = {
    timelines: new Map(),
    cards: new Map(),
    busyIds: new Set(),
    timezone: readTimezone(),
    hasLoaded: false,
    connected: false,
    revision: 0,
    listController: null,
    pollTimer: null,
    editingId: null,
    deletingId: null,
    formBusy: false,
    deleteBusy: false,
  };
  const numbers = new Intl.NumberFormat("it-IT", { maximumSignificantDigits: 10 });
  let dateFormatter;
  let timeFormatter;
  let zoneFormatter;

  function readTimezone() {
    try {
      return localStorage.getItem("chronos.timezone") === "UTC" ? "UTC" : "Europe/Rome";
    } catch {
      return "Europe/Rome";
    }
  }

  function updateFormatters() {
    dateFormatter = new Intl.DateTimeFormat("it-IT-u-ca-gregory", {
      timeZone: state.timezone, day: "numeric", month: "long", year: "numeric",
    });
    timeFormatter = new Intl.DateTimeFormat("it-IT-u-ca-gregory", {
      timeZone: state.timezone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    });
    zoneFormatter = new Intl.DateTimeFormat("it-IT", {
      timeZone: state.timezone, timeZoneName: "short", hour: "2-digit",
    });
  }

  function announce(message) {
    $("announcer").textContent = message;
  }

  function showActionError(message) {
    $("action-error-text").textContent = message;
    $("action-error").hidden = false;
  }

  function hideActionError() {
    $("action-error").hidden = true;
  }

  function setConnection(connected) {
    state.connected = connected;
    $("connection").classList.toggle("is-online", connected);
    $("connection").classList.toggle("is-offline", !connected);
    const text = connected ? "Connesso al server" : "Server non raggiungibile";
    if ($("connection-text").textContent !== text) $("connection-text").textContent = text;
    $("connection-warning").hidden = connected;
    $("connection-warning-text").textContent = state.hasLoaded
      ? "Connessione interrotta. Gli orari mostrati sono gli ultimi ricevuti e non vengono aggiornati. Nuovo tentativo automatico ogni secondo."
      : "Impossibile caricare le timeline. Controlla la connessione al server; Chronos riprova automaticamente.";
    ui.grid.classList.toggle("is-stale", !connected);
  }

  async function api(path, { method = "GET", body, signal } = {}) {
    let response;
    try {
      response = await fetch(`/api${path}`, {
        method,
        headers: body === undefined ? { Accept: "application/json" } : {
          Accept: "application/json", "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        signal,
      });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error("Server non raggiungibile. Controlla la connessione e riprova.");
    }
    if (response.status === 204) return null;
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Il server ha restituito una risposta non valida. Riprova.");
    }
    if (!response.ok) {
      if (response.status === 404) throw new Error("Questa timeline non esiste più. Aggiorna l’elenco.");
      if (response.status >= 500) throw new Error("Errore del server. L’operazione non è stata completata; riprova.");
      let detail = data.detail;
      if (Array.isArray(detail)) {
        detail = detail.map((item) => item.msg).filter((item) => typeof item === "string").join("; ");
      }
      throw new Error(typeof detail === "string" ? detail : "Dati non validi. Controlla i campi e riprova.");
    }
    return data;
  }

  async function refreshTimelines({ force = false } = {}) {
    if (!force && (state.listController || state.busyIds.size > 0)) return;
    if (state.listController) state.listController.abort();
    const controller = new AbortController();
    state.listController = controller;
    const revision = state.revision;
    const timeout = setTimeout(() => controller.abort("timeout"), 8000);
    try {
      const timelines = await api("/timelines", { signal: controller.signal });
      if (!Array.isArray(timelines) || timelines.some((timeline) =>
        typeof timeline.id !== "string" || typeof timeline.name !== "string" ||
        typeof timeline.speed !== "number" || !["running", "paused"].includes(timeline.status)
      )) throw new Error("Risposta del server non valida.");
      if (state.listController !== controller || revision !== state.revision) return;
      state.timelines = new Map(timelines.map((timeline) => [timeline.id, timeline]));
      state.hasLoaded = true;
      setConnection(true);
      renderTimelines();
    } catch {
      if (state.listController === controller && revision === state.revision) {
        setConnection(false);
        $("initial-loading").hidden = true;
        ui.grid.setAttribute("aria-busy", "false");
        if (!state.hasLoaded) ui.count.textContent = "Timeline non disponibili";
      }
    } finally {
      clearTimeout(timeout);
      if (state.listController === controller) state.listController = null;
    }
  }

  async function poll() {
    await refreshTimelines();
    state.pollTimer = setTimeout(poll, 1000);
  }

  function makeCard(timeline) {
    const fragment = ui.template.content.cloneNode(true);
    const card = fragment.querySelector("article");
    card.querySelector(".button-toggle").addEventListener("click", () => toggleTimeline(timeline.id));
    card.querySelector(".button-edit").addEventListener("click", () => openForm(timeline.id));
    card.querySelector(".button-delete").addEventListener("click", () => openDelete(timeline.id));
    ui.grid.append(card);
    state.cards.set(timeline.id, card);
    return card;
  }

  function renderCard(timeline, card) {
    const paused = timeline.status === "paused";
    const busy = state.busyIds.has(timeline.id);
    const date = timeline.virtual_now ? new Date(timeline.virtual_now) : null;
    const validDate = date && Number.isFinite(date.getTime());
    card.classList.toggle("is-paused", paused);
    card.classList.toggle("is-busy", busy);
    card.classList.toggle("has-time-error", !validDate);
    card.setAttribute("aria-busy", String(busy));
    card.querySelector(".timeline-name").textContent = timeline.name;
    card.querySelector(".status-text").textContent = paused ? "Paused" : "Running";
    card.querySelector(".status-badge").setAttribute("aria-label", paused ? "Paused, in pausa" : "Running, in esecuzione");
    card.querySelector(".virtual-date").textContent = validDate ? dateFormatter.format(date) : "Orologio non rappresentabile";
    card.querySelector(".virtual-time").textContent = validDate ? timeFormatter.format(date) : "Data fuori intervallo";
    const zone = validDate
      ? zoneFormatter.formatToParts(date).find((part) => part.type === "timeZoneName")?.value
      : null;
    card.querySelector(".clock-zone").textContent = state.timezone === "UTC"
      ? "UTC" : `EUROPE/ROME${zone ? ` · ${zone}` : ""}`;
    card.querySelector(".speed-value").textContent = `${numbers.format(timeline.speed)}×`;
    card.querySelector(".speed-equivalence").textContent = timeline.speed === 1
      ? "Tempo reale" : `1 h reale = ${numbers.format(timeline.speed)} h virtuali`;
    const timeError = card.querySelector(".timeline-time-error");
    timeError.hidden = Boolean(validDate);
    timeError.textContent = "Apri Modifica e imposta una data tra l’anno 1 e il 9999.";
    const toggle = card.querySelector(".button-toggle");
    card.querySelector(".toggle-text").textContent = paused ? "Riprendi" : "Pausa";
    toggle.setAttribute("aria-label", `${paused ? "Riprendi" : "Metti in pausa"}: ${timeline.name}`);
    const icon = card.querySelector(".toggle-icon");
    // Both paths are fixed application markup; timeline content is always textContent.
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", paused ? "M6 3.5 13 9l-7 5.5v-11Z" : "M6 4v10M12 4v10");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", paused ? "1.4" : "2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    icon.replaceChildren(path);
    card.querySelector(".button-edit").setAttribute("aria-label", `Modifica: ${timeline.name}`);
    card.querySelector(".button-delete").setAttribute("aria-label", `Elimina: ${timeline.name}`);
    for (const button of card.querySelectorAll("button")) button.disabled = busy;
  }

  function renderTimelines() {
    for (const [id, card] of state.cards) {
      if (!state.timelines.has(id)) {
        const hadFocus = card.contains(document.activeElement);
        card.remove();
        state.cards.delete(id);
        if (hadFocus) $("create-timeline").focus();
      }
    }
    for (const timeline of state.timelines.values()) {
      renderCard(timeline, state.cards.get(timeline.id) || makeCard(timeline));
    }
    const count = state.timelines.size;
    const running = [...state.timelines.values()].filter((timeline) => timeline.status === "running").length;
    ui.count.textContent = `${count} timeline · ${running} in esecuzione`;
    $("empty-state").hidden = count > 0 || !state.hasLoaded;
    $("initial-loading").hidden = true;
    ui.grid.setAttribute("aria-busy", "false");
  }

  async function mutate(id, path, options) {
    if (state.busyIds.has(id)) return;
    state.busyIds.add(id);
    state.revision += 1;
    hideActionError();
    const card = state.cards.get(id);
    if (card && state.timelines.has(id)) renderCard(state.timelines.get(id), card);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      return await api(path, { ...options, signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) {
        throw new Error("Il server non ha risposto in tempo. Verifica l’elenco prima di riprovare: l’operazione potrebbe essere stata salvata.");
      }
      throw error;
    } finally {
      clearTimeout(timeout);
      state.busyIds.delete(id);
      state.revision += 1;
      if (card && state.timelines.has(id)) renderCard(state.timelines.get(id), card);
      void refreshTimelines({ force: true });
    }
  }

  async function toggleTimeline(id) {
    const timeline = state.timelines.get(id);
    if (!timeline || state.busyIds.has(id)) return;
    try {
      const action = timeline.status === "paused" ? "resume" : "pause";
      const updated = await mutate(id, `/timelines/${encodeURIComponent(id)}/${action}`, { method: "POST" });
      if (updated?.id) {
        state.timelines.set(id, updated);
        renderTimelines();
      }
      announce(`${timeline.name}: ${action === "pause" ? "in pausa" : "ripresa"}.`);
    } catch (error) {
      showActionError(error.message);
    }
  }

  function clearFormErrors() {
    for (const id of ["name", "speed", "time"]) {
      $(`${id}-error`).hidden = true;
      ui[id].removeAttribute("aria-invalid");
    }
    $("form-error").hidden = true;
  }

  function fieldError(key, message) {
    $(`${key}-error`).textContent = message;
    $(`${key}-error`).hidden = false;
    ui[key].setAttribute("aria-invalid", "true");
  }

  function openForm(id = null) {
    if (ui.dialog.open || state.formBusy) return;
    const timeline = id ? state.timelines.get(id) : null;
    if (id && !timeline) return;
    state.editingId = id;
    ui.form.reset();
    clearFormErrors();
    ui.name.value = timeline?.name || "";
    ui.speed.value = String(timeline?.speed ?? 1);
    ui.status.value = timeline?.status || "running";
    const date = timeline?.virtual_now ? new Date(timeline.virtual_now) : new Date();
    ui.time.value = Number.isFinite(date.getTime()) ? date.toISOString().slice(0, -1) : new Date().toISOString().slice(0, -1);
    ui.changeTime.checked = false;
    $("status-field").hidden = Boolean(timeline);
    $("change-time-field").hidden = !timeline;
    $("edit-note").hidden = !timeline;
    $("form-title").textContent = timeline ? "Modifica timeline" : "Nuova timeline";
    $("form-description").textContent = timeline
      ? "Aggiorna il nome, il ritmo o il punto nel tempo."
      : "Scegli un punto di partenza e lascia scorrere il tempo.";
    $("save-timeline").textContent = timeline ? "Salva modifiche" : "Crea timeline";
    // The label contains only static text and an application-owned UTC badge.
    $("time-label").firstChild.textContent = timeline ? "Nuova data e ora " : "Data e ora iniziale ";
    updateTimeField();
    ui.dialog.showModal();
    ui.name.focus();
  }

  function updateTimeField() {
    const enabled = !state.editingId || ui.changeTime.checked;
    ui.time.disabled = !enabled;
    ui.time.required = enabled;
    $("time-field").hidden = !enabled;
  }

  function closeForm() {
    if (!state.formBusy) ui.dialog.close();
  }

  function readUtcInput(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/.exec(value);
    if (!match || Number(match[1]) < 1) return null;
    const iso = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6] || "00"}.${(match[7] || "").padEnd(3, "0")}Z`;
    const parsed = new Date(iso);
    if (!Number.isFinite(parsed.getTime()) || parsed.toISOString() !== iso) return null;
    return iso;
  }

  function validateForm() {
    clearFormErrors();
    const name = ui.name.value.trim();
    const speed = Number(ui.speed.value);
    const requiresTime = !state.editingId || ui.changeTime.checked;
    const iso = requiresTime ? readUtcInput(ui.time.value) : null;
    let valid = true;
    if (!name || name.length > 200) {
      fieldError("name", "Inserisci un nome da 1 a 200 caratteri.");
      valid = false;
    }
    if (!ui.speed.value || !Number.isFinite(speed) || speed < 0.01 || speed > 1000) {
      fieldError("speed", "Inserisci una velocità compresa tra 0,01 e 1000.");
      valid = false;
    }
    if (requiresTime && !iso) {
      fieldError("time", "Inserisci una data UTC valida, con anno compreso tra 0001 e 9999.");
      valid = false;
    }
    if (!valid) {
      ui.form.querySelector('[aria-invalid="true"]')?.focus();
      return null;
    }
    return { name, speed, iso };
  }

  function setFormBusy(busy) {
    state.formBusy = busy;
    ui.form.setAttribute("aria-busy", String(busy));
    for (const control of ui.form.querySelectorAll("button, input, select")) control.disabled = busy;
    if (!busy) updateTimeField();
    $("save-timeline").textContent = busy ? "Salvataggio…" : state.editingId ? "Salva modifiche" : "Crea timeline";
  }

  async function saveForm(event) {
    event.preventDefault();
    if (state.formBusy) return;
    const values = validateForm();
    if (!values) return;
    const id = state.editingId;
    const body = { name: values.name, speed: values.speed };
    if (id) {
      if (values.iso) body.virtual_time = values.iso;
    } else {
      body.start_time = values.iso;
      body.status = ui.status.value;
    }
    setFormBusy(true);
    try {
      const updated = await mutate(id || "__create__", id ? `/timelines/${encodeURIComponent(id)}` : "/timelines", {
        method: id ? "PATCH" : "POST", body,
      });
      if (updated?.id) {
        state.timelines.set(updated.id, updated);
        state.hasLoaded = true;
        renderTimelines();
      }
      ui.dialog.close();
      announce(id ? "Timeline aggiornata." : "Timeline creata.");
    } catch (error) {
      $("form-error").textContent = error.message;
      $("form-error").hidden = false;
    } finally {
      setFormBusy(false);
    }
  }

  function openDelete(id) {
    const timeline = state.timelines.get(id);
    if (!timeline || ui.deleteDialog.open || state.deleteBusy) return;
    state.deletingId = id;
    $("delete-name").textContent = `“${timeline.name}”`;
    $("delete-error").hidden = true;
    ui.deleteDialog.showModal();
    $("cancel-delete").focus();
  }

  function closeDelete() {
    if (!state.deleteBusy) ui.deleteDialog.close();
  }

  async function deleteTimeline(event) {
    event.preventDefault();
    const id = state.deletingId;
    if (!id || state.deleteBusy) return;
    state.deleteBusy = true;
    $("delete-form").setAttribute("aria-busy", "true");
    for (const button of ui.deleteDialog.querySelectorAll("button")) button.disabled = true;
    $("confirm-delete").textContent = "Eliminazione…";
    try {
      await mutate(id, `/timelines/${encodeURIComponent(id)}`, { method: "DELETE" });
      state.timelines.delete(id);
      renderTimelines();
      ui.deleteDialog.close();
      $("create-timeline").focus();
      announce("Timeline eliminata.");
    } catch (error) {
      $("delete-error").textContent = error.message;
      $("delete-error").hidden = false;
    } finally {
      state.deleteBusy = false;
      $("delete-form").setAttribute("aria-busy", "false");
      for (const button of ui.deleteDialog.querySelectorAll("button")) button.disabled = false;
      $("confirm-delete").textContent = "Elimina timeline";
    }
  }

  ui.timezone.value = state.timezone;
  updateFormatters();
  ui.timezone.addEventListener("change", () => {
    state.timezone = ui.timezone.value;
    try { localStorage.setItem("chronos.timezone", state.timezone); } catch { /* Storage may be unavailable. */ }
    updateFormatters();
    if (state.hasLoaded) renderTimelines();
  });
  $("create-timeline").addEventListener("click", () => openForm());
  $("empty-create").addEventListener("click", () => openForm());
  $("close-form").addEventListener("click", closeForm);
  $("cancel-form").addEventListener("click", closeForm);
  ui.changeTime.addEventListener("change", updateTimeField);
  ui.form.addEventListener("submit", saveForm);
  ui.dialog.addEventListener("cancel", (event) => { if (state.formBusy) event.preventDefault(); });
  $("close-delete").addEventListener("click", closeDelete);
  $("cancel-delete").addEventListener("click", closeDelete);
  $("delete-form").addEventListener("submit", deleteTimeline);
  ui.deleteDialog.addEventListener("cancel", (event) => { if (state.deleteBusy) event.preventDefault(); });
  $("dismiss-error").addEventListener("click", hideActionError);
  $("retry-connection").addEventListener("click", () => { void refreshTimelines({ force: true }); });
  window.addEventListener("online", () => { void refreshTimelines({ force: true }); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refreshTimelines({ force: true });
  });
  void poll();
})();
