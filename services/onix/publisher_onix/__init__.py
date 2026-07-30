"""
ONIX 3.0 Metadata Writer — AST -> ONIX XML.

From BUILD_PLAN.md §3.15:
Produces ONIX 3.0 metadata for distribution to vendors, retailers, and libraries.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


def _reproducible_utc(explicit: str | None = None) -> datetime:
    """Timestamp for embedding in a delivered artifact — never the wall clock."""
    if explicit:
        return datetime.fromisoformat(explicit.replace("Z", "+00:00"))
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    return datetime.fromtimestamp(int(epoch) if epoch else 0, tz=timezone.utc)



class ONIXWriter:
    """Generates ONIX 3.0 metadata from a Book AST."""
    
    def __init__(self, ast: dict, sent_at: str | None = None):
        self.ast = ast
        self.metadata = ast.get("metadata", {})
        # See module note: reproducible, not wall-clock.
        self._sent_at = _reproducible_utc(sent_at)
    
    def write(self, output_path: str | Path) -> Path:
        """Generate an ONIX 3.0 XML file."""
        output_path = Path(output_path)
        if output_path.suffix:
            output_path = output_path.with_name(output_path.stem + ".onix.xml")
        else:
            output_path = output_path.with_suffix(".onix.xml")
        
        root = ET.Element("ONIXMessage", {
            "release": "3.0",
            "xmlns": "http://ns.editeur.org/onix/3.0/reference",
        })
        
        header = ET.SubElement(root, "Header")
        ET.SubElement(header, "Sender").text = "Publisher"
        ET.SubElement(header, "SentDateTime").text = self._sent_at.isoformat()
        
        product = ET.SubElement(root, "Product")
        ET.SubElement(product, "RecordReference").text = self.metadata.get("isbn", "unknown")
        ET.SubElement(product, "NotificationType").text = "03"  # notification confirmed on publication
        
        # Descriptive detail
        desc = ET.SubElement(product, "DescriptiveDetail")
        ET.SubElement(desc, "ProductComposition").text = "00"  # single item
        ET.SubElement(desc, "ProductForm").text = "BB"  # paperback
        
        # Title
        title_detail = ET.SubElement(desc, "TitleDetail")
        ET.SubElement(title_detail, "TitleType").text = "01"  # distinctive title
        title_element = ET.SubElement(title_detail, "TitleElement")
        ET.SubElement(title_element, "TitleElementLevel").text = "01"  # product
        ET.SubElement(title_element, "TitleText").text = self.metadata.get("title", "Untitled")
        
        # Contributors
        for contributor in self.metadata.get("contributors", []):
            c = ET.SubElement(desc, "Contributor")
            ET.SubElement(c, "SequenceNumber").text = "1"
            role_map = {"author": "A01", "editor": "B01", "translator": "B06", "illustrator": "A12"}
            ET.SubElement(c, "ContributorRole").text = role_map.get(contributor.get("role", ""), "A01")
            person = ET.SubElement(c, "PersonName")
            person.text = contributor.get("displayName", "")
        
        # Language
        lang = self.metadata.get("language", "en")
        lang_el = ET.SubElement(desc, "Language")
        ET.SubElement(lang_el, "LanguageRole").text = "01"  # language of text
        ET.SubElement(lang_el, "LanguageCode").text = lang[:2]
        
        # Extent (page count)
        extent = ET.SubElement(product, "Extent")
        ET.SubElement(extent, "ExtentType").text = "00"  # main content
        ET.SubElement(extent, "ExtentValue").text = str(self._page_count())
        ET.SubElement(extent, "ExtentUnit").text = "03"  # pages
        
        # Publisher
        pub = ET.SubElement(product, "PublishingDetail")
        ET.SubElement(pub, "Publisher").text = "Publisher"
        ET.SubElement(pub, "PublishingStatus").text = "04"  # active
        
        # Product identifiers
        isbn = self.metadata.get("isbn", "")
        if isbn:
            pid = ET.SubElement(product, "ProductIdentifier")
            ET.SubElement(pid, "ProductIDType").text = "03"  # GTIN-13
            ET.SubElement(pid, "IDValue").text = isbn
        
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        output_path.write_bytes(xml)
        
        return output_path
    
    def _page_count(self) -> int:
        body = self.ast.get("body", [])
        total = sum(len(ch.get("content", [])) for ch in body)
        return max(total // 20, 1)  # rough estimate


def generate_onix(ast_path: str | Path, output_path: str | Path) -> Path:
    """Convenience: load AST JSON and generate ONIX."""
    import json
    ast = json.loads(Path(ast_path).read_bytes())
    writer = ONIXWriter(ast)
    return writer.write(output_path)
