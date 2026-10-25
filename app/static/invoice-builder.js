// The invoice builder: price-list picker, line totals, running total and due date. No libraries.
(function () {
  const form = document.getElementById("builder");
  if (!form) return;
  const catalogue = JSON.parse(document.getElementById("catalogue").textContent || "[]");
  const byKey = Object.fromEntries(catalogue.map((o) => [o.key, o]));
  const body = form.querySelector("#lines tbody");
  const grand = document.getElementById("grand");
  const dueOut = document.getElementById("due_out");
  const issue = document.getElementById("issue_date");
  const terms = document.getElementById("terms_days");

  const pounds = (n) => "£" + n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (text) => {
    const v = parseFloat(String(text).replace(/[£,\s]/g, ""));
    return Number.isFinite(v) ? v : 0;
  };

  function recalc() {
    let total = 0;
    body.querySelectorAll("tr.line").forEach((row) => {
      const qtyText = row.querySelector(".qty").value.trim();
      const unit = num(row.querySelector(".unit").value);
      const qty = qtyText === "" ? 1 : num(qtyText);
      const amount = Math.round(qty * unit * 100) / 100;
      row.querySelector(".line-total").textContent = pounds(amount);
      total += amount;
    });
    grand.textContent = pounds(total);
    const start = issue.value ? new Date(issue.value + "T12:00:00") : new Date();
    const due = new Date(start.getTime() + num(terms.value) * 86400000);
    dueOut.textContent = due.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  function newRow() {
    const first = body.querySelector("tr.line");
    const row = first.cloneNode(true);
    row.querySelectorAll("input").forEach((i) => (i.value = ""));
    row.querySelectorAll("select").forEach((s) => (s.selectedIndex = 0));
    row.querySelector(".line-total").textContent = "£0.00";
    body.appendChild(row);
    return row;
  }

  form.addEventListener("input", recalc);
  form.addEventListener("change", (event) => {
    if (event.target.classList.contains("pick")) {
      const row = event.target.closest("tr");
      const option = byKey[event.target.value];
      if (option) {
        row.querySelector(".desc").value = option.label;
        row.querySelector(".unit").value = option.price;
        if (!row.querySelector(".qty").value) row.querySelector(".qty").value = "1";
        const nominal = row.querySelector(".nominal");
        if (option.nominal) nominal.value = option.nominal;
      }
    }
    recalc();
  });
  form.addEventListener("click", (event) => {
    if (event.target.id === "add-line") {
      newRow().querySelector(".pick").focus();
    } else if (event.target.classList.contains("remove")) {
      if (body.querySelectorAll("tr.line").length > 1) event.target.closest("tr").remove();
      else event.target.closest("tr").querySelectorAll("input").forEach((i) => (i.value = ""));
      recalc();
    }
  });
  // Enter in a text box should not issue the invoice by accident.
  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.tagName === "INPUT" && event.target.type === "text") event.preventDefault();
  });
  recalc();
})();
