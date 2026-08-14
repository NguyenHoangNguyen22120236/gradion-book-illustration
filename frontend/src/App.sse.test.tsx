import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const streamMock = vi.hoisted(() => vi.fn());

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  openProjectStateStream: streamMock,
}));

import { App } from "./App";
import type { Project } from "./types";


type StreamHandlers = {
  onProjectState: (project: Project) => void;
  onDisconnect: (error: Error) => void;
};

const user = {
  id: "user-1",
  name: "Mira Hassan",
  email: "mira@example.com",
  created_at: "2026-01-01T00:00:00Z",
};

const idleNarration = {
  state: "IDLE" as const,
  started_at: null,
  error: null,
  can_recover: false,
  audio_url: null,
};

const project: Project = {
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
  narration: idleNarration,
  book_text: "Once beside the river, Mole opened the door to spring.",
};

const response = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );

function mockApi(detail: Project = project) {
  let currentDetail = detail;
  let commandResponse: Project | null = null;
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const path = new URL(String(input), "http://test.local").pathname;
    if (path === "/api/session") return response(user);
    if (path === "/api/projects") {
      const listItem = { ...currentDetail };
      delete listItem.book_text;
      return response([listItem]);
    }
    if (init?.method === "POST" && commandResponse) return response(commandResponse);
    if (path === `/api/projects/${project.id}`) return response(currentDetail);
    throw new Error(`Unexpected request: ${path}`);
  });
  return {
    fetchMock,
    setDetail(next: Project) {
      currentDetail = next;
    },
    setCommandResponse(next: Project) {
      commandResponse = next;
    },
  };
}

async function openProject(detail: Project = project) {
  const api = mockApi(detail);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /river story/i }));
  await screen.findByRole("heading", { name: "River Story" });
  await waitFor(() => expect(streamMock).toHaveBeenCalled());
  const handlers = streamMock.mock.calls.at(-1)?.[2] as StreamHandlers;
  return { ...api, handlers };
}

describe("project detail SSE updates", () => {
  beforeEach(() => {
    window.location.hash = "";
    sessionStorage.clear();
    sessionStorage.setItem("gradionSession", "session-token");
    vi.restoreAllMocks();
    streamMock.mockReset();
    streamMock.mockReturnValue(() => undefined);
  });

  it("opens an independent authenticated project-state stream", async () => {
    await openProject();

    expect(streamMock).toHaveBeenCalledWith(
      "project-1",
      "session-token",
      expect.objectContaining({
        onProjectState: expect.any(Function),
        onDisconnect: expect.any(Function),
      }),
    );
  });

  it("replaces rendered state with an incoming authoritative snapshot", async () => {
    const { handlers } = await openProject();

    act(() => {
      handlers.onProjectState({
        ...project,
        completed_stage: "STYLE_SET",
        style: "Live ink and watercolor",
      });
    });

    expect(screen.getByText("Live ink and watercolor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate characters/i })).toBeInTheDocument();
  });

  it("applies a successful step POST response without a follow-up project GET", async () => {
    const styleReady: Project = {
      ...project,
      completed_stage: "STYLE_SET",
      style: "Live ink and watercolor",
    };
    const charactersReady: Project = {
      ...styleReady,
      completed_stage: "CHARACTERS_GENERATED",
      characters: [
        {
          id: "mole",
          name: "Mole",
          prompt: "A gentle adult mole in a waistcoat",
          image_state: "PENDING",
          image_error: null,
          portrait_url: null,
        },
      ],
    };
    const { fetchMock, setCommandResponse } = await openProject(styleReady);
    setCommandResponse(charactersReady);
    const detailGetsBefore = fetchMock.mock.calls.filter(([input, init]) =>
      !init?.method && String(input).endsWith("/projects/project-1"),
    ).length;

    fireEvent.click(screen.getByRole("button", { name: /generate characters/i }));

    expect(await screen.findByRole("heading", { name: "Mole" })).toBeInTheDocument();
    expect(screen.getByText("Once beside the river, Mole opened the door to spring.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([input, init]) =>
      init?.method === "POST" && String(input).endsWith("/steps/characters"),
    )).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([input, init]) =>
      !init?.method && String(input).endsWith("/projects/project-1"),
    )).toHaveLength(detailGetsBefore);
  });

  it("renders pushed per-item portrait progress without a polling interval", async () => {
    const running: Project = {
      ...project,
      completed_stage: "CHARACTERS_GENERATED",
      step_state: "RUNNING",
      active_step: "PORTRAITS",
      characters: [
        {
          id: "mole",
          name: "Mole",
          prompt: "Mole portrait",
          image_state: "PENDING",
          image_error: null,
          portrait_url: null,
        },
      ],
    };
    const { handlers } = await openProject(running);

    act(() => {
      handlers.onProjectState({
        ...running,
        characters: [{ ...running.characters[0], image_state: "GENERATING" }],
      });
    });

    expect(screen.getByText(/generating portrait for mole/i)).toBeInTheDocument();
  });

  it("renders the existing failure and retry UI from a pushed snapshot", async () => {
    const running: Project = {
      ...project,
      step_state: "RUNNING",
      active_step: "STYLE",
    };
    const { handlers } = await openProject(running);

    act(() => {
      handlers.onProjectState({
        ...running,
        step_state: "FAILED",
        step_error: "Pushed provider failure",
      });
    });

    expect(screen.getByText("Pushed provider failure")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry style/i })).toBeInTheDocument();
  });

  it("renders existing interrupted recovery UI from a pushed snapshot", async () => {
    const { handlers } = await openProject();

    act(() => {
      handlers.onProjectState({
        ...project,
        step_state: "RUNNING",
        active_step: "STYLE",
        can_recover: true,
      });
    });

    expect(screen.getByText(/previous backend execution was interrupted/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recover style/i })).toBeInTheDocument();
  });

  it("lets two mounted views subscribe independently without triggering execution", async () => {
    mockApi();
    const first = render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /river story/i }));
    await screen.findByRole("heading", { name: "River Story" });

    const second = render(<App />);
    await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(2));
    const pushed = { ...project, completed_stage: "STYLE_SET" as const, style: "Shared pushed style" };

    act(() => {
      for (const call of streamMock.mock.calls) {
        (call[2] as StreamHandlers).onProjectState(pushed);
      }
    });

    expect(screen.getAllByText("Shared pushed style")).toHaveLength(2);
    expect(
      vi.mocked(globalThis.fetch).mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);
    first.unmount();
    second.unmount();
  });

  it("performs one safe project GET refresh when the stream disconnects", async () => {
    const { handlers, fetchMock } = await openProject();
    const detailCallsBefore = fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/projects/project-1"),
    ).length;

    await act(async () => {
      handlers.onDisconnect(new Error("stream disconnected"));
    });

    await waitFor(() => {
      const detailCallsAfter = fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/projects/project-1"),
      ).length;
      expect(detailCallsAfter).toBe(detailCallsBefore + 1);
    });
  });

  it("accepts a fresh authoritative snapshot on a new/reconnected stream", async () => {
    const { handlers } = await openProject();
    const latest = {
      ...project,
      completed_stage: "STYLE_SET" as const,
      style: "SQLite snapshot after reconnect",
    };

    act(() => handlers.onProjectState(latest));

    expect(screen.getByText("SQLite snapshot after reconnect")).toBeInTheDocument();
  });

  it("does not install the old 2-second periodic project polling interval", async () => {
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    await openProject({ ...project, step_state: "RUNNING", active_step: "CHAPTERS" });

    expect(
      setIntervalSpy.mock.calls.some(([, delay]) => delay === 2000),
    ).toBe(false);
  });
});
