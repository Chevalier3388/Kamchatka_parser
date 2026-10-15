import argparse
import json
import re
from datetime import date
from pathlib import Path

import pdfplumber

WATERMARK_GRAY = 0.9098039
NON_FINISH_STATUSES = {"DNF", "DNS", "DSQ"}
SPEED_PATTERN = re.compile(r"\d+(?:[.,]\d+)?|#ЗНАЧ!|#ДЕЛ/0!")

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
    """Убирает серые символы водяного знака, сохраняя остальные объекты."""
    if obj.get("object_type") != "char":
        return True
    color = obj.get("non_stroking_color")
    return not (
        isinstance(color, (tuple, list))
        and len(color) == 3
        and all(abs(component - WATERMARK_GRAY) < 0.0001 for component in color)
    )


def extract_pages(path: Path) -> list[str]:
    """Извлекает текст всех страниц PDF, исключая водяные знаки."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.filter(keep_object).extract_text(x_tolerance=1, y_tolerance=1)
            if not text or not text.strip():
                raise ValueError(f"На странице {page_number} не найден текст")
            pages.append(text)
    return pages


def duration_to_seconds(value: str) -> int:
    """Преобразует длительность ЧЧ:ММ:СС в целые секунды."""
    value = value.strip()
    if re.fullmatch(r"\d{2,}:[0-5]\d:[0-5]\d", value) is None:
        raise ValueError(f"Некорректная длительность: {value}")
    hours, minutes, seconds = map(int, value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def parse_result(value: str) -> tuple[str | None, int | None]:
    """Возвращает статус и секунды; нулевое время оставляет неизвестным."""
    if value in NON_FINISH_STATUSES:
        return value.lower(), None
    seconds = duration_to_seconds(value)
    return ("finished", seconds) if seconds else (None, None)


def parse_stages(pages: list[str]) -> list[dict]:
    """Извлекает номера и длины этапов, проверяя повторные заголовки."""
    distances = {}
    pattern = (
        r"Этап\s+(?P<number>\d+)\s+длина\s+"
        r"(?P<distance>\d+(?:[.,]\d+)?)\s+км\b"
    )

    for text in pages:
        for match in re.finditer(pattern, text):
            number = int(match.group("number"))
            distance_km = float(match.group("distance").replace(",", "."))
            if number <= 0 or distance_km <= 0:
                raise ValueError(f"Некорректный этап: {match.group(0)}")
            if distances.setdefault(number, distance_km) != distance_km:
                raise ValueError(f"Разная длина этапа {number} в заголовках PDF")

    if not distances:
        raise ValueError("В PDF не найдены заголовки этапов")
    return [
        {"number": number, "distance_km": distance}
        for number, distance in sorted(distances.items())
    ]


def parse_participants(pages: list[str], stages: list[dict]) -> list[dict]:
    """Разбирает строки участников с результатами и текущей дисциплиной."""
    participants = []
    discipline_name = None
    header_pattern = r"\d+\s+(\d+)\s+(.+?)\s+(\d{4})\s+(.+)"
    for text in pages:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Дисциплина "):
                discipline_name = line.removeprefix("Дисциплина ").strip()
                if discipline_name not in DISCIPLINE_CODES:
                    raise ValueError(f"Неизвестная дисциплина: {discipline_name}")
                continue
            if re.match(r"^\d+\s+\d+\s+", line) is None:
                continue
            header = re.fullmatch(header_pattern, line)
            if header is None:
                raise ValueError(f"Не удалось разобрать строку участника: {line}")
            number, name, birth_year, remaining = header.groups()
            first_result = re.search(
                r"\b(?:\d{2,}:\d{2}:\d{2}|DNF|DNS|DSQ)\b", remaining
            )
            if first_result is None:
                raise ValueError(f"Не найден первый результат этапа: {remaining}")
            region = remaining[: first_result.start()].strip()
            if not region:
                raise ValueError(f"Не указан регион: {remaining}")
            if discipline_name is None:
                raise ValueError(f"Участник найден до заголовка дисциплины: {line}")
            results_text = remaining[first_result.start() :].strip()
            results, summary_text = parse_stage_results(results_text, stages)
            summary = parse_summary(summary_text)
            penalties, penalty_warnings = parse_penalties(summary["raw_note"])
            participants.append(
                {
                    "start_number": int(number),
                    "full_name": name,
                    "birth_year": int(birth_year),
                    "region": region,
                    "results_text": results_text,
                    "discipline_code": DISCIPLINE_CODES[discipline_name],
                    "discipline_name": discipline_name,
                    "stage_results": results,
                    "summary_text": summary_text,
                    "summary": summary,
                    "penalties": penalties,
                    "penalty_warnings": penalty_warnings,
                }
            )
    return participants


def parse_stage_results(text: str, stages: list[dict]) -> tuple[list[dict], str]:
    """Разбирает результаты этапов и отделяет текст общего итога."""
    tokens = text.split()
    summary_start = len(stages) * 2
    if len(tokens) < summary_start + 2:
        raise ValueError(f"Недостаточно данных для результатов этапов: {text}")
    results = []
    for index, stage in enumerate(stages):
        raw_result, raw_speed = tokens[index * 2 : index * 2 + 2]
        if SPEED_PATTERN.fullmatch(raw_speed) is None:
            raise ValueError(
                f"Неожиданное значение скорости этапа {stage['number']}: {raw_speed}"
            )
        status, seconds = parse_result(raw_result)
        results.append(
            {
                "stage_number": stage["number"],
                "raw_result": raw_result,
                "status": status,
                "final_time_seconds": seconds,
                "warning": (
                    "Нулевое значение: статус этапа требует проверки"
                    if status is None
                    else None
                ),
            }
        )
    return results, " ".join(tokens[summary_start:])


def parse_summary(text: str) -> dict:
    """Извлекает общий результат, место из источника и примечание."""
    tokens = text.split()
    if len(tokens) < 2:
        raise ValueError(f"Недостаточно данных для общего итога: {text}")
    raw_speed, raw_result = tokens[:2]
    if SPEED_PATTERN.fullmatch(raw_speed) is None:
        raise ValueError(f"Неожиданная средняя скорость: {raw_speed}")
    status, seconds = parse_result(raw_result)
    if status is None:
        raise ValueError(f"Нулевой общий результат требует проверки: {text}")

    source_place = None
    note_start = 2

    if len(tokens) > 2 and re.fullmatch(r"[1-9]\d*", tokens[2]):
        source_place = int(tokens[2])
        note_start = 3
    elif status == "finished":
        raise ValueError(f"Не найдено место финишировавшего участника: {text}")
    return {
        "raw_result": raw_result,
        "status": status,
        "total_time_seconds": seconds,
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
        return [], [f"Не удалось распознать примечание: {raw_note}"]
    amount = int(match.group("amount"))
    seconds = amount * 60 if match.group("unit").lower() == "мин" else amount
    if seconds <= 0:
        return [], [f"Некорректная длительность штрафа: {raw_note}"]
    penalty = {
        "seconds": seconds,
        "rule_reference": f"П.{match.group('rule')}",
        "stage_number": None,
        "raw_note": raw_note,
    }

    return [penalty], ["В примечании не указан этап применения штрафа"]


def parse_race(pages: list[str]) -> dict:
    """Извлекает данные гонки из шапки PDF образца 2026 года."""
    if not pages:
        raise ValueError("Нет страниц для разбора данных гонки")

    header_text = pages[0].split("Дисциплина ", 1)[0]
    lines = [line.strip() for line in header_text.splitlines() if line.strip()]
    pattern = r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+([А-Яа-яЁё]+)\s+(\d{4})\s+г\."
    for index, line in enumerate(lines):
        match = re.fullmatch(pattern, line)
        if match:
            break
    else:
        raise ValueError("В шапке PDF не найдены даты гонки")

    title = re.search(r"«([^»]+)»", " ".join(lines[:index]))
    if title is None:
        raise ValueError("В шапке PDF не найдено название гонки")
    raw_name = title[1].strip()
    if not raw_name:
        raise ValueError("В шапке PDF указано пустое название гонки")
    start_day, end_day, month_name, year = match.groups()
    month = MONTH_NUMBERS.get(month_name.lower())
    if month is None:
        raise ValueError(f"Неизвестный месяц в датах гонки: {line}")
    year = int(year)
    try:
        start_date = date(year, month, int(start_day))
        end_date = date(year, month, int(end_day))
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


def parse_protocol_metadata(pages: list[str]) -> tuple[dict, list[str]]:
    """Извлекает погоду и подписи судей из PDF образца 2026 года."""
    fields = {
        "weather": "Погодные условия",
        "chief_judge": "Главный судья",
        "chief_secretary": "Главный секретарь",
    }
    metadata = {}
    warnings = []
    for field, label in fields.items():
        values = set()
        for text in pages:
            for line in text.splitlines():
                match = re.fullmatch(
                    rf"{re.escape(label)}\b\s*:?\s*(.*)", line.strip(), re.IGNORECASE
                )
                if match and match[1].strip():
                    values.add(match[1].strip())
        if len(values) > 1:
            raise ValueError(f"Разные значения поля «{label}» в PDF")
        metadata[field] = next(iter(values), None)
        if not values:
            warnings.append(f"В протоколе не найдено поле: {label}")
    return metadata, warnings


def validate_time(status: str | None, seconds: int | None, context: str) -> None:
    """Завершённому результату нужно положительное целое время, остальным — None."""
    if status == "finished":
        if type(seconds) is not int or seconds <= 0:
            raise ValueError(
                f"{context}: время должно быть целым числом секунд больше нуля"
            )
    elif seconds is not None:
        raise ValueError(f"{context}: время указано для статуса {status}")


def validate_protocol(document: dict) -> None:
    """Проверяет участников, набор результатов этапов и общее время."""
    stages = document["stages"]
    participants = document["participants"]
    if not stages:
        raise ValueError("В документе нет этапов")
    if not participants:
        raise ValueError("В документе нет участников")
    stage_numbers = sorted(stage["number"] for stage in stages)
    if len(stage_numbers) != len(set(stage_numbers)):
        raise ValueError("В документе повторяются номера этапов")
    seen_start_numbers = set()
    for participant in participants:
        number = participant["start_number"]
        if number in seen_start_numbers:
            raise ValueError(f"Повторяется стартовый номер: {number}")
        seen_start_numbers.add(number)
        results = participant["stage_results"]
        result_numbers = sorted(result["stage_number"] for result in results)
        if result_numbers != stage_numbers:
            raise ValueError(
                f"Участник №{number}: номера результатов этапов {result_numbers} "
                f"не совпадают с этапами {stage_numbers}"
            )

        for result in results:
            validate_time(
                result["status"],
                result["final_time_seconds"],
                f"Участник №{number}, этап {result['stage_number']}",
            )
        summary = participant["summary"]
        total = summary["total_time_seconds"]
        validate_time(summary["status"], total, f"Участник №{number}, общий результат")
        if summary["status"] != "finished":
            continue
        if any(result["status"] != "finished" for result in results):
            raise ValueError(
                f"Участник №{number}: общий статус finished, но завершены не все этапы"
            )

        calculated_total = sum(result["final_time_seconds"] for result in results)
        if total != calculated_total:
            raise ValueError(
                f"Участник №{number}: общий результат {total} с, "
                f"а сумма этапов {calculated_total} с"
            )


def parse_protocol(path: Path) -> dict:
    """Собирает данные PDF-протокола и предупреждения в один словарь."""
    pages = extract_pages(path)
    race = parse_race(pages)
    metadata, metadata_warnings = parse_protocol_metadata(pages)
    stages = parse_stages(pages)
    participants = parse_participants(pages, stages)
    warnings = metadata_warnings.copy()
    for participant in participants:
        prefix = f"Участник №{participant['start_number']}"
        warnings.extend(
            f"{prefix}, этап {result['stage_number']}: {result['warning']}"
            for result in participant["stage_results"]
            if result["warning"] is not None
        )
        warnings.extend(
            f"{prefix}: {warning}" for warning in participant["penalty_warnings"]
        )
    document = {
        "source_filename": path.name,
        "race": race,
        "protocol_metadata": metadata,
        "stages": stages,
        "participants": participants,
        "warnings": warnings,
    }

    validate_protocol(document)
    return document


def save_protocol_json(document: dict, path: Path) -> None:
    """Сохраняет проверенные данные протокола в JSON."""
    text = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def parse_arguments() -> argparse.Namespace:
    """Разбирает пути к исходному PDF и выходному JSON."""
    parser = argparse.ArgumentParser(
        description="Разбор PDF-протокола образца 2026 года в JSON.",
    )
    parser.add_argument(
        "--pdf", type=Path, help="Путь к исходному PDF-файлу (обязательный параметр)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Путь к JSON (по умолчанию: рядом с PDF, с тем же именем)",
    )

    args = parser.parse_args()
    if args.pdf is None:
        parser.error("Укажите путь к исходному PDF-файлу через параметр --pdf.")

    if not args.pdf.is_file():
        parser.error(f"Не найден PDF-файл: {args.pdf}")

    if args.output is None:
        args.output = args.pdf.with_suffix(".json")

    if args.output.is_dir():
        parser.error(
            f"В --output указан каталог, нужен путь к JSON-файлу: {args.output}"
        )

    if args.output.resolve() == args.pdf.resolve():
        parser.error("Пути исходного PDF и выходного JSON должны отличаться")
    return args


def main() -> None:
    args = parse_arguments()
    document = parse_protocol(args.pdf)
    save_protocol_json(document, args.output)
    print(f"JSON сохранён: {args.output}")

    for warning in document["warnings"]:
        print(f"Предупреждение: {warning}")


if __name__ == "__main__":
    main()
