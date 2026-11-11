// Tests for platform/code-workspace/foxcs-code-workspace.js's pure helpers.
//   node --test platform/code-workspace/tests/test_code_workspace.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ws = require('../foxcs-code-workspace.js');
const ids = (code) => ws.styleNotes(code).map((n) => n.id);

test('clean code gets no tips', () => {
  assert.deepEqual(ws.styleNotes('score = 0\nprint("Score:", score)\n# a comment\nif score == 0:\n    print("Zero")'), []);
});

test('extra spaces around print and inside parentheses are tips, never errors', () => {
  assert.deepEqual(ids('print ("Hi")'), ['space-before-paren']);
  assert.deepEqual(ids('print( "Hi" )'), ['space-inside-parens']);
  assert.deepEqual(ids('print ( "Hi" )'), ['space-before-paren', 'space-inside-parens']);
});

test('each tip is reported once, at its first line', () => {
  const notes = ws.styleNotes('print("ok")\nprint ("a")\nprint ("b")');
  assert.equal(notes.length, 1);
  assert.equal(notes[0].line, 2);
});

test('spacing around = in assignment', () => {
  assert.deepEqual(ids('score=0'), ['spaces-around-equals']);
  assert.deepEqual(ids('score  =  0'), ['spaces-around-equals']);
  assert.deepEqual(ids('score = 0'), []);
  assert.deepEqual(ids('if score == 0:\n    pass'), []);
  assert.deepEqual(ids('print(end="")'), []);
});

test('text inside strings and comments is never flagged', () => {
  assert.deepEqual(ids('print("print ( x )")'), []);
  assert.deepEqual(ids("print('a=b')"), []);
  assert.deepEqual(ids('print("ok")  # print ( "x" )'), []);
  assert.deepEqual(ids('print("say \\"hi\\" ( now )")'), []);
});

test('comments and tabs', () => {
  assert.deepEqual(ids('#comment'), ['comment-space']);
  assert.deepEqual(ids('# comment\n#!/usr/bin/env python3'), []);
  assert.deepEqual(ids('if True:\n\tprint("x")'), ['tab-indent']);
});

test('codeOnly keeps positions, so a comment is found after a string with #', () => {
  assert.equal(ws.codeOnly('print("#1")  # note'), 'print("__")  ');
});

test('output comparison ignores trailing spaces and blank last lines', () => {
  assert.deepEqual(ws.compareOutput('Hello  \nWorld\n\n', 'Hello\nWorld'), { match: true });
  assert.deepEqual(ws.compareOutput('Hello\r\nWorld\r\n', 'Hello\nWorld'), { match: true });
});

test('output comparison reports the first different line', () => {
  assert.deepEqual(ws.compareOutput('Score: 10\nDone', 'Score: 30\nDone'), { match: false, line: 1, got: 'Score: 10', want: 'Score: 30' });
  assert.deepEqual(ws.compareOutput('A', 'A\nB'), { match: false, line: 2, got: null, want: 'B' });
  assert.deepEqual(ws.compareOutput('A\nB', 'A'), { match: false, line: 2, got: 'B', want: null });
  assert.deepEqual(ws.compareOutput(' Hello', 'Hello').match, false); // leading spaces are real output
});

test('download filenames are safe .py names', () => {
  assert.equal(ws.safeFilename('status_board.py'), 'status_board.py');
  assert.equal(ws.safeFilename('My Program!'), 'My_Program.py');
  assert.equal(ws.safeFilename('../../etc/passwd'), 'etc_passwd.py');
  assert.equal(ws.safeFilename(''), 'my_program.py');
  assert.equal(ws.safeFilename(null), 'my_program.py');
});
