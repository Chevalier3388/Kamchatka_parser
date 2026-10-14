import re

from pathlib import Path

import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = PROJECT_ROOT / "docs" / "Total_result_Avacha-2026.pdf"

# Цвет символов водяного знака в PDF-протоколе за 2026 год.
WATERMARK_GRAY = 0.9098039


def keep_object(obj: dict) -> bool:
    """Проверяет, нужно ли сохранять объект PDF при фильтрации."""
    if obj.get("object_type") != "char":
        return True

    color = obj.get("non_stroking_color")

    if isinstance(color, (tuple, list)) and len(color) == 3:
        is_watermark = all(
            abs(component - WATERMARK_GRAY) < 0.0001
            for component in color
        )
        return not is_watermark

    return True


def extract_pages(path: Path) -> list[str]:
    """Извлекает текст всех страниц PDF, исключая водяные знаки."""
    pages = []

    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            filtered_page = page.filter(keep_object)
            text = filtered_page.extract_text(
                x_tolerance=1,
                y_tolerance=1,
            )

            if not text or not text.strip():
                raise ValueError(
                    f"На странице {page_number} не найден текст"
                )

            pages.append(text)

    return pages


def parse_participant_header(line: str) -> dict | None:
    """Извлекает номер, ФИО и год рождения из строки участника."""
    line = line.strip()

    if re.match(r"^\d+\s+\d+\s+", line) is None:
        return None

    match = re.fullmatch(
        r"\d+\s+(?P<start_number>\d+)\s+"
        r"(?P<full_name>.+?)\s+"
        r"(?P<birth_year>\d{4})\s+"
        r"(?P<remaining_text>.+)",
        line,
    )

    if match is None:
        raise ValueError(f"Не удалось разобрать строку участника: {line}")

    return {
        "start_number": int(match.group("start_number")),
        "full_name": match.group("full_name"),
        "birth_year": int(match.group("birth_year")),
        "remaining_text": match.group("remaining_text"),
    }


def main() -> None:
    pages = extract_pages(PDF_PATH)
    participant_count = 0

    for text in pages:
        for line in text.splitlines():
            participant = parse_participant_header(line)

            if participant is None:
                continue

            participant_count += 1

            print(
                f"Номер {participant['start_number']}: "
                f"{participant['full_name']}, "
                f"{participant['birth_year']} г.р."
            )

    print(f"\nНайдено участников: {participant_count}")


if __name__ == "__main__":
    main()