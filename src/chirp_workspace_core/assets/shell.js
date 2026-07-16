(() => {
  "use strict";

  const palette = document.getElementById("workspace-command-palette");
  const search = document.querySelector("[data-workspace-command-search]");
  const commandItems = [...document.querySelectorAll("[data-workspace-command-item]")];
  const empty = document.querySelector("[data-workspace-command-empty]");
  let paletteInvoker = null;
  let navigationInvoker = null;

  function isTypingTarget(target) {
    return target instanceof HTMLElement && (
      target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)
    );
  }

  function openPalette(invoker) {
    if (!(palette instanceof HTMLDialogElement)) return;
    paletteInvoker = invoker instanceof HTMLElement ? invoker : document.activeElement;
    palette.showModal();
    if (search instanceof HTMLInputElement) {
      search.value = "";
      filterCommands("");
      search.focus();
    }
  }

  function filterCommands(query) {
    const normalized = query.trim().toLocaleLowerCase();
    let visible = 0;
    for (const item of commandItems) {
      const matches = (item.dataset.commandText || "").toLocaleLowerCase().includes(normalized);
      item.hidden = !matches;
      if (matches) visible += 1;
    }
    if (empty instanceof HTMLElement) empty.hidden = visible !== 0;
  }

  document.addEventListener("click", (event) => {
    const opener = event.target instanceof Element
      ? event.target.closest("[data-workspace-command-open]")
      : null;
    if (opener && palette instanceof HTMLDialogElement) {
      event.preventDefault();
      openPalette(opener);
    }

    const command = event.target instanceof Element
      ? event.target.closest("[data-workspace-command-item] a")
      : null;
    if (command && palette instanceof HTMLDialogElement && palette.open) palette.close();

    const toggle = event.target instanceof Element
      ? event.target.closest("[data-workspace-nav-toggle]")
      : null;
    if (toggle instanceof HTMLButtonElement) {
      const sidebar = document.getElementById("workspace-sidebar");
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      sidebar?.toggleAttribute("data-workspace-open", !expanded);
    }

    const sidebarLink = event.target instanceof Element
      ? event.target.closest("#workspace-sidebar a")
      : null;
    if (sidebarLink) {
      const sidebar = document.getElementById("workspace-sidebar");
      const navToggle = document.querySelector("[data-workspace-nav-toggle]");
      sidebar?.removeAttribute("data-workspace-open");
      navToggle?.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    const commandKey = (event.metaKey || event.ctrlKey) && event.key.toLocaleLowerCase() === "k";
    const slashKey = event.key === "/" && !isTypingTarget(event.target);
    if (commandKey || slashKey) {
      event.preventDefault();
      openPalette(event.target);
    }
  });

  search?.addEventListener("input", () => {
    if (search instanceof HTMLInputElement) filterCommands(search.value);
  });

  palette?.addEventListener("close", () => {
    if (paletteInvoker instanceof HTMLElement && paletteInvoker.isConnected) paletteInvoker.focus();
  });

  palette?.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && palette instanceof HTMLDialogElement) {
      event.preventDefault();
      palette.close();
    }
  });

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    navigationInvoker = event.detail?.elt instanceof HTMLElement ? event.detail.elt : document.activeElement;
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail?.target;
    if (!(target instanceof HTMLElement) || target.id !== "workspace-main") return;
    const destination = target.querySelector("[data-workspace-autofocus], [data-workspace-page-heading]");
    if (destination instanceof HTMLElement) {
      if (!destination.hasAttribute("tabindex")) destination.setAttribute("tabindex", "-1");
      destination.focus({ preventScroll: true });
    } else if (navigationInvoker instanceof HTMLElement && navigationInvoker.isConnected) {
      navigationInvoker.focus({ preventScroll: true });
    }
  });

  const connectionStatus = document.querySelector("[data-workspace-sse-status]");
  document.body.addEventListener("chirp:sse:connected", () => {
    if (connectionStatus instanceof HTMLElement) connectionStatus.textContent = "Notifications connected.";
  });
  document.body.addEventListener("chirp:sse:disconnected", () => {
    if (connectionStatus instanceof HTMLElement) {
      connectionStatus.textContent = "Notifications disconnected; reconnecting.";
    }
  });
})();
