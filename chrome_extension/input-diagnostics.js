// Explicit, local-only regression harness. No forum or model calls are made.
async function runInputDiagnostic(command) {
  const fixtureUrl = `${BRIDGE}/diagnostics/input-fixture`;
  const ownedTabs = [];
  const cases = [];
  let tab;
  let cover;
  let testWindow;
  const initial = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  try {
    testWindow = await chrome.windows.create({ url: fixtureUrl, focused: false, width: 760, height: 620 });
    tab = testWindow.tabs[0];
    ownedTabs.push(tab.id);
    await waitForTab(tab.id);
    cover = await chrome.tabs.create({ windowId: testWindow.id, url: fixtureUrl, active: true });
    ownedTabs.push(cover.id);
    await waitForTab(cover.id);
    for (const scenario of [
      { name: "hidden_without_focus_lease", background: true, disableLease: true },
      { name: "hidden_with_focus_lease", background: true },
      { name: "foreground", background: false },
      { name: "keyboard_fallback", background: true, suppressMouse: true },
      { name: "covered_button", background: false, covered: true }
    ]) {
      const target = { tabId: tab.id };
      const calls = [];
      let before;
      let after;
      let activation;
      let error;
      // Start each case with a fresh document; do not inherit focus state or
      // event listeners from another case.
      await chrome.tabs.reload(tab.id);
      await waitForTab(tab.id);
      await chrome.windows.update(testWindow.id, { state: scenario.background ? "minimized" : "normal" });
      await chrome.debugger.attach(target, "1.3");
      const raw = (method, params) => chrome.debugger.sendCommand(target, method, params);
      try {
        const current = await chrome.tabs.get(tab.id);
        if (current.url !== fixtureUrl) throw new Error("Diagnostic tab left its local fixture");
        await raw("Emulation.setFocusEmulationEnabled", { enabled: false });
        await raw("Runtime.evaluate", {
          expression: `document.querySelector('#reset').click(); document.querySelector('#cover').hidden = ${!scenario.covered}`
        });
        await chrome.tabs.update(scenario.background ? cover.id : tab.id, { active: true });
        await new Promise(resolve => setTimeout(resolve, 250));
        before = (await raw("Runtime.evaluate", {
          expression: "window.inputFixtureState()", returnByValue: true
        })).result.value;
        before.tabActive = (await chrome.tabs.get(tab.id)).active;
        before.windowState = (await chrome.windows.get(testWindow.id)).state;
        const send = async (method, params) => {
          calls.push(method);
          // Keep this test target hidden to reproduce input delivery while another
          // page is active. Production still uses Page.bringToFront normally.
          if (scenario.background && method === "Page.bringToFront") return {};
          if (scenario.disableLease && method === "Emulation.setFocusEmulationEnabled") return {};
          if (scenario.suppressMouse && method === "Input.dispatchMouseEvent") return {};
          return raw(method, params);
        };
        try {
          activation = await focusAndActivateWithCdp(send, "document.querySelector('#choice')", "本地测试按钮");
        } catch (exc) { error = exc.message; }
        after = (await raw("Runtime.evaluate", {
          expression: "window.inputFixtureState()", returnByValue: true
        })).result.value;
        const expectedClicks = scenario.covered ? 0 : 1;
        const baseline = scenario.disableLease === true;
        cases.push({ name: scenario.name, baseline, before, after, activation, error, calls,
          passed: baseline ? null : !after.traceInstalled && after.trustedClicks === expectedClicks &&
            (!scenario.background || before.visibility === "hidden") &&
            (scenario.covered ? Boolean(error) : !error && after.selected === true) });
      } finally {
        await raw("Emulation.setFocusEmulationEnabled", { enabled: false }).catch(() => {});
        await chrome.debugger.detach(target).catch(() => {});
      }
    }
    // Exercise the same production wrapper used for selecting and submitting,
    // including restoring its minimized window. Both buttons belong to the inert
    // local fixture, whose submit handler only updates a counter in the page.
    await chrome.tabs.reload(tab.id);
    await waitForTab(tab.id);
    await chrome.windows.update(testWindow.id, { state: "minimized" });
    if ((await chrome.tabs.get(tab.id)).url !== fixtureUrl) throw new Error("Diagnostic tab left its local fixture");
    // Fixture counters live in the page's main JS world; the production execute()
    // helper uses an isolated world and cannot see them. Read through the same
    // CDP context as the earlier cases, releasing it before the production call.
    const readFixture = async () => {
      const target = { tabId: tab.id };
      await chrome.debugger.attach(target, "1.3");
      try {
        const state = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
          expression: "window.inputFixtureState()", returnByValue: true
        });
        if (!state.result?.value) throw new Error("Local fixture counters unavailable");
        return state.result.value;
      } finally { await chrome.debugger.detach(target).catch(() => {}); }
    };
    const before = await readFixture();
    let error;
    try {
      await trustedActivateButton(tab.id, "Select test option");
      await trustedActivateButton(tab.id, "Submit local test");
    } catch (exc) { error = exc.message; }
    const after = await readFixture();
    cases.push({ name: "production_select_and_submit_local", before, after, error,
      passed: !error && after.selected === true && after.trustedClicks === 1 &&
        after.trustedSubmissions === 1 && after.traceInstalled === false });
    return { diagnostic_id: command.diagnostic_id, extension_version: EXTENSION_VERSION,
      ok: cases.filter(item => !item.baseline).every(item => item.passed), cases,
      forum_actions: 0, model_calls: 0 };
  } catch (error) {
    return { diagnostic_id: command.diagnostic_id, extension_version: EXTENSION_VERSION,
      ok: false, error: error.message, cases, forum_actions: 0, model_calls: 0 };
  } finally {
    for (const id of ownedTabs) await chrome.tabs.remove(id).catch(() => {});
    if (initial[0]?.id) await chrome.tabs.update(initial[0].id, { active: true }).catch(() => {});
  }
}
