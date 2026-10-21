import { useState, type FormEvent } from "react";
import { api } from "../api";
import type { User } from "../types";
import { ErrorText } from "../components/ui";

export default function Login({ onSignedIn }: { onSignedIn: (token: string, user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = mode === "login" ? { email, password } : { email, password, display_name: name };
      const res = await api<{ access_token: string; user: User }>(`/api/auth/${mode}`, { body });
      onSignedIn(res.access_token, res.user);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-wrap">
      <div className="auth-card">
        <div className="row" style={{ justifyContent: "center", marginBottom: 20 }}>
          <span className="brand"><span className="brand-mark">D</span>DevFlow AI</span>
        </div>
        <form className="card card-body" onSubmit={submit}>
          <h2 style={{ marginBottom: 4 }}>{mode === "login" ? "Sign in" : "Create an account"}</h2>
          <p className="muted small" style={{ marginTop: 0, marginBottom: 18 }}>Autonomous multi-agent software engineering</p>
          {mode === "register" && (
            <div className="field">
              <label htmlFor="name">Display name</label>
              <input id="name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoComplete="name" />
            </div>
          )}
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
              minLength={mode === "register" ? 8 : undefined} autoComplete={mode === "login" ? "current-password" : "new-password"} />
            {mode === "register" && <div className="hint">At least 8 characters.</div>}
          </div>
          <div className="field"><ErrorText error={error} /></div>
          <div className="field">
            <button className="primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center" }}>
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            </button>
          </div>
          <p className="small muted" style={{ textAlign: "center", marginBottom: 0 }}>
            {mode === "login" ? "No account yet? " : "Already registered? "}
            <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); setError(null); }}>
              {mode === "login" ? "Create one" : "Sign in"}
            </a>
          </p>
        </form>
      </div>
    </div>
  );
}
