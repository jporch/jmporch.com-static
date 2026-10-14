(() => {
  "use strict";

  const mobile = window.matchMedia("(max-width: 640px)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const toggle = document.querySelector(".nav-toggle");
  const navigation = document.querySelector(".sidebar-nav");
  const home = document.body.hasAttribute("data-home");
  const panels = home ? [...document.querySelectorAll(".content-panel")] : [];
  let activePanel = panels[0];

  document.body.classList.add("js");

  function closeMenu() {
    toggle.setAttribute("aria-expanded", "false");
    navigation.classList.remove("is-open");
  }

  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    navigation.classList.toggle("is-open", open);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      closeMenu();
      toggle.focus();
    }
  });

  mobile.addEventListener("change", closeMenu);
  navigation.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link) return;
    closeMenu();
    if (home && mobile.matches && link.hash === window.location.hash) {
      event.preventDefault();
      showPanel(true);
    }
  });

  function showPanel(moveFocus = false) {
    if (!home) return;
    const requested = window.location.hash.slice(1);
    const panel = panels.find((candidate) => candidate.id === requested)
      || (requested === "content" ? activePanel : panels[0]);
    const changed = panel !== activePanel;
    panels.forEach((candidate) => { candidate.hidden = candidate !== panel; });
    activePanel = panel;
    document.querySelectorAll(".sidebar-buttons a").forEach((link) => {
      if (link.hash === `#${panel.id}`) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    closeMenu();
    if (moveFocus) {
      panel.querySelector("h1").focus({ preventScroll: true });
      if (mobile.matches) window.scrollTo({ top: 0, behavior: "instant" });
      else panel.querySelector(".viewport").scrollTop = 0;
    }
    if (changed && !reducedMotion.matches) {
      panel.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400 });
    }
  }

  window.addEventListener("hashchange", () => showPanel(true));
  showPanel();

  document.querySelectorAll(".collapse-toggle").forEach((button) => {
    const body = document.getElementById(button.getAttribute("aria-controls"));
    const icon = button.querySelector("span");
    let animation;
    button.addEventListener("click", () => {
      if (animation) animation.cancel();
      const expanded = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(expanded));
      icon.classList.toggle("minus-icon", expanded);
      icon.classList.toggle("plus-icon", !expanded);
      body.hidden = false;
      body.style.overflow = "";
      if (reducedMotion.matches) {
        body.hidden = !expanded;
        return;
      }
      const height = `${body.scrollHeight}px`;
      animation = body.animate(
        expanded ? [{ height: "0px" }, { height }] : [{ height }, { height: "0px" }],
        { duration: 400, easing: "ease", fill: "none" },
      );
      body.style.overflow = "hidden";
      animation.onfinish = () => {
        body.hidden = !expanded;
        body.style.overflow = "";
        animation = undefined;
      };
    });
  });
})();
