/*
 * foxcs-code-workspace.js -- an in-page Python editor for FoxCS lessons.
 * Added 2026-11-04, per Jay: students "can do exercises right in the code
 * editor in the platform," starting in Unit 01, and Coding Exercises take a
 * .py upload only. So the editor can Download a real .py file: a student on
 * a Chromebook without VS Code writes and runs code here, downloads it, and
 * uploads that file to the Coding Exercise.
 *
 * Runs real Python in the browser through Skulpt (adopted 2026-09-04; see
 * 02-authoring-system/skulpt-runtime/). Clean-code notes are tips only: they
 * never block a run or mark anything wrong (Jay, 2026-11-04: valid code with
 * extra spaces is correct, with a note on writing it cleanly).
 *
 * Markup:
 *   <div class="foxcs-workspace" data-filename="my_program.py"
 *        data-expected="Hello!">print("Hello!")</div>
 *   data-expected is optional: when present, a Check button compares the
 *   program's output with it, line by line, ignoring trailing spaces.
 * Then: FoxCSWorkspace.mountAll(document, window.Sk)
 *
 * Pure helpers (styleNotes, compareOutput, safeFilename) are exported for
 * Node tests: 02-authoring-system/tools/tests/test_code_workspace.mjs.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.FoxCSWorkspace = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Replaces the contents of string literals and comments so notes only look at code. */
  function codeOnly(line) {
    var out = '';
    var quote = null;
    for (var i = 0; i < line.length; i++) {
      var ch = line[i];
      if (quote) {
        if (ch === '\\') { out += '__'; i++; continue; }
        if (ch === quote) { quote = null; out += ch; } else { out += '_'; }
      } else if (ch === '"' || ch === "'") {
        quote = ch; out += ch;
      } else if (ch === '#') {
        break;
      } else {
        out += ch;
      }
    }
    return out;
  }

  var RULES = [
    { id: 'space-before-paren', test: function (c) { return /\b(print|input|int|float|str|type|len)\s+\(/.test(c); },
      note: 'No space between a function name and its parenthesis: print("Hi"), not print ("Hi").' },
    { id: 'space-inside-parens', test: function (c) { return /\(\s+\S|\S\s+\)/.test(c); },
      note: 'No spaces just inside parentheses: print("Hi"), not print( "Hi" ).' },
    { id: 'spaces-around-equals', test: function (c) {
        var m = /^\s*[A-Za-z_]\w*(\s*)=(?!=)(\s*)\S/.exec(c);
        return !!m && (m[1] !== ' ' || m[2] !== ' ');
      },
      note: 'Put exactly one space on each side of = when you assign a variable: score = 0.' },
    { id: 'tab-indent', test: function (c, raw) { return /^\t/.test(raw); },
      note: 'Indent with 4 spaces, not a tab.' },
  ];

  /** Clean-code tips for a program. Each rule is reported once, with the first line it applies to. */
  function styleNotes(code) {
    var notes = [];
    var seen = {};
    var lines = String(code).split('\n');
    for (var n = 0; n < lines.length; n++) {
      var raw = lines[n];
      var c = codeOnly(raw);
      var comment = raw.indexOf('#') >= 0 && codeOnly(raw).length < raw.length ? raw.slice(codeOnly(raw).length) : '';
      for (var r = 0; r < RULES.length; r++) {
        var rule = RULES[r];
        if (!seen[rule.id] && rule.test(c, raw)) {
          seen[rule.id] = true;
          notes.push({ id: rule.id, line: n + 1, note: rule.note });
        }
      }
      if (!seen['comment-space'] && /^#[^ !\s]/.test(comment)) {
        seen['comment-space'] = true;
        notes.push({ id: 'comment-space', line: n + 1, note: 'Put a space after # in a comment: # like this.' });
      }
    }
    return notes;
  }

  /** Compares program output with what was expected, line by line, ignoring trailing spaces and blank last lines. */
  function compareOutput(actual, expected) {
    function lines(text) {
      var out = String(text).replace(/\r\n/g, '\n').split('\n').map(function (l) { return l.replace(/\s+$/, ''); });
      while (out.length && out[out.length - 1] === '') { out.pop(); }
      return out;
    }
    var a = lines(actual);
    var e = lines(expected);
    for (var i = 0; i < Math.max(a.length, e.length); i++) {
      if (a[i] !== e[i]) {
        return { match: false, line: i + 1, got: a[i] === undefined ? null : a[i], want: e[i] === undefined ? null : e[i] };
      }
    }
    return { match: true };
  }

  /** A safe .py filename for the download. */
  function safeFilename(name) {
    var base = String(name || 'my_program').replace(/\.py$/i, '').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');
    return (base || 'my_program') + '.py';
  }

  function runWithSkulpt(Sk, code, onDone) {
    var captured = [];
    Sk.configure({
      output: function (t) { captured.push(t); },
      read: function (x) {
        if (Sk.builtinFiles === undefined || Sk.builtinFiles.files[x] === undefined) { throw "File not found: '" + x + "'"; }
        return Sk.builtinFiles.files[x];
      },
      inputfun: function (prompt) { return window.prompt(prompt) || ''; },
      inputfunTakesPrompt: true,
    });
    Sk.misceval.asyncToPromise(function () {
      return Sk.importMainWithBody('<stdin>', false, code, true);
    }).then(
      function () { onDone(captured.join(''), null); },
      function (err) { onDone(captured.join(''), String(err)); }
    );
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) { node.className = cls; }
    if (text) { node.textContent = text; }
    return node;
  }

  function mount(container, Sk) {
    var doc = container.ownerDocument;
    var starter = container.textContent.replace(/^\n/, '');
    var filename = safeFilename(container.getAttribute('data-filename'));
    var expected = container.getAttribute('data-expected');
    container.textContent = '';
    container.classList.add('foxcs-workspace-ready');

    var bar = el(doc, 'div', 'fw-toolbar', filename);
    var editor = el(doc, 'textarea', 'fw-editor');
    editor.value = starter;
    editor.spellcheck = false;
    editor.rows = Math.max(4, starter.split('\n').length + 1);
    editor.setAttribute('aria-label', 'Python code editor for ' + filename);
    var buttons = el(doc, 'div', 'fw-buttons');
    var run = el(doc, 'button', 'fw-run', 'Run');
    var check = expected !== null ? el(doc, 'button', 'fw-check', 'Check') : null;
    var download = el(doc, 'button', 'fw-download', 'Download ' + filename);
    var reset = el(doc, 'button', 'fw-reset', 'Reset');
    [run, check, download, reset].forEach(function (b) { if (b) { b.type = 'button'; buttons.appendChild(b); } });
    var output = el(doc, 'pre', 'fw-output');
    output.setAttribute('aria-live', 'polite');
    var result = el(doc, 'div', 'fw-result');
    var tips = el(doc, 'ul', 'fw-tips');
    [bar, editor, buttons, output, result, tips].forEach(function (n) { container.appendChild(n); });

    function showTips() {
      tips.textContent = '';
      styleNotes(editor.value).forEach(function (t) {
        tips.appendChild(el(doc, 'li', null, 'Clean-code tip (line ' + t.line + '): ' + t.note));
      });
    }
    function execute(then) {
      output.textContent = 'Running...';
      result.textContent = '';
      runWithSkulpt(Sk, editor.value, function (out, err) {
        output.textContent = out + (err ? (out ? '\n' : '') + err : '') || '(no output)';
        showTips();
        if (then) { then(out, err); }
      });
    }
    run.addEventListener('click', function () { execute(); });
    if (check) {
      check.addEventListener('click', function () {
        execute(function (out, err) {
          if (err) { result.textContent = 'Fix the error above first, then check again.'; result.className = 'fw-result fw-wrong'; return; }
          var cmp = compareOutput(out, expected);
          result.className = 'fw-result ' + (cmp.match ? 'fw-right' : 'fw-wrong');
          result.textContent = cmp.match ? 'Matches the expected output.'
            : 'Line ' + cmp.line + ' differs. Expected: ' + JSON.stringify(cmp.want) + '. Got: ' + JSON.stringify(cmp.got) + '.';
        });
      });
    }
    download.addEventListener('click', function () {
      var blob = new Blob([editor.value.replace(/\n?$/, '\n')], { type: 'text/x-python' });
      var a = doc.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      doc.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
    });
    reset.addEventListener('click', function () {
      editor.value = starter;
      output.textContent = '';
      result.textContent = '';
      tips.textContent = '';
    });
    return { editor: editor, run: run, check: check, download: download, reset: reset, output: output, result: result, tips: tips };
  }

  function mountAll(doc, Sk) {
    var all = doc.querySelectorAll('.foxcs-workspace:not(.foxcs-workspace-ready)');
    var mounted = [];
    for (var i = 0; i < all.length; i++) { mounted.push(mount(all[i], Sk)); }
    return mounted;
  }

  return { styleNotes: styleNotes, compareOutput: compareOutput, safeFilename: safeFilename, codeOnly: codeOnly, mount: mount, mountAll: mountAll };
});
