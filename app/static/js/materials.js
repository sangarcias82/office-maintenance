(function () {
  "use strict";

  var btn = document.getElementById("add-material-row");
  var host = document.getElementById("material-rows");
  if (!btn || !host) return;

  var labels = {};
  try {
    labels = JSON.parse(btn.getAttribute("data-labels") || "{}");
  } catch (err) {
    labels = {};
  }

  btn.addEventListener("click", function () {
    var row = document.createElement("div");
    row.className = "material-row";
    row.style.marginBottom = "0.5rem";
    row.innerHTML =
      '<input name="material_name" placeholder="' + (labels.name || "Material") + '"/>' +
      '<input name="material_quantity" type="number" step="0.01" min="0.01" placeholder="' + (labels.qty || "Qty") + '"/>' +
      '<input name="material_unit" placeholder="' + (labels.unit || "Unit") + '" value="ea"/>' +
      '<input name="material_notes" placeholder="' + (labels.notes || "Notes") + '"/>';
    host.appendChild(row);
  });
})();
