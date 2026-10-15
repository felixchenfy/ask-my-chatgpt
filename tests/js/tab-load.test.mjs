import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../chrome_extension/service-worker.js', import.meta.url), 'utf8');
const code = source.slice(source.indexOf('async function waitForTab('), source.indexOf('async function navigate('));
function harness(mode) {
  const listeners = new Set(), removed = new Set(), timers = new Map();
  let reads = 0, state = 'loading', now = 0;
  const signal = (id, info) => [...listeners].forEach(fn => fn(id, info));
  const scope = {
    Date: { now: () => now },
    setTimeout: fn => { timers.set(1, fn); return 1; }, clearTimeout: id => timers.delete(id),
    chrome: { tabs: {
      get: async () => {
        reads++;
        if (mode === 'missing') throw new Error('No tab');
        if (mode === 'race' && reads === 1) {
          state = 'complete'; queueMicrotask(() => signal(7, {status:'complete'}));
          return {status:'loading'};
        }
        return {status: mode === 'already' ? 'complete' : state, discarded:false};
      },
      onUpdated: {addListener: fn => listeners.add(fn), removeListener: fn => listeners.delete(fn)},
      onRemoved: {addListener: fn => removed.add(fn), removeListener: fn => removed.delete(fn)},
    } },
  };
  vm.runInNewContext(code, scope);
  return {wait: () => scope.waitForTab(7), signal,
    expire: async () => {now=30000; for (const fn of [...timers.values()]) await fn();},
    complete: () => {state='complete';},
    close: () => [...removed].forEach(fn => fn(7)),
    clean: () => assert.equal(listeners.size + removed.size + timers.size, 0)};
}
const flush = async () => { for(let i=0;i<5;i++) await Promise.resolve(); };
test('completion between initial snapshot and continuation is not lost', async () => {
  const h=harness('race'); const promise=h.wait(); await flush(); await h.expire();
  await promise; h.clean();
});
test('already loaded and normal complete clean their observers', async () => {
  for (const mode of ['already','normal']) {
    const h=harness(mode); const p=h.wait(); await flush();
    if(mode==='normal') {h.signal(99,{status:'complete'}); h.signal(7,{title:'unrelated'}); h.signal(7,{status:'complete'});}
    await p; h.clean();
  }
});
test('deadline rechecks actual state before declaring timeout', async () => {
  const h=harness('normal'); const p=h.wait(); await flush(); h.complete(); await h.expire();
  await p; h.clean();
});
test('real loading timeout retains bounded diagnostics and cleans observers', async () => {
  const h=harness('normal'); const p=h.wait();
  const rejected=assert.rejects(p, e => e.tabLoadEvidence?.status==='loading' && e.tabLoadEvidence.elapsedMs===30000);
  await flush(); await h.expire(); await rejected; h.clean();
});
test('missing tab and closing tab stop waiting and clean observers', async () => {
  for(const mode of ['missing','closed']) {
    const h=harness(mode); const p=h.wait(); const rejected=assert.rejects(p);
    await flush(); if(mode==='closed') h.close(); await h.expire(); await rejected; h.clean();
  }
});
