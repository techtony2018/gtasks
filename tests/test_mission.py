import unittest

from tests.test_frontend_contract import PROJECT_ROOT, run_app_runtime_probe


SETUP = r"""
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const refs = Array.from({length: 7}, (_, i) => `tasks/00000000-0000-4000-8000-00000000000${i}`);
const tasks = refs.map((slug, i) => ({slug, title: `Real task ${i}`, next_action: i ? '' : 'Read the actual evidence',
  status: 'planned', lifecycle_root: 'collections/tonys-tasks', due_day: '2099-01-01'}));
state.snapshot = {tasks}; state.loading = false;
state.tasksReadState = {status:'fresh', stale:false, refreshing:false, last_valid_at:Date.now()/1000};
let stored = null; let writes = 0;
window.localStorage = {getItem: () => stored, setItem: (key, value) => {stored=value; writes++;}};
global.fetch = () => {throw new Error('selection made a network request');};
assert(typeof dailyMissionPreference === 'function', 'daily mission preference is missing');
"""


class DailyMissionTests(unittest.TestCase):
    def probe(self, script):
        result = run_app_runtime_probe(SETUP + script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_nonpersonal_content_is_suppressed_for_active_and_retired_refs(self):
        self.probe(r"""
const flatten = element => [element, ...(element.children || []).flatMap(flatten)];
const rendered = () => flatten(renderDailyMission()).map(item=>item.textContent || '').join(' ');
const scopes = [
  {lifecycle_root:'collections/toddys-tasks',owner_agent:'toddy'},
  {lifecycle_root:'collections/mission-control-system-tickets'},
  {lifecycle_root:'collections/mission-control-qa-fixtures'},
  {lifecycle_root:'collections/tonys-tasks',qa_fixture:true},
  {lifecycle_root:'collections/tonys-tasks',owner_agent:'tammy'},
  {lifecycle_root:'collections/other-tasks'},
];
for (const scope of scopes) for (const field of ['slots','retired']) {
  state.dailyMission={day:isoDay(new Date()),slots:[null,null,null],retired:[null,null,null]};
  state.dailyMission[field][0]=refs[0];
  state.snapshot.tasks=[{...tasks[0],...scope,title:'NONPERSONAL_TITLE_SENTINEL',next_action:'NONPERSONAL_ACTION_SENTINEL'}];
  state.tasksReadState={status:'stale',stale:true,last_valid_at:Date.now()/1000};
  const selection=dailyMissionSlot(0);
  assert(selection.kind==='uncertain' && selection.ref===refs[0], 'scope suppression lost uncertain reference');
  assert(selection.task===null, `stale ${scope.lifecycle_root}/${field} exposed task object`);
  const text=rendered();
  assert(!text.includes('NONPERSONAL_') && text.includes(refs[0]), 'nonpersonal sentinel rendered or reference lost');
  assert(state.dailyMission[field][0]===refs[0], 'stale scope retired/reactivated choice');
  state.tasksReadState={status:'fresh',last_valid_at:Date.now()/1000};
  assert(!chooseDailyMission(0,refs[0]), 'fresh nonpersonal accepted');
}
state.dailyMission={day:isoDay(new Date()),slots:[refs[0],null,null],retired:[null,null,null]};
state.snapshot.tasks=[tasks[0]]; state.tasksReadState={status:'stale',stale:true,last_valid_at:Date.now()/1000};
assert(dailyMissionSlot(0).task===tasks[0] && rendered().includes('Last known next action: Read the actual evidence'), 'last-known personal content was lost');
state.snapshot.tasks=[];
assert(dailyMissionSlot(0).task===null && dailyMissionSlot(0).ref===refs[0], 'unknown reference lost');
""")

    def test_mission_feedback_updates_one_connected_live_node_without_global_toast(self):
        self.probe(r"""
const flatten = element => [element, ...(element.children || []).flatMap(flatten)];
FakeElement.prototype.querySelector=function(selector) {
  return flatten(this).slice(1).find(item=>selector===`#${item.id}` ||
    (selector.startsWith('.') && (item.className || '').split(' ').includes(selector.slice(1))) ||
    selector===`[data-mission-focus="${item.dataset?.missionFocus}"]`) || null;
};
const root=renderDailyMission(); document.body.append(root);
document.querySelector=selector=>selector==='#daily-mission' ? root : document.body.querySelector(selector);
const control=key=>root.querySelector(`[data-mission-focus="${key}"]`);
const status=root.querySelector('#daily-mission-status');
assert(status && status.getAttribute('role')==='status' && status.getAttribute('aria-live')==='polite', 'mission-local live node missing before action');
assert(!status.textContent && status.getAttribute('aria-atomic')==='true', 'live node not initialized empty and atomic');
// Observe actual text writes and attachment. Do not replace showToast: a call
// to the real global handler would mutate elements.toast and fail below.
elements.toast.textContent='GLOBAL_TOAST_UNCHANGED';
const updates=[]; let copy='';
Object.defineProperty(status,'isConnected',{get:()=>flatten(document.body).includes(status)});
Object.defineProperty(status,'textContent',{get:()=>copy,set:value=>{
  assert(flatten(document.body).includes(status), 'live status was detached before text update');
  copy=value; updates.push(value);
}});
const originalReplace=root.replaceChildren.bind(root);
root.replaceChildren=(...children)=>{assert(children.includes(status), 'action removed live status registration');originalReplace(...children);};
let select=control('select:0'); select.focus(); select.value=refs[0]; select.listeners.change[0]();
assert(root.querySelector('#daily-mission-status')===status && updates.length===1 && copy.includes('Main mission chosen'), 'choice did not update original live node once');
assert(document.activeElement===control('select:0'), 'choice lost focus');
assert(elements.toast.textContent==='GLOBAL_TOAST_UNCHANGED', 'choice invoked global toast');
assert(flatten(root).filter(e=>e.getAttribute?.('role')==='status' || e.getAttribute?.('aria-live')).length===1, 'duplicate mission live announcers');
select=control('select:0'); select.focus(); select.value=refs[1]; select.listeners.change[0]();
assert(updates.filter(Boolean).length===2 && copy.includes('Main mission chosen'), 'same-copy second choice was not announced');
control('clear:0').focus(); control('clear:0').click();
assert(updates.filter(Boolean).length===3 && copy.includes('cleared') && document.activeElement===control('select:0'), 'clear announcement/focus failed');
assert(elements.toast.textContent==='GLOBAL_TOAST_UNCHANGED', 'clear invoked global toast');
select=control('select:0'); select.focus(); select.value='invalid'; select.listeners.change[0]();
assert(updates.filter(Boolean).length===4 && copy.includes('Choice unavailable') && document.activeElement===control('select:0'), 'invalid choice feedback/focus failed');
refreshDailyMissionSection();
assert(updates.filter(Boolean).length===4, 'passive refresh repeated an unchanged announcement');
assert(elements.toast.textContent==='GLOBAL_TOAST_UNCHANGED', 'invalid choice invoked global toast');
const frames=[]; window.requestAnimationFrame=callback=>frames.push(callback);
select=control('select:0'); select.value='invalid'; select.listeners.change[0]();
state.dailyMissionNotice=''; refreshDailyMissionSection();
for (const callback of frames) callback();
assert(copy==='', 'delayed repeated message resurrected cleared feedback');
""")

    def test_visible_day_rollover_clears_without_waiting_for_network(self):
        javascript = (PROJECT_ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        listener = 'document.addEventListener("visibilitychange", () => {' + javascript.split(
            'document.addEventListener("visibilitychange", () => {', 1
        )[1].split('\ndocument.addEventListener("keydown"', 1)[0]
        self.probe(r"""
let onVisible;
document.addEventListener=(name,callback)=>{if(name==='visibilitychange') onVisible=callback;};
""" + listener + r"""
const yesterday=new Date(); yesterday.setDate(yesterday.getDate()-1);
state.dailyMission={day:isoDay(yesterday),slots:[refs[0],null,null],retired:[null,null,null]};
const root=new FakeElement('section'); root.dataset.day=isoDay(yesterday); root.dataset.fresh='true';
document.querySelector=selector=>selector==='#daily-mission' ? root : null;
clearAutoRefreshTimer=()=>{}; scheduleAutoRefresh=()=>{}; loadTasks=()=>new Promise(()=>{});
document.hidden=false; state.autoRefreshDueAt=null;
onVisible();
assert(state.dailyMission.day===isoDay(new Date()) && state.dailyMission.slots[0]===null, 'visible rollover retained yesterday pending network');
assert(root.dataset.day===isoDay(new Date()), 'visible rollover did not update section');
""")

    def test_real_controls_preserve_focus_announce_and_label_uncertainty(self):
        self.probe(r"""
assert(typeof renderDailyMission === 'function', 'daily mission controls are missing');
const flatten = (element) => [element, ...(element.children || []).flatMap(flatten)];
const text = (element) => flatten(element).map(item=>item.textContent || '').join(' ');
let root=renderDailyMission();
document.querySelector = (selector) => selector==='#daily-mission' ? root : null;
FakeElement.prototype.querySelector = function(selector) { return flatten(this).slice(1).find(item =>
  selector===`[data-mission-focus="${item.dataset?.missionFocus}"]` || selector===`#${item.id}` ||
  (selector.startsWith('.') && (item.className || '').split(' ').includes(selector.slice(1)))) || null; };
const control = (key) => flatten(root).find(item=>item.dataset?.missionFocus===key);
let opened=null; selectTask=(...args)=>{opened=args};
assert(text(root).includes('local daily focus') && text(root).includes('not change'), 'local-only explanation absent');
let select=control('select:0'); select.focus(); select.value=refs[0]; select.listeners.change[0]();
assert(document.activeElement===control('select:0'), 'choice displaced select focus');
assert(root.querySelector('#daily-mission-status').textContent.includes('Main mission chosen') && text(root).includes('Read the actual evidence'), 'no announcement or recorded next action');
assert(text(root).includes('Main mission chosen. Local focus only.'), 'readable inline confirmation missing');
control('open:0').click();
assert(opened[0]===refs[0] && opened[2]===control('open:0'), 'did not reuse detail opener with origin');
control('clear:0').focus(); control('clear:0').click();
assert(document.activeElement===control('select:0') && dailyMissionPreference().slots[0]===null, 'clear did not restore relevant focus');
chooseDailyMission(0,refs[1]); refreshDailyMissionSection();
assert(text(root).includes('No next action recorded'), 'invented missing action');
chooseDailyMission(0,refs[0]); state.tasksReadState.status='error'; refreshDailyMissionSection();
assert(text(root).includes('Last known next action') && text(root).includes('not verified'), 'stale action presented as current');
assert(!control('select:0').disabled, 'stale state made clear inaccessible');
assert(flatten(control('select:0')).filter(item=>item.tagName==='OPTION' && item.value && !item.disabled).length===0, 'stale choices available');
state.snapshot=null; refreshDailyMissionSection();
assert(text(root).includes('not verified') && dailyMissionPreference().slots[0]===refs[0], 'cold state erased reference');
""")

    def test_background_render_and_detail_close_restore_mission_origin(self):
        self.probe(r"""
assert(typeof renderDailyMission === 'function', 'daily mission controls are missing');
const flatten = element => [element, ...(element.children || []).flatMap(flatten)];
document.createDocumentFragment=()=>new FakeElement('fragment');
state.activeView='today'; state.snapshot.as_of=isoDay(new Date()); state.snapshot.goals=[];
state.snapshot.today={in_progress:[],todays_actions:[],overdue:[]}; state.snapshot.views={blocked:[]};
renderNavigation=()=>{}; updateBoardStatus=()=>{}; inContextCountLabel=()=>''; renderCanonicalRootIssues=()=>null;
renderSurfaceFreshness=()=>null; syncMobileDetailModalState=()=>{}; restorePendingDetailFocus=()=>{};
creationEntry=()=>node('div','','create'); emptyActionState=()=>node('div','','empty');
section=(title)=>node('section','',title); goalsHomeSection=()=>node('section','','Goals progress');
elements.viewSurface.replaceChildren=function(...children) {this.children=children; document.activeElement=document.body;};
elements.viewSurface.querySelector=selector=>flatten(elements.viewSurface).find(item=>selector===`[data-mission-focus="${item.dataset?.missionFocus}"]`) || null;
render();
const select=elements.viewSurface.querySelector('[data-mission-focus="select:0"]');
select.closestResult=select; select.focus(); render();
assert(document.activeElement===elements.viewSurface.querySelector('[data-mission-focus="select:0"]'), 'background render lost mission focus');
const oldOrigin=new FakeElement('button'); oldOrigin.dataset.missionFocus='open:0';
const replacement=new FakeElement('button');
document.querySelector=selector=>selector==='[data-mission-focus="open:0"]' ? replacement : null;
assert(detailFocusReturnTarget(detailReturnFocusAnchor(oldOrigin,refs[0]))===replacement, 'detail close lost rerendered origin');
const allText=flatten(elements.viewSurface).map(item=>item.textContent||'').join(' ');
for (const label of ['In Progress','Today’s Actions','Blocked','Overdue','Goals progress']) assert(allText.includes(label), `removed ${label}`);
assert(flatten(elements.viewSurface).findIndex(item=>item.id==='daily-mission') < flatten(elements.viewSurface).findIndex(item=>item.textContent==='Blocked'), 'mission not above task groups');
""")

    def test_reference_only_preference_scope_cap_and_explicit_selection(self):
        self.probe(r"""
assert(dailyMissionPreference().slots.every(ref => ref === null), 'default picked a task');
assert(chooseDailyMission(1, refs[1]), 'optional without main rejected');
assert(chooseDailyMission(0, refs[0]), 'main not chosen');
assert(!chooseDailyMission(2, refs[0]), 'duplicate accepted');
assert(chooseDailyMission(2, refs[2]), 'second optional rejected');
assert(!chooseDailyMission(3, refs[3]), 'fourth slot accepted');
assert(!chooseDailyMission(0, 'tasks/not-valid'), 'invalid reference accepted');
tasks[3].lifecycle_root='collections/toddys-tasks';
tasks[4].lifecycle_root='collections/mission-control-system-tickets';
tasks[5].status='completed'; tasks[6].owner_agent='toddy';
for (const ref of refs.slice(3)) assert(!chooseDailyMission(0, ref), 'out-of-scope or terminal task accepted');
assert(JSON.parse(stored).slots.length===3, 'unbounded slots');
assert(!stored.includes('Real task') && !stored.includes('2099') && !stored.includes('Read the actual'), 'copied canonical content');
assert(chooseDailyMission(0, null), 'clear failed');
assert(dailyMissionPreference().slots[0]===null && tasks[0].status==='planned' && tasks[0].due_day==='2099-01-01', 'selection mutated task');
""")

    def test_local_day_reload_validation_and_denied_storage(self):
        self.probe(r"""
// Local accessors deliberately disagree with UTC, independent of machine TZ.
const localDay = {getFullYear:()=>2026, getMonth:()=>8, getDate:()=>6, toISOString:()=> '2026-09-07T01:00:00Z'};
assert(dailyMissionPreference(localDay).day==='2026-09-06', 'used UTC day');
const day=isoDay(new Date());
stored=JSON.stringify({day, slots:[refs[0],refs[0],'invalid',refs[1]], retired:[null,null,null]});
state.dailyMission=null;
assert(dailyMissionPreference().slots.join('|')===`${refs[0]}||`, 'invalid/duplicate/overflow references survived');
assert(chooseDailyMission(1,refs[1]), 'second slot failed'); state.dailyMission=null;
assert(dailyMissionPreference().slots[1]===refs[1], 'same-day reload lost choice');
const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate()+1);
assert(dailyMissionPreference(tomorrow).slots.every(ref=>ref===null), 'yesterday carried over');
stored='{broken'; state.dailyMission=null;
assert(dailyMissionPreference().slots.every(ref=>ref===null) && state.dailyMissionStorageWarning, 'malformed storage not explained');
window.localStorage={getItem:()=>{throw Error('denied')},setItem:()=>{throw Error('denied')}};
state.dailyMission=null;
assert(chooseDailyMission(0,refs[0]), 'denied storage prevented in-tab choice');
assert(dailyMissionPreference().slots[0]===refs[0] && state.dailyMissionStorageWarning, 'in-tab fallback missing');
""")

    def test_terminal_retirement_stale_missing_and_reload_reopen(self):
        self.probe(r"""
chooseDailyMission(0,refs[0]);
tasks[0].status='completed'; tasks[0].lifecycle_root='collections/tonys-completed-tasks';
state.tasksReadState.stale=true; state.tasksReadState.status='error';
assert(dailyMissionSlot(0).kind==='uncertain' && dailyMissionPreference().slots[0]===refs[0], 'stale completion retired choice');
assert(!chooseDailyMission(1,refs[1]), 'new choice accepted without fresh evidence');
state.tasksReadState={status:'fresh', last_valid_at:Date.now()/1000};
assert(dailyMissionSlot(0).kind==='completed', 'fresh completion not explained');
assert(dailyMissionPreference().slots[0]===null && dailyMissionPreference().retired[0]===refs[0], 'terminal choice not retired');
state.dailyMission=null; tasks[0].status='planned'; tasks[0].lifecycle_root='collections/tonys-tasks';
assert(dailyMissionSlot(0).kind==='previous' && dailyMissionPreference().slots[0]===null, 'reload reopened task automatically selected');
assert(chooseDailyMission(0,refs[0]) && dailyMissionSlot(0).kind==='selected', 'explicit reselection failed');
state.snapshot.tasks=tasks.slice(1);
assert(dailyMissionSlot(0).kind==='unavailable' && dailyMissionPreference().slots[0]===refs[0], 'omission treated as deletion');
state.tasksReadState={status:'stale', stale:true, last_valid_at:Date.now()/1000};
assert(dailyMissionSlot(0).kind==='uncertain', 'stale missing not uncertain');
state.tasksReadState={status:'fresh', last_valid_at:Date.now()/1000-301};
assert(!dailyMissionEvidenceFresh(), 'expired snapshot claimed current');
""")


if __name__ == '__main__':
    unittest.main()
