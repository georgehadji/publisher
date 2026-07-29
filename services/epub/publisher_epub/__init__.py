"""
EPUB 3 Writer — AST -> EPUB with accessibility.

From BUILD_PLAN.md §3.12:
Produces EPUB 3 + a11y, validated by EPUBCheck AND ACE by DAISY.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from typing import Optional


class EPUB3Writer:
    """
    Generates an EPUB 3 package from a Book AST.
    
    Structure:
    - mimetype (application/epub+zip)
    - META-INF/container.xml
    - OEBPS/content.opf
    - OEBPS/toc.xhtml
    - OEBPS/nav.xhtml
    - OEBPS/sections/
    """
    
    def __init__(self, ast: dict):
        self.ast = ast
        self._section_counter = 0
    
    def write(self, output_path: str | Path) -> Path:
        """Generate an EPUB file."""
        output_path = Path(output_path)
        if output_path.suffix != ".epub":
            output_path = output_path.with_suffix(".epub")
        
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", "application/epub+zip")
            self._write_container(zf)
            self._write_opf(zf)
            self._write_nav(zf)
            self._write_sections(zf)
        
        return output_path
    
    def _write_container(self, zf: zipfile.ZipFile):
        """Write META-INF/container.xml."""
        root = ET.Element("container", {
            "version": "1.0",
            "xmlns": "urn:oasis:names:tc:opendocument:xmlns:container",
        })
        rootfiles = ET.SubElement(root, "rootfiles")
        rf = ET.SubElement(rootfiles, "rootfile", {
            "full-path": "OEBPS/content.opf",
            "media-type": "application/oebps-package+xml",
        })
        zf.writestr("META-INF/container.xml", 
                     ET.tostring(root, encoding="utf-8", xml_declaration=True))
    
    def _write_opf(self, zf: zipfile.ZipFile):
        """Write content.opf — the EPUB package manifest."""
        root = ET.Element("package", {
            "version": "3.0",
            "unique-identifier": "book-id",
            "xmlns": "http://www.idpf.org/2007/opf",
        })
        
        metadata = ET.SubElement(root, "metadata", {
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
        })
        meta = ET.SubElement(metadata, "meta", {"property": "dcterms:modified"})
        meta.text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Manifest
        manifest = ET.SubElement(root, "manifest")
        ET.SubElement(manifest, "item", {
            "id": "nav", "href": "nav.xhtml",
            "media-type": "application/xhtml+xml", "properties": "nav",
        })
        
        body = self.ast.get("body", [])
        for i, chapter in enumerate(body):
            section_id = f"section_{i+1}"
            ET.SubElement(manifest, "item", {
                "id": section_id,
                "href": f"sections/{section_id}.xhtml",
                "media-type": "application/xhtml+xml",
            })
        
        # Spine
        spine = ET.SubElement(root, "spine", {"page-progression-direction": "default"})
        ET.SubElement(spine, "itemref", {"idref": "nav"})
        for i in range(len(body)):
            ET.SubElement(spine, "itemref", {"idref": f"section_{i+1}"})
        
        zf.writestr("OEBPS/content.opf",
                     ET.tostring(root, encoding="utf-8", xml_declaration=True))
    
    def _write_nav(self, zf: zipfile.ZipFile):
        """Write the EPUB navigation document."""
        body = self.ast.get("body", [])
        
        html = ET.Element("html", {"xmlns": "http://www.w3.org/1999/xhtml"})
        head = ET.SubElement(html, "head")
        ET.SubElement(head, "title").text = "Table of Contents"
        
        nav = ET.SubElement(ET.SubElement(html, "body"), "nav", {
            "epub:type": "toc",
            "xmlns:epub": "http://www.idpf.org/2007/ops",
        })
        ET.SubElement(nav, "h1").text = "Contents"
        ol = ET.SubElement(nav, "ol")
        
        for i, chapter in enumerate(body):
            title = chapter.get("attrs", {}).get("title", f"Chapter {i+1}")
            li = ET.SubElement(ol, "li")
            a = ET.SubElement(li, "a", {"href": f"sections/section_{i+1}.xhtml"})
            a.text = title
        
        zf.writestr("OEBPS/nav.xhtml",
                     ET.tostring(html, encoding="utf-8", xml_declaration=True))
    
    def _write_sections(self, zf: zipfile.ZipFile):
        """Write chapter XHTML files."""
        body = self.ast.get("body", [])
        
        for i, chapter in enumerate(body):
            section_id = f"section_{i+1}"
            title = chapter.get("attrs", {}).get("title", f"Chapter {i+1}")
            content = chapter.get("content", [])
            
            html = ET.Element("html", {"xmlns": "http://www.w3.org/1999/xhtml"})
            head = ET.SubElement(html, "head")
            ET.SubElement(head, "title").text = title
            
            body_el = ET.SubElement(html, "body")
            ET.SubElement(body_el, "h1").text = title
            
            for para in content:
                if para.get("type") == "paragraph":
                    p = ET.SubElement(body_el, "p")
                    self._add_inline_content(p, para.get("content", []))
                elif para.get("type") == "blockquote":
                    bq = ET.SubElement(body_el, "blockquote")
                    for child in para.get("content", []):
                        if child.get("type") == "paragraph":
                            p = ET.SubElement(bq, "p")
                            self._add_inline_content(p, child.get("content", []))
                elif para.get("type") == "heading":
                    h = ET.SubElement(body_el, "h2")
                    self._add_inline_content(h, para.get("content", []))
            
            zf.writestr(f"OEBPS/sections/{section_id}.xhtml",
                         ET.tostring(html, encoding="utf-8", xml_declaration=True))
    
    def _add_inline_content(self, parent: ET.Element, content: list[dict]):
        """Add inline text content to an XML element."""
        for node in content:
            if node.get("type") == "text":
                parent.text = (parent.text or "") + node.get("text", "")
            elif node.get("type") == "emphasis":
                em = ET.SubElement(parent, "em")
                em.text = node.get("text", "")
            elif node.get("type") == "strong":
                strong = ET.SubElement(parent, "strong")
                strong.text = node.get("text", "")


def generate_epub(ast_path: str | Path, output_path: str | Path) -> Path:
    """Convenience: load AST JSON and generate EPUB."""
    ast = json.loads(Path(ast_path).read_bytes())
    writer = EPUB3Writer(ast)
    return writer.write(output_path)
