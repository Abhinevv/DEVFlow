import { useEffect, useState } from "react";
import { api, apiText } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { Commit } from "../types";
import { Empty, ErrorText, Spinner, relative, useLoad } from "../components/ui";

function CodeView({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <div className="code-view">
      <table><tbody>{lines.map((line, i) => <tr key={i}><td className="ln">{i + 1}</td><td>{line || " "}</td></tr>)}</tbody></table>
    </div>
  );
}

function DiffView({ text }: { text: string }) {
  return (
    <div className="code-view diff">
      <table><tbody>{text.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ") ? "meta"
          : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
        return <tr key={i} className={cls}><td>{line || " "}</td></tr>;
      })}</tbody></table>
    </div>
  );
}

export default function CodeTab({ project, version }: TabProps) {
  const [view, setView] = useState<"files" | "history">("files");
  const files = useLoad(() => api<{ path: string; size: number }[]>(`/api/projects/${project.id}/files`), [project.id, version]);
  const commits = useLoad(() => api<Commit[]>(`/api/projects/${project.id}/commits`), [project.id, version]);
  const [path, setPath] = useState<string | null>(null);
  const [sha, setSha] = useState<string | null>(null);
  const [content, setContent] = useState<{ url: string; text: string; error?: unknown } | null>(null);

  useEffect(() => {
    if (!path && files.data?.length) setPath(files.data.find((f) => f.path === "app/main.py")?.path ?? files.data[0].path);
  }, [files.data, path]);
  useEffect(() => {
    if (!sha && commits.data?.length) setSha(commits.data[0].sha);
  }, [commits.data, sha]);

  useEffect(() => {
    const url = view === "files"
      ? path && `/api/projects/${project.id}/files/content?path=${encodeURIComponent(path)}`
      : sha && `/api/projects/${project.id}/commits/${sha}`;
    if (!url) return;
    let alive = true;
    // Clear stale content when the selection changes; keep it during live refreshes of the same item.
    setContent((c) => (c?.url === url ? c : null));
    apiText(url).then((text) => alive && setContent({ url, text }), (error) => alive && setContent({ url, text: "", error }));
    return () => { alive = false; };
  }, [view, path, sha, project.id, version]);

  if (files.data && files.data.length === 0) return <div className="card"><Empty>The workspace is created when execution starts.</Empty></div>;

  // Group files by directory for the tree.
  const groups = new Map<string, { path: string; size: number }[]>();
  (files.data ?? []).forEach((f) => {
    const dir = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
    groups.set(dir, [...(groups.get(dir) ?? []), f]);
  });

  return (
    <div className="stack">
      <div className="segmented" role="group" aria-label="Code view">
        <button className={view === "files" ? "on" : ""} onClick={() => setView("files")}>Files ({files.data?.length ?? 0})</button>
        <button className={view === "history" ? "on" : ""} onClick={() => setView("history")}>History ({commits.data?.length ?? 0} commits)</button>
      </div>
      <div className="code-layout">
        <div className="card file-tree">
          {view === "files" && [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([dir, items]) => (
            <div key={dir}>
              {dir && <div className="dir">{dir}/</div>}
              {items.map((f) => (
                <div key={f.path} className={`f ${path === f.path ? "selected" : ""}`} onClick={() => setPath(f.path)} role="button" tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && setPath(f.path)}>
                  <span>{f.path.slice(dir ? dir.length + 1 : 0)}</span><span className="faint">{(f.size / 1024).toFixed(1)}k</span>
                </div>
              ))}
            </div>
          ))}
          {view === "history" && (commits.data ?? []).map((c) => (
            <div key={c.sha} className={`list-item clickable ${sha === c.sha ? "selected" : ""}`} style={{ padding: "8px 14px" }}
              onClick={() => setSha(c.sha)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSha(c.sha)}>
              <div className="small" style={{ fontWeight: 550 }}>{c.message}</div>
              <div className="small faint"><span className="mono">{c.sha.slice(0, 8)}</span> · {relative(c.date)}</div>
              {c.stats && <div className="small faint">{c.stats}</div>}
            </div>
          ))}
        </div>
        <div className="card" style={{ minWidth: 0 }}>
          <div className="card-head"><span className="mono small">{view === "files" ? path : sha?.slice(0, 12)}</span></div>
          {content?.error ? <div className="card-body"><ErrorText error={content.error} /></div>
            : content ? (view === "files" ? <CodeView text={content.text} /> : <DiffView text={content.text} />)
            : <div className="card-body small muted"><Spinner /> Loading…</div>}
        </div>
      </div>
    </div>
  );
}
