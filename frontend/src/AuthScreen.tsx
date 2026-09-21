import { useState } from "react";
import type { FormEvent } from "react";
import { ArrowRight, AudioLines, Captions, ScanLine } from "lucide-react";
import { api, errorText, json, setCsrf } from "./api";
import type { Auth } from "./types";
import { Brand, Notice, Spinner } from "./ui";

export default function AuthScreen({
  setup,
  registrationOpen,
  onAuth,
}: {
  setup: boolean;
  registrationOpen: boolean;
  onAuth: (auth: Auth) => void;
}) {
  const [register, setRegister] = useState(setup);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const auth = await api<Auth>(
        register ? "/auth/register" : "/auth/login",
        json("POST", {
          email: data.get("email"),
          password: data.get("password"),
          ...(register ? { name: data.get("name") } : {}),
        }),
      );
      setCsrf(auth.csrf_token);
      onAuth(auth);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-page">
      <div className="auth-story">
        <Brand />
        <span className="eyebrow">THE MOMENT IS IN THERE.</span>
        <h1>
          Find it.
          <br />
          Make it
          <br />
          <em>worth replaying.</em>
        </h1>
        <p>
          A workspace for turning long recordings into the moments that matter.
          Search what was said, see what was shown, and keep the evidence.
        </p>
        <div className="auth-chips">
          <span>
            <AudioLines size={16} /> Speech
          </span>
          <span>
            <Captions size={16} /> Screen text
          </span>
          <span>
            <ScanLine size={16} /> Visuals
          </span>
        </div>
        <div className="auth-timeline" aria-hidden="true">
          <div />
          <div />
          <div />
          <span />
        </div>
        <small>YOUR FOOTAGE. YOUR EDIT. EVERY SOURCE IN VIEW.</small>
      </div>
      <section className="auth-form-panel">
        <div className="auth-form-content">
          <span className="eyebrow">WELCOME TO YOUR EDIT ROOM</span>
          <h2>{register ? "Create your account" : "Good to see you."}</h2>
          <p>
            {setup
              ? "Set up the first account to open your studio."
              : register
                ? "A new space for your recordings and ideas."
                : "Sign in to pick up where you left off."}
          </p>
          {error && <Notice warning>{error}</Notice>}
          <form onSubmit={submit}>
            {register && (
              <label>
                Your name
                <input
                  name="name"
                  autoComplete="name"
                  required
                  maxLength={80}
                  placeholder="Alex Morgan"
                />
              </label>
            )}
            <label>
              Email address
              <input
                name="email"
                type="email"
                autoComplete="email"
                required
                placeholder="you@example.com"
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete={register ? "new-password" : "current-password"}
                required
                minLength={register ? 10 : 1}
                maxLength={256}
                placeholder={
                  register ? "At least 10 characters" : "Your password"
                }
              />
            </label>
            <button className="button primary auth-submit" disabled={busy}>
              {busy ? (
                <Spinner label={register ? "Creating account" : "Signing in"} />
              ) : (
                <>
                  {register ? "Create account" : "Open studio"}
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>
          {!setup && registrationOpen && (
            <p className="auth-switch">
              {register ? "Already have an account?" : "New to Replay?"}{" "}
              <button
                className="text-button"
                onClick={() => {
                  setRegister(!register);
                  setError("");
                }}
              >
                {register ? "Sign in" : "Create an account"}
              </button>
            </p>
          )}
          <div className="auth-footnote">
            Private by default. Project access is managed by your team.
          </div>
        </div>
      </section>
    </main>
  );
}
