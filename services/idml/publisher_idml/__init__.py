"""
IDML Writer — AST -> IDML package.

From BUILD_PLAN.md §3.11 and ARCHITECTURE.md §2.7:
IDML = ZIP of XML (stories, spreads, master spreads, styles, resources).
Generatable without InDesign installed.

Gate: IDML opens clean in InDesign, Affinity, Scribus.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Optional


class IDMLWriter:
    """
    Generates an IDML package from a Book AST and DesignSpec.
    
    IDML structure:
    - designmap.xml
    - master spreads/
    - spreads/
    - stories/
    - styles/
    - resources/
    """
    
    def __init__(self, ast: dict, designspec: Optional[dict] = None):
        self.ast = ast
        self.designspec = designspec or {}
        self._story_counter = 0
        self._spread_counter = 0
    
    def write(self, output_path: str | Path) -> Path:
        """Generate an IDML package at the given path."""
        output_path = Path(output_path)
        if output_path.suffix != ".idml":
            output_path = output_path.with_suffix(".idml")
        
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            self._write_designmap(zf)
            self._write_styles(zf)
            self._write_resources(zf)
            self._write_master_spreads(zf)
            self._write_stories(zf)
            self._write_spreads(zf)
            self._write_preview(zf)
        
        return output_path
    
    def _write_designmap(self, zf: zipfile.ZipFile):
        """Write designmap.xml — the IDML package manifest."""
        root = ET.Element("Document", {
            "xmlns": "http://ns.adobe.com/InDesign/6.0",
            "Self": "d",
            "Version": "8.0",
        })
        
        # Preferences
        pref = ET.SubElement(root, "Preferences")
        pref.set("PageWidth", str(self._page_width()))
        pref.set("PageHeight", str(self._page_height()))
        pref.set("ColumnCount", "1")
        pref.set("ColumnGutter", "12")
        pref.set("BleedTop", "3")
        pref.set("BleedBottom", "3")
        pref.set("BleedInside", "3")
        pref.set("BleedOutside", "3")
        
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        zf.writestr("designmap.xml", xml)
    
    def _write_styles(self, zf: zipfile.ZipFile):
        """Write paragraph and character styles."""
        root = ET.Element("RootParagraphStyleGroup", {
            "Self": "ParagraphStyles",
            "Name": "Paragraph Styles",
        })
        
        # Body text style
        body = ET.SubElement(root, "ParagraphStyle", {
            "Self": "ParagraphStyle/Body",
            "Name": "Body Text",
            "AppliedFont": self._body_font(),
            "FontStyle": "Regular",
            "PointSize": str(self._body_size()),
            "Leading": str(self._leading()),
            "LeftIndent": "0",
            "RightIndent": "0",
            "FirstLineIndent": str(self._para_indent()),
            "Justification": "LeftAlign" if self._alignment() == "ragged-right" else "CenterAlign" if self._alignment() == "center" else "LeftAlign",
        })
        
        # Chapter title style
        chapter = ET.SubElement(root, "ParagraphStyle", {
            "Self": "ParagraphStyle/ChapterTitle",
            "Name": "Chapter Title",
            "AppliedFont": self._heading_font(),
            "FontStyle": "Bold",
            "PointSize": str(self._body_size() * 1.8),
            "Leading": str(self._leading() * 2),
            "SpaceBefore": str(self._leading() * 4),
            "SpaceAfter": str(self._leading()),
            "Justification": "CenterAlign",
        })
        
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        zf.writestr("Styles/ParagraphStyles.xml", xml)
        
        # Character styles
        char_root = ET.Element("RootCharacterStyleGroup", {
            "Self": "CharacterStyles",
            "Name": "Character Styles",
        })
        italic = ET.SubElement(char_root, "CharacterStyle", {
            "Self": "CharacterStyle/Italic",
            "Name": "Italic",
            "FontStyle": "Italic",
        })
        xml = ET.tostring(char_root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        zf.writestr("Styles/CharacterStyles.xml", xml)
    
    def _write_resources(self, zf: zipfile.ZipFile):
        """Write resources.xml (swatches, layers, etc.)."""
        root = ET.Element("Resources", {
            "xmlns": "http://ns.adobe.com/InDesign/6.0",
        })
        
        # Color swatches
        swatches = ET.SubElement(root, "Swatch")
        black = ET.SubElement(swatches, "Swatch", {
            "Self": "Swatch/Black",
            "Name": "Black",
            "ColorValue": "0 0 0 100",  # CMYK
        })
        white = ET.SubElement(swatches, "Swatch", {
            "Self": "Swatch/Paper",
            "Name": "Paper",
            "ColorValue": "0 0 0 0",
        })
        
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        zf.writestr("Resources.xml", xml)
    
    def _write_master_spreads(self, zf: zipfile.ZipFile):
        """Write master spread templates."""
        root = ET.Element("Document", {"Self": "MasterSpreads"})
        spread = ET.SubElement(root, "Spread", {
            "Self": "MasterSpread/A",
            "Name": "A-Master",
        })
        
        # Recto page
        recto = ET.SubElement(spread, "Page", {
            "Self": "MasterSpread/A/Page/1",
            "Name": "A",
            "AppliedMaster": "MasterSpread/A",
            "Bounds": f"0 0 {self._page_height()} {self._page_width()}",
        })
        
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        zf.writestr("MasterSpreads/MasterSpread_A.xml", xml)
    
    def _write_stories(self, zf: zipfile.ZipFile):
        """Write story XML files — the actual content."""
        body = self.ast.get("body", [])
        
        for chapter in body:
            attrs = chapter.get("attrs", {})
            title = attrs.get("title", "Chapter")
            chapter_id = attrs.get("id", "ch1")
            content = chapter.get("content", [])
            
            # Chapter title story
            self._story_counter += 1
            story = self._make_story(
                story_id=self._story_counter,
                content=[{"type": "paragraph", "style": "ChapterTitle", "text": title}],
            )
            zf.writestr(f"Stories/Story_{self._story_counter}.xml", story)
            
            # Chapter body story
            self._story_counter += 1
            body_paragraphs = [p for p in content if p.get("type") == "paragraph"]
            body_story = self._make_story(
                story_id=self._story_counter,
                content=[{"type": "paragraph", "style": "Body", "text": self._extract_text(p)} for p in body_paragraphs],
            )
            zf.writestr(f"Stories/Story_{self._story_counter}.xml", body_story)
    
    def _make_story(self, story_id: int, content: list[dict]) -> str:
        """Generate a story XML document."""
        root = ET.Element("Story", {
            "Self": f"Story_{story_id}",
            "AppliedTOC": "DefaultTOCStyle",
        })
        
        for item in content:
            if item["type"] == "paragraph":
                p = ET.SubElement(root, "ParagraphStyleRange", {
                    "AppliedParagraphStyle": f"ParagraphStyle/{item.get('style', 'Body')}",
                })
                text = ET.SubElement(p, "Content")
                text.text = item.get("text", "")
                br = ET.SubElement(p, "Br")
        
        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    
    def _write_spreads(self, zf: zipfile.ZipFile):
        """Write spread XML files — page layout with story references."""
        body = self.ast.get("body", [])
        
        page_num = 0
        for ci, chapter in enumerate(body):
            # Chapter opening spread (recto)
            page_num += 1 if page_num % 2 == 0 else 1  # ensure recto
            self._spread_counter += 1
            attrs = chapter.get("attrs", {})
            chapter_id = attrs.get("id", f"ch{ci+1}")
            
            spread = self._make_spread(
                spread_id=self._spread_counter,
                page_num=page_num,
                chapter_id=chapter_id,
                story_refs=[
                    f"Story_{ci * 2 + 1}",  # title story
                    f"Story_{ci * 2 + 2}",  # body story
                ],
            )
            zf.writestr(f"Spreads/Spread_{self._spread_counter}.xml", spread)
            page_num += 1  # body continues on verso+1
    
    def _make_spread(self, spread_id: int, page_num: int, chapter_id: str, story_refs: list[str]) -> str:
        """Generate a spread XML document."""
        root = ET.Element("Spread", {
            "Self": f"Spread_{spread_id}",
        })
        
        # Single page in spread
        page = ET.SubElement(root, "Page", {
            "Self": f"Spread_{spread_id}/Page/{page_num}",
            "Name": str(page_num),
            "Bounds": f"0 0 {self._page_height()} {self._page_width()}",
        })
        
        # Text frame for content
        for i, ref in enumerate(story_refs):
            tf = ET.SubElement(root, "TextFrame", {
                "Self": f"Spread_{spread_id}/TextFrame/{i}",
                "PreviousTextFrame": "Nothing",
                "NextTextFrame": "Nothing",
                "ContentType": "TextType",
                "ItemLayer": "Layer/1",
                "GeometricBounds": f"{self._margin_top()} {self._margin_inside()} {self._page_height() - self._margin_bottom()} {self._page_width() - self._margin_outside()}",
                "AssociatedStory": ref,
            })
        
        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    
    def _write_preview(self, zf: zipfile.ZipFile):
        """Write a preview image placeholder (IDML spec requires it)."""
        zf.writestr("Resources/Preview.png", b"")
    
    # ── Design spec accessors ───────────────────────────────────
    
    def _body_font(self) -> str:
        t = self.designspec.get("typography", {})
        return (t.get("bodyFont") or {}).get("family", "EB Garamond")
    
    def _heading_font(self) -> str:
        t = self.designspec.get("typography", {})
        return (t.get("headingFont") or {}).get("family", self._body_font())
    
    def _body_size(self) -> float:
        return self.designspec.get("typography", {}).get("bodySize", 10.5)
    
    def _leading(self) -> float:
        return self.designspec.get("typography", {}).get("leading", 14.0)
    
    def _para_indent(self) -> float:
        return self.designspec.get("typography", {}).get("paragraphIndent", 1.5) * self._body_size()
    
    def _extract_text(self, para: dict) -> str:
        """Extract text content from a paragraph node."""
        content = para.get("content", [])
        return "".join(
            node.get("text", "") for node in content
            if isinstance(node, dict) and node.get("type") == "text"
        )
    
    def _alignment(self) -> str:
        return self.designspec.get("typography", {}).get("bodyAlignment", "justified")
    
    def _page_width(self) -> float:
        ts = self.designspec.get("trimSize", {})
        return ts.get("width", 152.4) * 2.83465  # mm to pt
    
    def _page_height(self) -> float:
        ts = self.designspec.get("trimSize", {})
        return ts.get("height", 228.6) * 2.83465
    
    def _margin_top(self) -> float:
        m = self.designspec.get("margins", {})
        return m.get("top", 18) * 2.83465
    
    def _margin_bottom(self) -> float:
        m = self.designspec.get("margins", {})
        return m.get("bottom", 20) * 2.83465
    
    def _margin_inside(self) -> float:
        m = self.designspec.get("margins", {})
        return m.get("inside", 15) * 2.83465
    
    def _margin_outside(self) -> float:
        m = self.designspec.get("margins", {})
        return m.get("outside", 20) * 2.83465


def generate_idml(ast_path: str | Path, output_path: str | Path, designspec: Optional[dict] = None) -> Path:
    """Convenience: load AST JSON and generate IDML."""
    ast = json.loads(Path(ast_path).read_bytes())
    writer = IDMLWriter(ast, designspec=designspec)
    return writer.write(output_path)
