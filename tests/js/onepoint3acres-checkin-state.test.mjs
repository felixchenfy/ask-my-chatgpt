import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = fs.readFileSync(
  path.join(root, "chrome_extension", "checkin-state.js"),
  "utf8",
);
const { inspectCheckin, waitForCheckinState } = vm.runInNewContext(
  `${source}\n({ inspectCheckin, waitForCheckinState });`,
  { setTimeout },
  { filename: "checkin-state.js" },
);

function visibleButton(text, { disabled = false, selected = false } = {}) {
  return {
    innerText: text,
    offsetParent: {},
    disabled,
    classList: {
      contains(className) {
        return className === "bg-primary-focus" && selected;
      },
    },
  };
}

test("recognizes a successful check-in before requiring form controls", () => {
  let buttonQueries = 0;
  const document = {
    body: {
      innerText: [
        "恭喜你签到成功!获得奖励 大米 1 升",
        "您上次签到时间：2026-08-30 13:40",
      ].join("\n"),
    },
    querySelectorAll(selector) {
      if (selector === "button") buttonQueries += 1;
      return [];
    },
    querySelector() {
      return null;
    },
  };

  const result = inspectCheckin(document, { pathname: "/daily-check-in" });

  assert.equal(result.completed, true);
  assert.equal(result.lastSign, "2026-08-30 13:40");
  assert.equal(buttonQueries, 0);
});

test("returns the current fields for a normal check-in form", () => {
  const mood = visibleButton("😀 开心", { selected: true });
  const submit = visibleButton("提交签到");
  const note = { value: " 今日签到 " };
  const document = {
    body: { innerText: "每日签到\n您上次签到时间：2026-08-29 00:26" },
    querySelector(selector) {
      return selector === 'textarea[placeholder*="记录今天的点点滴滴"]' ? note : null;
    },
    querySelectorAll(selector) {
      if (selector === "button") return [mood, submit];
      if (selector === '[role="alert"],[role="status"],[data-sonner-toast]') {
        return [{ innerText: " 页面已就绪 " }];
      }
      return [];
    },
  };

  const result = inspectCheckin(document, { pathname: "/daily-check-in" });

  assert.equal(result.completed, false);
  assert.equal(result.lastSign, "2026-08-29 00:26");
  assert.equal(result.moodSelected, true);
  assert.equal(result.noteValue, "今日签到");
  assert.equal(result.submitDisabled, false);
  assert.deepEqual(Array.from(result.alerts), ["页面已就绪"]);
});

test("reports the precise missing-button error while the form is only partially hydrated", () => {
  const mood = visibleButton("😀 开心");
  const note = { value: "" };
  const document = {
    body: { innerText: "每日签到\n页面载入中" },
    querySelector(selector) {
      return selector === 'textarea[placeholder*="记录今天的点点滴滴"]' ? note : null;
    },
    querySelectorAll(selector) {
      return selector === "button" ? [mood] : [];
    },
  };

  const result = inspectCheckin(document, { pathname: "/daily-check-in" });

  assert.equal(result.error, "没有找到明确的“提交签到”按钮");
});

test("retries a transient thrown inspection error before accepting the success page", async () => {
  let attempts = 0;
  const result = await waitForCheckinState(
    async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("没有找到明确的“提交签到”按钮");
      return { completed: true, lastSign: "2026-08-31 00:43" };
    },
    100,
    0,
  );

  assert.equal(attempts, 2);
  assert.equal(result.completed, true);
  assert.equal(result.lastSign, "2026-08-31 00:43");
});

test("does not hide a non-transient inspection failure", async () => {
  await assert.rejects(
    waitForCheckinState(async () => {
      throw new Error("Chrome 登录已失效");
    }, 100, 0),
    /Chrome 登录已失效/,
  );
});
