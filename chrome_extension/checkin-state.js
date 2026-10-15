function inspectCheckin(root = document, pageLocation = location) {
  const text = root.body?.innerText || "";
  const lastSign = text.match(/您上次签到时间[：:]\s*([^\n]+)/)?.[1]?.trim() || null;
  const alerts = [...root.querySelectorAll('[role="alert"],[role="status"],[data-sonner-toast]')]
    .map(node => node.innerText.trim()).filter(Boolean).slice(0, 5);
  const blocked = /验证码|安全验证|人机验证|captcha|security check|challenge/i.test(text)
    ? "页面要求人工验证"
    : (/请先登录|立即登录|尚未登录/.test(text) || /\/login|\/signin/.test(pageLocation.pathname)
      ? "Chrome 登录已失效"
      : null);
  if (blocked) return { error: blocked, lastSign, alerts };

  const successMessage = text.match(/(?:恭喜你)?签到成功[^\n]*/)?.[0]?.trim() || null;
  if (successMessage || /今日已签到|已经签到/.test(text)) {
    return { completed: true, lastSign, successMessage, alerts };
  }

  const buttons = [...root.querySelectorAll("button")]
    .filter(button => button.offsetParent !== null);
  const submit = buttons.find(button => button.innerText.trim() === "提交签到");
  if (!submit) return { error: "没有找到明确的“提交签到”按钮", lastSign, alerts };
  const mood = buttons.find(button => /开心/.test(button.innerText));
  if (!mood) return { error: "没有找到签到心情选项", lastSign, alerts };
  const note = root.querySelector('textarea[placeholder*="记录今天的点点滴滴"]');
  if (!note) return { error: "没有找到签到备注输入框", lastSign, alerts };
  return {
    completed: false,
    lastSign,
    moodSelected: mood.classList.contains("bg-primary-focus"),
    noteValue: note.value.trim(),
    submitDisabled: submit.disabled,
    alerts
  };
}

async function waitForCheckinState(readState, timeoutMs = 8000, pollMs = 250) {
  const transientErrors = new Set([
    "没有找到明确的“提交签到”按钮",
    "没有找到签到心情选项",
    "没有找到签到备注输入框"
  ]);
  const deadline = Date.now() + Math.max(0, timeoutMs);
  let state;
  do {
    try {
      state = await readState();
    } catch (error) {
      const message = typeof error?.message === "string" ? error.message : String(error);
      if (!transientErrors.has(message)) throw error;
      state = { error: message };
    }
    if (!state?.error || !transientErrors.has(state.error)) return state;
    if (Date.now() >= deadline) return state;
    await new Promise(resolve => setTimeout(resolve, Math.max(0, pollMs)));
  } while (Date.now() < deadline);
  return state;
}
