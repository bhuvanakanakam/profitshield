const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {execFileSync} = require('node:child_process');
const loadEngine = require('./load-engine.cjs');
const fixtures = join(__dirname, '..', 'fixtures');
const audit = JSON.parse(readFileSync(join(fixtures, 'audit_baselines.json')));
let synthetic;
const close = (actual, expected, tolerance=1e-8) => {
  assert.equal(typeof actual, 'number');
  assert.ok(Number.isFinite(actual));
  assert.ok(Math.abs(actual-expected)<tolerance, `${actual} differs from ${expected}`);
};
const exported = mode => JSON.parse(execFileSync(process.env.PYTHON || 'python3',
  [join(__dirname, '..', 'export_sample_data.py'), mode], {encoding:'utf8', timeout:30000}));

// Import the same files through the Python backend rather than duplicating its mapping.
const imported = exported('samples');
synthetic=exported('synthetic');

test('financial models come from the canonical server adapter', () => {
  assert.equal(imported.financial_model.metrics[imported.weeks.at(-1).id].estimated,true);
  assert.ok(imported.financial_model.assumptions.some(x=>x.includes('Accounting profit unavailable')));
});

test('CSV-backed financial results preserve audited sample baselines', () => {
  const app=loadEngine(imported);
  close(app.get('A.c.profit-A.p.profit'), 164.03);
  close(app.get('(A.c.gross/A.p.gross-1)*100'), audit.imported.sales_change_pct);
  close(app.get("-A.findings.find(f=>f.kind==='comm').impact"), audit.imported.commission_overcharge);
  close(app.get("A.findings.find(f=>f.kind==='price'&&f.g==='mozz').impact"), audit.imported.mozzarella_impact);
  close(app.get("A.findings.find(f=>f.kind==='err').impact"), audit.imported.error_change);
  // Correctness fix: document-copy exposure is informational, never a payment deduction.
  close(app.get("A.findings.find(f=>f.kind==='dup').exposure"), audit.imported.duplicate_document_amount);
});

test('sample profit bridge reconciles each comparable week', () => {
  const app=loadEngine(imported);
  for(const week of imported.weeks.slice(1)){
    app.run(`CUR=${JSON.stringify(week.id)};A=analyze()`);
    close(app.get('A.check'), app.get('A.c.profit'));
  }
});

test('synthetic sales, fees, recipe costs and estimate are independently known', () => {
  const app=loadEngine(synthetic);
  close(app.get('A.p.gross'), 20.5);
  close(app.get('A.c.gross'), 41);
  close(app.get('A.c.contractComm'), 6.4);
  close(app.get('A.c.over'), 1.6);
  close(app.get('A.c.unappPromo'), 2);
  close(app.get('A.c.err'), 1);
  close(app.get('A.p.food'), 3.83);
  close(app.get('A.c.food'), 8.78);
  close(app.get('A.p.profit'), 13.47);
  close(app.get('A.c.profit'), 21.22);
  close(app.get('A.c.profit-A.p.profit'), 7.75);
  close(app.get('A.check'), app.get('A.c.profit'));
});

test('recipe costs are supplied by the server model', () => {
  const app=loadEngine(synthetic);
  close(app.get("itemCost('marg', A.p.P)"),2.48);
  close(app.get("itemCost('marg', A.c.P)"),3.04);
});

test('backend and frontend synthetic fixtures describe the same transactions', () => {
  const actual=exported('synthetic');
  const project=orders=>orders.map(o=>({id:o.id,channel:o.channel,week:o.week,subtotal:o.subtotal,rate:o.rate,
    items:o.items.map(i=>({sku:i.sku,qty:i.qty}))})).sort((a,b)=>a.id.localeCompare(b.id));
  assert.deepEqual(project(actual.orders), project(synthetic.orders));
  const app=loadEngine(actual);
  close(app.get('A.c.profit'),21.22);
  close(app.get('A.c.profit-A.p.profit'),7.75);
});

test('backend prices and duplicate-document display preserve cent baseline', () => {
  const fixture=structuredClone(synthetic);
  fixture.invoices.push({...structuredClone(fixture.invoices[1]),source:'Second document'});
  const app=loadEngine(fixture);
  close(app.get("A.PT[CUR].mozz"),5.6);
  close(app.get("A.PT[CUR].soda"),1.35);
  assert.equal(app.get('dupGroups().length'),1);
  close(app.get('A.c.profit'),21.22);
  close(app.get("A.findings.find(f=>f.kind==='dup').impact"),0);
  close(app.get("A.findings.find(f=>f.kind==='dup').exposure"),56);
});
