import re

from pathlib import Path

import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = PROJECT_ROOT / "docs" / "Total_result_Avacha-2026.pdf"

# Цвет символов водяного знака в PDF-протоколе за 2026 год.
WATERMARK_GRAY = 0.9098039

DISCIPLINE_CODES = {
    "Лыжи длинная дистанция 2 собаки": "ski_2",
    "Нарта-длинная дистанция 8 собак": "sled_8",
    "Нарта-длинная дистанция более 8 собак": "sled_8plus",
}


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


def duration_to_seconds(value: str) -> int:
    """Преобразует длительность ЧЧ:ММ:СС в целые секунды."""
    value = value.strip()

    if re.fullmatch(r"\d{2,}:[0-5]\d:[0-5]\d", value) is None:
        raise ValueError(f"Некорректная длительность: {value}")

    hours, minutes, seconds = value.split(":")

    return (
            int(hours) * 3600
            + int(minutes) * 60
            + int(seconds)
    )


def split_region_and_results(text: str) -> tuple[str, str]:
    """Отделяет регион от текста результатов по первому времени или статусу."""
    match = re.search(
        r"\b(?:\d{2,}:\d{2}:\d{2}|DNF|DNS|DSQ)\b",
        text,
    )

    if match is None:
        raise ValueError(f"Не найден первый результат этапа: {text}")

    region = text[:match.start()].strip()
    results_text = text[match.start():].strip()

    if not region:
        raise ValueError(f"Не указан регион: {text}")

    return region, results_text


def parse_participant_header(line: str) -> dict | None:
    """Извлекает данные участника и отделяет текст его результатов."""
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

    region, results_text = split_region_and_results(
        match.group("remaining_text")
    )

    return {
        "start_number": int(match.group("start_number")),
        "full_name": match.group("full_name"),
        "birth_year": int(match.group("birth_year")),
        "region": region,
        "results_text": results_text,
    }


def parse_stages(pages: list[str]) -> list[dict]:
    """Извлекает номера и длины этапов, проверяя повторные заголовки."""
    stages_by_number = {}

    pattern = (
        r"Этап\s+(?P<number>\d+)\s+длина\s+"
        r"(?P<distance>\d+(?:[.,]\d+)?)\s+км\b"
    )

    for text in pages:
        for match in re.finditer(pattern, text):
            number = int(match.group("number"))
            distance_km = float(
                match.group("distance").replace(",", ".")
            )

            if number <= 0 or distance_km <= 0:
                raise ValueError(
                    f"Некорректный этап: {match.group(0)}"
                )

            if number in stages_by_number:
                if stages_by_number[number]["distance_km"] != distance_km:
                    raise ValueError(
                        f"Разная длина этапа {number} в заголовках PDF"
                    )
            else:
                stages_by_number[number] = {
                    "number": number,
                    "distance_km": distance_km,
                }

    if not stages_by_number:
        raise ValueError("В PDF не найдены заголовки этапов")

    return [
        stages_by_number[number]
        for number in sorted(stages_by_number)
    ]


def parse_participants(pages: list[str]) -> list[dict]:
    """Собирает участников и связывает их с дисциплинами из PDF."""
    participants = []
    discipline_name = None
    discipline_code = None

    for text in pages:
        for line in text.splitlines():
            line = line.strip()

            if line.startswith("Дисциплина "):
                discipline_name = line.removeprefix("Дисциплина ").strip()
                discipline_code = DISCIPLINE_CODES.get(discipline_name)

                if discipline_code is None:
                    raise ValueError(
                        f"Неизвестная дисциплина: {discipline_name}"
                    )

                continue

            participant = parse_participant_header(line)

            if participant is None:
                continue

            if discipline_code is None:
                raise ValueError(
                    f"Участник найден до заголовка дисциплины: {line}"
                )

            participant["discipline_code"] = discipline_code
            participant["discipline_name"] = discipline_name
            participants.append(participant)

    return participants


def main() -> None:
    pages = extract_pages(PDF_PATH)
    stages = parse_stages(pages)

    for stage in stages:
        print(
            f"Этап {stage['number']}: "
            f"{stage['distance_km']} км"
        )

    print(f"Найдено этапов: {len(stages)}\n")

    participants = parse_participants(pages)

    for participant in participants:
        print(
            f"Номер {participant['start_number']}: "
            f"{participant['full_name']}, "
            f"{participant['birth_year']} г. р. — "
            f"{participant['discipline_code']} — "
            f"{participant['region']}"
        )

    print(f"\nНайдено участников: {len(participants)}")


if __name__ == "__main__":
    main()