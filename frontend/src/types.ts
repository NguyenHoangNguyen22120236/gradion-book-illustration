export type CompletedStage =
  | "CREATED"
  | "STYLE_SET"
  | "CHARACTERS_GENERATED"
  | "PORTRAITS_GENERATED"
  | "CHAPTERS_GENERATED"
  | "DONE";

export type PipelineStep =
  | "STYLE"
  | "CHARACTERS"
  | "PORTRAITS"
  | "CHAPTERS"
  | "ILLUSTRATIONS";

export type StepState = "IDLE" | "RUNNING" | "FAILED";
export type ImageState = "PENDING" | "GENERATING" | "READY" | "FAILED";
export type AttemptOutcome = "RUNNING" | "SUCCEEDED" | "FAILED" | "INTERRUPTED";
export type NarrationState = "IDLE" | "RUNNING" | "FAILED" | "COMPLETED";

export type Narration = {
  state: NarrationState;
  started_at: string | null;
  error: string | null;
  can_recover: boolean;
  audio_url: string | null;
};

export type PipelineAttempt = {
  id: string;
  step: PipelineStep;
  attempt_number: number;
  started_at: string;
  ended_at: string | null;
  outcome: AttemptOutcome;
  error: string | null;
};

export type User = {
  id: string;
  name: string;
  email: string;
  created_at: string;
};

export type SampleBook = {
  id: string;
  title: string;
  author: string;
};

export type Character = {
  id: string;
  name: string;
  prompt: string;
  image_state: ImageState;
  image_error: string | null;
  portrait_url: string | null;
};

export type Chapter = {
  id: string;
  name: string;
  prompt: string;
  image_state: ImageState;
  image_error: string | null;
  illustration_url: string | null;
};

export type Project = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  completed_stage: CompletedStage;
  step_state: StepState;
  active_step: PipelineStep | null;
  step_started_at: string | null;
  step_error: string | null;
  can_recover: boolean;
  style: string | null;
  characters: Character[];
  chapters: Chapter[];
  attempts: PipelineAttempt[];
  narration: Narration;
  book_text?: string;
};
