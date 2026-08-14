import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const NativeURL = URL;

const user = {
  id: "user-1",
  name: "Mira Hassan",
  email: "mira@example.com",
  created_at: "2026-01-01T00:00:00Z",
};

const project = {
  id: "project-1",
  title: "River Story",
  created_at: "2026-01-02T10:00:00Z",
  updated_at: "2026-01-02T10:00:00Z",
  completed_stage: "CREATED",
  step_state: "IDLE",
  active_step: null,
  step_started_at: null,
  step_error: null,
  can_recover: false,
  style: null,
  characters: [],
  chapters: [],
  attempts: [],
  book_text: "Once beside the river, Mole opened the door to spring.",
};

const sampleBooks = [
  {
    id: "alice-in-wonderland",
    title: "Alice’s Adventures in Wonderland",
    author: "Lewis Carroll",
  },
  {
    id: "wizard-of-oz",
    title: "The Wonderful Wizard of Oz",
    author: "L. Frank Baum",
  },
  {
    id: "wind-in-the-willows",
    title: "The Wind in the Willows",
    author: "Kenneth Grahame",
  },
];

const response = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

type FetchHandler = (path: string, init: RequestInit) => Promise<Response>;

function mockApi({
  projects = [],
  detail = project,
  handler,
}: {
  projects?: Array<Record<string, unknown>>;
  detail?: Record<string, unknown>;
  handler?: FetchHandler;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init = {}) => {
    const path = new URL(String(input), "http://test.local").pathname;
    if (handler) {
      const handled = handler(path, init);
      if (handled) return handled;
    }
    if (path === "/api/session") return response(user);
    if (path === "/api/sample-books") return response(sampleBooks);
    if (path === "/api/projects") return response(projects);
    if (path === `/api/projects/${project.id}`) return response(detail);
    throw new Error(`Unexpected request: ${init.method ?? "GET"} ${path}`);
  });
}

async function openProject(
  detail: Record<string, unknown> = project,
  handler?: FetchHandler,
) {
  const listItem = { ...detail };
  delete listItem.book_text;
  const fetchMock = mockApi({ projects: [listItem], detail, handler });
  render(<App />);
  const projectButton = await screen.findByRole("button", { name: /river story/i });
  fireEvent.click(projectButton);
  await screen.findByRole("heading", { name: "River Story" });
  return fetchMock;
}

describe("App", () => {
  beforeEach(() => {
    window.location.hash = "";
    sessionStorage.clear();
    sessionStorage.setItem("gradionSession", "session-token");
    vi.restoreAllMocks();
    class MockURL extends NativeURL {
      static createObjectURL = vi.fn(() => "blob:authenticated-image");
      static revokeObjectURL = vi.fn();
    }
    vi.stubGlobal("URL", MockURL);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("validates identity fields before signing in", async () => {
    sessionStorage.clear();
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /continue to your studio/i }));

    expect(await screen.findByText(/name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/enter a valid email/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the project list empty state", async () => {
    mockApi();
    render(<App />);

    expect(await screen.findByRole("heading", { name: /no projects yet/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create.*project/i })).toBeInTheDocument();
  });

  it("renders project status and five-step progress from backend state", async () => {
    mockApi({
      projects: [{ ...project, completed_stage: "PORTRAITS_GENERATED", book_text: undefined }],
    });
    render(<App />);

    const card = await screen.findByRole("button", { name: /river story/i });
    expect(card).toHaveTextContent("In progress");
    expect(card).toHaveTextContent("3 of 5 steps complete");
    expect(card.querySelectorAll('[data-complete="true"]')).toHaveLength(3);
  });

  it("shows the bundled sample-book catalogue on the New Project screen", async () => {
    mockApi();
    render(<App />);
    await screen.findByRole("heading", { name: /no projects yet/i });

    fireEvent.click(screen.getByRole("button", { name: /new project/i }));

    expect(await screen.findByText("Alice’s Adventures in Wonderland")).toBeInTheDocument();
    expect(screen.getByText("Lewis Carroll")).toBeInTheDocument();
    expect(screen.getByText("The Wonderful Wizard of Oz")).toBeInTheDocument();
    expect(screen.getByText("L. Frank Baum")).toBeInTheDocument();
    expect(screen.getByText("The Wind in the Willows")).toBeInTheDocument();
    expect(screen.getByText("Kenneth Grahame")).toBeInTheDocument();
  });

  it("creates a project with the selected sample ID and opens persisted detail", async () => {
    const created = {
      ...project,
      title: "My Oz Project",
      book_text: "The Project Gutenberg eBook of The Wonderful Wizard of Oz",
    };
    let wasCreated = false;
    mockApi({
      handler: (path, init) => {
        if (path === "/api/projects" && init.method === "POST") {
          wasCreated = true;
          const body = JSON.parse(String(init.body));
          expect(body).toEqual({
            title: "My Oz Project",
            sample_book_id: "wizard-of-oz",
          });
          return response(created, 201);
        }
        if (wasCreated && path === `/api/projects/${project.id}`) return response(created);
        return undefined as never;
      },
    });
    render(<App />);
    await screen.findByRole("heading", { name: /no projects yet/i });
    fireEvent.click(screen.getByRole("button", { name: /new project/i }));
    await screen.findByText("The Wonderful Wizard of Oz");

    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: "My Oz Project" },
    });
    fireEvent.click(screen.getByRole("radio", { name: /wonderful wizard of oz/i }));
    fireEvent.click(screen.getByRole("button", { name: /^create project/i }));

    expect(await screen.findByRole("heading", { name: "My Oz Project" })).toBeInTheDocument();
    expect(screen.getByText(/project gutenberg ebook of the wonderful wizard of oz/i)).toBeInTheDocument();
  });

  it("prevents custom text and upload from conflicting with a selected sample", async () => {
    mockApi();
    render(<App />);
    await screen.findByRole("heading", { name: /no projects yet/i });
    fireEvent.click(screen.getByRole("button", { name: /new project/i }));
    await screen.findByText("Alice’s Adventures in Wonderland");

    fireEvent.click(screen.getByRole("radio", { name: /alice.*wonderland/i }));

    expect(screen.getByLabelText(/choose a \.txt file/i)).toBeDisabled();
    expect(screen.getByLabelText(/book text/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /clear sample selection/i })).toBeInTheDocument();
  });

  it("creates a pasted-text project locally and then opens fresh backend detail", async () => {
    const created = { ...project, title: "New River Story", book_text: "A new story beside the river." };
    let wasCreated = false;
    mockApi({
      handler: (path, init) => {
        if (path === "/api/projects" && init.method === "POST") {
          wasCreated = true;
          expect(JSON.parse(String(init.body))).toMatchObject({
            title: "New River Story",
            book_text: "A new story beside the river.",
          });
          expect(JSON.parse(String(init.body))).not.toHaveProperty("sample_book_id");
          return response(created, 201);
        }
        if (wasCreated && path === `/api/projects/${project.id}`) return response(created);
        return undefined as never;
      },
    });
    render(<App />);
    await screen.findByRole("heading", { name: /no projects yet/i });

    fireEvent.click(screen.getByRole("button", { name: /new project/i }));
    await screen.findByRole("heading", { name: /add a book/i });
    fireEvent.change(screen.getByLabelText(/project title/i), { target: { value: "New River Story" } });
    fireEvent.change(screen.getByLabelText(/book text/i), { target: { value: "A new story beside the river." } });
    fireEvent.click(screen.getByRole("button", { name: /^create project/i }));

    expect(await screen.findByRole("heading", { name: "New River Story" })).toBeInTheDocument();
    expect(screen.getByText("A new story beside the river.")).toBeInTheDocument();
  });

  it("creates a project from a .txt file and sends its filename", async () => {
    const created = {
      ...project,
      title: "Uploaded River Story",
      book_text: "Text loaded from the selected file.",
    };
    let wasCreated = false;
    mockApi({
      handler: (path, init) => {
        if (path === "/api/projects" && init.method === "POST") {
          wasCreated = true;
          expect(JSON.parse(String(init.body))).toMatchObject({
            title: "Uploaded River Story",
            book_text: "Text loaded from the selected file.",
            source_filename: "river.txt",
          });
          expect(JSON.parse(String(init.body))).not.toHaveProperty("sample_book_id");
          return response(created, 201);
        }
        if (wasCreated && path === `/api/projects/${project.id}`) return response(created);
        return undefined as never;
      },
    });
    render(<App />);
    await screen.findByRole("heading", { name: /no projects yet/i });

    fireEvent.click(screen.getByRole("button", { name: /new project/i }));
    await screen.findByRole("heading", { name: /add a book/i });
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: "Uploaded River Story" },
    });
    const file = new File(["Text loaded from the selected file."], "river.txt", {
      type: "text/plain",
    });
    Object.defineProperty(file, "text", {
      value: vi.fn().mockResolvedValue("Text loaded from the selected file."),
    });
    fireEvent.change(screen.getByLabelText(/choose a \.txt file/i), {
      target: { files: [file] },
    });

    expect(await screen.findByDisplayValue("Text loaded from the selected file.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^create project/i }));

    expect(await screen.findByRole("heading", { name: "Uploaded River Story" })).toBeInTheDocument();
  });

  it("shows exactly the next legal pipeline action on project detail", async () => {
    await openProject({ ...project, completed_stage: "STYLE_SET", style: "Soft watercolor" });

    expect(screen.getByRole("button", { name: /generate characters/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate style/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate portraits/i })).not.toBeInTheDocument();
  });

  it("names the active step while a project is running", async () => {
    await openProject({
      ...project,
      completed_stage: "CHARACTERS_GENERATED",
      step_state: "RUNNING",
      active_step: "PORTRAITS",
    });

    expect(screen.getByText(/generating character portraits/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^generate/i })).not.toBeInTheDocument();
  });

  it("shows a persisted failure and retries only the failed step", async () => {
    const failed = {
      ...project,
      completed_stage: "STYLE_SET",
      step_state: "FAILED",
      active_step: "CHARACTERS",
      step_error: "Gemini returned malformed character JSON",
    };
    let retried = false;
    const fetchMock = await openProject(failed, (path, init) => {
      if (path.endsWith("/steps/characters") && init.method === "POST") {
        retried = true;
        return response(failed);
      }
      if (retried && path.endsWith(project.id)) return response(failed);
      return undefined as never;
    });

    expect(screen.getByText(/gemini returned malformed character json/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry characters/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/projects/project-1/steps/characters",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("renders compact successful and failed retry attempt history by step", async () => {
    await openProject({
      ...project,
      completed_stage: "CHARACTERS_GENERATED",
      attempts: [
        { id: "style-1", step: "STYLE", attempt_number: 1, started_at: "2026-01-02T14:20:00Z", ended_at: "2026-01-02T14:21:00Z", outcome: "SUCCEEDED", error: null },
        { id: "characters-1", step: "CHARACTERS", attempt_number: 1, started_at: "2026-01-02T14:29:00Z", ended_at: "2026-01-02T14:30:00Z", outcome: "FAILED", error: "Gemini request failed" },
        { id: "characters-2", step: "CHARACTERS", attempt_number: 2, started_at: "2026-01-02T14:31:00Z", ended_at: "2026-01-02T14:32:00Z", outcome: "SUCCEEDED", error: null },
      ],
    });

    expect(screen.getByRole("region", { name: /pipeline attempt history/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Style attempts" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Characters attempts" })).toBeInTheDocument();
    expect(screen.getByText(/attempt 2.*succeeded/i)).toBeInTheDocument();
    expect(screen.getByText(/attempt 1.*failed/i)).toBeInTheDocument();
    expect(screen.getByText("Gemini request failed")).toBeInTheDocument();
  });

  it("shows interrupted history without replacing the recovery control", async () => {
    await openProject({
      ...project,
      step_state: "RUNNING",
      active_step: "STYLE",
      can_recover: true,
      attempts: [
        { id: "style-1", step: "STYLE", attempt_number: 1, started_at: "2026-01-02T14:20:00Z", ended_at: null, outcome: "RUNNING", error: null },
      ],
    });

    expect(screen.getByText(/attempt 1.*running/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recover style/i })).toBeInTheDocument();
  });

  it("offers backend-authorized recovery for an interrupted run", async () => {
    await openProject({
      ...project,
      step_state: "RUNNING",
      active_step: "STYLE",
      can_recover: true,
    });

    expect(screen.getByText(/previous backend execution was interrupted/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recover style/i })).toBeInTheDocument();
  });

  it("refreshes project detail after successful recovery", async () => {
    const interrupted = {
      ...project,
      step_state: "RUNNING",
      active_step: "STYLE",
      can_recover: true,
    };
    const recovered = {
      ...project,
      step_state: "FAILED",
      active_step: "STYLE",
      step_error: "Pipeline execution was interrupted by a backend restart",
    };
    let didRecover = false;
    await openProject(interrupted, (path, init) => {
      if (path.endsWith("/recover") && init.method === "POST") {
        didRecover = true;
        return response(recovered);
      }
      if (didRecover && path.endsWith(project.id)) return response(recovered);
      return undefined as never;
    });

    fireEvent.click(screen.getByRole("button", { name: /recover style/i }));

    expect(await screen.findByRole("button", { name: /retry style/i })).toBeInTheDocument();
    expect(screen.getByText(/backend restart/i)).toBeInTheDocument();
  });

  it("renders generated character cards and prompts", async () => {
    await openProject({
      ...project,
      completed_stage: "CHARACTERS_GENERATED",
      style: "Warm ink and watercolor",
      characters: [
        { id: "mole", name: "Mole", prompt: "A gentle adult mole in a waistcoat", image_state: "PENDING", image_error: null, portrait_url: null },
        { id: "rat", name: "Water Rat", prompt: "A confident adult water vole by the river", image_state: "PENDING", image_error: null, portrait_url: null },
      ],
    });

    expect(screen.getByRole("heading", { name: "Mole" })).toBeInTheDocument();
    expect(screen.getByText(/gentle adult mole/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Water Rat" })).toBeInTheDocument();
  });

  it("shows portrait pending, generating, ready, and failed item states", async () => {
    const imageResponse = Promise.resolve(
      new Response(new Blob(["portrait"], { type: "image/png" }), { status: 200 }),
    );
    await openProject(
      {
        ...project,
        completed_stage: "CHARACTERS_GENERATED",
        step_state: "RUNNING",
        active_step: "PORTRAITS",
        characters: [
          { id: "mole", name: "Mole", prompt: "Mole prompt", image_state: "READY", image_error: null, portrait_url: "/api/projects/project-1/characters/mole/portrait" },
          { id: "rat", name: "Water Rat", prompt: "Rat prompt", image_state: "GENERATING", image_error: null, portrait_url: null },
          { id: "badger", name: "Badger", prompt: "Badger prompt", image_state: "FAILED", image_error: "Portrait request failed", portrait_url: null },
          { id: "toad", name: "Toad", prompt: "Toad prompt", image_state: "PENDING", image_error: null, portrait_url: null },
        ],
      },
      (path) => (path.endsWith("/portrait") ? imageResponse : (undefined as never)),
    );

    expect(await screen.findByRole("img", { name: /portrait of mole/i })).toHaveAttribute("src", "blob:authenticated-image");
    expect(screen.getByText(/generating portrait for water rat/i)).toBeInTheDocument();
    expect(screen.getByText(/portrait request failed/i)).toBeInTheDocument();
    expect(screen.getByText(/portrait not generated yet/i)).toBeInTheDocument();
  });

  it("renders a generated chapter and authenticated final illustration", async () => {
    const done = {
      ...project,
      completed_stage: "DONE",
      style: "Watercolor",
      chapters: [
        {
          id: "chapter-1",
          name: "Spring Cleaning",
          prompt: "Mole emerges into a sunlit meadow",
          image_state: "READY",
          image_error: null,
          illustration_url: "/api/projects/project-1/chapters/chapter-1/illustration",
        },
      ],
    };
    await openProject(done, (path) =>
      path.endsWith("/illustration")
        ? Promise.resolve(new Response(new Blob(["scene"], { type: "image/png" }), { status: 200 }))
        : (undefined as never),
    );

    expect(screen.getByRole("heading", { name: "Spring Cleaning" })).toBeInTheDocument();
    expect(screen.getByText(/mole emerges into a sunlit meadow/i)).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /illustration for spring cleaning/i })).toHaveAttribute("src", "blob:authenticated-image");
    expect(screen.queryByRole("button", {
      name: /generate (style|characters|portraits|chapters|illustrations)/i,
    })).not.toBeInTheDocument();
  });

  it("polls project detail while backend state is RUNNING", async () => {
    let poll: (() => void) | undefined;
    const nativeSetInterval = window.setInterval.bind(window);
    vi.spyOn(window, "setInterval").mockImplementation((callback, delay, ...args) => {
      if (delay === 2000) {
        poll = callback as () => void;
        return 42;
      }
      return nativeSetInterval(callback, delay, ...args);
    });
    const running = { ...project, step_state: "RUNNING", active_step: "CHAPTERS" };
    const fetchMock = await openProject(running);
    await waitFor(() => expect(poll).toBeDefined());
    const detailCallsBefore = fetchMock.mock.calls.filter(([url]) => String(url).endsWith(project.id)).length;

    await act(async () => {
      poll?.();
    });

    await waitFor(() => {
      const detailCallsAfter = fetchMock.mock.calls.filter(([url]) => String(url).endsWith(project.id)).length;
      expect(detailCallsAfter).toBeGreaterThan(detailCallsBefore);
    });
  });

  it.each(["IDLE", "FAILED"])("stops polling once project state becomes %s", async (settledState) => {
    let poll: (() => void) | undefined;
    let pollId = 83;
    const nativeSetInterval = window.setInterval.bind(window);
    const clearIntervalSpy = vi.spyOn(window, "clearInterval");
    vi.spyOn(window, "setInterval").mockImplementation((callback, delay, ...args) => {
      if (delay === 2000) {
        poll = callback as () => void;
        pollId += 1;
        return pollId;
      }
      return nativeSetInterval(callback, delay, ...args);
    });
    let detailCalls = 0;
    const running = { ...project, step_state: "RUNNING", active_step: "CHAPTERS" };
    const settled = {
      ...running,
      step_state: settledState,
      step_error: settledState === "FAILED" ? "Chapter generation failed" : null,
    };
    await openProject(running, (path) => {
      if (path.endsWith(project.id)) {
        detailCalls += 1;
        return response(detailCalls === 1 ? running : settled);
      }
      return undefined as never;
    });
    await waitFor(() => expect(poll).toBeDefined());
    const currentPollId = pollId;

    await act(async () => {
      poll?.();
    });

    await waitFor(() => expect(detailCalls).toBe(2));
    await waitFor(() => expect(clearIntervalSpy).toHaveBeenCalledWith(currentPollId));
  });

  it("offers explicit narration generation only after the required pipeline is done", async () => {
    const done = {
      ...project,
      completed_stage: "DONE",
      narration: {
        state: "IDLE",
        started_at: null,
        error: null,
        can_recover: false,
        audio_url: null,
      },
    };
    let started = false;
    const fetchMock = await openProject(done, (path, init) => {
      if (path.endsWith("/narration") && init.method === "POST") {
        started = true;
        return response({
          ...done,
          narration: { ...done.narration, state: "RUNNING" },
        });
      }
      if (started && path.endsWith(project.id)) return response(done);
      return undefined as never;
    });

    fireEvent.click(screen.getByRole("button", { name: /generate narration/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/projects/project-1/narration",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("shows named narration progress and disables duplicate generation", async () => {
    await openProject({
      ...project,
      completed_stage: "DONE",
      narration: {
        state: "RUNNING",
        started_at: "2026-01-02T15:00:00Z",
        error: null,
        can_recover: false,
        audio_url: null,
      },
    });

    expect(screen.getByText(/generating narration/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate narration/i })).not.toBeInTheDocument();
  });

  it("shows narration failure and manual retry without hiding required outputs", async () => {
    const failed = {
      ...project,
      completed_stage: "DONE",
      style: "Watercolor",
      chapters: [
        {
          id: "chapter-1",
          name: "Spring Cleaning",
          prompt: "Mole emerges into a sunlit meadow",
          image_state: "READY",
          image_error: null,
          illustration_url: null,
        },
      ],
      narration: {
        state: "FAILED",
        started_at: "2026-01-02T15:00:00Z",
        error: "Gemini narration request failed",
        can_recover: false,
        audio_url: null,
      },
    };
    await openProject(failed);

    expect(screen.getByText(/gemini narration request failed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry narration/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Spring Cleaning" })).toBeInTheDocument();
    expect(screen.getByText(/mole emerges into a sunlit meadow/i)).toBeInTheDocument();
  });

  it("renders completed narration in an accessible audio controls player", async () => {
    const done = {
      ...project,
      completed_stage: "DONE",
      narration: {
        state: "COMPLETED",
        started_at: "2026-01-02T15:00:00Z",
        error: null,
        can_recover: false,
        audio_url: "/api/projects/project-1/narration/audio",
      },
    };
    await openProject(done, (path) =>
      path.endsWith("/narration/audio")
        ? Promise.resolve(
            new Response(new Blob(["audio"], { type: "audio/wav" }), { status: 200 }),
          )
        : (undefined as never),
    );

    await waitFor(() =>
      expect(document.querySelector("audio[controls]")).toBeInTheDocument(),
    );
    const player = document.querySelector("audio[controls]");
    expect(player).toHaveAttribute("src", "blob:authenticated-image");
    expect(player).toHaveAccessibleName(/chapter opening narration/i);
  });

  it("offers explicit recovery for narration interrupted by a backend restart", async () => {
    await openProject({
      ...project,
      completed_stage: "DONE",
      narration: {
        state: "RUNNING",
        started_at: "2026-01-02T15:00:00Z",
        error: null,
        can_recover: true,
        audio_url: null,
      },
    });

    expect(screen.getByText(/narration.*interrupted|interrupted.*narration/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recover narration/i })).toBeInTheDocument();
  });
});
