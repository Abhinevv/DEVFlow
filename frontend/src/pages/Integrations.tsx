import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Empty, ErrorText, StatusBadge, dateTime, relative, useLoad } from "../components/ui";

interface ApiKey { id: number; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
interface Webhook {
  id: number; name: string; url: string; events: string[]; header_names: string[]; active: boolean; created_at: string;
  last_delivery_at: string | null; last_status_code: number | null; last_error: string | null;
}
interface Delivery { id: number; event_type: string; success: boolean; status_code: number | null; attempts: number; error: string | null; duration_ms: number; created_at: string }
interface Catalog { events: Record<string, string>; defaults: string[] }

/** Shows a secret once, with a copy button. */
function Reveal({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="banner warn" style={{ display: "block" }}>
      <b>{label}</b> — copy it now; it will not be shown again.
      <div className="row" style={{ marginTop: 8 }}>
        <code className="log" style={{ flex: 1, padding: "6px 10px" }}>{value}</code>
        <button className="small" onClick={() => navigator.clipboard?.writeText(value).then(() => setCopied(true), () => setCopied(false))}>{copied ? "Copied" : "Copy"}</button>
        <button className="small ghost" onClick={onDone}>Done</button>
      </div>
    </div>
  );
}

function ApiKeys() {
  const [version, setVersion] = useState(0);
  const keys = useLoad(() => api<ApiKey[]>("/api/api-keys"), [version]);
  const [name, setName] = useState("n8n");
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = await api<ApiKey & { key: string }>("/api/api-keys", { body: { name } });
      setCreated(res.key);
      setVersion((v) => v + 1);
    } catch (err) { setError(err); }
  };
  const revoke = async (k: ApiKey) => {
    if (!window.confirm(`Revoke "${k.name}"? Anything using it will stop working.`)) return;
    try { await api(`/api/api-keys/${k.id}/revoke`, { method: "POST" }); setVersion((v) => v + 1); } catch (err) { setError(err); }
  };

  return (
    <div className="card">
      <div className="card-head"><h3>API keys</h3><span className="small faint">For automation such as n8n's HTTP Request node: <span className="mono">Authorization: Bearer dfk_…</span></span></div>
      <div className="card-body stack" style={{ gap: 12 }}>
        {created && <Reveal label="New API key" value={created} onDone={() => setCreated(null)} />}
        <form className="row" onSubmit={create}>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required aria-label="Key name" style={{ maxWidth: 280 }} placeholder="Key name" />
          <button className="primary" type="submit">Create key</button>
          <ErrorText error={error} />
        </form>
        {keys.data?.length === 0 && <div className="small muted">No keys yet.</div>}
      </div>
      {!!keys.data?.length && (
        <table>
          <thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th /></tr></thead>
          <tbody>{keys.data.map((k) => (
            <tr key={k.id}>
              <td><b>{k.name}</b></td>
              <td className="mono small">{k.prefix}…</td>
              <td className="small muted">{dateTime(k.created_at)}</td>
              <td className="small muted">{k.last_used_at ? relative(k.last_used_at) : "never"}</td>
              <td style={{ textAlign: "right" }}>{k.revoked_at ? <StatusBadge status="revoked" /> : <button className="small danger" onClick={() => revoke(k)}>Revoke</button>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

function WebhookRow({ hook, onChange }: { hook: Webhook; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState(0);
  const deliveries = useLoad(() => (open ? api<Delivery[]>(`/api/webhooks/${hook.id}/deliveries`) : Promise.resolve([])), [open, version, hook.id]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); onChange(); setVersion((v) => v + 1); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  const healthy = hook.last_status_code !== null && hook.last_status_code < 300;

  return (
    <div className="list-item">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="stack" style={{ gap: 2 }}>
          <div className="row"><b>{hook.name}</b>{!hook.active && <span className="badge">paused</span>}
            {hook.last_status_code !== null && <span className={`badge ${healthy ? "ok" : "bad"}`}>last: {hook.last_status_code}</span>}
            {hook.last_status_code === null && hook.last_error && <span className="badge bad">unreachable</span>}</div>
          <span className="mono small muted">{hook.url}</span>
          <span className="small faint">{hook.events.includes("*") ? "All events" : hook.events.join(", ")}
            {hook.header_names.length > 0 && ` · headers: ${hook.header_names.join(", ")}`}</span>
          {hook.last_error && <span className="small sev-high">{hook.last_error}</span>}
        </div>
        <div className="row">
          <button className="small" disabled={busy} onClick={() => run(() => api(`/api/webhooks/${hook.id}/test`, { method: "POST" }))}>Send test</button>
          <button className="small" disabled={busy} onClick={() => run(() => api(`/api/webhooks/${hook.id}`, { method: "PATCH", body: { active: !hook.active } }))}>{hook.active ? "Pause" : "Enable"}</button>
          <button className="small ghost" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Deliveries"}</button>
          <button className="small danger" disabled={busy} onClick={() => window.confirm(`Delete webhook "${hook.name}"?`) && run(() => api(`/api/webhooks/${hook.id}`, { method: "DELETE" }))}>Delete</button>
        </div>
      </div>
      <ErrorText error={error} />
      {open && (
        <div style={{ marginTop: 10 }}>
          {deliveries.data?.length === 0 && <div className="small muted">No deliveries yet.</div>}
          {deliveries.data?.map((d) => (
            <div key={d.id} className="row small" style={{ gap: 8, padding: "3px 0" }}>
              <span className={`badge ${d.success ? "ok" : "bad"}`}>{d.status_code ?? "—"}</span>
              <span className="mono">{d.event_type}</span>
              <span className="faint">{relative(d.created_at)} · {d.attempts} attempt(s) · {d.duration_ms} ms</span>
              {d.error && <span className="sev-high">{d.error}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Webhooks() {
  const [version, setVersion] = useState(0);
  const hooks = useLoad(() => api<Webhook[]>("/api/webhooks"), [version]);
  const catalog = useLoad(() => api<Catalog>("/api/webhooks/events"), []);
  const [name, setName] = useState("n8n");
  const [url, setUrl] = useState("http://localhost:5678/webhook/devflow-events");
  const [all, setAll] = useState(false);
  const [events, setEvents] = useState<string[] | null>(null);
  const [headerName, setHeaderName] = useState("X-Webhook-Token");
  const [headerValue, setHeaderValue] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const chosen = events ?? catalog.data?.defaults ?? [];

  const toggle = (e: string) => setEvents(chosen.includes(e) ? chosen.filter((x) => x !== e) : [...chosen, e]);
  const create = async (ev: FormEvent) => {
    ev.preventDefault();
    setError(null);
    try {
      const headers = headerName.trim() && headerValue ? { [headerName.trim()]: headerValue } : {};
      const res = await api<Webhook & { secret: string }>("/api/webhooks", { body: { name, url, events: all ? ["*"] : chosen, headers } });
      setSecret(res.secret);
      setHeaderValue("");
      setVersion((v) => v + 1);
    } catch (err) { setError(err); }
  };

  return (
    <div className="card">
      <div className="card-head"><h3>Webhooks</h3><span className="small faint">DevFlow POSTs signed JSON events to these URLs</span></div>
      <div className="card-body stack" style={{ gap: 12 }}>
        {secret && <Reveal label="Signing secret (verifies X-DevFlow-Signature)" value={secret} onDone={() => setSecret(null)} />}
        <form className="stack" style={{ gap: 12 }} onSubmit={create}>
          <div className="field-row">
            <div><label htmlFor="wh-name">Name</label><input id="wh-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></div>
            <div style={{ gridColumn: "span 2" }}><label htmlFor="wh-url">URL</label>
              <input id="wh-url" value={url} onChange={(e) => setUrl(e.target.value)} required pattern="https?://.+" maxLength={500} /></div>
          </div>
          <div>
            <label>Events</label>
            <label className="small" style={{ fontWeight: 400 }}><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} style={{ width: "auto", marginRight: 6 }} />All events</label>
            {!all && catalog.data && (
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 4, marginTop: 6 }}>
                {Object.entries(catalog.data.events).map(([key, desc]) => (
                  <label key={key} className="small" style={{ fontWeight: 400, margin: 0 }} title={desc}>
                    <input type="checkbox" checked={chosen.includes(key)} onChange={() => toggle(key)} style={{ width: "auto", marginRight: 6 }} />
                    <span className="mono">{key}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
          <div className="field-row">
            <div><label htmlFor="wh-hn">Auth header name <span className="faint">(optional)</span></label>
              <input id="wh-hn" value={headerName} onChange={(e) => setHeaderName(e.target.value)} maxLength={64} /></div>
            <div><label htmlFor="wh-hv">Auth header value</label>
              <input id="wh-hv" type="password" value={headerValue} onChange={(e) => setHeaderValue(e.target.value)} maxLength={500} autoComplete="off"
                placeholder="Matches n8n's Header Auth credential" /></div>
          </div>
          <div className="row"><button className="primary" type="submit">Add webhook</button><ErrorText error={error} /></div>
        </form>
      </div>
      {hooks.data?.length === 0 && <Empty>No webhooks yet.</Empty>}
      {hooks.data?.map((h) => <WebhookRow key={h.id} hook={h} onChange={() => setVersion((v) => v + 1)} />)}
    </div>
  );
}

export default function Integrations() {
  return (
    <main className="page" style={{ maxWidth: 980 }}>
      <div className="page-head">
        <div>
          <h1>Integrations</h1>
          <p>Connect DevFlow to n8n or other automation: webhooks push events out, API keys let tools call the DevFlow API.
            Ready-made n8n workflows are in <span className="mono">integrations/n8n</span>.</p>
        </div>
      </div>
      <div className="stack">
        <Webhooks />
        <ApiKeys />
      </div>
    </main>
  );
}
