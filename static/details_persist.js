// Persist <details id="..."> open/closed state in localStorage. Any
// <details> element with an id gets remembered across page loads — refresh
// no longer collapses Kevin's setup. Untagged <details> behave as before.
(function () {
  const KEY = "details:";  // localStorage namespace
  document.querySelectorAll("details[id]").forEach(el => {
    const k = KEY + el.id;
    const stored = localStorage.getItem(k);
    if (stored === "1") el.open = true;
    else if (stored === "0") el.open = false;
    el.addEventListener("toggle", () => {
      try {
        localStorage.setItem(k, el.open ? "1" : "0");
      } catch (e) { /* quota exceeded / private mode — fail silent */ }
    });
  });
})();
