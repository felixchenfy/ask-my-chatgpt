import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const worker = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const inspector = worker.slice(worker.indexOf("function inspectChatGptComposer("), worker.indexOf("function inspectChatGptThinkingMode("));
const action = worker.slice(worker.indexOf("async function executeChatGptComposerDiagnostic("), worker.indexOf("async function executeChatGpt(command)"));

test("read-only production inspector reports actual FileList without scrolling or reading messages", () => {
  const composer = { tagName: "TEXTAREA", offsetParent: {}, value: "", scrollIntoView() { throw new Error("scroll forbidden"); },
    getBoundingClientRect: () => ({left:0,top:0,width:200,height:40}) };
  const file = { files: [{name:"previous.pdf"}] };
  const scope = { document: {
    get body() { throw new Error("body read forbidden"); }, readyState:"complete",visibilityState:"visible",
    hasFocus: () => true,
    querySelector: selector => selector === "#prompt-textarea" ? composer : null,
    querySelectorAll: selector => selector === 'input[type="file"]' ? [file] : []
  } };
  scope.self = scope;
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-text.js", import.meta.url), "utf8"), scope);
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-submission.js", import.meta.url), "utf8"), scope);
  vm.runInNewContext(inspector, scope);
  const result = scope.inspectChatGptComposer("", "next.pdf", {readOnly:true});
  assert.equal(result.attachment.cardCount, 0);
  assert.equal(result.selectedFiles.length, 1);
  assert.equal(result.composerDiagnostic.selectedFilename, "previous.pdf");
  assert.equal(result.composerDiagnostic.formPresent, false);
});

for (const mode of ["managed", "missing", "wrong-destination"]) {
  test(`composer diagnostic ${mode} never creates, navigates, edits or reads an answer`, async () => {
    const seen = [];
    const tabs = mode === "missing" ? [] : [
      {id:1,url:"https://chatgpt.com/c/example",active:true},
      {id:2,url:"https://chatgpt.com/g/g-p-project/c/example"}
    ];
    const scope = { URL, self:{}, inspectChatGptComposer() {},
      chrome: {storage:{session:{get:async()=>({chatGptManagedTabs:{"session:original":2}})}},
        tabs:{query:async()=>tabs}},
      prepareChatGptText: async id => seen.push(["prepare", id]),
      assertChatGptDestination: async () => { if (mode === "wrong-destination") throw new Error("conversation_mismatch"); },
      execute: async (id, func, args) => {
        assert.equal(id, 2); assert.equal(func, scope.inspectChatGptComposer);
        assert.equal(args[2].readOnly,true);
        return { selectedFiles:["previous.pdf"], attachment:{cardCount:0},
          composerDiagnostic:{selectedFileCount:1,selectedFilename:"previous.pdf"} };
      },
      requestChatGpt: async (route, options) => {assert.equal(route,"/observation");seen.push(JSON.parse(options.body));}
    };
    vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-tabs.js",import.meta.url),"utf8"),scope);
    scope.ChatGptTabs=scope.self.ChatGptTabs;
    vm.runInNewContext(action,scope);
    await scope.executeChatGptComposerDiagnostic({run_id:"diagnostic",session_key:"original",conversation_url:"https://chatgpt.com/c/example"});
    const observation=seen.at(-1).last_observation;
    if(mode==="managed") {
      assert.equal(observation.tabId,2);assert.equal(observation.tabSource,"managed");
      assert.equal(observation.selectedFileCount,1);
      assert.equal(observation.composerDiagnostic.matchingTabCount,2);
    } else {
      assert.equal(observation.phase,"composer_diagnostic_failed");
      assert.ok(observation.composerErrorCode);
    }
  });
}
