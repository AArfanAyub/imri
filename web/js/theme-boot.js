/**
 * IMRI — theme bootstrap.
 *
 * A classic (non-module) script, loaded synchronously in <head> before the
 * stylesheet. Its only job is to put the right `data-theme` on <html> before
 * the first paint, so a user who chose light on a dark-mode machine never sees
 * a black flash on launch.
 *
 * `store.js` takes over afterwards and keeps the two in sync. This file must
 * stay dependency-free and under a millisecond.
 */
(function () {
  'use strict';

  var KEY = 'imri.theme';
  var choice = 'system';

  try {
    var saved = window.localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark' || saved === 'system') {
      choice = saved;
    }
  } catch (err) {
    // Private mode or a locked-down profile: fall through to "system".
  }

  var root = document.documentElement;

  if (choice === 'system') {
    var dark = false;
    try {
      dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    } catch (err) {
      dark = false;
    }
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
  } else {
    root.setAttribute('data-theme', choice);
  }

  root.setAttribute('data-theme-choice', choice);
})();
