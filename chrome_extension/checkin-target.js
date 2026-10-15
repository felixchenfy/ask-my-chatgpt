function locateCheckinTarget(target, root = document) {
  const visibleButtons = [...root.querySelectorAll("button")].filter(
    button => button.offsetParent !== null
  );
  let element = null;
  let label = "";

  if (target === "mood") {
    element = visibleButtons.find(button => /开心/.test(button.innerText));
    label = "签到心情选项";
  } else if (target === "note") {
    element = root.querySelector('textarea[placeholder*="记录今天的点点滴滴"]');
    label = "签到备注输入框";
  } else if (target === "submit") {
    element = visibleButtons.find(button => button.innerText.trim() === "提交签到");
    label = "提交签到按钮";
  } else {
    return { error: `未知签到操作目标：${target}` };
  }

  if (!element || element.offsetParent === null) {
    return { error: `没有找到可见的${label}` };
  }

  // Coordinates returned by getBoundingClientRect are viewport-relative. Scroll
  // only the element that will be acted on next, then measure it immediately.
  // Measuring several elements after separate scrolls would make earlier points stale.
  element.scrollIntoView({ block: "center", inline: "center" });
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) {
    return { error: `${label}没有可用的屏幕坐标` };
  }
  return {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  };
}
