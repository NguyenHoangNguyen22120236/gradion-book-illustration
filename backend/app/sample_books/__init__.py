from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SampleBook:
    id: str
    title: str
    author: str
    filename: str

    def public_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "author": self.author}


SAMPLE_BOOKS = (
    SampleBook(
        id="alice-in-wonderland",
        title="Alice’s Adventures in Wonderland",
        author="Lewis Carroll",
        filename="alice-in-wonderland.txt",
    ),
    SampleBook(
        id="wizard-of-oz",
        title="The Wonderful Wizard of Oz",
        author="L. Frank Baum",
        filename="wizard-of-oz.txt",
    ),
    SampleBook(
        id="wind-in-the-willows",
        title="The Wind in the Willows",
        author="Kenneth Grahame",
        filename="wind-in-the-willows.txt",
    ),
)

_SAMPLE_BOOKS_BY_ID = {book.id: book for book in SAMPLE_BOOKS}
_SAMPLE_BOOK_DIRECTORY = Path(__file__).resolve().parent


def read_sample_book(sample_book_id: str) -> str:
    try:
        sample = _SAMPLE_BOOKS_BY_ID[sample_book_id]
    except KeyError as error:
        raise ValueError("Unknown sample book") from error
    return (_SAMPLE_BOOK_DIRECTORY / sample.filename).read_text(encoding="utf-8-sig")
