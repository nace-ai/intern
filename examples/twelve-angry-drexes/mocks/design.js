/* One look for the three screens: Design A, the figure plate (rules in design.css).
   Sets <html data-design="a"> before the page paints. There is no switch: the address and browser storage are not read. */
(function () {
  "use strict";
  document.documentElement.dataset.design = "a";
})();
