import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { api, db, eventDay, money, readAuth, synchronize } from "./data";
import { useResource } from "./useResource";
import { Empty, Message } from "./ui";

const VIEWS = [["sold", "Sales"], ["cancelled", "Cancelled"], ["discarded", "Discarded"], ["all", "All orders"]];
const EXPORTS = [["complete", "Complete sales report", "complete_sales"], ["sales", "Sales by item", "sales"],
  ["cancelled", "Cancelled orders", "cancelled"], ["discarded", "Discarded drinks", "discarded"], ["all", "All orders", "all_orders"]];
const STAGES = ["received", "making", "ready", "collected", "cancelled", "discarded"];
const labels = { recorded: "All recorded orders", refunded: "Recorded refunds", unpaid: "Unpaid cancellations", final: "Final recorded sales",
  no_refund: "Paid cancellations, no refund", discarded: "Discarded", cancelled: "All cancellations" };
const dateTime = (value) => value ? new Date(value).toLocaleString("en-SG", { timeZone: "Asia/Singapore", dateStyle: "medium", timeStyle: "medium" }) : "—";
const mins = (value) => value == null ? "—" : `${value} min`;
const options = (line) => [line.temperature, line.milk && `${line.milk} milk`].filter(Boolean).join(" · ");
const DEFAULT_SECTIONS = {
  "sales-summary": true, "by-drink": true, "by-item": false,
  "discard-breakdown": false, "discarded-orders": false,
  "refund-orders": false, "other-cancelled-orders": false,
  "cancel-summary": false, "cancel-breakdown": false, "cancelled-orders": false,
  "discard-summary": false, "all-orders": false,
};
const ReportSections = createContext(null);
function useSections() {
  const key = `bb-report-sections:v1:${readAuth()?.username || "staff"}`;
  const [choices, setChoices] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "{}");
      return Object.fromEntries(Object.entries(saved || {}).filter(([name, value]) => name in DEFAULT_SECTIONS && typeof value === "boolean"));
    } catch { return {}; }
  });
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(choices)); } catch { /* Reporting still works without storage. */ }
  }, [key, choices]);
  return {
    isOpen: (id) => choices[id] ?? DEFAULT_SECTIONS[id] ?? false,
    toggle: (id) => setChoices((old) => ({ ...old, [id]: !(old[id] ?? DEFAULT_SECTIONS[id] ?? false) })),
    expand: (id) => setChoices((old) => ({ ...old, [id]: true })),
    setAll: (value) => setChoices(Object.fromEntries(Object.keys(DEFAULT_SECTIONS).map((id) => [id, value]))),
  };
}
function Section({ id, title, metric, className = "", children }) {
  const sections = useContext(ReportSections);
  const expanded = sections.isOpen(id);
  return <section className={`panel table-panel report-section ${className}`} data-report-section={id}>
    <div className="panel-heading report-section-heading">
      <h2><button type="button" className="report-section-toggle" aria-expanded={expanded}
        aria-controls={`report-body-${id}`} onClick={() => sections.toggle(id)}>
        <span aria-hidden="true">{expanded ? "▾" : "▸"}</span> {title}
      </button></h2>
      {metric && <span className="report-section-metric">{metric}</span>}
    </div>
    <div className="report-section-body" id={`report-body-${id}`} hidden={!expanded}>{children}</div>
  </section>;
}
const orderMetric = (orders) => `${orders.length} orders · ${orders.reduce((n, o) => n + o.cups, 0)} cups · ${money(orders.reduce((n, o) => n + o.amount_cents, 0))}`;
function Table({ headers, children }) {
  return <div className="table-scroll"><table><thead><tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
function Summary({ data, onPick, id = "sales-summary", title = "Sales summary", keys = ["recorded", "refunded", "unpaid", "final", "no_refund", "discarded"] }) {
  const bucket = data?.[id === "discard-summary" ? "discarded" : "cancelled"];
  const metric = id === "sales-summary" ? `${money(data?.final?.amount_cents || 0)} final sales · ${data?.final?.cups || 0} cups`
    : `${bucket?.orders || 0} orders · ${bucket?.cups || 0} cups · ${money(bucket?.amount_cents || 0)}${id === "discard-summary" ? " included in sales" : ""}`;
  return <Section id={id} title={title} className="report-summary" metric={metric}>
    <Table headers={["Summary", "Orders", "Cups", "Amount", "Sales treatment"]}>{keys.map((key) => {
      const t = data?.[key] || { orders: 0, cups: 0, amount_cents: 0 };
      const treatment = ["refunded", "unpaid"].includes(key) ? "Excluded" : ["no_refund", "discarded"].includes(key) ? "Already included" : "";
      return <tr key={key}><td>{onPick ? <button className="tile-link" onClick={() => onPick(key)}>{labels[key]}</button> : labels[key]}</td>
        <td>{t.orders}</td><td>{t.cups}</td><td>{money(t.amount_cents)}</td><td>{treatment}</td></tr>;
    })}</Table>
    {data?.recorded && <p className="report-note">{money(data.recorded.amount_cents)} − {money(data.refunded.amount_cents)} − {money(data.unpaid.amount_cents)} = <strong>{money(data.final.amount_cents)}</strong>. Discarded value is selling value, not ingredient cost or lost profit.</p>}
  </Section>;
}
function MasterDetails({ order }) {
  return <details className="master-details"><summary>All details · {order.order_number}</summary>
    <dl className="master-fields"><div><dt>Order ID</dt><dd>{order.order_id}</dd></div><div><dt>Original order date</dt><dd>{order.event_day}</dd></div>
      <div><dt>Order-entry device</dt><dd>{order.device_id || "Not recorded"}</dd></div><div><dt>Order cups / total</dt><dd>{order.cups} / {money(order.amount_cents)}</dd></div>
      <div><dt>Making step</dt><dd>{order.making_step}</dd></div><div><dt>Counted in sales</dt><dd>{order.counts_in_sales ? "Yes" : "No"}</dd></div>
      <div><dt>Recorded money status</dt><dd>{order.money || "No cancellation/refund recorded"}</dd></div>
      {order.status === "cancelled" && <div><dt>Cancellation reason</dt><dd>{order.reason || "Not recorded"}</dd></div>}</dl>
    <Table headers={["Stage", "Occurred at (SGT)", "Login", "Device", "Action ID", "Synced/recorded at (SGT)"]}>{STAGES.map((s) => {
      const m = order.stages?.[s] || {}; const missing = m.at ? "Not recorded" : "—";
      return <tr key={s}><td>{s}</td><td>{dateTime(m.at)}</td><td>{m.by || missing}</td><td>{m.device_id || missing}</td><td>{m.action_id || missing}</td><td>{m.synced_at ? dateTime(m.synced_at) : missing}</td></tr>;
    })}</Table>
    <Table headers={["Drink", "Options", "Quantity", "Captured unit price", "Line subtotal", "Included in sales"]}>{order.lines.map((l, i) =>
      <tr key={l.line_id || i}><td>{l.item}</td><td>{options(l)}</td><td>{l.quantity}</td><td>{money(l.unit_price_cents)}</td><td>{money(l.quantity*l.unit_price_cents)}</td><td>{money(order.counts_in_sales ? l.quantity*l.unit_price_cents : 0)}</td></tr>)}</Table>
    <p className="report-note">Received → Ready: {mins(order.received_to_ready_min)} · Ready → Collected: {mins(order.ready_to_collected_min)} · Ready → Discarded: {mins(order.ready_to_discarded_min)} · Received → final closure: {mins(order.received_to_final_min)}</p>
  </details>;
}
function OrdersTable({ orders, kind, day, allDays, title, id }) {
  return <Section id={id || `${kind === "all" ? "all" : kind}-orders`} title={title || (kind === "all" ? "All orders" : kind === "discarded" ? "Discarded drinks" : "Cancelled orders")} className="report-orders" metric={`${orderMetric(orders)}${kind === "discarded" ? " included in sales" : ""}`}>
    <Table headers={["Order date", "Order / status", "Drinks", "Cups", "Order amount", ...(kind === "cancelled" ? ["Reason", "Money"] : []),
      ...(kind === "all" ? ["In sales"] : ["Ready at (SGT)", kind === "discarded" ? "Discarded at (SGT)" : "Cancellation recorded at (SGT)", "By", "Elapsed"]), "Details"]}>
      {orders.map((o) => <tr key={o.order_id}><td>{o.event_day}</td><td><strong>{o.order_number}</strong><br/>{o.status.charAt(0).toUpperCase()+o.status.slice(1)}</td><td>{o.drinks}</td><td>{o.cups}</td><td>{money(o.amount_cents)}</td>
        {kind === "cancelled" && <><td>{o.reason || "Not recorded"}</td><td>{o.money || "Not recorded"}</td></>}
        {kind === "all" ? <td>{o.counts_in_sales ? "Yes" : "No"}</td> : <><td>{dateTime(o.stages?.ready?.at)}</td><td>{dateTime(o.stages?.[kind]?.at)}</td><td>{o.by || "Not recorded"}</td><td>{mins(kind === "discarded" ? o.ready_to_discarded_min : o.received_to_final_min)}</td></>}
        <td><MasterDetails order={o}/></td></tr>)}
    </Table>
    {!orders.length && <Empty title={`No ${kind === "all" ? "orders" : `${kind} orders`} for ${allDays ? "all event days" : day}.`}>{!allDays && "Check All event days for older orders."}</Empty>}
  </Section>;
}
function Breakdown({ rows, title, id }) {
  return <Section id={id} title={title} metric={`${rows.reduce((n, r) => n + r.cups, 0)} cups · ${money(rows.reduce((n, r) => n + r.amount_cents, 0))}`}>
    <Table headers={["Drink", "Options", "Captured unit price", "Category", "Cups", "Sales value"]}>{rows.map((b,i) => <tr key={i}><td>{b.item}</td><td>{options(b)}</td><td>{money(b.unit_price_cents)}</td><td>{b.category}</td><td>{b.cups}</td><td>{money(b.amount_cents)}</td></tr>)}</Table>
  </Section>;
}
export function ReportScreen() {
  const sections = useSections();
  const root = useRef(null);
  const [scrollTarget, setScrollTarget] = useState(null);
  useEffect(() => {
    if (scrollTarget) root.current?.querySelector(`[data-report-section="${scrollTarget.id}"]`)?.scrollIntoView({ block: "start" });
  }, [scrollTarget]);
  const [day, setDay] = useState(eventDay), [scope, setScope] = useState("day"), [view, setView] = useState("sold");
  const [filter, setFilter] = useState(""), [menu, setMenu] = useState(false), [exporting, setExporting] = useState(false), [error, setError] = useState("");
  const pending = useLiveQuery(() => db.outbox.count(), [], 0);
  const query = `event_day=${day}&scope=${scope}`;
  // Always load the same master list; all visible exception sections derive from it.
  const master = useResource(`/reports/orders?kind=all&${query}`);

  const data = master.data;
  const drinkTotals = {};
  for (const o of data?.orders || []) if (o.counts_in_sales) for (const l of o.lines) {
    const d = drinkTotals[l.item] ||= {item_name:l.item, cups_sold:0, total_revenue_cents:0};
    d.cups_sold += l.quantity; d.total_revenue_cents += l.quantity*l.unit_price_cents;
  }
  const report = {drinks:Object.values(drinkTotals)};
  const orders = data?.orders || [], totals = data?.summary;
  const cancelled = orders.filter((o) => o.status === "cancelled"), discarded = orders.filter((o) => o.status === "discarded");
  const pick = (key) => {
    setFilter({refunded:"Refunded on POS",unpaid:"Not paid yet",no_refund:"Paid, no refund"}[key] || "");
    const nextView = key === "discarded" ? "discarded" : ["refunded","unpaid","no_refund","cancelled"].includes(key) ? "cancelled" : key === "recorded" ? "all" : "sold";
    const id = {discarded:"discarded-orders",cancelled:"cancelled-orders",all:"all-orders",sold:"by-drink"}[nextView];
    sections.expand(id); setView(nextView); setScrollTarget({id});
  };
  const segmented = (values, value, setter, name) => <div className="segmented report-segmented" role="group" aria-label={name}>{values.map(([key,label]) => <button key={key} aria-pressed={value===key} className={value===key?"active":""} onClick={() => setter(key)}>{label}</button>)}</div>;
  async function download(kind) {
    setMenu(false); setExporting(true); setError("");
    try {
      await synchronize(true);
      if (await db.outbox.count()) throw Error("Sync saved changes first so the export includes every order.");
      const blob = await api(`/reports/csv?kind=${kind}&${query}`, {blob:true});
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = `beansbeats_${EXPORTS.find(([k])=>k===kind)[2]}_${scope==="all"?"all-days":day}.csv`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) { setError(e.message); } finally { setExporting(false); }
  }
  const connected = master.connected;
  return <ReportSections.Provider value={sections}><div ref={root} className="report-content">
    <div className="toolbar report-toolbar"><label>Event day<input type="date" required value={day} disabled={scope==="all"} onChange={(e)=>e.target.value&&setDay(e.target.value)}/></label>
      {segmented([["day","This day"],["all","All event days"]],scope,setScope,"Report days")}
      {segmented(VIEWS,view,setView,"Show")}
      <button className="secondary" onClick={()=>{void master.reload();}}>Refresh</button>
      <button className="secondary" onClick={() => sections.setAll(true)}>Expand all</button>
      <button className="secondary" onClick={() => sections.setAll(false)}>Collapse all</button>
      <div className="export-menu toolbar-end"><button className="primary" aria-haspopup="menu" aria-expanded={menu} disabled={!connected||exporting} onClick={()=>setMenu(!menu)}>{exporting?"Exporting…":"Export CSV ▾"}</button>
        {menu&&<div className="export-options" role="menu">{EXPORTS.map(([k,l])=><button key={k} role="menuitem" onClick={()=>download(k)}>{l}</button>)}</div>}</div>
    </div>
    <p className="report-note">Dates refer to the <strong>original order event date</strong>, not when it was discarded or cancelled. Action timestamps use Singapore time (SGT).</p>
    <p className="report-note" role="status">{connected?"Updated":"Showing saved report · Last updated"}: {dateTime(master.updatedAt)} · {pending ? `${pending} changes waiting to sync. Report may be incomplete.` : "This device has no pending changes; other devices may still be offline."}</p>
    {(error||master.error)&&<Message error>{error||master.error}</Message>}
    {totals&&<><div className="report-strip"><dl><div><dt>Final recorded sales</dt><dd>{money(totals.final.amount_cents)}</dd></div><div><dt>Cups sold</dt><dd>{totals.final.cups}</dd></div>
      <div><dt>Cancelled</dt><dd><button className="tile-link" onClick={()=>pick("cancelled")}>{totals.cancelled.orders}</button></dd></div><div><dt>Discarded</dt><dd><button className="tile-link" onClick={()=>pick("discarded")}>{totals.discarded.orders}</button></dd></div></dl>
      <p className="report-note">Paid discarded drinks and paid cancellations without refund remain included in sales.</p></div>
      <Summary data={totals} onPick={pick}/>
      {view==="sold"&&<>
        <Section id="by-drink" title="By drink" metric={`${totals.final.cups} cups · ${money(totals.final.amount_cents)}`}><Table headers={["Drink","Cups","Sales","Discarded cups (included)","Discarded sales value (included)"]}>
          {(report?.drinks||[]).map((d)=>{const wasted=(data.breakdown||[]).filter(b=>b.item===d.item_name&&b.category==="discarded");return <tr key={d.item_name}><td>{d.item_name}</td><td>{d.cups_sold}</td><td>{money(d.total_revenue_cents)}</td><td>{wasted.reduce((n,b)=>n+b.cups,0)}</td><td>{money(wasted.reduce((n,b)=>n+b.amount_cents,0))}</td></tr>;})}
        </Table></Section>
        <Breakdown rows={(data.breakdown||[]).filter(b=>["sales","discarded","no_refund"].includes(b.category))} id="by-item" title="By item"/>
        <Breakdown rows={(data.breakdown||[]).filter(b=>b.category==="discarded")} id="discard-breakdown" title="Discarded drinks by item"/>
        <OrdersTable orders={discarded} kind="discarded" day={day} allDays={scope==="all"}/>
        <OrdersTable id="refund-orders" title="Recorded refunds" orders={cancelled.filter(o=>o.money==="Refunded on POS")} kind="cancelled" day={day} allDays={scope==="all"}/>
        <OrdersTable id="other-cancelled-orders" title="Other cancellations" orders={cancelled.filter(o=>o.money!=="Refunded on POS")} kind="cancelled" day={day} allDays={scope==="all"}/>
      </>}
      {view==="cancelled"&&<><Summary id="cancel-summary" title="Cancelled summary" data={totals} keys={["cancelled","refunded","unpaid","no_refund"]}/><div className="money-filter"><strong>Money</strong>{segmented([["","All"],["Refunded on POS","Refunded on POS"],["Not paid yet","Not paid yet"],["Paid, no refund","Paid, no refund"]],filter,setFilter,"Money")}</div>
        <Breakdown rows={(data.breakdown||[]).filter(b=>["refunded","unpaid","no_refund"].includes(b.category))} id="cancel-breakdown" title="Cancelled drinks by item"/>
        <OrdersTable orders={cancelled.filter(o=>!filter||o.money===filter)} kind="cancelled" day={day} allDays={scope==="all"}/></>}
      {view==="discarded"&&<><Summary id="discard-summary" title="Discarded summary" data={totals} keys={["discarded"]}/><Breakdown rows={(data.breakdown||[]).filter(b=>b.category==="discarded")} id="discard-breakdown" title="Discarded drinks by item"/><OrdersTable orders={discarded} kind="discarded" day={day} allDays={scope==="all"}/></>}
      {view==="all"&&<OrdersTable orders={orders} kind="all" day={day} allDays={scope==="all"}/>}</>}
    <p className="footnote">Payment/refunds happen on the organiser’s POS. These are staff-recorded whole-order refund statuses; POS transactions are not independently verified. Partial refunds and refunds after discard are not supported. No guest names in reports or exports.</p>
  </div></ReportSections.Provider>;
}
