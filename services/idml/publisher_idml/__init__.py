"""
IDML writer -- AST/story XML -> an IDML package InDesign will actually open.

WHAT CHANGED AND WHY. The previous writer produced a ZIP containing
`designmap.xml`, `Resources.xml` and bare `Stories/*.xml` roots. InDesign rejects
that: an IDML package is an OCF-style container, and the parts it refuses to open
without are not optional decoration. Every structural decision below was read off
a real InDesign-authored IDML (DOMVersion 10.0), not inferred:

  mimetype                 FIRST zip entry, STORED (never deflated), exactly
                           "application/vnd.adobe.indesign-idml-package"
  META-INF/container.xml   OASIS container naming designmap.xml as the rootfile
  designmap.xml            <Document xmlns:idPkg=...> whose <idPkg:* src="..."/>
                           children point at every other part in the package
  Resources/*.xml          Graphic (colors/swatches), Fonts, Styles, Preferences
  MasterSpreads, Spreads   page geometry; text frames live on spread pages
  Stories/Story_*.xml      each wrapped in <idPkg:Story>, not a bare <Story>
  XML/{Tags,BackingStory}  referenced by designmap; small but not omittable

WHY THERE IS NO PAGE COMPOSITION HERE. InDesign composes text when the document
is opened -- line breaking, hyphenation and page breaks are its composer's, and
no generator can predict them. So this writer does not pretend to paginate. It
threads a chain of frames sized from the render path's MEASURED page count and
turns on Smart Text Reflow (`AddPages="EndOfStory"`, `DeleteEmptyPages="true"`),
so InDesign extends the document if our count was short and drops the surplus if
it was long. That is why the `idml` stage is terminal: its page count is not a
fact this system knows, and nothing downstream may price a spine off it.

WHY THE STORY COMES FROM PANDOC. `Stories/*.xml` content is the same
ParagraphStyleRange/CharacterStyleRange markup pandoc's ICML writer emits, so the
story is converted from the HTML that `ast-assemble` already proved text-complete
rather than by a second AST walker that could drift from it.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

IDML_MIMETYPE = "application/vnd.adobe.indesign-idml-package"
DOM_VERSION = "10.0"
IDPKG_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"

PT_PER_MM = 72.0 / 25.4

# Self ids. Fixed rather than generated: an IDML built twice from the same inputs
# must be byte-identical, and random ids would make it a different artifact every
# run (ARCHITECTURE.md §2.1 principle 7).
LAYER = "ulayer"
STORY = "ustory"
MASTER = "umaster"
SECTION = "usection"

# Paragraph/character styles this writer DEFINES. pandoc's ICML writer emits
# exactly these names for headings, body text, emphasis and strong; anything else
# it produces (nested group styles like "Blockquote > Paragraph", table styles) is
# remapped below, because an AppliedParagraphStyle pointing at a style the package
# never defines is a dangling reference InDesign has to resolve at open time.
PARAGRAPH_STYLES = (
    "$ID/NormalParagraphStyle",
    "ParagraphStyle/Paragraph",
    "ParagraphStyle/Header1",
    "ParagraphStyle/Header2",
    "ParagraphStyle/Header3",
)
CHARACTER_STYLES = (
    "$ID/NormalCharacterStyle",
    "CharacterStyle/Italic",
    "CharacterStyle/Bold",
)
FALLBACK_PSTYLE = "ParagraphStyle/Paragraph"
FALLBACK_CSTYLE = "$ID/NormalCharacterStyle"

_APPLIED_P_RE = re.compile(r'AppliedParagraphStyle="([^"]*)"')
_APPLIED_C_RE = re.compile(r'AppliedCharacterStyle="([^"]*)"')
# pandoc emits an anchor per heading. A destination with no Hyperlink object in
# designmap is a reference into nothing; strip rather than dangle.
_HYPERLINK_DEST_RE = re.compile(r"<HyperlinkTextDestination[^>]*/>\s*")

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def normalize_story_xml(icml: str) -> str:
    """Make a pandoc ICML story fragment safe to drop into `Stories/Story_*.xml`.

    Two jobs, both about dangling references: strip hyperlink destinations that
    designmap does not declare, and repoint any style this package does not
    define at one it does.
    """
    icml = _HYPERLINK_DEST_RE.sub("", icml)

    def fix_p(m: re.Match) -> str:
        name = m.group(1)
        return f'AppliedParagraphStyle="{name if name in PARAGRAPH_STYLES else FALLBACK_PSTYLE}"'

    def fix_c(m: re.Match) -> str:
        name = m.group(1)
        return f'AppliedCharacterStyle="{name if name in CHARACTER_STYLES else FALLBACK_CSTYLE}"'

    return _APPLIED_C_RE.sub(fix_c, _APPLIED_P_RE.sub(fix_p, icml))


class IDMLWriter:
    """Builds an IDML package from a story fragment plus a DesignSpec/profile.

    `story_xml` is ICML markup (ParagraphStyleRange elements). `page_count` is the
    measured extent from the render path's pagemap -- a hint for how many threaded
    frames to lay down, never a claim about how InDesign will paginate.
    """

    def __init__(
        self,
        story_xml: str,
        *,
        title: str = "Untitled",
        designspec: Optional[dict] = None,
        profile: Optional[dict] = None,
        page_count: int = 1,
    ):
        self.story_xml = normalize_story_xml(story_xml)
        self.title = title
        self.spec = designspec or {}
        self.profile = profile or {}
        # Slack in both directions: Smart Text Reflow adds pages when the story
        # overruns and deletes the empties when it does not fill them, so an
        # approximate frame count converges on open instead of leaving overset
        # text (a red + box) in a file a designer is meant to receive finished.
        self.page_count = max(1, int(page_count))

    # ── geometry ─────────────────────────────────────────────────────────

    @property
    def _trim(self) -> tuple[float, float]:
        trim = self.profile.get("trimSize") or self.spec.get("trimSize") or {}
        return (
            float(trim.get("width", 152.4)) * PT_PER_MM,
            float(trim.get("height", 228.6)) * PT_PER_MM,
        )

    @property
    def _bleed_pt(self) -> float:
        return float((self.profile.get("bleed") or {}).get("all", 0.0)) * PT_PER_MM

    @property
    def _margins(self) -> tuple[float, float, float, float]:
        m = self.spec.get("margins") or {}
        return (
            float(m.get("top", 18)) * PT_PER_MM,
            float(m.get("bottom", 20)) * PT_PER_MM,
            (float(m.get("inside", 15)) + float(m.get("gutter", 0))) * PT_PER_MM,
            float(m.get("outside", 20)) * PT_PER_MM,
        )

    def _typography(self) -> dict:
        t = self.spec.get("typography") or {}
        return {
            "font": (t.get("bodyFont") or {}).get("family", "EB Garamond"),
            "heading_font": ((t.get("headingFont") or t.get("displayFont") or {})
                             .get("family")
                             or (t.get("bodyFont") or {}).get("family", "EB Garamond")),
            "size": float(t.get("bodySize", 10.5)),
            # 14.173pt = 5.000mm, the house baseline. Restated rather than
            # imported: this package is standalone (services/idml is on its own
            # sys.path in the worker) and must not depend on the repo-root
            # `templates` module. If one changes, the other has to follow --
            # which is why the number carries its derivation here too.
            "leading": float(t.get("leading", 14.173)),
            "indent": float(t.get("paragraphIndent", 1.5)),
            "justified": t.get("bodyAlignment", "justified") == "justified",
        }

    # ── package ──────────────────────────────────────────────────────────

    def write(self, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        if output_path.suffix != ".idml":
            output_path = output_path.with_suffix(".idml")

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # First and STORED. InDesign reads the mimetype at a fixed offset the
            # way EPUB and ODF readers do; deflating it, or writing it second,
            # makes the package unrecognisable before any XML is parsed.
            zf.writestr(
                zipfile.ZipInfo("mimetype"), IDML_MIMETYPE,
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml", self._container())
            zf.writestr("META-INF/metadata.xml", self._metadata())
            zf.writestr("designmap.xml", self._designmap())
            zf.writestr("Resources/Graphic.xml", self._graphic())
            zf.writestr("Resources/Fonts.xml", self._fonts())
            zf.writestr("Resources/Styles.xml", self._styles())
            zf.writestr("Resources/Preferences.xml", self._preferences())
            zf.writestr("XML/Tags.xml", self._tags())
            zf.writestr("XML/BackingStory.xml", self._backing_story())
            zf.writestr(f"MasterSpreads/MasterSpread_{MASTER}.xml", self._master_spread())
            for page in range(1, self.page_count + 1):
                zf.writestr(f"Spreads/Spread_uspread{page}.xml", self._spread(page))
            zf.writestr(f"Stories/Story_{STORY}.xml", self._story())

        return output_path

    def _container(self) -> str:
        return (
            XML_DECL
            + '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="designmap.xml" media-type="text/xml"></rootfile>\n'
            "  </rootfiles>\n"
            "</container>\n"
        )

    def _metadata(self) -> str:
        return (
            XML_DECL
            + '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
            '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '    <rdf:Description rdf:about="" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f"      <dc:title><rdf:Alt><rdf:li xml:lang=\"x-default\">"
            f"{escape(self.title)}</rdf:li></rdf:Alt></dc:title>\n"
            "    </rdf:Description>\n"
            "  </rdf:RDF>\n"
            "</x:xmpmeta>\n"
        )

    def _designmap(self) -> str:
        spreads = "\n".join(
            f'  <idPkg:Spread src="Spreads/Spread_uspread{p}.xml" />'
            for p in range(1, self.page_count + 1)
        )
        return (
            XML_DECL
            + '<?aid style="50" type="document" readerVersion="6.0" '
            'featureSet="257" product="10.0(70)" ?>\n'
            f'<Document xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}" Self="d" '
            f'StoryList="{STORY}" ZeroPoint="0 0" ActiveLayer="{LAYER}" '
            'CMYKProfile="$ID/" RGBProfile="$ID/" '
            'SolidColorIntent="UseColorSettings" AfterBlendingIntent="UseColorSettings" '
            'DefaultImageIntent="UseColorSettings" RGBPolicy="ColorPolicyOff" '
            'CMYKPolicy="ColorPolicyOff" AccurateLABSpots="false">\n'
            '  <idPkg:Graphic src="Resources/Graphic.xml" />\n'
            '  <idPkg:Fonts src="Resources/Fonts.xml" />\n'
            '  <idPkg:Styles src="Resources/Styles.xml" />\n'
            '  <idPkg:Preferences src="Resources/Preferences.xml" />\n'
            '  <idPkg:Tags src="XML/Tags.xml" />\n'
            f'  <Layer Self="{LAYER}" Name="Layer 1" Visible="true" Locked="false" '
            'IgnoreWrap="false" ShowGuides="true" LockGuides="false" UI="true" '
            'Expendable="true" Printable="true" />\n'
            f'  <idPkg:MasterSpread src="MasterSpreads/MasterSpread_{MASTER}.xml" />\n'
            f"{spreads}\n"
            '  <idPkg:BackingStory src="XML/BackingStory.xml" />\n'
            f'  <idPkg:Story src="Stories/Story_{STORY}.xml" />\n'
            f'  <Section Self="{SECTION}" Length="{self.page_count}" Name="" '
            'ContinueNumbering="false" IncludeSectionPrefix="false" Marker="" '
            'PageNumberStart="1" PageStart="upage1" SectionPrefix="" '
            'PageNumberStyle="Arabic" />\n'
            "</Document>\n"
        )

    def _graphic(self) -> str:
        return (
            XML_DECL
            + f'<idPkg:Graphic xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            '  <Color Self="Color/Black" Model="Process" Space="CMYK" '
            'ColorValue="0 0 0 100" ColorOverride="Specialblack" '
            'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Black" '
            'ColorEditable="false" ColorRemovable="false" Visible="true" />\n'
            '  <Color Self="Color/Paper" Model="Process" Space="CMYK" '
            'ColorValue="0 0 0 0" ColorOverride="Specialpaper" '
            'AlternateSpace="NoAlternateColor" AlternateColorValue="" Name="Paper" '
            'ColorEditable="false" ColorRemovable="false" Visible="true" />\n'
            '  <Swatch Self="Swatch/None" Name="None" ColorEditable="false" '
            'ColorRemovable="false" Visible="true" SwatchCreatorID="7730" />\n'
            '  <StrokeStyle Self="StrokeStyle/$ID/Solid" Name="$ID/Solid" />\n'
            "</idPkg:Graphic>\n"
        )

    def _fonts(self) -> str:
        """Declare the faces the styles ask for.

        `Status="Installed"` is what InDesign writes for a font present on the
        authoring machine. If the face is missing on the machine that opens the
        package InDesign reports a missing font and substitutes -- visibly, in its
        own dialog. That is the correct failure: this writer cannot embed fonts
        (IDML has no mechanism for it), so it must not pretend otherwise.
        """
        t = self._typography()
        families = []
        # Index, not hash(family): Python randomises str hashes per process, so a
        # hash-derived Self id would make the same book a different artifact on
        # every run and defeat content-addressed caching.
        for i, family in enumerate(dict.fromkeys([t["font"], t["heading_font"]])):
            families.append(
                f'  <FontFamily Self="difont{i}" '
                f"Name={quoteattr(family)}>\n"
                f'    <Font Self="difont{i}Regular" '
                f"FontFamily={quoteattr(family)} Name={quoteattr(family + ' Regular')} "
                f"PostScriptName={quoteattr(family.replace(' ', '') + '-Regular')} "
                'Status="Installed" FontStyleName="Regular" FontType="OpenTypeCFF" '
                'WritingScript="0" />\n'
                "  </FontFamily>"
            )
        return (
            XML_DECL
            + f'<idPkg:Fonts xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            + "\n".join(families)
            + "\n</idPkg:Fonts>\n"
        )

    def _styles(self) -> str:
        t = self._typography()
        justification = "LeftJustified" if t["justified"] else "LeftAlign"
        indent_pt = t["indent"] * t["size"]

        def pstyle(self_id: str, name: str, **attrs) -> str:
            font = attrs.pop("_font", t["font"])
            attr_s = " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items())
            return (
                f'  <ParagraphStyle Self={quoteattr(self_id)} Name={quoteattr(name)} '
                f"{attr_s}>\n"
                "    <Properties>\n"
                f"      <AppliedFont type=\"string\">{escape(font)}</AppliedFont>\n"
                "    </Properties>\n"
                "  </ParagraphStyle>"
            )

        paragraph_styles = [
            pstyle("$ID/NormalParagraphStyle", "$ID/NormalParagraphStyle",
                   PointSize=t["size"], Leading=t["leading"],
                   Justification=justification, Hyphenation="true"),
            pstyle("ParagraphStyle/Paragraph", "Body",
                   PointSize=t["size"], Leading=t["leading"],
                   FirstLineIndent=round(indent_pt, 3),
                   Justification=justification, Hyphenation="true"),
            # The chapter break lives in the style, not in the story: InDesign
            # starts every Header1 on a new recto, which is what a book does and
            # what the DesignSpec's chapterOpenings.startsOn asks for.
            pstyle("ParagraphStyle/Header1", "Chapter Title",
                   _font=t["heading_font"],
                   PointSize=round(t["size"] * 1.6, 2),
                   Leading=round(t["leading"] * 1.6, 2),
                   SpaceBefore=round(t["leading"] * 3, 2),
                   SpaceAfter=round(t["leading"] * 2, 2),
                   Justification="CenterAlign",
                   StartParagraph=self._chapter_start(),
                   KeepWithNext="2"),
            pstyle("ParagraphStyle/Header2", "Subhead",
                   _font=t["heading_font"],
                   PointSize=round(t["size"] * 1.2, 2),
                   Leading=round(t["leading"] * 1.2, 2),
                   SpaceBefore=round(t["leading"], 2),
                   SpaceAfter=round(t["leading"] / 2, 2),
                   Justification="LeftAlign", KeepWithNext="2"),
            pstyle("ParagraphStyle/Header3", "Sub-subhead",
                   _font=t["heading_font"], PointSize=t["size"],
                   Leading=t["leading"], SpaceBefore=round(t["leading"], 2),
                   Justification="LeftAlign", KeepWithNext="2"),
        ]

        return (
            XML_DECL
            + f'<idPkg:Styles xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            '  <RootCharacterStyleGroup Self="ucharstyles">\n'
            '    <CharacterStyle Self="$ID/NormalCharacterStyle" '
            'Name="$ID/[No character style]" />\n'
            '    <CharacterStyle Self="CharacterStyle/Italic" Name="Italic" '
            'FontStyle="Italic" />\n'
            '    <CharacterStyle Self="CharacterStyle/Bold" Name="Bold" '
            'FontStyle="Bold" />\n'
            "  </RootCharacterStyleGroup>\n"
            '  <RootParagraphStyleGroup Self="uparastyles">\n'
            + "\n".join(paragraph_styles)
            + "\n  </RootParagraphStyleGroup>\n"
            "</idPkg:Styles>\n"
        )

    def _chapter_start(self) -> str:
        starts_on = (self.spec.get("chapterOpenings") or {}).get("startsOn", "recto")
        return {"recto": "NextOddPage", "verso": "NextEvenPage"}.get(starts_on, "NextPage")

    def _preferences(self) -> str:
        w, h = self._trim
        bleed = self._bleed_pt
        return (
            XML_DECL
            + f'<idPkg:Preferences xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            f'  <DocumentPreference PageHeight="{h:.6f}" PageWidth="{w:.6f}" '
            'CreatePrimaryTextFrame="false" '
            f'PagesPerDocument="{self.page_count}" FacingPages="true" '
            f'DocumentBleedTopOffset="{bleed:.6f}" '
            f'DocumentBleedBottomOffset="{bleed:.6f}" '
            f'DocumentBleedInsideOrLeftOffset="{bleed:.6f}" '
            f'DocumentBleedOutsideOrRightOffset="{bleed:.6f}" '
            'DocumentBleedUniformSize="true" PreserveLayoutWhenShuffling="true" '
            'AllowPageShuffle="true" Intent="PrintIntent" PageBinding="LeftToRight" />\n'
            # The load-bearing preference: InDesign reflows the threaded story on
            # open, adding pages when it overruns our frame count and removing the
            # empties when it does not reach them.
            '  <TextPreference SmartTextReflow="true" AddPages="EndOfStory" '
            'LimitToMasterTextFrames="false" DeleteEmptyPages="true" '
            'TypographersQuotes="true" />\n'
            '  <ViewPreference PointsPerInch="72" HorizontalMeasurementUnits="Millimeters" '
            'VerticalMeasurementUnits="Millimeters" RulerOrigin="SpreadOrigin" />\n'
            "</idPkg:Preferences>\n"
        )

    def _tags(self) -> str:
        return (
            XML_DECL
            + f'<idPkg:Tags xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            '  <XMLTag Self="XMLTag/Root" Name="Root">\n'
            "    <Properties>\n"
            '      <TagColor type="enumeration">LightBlue</TagColor>\n'
            "    </Properties>\n"
            "  </XMLTag>\n"
            "</idPkg:Tags>\n"
        )

    def _backing_story(self) -> str:
        return (
            XML_DECL
            + f'<idPkg:BackingStory xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            '  <XmlStory Self="ubacking" AppliedTOCStyle="n" TrackChanges="false" '
            'StoryTitle="$ID/" AppliedNamedGrid="n">\n'
            '    <ParagraphStyleRange AppliedParagraphStyle="$ID/NormalParagraphStyle">\n'
            '      <CharacterStyleRange AppliedCharacterStyle="$ID/NormalCharacterStyle">\n'
            '        <XMLElement Self="di3" MarkupTag="XMLTag/Root" />\n'
            "      </CharacterStyleRange>\n"
            "    </ParagraphStyleRange>\n"
            "  </XmlStory>\n"
            "</idPkg:BackingStory>\n"
        )

    def _page_margins(self, page: int) -> tuple[float, float, float, float]:
        """(top, bottom, left, right) in points for this page.

        Inside/outside swap by parity: on a recto (odd page) the binding edge is
        on the left, on a verso it is on the right. Emitting one fixed pair would
        put every gutter on the same side and bind half the book into the spine.
        """
        top, bottom, inside, outside = self._margins
        if page % 2 == 1:
            return top, bottom, inside, outside
        return top, bottom, outside, inside

    def _master_spread(self) -> str:
        w, h = self._trim
        top, bottom, inside, outside = self._margins
        pages = []
        for i, (name, x_offset, left, right) in enumerate(
            [("A", -w, outside, inside), ("A", 0.0, inside, outside)]
        ):
            pages.append(
                f'    <Page Self="umasterpage{i}" AppliedAlternateLayout="n" '
                f'LayoutRule="Off" OptionalPage="false" '
                f'GeometricBounds="0 0 {h:.6f} {w:.6f}" '
                f'ItemTransform="1 0 0 1 {x_offset:.6f} {-h / 2:.6f}" '
                f'Name={quoteattr(name)} AppliedMaster="n" '
                'MasterPageTransform="1 0 0 1 0 0" TabOrder="" '
                'GridStartingPoint="TopOutside" UseMasterGrid="true">\n'
                f'      <MarginPreference ColumnCount="1" ColumnGutter="12" '
                f'Top="{top:.6f}" Bottom="{bottom:.6f}" Left="{left:.6f}" '
                f'Right="{right:.6f}" ColumnDirection="Horizontal" />\n'
                "    </Page>"
            )
        return (
            XML_DECL
            + f'<idPkg:MasterSpread xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            f'  <MasterSpread Self="{MASTER}" ItemTransform="1 0 0 1 0 0" '
            'Name="A-Master" NamePrefix="A" BaseName="Master" ShowMasterItems="true" '
            'PageCount="2" PrimaryTextFrame="n">\n'
            + "\n".join(pages)
            + "\n  </MasterSpread>\n"
            "</idPkg:MasterSpread>\n"
        )

    def _spread(self, page: int) -> str:
        w, h = self._trim
        top, bottom, left, right = self._page_margins(page)
        frame_w = w - left - right
        frame_h = h - top - bottom
        # Page origin sits at the top-left of the page in spread coordinates; the
        # frame is then translated by the margins and drawn as a rectangle path
        # from its own origin.
        frame_x = left
        frame_y = -h / 2 + top
        previous_frame = f"uframe{page - 1}" if page > 1 else "n"
        next_frame = f"uframe{page + 1}" if page < self.page_count else "n"

        return (
            XML_DECL
            + f'<idPkg:Spread xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            f'  <Spread Self="uspread{page}" FlattenerOverride="Default" '
            'AllowPageShuffle="true" ItemTransform="1 0 0 1 0 0" '
            'ShowMasterItems="true" PageCount="1" BindingLocation="0" '
            'PageTransitionType="None" PageTransitionDirection="NotApplicable" '
            'PageTransitionDuration="Medium">\n'
            f'    <Page Self="upage{page}" AppliedAlternateLayout="n" LayoutRule="Off" '
            f'OptionalPage="false" GeometricBounds="0 0 {h:.6f} {w:.6f}" '
            f'ItemTransform="1 0 0 1 0 {-h / 2:.6f}" Name="{page}" '
            f'AppliedMaster="{MASTER}" MasterPageTransform="1 0 0 1 0 0" '
            'TabOrder="" OverrideList="" GridStartingPoint="TopOutside" '
            'UseMasterGrid="true">\n'
            f'      <MarginPreference ColumnCount="1" ColumnGutter="12" '
            f'Top="{top:.6f}" Bottom="{bottom:.6f}" Left="{left:.6f}" '
            f'Right="{right:.6f}" ColumnDirection="Horizontal" />\n'
            "    </Page>\n"
            f'    <TextFrame Self="uframe{page}" ParentStory="{STORY}" '
            f'PreviousTextFrame="{previous_frame}" NextTextFrame="{next_frame}" '
            f'ContentType="TextType" ItemLayer="{LAYER}" Locked="false" '
            'Visible="true" Name="$ID/" '
            f'ItemTransform="1 0 0 1 {frame_x:.6f} {frame_y:.6f}">\n'
            "      <Properties>\n"
            "        <PathGeometry>\n"
            '          <GeometryPathType PathOpen="false">\n'
            "            <PathPointArray>\n"
            f'              <PathPointType Anchor="0 0" LeftDirection="0 0" '
            'RightDirection="0 0" />\n'
            f'              <PathPointType Anchor="0 {frame_h:.6f}" '
            f'LeftDirection="0 {frame_h:.6f}" RightDirection="0 {frame_h:.6f}" />\n'
            f'              <PathPointType Anchor="{frame_w:.6f} {frame_h:.6f}" '
            f'LeftDirection="{frame_w:.6f} {frame_h:.6f}" '
            f'RightDirection="{frame_w:.6f} {frame_h:.6f}" />\n'
            f'              <PathPointType Anchor="{frame_w:.6f} 0" '
            f'LeftDirection="{frame_w:.6f} 0" RightDirection="{frame_w:.6f} 0" />\n'
            "            </PathPointArray>\n"
            "          </GeometryPathType>\n"
            "        </PathGeometry>\n"
            "      </Properties>\n"
            f'      <TextFramePreference TextColumnCount="1" '
            f'TextColumnFixedWidth="{frame_w:.6f}" AutoSizingType="Off" />\n'
            "    </TextFrame>\n"
            "  </Spread>\n"
            "</idPkg:Spread>\n"
        )

    def _story(self) -> str:
        return (
            XML_DECL
            + f'<idPkg:Story xmlns:idPkg="{IDPKG_NS}" DOMVersion="{DOM_VERSION}">\n'
            f'  <Story Self="{STORY}" AppliedTOCStyle="n" TrackChanges="false" '
            'StoryTitle="$ID/" AppliedNamedGrid="n">\n'
            '    <StoryPreference OpticalMarginAlignment="true" OpticalMarginSize="12" '
            'FrameType="TextFrameType" StoryOrientation="Horizontal" '
            'StoryDirection="LeftToRightDirection" />\n'
            f"{self.story_xml}\n"
            "  </Story>\n"
            "</idPkg:Story>\n"
        )


# ── validation ───────────────────────────────────────────────────────────


class IDMLValidationError(Exception):
    """An IDML package that InDesign would refuse, or open wrong."""


def validate_idml(path: str | Path) -> dict:
    """Check the invariants that decide whether InDesign can open the package.

    This exists because the previous writer's tests asserted only that certain
    ZIP entries existed -- which they did, in a file InDesign rejects. These are
    the properties whose absence actually breaks the open, checked on the bytes
    that will be delivered:

      1. mimetype is the first entry, STORED, with the exact media type
      2. META-INF/container.xml names designmap.xml as the rootfile
      3. every idPkg src in designmap resolves to an entry in the package
      4. every TextFrame's ParentStory resolves to a Story that exists
      5. every applied paragraph/character style is defined in Styles.xml
      6. Self ids are unique

    Returns a small summary dict; raises IDMLValidationError on any violation.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if not infos or infos[0].filename != "mimetype":
            first = infos[0].filename if infos else "<empty>"
            raise IDMLValidationError(
                f"first zip entry is {first!r}, must be 'mimetype'")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise IDMLValidationError("mimetype must be STORED, not deflated")
        mimetype = zf.read("mimetype").decode("ascii", "replace")
        if mimetype != IDML_MIMETYPE:
            raise IDMLValidationError(f"mimetype is {mimetype!r}, expected {IDML_MIMETYPE!r}")

        names = set(zf.namelist())

        # Well-formedness first: the parts are assembled as text, and a stray
        # unescaped character or unclosed tag is a class of breakage no amount of
        # structural regex checking below can see. InDesign's parser stops at the
        # first one.
        for name in sorted(names):
            if not name.endswith(".xml"):
                continue
            try:
                ElementTree.fromstring(zf.read(name))
            except ElementTree.ParseError as e:
                raise IDMLValidationError(f"{name} is not well-formed XML: {e}") from e

        if "META-INF/container.xml" not in names:
            raise IDMLValidationError("META-INF/container.xml is missing")
        container = zf.read("META-INF/container.xml").decode("utf-8")
        if 'full-path="designmap.xml"' not in container:
            raise IDMLValidationError("container.xml does not name designmap.xml as rootfile")

        designmap = zf.read("designmap.xml").decode("utf-8")
        srcs = re.findall(r'<idPkg:\w+\s+src="([^"]+)"', designmap)
        missing = [s for s in srcs if s not in names]
        if missing:
            raise IDMLValidationError(f"designmap references missing parts: {missing}")
        if not srcs:
            raise IDMLValidationError("designmap declares no idPkg parts")

        story_ids = set()
        for name in names:
            if name.startswith("Stories/"):
                story_ids.update(re.findall(r'<Story Self="([^"]+)"',
                                            zf.read(name).decode("utf-8")))

        applied_p, applied_c, frames = set(), set(), 0
        for name in names:
            if not (name.startswith("Spreads/") or name.startswith("Stories/")):
                continue
            part = zf.read(name).decode("utf-8")
            for parent in re.findall(r'<TextFrame[^>]*ParentStory="([^"]+)"', part):
                frames += 1
                if parent not in story_ids:
                    raise IDMLValidationError(
                        f"{name}: TextFrame ParentStory={parent!r} has no such Story")
            applied_p.update(_APPLIED_P_RE.findall(part))
            applied_c.update(_APPLIED_C_RE.findall(part))

        styles = zf.read("Resources/Styles.xml").decode("utf-8")
        defined_p = set(re.findall(r'<ParagraphStyle Self="([^"]+)"', styles))
        defined_c = set(re.findall(r'<CharacterStyle Self="([^"]+)"', styles))
        undefined = (applied_p - defined_p) | (applied_c - defined_c)
        if undefined:
            raise IDMLValidationError(f"applied styles are not defined: {sorted(undefined)}")

        selfs: list[str] = []
        for name in names:
            if name.endswith(".xml"):
                selfs += re.findall(r'\sSelf="([^"]+)"', zf.read(name).decode("utf-8"))
        duplicates = {s for s in selfs if selfs.count(s) > 1}
        if duplicates:
            raise IDMLValidationError(f"duplicate Self ids: {sorted(duplicates)}")

    return {
        "parts": len(names),
        "stories": len(story_ids),
        "text_frames": frames,
        "paragraph_styles": len(defined_p),
    }


def write_idml(story_xml: str, output_path: str | Path, *, title: str = "Untitled",
               designspec: Optional[dict] = None, profile: Optional[dict] = None,
               page_count: int = 1) -> Path:
    """Convenience wrapper: build the package and validate it before returning."""
    out = IDMLWriter(story_xml, title=title, designspec=designspec,
                     profile=profile, page_count=page_count).write(output_path)
    validate_idml(out)
    return out
