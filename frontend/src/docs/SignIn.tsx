import { Lock, LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

/** A private site and nobody signed in: the sign-in of PlainBoard, here at the site (also at its own domain, whose
 * session cookie is that domain's). Only people with a role in the site's project read it afterwards. */
export function SignIn({ name }: { name: string }) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const resp = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (resp.ok) {
        location.reload();
        return;
      }
      setError(t(resp.status === 429 ? "docsSite.signInBusy" : "docsSite.signInFailed"));
    } catch {
      setError(t("docsSite.failed"));
    }
    setBusy(false);
  };
  return (
    <div className="doc-signin">
      <form className="doc-signin-card" onSubmit={submit}>
        <span className="doc-mark is-big" aria-hidden>
          {name.charAt(0).toUpperCase()}
        </span>
        <h1>{name}</h1>
        <p className="doc-signin-lead">
          <Lock className="size-4 shrink-0" /> {t("docsSite.privateHint")}
        </p>
        <label>
          <span>{t("docsSite.username")}</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required autoFocus />
        </label>
        <label>
          <span>{t("docsSite.password")}</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && (
          <p className="doc-signin-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="doc-btn is-wide" disabled={busy}>
          <LogIn className="size-4" /> {t("docsSite.signIn")}
        </button>
        <p className="doc-signin-foot">{t("docsSite.membersOnly")}</p>
      </form>
    </div>
  );
}
