import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../../chrome_extension/google-search.js", import.meta.url), "utf8");
const context = vm.createContext({});
vm.runInContext(source, context);

test("builds a bounded official-site Google query from the quiz", () => {
  const query = context.buildGoogleSearchQuery(
    "关于积分的说法，哪个正确？",
    ["以上都对", "分享干货获得大米", "购买蓝莓兑换鳄梨", "购买 VIP 通行证"]
  );
  assert.match(query, /^site:1point3acres\.com /);
  assert.match(query, /关于积分的说法/);
  assert.doesNotMatch(query, /购买蓝莓兑换鳄梨/);
  assert.ok(query.length <= 700);
});

test("legacy fallback flag cannot broaden search outside the forum", () => {
  const query = context.buildGoogleSearchQuery("每日问题", ["第一项", "第二项"], { siteOnly: false });
  assert.equal(query, "site:1point3acres.com 每日问题");
});

test("incivility question does not become an unrelated self-discipline search", () => {
  const query = context.buildGoogleSearchQuery("遇到不文明言论，如何合理应对？",
    ["严于律己，不因为别人言行不文明而以同样方式还以颜色。", "这些都是"], { siteOnly: false });
  assert.equal(query, "site:1point3acres.com 遇到不文明言论，如何合理应对？");
});

function inspectResults(items, body = "网页搜索结果") {
  const anchors = items.map(({ url, title, text, ownHeading = true }) => {
    const heading = { innerText: title };
    const container = { innerText: text, querySelector: () => heading };
    return {
      href: url, innerText: title, parentElement: container,
      closest: () => container,
      querySelector: () => ownHeading ? heading : null,
    };
  });
  const page = vm.createContext({
    URL, location: { pathname: "/search", hostname: "www.google.com" },
    document: { body: { innerText: body }, querySelectorAll: () => anchors },
  });
  vm.runInContext(source, page);
  return JSON.parse(JSON.stringify(page.inspectGoogleSearchResults()));
}

test("only own-heading forum results survive; banks and AI overview links do not", () => {
  const result = inspectResults([
    { url: "https://example.org/answers", title: "论坛题库", text: "论坛题库 历史答案" },
    { url: "https://support.google.com/websearch/answer/1", title: "AI 概览", text: "AI 概览 一个生成的回答" },
    { url: "https://www.1point3acres.com/bbs/thread-1-1-1.html", title: "规则", text: "AI 概览 一个生成的回答", ownHeading: false },
    { url: "https://www.1point3acres.com/bbs/thread-2-1-1.html", title: "每日答题答案", text: "每日答题答案 第四项" },
    { url: "https://www.1point3acres.com/bbs/rules.html", title: "社区规则", text: "社区规则 清晰表达，方便检索。" },
  ]);
  assert.deepEqual(result.results, [{ title: "社区规则", url: "https://www.1point3acres.com/bbs/rules.html", snippet: "清晰表达，方便检索。" }]);
});

test("AI summaries cannot borrow an ordinary result heading", () => {
  assert.deepEqual(inspectResults([
    { url: "https://www.1point3acres.com/bbs/rules.html", title: "社区规则", text: "AI Overview 社区规则 生成文字" },
    { url: "https://www.1point3acres.com.evil.example/rules", title: "社区规则", text: "社区规则 仿冒域名" },
  ]).results, []);
});

test("access challenges remain errors, never usable evidence", () => {
  assert.match(inspectResults([], "unusual traffic CAPTCHA").error, /CAPTCHA/);
});
