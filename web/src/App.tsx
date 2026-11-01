import { FormEvent, useEffect, useState } from "react";
import { api, isOidcMode, setDevRole, streamConversationEvents } from "./api";
import type { ActionExecution, ActionPlan, AuthProfile, Conversation, EvaluationDataset, EvaluationRun, Handoff, MessageFacts, PilotMetrics, PolicyVersion, RetrievalDebug, RetrievalProfile, RetrievalProfileReadiness, RuntimeTask, Ticket, WorkerInfo } from "./types";

type View = "customer" | "agent" | "admin" | "platform";

export function App() {
  const [view, setView] = useState<View>("customer");
  const [profile, setProfile] = useState<AuthProfile | null>(null);
  const [notice, setNotice] = useState("系统就绪");
  async function loadProfile() {
    const value = await api<AuthProfile>("/api/v1/auth/me");
    setProfile(value);
    setView(value.role === "tenant_admin" ? "admin" : value.role === "platform_admin" ? "platform" : value.role);
  }
  useEffect(()=>{loadProfile().catch(error=>setNotice(error.message))},[]);
  async function changeView(next: View) {
    if (isOidcMode) return;
    setDevRole(next === "admin" ? "tenant_admin" : next); await loadProfile(); setNotice("身份与工作区已切换");
  }
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brandMark">J</div><div><b>JayAgent</b><span>Service OS</span></div></div>
      <div className="tenant"><span className="pulse" />{profile?.username || "正在验证身份"}<small>{profile?.tenant_id || "—"}</small></div>
      <nav>
        {(!isOidcMode || profile?.role === "customer") && <button className={view === "customer" ? "active" : ""} onClick={() => changeView("customer")}>客户对话</button>}
        {(!isOidcMode || profile?.role === "agent") && <button className={view === "agent" ? "active" : ""} onClick={() => changeView("agent")}>人工工作台</button>}
        {(!isOidcMode || profile?.role === "tenant_admin") && <button className={view === "admin" ? "active" : ""} onClick={() => changeView("admin")}>运营与治理</button>}
        {profile?.role === "platform_admin" && <button className="active">平台运维</button>}
      </nav>
      <div className="security"><span>安全边界</span><b>租户隔离 · 幂等写入</b><small>高风险动作始终转人工审批</small></div>
    </aside>
    <main><header><div><p className="eyebrow">MARKET ASSISTANT PILOT</p><h1>{view === "customer" ? "客户服务中心" : view === "agent" ? "人工客服工作台" : view === "platform" ? "平台运维台" : "运营与治理台"}</h1></div><div className="status"><span className="pulse" />{notice}</div></header>
      {view === "customer" && profile && <CustomerView notify={setNotice} customerId={profile.customer_id || "customer_001"} />}
      {view === "agent" && <AgentView notify={setNotice} />}
      {view === "admin" && <AdminView notify={setNotice} />}
      {view === "platform" && <PlatformView notify={setNotice} />}
    </main>
  </div>;
}

function CustomerView({notify,customerId}:{notify:(s:string)=>void;customerId:string}) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [content, setContent] = useState("请查询 order_1001 的物流");
  const [pending, setPending] = useState(false);
  const conversationId=conversation?.conversation_id;
  async function refresh(id=conversationId) { if(!id)return; const [value,ticketData]=await Promise.all([api<Conversation>(`/api/v1/conversations/${id}`),api<Ticket[]>("/api/v1/tickets")]);setConversation(value);setTickets(ticketData); }
  useEffect(()=>{const saved=localStorage.getItem("jayagent.currentConversation");if(saved)api<Conversation>(`/api/v1/conversations/${saved}`).then(setConversation).catch(()=>localStorage.removeItem("jayagent.currentConversation"))},[]);
  useEffect(()=>{if(!conversationId)return;localStorage.setItem("jayagent.currentConversation",conversationId);const controller=new AbortController();streamConversationEvents(conversationId,(type)=>{notify(`实时事件：${type}`);refresh(conversationId).finally(()=>setPending(false))},controller.signal).catch(error=>{if(!controller.signal.aborted)notify(error.message)});return()=>controller.abort()},[conversationId]);
  async function create() { const value = await api<Conversation>("/api/v1/conversations", {method:"POST", body:JSON.stringify({customer_id:customerId,channel:"web"})}); setConversation(value); setTickets(await api<Ticket[]>("/api/v1/tickets")); notify("新会话已创建"); }
  async function send(event: FormEvent) { event.preventDefault(); if (!conversation || !content.trim()) return; setPending(true);try{const queued = await api<{task_id:string}>(`/api/v1/conversations/${conversation.conversation_id}/messages`,{method:"POST",body:JSON.stringify({content})}); notify(`任务 ${queued.task_id.slice(0,12)}… 已入队`); setContent("");}catch(error){setPending(false);notify((error as Error).message)} }
  async function handoff(){if(!conversation)return;await api(`/api/v1/conversations/${conversation.conversation_id}/handoff`,{method:"POST",body:JSON.stringify({reason:"user_requested_human"})});setConversation(await api(`/api/v1/conversations/${conversation.conversation_id}`));notify("已进入人工客服队列");}
  async function confirm(plan:ActionPlan){await api(`/api/v1/action-plans/${plan.action_plan_id}/confirm`,{method:"POST",body:JSON.stringify({confirmation:"confirm"})});notify("动作已确认并进入幂等执行队列");await refresh()}
  return <section className="customerGrid">
    <div className="panel chat"><div className="panelHead"><div><b>在线助理</b><span>{conversation ? `会话 ${conversation.conversation_id.slice(-8)}` : "尚未创建会话"}</span></div><button className="ghost" onClick={create}>新建会话</button></div>
      <div className="messages">{!conversation?.messages.length && <div className="welcome"><div className="orb">✦</div><h2>你好，我是 JayAgent</h2><p>我会区分订单事实、政策证据与待审批动作。试试查询物流或描述商品问题。</p></div>}{conversation?.messages.map(m=><div key={m.message_id} className={`message ${m.sender_type}`}><span>{m.sender_type === "customer" ? "你" : m.sender_type === "assistant" ? "AI" : "客服"}</span><p>{m.content}</p>{m.facts&&<FactCards facts={m.facts} onConfirm={confirm}/>}</div>)}</div>
      <form className="composer" onSubmit={send}><textarea value={content} onChange={e=>setContent(e.target.value)} placeholder="输入订单、物流或售后问题…"/><div><button type="button" className="ghost" onClick={handoff}>转人工</button><button disabled={!conversation||pending}>{pending?"处理中":"发送"}</button></div></form>
    </div>
    <aside className="context"><div className="contextCard"><p className="eyebrow">CURRENT STATE</p><h3>{conversation?.status || "未开始"}</h3><span>控制权：{conversation?.owner_type || "—"}</span>{pending&&<p className="liveProgress"><span className="pulse"/> 正在执行任务并等待实时事件</p>}</div><div className="contextCard"><h3>工单进度</h3>{tickets.length?tickets.map(ticket=><div className="miniCard" key={ticket.ticket_id}><b>{ticket.title}</b><small>{ticket.status} · {ticket.priority}</small></div>):<p>暂无工单</p>}</div><div className="contextCard"><h3>可信回答规则</h3><ul><li>订单结果来自业务事实源</li><li>政策回答携带版本引用</li><li>证据不足时拒绝确定回答</li><li>写操作需确认和幂等键</li></ul></div></aside>
  </section>;
}

function FactCards({facts,onConfirm}:{facts:MessageFacts;onConfirm:(plan:ActionPlan)=>void}) {
  const plan=facts.action_plan;
  return <div className="factCards">
    {facts.order&&<div className="factCard"><b>订单与物流事实</b><p>订单 {String(facts.order.order_id||"—")} · {String(facts.order.status||"—")}</p><small>{String(facts.order.shipment_status||"—")} · {String(facts.order.tracking_number||"—")}</small></div>}
    {facts.citations?.map(item=><div className="factCard citation" key={item.chunk_id}><b>{item.title} · {item.version}</b><small>{item.scope}</small><p>{item.excerpt}</p></div>)}
    {facts.proposal&&<div className="factCard warning"><b>补偿建议（需人工审批）</b><p>建议额度 ¥{String(facts.proposal.recommended_amount||"—")}</p><small>该金额由服务端政策引擎计算，不会自动发放。</small></div>}
    {plan&&<div className="factCard actionCard"><b>待确认动作</b><p>{plan.preview}</p><small>状态 {plan.status} · 风险 {plan.risk_level||"medium"}</small>{plan.status==="approval_required"&&<button onClick={()=>onConfirm(plan)}>确认并提交</button>}</div>}
    {facts.error_code&&<div className="factCard errorText"><b>安全降级</b><p>{facts.error_code}</p></div>}
  </div>
}

function AgentView({notify}:{notify:(s:string)=>void}) {
  const [items,setItems]=useState<Handoff[]>([]); const [selected,setSelected]=useState<Handoff|null>(null); const [reply,setReply]=useState("您好，我已经看到订单与前序对话，正在为您核实。"); const [transferReason,setTransferReason]=useState("需要转交通用服务队列继续处理");
  async function refresh(){const data=await api<Handoff[]>("/api/v1/agent-console/handoffs");setItems(data);if(selected)setSelected(data.find(x=>x.handoff_id===selected.handoff_id)||selected)}
  useEffect(()=>{refresh().catch(e=>notify(e.message))},[]);
  async function claim(item:Handoff){const value=await api<Handoff>(`/api/v1/handoffs/${item.handoff_id}/claim`,{method:"POST"});setSelected(value);notify("会话已原子领取");refresh()}
  async function send(){if(!selected)return;await api(`/api/v1/handoffs/${selected.handoff_id}/reply`,{method:"POST",body:JSON.stringify({content:reply})});notify("人工回复已送达");}
  async function resolve(){if(!selected)return;await api(`/api/v1/handoffs/${selected.handoff_id}/resolve`,{method:"POST",body:JSON.stringify({resume_ai:false})});setSelected(null);refresh();notify("接管已解决并审计");}
  async function release(){if(!selected)return;await api(`/api/v1/handoffs/${selected.handoff_id}/release`,{method:"POST"});setSelected(null);await refresh();notify("会话已安全释放回队列");}
  async function transfer(){if(!selected)return;await api(`/api/v1/handoffs/${selected.handoff_id}/transfer`,{method:"POST",body:JSON.stringify({queue_id:"queue_general",reason:transferReason})});setSelected(null);await refresh();notify("会话已转派并记录审计");}
  function waiting(item:Handoff){return Math.max(0,Math.round((Date.now()-new Date(item.created_at).getTime())/60000))}
  function sla(item:Handoff){const due=item.context_snapshot.sla_due_at;if(!due)return "未配置";const seconds=Math.round((new Date(due).getTime()-Date.now())/1000);return seconds>0?`${Math.ceil(seconds/60)} 分钟内响应`:`已超时 ${Math.ceil(Math.abs(seconds)/60)} 分钟`}
  return <section className="workspace"><div className="panel queue"><div className="panelHead"><div><b>待接管队列</b><span>按优先级与等待时间排序</span></div><button className="ghost" onClick={refresh}>刷新</button></div>{items.map(item=><button className={`queueItem ${selected?.handoff_id===item.handoff_id?"chosen":""}`} key={item.handoff_id} onClick={()=>setSelected(item)}><span className="priority">P{item.priority}</span><div><b>{item.reason}</b><small>{item.queue_id} · 已等待 {waiting(item)} 分钟</small></div><em className={sla(item).startsWith("已超时")?"slaLate":""}>{sla(item)}</em></button>)}</div><div className="panel case"><div className="panelHead"><div><b>接管上下文</b><span>客户、订单、政策与风险一次交接</span></div>{selected?.status==="queued"&&<button onClick={()=>claim(selected)}>原子领取</button>}</div>{selected?<><div className="agentContext"><div className="summary"><b>会话摘要</b>{selected.context_snapshot.summary?.map((x,i)=><p key={i}>{x}</p>)}</div><div className="factCard"><b>队列与 SLA</b><p>{selected.queue_id} · {sla(selected)}</p><small>风险：{selected.context_snapshot.risk||"normal"}</small></div>{selected.context_snapshot.latest_order&&<div className="factCard"><b>最近订单</b><pre>{JSON.stringify(selected.context_snapshot.latest_order,null,2)}</pre></div>}{selected.context_snapshot.policy_citations?.map(item=><div className="factCard citation" key={item.chunk_id}><b>{item.title} · {item.version}</b><p>{item.excerpt}</p></div>)}{selected.context_snapshot.action_plan&&<div className="factCard warning"><b>待处理动作</b><pre>{JSON.stringify(selected.context_snapshot.action_plan,null,2)}</pre></div>}</div><textarea value={reply} onChange={e=>setReply(e.target.value)}/><label className="inlineField">转派理由<input value={transferReason} onChange={e=>setTransferReason(e.target.value)}/></label><div className="actions multiActions"><button className="ghost" onClick={release} disabled={selected.status!=="claimed"}>释放</button><button className="ghost" onClick={transfer} disabled={selected.status!=="claimed"}>转派</button><button className="ghost" onClick={resolve} disabled={selected.status!=="claimed"}>解决并关闭</button><button onClick={send} disabled={selected.status!=="claimed"}>发送人工回复</button></div></>:<div className="welcome"><h2>选择一个待办</h2><p>这里会展示客户、会话摘要、政策引用、待确认动作和风险信号。</p></div>}</div></section>;
}

function AdminView({notify}:{notify:(s:string)=>void}) {
  const [scope,setScope]=useState("after_sale"); const [content,setContent]=useState("商品签收后 7 天内，如存在破损并提供照片证据，可创建售后工单，由人工审核补偿方案。"); const [run,setRun]=useState<EvaluationRun|null>(null);
  const [datasets,setDatasets]=useState<EvaluationDataset[]>([]); const [runs,setRuns]=useState<EvaluationRun[]>([]); const [failureFilter,setFailureFilter]=useState(""); const [comparison,setComparison]=useState<Record<string,unknown>|null>(null);
  const [profiles,setProfiles]=useState<RetrievalProfile[]>([]);
  const [profileReadiness,setProfileReadiness]=useState<Record<string,RetrievalProfileReadiness>>({});
  const [policies,setPolicies]=useState<PolicyVersion[]>([]); const [debugQuery,setDebugQuery]=useState("破损商品多久可以申请售后？"); const [debug,setDebug]=useState<RetrievalDebug|null>(null);
  const [tasks,setTasks]=useState<RuntimeTask[]>([]); const [filter,setFilter]=useState("");
  const [selected,setSelected]=useState<RuntimeTask|null>(null); const [reason,setReason]=useState("已修复依赖数据，人工确认重新执行");
  const [reconciliations,setReconciliations]=useState<ActionExecution[]>([]);
  const [summary,setSummary]=useState<{tasks:Record<string,number>;workers:{online:number;stale:number}}>({tasks:{},workers:{online:0,stale:0}});
  const [pilotMetrics,setPilotMetrics]=useState<PilotMetrics|null>(null);
  async function refreshPolicies(){setPolicies(await api<PolicyVersion[]>("/api/v1/admin/policies"))}
  async function refreshEvaluations(){const [datasetData,runData,profileData]=await Promise.all([api<EvaluationDataset[]>("/api/v1/admin/evaluation-datasets"),api<EvaluationRun[]>("/api/v1/admin/evaluations"),api<RetrievalProfile[]>("/api/v1/admin/retrieval-profiles")]);setDatasets(datasetData);setRuns(runData.filter(item=>item.metric_schema_version==="v3"));setProfiles(profileData);const readinessEntries=await Promise.all(profileData.map(async item=>[item.retrieval_profile_id,await api<RetrievalProfileReadiness>(`/api/v1/admin/retrieval-profiles/${item.retrieval_profile_id}/activation-readiness`)] as const));setProfileReadiness(Object.fromEntries(readinessEntries));if(run?.evaluation_run_id)setRun(await api<EvaluationRun>(`/api/v1/admin/evaluations/${run.evaluation_run_id}`))}
  async function publish(){const policy=await api<{policy_version_id:string}>("/api/v1/admin/policies",{method:"POST",body:JSON.stringify({scope,version:`v${Date.now()}`,title:"售后处理政策",content,acl:{channel:["web"]}})});await api(`/api/v1/admin/policies/${policy.policy_version_id}/publish`,{method:"POST"});await refreshPolicies();notify("新政策版本已发布，旧版本已自动失效")}
  async function evaluate(){const active=profiles.find(item=>item.status==="active");const value=await api<EvaluationRun>("/api/v1/admin/evaluations/run",{method:"POST",body:JSON.stringify({dataset:"week4_gold",dataset_version:"v3",retrieval_profile_id:active?.retrieval_profile_id})});setRun(value);notify("v3质量评测任务已进入持久化队列");await refreshEvaluations()}
  async function compareLatest(){const comparable=runs.filter(item=>item.dataset==="week4_gold"&&item.dataset_version==="v3");if(comparable.length<2){notify("至少需要两个v3运行才能比较");return}setComparison(await api(`/api/v1/admin/evaluations/compare?left=${comparable[1].evaluation_run_id}&right=${comparable[0].evaluation_run_id}`))}
  async function activateProfile(item:RetrievalProfile){if(!item.evaluation_run_id)return;await api(`/api/v1/admin/retrieval-profiles/${item.retrieval_profile_id}/activate`,{method:"POST",body:JSON.stringify({evaluation_run_id:item.evaluation_run_id})});notify(`${item.retrieval_profile_id} 已通过后端门禁并激活`);await refreshEvaluations()}
  async function retrievalDebug(){setDebug(await api<RetrievalDebug>("/api/v1/admin/retrieval/debug",{method:"POST",body:JSON.stringify({query:debugQuery,filters:{channel:"web"},top_k:5})}))}
  async function refreshRuntime(){const suffix=filter?`?status=${filter}`:"";const [taskData,reconciliationData,summaryData,metricsData]=await Promise.all([api<RuntimeTask[]>(`/api/v1/admin/runtime-tasks${suffix}`),api<ActionExecution[]>("/api/v1/admin/reconciliations"),api<{tasks:Record<string,number>;workers:{online:number;stale:number}}>("/api/v1/admin/runtime-summary"),api<PilotMetrics>("/api/v1/admin/pilot-metrics")]);setTasks(taskData);setReconciliations(reconciliationData);setSummary(summaryData);setPilotMetrics(metricsData)}
  useEffect(()=>{refreshRuntime().catch(e=>notify(e.message));refreshPolicies().catch(e=>notify(e.message));refreshEvaluations().catch(e=>notify(e.message))},[filter]);
  async function inspect(task:RuntimeTask){setSelected(await api<RuntimeTask>(`/api/v1/admin/runtime-tasks/${task.task_id}`))}
  async function replay(){if(!selected)return;const value=await api<RuntimeTask>(`/api/v1/admin/runtime-tasks/${selected.task_id}/replay`,{method:"POST",body:JSON.stringify({reason})});notify(`已创建重放任务 ${value.task_id.slice(0,12)}…`);await refreshRuntime()}
  async function cancel(task:RuntimeTask){await api(`/api/v1/admin/runtime-tasks/${task.task_id}/cancel`,{method:"POST"});notify("任务已取消并记录事件");await refreshRuntime()}
  async function resolve(execution:ActionExecution,outcome:"succeeded"|"failed"|"compensation_required"){await api(`/api/v1/admin/action-executions/${execution.execution_id}/resolve-reconciliation`,{method:"POST",body:JSON.stringify({outcome,reason:"运营人员已核验外部系统回执",receipt:{source:"manual_console"}})});notify("人工对账结论已保存并审计");await refreshRuntime()}
  const count=(status:string)=>summary.tasks[status]||0;
  const visibleFailures=(run?.summary.results||[]).filter(item=>!item.passed&&(!failureFilter||`${item.details.case_type} ${item.details.tags.join(" ")} ${item.details.reason_code}`.includes(failureFilter)));
  return <section className="adminGrid"><div className="panel pilotMetrics"><div className="panelHead"><div><b>M3 试点运营指标</b><span>所有指标可回溯到会话、接管、工单与 Agent Run</span></div><button className="ghost" onClick={refreshRuntime}>刷新</button></div>{pilotMetrics&&<div className="metricGrid wide"><Metric n={formatMetric(pilotMetrics.self_service_resolution_rate)} label="自助解决率"/><Metric n={formatMetric(pilotMetrics.handoff_rate)} label="转人工率"/><Metric n={formatMetric(pilotMetrics.first_contact_resolution_rate)} label="一次解决率"/><Metric n={`${Math.round(pilotMetrics.agent_p95_latency_ms)} ms`} label="Agent P95"/><Metric n={`${Math.round(pilotMetrics.average_first_response_seconds)} s`} label="首次响应"/><Metric n={`¥${pilotMetrics.estimated_cost.toFixed(4)}`} label="估算成本"/><Metric n={String(pilotMetrics.open_ticket_count)} label="开放工单"/><Metric n={String(pilotMetrics.unsafe_write_count)} label="错误写动作"/></div>}</div><div className="panel"><div className="panelHead"><div><b>政策版本发布</b><span>草稿不入在线索引 · 原子发布</span></div></div><label>作用域<input value={scope} onChange={e=>setScope(e.target.value)}/></label><label>政策正文<textarea rows={8} value={content} onChange={e=>setContent(e.target.value)}/></label><button onClick={publish}>创建并发布</button><div className="policyHistory">{policies.map(policy=><div className="reconcileRow" key={policy.policy_version_id}><div><b>{policy.title} · {policy.version}</b><small>{policy.scope} · {policy.status} · 索引 {policy.index_status}</small>{policy.index_error&&<p className="errorText">{policy.index_error}</p>}</div></div>)}</div></div><div className="panel metrics"><div className="panelHead"><div><b>Week 4质量门禁</b><span>Calibration + Gold v3 + Hard-negative v2 · False Answer 必须为0</span></div><div className="runtimeTools"><button className="ghost" onClick={refreshEvaluations}>刷新</button><button className="ghost" onClick={compareLatest}>比较最近两次</button><button onClick={evaluate}>运行v3回归</button></div></div><div className="policyHistory">{profiles.map(item=>{const ready=profileReadiness[item.retrieval_profile_id];return <div className="reconcileRow" key={item.retrieval_profile_id}><div><b>{item.retrieval_profile_id} · {item.status}</b><small>{item.embedding_model} · Gate {item.gate_version} · 索引 {item.indexed_chunks}/{item.total_chunks}</small>{item.parent_profile_id&&<small>继承 {item.parent_profile_id}</small>}{ready&&!ready.activatable&&<p className="errorText">门禁：{ready.failed_gates.join("、")}</p>}{item.rejected_reason&&<p className="errorText">{item.rejected_reason}</p>}</div><button onClick={()=>activateProfile(item)} disabled={!ready?.activatable}>激活</button></div>})}</div><div className="policyHistory">{datasets.filter(item=>item.dataset.startsWith("week4_")).map(item=><div className="reconcileRow" key={`${item.dataset}-${item.version}`}><div><b>{item.dataset}/{item.version}</b><small>{item.status} · {item.case_count}案例 · {item.content_hash.slice(0,12)}…</small></div></div>)}</div>{run&&<><h3>{run.quality_status} · {run.status}</h3><div className="metricGrid"><Metric n={formatMetric(run.summary.recall_at_5)} label="Recall@5"/><Metric n={formatMetric(run.summary.mrr)} label="MRR"/><Metric n={formatMetric(run.summary.false_answer_rate)} label="False Answer"/><Metric n={formatMetric(run.summary.unanswerable_block_rate)} label="拦截率"/><Metric n={formatMetric(run.summary.semantic_only_false_answer_rate)} label="纯语义误答"/><Metric n={String(run.summary.p95_latency_ms??"—")} label="P95 ms"/></div>{run.quality_failures.length>0&&<ul className="errorText">{run.quality_failures.map(item=><li key={item}>{item}</li>)}</ul>}<label>筛选失败案例<input value={failureFilter} onChange={event=>setFailureFilter(event.target.value)} placeholder="case type / tag / reason"/></label>{visibleFailures.map(item=><div className="reconcileRow" key={item.case_id}><div><b>{item.details.case_type} · {item.details.actual_decision}</b><small>{item.details.reason_code} · {item.details.failure_reasons.join("、")}</small><p>Gate {String(item.details.diagnostics?.gate_rule_id||"—")} · 快照 {item.details.snapshot_id}</p></div></div>)}</>}{comparison&&<pre>{JSON.stringify(comparison,null,2)}</pre>}</div>
    <div className="panel runtimeCenter"><div className="panelHead"><div><b>混合检索调试</b><span>Lexical + Vector → RRF → Answerability Gate</span></div><button onClick={retrievalDebug}>执行检索</button></div><label>测试问题<input value={debugQuery} onChange={e=>setDebugQuery(e.target.value)}/></label>{debug&&<div className="taskDetail"><h3>{debug.decision} · {debug.reason_code}</h3><p>置信度 {debug.confidence} · 快照 {debug.retrieval_snapshot_id}</p>{debug.hits.map(hit=><div className="reconcileRow" key={hit.chunk_id}><div><b>{hit.title} · {hit.version}</b><small>lexical #{hit.lexical_rank||"—"} · vector #{hit.semantic_rank||"—"} · RRF {hit.rrf_score.toFixed(4)}</small><p>{hit.excerpt}</p></div></div>)}</div>}</div>
    <div className="panel runtimeCenter"><div className="panelHead"><div><b>任务运行中心</b><span>PostgreSQL 事实源 · 原死信不可变 · Worker 在线 {summary.workers.online} / 失联 {summary.workers.stale}</span></div><div className="runtimeTools"><select aria-label="任务状态筛选" value={filter} onChange={e=>setFilter(e.target.value)}><option value="">全部状态</option><option value="queued">queued</option><option value="running">running</option><option value="retry_wait">retry_wait</option><option value="dead_letter">dead_letter</option></select><button className="ghost" onClick={refreshRuntime}>刷新</button></div></div><div className="runtimeStats"><Metric n={String(count("queued"))} label="排队"/><Metric n={String(count("running"))} label="执行中"/><Metric n={String(count("retry_wait"))} label="等待重试"/><Metric n={String(count("dead_letter"))} label="死信"/></div><div className="runtimeLayout"><div className="taskTable"><div className="taskRow taskHeader"><span>类型</span><span>状态</span><span>次数</span><span>操作</span></div>{tasks.map(task=><div className="taskRow" key={task.task_id}><button className="taskLink" onClick={()=>inspect(task)}>{task.task_type}<small>{task.task_id.slice(0,18)}…</small></button><code>{task.status}</code><span>{task.attempts}/{task.max_attempts}</span><span>{["queued","retry_wait"].includes(task.status)&&<button className="ghost compact" onClick={()=>cancel(task)}>取消</button>}</span></div>)}</div><aside className="taskDetail">{selected?<><h3>任务详情</h3><code>{selected.task_id}</code><p className="errorText">{selected.last_error||"无错误"}</p>{selected.replay_of_task_id&&<p>来源任务：{selected.replay_of_task_id}</p>}<label>重放理由<textarea rows={3} value={reason} onChange={e=>setReason(e.target.value)}/></label>{selected.status==="dead_letter"&&<button onClick={replay}>创建关联重放任务</button>}<h4>事件时间线</h4><ol className="timeline">{selected.events?.map((event,index)=><li key={`${event.created_at}-${index}`}><b>{event.event_type}</b><small>{new Date(event.created_at).toLocaleString()}</small></li>)}</ol></>:<p>选择任务查看脱敏详情、错误和事件时间线。</p>}</aside></div></div>
    <div className="panel reconciliation"><div className="panelHead"><div><b>待对账执行</b><span>只查询外部回执，绝不自动重放原写操作</span></div></div>{reconciliations.length===0?<div className="emptyState">当前没有待人工核验的未知执行</div>:reconciliations.map(item=><div className="reconcileRow" key={item.execution_id}><div><b>{item.execution_id}</b><small>查询次数 {item.reconciliation_attempts} · {item.error_code}</small></div><button className="ghost" onClick={()=>resolve(item,"failed")}>确认失败</button><button onClick={()=>resolve(item,"succeeded")}>确认成功</button></div>)}</div></section>;
}
function PlatformView({notify}:{notify:(s:string)=>void}) {
  const [workers,setWorkers]=useState<WorkerInfo[]>([]);
  const [summary,setSummary]=useState<{workers:{online:number;stale:number}}>({workers:{online:0,stale:0}});
  async function refresh(){
    try {
      const [workerData, summaryData] = await Promise.all([
        api<WorkerInfo[]>("/api/v1/admin/workers"),
        api<{workers:{online:number;stale:number}}>("/api/v1/admin/runtime-summary"),
      ]);
      setWorkers(workerData); setSummary(summaryData); notify("平台运行状态已刷新");
    } catch(error) { notify((error as Error).message); }
  }
  useEffect(()=>{refresh()},[]);
  return <section className="adminGrid"><div className="panel runtimeCenter"><div className="panelHead"><div><b>全局 Worker</b><span>平台管理员仅查看运维元数据，不读取租户业务任务</span></div><button className="ghost" onClick={refresh}>刷新</button></div><div className="runtimeStats"><Metric n={String(summary.workers.online)} label="在线"/><Metric n={String(summary.workers.stale)} label="失联"/></div><div className="taskTable"><div className="taskRow taskHeader"><span>Worker</span><span>状态</span><span>进程</span><span>当前任务</span></div>{workers.map(worker=><div className="taskRow" key={worker.id}><span>{worker.hostname}</span><code>{worker.status}</code><span>{worker.process_id}</span><span>{worker.current_task_id || "空闲"}</span></div>)}</div></div></section>;
}
function Metric({n,label}:{n:string;label:string}){return <div className="metric"><b>{n}</b><span>{label}</span></div>}
function formatMetric(value:unknown){return typeof value==="number"?`${(value*100).toFixed(1)}%`:"—"}
