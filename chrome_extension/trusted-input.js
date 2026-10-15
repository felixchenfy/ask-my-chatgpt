async function focusAndReplaceTextWithCdp(sendCommand, selector, text) {
  const documentTree = await sendCommand("DOM.getDocument", {
    depth: -1,
    pierce: true
  });
  const rootNodeId = documentTree.root?.nodeId;
  if (!rootNodeId) throw new Error("无法读取签到页面 DOM");

  const found = await sendCommand("DOM.querySelector", {
    nodeId: rootNodeId,
    selector
  });
  if (!found.nodeId) throw new Error("没有找到签到备注输入框的 DOM 节点");

  // A synthetic mouse click can be swallowed by the page or leave BODY focused.
  // DOM.focus targets the exact textarea node before trusted keyboard input.
  await sendCommand("DOM.focus", { nodeId: found.nodeId });

  const focused = await sendCommand("Runtime.evaluate", {
    expression: `(() => {
      const expected = document.querySelector(${JSON.stringify(selector)});
      const active = document.activeElement;
      return {
        found: Boolean(expected),
        focused: Boolean(expected && active === expected),
        activeTag: active?.tagName || null
      };
    })()`,
    returnByValue: true
  });
  const focusState = focused.result?.value;
  if (!focusState?.found || !focusState.focused) {
    throw new Error(
      `签到备注输入框无法获得焦点；当前焦点：${focusState?.activeTag || "未知"}`
    );
  }

  const selectAll = {
    modifiers: 2,
    key: "a",
    code: "KeyA",
    windowsVirtualKeyCode: 65,
    nativeVirtualKeyCode: 65
  };
  await sendCommand("Input.dispatchKeyEvent", { type: "rawKeyDown", ...selectAll });
  await sendCommand("Input.dispatchKeyEvent", { type: "keyUp", ...selectAll });
  await sendCommand("Input.insertText", { text });

  const inserted = await sendCommand("Runtime.evaluate", {
    expression: `(() => {
      const expected = document.querySelector(${JSON.stringify(selector)});
      return {
        found: Boolean(expected),
        focused: Boolean(expected && document.activeElement === expected),
        value: expected?.value ?? null
      };
    })()`,
    returnByValue: true
  });
  const insertedState = inserted.result?.value;
  if (!insertedState?.found || insertedState.value !== text) {
    throw new Error(
      `签到备注输入后校验失败；当前值：${JSON.stringify(insertedState?.value ?? null)}`
    );
  }
  return insertedState;
}

async function focusAndActivateWithCdp(sendCommand, expression, label) {
  // CDP mouse input can be ignored when the target remains a background tab.
  // Activate the page before resolving coordinates or dispatching input.
  await sendCommand("Page.bringToFront");
  // Chrome can report the workflow tab/window as active while the renderer still
  // exposes a hidden page. In that state CDP accepts input commands but the page
  // receives no events. Focus emulation makes this exact renderer active for the
  // short, audited activation lease; it is always removed below.
  await sendCommand("Emulation.setFocusEmulationEnabled", { enabled: true });
  let objectId = null;
  try {
    const evaluated = await sendCommand("Runtime.evaluate", {
      expression,
      returnByValue: false
    });
    objectId = evaluated.result?.objectId;
    if (!objectId) throw new Error(`没有找到唯一、可操作的${label}`);

    // Resolve the hit point only after scrolling the exact current element into
    // view. This avoids stale document coordinates and works on site controls
    // whose click handler does not implement keyboard activation.
    const target = await sendCommand("Runtime.callFunctionOn", {
      objectId,
      functionDeclaration: `function () {
        if (!(this instanceof HTMLElement)) return { ready: false, reason: "not-element" };
        if (this.matches(":disabled") || this.getAttribute("aria-disabled") === "true") {
          return { ready: false, reason: "disabled" };
        }
        this.scrollIntoView({ block: "center", inline: "center" });
        this.focus({ preventScroll: true });
        const activationState = { target: this, trace: [], listeners: [] };
        window.__wcaTrustedActivationState = activationState;
        window.__wcaTrustedActivationTrace = activationState.trace;
        for (const type of [
          "pointerdown", "mousedown", "pointerup", "mouseup", "click", "keydown", "keypress", "keyup"
        ]) {
          const handler = event => {
            if (event.target === this || this.contains(event.target)) {
              activationState.trace.push({
                type,
                isTrusted: event.isTrusted,
                targetTag: event.target?.tagName || null,
                defaultPrevented: event.defaultPrevented
              });
            }
          };
          activationState.listeners.push({ type, handler });
          document.addEventListener(type, handler, true);
        }
        const rect = this.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const hit = document.elementFromPoint(x, y);
        return {
          ready: rect.width > 0 && rect.height > 0 && (hit === this || this.contains(hit)),
          focused: document.activeElement === this,
          documentFocused: document.hasFocus(),
          visibilityState: document.visibilityState,
          x,
          y,
          hitTag: hit?.tagName || null
        };
      }`,
      returnByValue: true
    });
    const targetState = target.result?.value;
    if (!targetState?.ready || !Number.isFinite(targetState.x) || !Number.isFinite(targetState.y)) {
      throw new Error(
        `${label}当前无法被准确点击；命中元素：${targetState?.hitTag || targetState?.reason || "未知"}`
      );
    }

    const point = { x: targetState.x, y: targetState.y };
    await sendCommand("Input.dispatchMouseEvent", { type: "mouseMoved", ...point });
    await sendCommand("Input.dispatchMouseEvent", {
      type: "mousePressed",
      ...point,
      button: "left",
      buttons: 1,
      clickCount: 1
    });
    await sendCommand("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      ...point,
      button: "left",
      buttons: 0,
      clickCount: 1
    });

    // Record what the page actually received. A successful CDP command only
    // means Chrome accepted the input request; it does not prove that a trusted
    // click reached the intended DOM node.
    const observeTrace = async forceCleanup => {
      const observed = await sendCommand("Runtime.evaluate", {
        expression: `new Promise(resolve => setTimeout(() => {
          const state = window.__wcaTrustedActivationState;
          const trace = Array.isArray(state?.trace) ? state.trace : [];
          const trustedClick = trace.some(
            event => event.type === "click" && event.isTrusted === true
          );
          const exactTargetFocused = Boolean(state?.target?.isConnected &&
            document.activeElement === state.target && !state.target.matches(':disabled') &&
            state.target.getAttribute('aria-disabled') !== 'true');
          if (trustedClick || ${forceCleanup ? "true" : "false"}) {
            for (const listener of state?.listeners || []) {
              document.removeEventListener(listener.type, listener.handler, true);
            }
            delete window.__wcaTrustedActivationState;
            delete window.__wcaTrustedActivationTrace;
          }
          resolve({
            trace,
            trustedClick,
            exactTargetFocused,
            documentFocused: document.hasFocus(),
            visibilityState: document.visibilityState
          });
        }, 75))`,
        awaitPromise: true,
        returnByValue: true
      });
      return observed.result?.value;
    };

    let observedState = await observeTrace(false);
    if (!observedState || typeof observedState.trustedClick !== "boolean") {
      throw new Error(`${label}点击后的事件状态无法读取；不再发送按键，避免重复操作`);
    }
    if (!observedState?.trustedClick) {
      if (!observedState.exactTargetFocused) {
        throw new Error(`${label}未收到点击且目标焦点已改变；不向其他控件发送 Enter`);
      }
      // Some Chrome states accept exact DOM focus and keyboard input while
      // suppressing pointer input for a hidden/background document. Native
      // button activation with Enter is a safe fallback because the same exact
      // target remains focused and the resulting trusted click is still traced.
      const enter = {
        key: "Enter",
        code: "Enter",
        windowsVirtualKeyCode: 13,
        nativeVirtualKeyCode: 13
      };
      // rawKeyDown alone produces keydown but not the character event that
      // activates a native button in Chrome. Supply Enter's text with keyDown.
      await sendCommand("Input.dispatchKeyEvent", {
        type: "keyDown", ...enter, text: "\r", unmodifiedText: "\r"
      });
      await sendCommand("Input.dispatchKeyEvent", { type: "keyUp", ...enter });
      observedState = await observeTrace(true);
    }
    const trace = Array.isArray(observedState?.trace) ? observedState.trace : [];
    const trustedClick = observedState?.trustedClick === true;
    if (!trustedClick) {
      const eventSummary = trace.length
        ? trace.map(event => `${event.type}:${event.isTrusted ? "trusted" : "synthetic"}`).join(", ")
        : "无";
      throw new Error(
        `${label}没有收到可信 click 事件（鼠标与 Enter 均已尝试）；` +
        `页面焦点：${observedState?.documentFocused ? "是" : "否"}；` +
        `页面状态：${observedState?.visibilityState || targetState.visibilityState || "未知"}；事件：${eventSummary}`
      );
    }
    return { target: targetState, trace };
  } finally {
    if (objectId) {
      // Also clean up on covered targets and CDP/observation failures. Only
      // remove listeners belonging to this exact activation target.
      await sendCommand("Runtime.callFunctionOn", {
        objectId,
        functionDeclaration: `function () {
          const state = window.__wcaTrustedActivationState;
          if (state?.target !== this) return;
          for (const listener of state.listeners || []) {
            document.removeEventListener(listener.type, listener.handler, true);
          }
          delete window.__wcaTrustedActivationState;
          delete window.__wcaTrustedActivationTrace;
        }`,
        returnByValue: true
      }).catch(() => {});
      await sendCommand("Runtime.releaseObject", { objectId }).catch(() => {});
    }
    await sendCommand("Emulation.setFocusEmulationEnabled", { enabled: false }).catch(() => {});
  }
}
