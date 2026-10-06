"""Generate the geometric RACP monitor icon and native Windows ICO assets."""

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    root = Path("apps/client/assets")
    root.mkdir(parents=True, exist_ok=True)
    for name, color in [
        ("connected", "#36d4a3"),
        ("offline", "#90a1b5"),
        ("busy", "#ffbe55"),
        ("app", "#58a9ec"),
    ]:
        image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 248, 248), radius=50, fill="#14263e")
        draw.rounded_rectangle((40, 48, 216, 170), radius=15, outline=color, width=15)
        draw.line((128, 173, 128, 204), fill=color, width=15)
        draw.line((90, 206, 166, 206), fill=color, width=15)
        draw.line((88, 91, 108, 111, 88, 131), fill="white", width=10)
        draw.line((126, 133, 168, 133), fill="white", width=10)
        image.save(root / f"{name}.png")
        image.save(root / f"{name}.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    main()
