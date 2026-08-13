import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .database import Database
from .pipeline import PipelineExecutionError, PipelineStep


STYLE_INSTRUCTION = """You are preparing a consistent illustration system for the supplied book.
Read the uploaded book and establish one concise visual art style that suits its setting,
period, mood, and intended storybook illustrations. Return only the chosen style description.
The style will be reused when designing characters and later scene illustrations."""

CHARACTER_INSTRUCTION = """Using the uploaded book and established art style from this
conversation, identify the main adult characters only. Exclude children, minor characters,
crowds, and unnamed figures. Return at most two characters. For each, provide its name,
a detailed standalone image-generation prompt consistent with the established style, and
is_adult=true. The prompt must describe stable visual identity, clothing, physical features,
and suitable portrait composition without referring to unavailable text."""

CHARACTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Character name"},
                    "image_prompt": {
                        "type": "string",
                        "description": "Detailed portrait image-generation prompt",
                    },
                    "is_adult": {
                        "type": "boolean",
                        "description": "True only when the character is an adult",
                    },
                },
                "required": ["name", "image_prompt", "is_adult"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["characters"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    text_model: str = "gemini-3.5-flash"
    timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls) -> "GeminiSettings | None":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            return None
        model = os.getenv("GEMINI_TEXT_MODEL", cls.text_model).strip()
        timeout_raw = os.getenv("GEMINI_OPERATION_TIMEOUT_SECONDS", "180")
        try:
            timeout = float(timeout_raw)
        except ValueError as error:
            raise RuntimeError(
                "GEMINI_OPERATION_TIMEOUT_SECONDS must be a number"
            ) from error
        if not model or timeout <= 0:
            raise RuntimeError("Gemini model and timeout configuration must be valid")
        return cls(api_key=api_key, text_model=model, timeout_seconds=timeout)


class GeminiInteraction(Protocol):
    id: str
    output_text: str


class GeminiClient(Protocol):
    def upload_book(self, book_path: Path) -> str: ...

    def create_style_interaction(
        self, file_uri: str, requested_style: str | None
    ) -> GeminiInteraction: ...

    def create_characters_interaction(
        self, previous_interaction_id: str
    ) -> GeminiInteraction: ...


class GoogleGenAIClient:
    """Small wrapper around the official SDK's File and Interactions APIs."""

    def __init__(
        self,
        *,
        settings: GeminiSettings | None = None,
        sdk_client: Any | None = None,
        text_model: str | None = None,
    ) -> None:
        if sdk_client is None:
            if settings is None:
                raise ValueError("Gemini settings are required")
            from google import genai

            sdk_client = genai.Client(
                api_key=settings.api_key,
                http_options={
                    "timeout": int(settings.timeout_seconds * 1000),
                    "retry_options": {"attempts": 1},
                },
            )
        self._client = sdk_client
        self._text_model = text_model or (settings.text_model if settings else None)
        if not self._text_model:
            raise ValueError("A Gemini text model is required")

    def upload_book(self, book_path: Path) -> str:
        uploaded = self._client.files.upload(
            file=str(book_path),
            config={"mime_type": "text/plain", "display_name": book_path.name},
        )
        uri = getattr(uploaded, "uri", None)
        if not isinstance(uri, str) or not uri.strip():
            raise RuntimeError("Gemini File API returned no file URI")
        return uri

    def create_style_interaction(
        self, file_uri: str, requested_style: str | None
    ) -> GeminiInteraction:
        instruction = STYLE_INSTRUCTION
        if requested_style:
            instruction += (
                "\nUse this exact user-selected style as the established style: "
                f"{requested_style}\nReturn that style description only."
            )
        return self._client.interactions.create(
            model=self._text_model,
            input=[
                {"type": "text", "text": instruction},
                {
                    "type": "document",
                    "uri": file_uri,
                    "mime_type": "text/plain",
                },
            ],
        )

    def create_characters_interaction(
        self, previous_interaction_id: str
    ) -> GeminiInteraction:
        return self._client.interactions.create(
            model=self._text_model,
            previous_interaction_id=previous_interaction_id,
            input=CHARACTER_INSTRUCTION,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": CHARACTERS_SCHEMA,
            },
        )


class CharacterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    image_prompt: str
    is_adult: bool

    @field_validator("name", "image_prompt")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class CharacterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    characters: list[CharacterCandidate] = Field(min_length=1)


class GeminiPipelineExecutor:
    """Real Stage 5 executor; later pipeline steps deliberately remain local fakes."""

    def __init__(
        self, client: GeminiClient, database: Database, data_dir: Path
    ) -> None:
        self.client = client
        self.database = database
        self.data_dir = data_dir

    def execute(self, step: str, project: dict[str, Any]) -> dict[str, Any]:
        if step not in (PipelineStep.STYLE, PipelineStep.CHARACTERS):
            return {}
        try:
            if step == PipelineStep.STYLE:
                return self._execute_style(project)
            return self._execute_characters(project)
        except PipelineExecutionError:
            raise
        except Exception as error:
            raise PipelineExecutionError(f"Gemini {step.lower()} failed: {error}") from error

    def _execute_style(self, project: dict[str, Any]) -> dict[str, Any]:
        file_uri = project.get("gemini_file_uri")
        if not file_uri:
            book_path = self.data_dir / project["book_path"]
            file_uri = self.client.upload_book(book_path)
            with self.database.connect() as connection:
                saved = connection.execute(
                    """UPDATE projects SET gemini_file_uri = ?
                       WHERE id = ? AND step_state = 'RUNNING'
                         AND active_step = 'STYLE' AND execution_owner = ?
                         AND gemini_file_uri IS NULL""",
                    (file_uri, project["id"], project["execution_owner"]),
                )
            if saved.rowcount != 1:
                raise PipelineExecutionError(
                    "Could not persist the uploaded Gemini file reference"
                )

        requested_style = project.get("requested_style")
        interaction = self.client.create_style_interaction(file_uri, requested_style)
        interaction_id = self._require_interaction_id(interaction)
        if requested_style:
            style = requested_style
        else:
            style = self._require_output_text(interaction, "style")
        return {"style": style, "latest_interaction_id": interaction_id}

    def _execute_characters(self, project: dict[str, Any]) -> dict[str, Any]:
        previous_id = project.get("latest_interaction_id")
        if not isinstance(previous_id, str) or not previous_id.strip():
            raise PipelineExecutionError(
                "Characters require the completed Style interaction"
            )
        interaction = self.client.create_characters_interaction(previous_id)
        interaction_id = self._require_interaction_id(interaction)
        output = self._require_output_text(interaction, "characters")
        try:
            parsed = CharacterResponse.model_validate_json(output)
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            raise PipelineExecutionError(
                "Gemini returned invalid structured character output"
            ) from error

        adults = [character for character in parsed.characters if character.is_adult][:2]
        if not adults:
            raise PipelineExecutionError(
                "Gemini returned no valid adult characters"
            )
        return {
            "latest_interaction_id": interaction_id,
            "characters": [
                {"name": character.name, "prompt": character.image_prompt}
                for character in adults
            ],
        }

    @staticmethod
    def _require_interaction_id(interaction: GeminiInteraction) -> str:
        interaction_id = getattr(interaction, "id", None)
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise PipelineExecutionError("Gemini returned no interaction ID")
        return interaction_id

    @staticmethod
    def _require_output_text(interaction: GeminiInteraction, label: str) -> str:
        output = getattr(interaction, "output_text", None)
        if not isinstance(output, str) or not output.strip():
            raise PipelineExecutionError(f"Gemini returned no {label} output")
        return output.strip()
