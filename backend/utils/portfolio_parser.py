from io import BytesIO

from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError

KEYWORD_GROUPS = {
    "categories": {
        "fashion": ("fashion", "style", "clothing", "apparel"),
        "beauty": ("beauty", "skincare", "makeup"),
        "food": ("food", "recipe", "cooking", "restaurant"),
        "travel": ("travel", "destination", "hotel", "tourism"),
        "technology": ("technology", "software", "tech", "app"),
        "fitness": ("fitness", "workout", "training", "wellness"),
        "finance": ("finance", "budgeting", "investing"),
        "gaming": ("gaming", "game", "streaming"),
    },
    "skills": {
        "product reviews": ("review", "unboxing"),
        "tutorials": ("tutorial", "how-to", "guide"),
        "photography": ("photography", "photo shoot", "photographer"),
        "storytelling": ("storytelling", "story"),
        "community building": ("community", "engagement"),
    },
    "formats": {
        "short-form video": ("reel", "short-form video", "tiktok", "short video"),
        "long-form video": ("youtube", "long-form video", "vlog"),
        "stories": ("story frame", "stories"),
        "blogs": ("blog", "written article"),
        "live streaming": ("livestream", "live stream", "streaming"),
    },
}


def extract_portfolio_text(file_storage, max_bytes: int = 5 * 1024 * 1024) -> str:
    if not file_storage or not file_storage.filename:
        raise ValueError("Choose a PDF portfolio to upload.")
    if not file_storage.filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF portfolios are supported.")
    content = file_storage.read(max_bytes + 1)
    file_storage.stream.seek(0)
    if len(content) > max_bytes:
        raise ValueError("The portfolio PDF exceeds the 5 MB limit.")
    if not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a valid PDF.")
    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except (PdfReadError, EOFError, ValueError, OSError) as exc:
        raise ValueError("The PDF could not be read. Please try another file.") from exc
    if not text:
        raise ValueError("No selectable text was found. Scanned PDFs are not supported yet.")
    return text[:20000]


def suggest_profile_fields(text: str) -> dict[str, list[str]]:
    normalized = text.casefold()
    return {
        group: [
            label for label, keywords in options.items()
            if any(keyword in normalized for keyword in keywords)
        ]
        for group, options in KEYWORD_GROUPS.items()
    }
