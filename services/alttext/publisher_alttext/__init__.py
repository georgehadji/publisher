"""
Alt-text service — automated alt-text generation for figures.

From BUILD_PLAN.md §3.18 and P6:
Generates image descriptions using an LLM.
Has its own schema with approved free-text field (the only one).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class AltTextService:
    """
    Generates alt-text descriptions for figures in a book.
    
    Uses the inference gateway's image-analysis capabilities.
    Has an approval UI for human review of generated descriptions.
    """
    
    def __init__(self):
        self._generation_count = 0
    
    def generate(self, image_path: str | Path, caption: str = "",
                 context: Optional[str] = None) -> dict:
        """
        Generate alt-text for an image.
        
        Args:
            image_path: Path to the image file
            caption: Optional existing caption
            context: Optional chapter/section context
            
        Returns:
            dict with "altText", "confidence", "caption", and "warning" keys
        """
        self._generation_count += 1
        
        # In production, calls the inference gateway with the image
        # In the tracer bullet, returns a rule-based description
        alt_text = self._rule_based_description(image_path, caption)
        
        return {
            "altText": alt_text,
            "confidence": 0.85,
            "caption": caption,
            "warning": "AI-generated — please verify" if self._generation_count > 0 else "",
        }
    
    def _rule_based_description(self, image_path: str | Path, caption: str) -> str:
        """Generate a description using basic heuristics."""
        path = Path(image_path)
        name = path.stem.lower()
        
        if caption:
            return f"Image showing: {caption}"
        
        # Heuristic descriptions based on filename
        if "cover" in name:
            return "Book cover image"
        elif "photo" in name or "photograph" in name:
            return "Photograph"
        elif "illustration" in name or "drawing" in name or "art" in name:
            return "Illustration"
        elif "chart" in name or "graph" in name or "diagram" in name:
            return "Chart or diagram"
        elif "map" in name:
            return "Map"
        elif "portrait" in name or "headshot" in name:
            return "Portrait photograph"
        else:
            return "Image (no description available)"
    
    @property
    def generation_count(self) -> int:
        return self._generation_count


def generate_alttext(image_path: str | Path, caption: str = "") -> str:
    """Convenience: generate alt-text for a single image."""
    service = AltTextService()
    result = service.generate(image_path, caption)
    return result["altText"]
