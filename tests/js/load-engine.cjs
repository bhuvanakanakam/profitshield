// Test-only seam: execute the actual inline financial code, never a copied model.
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const root = join(__dirname, '..', '..');
function between(source, start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first + start.length);
  if (first < 0 || last < 0) throw new Error(`Financial test boundary changed: ${start} → ${end}`);
  return source.slice(first + start.length, last);
}
module.exports = function loadEngine(fixture) {
  const html = readFileSync(join(root, 'index.html'), 'utf8');
  const match = html.match(/<script\b[^>]*>([\s\S]*?)<\/script>/);
  if (!match) throw new Error('Expected the application inline script');
  const source = match[1];
  const engine = source.slice(0, source.indexOf('/* ============ rendering ============ */'));
  if (!engine || !engine.includes('function analyze()')) throw new Error('Financial engine boundary changed');
  const simulation = between(source, '/* ============ simulation ============ */', 'function chartHTML');
  const itemCost = 'function itemCost' + between(source, 'function itemCost', '/* ============ payouts ============ */');
  // No document/fetch/require/process APIs are available in this context.
  const context = vm.createContext({});
  const run = code => vm.runInContext(code, context, {timeout: 2000});
  new vm.Script(source); // Parse all production JavaScript, including rendering.
  run(engine + '\n' + simulation + '\n' + itemCost);
  if (fixture) run(`SERVER_MODEL=${JSON.stringify(fixture.financial_model)};WEEKS=${JSON.stringify(fixture.weeks)};DB=${JSON.stringify({orders:fixture.orders,errors:fixture.errors,invoices:fixture.invoices})};CUR=WEEKS.at(-1).id;A=analyze();`);
  return {get: expression => JSON.parse(JSON.stringify(run(expression))), run};
};
