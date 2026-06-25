// ponytail: no-op override for the xblocks-contrib formula_equation_preview.js,
// which uses MathJax v2 Hub.Queue/getAllJax/Callback APIs that silently fail
// in MathJax v4. The built-in ProblemBlockDisplay.js (activated when
// USE_EXTRACTED_PROBLEM_BLOCK=False) already provides a working v4-compatible
// preview via typesetPromise. Remove this file when xblocks-contrib ships
// MathJax v4-compatible preview JS or when USE_EXTRACTED_PROBLEM_BLOCK is
// permanently removed.
