import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { api, getToken, setToken } from "./api";
import type { SystemInfo, User } from "./types";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import NewProject from "./pages/NewProject";
import ProjectView from "./pages/ProjectView";
import { Spinner } from "./components/ui";

interface Session { user: User; system: SystemInfo | null; signIn: (token: string, user: User) => void; signOut: () => void }

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside a signed-in route");
  return session;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [checking, setChecking] = useState(Boolean(getToken()));

  const signOut = useCallback(() => { setToken(null); setUser(null); }, []);
  const signIn = useCallback((token: string, u: User) => { setToken(token); setUser(u); }, []);

  useEffect(() => {
    if (!getToken()) return;
    api<User>("/api/auth/me").then(setUser, () => setToken(null)).finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    const onLogout = () => setUser(null);
    window.addEventListener("devflow:logout", onLogout);
    return () => window.removeEventListener("devflow:logout", onLogout);
  }, []);

  useEffect(() => {
    if (user) api<SystemInfo>("/api/system").then(setSystem, () => setSystem(null));
  }, [user]);

  if (checking) return <div className="auth-wrap"><Spinner /></div>;
  if (!user) return <Login onSignedIn={signIn} />;

  return (
    <SessionContext.Provider value={{ user, system, signIn, signOut }}>
      <header className="topbar">
        <Link to="/" className="brand"><span className="brand-mark">D</span>DevFlow AI</Link>
        <div className="spacer" />
        {system && (
          <span className="row small muted">
            <span className="badge accent">{system.llm_provider === "mock" ? "Mock provider" : system.llm_model}</span>
            <span className={`badge ${system.isolated_execution ? "ok" : "warn"}`} title="Where generated code runs">
              {system.isolated_execution ? "Docker sandbox" : system.executor === "local" ? "Local executor (not isolated)" : "No executor"}
            </span>
          </span>
        )}
        <span className="who">{user.display_name}</span>
        <button className="ghost small" onClick={signOut}>Sign out</button>
      </header>
      <Routes>
        <Route path="/" element={<Projects />} />
        <Route path="/new" element={<NewProject />} />
        <Route path="/projects/:id/*" element={<ProjectView />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </SessionContext.Provider>
  );
}
