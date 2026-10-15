import re

from datetime import date
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

MONTH_NUMBERS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
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


def parse_stage_results(
        text: str,
        stages: list[dict],
) -> tuple[list[dict], str]:
    """Разбирает результаты этапов и отделяет текст общего итога."""
    tokens = text.split()
    stage_token_count = len(stages) * 2

    if len(tokens) < stage_token_count + 2:
        raise ValueError(
            f"Недостаточно данных для результатов этапов: {text}"
        )

    results = []

    for index, stage in enumerate(stages):
        raw_result = tokens[index * 2]
        raw_speed = tokens[index * 2 + 1]

        if re.fullmatch(
                r"\d+(?:[.,]\d+)?|#ЗНАЧ!|#ДЕЛ/0!",
                raw_speed,
        ) is None:
            raise ValueError(
                f"Неожиданное значение скорости "
                f"этапа {stage['number']}: {raw_speed}"
            )

        status = None
        final_time_seconds = None
        warning = None

        if raw_result in ("DNF", "DNS", "DSQ"):
            status = raw_result.lower()
        else:
            seconds = duration_to_seconds(raw_result)

            if seconds == 0:
                warning = "Нулевое значение: статус этапа требует проверки"
            else:
                status = "finished"
                final_time_seconds = seconds

        results.append({
            "stage_number": stage["number"],
            "raw_result": raw_result,
            "status": status,
            "final_time_seconds": final_time_seconds,
            "warning": warning,
        })

    summary_text = " ".join(tokens[stage_token_count:])

    return results, summary_text


def parse_summary(text: str) -> dict:
    """Извлекает общий результат, место из источника и примечание."""
    tokens = text.split()

    if len(tokens) < 2:
        raise ValueError(
            f"Недостаточно данных для общего итога: {text}"
        )

    raw_speed = tokens[0]
    raw_result = tokens[1]

    if re.fullmatch(
            r"\d+(?:[.,]\d+)?|#ЗНАЧ!|#ДЕЛ/0!",
            raw_speed,
    ) is None:
        raise ValueError(
            f"Неожиданная средняя скорость: {raw_speed}"
        )

    total_time_seconds = None

    if raw_result in ("DNF", "DNS", "DSQ"):
        status = raw_result.lower()
    else:
        total_time_seconds = duration_to_seconds(raw_result)

        if total_time_seconds == 0:
            raise ValueError(
                f"Нулевой общий результат требует проверки: {text}"
            )

        status = "finished"

    source_place = None
    note_start = 2

    if (
            len(tokens) > 2
            and re.fullmatch(r"[1-9]\d*", tokens[2]) is not None
    ):
        source_place = int(tokens[2])
        note_start = 3
    elif status == "finished":
        raise ValueError(
            f"Не найдено место финишировавшего участника: {text}"
        )

    return {
        "raw_result": raw_result,
        "status": status,
        "total_time_seconds": total_time_seconds,
        "source_place": source_place,
        "raw_note": " ".join(tokens[note_start:]),
    }


def parse_penalties(raw_note: str) -> tuple[list[dict], list[str]]:
    """Извлекает штраф из примечания образца 2026 года и предупреждения."""
    text = raw_note.strip()

    if text in ("", "0"):
        return [], []

    pattern = (
        r"штр\.\s*вр\.\s*(?P<amount>\d+)\s*"
        r"(?P<unit>мин|сек)\.\s*п[.,]\s*"
        r"П\.(?P<rule>\d+(?:\.\d+)+)"
    )

    match = re.fullmatch(pattern, text, flags=re.IGNORECASE)

    if match is None:
        return [], [
            f"Не удалось распознать примечание: {raw_note}"
        ]

    amount = int(match.group("amount"))
    unit = match.group("unit").lower()
    seconds = amount * 60 if unit == "мин" else amount

    if seconds <= 0:
        return [], [
            f"Некорректная длительность штрафа: {raw_note}"
        ]

    penalty = {
        "seconds": seconds,
        "rule_reference": f"П.{match.group('rule')}",
        "stage_number": None,
        "raw_note": raw_note,
    }

    return [penalty], [
        "В примечании не указан этап применения штрафа"
    ]


def parse_race(pages: list[str]) -> dict:
    """Извлекает данные гонки из шапки PDF образца 2026 года."""
    if not pages:
        raise ValueError("Нет страниц для разбора данных гонки")

    header_text = pages[0].split("Дисциплина ", 1)[0]
    lines = [
        line.strip()
        for line in header_text.splitlines()
        if line.strip()
    ]

    date_pattern = (
        r"(?P<start_day>\d{1,2})\s*[-–—]\s*"
        r"(?P<end_day>\d{1,2})\s+"
        r"(?P<month>[А-Яа-яЁё]+)\s+"
        r"(?P<year>\d{4})\s+г\."
    )

    for index, line in enumerate(lines):
        match = re.fullmatch(date_pattern, line)

        if match is None:
            continue

        title_match = re.search(
            r"«(?P<name>[^»]+)»",
            " ".join(lines[:index]),
        )

        if title_match is None:
            raise ValueError("В шапке PDF не найдено название гонки")

        raw_name = title_match.group("name").strip()

        if not raw_name:
            raise ValueError("В шапке PDF указано пустое название гонки")

        month = MONTH_NUMBERS.get(match.group("month").lower())

        if month is None:
            raise ValueError(f"Неизвестный месяц в датах гонки: {line}")

        year = int(match.group("year"))

        try:
            start_date = date(year, month, int(match.group("start_day")))
            end_date = date(year, month, int(match.group("end_day")))
        except ValueError as error:
            raise ValueError(f"Некорректные даты гонки: {line}") from error

        if end_date < start_date:
            raise ValueError(f"Дата окончания раньше даты начала: {line}")

        if index + 1 >= len(lines):
            raise ValueError("В шапке PDF не найдено место проведения")

        return {
            "name": f"{raw_name} {year}",
            "year": year,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "location": lines[index + 1],
            "raw_name": raw_name,
            "raw_dates": line,
        }

    raise ValueError("В шапке PDF не найдены даты гонки")


def parse_protocol_metadata(pages: list[str]) -> tuple[dict, list[str]]:
    """Извлекает погоду и подписи судей из PDF образца 2026 года."""
    fields = {
        "weather": "Погодные условия",
        "chief_judge": "Главный судья",
        "chief_secretary": "Главный секретарь",
    }
    metadata = {field: None for field in fields}

    for text in pages:
        for line in text.splitlines():
            line = line.strip()

            for field, label in fields.items():
                match = re.fullmatch(
                    rf"{re.escape(label)}\b\s*:?\s*(?P<value>.*)",
                    line,
                    flags=re.IGNORECASE,
                )

                if match is None:
                    continue

                value = match.group("value").strip()

                if not value:
                    break

                previous_value = metadata[field]

                if previous_value is not None and previous_value != value:
                    raise ValueError(
                        f"Разные значения поля «{label}» в PDF"
                    )

                metadata[field] = value
                break

    warnings = []

    for field, label in fields.items():
        if metadata[field] is None:
            warnings.append(f"В протоколе не найдено поле: {label}")

    return metadata, warnings


def parse_protocol(path: Path) -> dict:
    """Собирает данные PDF-протокола и предупреждения в один словарь."""
    pages = extract_pages(path)
    race = parse_race(pages)
    metadata, metadata_warnings = parse_protocol_metadata(pages)
    stages = parse_stages(pages)
    participants = parse_participants(pages)
    warnings = metadata_warnings.copy()

    for participant in participants:
        results, summary_text = parse_stage_results(
            participant["results_text"],
            stages,
        )
        summary = parse_summary(summary_text)
        penalties, penalty_warnings = parse_penalties(
            summary["raw_note"]
        )

        participant["stage_results"] = results
        participant["summary_text"] = summary_text
        participant["summary"] = summary
        participant["penalties"] = penalties
        participant["penalty_warnings"] = penalty_warnings

        for result in results:
            if result["warning"] is not None:
                warnings.append(
                    f"Участник №{participant['start_number']}, "
                    f"этап {result['stage_number']}: {result['warning']}"
                )

        for warning in penalty_warnings:
            warnings.append(
                f"Участник №{participant['start_number']}: {warning}"
            )
    return {
        "source_filename": path.name,
        "race": race,
        "protocol_metadata": metadata,
        "stages": stages,
        "participants": participants,
        "warnings": warnings,
    }


def main() -> None:
    document = parse_protocol(PDF_PATH)

    stage_result_count = sum(
        len(participant["stage_results"])
        for participant in document["participants"]
    )
    penalty_count = sum(
        len(participant["penalties"])
        for participant in document["participants"]
    )

    print(f"Файл: {document['source_filename']}")
    print(f"Гонка: {document['race']['name']}")
    print(f"Этапов: {len(document['stages'])}")
    print(f"Участников: {len(document['participants'])}")
    print(f"Результатов этапов: {stage_result_count}")
    print(f"Штрафов: {penalty_count}")
    print(f"Предупреждений: {len(document['warnings'])}")

    for warning in document["warnings"]:
        print(f"  - {warning}")


if __name__ == "__main__":
    main()