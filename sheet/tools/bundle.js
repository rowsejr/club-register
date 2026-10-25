/**
 * Joins the files in src/ into dist/Club.gs: the single file pasted into
 * Extensions ▸ Apps Script. Run with: npm run bundle
 */
'use strict';
const fs = require('fs');
const path = require('path');

const src = path.join(__dirname, '..', 'src');
const out = path.join(__dirname, '..', 'dist', 'Club.gs');
const files = fs.readdirSync(src).filter((f) => f.endsWith('.js')).sort();
const header = [
  '/**',
  ' * CLUB REGISTER',
  ' * Paste this whole file into Extensions ▸ Apps Script in the club register spreadsheet, then save.',
  ' * It is made from the files in src/ by "npm run bundle". To change it, change those files and bundle again.',
  ' * Files: ' + files.join(', '),
  ' */',
  '',
].join('\n');
const body = files.map((f) => '// ===== ' + f + ' =====\n\n' + fs.readFileSync(path.join(src, f), 'utf8').trim() + '\n').join('\n');
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, header + body);
console.log('Wrote ' + path.relative(process.cwd(), out) + ' (' + body.split('\n').length + ' lines from ' + files.length + ' files)');
