import { FormEvent, useEffect, useState } from "react";

import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const SESSION_KEY = "gradionSession";

type User = { id: string; name: string; email: string; created_at: string };
type Project = {
  id: string;
  title: string;
  created_at: string;
  completed_stage: string;
  book_text?: string;
};

async function api<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? "Request failed");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export function App() {
  const [token, setToken] = useState(
    () => sessionStorage.getItem(SESSION_KEY) ?? "",
  );
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [screen, setScreen] = useState<
    "identity" | "projects" | "new" | "detail"
  >(token ? "projects" : "identity");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState(Boolean(token));
  const [error, setError] = useState("");

  const loadProjects = async (sessionToken: string) => {
    const items = await api<Project[]>("/projects", {}, sessionToken);
    setProjects(items);
  };

  useEffect(() => {
    if (!token || user) return;
    Promise.all([
      api<User>("/session", {}, token),
      api<Project[]>("/projects", {}, token),
    ])
      .then(([currentUser, items]) => {
        setUser(currentUser);
        setProjects(items);
      })
      .catch(() => {
        sessionStorage.removeItem(SESSION_KEY);
        setToken("");
        setScreen("identity");
      })
      .finally(() => setBusy(false));
  }, [token, user]);

  const signOut = async () => {
    try {
      await api("/session", { method: "DELETE" }, token);
    } finally {
      sessionStorage.removeItem(SESSION_KEY);
      setToken("");
      setUser(null);
      setProjects([]);
      setSelectedProject(null);
      setScreen("identity");
    }
  };

  if (busy)
    return (
      <main className="app-shell">
        <p>Loading…</p>
      </main>
    );

  if (screen === "identity") {
    return (
      <IdentityScreen
        onSignedIn={async (signedInUser, sessionToken) => {
          sessionStorage.setItem(SESSION_KEY, sessionToken);
          setToken(sessionToken);
          setUser(signedInUser);
          await loadProjects(sessionToken);
          setScreen("projects");
        }}
      />
    );
  }

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setScreen("projects")}>
          Gradion Studio
        </button>
        <div>
          <span>{user?.name}</span>
          <button className="text-button" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      {error && (
        <p className="error banner" role="alert">
          {error}
        </p>
      )}
      {screen === "projects" && (
        <section className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">Your library</p>
              <h1>Projects</h1>
            </div>
            <button onClick={() => setScreen("new")}>New project</button>
          </div>
          {projects.length === 0 ? (
            <div className="empty">
              <h2>No projects yet</h2>
              <p>Add a book to begin your illustration project.</p>
            </div>
          ) : (
            <div className="project-grid">
              {projects.map((project) => (
                <button
                  className="project-card"
                  key={project.id}
                  onClick={async () => {
                    try {
                      const detail = await api<Project>(
                        `/projects/${project.id}`,
                        {},
                        token,
                      );
                      setSelectedProject(detail);
                      setScreen("detail");
                    } catch (reason) {
                      setError((reason as Error).message);
                    }
                  }}
                >
                  <strong>{project.title}</strong>
                  <span>
                    Draft · {new Date(project.created_at).toLocaleDateString()}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      )}
      {screen === "new" && (
        <NewProject
          token={token}
          onCancel={() => setScreen("projects")}
          onCreated={(project) => {
            setProjects((current) => [project, ...current]);
            setSelectedProject(project);
            setScreen("detail");
          }}
        />
      )}
      {screen === "detail" && selectedProject && (
        <section className="content detail">
          <button
            className="text-button back"
            onClick={() => setScreen("projects")}
          >
            ← All projects
          </button>
          <p className="eyebrow">
            Status: Draft ({selectedProject.completed_stage}) ·{" "}
            {new Date(selectedProject.created_at).toLocaleDateString()}
          </p>
          <h1>{selectedProject.title}</h1>
          <section className="book">
            <h2>Book text</h2>
            <pre>{selectedProject.book_text}</pre>
          </section>
        </section>
      )}
    </main>
  );
}

function IdentityScreen({
  onSignedIn,
}: {
  onSignedIn: (user: User, token: string) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const validation = [
      !name.trim() ? "Name is required." : "",
      !/^\S+@\S+\.\S+$/.test(email.trim()) ? "Enter a valid email." : "",
    ].filter(Boolean);
    if (validation.length) return setErrors(validation);
    setBusy(true);
    setErrors([]);
    try {
      const result = await api<{ user: User; token: string }>("/session", {
        method: "POST",
        body: JSON.stringify({ name, email }),
      });
      await onSignedIn(result.user, result.token);
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="app-shell">
      <section className="welcome-card">
        <p className="eyebrow">Gradion</p>
        <h1>Book Illustration Studio</h1>
        <p>Sign in to continue your locally saved projects.</p>
        <form onSubmit={submit} noValidate>
          <label>
            Name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          {errors.map((message) => (
            <p className="error" key={message}>
              {message}
            </p>
          ))}
          <button disabled={busy}>{busy ? "Signing in…" : "Continue"}</button>
        </form>
      </section>
    </main>
  );
}

function NewProject({
  token,
  onCancel,
  onCreated,
}: {
  token: string;
  onCancel: () => void;
  onCreated: (project: Project) => void;
}) {
  const [title, setTitle] = useState("");
  const [bookText, setBookText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!title.trim()) return setError("Project title is required.");
    if (file && !file.name.toLowerCase().endsWith(".txt"))
      return setError("Choose a .txt file.");
    const text = file ? await file.text() : bookText;
    if (!text.trim()) return setError("Book text is required.");
    setBusy(true);
    try {
      const project = await api<Project>(
        "/projects",
        {
          method: "POST",
          body: JSON.stringify({
            title,
            book_text: text,
            source_filename: file?.name ?? null,
          }),
        },
        token,
      );
      onCreated(project);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="content form-page">
      <button className="text-button back" onClick={onCancel}>
        ← Cancel
      </button>
      <p className="eyebrow">New project</p>
      <h1>Add a book</h1>
      <form onSubmit={submit} noValidate>
        <label>
          Project title
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Choose a .txt file
          <input
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <span className="or">or</span>
        <label>
          Paste book text
          <textarea
            rows={12}
            value={bookText}
            onChange={(event) => setBookText(event.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button disabled={busy}>{busy ? "Creating…" : "Create project"}</button>
      </form>
    </section>
  );
}
