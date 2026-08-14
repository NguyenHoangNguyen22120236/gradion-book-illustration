import { ChangeEvent, FormEvent, useCallback, useEffect, useState } from "react";

import {
  api,
  authenticatedImage,
  authenticatedMedia,
  openProjectStateStream,
} from "./api";
import type {
  Character,
  Chapter,
  CompletedStage,
  ImageState,
  PipelineAttempt,
  PipelineStep,
  Project,
  Narration,
  SampleBook,
  User,
} from "./types";
import "./styles.css";

const SESSION_KEY = "gradionSession";
const IDLE_NARRATION: Narration = {
  state: "IDLE",
  started_at: null,
  error: null,
  can_recover: false,
  audio_url: null,
};

const STEPS: Array<{ key: PipelineStep; label: string; completeAt: CompletedStage }> = [
  { key: "STYLE", label: "Style", completeAt: "STYLE_SET" },
  { key: "CHARACTERS", label: "Characters", completeAt: "CHARACTERS_GENERATED" },
  { key: "PORTRAITS", label: "Portraits", completeAt: "PORTRAITS_GENERATED" },
  { key: "CHAPTERS", label: "Chapters", completeAt: "CHAPTERS_GENERATED" },
  { key: "ILLUSTRATIONS", label: "Illustrations", completeAt: "DONE" },
];

const STAGE_ORDER: CompletedStage[] = [
  "CREATED",
  "STYLE_SET",
  "CHARACTERS_GENERATED",
  "PORTRAITS_GENERATED",
  "CHAPTERS_GENERATED",
  "DONE",
];

const RUNNING_LABEL: Record<PipelineStep, string> = {
  STYLE: "Generating style…",
  CHARACTERS: "Finding characters…",
  PORTRAITS: "Generating character portraits…",
  CHAPTERS: "Creating chapter prompt…",
  ILLUSTRATIONS: "Generating final illustration…",
};

function completedCount(stage: CompletedStage) {
  return Math.max(0, STAGE_ORDER.indexOf(stage));
}

function nextStep(stage: CompletedStage): PipelineStep | null {
  return STEPS[completedCount(stage)]?.key ?? null;
}

function stepLabel(step: PipelineStep | null) {
  return STEPS.find((item) => item.key === step)?.label ?? "step";
}

function routeFromHash() {
  const route = window.location.hash.replace(/^#\/?/, "");
  if (route === "projects/new") return { screen: "new" as const };
  const match = route.match(/^projects\/([^/]+)$/);
  if (match) return { screen: "detail" as const, projectId: match[1] };
  return { screen: "projects" as const };
}

function navigate(path: string) {
  window.location.hash = path;
}

export function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem(SESSION_KEY) ?? "");
  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [route, setRoute] = useState(routeFromHash);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState(Boolean(token));
  const [error, setError] = useState("");

  useEffect(() => {
    const updateRoute = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", updateRoute);
    return () => window.removeEventListener("hashchange", updateRoute);
  }, []);

  const applyProjectState = useCallback((detail: Project) => {
    setSelectedProject(detail);
    setProjects((current) =>
      current.map((item) => (item.id === detail.id ? { ...item, ...detail } : item)),
    );
  }, []);

  const loadProject = useCallback(async (projectId: string) => {
    const detail = await api<Project>(`/projects/${projectId}`, {}, token);
    applyProjectState(detail);
    return detail;
  }, [applyProjectState, token]);

  useEffect(() => {
    if (!token || user) return;
    let cancelled = false;
    const restore = async () => {
      try {
        const [currentUser, items] = await Promise.all([
          api<User>("/session", {}, token),
          api<Project[]>("/projects", {}, token),
        ]);
        if (cancelled) return;
        setUser(currentUser);
        setProjects(items);
      } catch {
        if (cancelled) return;
        sessionStorage.removeItem(SESSION_KEY);
        setToken("");
        navigate("/");
      } finally {
        if (!cancelled) setBusy(false);
      }
    };
    void restore();
    return () => {
      cancelled = true;
    };
  }, [token, user]);

  useEffect(() => {
    if (!token || !user || route.screen !== "detail") return;
    setError("");
    setSelectedProject(null);
    void loadProject(route.projectId).catch((reason: Error) => setError(reason.message));
  }, [loadProject, route, token, user]);

  const signOut = async () => {
    try {
      await api("/session", { method: "DELETE" }, token);
    } finally {
      sessionStorage.removeItem(SESSION_KEY);
      setToken("");
      setUser(null);
      setProjects([]);
      setSelectedProject(null);
      setError("");
      navigate("/");
    }
  };

  if (busy) return <LoadingPage label="Restoring your studio…" />;

  if (!token || !user) {
    return (
      <IdentityScreen
        onSignedIn={async (signedInUser, sessionToken) => {
          sessionStorage.setItem(SESSION_KEY, sessionToken);
          setToken(sessionToken);
          setUser(signedInUser);
          const items = await api<Project[]>("/projects", {}, sessionToken);
          setProjects(items);
          navigate("/projects");
        }}
      />
    );
  }

  return (
    <div className="workspace-shell">
      <Header user={user} onSignOut={signOut} />
      {error && <div className="global-error" role="alert">{error}</div>}
      {route.screen === "projects" && (
        <ProjectList projects={projects} onOpen={(id) => navigate(`/projects/${id}`)} />
      )}
      {route.screen === "new" && (
        <NewProject
          token={token}
          onCancel={() => navigate("/projects")}
          onCreated={(created) => {
            setProjects((current) => [created, ...current]);
            setSelectedProject(created);
            navigate(`/projects/${created.id}`);
          }}
        />
      )}
      {route.screen === "detail" &&
        (selectedProject?.id === route.projectId ? (
          <ProjectDetail
            key={selectedProject.id}
            project={selectedProject}
            token={token}
            loadProject={loadProject}
            onProjectState={applyProjectState}
          />
        ) : (
          <LoadingPage label="Loading project…" inline />
        ))}
      <Footer />
    </div>
  );
}

function Header({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const initials = user.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  return (
    <header className="topbar">
      <button className="brand" onClick={() => navigate("/projects")} aria-label="Gradion Book Illustration Studio home">
        <span className="brand-mark">G</span>
        <span>Gradion <b>Studio</b></span>
      </button>
      <nav aria-label="Primary navigation">
        <button className="nav-link" onClick={() => navigate("/projects")}>Projects</button>
      </nav>
      <div className="user-menu">
        <span className="avatar" aria-hidden="true">{initials}</span>
        <span className="user-name">{user.name}</span>
        <button className="nav-link subtle" onClick={onSignOut}>Sign out</button>
      </div>
    </header>
  );
}

function IdentityScreen({ onSignedIn }: { onSignedIn: (user: User, token: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const validation = [
      !name.trim() ? "Name is required." : "",
      !/^\S+@\S+\.\S+$/.test(email.trim()) ? "Enter a valid email." : "",
    ].filter(Boolean);
    if (validation.length) {
      setErrors(validation);
      return;
    }
    setSubmitting(true);
    setErrors([]);
    try {
      const result = await api<{ user: User; token: string }>("/session", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), email: email.trim() }),
      });
      await onSignedIn(result.user, result.token);
    } catch (reason) {
      setErrors([(reason as Error).message]);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="identity-page">
      <section className="identity-card" aria-labelledby="identity-title">
        <div className="identity-brand"><span className="brand-mark">G</span> Gradion</div>
        <p className="eyebrow">Book Illustration Studio</p>
        <h1 id="identity-title">Bring the people and places in your book to life.</h1>
        <p className="lede">Enter your details to start a project or resume exactly where you left off.</p>
        <form onSubmit={submit} noValidate>
          <Field label="Full name" required>
            <input autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Mira Hassan" />
          </Field>
          <Field label="Email" required>
            <input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="mira@example.com" />
          </Field>
          {errors.length > 0 && <div className="form-error" role="alert">{errors.map((message) => <p key={message}>{message}</p>)}</div>}
          <button className="primary wide" disabled={submitting}>{submitting ? "Signing in…" : "Continue to your studio"}<span aria-hidden="true">→</span></button>
        </form>
        <p className="fine-print">No password or OAuth. Your lightweight session only identifies your locally stored projects.</p>
      </section>
    </main>
  );
}

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return <label className="field"><span>{label}{required && <em aria-hidden="true"> *</em>}</span>{children}</label>;
}

function ProjectList({ projects, onOpen }: { projects: Project[]; onOpen: (id: string) => void }) {
  return (
    <main className="page">
      <div className="page-heading">
        <div><p className="eyebrow">Your library</p><h1>Illustration projects</h1><p>Every project resumes from its persisted backend state.</p></div>
        <button className="primary" onClick={() => navigate("/projects/new")}>New project <span aria-hidden="true">＋</span></button>
      </div>
      {projects.length === 0 ? (
        <section className="empty-state">
          <div className="empty-icon" aria-hidden="true">✦</div>
          <h2>No projects yet</h2>
          <p>Add a plain-text book, then guide it through five deliberate illustration steps.</p>
          <button className="primary" onClick={() => navigate("/projects/new")}>Create your first project</button>
        </section>
      ) : (
        <div className="project-list" aria-label="Projects">
          {projects.map((item) => <ProjectRow key={item.id} project={item} onOpen={onOpen} />)}
        </div>
      )}
    </main>
  );
}

function ProjectRow({ project, onOpen }: { project: Project; onOpen: (id: string) => void }) {
  const complete = completedCount(project.completed_stage);
  const status = project.completed_stage === "DONE" ? "Done" : project.completed_stage === "CREATED" && project.step_state === "IDLE" ? "Draft" : "In progress";
  return (
    <button className="project-row" onClick={() => onOpen(project.id)}>
      <span className="project-summary"><strong>{project.title}</strong><small>Created {formatDate(project.created_at)}</small></span>
      <span className="mini-progress" aria-label={`${complete} of 5 steps complete`}>
        <span className="mini-bars" aria-hidden="true">{STEPS.map((step, index) => <i key={step.key} data-complete={index < complete} />)}</span>
        <small>{complete} of 5 steps complete</small>
      </span>
      <span className={`status-pill ${status.toLowerCase().replace(" ", "-")}`}>{status}</span>
      <span className="row-arrow" aria-hidden="true">→</span>
    </button>
  );
}

function NewProject({ token, onCancel, onCreated }: { token: string; onCancel: () => void; onCreated: (project: Project) => void }) {
  const [title, setTitle] = useState("");
  const [bookText, setBookText] = useState("");
  const [sourceFilename, setSourceFilename] = useState<string | null>(null);
  const [sampleBooks, setSampleBooks] = useState<SampleBook[]>([]);
  const [selectedSampleId, setSelectedSampleId] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api<SampleBook[]>("/sample-books", {}, token)
      .then((samples) => {
        if (!cancelled) setSampleBooks(samples);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const chooseFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setSourceFilename(null);
      setError("Unsupported file type. Choose a .txt file.");
      event.target.value = "";
      return;
    }
    try {
      setBookText(await file.text());
      setSourceFilename(file.name);
      setError("");
    } catch {
      setError("The selected file could not be read.");
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (!title.trim()) return setError("Project title is required.");
    if (!selectedSampleId && !bookText.trim()) return setError("Choose a sample book, paste text, or choose a .txt file.");
    setSubmitting(true);
    try {
      const payload = selectedSampleId
        ? { title: title.trim(), sample_book_id: selectedSampleId }
        : { title: title.trim(), book_text: bookText, source_filename: sourceFilename };
      const created = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify(payload),
      }, token);
      onCreated(created);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="page narrow-page">
      <button className="back-link" onClick={onCancel}>← Back to projects</button>
      <p className="eyebrow">New project</p>
      <h1>Add a book</h1>
      <p className="page-intro">Creating a project saves its text locally. Gemini is not called until you explicitly start Style.</p>
      <form className="project-form" onSubmit={submit} noValidate>
        <Field label="Project title" required><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="The Wind in the Willows" /></Field>
        <fieldset className="sample-picker">
          <legend>Choose a sample book</legend>
          <div className="sample-options">
            {sampleBooks.map((sample) => (
              <label className="sample-option" key={sample.id}>
                <input
                  type="radio"
                  name="sample-book"
                  value={sample.id}
                  checked={selectedSampleId === sample.id}
                  onChange={() => {
                    setSelectedSampleId(sample.id);
                    setBookText("");
                    setSourceFilename(null);
                    setError("");
                  }}
                />
                <span><strong>{sample.title}</strong><small>{sample.author}</small></span>
              </label>
            ))}
          </div>
          {selectedSampleId && <button type="button" className="text-button" onClick={() => setSelectedSampleId("")}>Clear sample selection</button>}
        </fieldset>
        <span className="or"><i />or upload a .txt file<i /></span>
        <div className={`upload-block ${selectedSampleId ? "source-disabled" : ""}`}>
          <label className="file-picker" aria-disabled={Boolean(selectedSampleId)}>
            <span className="upload-icon" aria-hidden="true">↑</span>
            <strong>{sourceFilename ?? "Choose a .txt file"}</strong>
            <small>Plain text only</small>
            <input type="file" accept=".txt,text/plain" onChange={chooseFile} disabled={Boolean(selectedSampleId)} />
          </label>
          <span className="or"><i />or paste text<i /></span>
          <Field label="Book text" required><textarea rows={14} value={bookText} disabled={Boolean(selectedSampleId)} onChange={(event) => { setBookText(event.target.value); if (sourceFilename) setSourceFilename(null); }} placeholder="Once upon a time…" /></Field>
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary wide" disabled={submitting}>{submitting ? "Creating project…" : "Create project"}<span aria-hidden="true">→</span></button>
      </form>
    </main>
  );
}

function ProjectDetail({ project, token, loadProject, onProjectState }: { project: Project; token: string; loadProject: (projectId: string) => Promise<Project>; onProjectState: (project: Project) => void }) {
  const [style, setStyle] = useState("");
  const [mutating, setMutating] = useState(false);
  const [actionError, setActionError] = useState("");
  const refresh = useCallback(() => loadProject(project.id), [loadProject, project.id]);

  useEffect(() => openProjectStateStream(project.id, token, {
    onProjectState,
    onDisconnect: () => {
      void refresh()
        .then(() => setActionError(""))
        .catch((reason: Error) => setActionError(reason.message));
    },
  }), [onProjectState, project.id, refresh, token]);

  const applyCommandState = (updated: Project) => {
    onProjectState({
      ...updated,
      book_text: updated.book_text ?? project.book_text,
    });
  };

  const runStep = async (step: PipelineStep) => {
    setMutating(true);
    setActionError("");
    try {
      const updated = await api<Project>(`/projects/${project.id}/steps/${step.toLowerCase()}`, {
        method: "POST",
        ...(step === "STYLE" ? { body: JSON.stringify({ style: style.trim() || null }) } : {}),
      }, token);
      applyCommandState(updated);
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setMutating(false);
    }
  };

  const recover = async () => {
    setMutating(true);
    setActionError("");
    try {
      const updated = await api<Project>(`/projects/${project.id}/recover`, { method: "POST" }, token);
      applyCommandState(updated);
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setMutating(false);
    }
  };

  const runNarration = async () => {
    setMutating(true);
    setActionError("");
    try {
      const updated = await api<Project>(`/projects/${project.id}/narration`, { method: "POST" }, token);
      applyCommandState(updated);
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setMutating(false);
    }
  };

  const recoverNarration = async () => {
    setMutating(true);
    setActionError("");
    try {
      const updated = await api<Project>(`/projects/${project.id}/narration/recover`, { method: "POST" }, token);
      applyCommandState(updated);
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setMutating(false);
    }
  };

  return (
    <main className="page detail-page">
      <button className="back-link" onClick={() => navigate("/projects")}>← Back to projects</button>
      <div className="detail-heading"><div><p className="eyebrow">Illustration project</p><h1>{project.title}</h1><p>Created {formatDate(project.created_at)}</p></div><StatusPill project={project} /></div>
      <PipelineStepper project={project} />
      <div className="detail-layout">
        <div className="detail-main">
          <ActionPanel project={project} style={style} onStyle={setStyle} busy={mutating} error={actionError} onRun={runStep} onRecover={recover} />
          {project.completed_stage === "DONE" && (
            <NarrationPanel
              narration={project.narration ?? IDLE_NARRATION}
              token={token}
              busy={mutating}
              error={actionError}
              onRun={runNarration}
              onRecover={recoverNarration}
            />
          )}
          <AttemptHistory attempts={project.attempts ?? []} />
          {project.style && <section className="result-section style-result"><p className="section-kicker">Established art direction</p><h2>Style</h2><p>{project.style}</p></section>}
          {project.characters.length > 0 && <section className="result-section"><div className="section-heading"><div><p className="section-kicker">Cast</p><h2>Characters</h2></div><span>{project.characters.length} of 2 maximum</span></div><div className="character-grid">{project.characters.map((character) => <CharacterCard key={character.id} character={character} token={token} />)}</div></section>}
          {project.chapters.length > 0 && <section className="result-section"><div className="section-heading"><div><p className="section-kicker">Scene</p><h2>Chapter illustration</h2></div></div>{project.chapters.map((chapter) => <ChapterCard key={chapter.id} chapter={chapter} token={token} />)}</section>}
        </div>
        <aside className="book-panel"><p className="section-kicker">Source material</p><h2>Full book text</h2><div className="book-text">{project.book_text}</div></aside>
      </div>
    </main>
  );
}

function NarrationPanel({ narration, token, busy, error, onRun, onRecover }: { narration: Narration; token: string; busy: boolean; error: string; onRun: () => void; onRecover: () => void }) {
  return (
    <section className="result-section narration-panel" aria-labelledby="narration-title">
      <p className="section-kicker">Optional bonus</p>
      <h2 id="narration-title">Narration</h2>
      {narration.state === "IDLE" && (
        <>
          <p>Generate a single-speaker reading of a short opening excerpt from chapter one.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary" disabled={busy} onClick={onRun}>{busy ? "Starting narrationâ€¦" : "Generate narration"}</button>
        </>
      )}
      {narration.state === "RUNNING" && narration.can_recover && (
        <div className="narration-state warning-panel">
          <h3>Narration was interrupted.</h3>
          <p>Recover the interrupted state first. Generation will not restart until you explicitly retry it.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="secondary" disabled={busy} onClick={onRecover}>{busy ? "Recoveringâ€¦" : "Recover narration"}</button>
        </div>
      )}
      {narration.state === "RUNNING" && !narration.can_recover && (
        <div className="narration-state" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <div><h3>Generating narrationâ€¦</h3><p>The protected audio will appear here when it is ready.</p></div>
        </div>
      )}
      {narration.state === "FAILED" && (
        <div className="narration-state error-panel">
          <h3>Narration generation failed.</h3>
          <p className="persisted-error" role="alert">{narration.error || "Narration failed without an error message."}</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary" disabled={busy} onClick={onRun}>{busy ? "Retrying narrationâ€¦" : "Retry narration"}</button>
        </div>
      )}
      {narration.state === "COMPLETED" && narration.audio_url && (
        <AuthenticatedAudio url={narration.audio_url} token={token} />
      )}
    </section>
  );
}

function AuthenticatedAudio({ url, token }: { url: string; token: string }) {
  const [objectUrl, setObjectUrl] = useState("");
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let createdUrl = "";
    void authenticatedMedia(url, token, "Narration audio could not be loaded")
      .then((blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setObjectUrl(createdUrl);
      })
      .catch((reason: Error) => {
        if (!cancelled) setLoadError(reason.message);
      });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [token, url]);

  if (loadError) return <p className="form-error" role="alert">{loadError}</p>;
  if (!objectUrl) return <div className="narration-state" aria-live="polite"><span className="spinner" aria-hidden="true" /><p>Loading narrationâ€¦</p></div>;
  return <audio controls src={objectUrl} aria-label="Chapter opening narration" />;
}

function AttemptHistory({ attempts }: { attempts: PipelineAttempt[] }) {
  if (attempts.length === 0) return null;
  return (
    <section className="attempt-history" aria-label="Pipeline attempt history">
      <p className="section-kicker">Execution history</p>
      <div className="attempt-groups">
        {STEPS.map((step) => {
          const stepAttempts = attempts.filter((attempt) => attempt.step === step.key);
          if (stepAttempts.length === 0) return null;
          return (
            <section className="attempt-group" key={step.key}>
              <h2>{step.label} attempts</h2>
              <ol>
                {stepAttempts.map((attempt) => (
                  <li className={`attempt-row ${attempt.outcome.toLowerCase()}`} key={attempt.id}>
                    <div>
                      <strong>Attempt {attempt.attempt_number} · {attemptOutcomeLabel(attempt.outcome)}</strong>
                      <span>
                        Started <time dateTime={attempt.started_at}>{formatAttemptTime(attempt.started_at)}</time>
                        {attempt.ended_at && <> · Ended <time dateTime={attempt.ended_at}>{formatAttemptTime(attempt.ended_at)}</time></>}
                      </span>
                    </div>
                    {attempt.error && <p>{attempt.error}</p>}
                  </li>
                ))}
              </ol>
            </section>
          );
        })}
      </div>
    </section>
  );
}

function attemptOutcomeLabel(outcome: PipelineAttempt["outcome"]) {
  return outcome.charAt(0) + outcome.slice(1).toLowerCase();
}

function formatAttemptTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function StatusPill({ project }: { project: Project }) {
  const label = project.completed_stage === "DONE" ? "Done" : project.completed_stage === "CREATED" && project.step_state === "IDLE" ? "Draft" : "In progress";
  return <span className={`status-pill ${label.toLowerCase().replace(" ", "-")}`}>{label}</span>;
}

function PipelineStepper({ project }: { project: Project }) {
  const complete = completedCount(project.completed_stage);
  return (
    <ol className="stepper" aria-label="Illustration pipeline">
      {STEPS.map((step, index) => {
        const state = index < complete ? "complete" : index === complete && project.completed_stage !== "DONE" ? "current" : "pending";
        return <li key={step.key} className={state}><span className="step-number" aria-hidden="true">{state === "complete" ? "✓" : index + 1}</span><span><strong>{step.label}</strong><small>{state === "complete" ? "Complete" : state === "current" ? project.step_state === "RUNNING" ? "Running" : project.step_state === "FAILED" ? "Needs attention" : "Next" : "Pending"}</small></span></li>;
      })}
    </ol>
  );
}

function ActionPanel({ project, style, onStyle, busy, error, onRun, onRecover }: { project: Project; style: string; onStyle: (value: string) => void; busy: boolean; error: string; onRun: (step: PipelineStep) => void; onRecover: () => void }) {
  const next = nextStep(project.completed_stage);
  if (project.completed_stage === "DONE") return <section className="action-panel complete-panel"><span className="success-mark" aria-hidden="true">✓</span><div><h2>All five steps are complete</h2><p>Your persisted portraits and final illustration are ready whenever you return.</p></div></section>;
  if (project.step_state === "RUNNING" && project.can_recover) return <section className="action-panel warning-panel"><p className="panel-label">Interrupted {stepLabel(project.active_step)}</p><h2>The previous backend execution was interrupted.</h2><p>Completed work is preserved. Recover this run before manually retrying the same step.</p>{error && <p className="form-error" role="alert">{error}</p>}<button className="secondary" disabled={busy} onClick={onRecover}>{busy ? "Recovering…" : `Recover ${stepLabel(project.active_step)}`}</button></section>;
  if (project.step_state === "RUNNING") return <section className="action-panel running-panel" aria-live="polite"><span className="spinner" aria-hidden="true" /><div><p className="panel-label">Step in progress</p><h2>{project.active_step ? RUNNING_LABEL[project.active_step] : "Generating…"}</h2><p>This page follows the backend’s persisted state. It will update as results land.</p></div></section>;
  if (project.step_state === "FAILED") return <section className="action-panel error-panel"><p className="panel-label">{stepLabel(project.active_step)} failed</p><h2>This step needs your attention.</h2><p className="persisted-error" role="alert">{project.step_error || "The step failed without an error message."}</p>{project.active_step === "STYLE" && <Field label="Art style for retry (optional)"><input value={style} onChange={(event) => onStyle(event.target.value)} placeholder="Leave blank for Gemini to derive a style from the book" /></Field>}{error && <p className="form-error" role="alert">{error}</p>}<button className="primary" disabled={busy || !project.active_step} onClick={() => project.active_step && onRun(project.active_step)}>{busy ? "Retrying…" : `Retry ${stepLabel(project.active_step)}`}</button></section>;
  return <section className="action-panel"><p className="panel-label">Next step · {stepLabel(next)}</p><h2>{next === "STYLE" ? "Choose the visual language for this book." : `Ready to generate ${stepLabel(next).toLowerCase()}.`}</h2>{next === "STYLE" && <Field label="Art style (optional)"><input value={style} onChange={(event) => onStyle(event.target.value)} placeholder="Leave blank for Gemini to derive a style from the book" /></Field>}<p>Each step starts only when you ask. Nothing advances until the backend reports success.</p>{error && <p className="form-error" role="alert">{error}</p>}<button className="primary" disabled={busy || !next} onClick={() => next && onRun(next)}>{busy ? "Starting…" : `Generate ${stepLabel(next)}`}<span aria-hidden="true">→</span></button></section>;
}

function CharacterCard({ character, token }: { character: Character; token: string }) {
  return <article className="entity-card"><GeneratedImage url={character.portrait_url} token={token} state={character.image_state} alt={`Portrait of ${character.name}`} kind="portrait" name={character.name} error={character.image_error} /><div className="entity-copy"><p className="item-state">{imageStateLabel(character.image_state)}</p><h3>{character.name}</h3><p>{character.prompt}</p></div></article>;
}

function ChapterCard({ chapter, token }: { chapter: Chapter; token: string }) {
  return <article className="chapter-card"><GeneratedImage url={chapter.illustration_url} token={token} state={chapter.image_state} alt={`Illustration for ${chapter.name}`} kind="illustration" name={chapter.name} error={chapter.image_error} /><div className="chapter-copy"><p className="item-state">{imageStateLabel(chapter.image_state)}</p><h3>{chapter.name}</h3><p>{chapter.prompt}</p></div></article>;
}

function GeneratedImage({ url, token, state, alt, kind, name, error }: { url: string | null; token: string; state: ImageState; alt: string; kind: "portrait" | "illustration"; name: string; error: string | null }) {
  const [objectUrl, setObjectUrl] = useState("");
  const [loadError, setLoadError] = useState("");
  useEffect(() => {
    if (state !== "READY" || !url) {
      setObjectUrl("");
      return;
    }
    let cancelled = false;
    let createdUrl = "";
    void authenticatedImage(url, token).then((blob) => {
      if (cancelled) return;
      createdUrl = URL.createObjectURL(blob);
      setObjectUrl(createdUrl);
    }).catch((reason: Error) => {
      if (!cancelled) setLoadError(reason.message);
    });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [state, token, url]);

  const className = `generated-art ${kind}`;
  if (objectUrl) return <div className={className}><img src={objectUrl} alt={alt} /></div>;
  if (state === "GENERATING") return <div className={`${className} placeholder`}><span className="spinner" aria-hidden="true" /><p>Generating {kind} for {name}…</p></div>;
  if (state === "FAILED") return <div className={`${className} placeholder failed-art`}><span aria-hidden="true">!</span><p>{error || `${kind} generation failed`}</p></div>;
  if (state === "READY") return <div className={`${className} placeholder`}><span className="spinner" aria-hidden="true" /><p>{loadError || `Loading ${kind}…`}</p></div>;
  return <div className={`${className} placeholder`}><span aria-hidden="true">◇</span><p>{kind === "portrait" ? "Portrait not generated yet" : "Illustration not generated yet"}</p></div>;
}

function imageStateLabel(state: ImageState) {
  return ({ PENDING: "Awaiting generation", GENERATING: "Generating", READY: "Ready", FAILED: "Generation failed" } as const)[state];
}

function LoadingPage({ label, inline = false }: { label: string; inline?: boolean }) {
  return <main className={inline ? "inline-loading" : "loading-page"} aria-live="polite"><span className="spinner" aria-hidden="true" /><p>{label}</p></main>;
}

function Footer() {
  return <footer><span>GRADION</span><i /> Scaling Business</footer>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" }).format(new Date(value));
}
