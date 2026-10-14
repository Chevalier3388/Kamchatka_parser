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


def main() -> None:
    pages = extract_pages(PDF_PATH)
    print(f"Количество страниц: {len(pages)}")

    for page_number, text in enumerate(pages, start=1):
        print(f"\nСтраница {page_number}")
        print(text)


if __name__ == "__main__":
    main()