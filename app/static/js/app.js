(function () {
  "use strict";

  document.querySelectorAll("[data-reload-on-change]").forEach(function (el) {
    el.addEventListener("change", function () {
      if (!el.value) {
        window.location.href = "/maintenance/new";
        return;
      }
      window.location.href = "/maintenance/new?requestId=" + encodeURIComponent(el.value);
    });
  });
})();
