/**
 * Renders a Text (HTML) XBlock inside a shadow root so that the block is styled
 * by the MFE theme without leaking styles in either direction.
 *
 * The block's HTML is rendered server-side, so there is nothing to "render"
 * here: the job is to move the existing children into a shadow root and attach
 * the theme stylesheets to it.
 */
(function () {
  'use strict';

  var CDN_CORE = 'https://cdn.jsdelivr.net/npm/@openedx/paragon@23/dist/core.min.css';

  /**
   * Fetch the Paragon theme stylesheet URLs from the MFE config API.
   *
   * Returns `core` and `theme` arrays built from PARAGON_THEME_URLS. Each entry
   * may be layered (Paragon's stylesheet plus a brand override), so both URLs are
   * kept; only `core` falls back to the CDN when the deployment publishes none.
   * Studio's editor reads the same key and builds its preview from it, so whatever
   * is attached here is what is previewed there.
   *
   * @param {string} mfeConfigApiUrl - URL of the MFE config API.
   * @returns {Promise<{core: string[], theme: string[]}>}
   */
  async function getThemes(mfeConfigApiUrl) {
    let themeUrls;
    try {
      var response = await fetch(mfeConfigApiUrl);
      var mfeConfig = await response.json();
      themeUrls = mfeConfig.PARAGON_THEME_URLS || {};
    } catch (error) {
      // Not fatal: the block still renders, just with the CDN defaults.
      console.error('Text XBlock: failed to fetch theme URLs:', error);
      themeUrls = {};
    }
    var variant = themeUrls.variants && themeUrls.variants[activeVariant(themeUrls)];
    var coreUrls = layerUrls(themeUrls.core);
    return {
      core: coreUrls.length ? coreUrls : [CDN_CORE],
      theme: layerUrls(variant),
    };
  }

  /**
   * Work out which variant is active.
   *
   * Two shapes are published in practice: frontend-base's `Theme`
   * (https://github.com/openedx/frontend-base/blob/main/types.ts) carries an optional
   * `defaults` map naming the active light and dark variants, while tutor-indigo
   * (https://github.com/overhangio/tutor-indigo) ships only a `variants` map with
   * nothing pointing at one. So read `defaults` when it is there, and otherwise
   * take the first variant present rather than dropping a configured theme.
   */
  function activeVariant(themeUrls) {
    if (themeUrls.defaults && themeUrls.defaults.light) {
      return themeUrls.defaults.light;
    }
    var variants = themeUrls.variants || {};
    if (variants.light) {
      return 'light';
    }
    var names = Object.keys(variants);
    return names.length ? names[0] : null;
  }

  /**
   * Return the stylesheet URLs for a `core` or `variants` entry, in the order
   * the platform applies them.
   *
   * Entries appear either nested (`{urls: {default, brandOverride}}`, as
   * frontend-platform and tutor-indigo publish) or flat (`{url}`). The nested
   * form is layered: `default` is the Paragon stylesheet and `brandOverride` is
   * the brand package, which is built to be imported *on top of* it rather than
   * to replace it. Returning both matches frontend-platform's loader; a
   * deployment that points them at the same file is de-duplicated by the caller.
   */
  function layerUrls(entry) {
    if (!entry) {
      return [];
    }
    if (entry.urls) {
      return [entry.urls.default, entry.urls.brandOverride].filter(Boolean);
    }
    return entry.url ? [entry.url] : [];
  }

  /**
   * Attach a stylesheet inside the shadow root only.
   *
   * The theme declares its custom properties on `:root, :host`, so inside the
   * shadow tree they apply to the host element and inherit to the content.
   * Attaching the stylesheets to the document as well would restyle every other
   * block sharing the page, since Paragon core carries top-level rules for bare
   * element selectors.
   */
  function addStylesheet(shadowRoot, url) {
    return new Promise(function (resolve) {
      var link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = url;
      link.onload = resolve;
      link.onerror = resolve;
      shadowRoot.appendChild(link);
    });
  }

  /**
   * Attach the theme stylesheets to the shadow root and wait for them, and for
   * their fonts, before anything measures the content.
   *
   * MathJax measures the font when it typesets and bakes the result into the
   * SVG output. If the theme CSS arrives while it is typesetting, the formulas
   * get a wrong scale, which is what made some glyphs huge.
   *
   * @param {ShadowRoot} shadowRoot - The root to attach the stylesheets to.
   * @param {string[]} urls - Stylesheet URLs, in load order.
   * @returns {Promise} Resolves once the stylesheets and fonts are ready.
   */
  function waitForStylesheets(shadowRoot, urls) {
    var seen = {};
    var loads = [];
    urls.forEach(function (url) {
      if (!seen[url]) {
        seen[url] = true;
        loads.push(addStylesheet(shadowRoot, url));
      }
    });
    return Promise.all(loads).then(function () {
      return (document.fonts && document.fonts.ready) ? document.fonts.ready : undefined;
    });
  }

  /**
   * Move the block's server-rendered children into a shadow root.
   */
  function sandbox(element) {
    if (element.shadowRoot) {
      return element.shadowRoot.querySelector('.xblock-root');
    }

    var shadowRoot = element.attachShadow({ mode: 'open' });
    // A real <body> element, so the theme's `body` rules (font, line height,
    // colour) match inside the shadow tree without a hand-maintained copy.
    var root = document.createElement('body');
    root.classList.add('xblock-root');

    // Adopt the rendered content rather than re-rendering it, so anything the
    // server produced (images, anchors, embedded markup) is preserved as-is.
    while (element.firstChild) {
      root.appendChild(element.firstChild);
    }

    shadowRoot.appendChild(root);
    return root;
  }

  /**
   * Correct the root font size when the block is rendered inside an iframe.
   *
   * Studio's base styles set `html { font-size: 62.5% }`, so 1rem is 10px in the
   * legacy UI, while the theme sizes everything in rem assuming 1rem is 16px.
   * The Authoring MFE renders the unit, and with it this block, inside an
   * iframe, so the correction is scoped there and the LMS page is left alone.
   */
  function normalizeRootFontSize() {
    if (window.self !== window.top) {
      document.documentElement.style.fontSize = '100%';
    }
  }

  /**
   * Copy MathJax's SVG glyph definitions into the shadow root.
   *
   * MathJax 2 renders SVG output as `<svg><use href="#glyph">`, pointing at a
   * shared `<svg><defs id="MathJax_SVG_glyphs">` that lives in the document
   * body. ID references do not cross a shadow boundary, so the formulas render
   * with zero-size glyphs unless the definitions are also inside the shadow
   * tree.
   *
   * @param {ShadowRoot} shadowRoot - The root the block content lives in.
   */
  function copyMathJaxGlyphs(shadowRoot) {
    var defs = document.getElementById('MathJax_SVG_glyphs');
    if (!defs || !defs.closest) {
      return;
    }
    var glyphs = defs.closest('svg');
    if (!glyphs) {
      return;
    }
    var previous = shadowRoot.querySelector('#MathJax_SVG_glyphs');
    if (previous) {
      var previousSvg = previous.closest('svg');
      if (previousSvg) {
        previousSvg.remove();
      }
    }
    shadowRoot.appendChild(glyphs.cloneNode(true));
  }

  /**
   * Attach the MathJax glyph cache to the target, if it is inside a shadow root
   * and contains SVG output.
   *
   * @param {Element} target - The element (or shadow content root) typeset.
   */
  function copyGlyphsFor(target) {
    var root = target.getRootNode ? target.getRootNode() : null;
    if (root && root.host && target.querySelector('.MathJax_SVG')) {
      copyMathJaxGlyphs(root);
    }
  }

  /**
   * Typeset the target with whichever MathJax API is loaded.
   *
   * @param {Element} target - The element (or shadow content root) to typeset.
   * @returns {boolean} Whether the typeset pass was queued.
   */
  function queueTypeset(target) {
    if (typeof MathJax === 'undefined' || MathJax === null) {
      return false;
    }
    if (MathJax.startup && MathJax.startup.promise) {
      MathJax.startup.promise.then(function () {
        MathJax.typesetPromise([target]);
      });
      return true;
    }
    if (MathJax.Hub) {
      MathJax.Hub.Queue(['Typeset', MathJax.Hub, target], function () {
        copyGlyphsFor(target);
      });
      return true;
    }
    return false;
  }

  /**
   * Typeset the block's formulas once MathJax has finished starting up.
   *
   * The content is rendered server-side, so MathJax does not revisit it on its
   * own; the legacy inline initializer used to queue this typesetting pass, and
   * it has to keep working for both rendering paths. Ulmo ships MathJax 2.7.5,
   * which exposes `MathJax.Hub`, so use its queue and keep the MathJax 3 promise
   * API as a fallback. For themed blocks the formulas live in the shadow tree,
   * so `target` is the shadow content root rather than the host element.
   *
   * `window.MathJax` can be the page's config placeholder before MathJax.js
   * loads, so wait for the real object instead of dropping the pass.
   *
   * @param {Element} target - The element (or shadow content root) to typeset.
   */
  function typesetMath(target) {
    if (queueTypeset(target)) {
      return;
    }
    var attempts = 0;
    var timer = setInterval(function () {
      attempts += 1;
      if (queueTypeset(target) || attempts > 100) {
        clearInterval(timer);
      }
    }, 100);
  }

  /**
   * XBlock view entry point. Invoked by the XBlock JS runtime as
   * `HtmlBlock(runtime, element, initArgs)`.
   *
   * @param {Object} runtime - XBlock runtime (unused).
   * @param {Element} element - The block's root element.
   * @param {Object} initArgs - Data supplied by `Fragment.initialize_js`.
   */
  function HtmlBlock(runtime, element, initArgs) {
    if (!initArgs || !initArgs.use_mfe_theming) {
      typesetMath(element);
      return;
    }

    normalizeRootFontSize();
    var root = sandbox(element);

    getThemes(initArgs.mfe_config_api).then(function (themes) {
      return waitForStylesheets(root.getRootNode(), [...themes.core, ...themes.theme]);
    }).then(function () {
      typesetMath(root);
    });
  }

  window.HtmlBlock = HtmlBlock;
}());
