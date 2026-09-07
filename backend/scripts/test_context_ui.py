"""Run the existing Inspector/state functions in local Node with a minimal DOM."""

from pathlib import Path
import re
import subprocess


def main():
    page = (Path(__file__).resolve().parents[1] / "static/index.html").read_text(encoding="utf-8")
    inline = "\n".join(re.findall(r"<script>(.*?)</script>", page, re.S))
    subprocess.run(["node", "--check"], input=inline, text=True, check=True, capture_output=True)
    renderers = page[page.index("    function traceElement("):page.index("    function closeTurnInspector(")]
    states = page[page.index("    function applyEventToConversationState("):page.index("    function applySessionHistory(")]
    setup = r'''
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
  set innerHTML(value) { throw new Error('Trace must use textContent'); }
}
const document = { createElement: () => new Element() };
const turnInspectorContent = new Element(), turnInspectorMeta = new Element();
const text = (node) => node.textContent + node.children.map(text).join('\n');
const activities = [], content = [], messages = [], updates = [];
const activeSessionId = 'bound';
function addRuntimeActivity(id, entry) { activities.push(entry); }
function setRuntimeStatus() {}
function previousToolFailed() { return false; }
function appendMessageContent(id, delta) { content.push(delta); }
function updateMessage(id, value) { updates.push(value); }
function addMessage(role, value, options) { const m = {id: messages.length + 1, role, value, options}; messages.push(m); return m; }
'''
    checks = r'''
const budget = {W: 12000, I: 6500, O: 1000, S:100, B:10900, T:9600, G:7200, measurement_kind:'estimated'};
const contextEvents = [
 {type:'llm/context_budget', data:budget},
 {type:'context/compaction', data:{number:1, elapsed_ms:52, trigger_reason:'provider_context_exceeded', usage:{total_tokens:80}, finish_reason:'stop', input_tokens_before:9000, input_tokens_after:6500}},
 {type:'context/recovery', data:{status:'succeeded', retries:1, reason:'retry_completed'}}
];
renderTurnTrace({turn_id:'t1', status:'success', user_input:'<script>unsafe()</script>', final_answer:'main answer',
 checkpoints:[{seq:20, covered_through_seq:12}], steps:[{step:1, context_events:contextEvents,
 llm_calls:[{input:{messages:[]},error:{code:'provider_context_exceeded'}},{input:{messages:[]},output:{content:'main answer',usage:{total_tokens:99},finish_reason:'stop'}}]}]});
const rendered = text(turnInspectorContent);
for (const term of ['W=12000','I=6500','O=1000','S=100','B=10900','T=9600','G=7200','estimated','摘要调用独立计量','正常 Agent 调用','covered_through_seq','elapsed_ms','finish_reason','retry_completed','80','99']) assert.ok(rendered.includes(term), term);
assert.ok(rendered.includes('<script>unsafe()</script>')); // text, never HTML execution
for (const phase of ['summarizing','summarized','summary_failed','retrying']) applyEventToConversationState(1,{type:'context_status',phase,message:'status '+phase,summary:'must not appear'});
assert.equal(activities.length,4); assert.equal(content.length,0); assert.equal(messages.length,0);
applyEventToConversationState(1,{type:'error',message:'回答流中断，未自动重放'});
assert.equal(activities.at(-1).content,'回答流中断，未自动重放');
replayPersistedEvents([
 {turn_id:'old',event_type:'user/message',data:{content:'question'}},
 {turn_id:'old',event_type:'context/checkpoint',data:{summary:'never an answer'}},
 {turn_id:'old',event_type:'context/status',data:{phase:'summarized',message:'整理完成'}},
 {turn_id:'old',event_type:'assistant/message',data:{content:'stored answer'}},
 {turn_id:'old',event_type:'turn/end',data:{status:'success'}}
],[]);
assert.equal(messages.filter(m=>m.role==='user').length,1);
assert.equal(messages.length,2); assert.equal(content.length,0);
assert.ok(updates.some(u=>u.content==='stored answer'));
assert.ok(!JSON.stringify(messages).includes('never an answer'));
console.log('ALL CONTEXT UI EXECUTION TESTS PASSED');
'''
    result = subprocess.run(["node"], input=setup + renderers + states + checks, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
