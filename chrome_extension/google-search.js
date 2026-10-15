function buildGoogleSearchQuery(question, choices) {
  const normalizedQuestion = String(question || "").replace(/\s+/g, " ").trim();
  // Entire answer paragraphs overconstrain Google and match unrelated words.
  // Keep the forum scope; never search the options as one long sentence.
  // Do not reintroduce removed community answer banks via a full-web fallback.
  const scope = "site:1point3acres.com ";
  return `${scope}${normalizedQuestion}`.trim().slice(0, 700);
}

function inspectGoogleSearchResults() {
  const pageText = document.body?.innerText || "";
  const blocked = /unusual traffic|我们的系统检测到|不是机器人|captcha/i.test(pageText)
    || /\/sorry\//.test(location.pathname);
  const consentRequired = /consent\.google\./.test(location.hostname)
    || /Before you continue to Google|在继续前往 Google 之前/i.test(pageText);
  if (blocked) return { error: "Google 要求 CAPTCHA 或检测到异常流量" };
  if (consentRequired) return { error: "Google 显示了需要人工处理的同意页面" };

  const results = [];
  const seen = new Set();
  const anchors = [...document.querySelectorAll('#search a[href^="http"]')];
  for (const anchor of anchors) {
    let url;
    try {
      url = new URL(anchor.href);
    } catch (_) {
      continue;
    }
    if (!(url.hostname === "1point3acres.com" || url.hostname.endsWith(".1point3acres.com"))
      || !["http:", "https:"].includes(url.protocol) || url.username || url.password
      || seen.has(url.href)) continue;
    const container = anchor.closest("div.MjjYud, div.g, article") || anchor.parentElement;
    // Help/privacy/citation links must not borrow a heading or generated answer
    // from a surrounding AI Overview block.
    const heading = anchor.querySelector("h3");
    if (!heading) continue;
    const title = (heading.innerText || "").replace(/\s+/g, " ").trim();
    const text = (container?.innerText || "").replace(/\s+/g, " ").trim();
    if (!title || !text) continue;
    if (/题库|每日答题.{0,8}答案|quiz[\s_-]*bank|answer[\s_-]*key|AI\s*(?:概览|模式|overview|mode)/i.test(`${title} ${text}`)) continue;
    const snippet = text.replace(title, " ").replace(/\s+/g, " ").trim();
    results.push({ title: title.slice(0, 240), url: url.href, snippet: snippet.slice(0, 700) });
    seen.add(url.href);
    if (results.length >= 5) break;
  }
  return { results };
}
