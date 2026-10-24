"""Build the standalone demo page from the pipeline's outputs.

    python scripts/build_demo.py   ->  outputs/thehro_demo.html
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    data = json.load(open(ROOT / "outputs" / "demo_scenarios.json"))
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "demo" / "template.html").read_text().replace("__DATA__", blob)
    out = ROOT / "outputs" / "thehro_demo.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
