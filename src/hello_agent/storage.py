from pathlib import Path


def save_text(directory: Path | str, filename: str, text: str) -> Path:
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / filename
    output_path.write_text(text, encoding="utf-8")

    return output_path

