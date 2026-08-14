import base64
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

CHAPTER_INSTRUCTION = """Using the uploaded book, established art style, and adult
characters already identified in this conversation, select one chapter scene to illustrate.
Return at most one chapter. Provide its chapter or scene name and a detailed standalone
image-generation prompt. The prompt must describe the setting, action, composition,
lighting, and mood, and explicitly name the relevant established characters so their saved
portraits can be used as visual references. Do not refer to unavailable text or context."""

CHARACTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "minItems": 1,
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

CHAPTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Chapter or scene name"},
                    "image_prompt": {
                        "type": "string",
                        "description": (
                            "Detailed scene prompt naming relevant established characters"
                        ),
                    },
                },
                "required": ["name", "image_prompt"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["chapters"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GeminiSettings:
    api_key: str
    text_model: str = "gemini-3.5-flash"
    image_model: str = "gemini-3.1-flash-image"
    timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls) -> "GeminiSettings | None":
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            return None
        model = os.getenv("GEMINI_TEXT_MODEL", cls.text_model).strip()
        image_model = os.getenv("GEMINI_IMAGE_MODEL", cls.image_model).strip()
        timeout_raw = os.getenv("GEMINI_OPERATION_TIMEOUT_SECONDS", "180")
        try:
            timeout = float(timeout_raw)
        except ValueError as error:
            raise RuntimeError(
                "GEMINI_OPERATION_TIMEOUT_SECONDS must be a number"
            ) from error
        if not model or not image_model or timeout <= 0:
            raise RuntimeError("Gemini models and timeout configuration must be valid")
        return cls(
            api_key=api_key,
            text_model=model,
            image_model=image_model,
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime_type: str


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

    def generate_portrait(self, prompt: str) -> GeneratedImage: ...

    def create_chapters_interaction(
        self, previous_interaction_id: str
    ) -> GeminiInteraction: ...

    def generate_illustration(
        self, prompt: str, portrait_references: list[GeneratedImage]
    ) -> GeneratedImage: ...


class GoogleGenAIClient:
    """Small wrapper around the official SDK's File and Interactions APIs."""

    def __init__(
        self,
        *,
        settings: GeminiSettings | None = None,
        sdk_client: Any | None = None,
        text_model: str | None = None,
        image_model: str | None = None,
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
        self._image_model = image_model or (
            settings.image_model if settings else GeminiSettings.image_model
        )
        if not self._text_model:
            raise ValueError("A Gemini text model is required")
        if not self._image_model:
            raise ValueError("A Gemini image model is required")

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

    def generate_portrait(self, prompt: str) -> GeneratedImage:
        interaction = self._client.interactions.create(
            model=self._image_model,
            input=prompt,
        )
        return self._generated_image(interaction, "portrait")

    def create_chapters_interaction(
        self, previous_interaction_id: str
    ) -> GeminiInteraction:
        return self._client.interactions.create(
            model=self._text_model,
            previous_interaction_id=previous_interaction_id,
            input=CHAPTER_INSTRUCTION,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": CHAPTERS_SCHEMA,
            },
        )

    def generate_illustration(
        self, prompt: str, portrait_references: list[GeneratedImage]
    ) -> GeneratedImage:
        image_inputs = [
            {
                "type": "image",
                "data": base64.b64encode(reference.data).decode("ascii"),
                "mime_type": reference.mime_type,
            }
            for reference in portrait_references
        ]
        interaction = self._client.interactions.create(
            model=self._image_model,
            input=[{"type": "text", "text": prompt}, *image_inputs],
        )
        return self._generated_image(interaction, "illustration")

    @staticmethod
    def _generated_image(interaction: Any, label: str) -> GeneratedImage:
        output_image = getattr(interaction, "output_image", None)
        encoded_data = getattr(output_image, "data", None)
        mime_type = getattr(output_image, "mime_type", None)
        if not isinstance(encoded_data, (str, bytes)) or not encoded_data:
            raise RuntimeError(f"Gemini returned no {label} image data")
        if not isinstance(mime_type, str) or not mime_type.strip():
            raise RuntimeError(f"Gemini returned no {label} image type")
        try:
            image_data = base64.b64decode(encoded_data, validate=True)
        except (ValueError, TypeError) as error:
            raise RuntimeError(f"Gemini returned invalid {label} image data") from error
        if not image_data:
            raise RuntimeError(f"Gemini returned empty {label} image data")
        return GeneratedImage(data=image_data, mime_type=mime_type.strip().lower())


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


class ChapterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str
    image_prompt: str

    @field_validator("name", "image_prompt")
    @classmethod
    def require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ChapterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chapters: list[ChapterCandidate] = Field(min_length=1)


class GeminiPipelineExecutor:
    """Real executor for the five-step book illustration pipeline."""

    def __init__(
        self, client: GeminiClient, database: Database, data_dir: Path
    ) -> None:
        self.client = client
        self.database = database
        self.data_dir = data_dir

    def execute(self, step: str, project: dict[str, Any]) -> dict[str, Any]:
        if step not in (
            PipelineStep.STYLE,
            PipelineStep.CHARACTERS,
            PipelineStep.PORTRAITS,
            PipelineStep.CHAPTERS,
            PipelineStep.ILLUSTRATIONS,
        ):
            return {}
        try:
            if step == PipelineStep.STYLE:
                return self._execute_style(project)
            if step == PipelineStep.CHARACTERS:
                return self._execute_characters(project)
            if step == PipelineStep.PORTRAITS:
                return self._execute_portraits(project)
            if step == PipelineStep.CHAPTERS:
                return self._execute_chapters(project)
            return self._execute_illustrations(project)
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

    def _execute_portraits(self, project: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            characters = connection.execute(
                """SELECT id, name, prompt, portrait_path, image_state
                   FROM characters WHERE project_id = ? ORDER BY sort_order""",
                (project["id"],),
            ).fetchall()
        if not characters:
            raise PipelineExecutionError("Portraits require persisted characters")

        for character in characters:
            if self._portrait_is_available(character):
                continue
            self._mark_portrait_generating(character["id"])
            try:
                prompt = self._portrait_prompt(
                    character["prompt"], project.get("style")
                )
                image = self.client.generate_portrait(prompt)
                relative_path = self._save_portrait(
                    project["id"], character["id"], image
                )
                with self.database.connect() as connection:
                    connection.execute(
                        """UPDATE characters
                           SET portrait_path = ?, image_state = 'READY', image_error = NULL
                           WHERE id = ? AND project_id = ?""",
                        (relative_path, character["id"], project["id"]),
                    )
            except Exception as error:
                message = f"Portrait generation failed for {character['name']}"
                with self.database.connect() as connection:
                    connection.execute(
                        """UPDATE characters
                           SET image_state = 'FAILED', image_error = ?
                           WHERE id = ? AND project_id = ?""",
                        (message, character["id"], project["id"]),
                    )
                raise PipelineExecutionError(message) from error
        return {}

    def _execute_chapters(self, project: dict[str, Any]) -> dict[str, Any]:
        previous_id = project.get("latest_interaction_id")
        if not isinstance(previous_id, str) or not previous_id.strip():
            raise PipelineExecutionError(
                "Chapters require the completed Characters interaction"
            )
        interaction = self.client.create_chapters_interaction(previous_id)
        interaction_id = self._require_interaction_id(interaction)
        output = self._require_output_text(interaction, "chapters")
        try:
            parsed = ChapterResponse.model_validate_json(output)
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            raise PipelineExecutionError(
                "Gemini returned invalid structured chapter output"
            ) from error

        chapter = parsed.chapters[0]
        return {
            "latest_interaction_id": interaction_id,
            "chapters": [{"name": chapter.name, "prompt": chapter.image_prompt}],
        }

    def _execute_illustrations(self, project: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            chapter = connection.execute(
                """SELECT id, name, prompt FROM chapters
                   WHERE project_id = ? ORDER BY sort_order LIMIT 1""",
                (project["id"],),
            ).fetchone()
            characters = connection.execute(
                """SELECT name, portrait_path, image_state FROM characters
                   WHERE project_id = ? ORDER BY sort_order""",
                (project["id"],),
            ).fetchall()
        if chapter is None:
            raise PipelineExecutionError("Illustrations require a persisted chapter")
        if not characters:
            raise PipelineExecutionError("Illustrations require persisted characters")

        references: list[GeneratedImage] = []
        for character in characters:
            if character["image_state"] != "READY" or not character["portrait_path"]:
                raise PipelineExecutionError(
                    "Illustrations require all character portraits"
                )
            portrait_path = self._resolve_data_path(character["portrait_path"])
            if not portrait_path.is_file():
                raise PipelineExecutionError(
                    f"Stored portrait is missing for {character['name']}"
                )
            references.append(
                GeneratedImage(
                    data=portrait_path.read_bytes(),
                    mime_type=self._image_mime_type(portrait_path),
                )
            )

        self._mark_illustration_generating(chapter["id"])
        try:
            prompt = self._illustration_prompt(
                chapter["prompt"], project.get("style")
            )
            image = self.client.generate_illustration(prompt, references)
            relative_path = self._save_illustration(
                project["id"], chapter["id"], image
            )
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE chapters
                       SET illustration_path = ?, image_state = 'READY', image_error = NULL
                       WHERE id = ? AND project_id = ?""",
                    (relative_path, chapter["id"], project["id"]),
                )
        except Exception as error:
            message = f"Illustration generation failed for {chapter['name']}"
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE chapters SET image_state = 'FAILED', image_error = ?
                       WHERE id = ? AND project_id = ?""",
                    (message, chapter["id"], project["id"]),
                )
            raise PipelineExecutionError(message) from error
        return {}

    def _portrait_is_available(self, character: Any) -> bool:
        portrait_path = character["portrait_path"]
        if character["image_state"] != "READY" or not portrait_path:
            return False
        try:
            path = self._resolve_data_path(portrait_path)
        except ValueError:
            return False
        return path.is_file()

    def _mark_portrait_generating(self, character_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE characters
                   SET image_state = 'GENERATING', image_error = NULL
                   WHERE id = ?""",
                (character_id,),
            )

    def _save_portrait(
        self, project_id: str, character_id: str, image: GeneratedImage
    ) -> str:
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        extension = extensions.get(image.mime_type.lower())
        if extension is None:
            raise ValueError("Gemini returned an unsupported portrait image type")
        if not image.data:
            raise ValueError("Gemini returned empty portrait image data")

        relative_path = (
            Path("images")
            / project_id
            / "characters"
            / f"{character_id}{extension}"
        )
        target = self._resolve_data_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        try:
            temporary.write_bytes(image.data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return relative_path.as_posix()

    def _mark_illustration_generating(self, chapter_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE chapters
                   SET image_state = 'GENERATING', image_error = NULL
                   WHERE id = ?""",
                (chapter_id,),
            )

    def _save_illustration(
        self, project_id: str, chapter_id: str, image: GeneratedImage
    ) -> str:
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }
        extension = extensions.get(image.mime_type.lower())
        if extension is None:
            raise ValueError("Gemini returned an unsupported illustration image type")
        if not image.data:
            raise ValueError("Gemini returned empty illustration image data")
        relative_path = (
            Path("images") / project_id / "chapters" / f"{chapter_id}{extension}"
        )
        target = self._resolve_data_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        try:
            temporary.write_bytes(image.data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return relative_path.as_posix()

    def _resolve_data_path(self, relative_path: str | Path) -> Path:
        root = self.data_dir.resolve()
        resolved = (root / relative_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError("Portrait path is outside the data directory") from error
        return resolved

    @staticmethod
    def _image_mime_type(path: Path) -> str:
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        mime_type = media_types.get(path.suffix.lower())
        if mime_type is None:
            raise ValueError("Stored image has an unsupported type")
        return mime_type

    @staticmethod
    def _portrait_prompt(character_prompt: str, style: Any) -> str:
        established_style = style if isinstance(style, str) and style.strip() else ""
        return (
            "Generate exactly one standalone character portrait. Do not add captions, "
            "labels, borders, or additional characters.\n"
            f"Established art style: {established_style}\n"
            f"Character portrait prompt: {character_prompt}"
        )

    @staticmethod
    def _illustration_prompt(chapter_prompt: str, style: Any) -> str:
        established_style = style if isinstance(style, str) and style.strip() else ""
        return (
            "Generate exactly one chapter scene illustration using the supplied character "
            "portraits as visual identity references. Preserve their recognizable features, "
            "clothing, and proportions. Do not add captions, labels, or borders.\n"
            f"Established art style: {established_style}\n"
            f"Persisted chapter illustration prompt: {chapter_prompt}"
        )

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
