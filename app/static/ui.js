// Small behaviours that used to be inline handlers. The site's content security policy allows only scripts served from the site itself.
document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const ask = target.closest("[data-confirm]");
  if (ask && !window.confirm(ask.getAttribute("data-confirm"))) {
    event.preventDefault();
    return;
  }
  if (target.closest("[data-print]")) window.print();
});

document.addEventListener("change", (event) => {
  const box = event.target;
  if (!(box instanceof HTMLInputElement) || !box.hasAttribute("data-check-all")) return;
  document.querySelectorAll(box.getAttribute("data-check-all")).forEach((other) => {
    if (!other.disabled) other.checked = box.checked;
  });
});
