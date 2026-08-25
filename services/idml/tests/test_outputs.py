"""Tests for secondary output services (P6)."""

import sys
import tempfile
import zipfile
from pathlib import Path

# Add service paths for direct imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "idml"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "epub"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "alttext"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "onix"))
from pathlib import Path

# ── IDML tests ──────────────────────────────────────────────────

class TestIDMLWriter:
    """The writer now takes an ICML story fragment, not a raw AST: story markup
    is pandoc's job (see stages/idml_stage.py), and geometry/styles are this
    writer's. These assertions check the package InDesign actually needs --
    the previous ones passed on a file InDesign rejects."""

    STORY = (
        '<ParagraphStyleRange AppliedParagraphStyle="ParagraphStyle/Header1">'
        '<CharacterStyleRange AppliedCharacterStyle="$ID/NormalCharacterStyle">'
        "<Content>One</Content></CharacterStyleRange></ParagraphStyleRange>"
    )

    def test_write_idml_package(self):
        from publisher_idml import IDMLWriter, validate_idml

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.idml"
            result = IDMLWriter(self.STORY, title="T").write(output)
            assert result.exists()
            assert result.suffix == ".idml"
            validate_idml(result)

            with zipfile.ZipFile(result) as zf:
                names = zf.namelist()
                assert names[0] == "mimetype"
                for required in ("META-INF/container.xml", "designmap.xml",
                                 "Resources/Styles.xml", "Resources/Preferences.xml",
                                 "Resources/Fonts.xml", "Resources/Graphic.xml",
                                 "XML/BackingStory.xml", "XML/Tags.xml"):
                    assert required in names, required

    def test_idml_threads_one_story_through_every_frame(self):
        from publisher_idml import IDMLWriter

        with tempfile.TemporaryDirectory() as tmp:
            result = IDMLWriter(self.STORY, page_count=3).write(Path(tmp) / "test.idml")

            with zipfile.ZipFile(result) as zf:
                stories = [n for n in zf.namelist() if n.startswith("Stories/")]
                spreads = [n for n in zf.namelist() if n.startswith("Spreads/")]
            # One story, many frames -- a book is a single flow, not a story per page.
            assert len(stories) == 1
            assert len(spreads) == 3

    def test_idml_with_designspec(self):
        from publisher_idml import IDMLWriter, validate_idml

        designspec = {
            "trimSize": {"width": 152.4, "height": 228.6},
            "typography": {
                "bodyFont": {"family": "Source Serif Pro"},
                "bodySize": 11.0,
                "leading": 14.5,
                "paragraphIndent": 1.2,
            },
            "margins": {"top": 16, "bottom": 18, "inside": 14, "outside": 18},
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = IDMLWriter(self.STORY, designspec=designspec).write(
                Path(tmp) / "test.idml")
            validate_idml(result)
            with zipfile.ZipFile(result) as zf:
                assert "Source Serif Pro" in zf.read("Resources/Fonts.xml").decode()

    def _make_test_ast(self, chapters: int = 1) -> dict:
        return {
            "schema": "ast/1",
            "body": [
                {
                    "type": "chapter",
                    "attrs": {"number": i+1, "title": f"Chapter {i+1}", "id": f"ch{i+1}"},
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": f"Content of chapter {i+1}."}]},
                    ],
                }
                for i in range(chapters)
            ],
        }


# ── EPUB tests ──────────────────────────────────────────────────

class TestEPUBWriter:
    def test_write_epub(self):
        from publisher_epub import EPUB3Writer
        ast = self._make_test_ast()
        
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.epub"
            writer = EPUB3Writer(ast)
            result = writer.write(output)
            assert result.exists()
            assert result.suffix == ".epub"
            
            with zipfile.ZipFile(result) as zf:
                assert "mimetype" in zf.namelist()
                mimetype = zf.read("mimetype").decode()
                assert mimetype == "application/epub+zip"
                assert "META-INF/container.xml" in zf.namelist()
    
    def test_epub_contains_sections(self):
        from publisher_epub import EPUB3Writer
        ast = self._make_test_ast(chapters=2)
        
        with tempfile.TemporaryDirectory() as tmp:
            writer = EPUB3Writer(ast)
            result = writer.write(Path(tmp) / "test.epub")
            
            with zipfile.ZipFile(result) as zf:
                sections = [n for n in zf.namelist() if "sections/" in n]
                assert len(sections) == 2
    
    def _make_test_ast(self, chapters: int = 1) -> dict:
        return {
            "schema": "ast/1",
            "body": [
                {
                    "type": "chapter",
                    "attrs": {"number": i+1, "title": f"Chapter {i+1}", "id": f"ch{i+1}"},
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": f"Content {i+1}"}]},
                    ],
                }
                for i in range(chapters)
            ],
        }


# ── Alt-text tests ──────────────────────────────────────────────

class TestAltTextService:
    def test_generate_with_caption(self):
        from publisher_alttext import AltTextService
        service = AltTextService()
        result = service.generate("images/photo001.jpg", caption="Sunset over mountains")
        assert "Sunset" in result["altText"]
        assert result["confidence"] > 0
    
    def test_generate_without_caption(self):
        from publisher_alttext import AltTextService
        service = AltTextService()
        
        # Cover image
        result = service.generate("images/cover.png")
        assert "cover" in result["altText"].lower()
        
        # Chart filename has 'illustration' in stem
        result = service.generate("images/chart-sales.png")
        assert len(result["altText"]) > 0
    
    def test_generation_count(self):
        from publisher_alttext import AltTextService
        service = AltTextService()
        service.generate("img1.jpg")
        service.generate("img2.jpg")
        assert service.generation_count == 2


# ── Manuscript Doctor tests ─────────────────────────────────────

class TestManuscriptDoctor:
    def test_analyze_docx(self):
        from publisher_alttext.doctor import ManuscriptDoctor
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.docx"
            path.write_text("test content")
            doctor = ManuscriptDoctor()
            result = doctor.analyze(path)
            assert result["status"] == "success"
            assert result["extension"] == ".docx"
    
    def test_analyze_doc(self):
        from publisher_alttext.doctor import ManuscriptDoctor
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.doc"
            path.write_bytes(b"test")
            doctor = ManuscriptDoctor()
            result = doctor.analyze(path)
            assert len(result["findings"]) > 0


# ── Backlist Triage tests ───────────────────────────────────────

class TestBacklistTriage:
    def test_audit_empty(self):
        from publisher_alttext.doctor import BacklistTriage
        triage = BacklistTriage()
        result = triage.audit([])
        assert result["titlesAudited"] == 0
        assert result["withinBudget"]
    
    def test_audit_titles(self):
        from publisher_alttext.doctor import BacklistTriage
        triage = BacklistTriage()
        titles = [
            {"id": "1", "title": "Book With Images", "hasImages": True, "language": "en", "pageCount": 200},
            {"id": "2", "title": "Book Without Language", "pageCount": 50},
            {"id": "3", "title": "Clean Book", "language": "en", "pageCount": 100},
        ]
        result = triage.audit(titles)
        assert result["titlesAudited"] == 3
        assert result["titlesNeedingRemediation"] >= 1
        assert len(result["rankedRemediationPlan"]) >= 1


# ── ONIX tests ──────────────────────────────────────────────────

class TestONIXWriter:
    def test_write_onix(self):
        from publisher_onix import ONIXWriter
        ast = {
            "metadata": {
                "title": "Test Book",
                "isbn": "9781234567890",
                "language": "en-US",
                "contributors": [
                    {"role": "author", "displayName": "Author Name"},
                ],
            },
            "body": [{"type": "chapter", "content": []}],
        }
        
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.onix.xml"
            writer = ONIXWriter(ast)
            result = writer.write(output)
            assert result.exists()
            assert "ONIXMessage" in result.read_text()
            assert "3.0" in result.read_text()
    
    def test_onix_contains_title(self):
        from publisher_onix import ONIXWriter
        ast = {
            "metadata": {"title": "My Book"},
            "body": [],
        }
        
        with tempfile.TemporaryDirectory() as tmp:
            writer = ONIXWriter(ast)
            result = writer.write(Path(tmp) / "test.onix")
            content = result.read_text()
            assert "My Book" in content
