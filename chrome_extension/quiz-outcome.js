// Self-contained because Chrome serializes this function into the website tab.
function inspectQuizOutcome(doc = globalThis.document) {
  const lines = (doc.body?.innerText || "").split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const correct = lines.find(s => /^(?:恭喜[你，,！!\s]*)?(?:你)?(?:回答正确|答题成功)(?:[，,。！!\s]|$)/.test(s));
  const incorrect = lines.find(s => /^(?:抱歉[，,\s]*)?(?:你)?(?:回答错误|答题失败)(?:[，,。！!\s]|$)/.test(s));
  const already = lines.find(s => /^(?:今日已答题|已经完成今日答题|明日再来)(?:[，,。！!\s]|$)/.test(s));
  const verdict = correct && incorrect ? "ambiguous" : incorrect ? "incorrect"
    : correct ? "correct" : already ? "already_completed_unknown" : "unknown";
  const pendingLabel = [...doc.querySelectorAll("button")]
    .map(node => node.innerText.trim())
    .find(text => /^(?:正在验证|验证中|正在提交|提交中|处理中)/.test(text));
  return {
    completed: ["correct", "incorrect", "already_completed_unknown"].includes(verdict),
    verdict,
    pending: Boolean(pendingLabel),
    pending_label: pendingLabel || null,
    evidence: [correct, incorrect, already].filter(Boolean).join("；").slice(0, 800),
    alerts: [...doc.querySelectorAll('[role="alert"],[role="status"],[data-sonner-toast]')]
      .map(node => node.innerText.trim()).filter(Boolean).slice(0, 5)
  };
}
