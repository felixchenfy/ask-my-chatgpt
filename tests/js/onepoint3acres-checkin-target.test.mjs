import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = fs.readFileSync(
  path.join(root, "chrome_extension", "checkin-target.js"),
  "utf8"
);
const locateCheckinTarget = vm.runInNewContext(
  `${source}\nlocateCheckinTarget;`,
  {},
  { filename: "checkin-target.js" }
);

function createFixture() {
  const viewportHeight = 500;
  let scrollY = 0;

  function element({ docTop, height, left, width, text = "" }) {
    const item = {
      innerText: text,
      offsetParent: {},
      scrollCount: 0,
      scrollIntoView() {
        item.scrollCount += 1;
        scrollY = docTop + height / 2 - viewportHeight / 2;
      },
      getBoundingClientRect() {
        return { left, top: docTop - scrollY, width, height };
      }
    };
    return item;
  }

  const mood = element({ docTop: 700, height: 70, left: 500, width: 820, text: "😀 开心" });
  const note = element({ docTop: 860, height: 100, left: 500, width: 820 });
  const submit = element({ docTop: 980, height: 32, left: 870, width: 80, text: "提交签到" });
  const document = {
    querySelector(selector) {
      return selector.startsWith("textarea") ? note : null;
    },
    querySelectorAll(selector) {
      return selector === "button" ? [mood, submit] : [];
    }
  };
  return { document, mood, note, submit };
}

test("locates and scrolls only the next check-in target", () => {
  const fixture = createFixture();

  const notePoint = locateCheckinTarget("note", fixture.document);
  assert.deepEqual({ ...notePoint }, { x: 910, y: 250 });
  assert.equal(fixture.note.scrollCount, 1);
  assert.equal(fixture.mood.scrollCount, 0);
  assert.equal(fixture.submit.scrollCount, 0);

  const submitPoint = locateCheckinTarget("submit", fixture.document);
  assert.deepEqual({ ...submitPoint }, { x: 910, y: 250 });
  assert.equal(fixture.note.scrollCount, 1);
  assert.equal(fixture.submit.scrollCount, 1);
});

test("rejects an unknown check-in target", () => {
  const fixture = createFixture();
  assert.match(locateCheckinTarget("other", fixture.document).error, /未知签到操作目标/);
});
