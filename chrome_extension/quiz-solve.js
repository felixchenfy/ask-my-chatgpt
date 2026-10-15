function interpretQuizSolveResult(result) {
  if (result?.status === "pending") return { pending: true, answer: null };
  if (result?.status === "complete" && result.answer) {
    return { pending: false, answer: result.answer };
  }
  if (result?.status === "error") throw new Error(result.error || "本地模型答题失败");
  throw new Error("本地模型返回了未知答题状态");
}

function assertReviewedQuizMatches(expected, live, answer) {
  if (live?.error || live?.completed || live?.question !== expected.question ||
      JSON.stringify(live?.choices) !== JSON.stringify(expected.choices)) {
    const detail = live?.error ? `页面读取失败：${live.error}`
      : live?.completed ? "页面已显示答题完成"
      : live?.question !== expected.question ? "题干不一致" : "选项文字或顺序不一致";
    const error = new Error(`提交前核验失败（${detail}）；本次审核失效，不提交`);
    error.quizDiagnostic = { expected, live: live || null };
    throw error;
  }
  if (!Number.isInteger(answer.choice_index) ||
      expected.choices[answer.choice_index - 1] !== answer.choice_text) {
    throw new Error("审核答案与原选项不对应，不提交");
  }
}
