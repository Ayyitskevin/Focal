// Copy-link buttons (gallery / portal / proposal / contract / invoice admin
// pages). Tiny global handler — scans the page on load and wires every
// .copy-link button to navigator.clipboard.writeText with its data-copy
// payload. Sibling .copy-feedback span flashes "Copied!" for 2.5s. Graceful
// failure copy when the API isn't available (older browsers, non-HTTPS).
(function () {
  let timer = null;
  document.querySelectorAll(".copy-link").forEach(btn => {
    btn.addEventListener("click", async () => {
      const fb = btn.parentElement.querySelector(".copy-feedback");
      try {
        await navigator.clipboard.writeText(btn.dataset.copy);
        if (fb) fb.textContent = "Copied!";
      } catch (e) {
        if (fb) fb.textContent = "Copy failed — select and copy manually.";
      }
      if (!fb) return;
      fb.hidden = false;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fb.hidden = true, 2500);
    });
  });
})();
