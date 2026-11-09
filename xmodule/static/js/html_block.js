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
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = url;
    shadowRoot.appendChild(link);
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
   * Typeset the block's formulas once MathJax has finished starting up.
   *
   * The content is rendered server-side, so MathJax does not revisit it on its
   * own; the legacy inline initializer used to queue this typesetting pass, and
   * it has to keep working for both rendering paths. For themed blocks the
   * formulas live in the shadow tree, so `target` is the shadow content root
   * rather than the host element.
   *
   * @param {Element} target - The element (or shadow content root) to typeset.
   */
  function typesetMath(target) {
    if (typeof MathJax !== 'undefined' && MathJax !== null &&
        MathJax.startup && MathJax.startup.promise) {
      MathJax.startup.promise.then(function () {
        MathJax.typesetPromise([target]);
      });
    }
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

    var root = sandbox(element);
    typesetMath(root);

    getThemes(initArgs.mfe_config_api).then(function (themes) {
      var seen = {};
      [...themes.core, ...themes.theme].forEach(function (url) {
        if (!seen[url]) {
          seen[url] = true;
          addStylesheet(root.getRootNode(), url);
        }
      });
    });
  }

  window.HtmlBlock = HtmlBlock;
}());
