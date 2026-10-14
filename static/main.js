(() => {
  "use strict";

  const mobile = window.matchMedia("(max-width: 640px)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const toggle = document.querySelector(".nav-toggle");
  const navigation = document.querySelector(".sidebar-nav");
  const header = document.querySelector(".sidebar-header");
  const coveredContent = document.querySelectorAll("#content, .copyright-notice, #skip-nav");
  const home = document.body.hasAttribute("data-home");
  const panels = home ? [...document.querySelectorAll(".content-panel")] : [];
  let activePanel = panels[0];
  let menuAnimation;

  document.body.classList.add("js");

  function setMenu(open, animate = true) {
    const wasOpen = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(open));
    navigation.inert = mobile.matches && !open;
    const overlayOpen = mobile.matches && open;
    document.documentElement.classList.toggle("menu-open", overlayOpen);
    coveredContent.forEach((element) => { element.inert = overlayOpen; });
    if (mobile.matches) {
      navigation.style.setProperty("--menu-top", `${Math.max(0, header.getBoundingClientRect().bottom)}px`);
    }

    if (!mobile.matches || reducedMotion.matches || !animate) {
      if (menuAnimation) menuAnimation.cancel();
      menuAnimation = undefined;
      navigation.classList.toggle("is-open", open);
      navigation.style.overflow = "";
      return;
    }
    if (open === wasOpen) return;

    // Continue from the current height when a second click reverses a slide.
    const startHeight = navigation.getBoundingClientRect().height;
    if (menuAnimation) menuAnimation.cancel();
    navigation.classList.add("is-open");
    navigation.style.overflow = "hidden";
    const endHeight = open ? navigation.getBoundingClientRect().height : 0;
    menuAnimation = navigation.animate(
      [{ height: `${startHeight}px` }, { height: `${endHeight}px` }],
      { duration: 400, easing: "ease" },
    );
    menuAnimation.onfinish = () => {
      navigation.classList.toggle("is-open", open);
      navigation.style.overflow = "";
      menuAnimation = undefined;
    };
  }

  function closeMenu() {
    setMenu(false);
  }

  toggle.addEventListener("click", () => {
    setMenu(toggle.getAttribute("aria-expanded") !== "true");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      closeMenu();
      toggle.focus();
    }
  });

  mobile.addEventListener("change", () => setMenu(false, false));
  window.addEventListener("resize", () => {
    if (mobile.matches && toggle.getAttribute("aria-expanded") === "true") {
      navigation.style.setProperty("--menu-top", `${Math.max(0, header.getBoundingClientRect().bottom)}px`);
    }
  });
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
