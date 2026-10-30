"""Group EDS maps by their electron image, using Word captions and image placement."""
from dataclasses import dataclass
from pathlib import Path
import shutil

from PIL import Image, ImageOps

from .docx_extract import DocxImage, _electron_label


@dataclass
class MapGroup:
    label: str
    images: list[Path]


def _read_electron_label(path: Path) -> str:
    """Read only a candidate anchor's title strip; OCR is optional and bounded."""
    if shutil.which("tesseract") is None:
        return ""
    try:
        import pytesseract
        with Image.open(path) as source:
            strip = source.crop((0, 0, source.width, max(1, round(source.height * .12)))).convert("RGBA")
            # Instrument PNG titles are black on transparent white margins.
            white = Image.new("RGBA", strip.size, "white")
            white.alpha_composite(strip)
            width = min(1800, white.width * 2)
            title = ImageOps.grayscale(white).resize((width, max(1, round(white.height * width / white.width))))
            text = pytesseract.image_to_string(title, lang="eng", config="--psm 6", timeout=2)
        return _electron_label(text)
    except (ImportError, OSError, ValueError, RuntimeError, Image.DecompressionBombError):
        # Servers without OCR can still split the repeated large-image layout.
        return ""


def split_map_groups(items: list[DocxImage]) -> list[MapGroup]:
    if not items:
        return []
    # Compare sizes in one coordinate system. Display size is preferable: some
    # instruments export all bitmaps at the same resolution but place them at
    # different sizes in Word. Older/VML reports fall back to pixel dimensions.
    if all(item.display_size for item in items):
        sizes = [item.display_size for item in items]
    else:
        sizes = []
        for item in items:
            try:
                with Image.open(item.path) as image:
                    sizes.append(image.size)
            except (OSError, ValueError):
                sizes.append(None)

    def much_smaller(small, large) -> bool:
        return bool(small and large and small[0] <= large[0] * .8
                    and small[1] <= large[1] * .8
                    and small[0] * small[1] <= large[0] * large[1] * .4)

    def starts_large_image(index: int) -> bool:
        if index < 2 or not sizes[0] or not sizes[index]:
            return False
        size, first = sizes[index], sizes[0]
        # A second large image must resemble the first anchor and be separated
        # by smaller maps. Equal-sized maps and ordinary pagination never split.
        comparable = all(.65 <= size[axis] / first[axis] <= 1.55 for axis in (0, 1))
        return (comparable and much_smaller(sizes[index - 1], size)
                and (index + 1 == len(items) or much_smaller(sizes[index + 1], size)))

    starts = [0]
    for index in range(1, len(items)):
        if items[index].label or starts_large_image(index):
            starts.append(index)
    groups = []
    for number, start in enumerate(starts, 1):
        end = starts[number] if number < len(starts) else len(items)
        label = items[start].label or _read_electron_label(items[start].path)
        if not label and len(starts) > 1:
            label = f"Electron Image{number}"
        groups.append(MapGroup(label, [item.path for item in items[start:end]]))
    return groups
