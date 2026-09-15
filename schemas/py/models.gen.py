# Auto-generated from JSON Schema — do not edit manually.
# Run `node codegen/generate.mjs` from schemas/ to regenerate.

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Optional, Union
import re


class BookAST_SchemaRef(str, Enum):
    AST_1 = "ast/1"


class ArtBrief_SchemaRef(str, Enum):
    ART_BRIEF_1 = "art-brief/1"


class BookAST_SourceRef(BaseModel):
    manuscriptId: str
    manuscriptVersion: Optional[int] = Field(default=None)
    inferenceVersion: int
    inferenceModelId: Optional[str] = Field(default=None)
    createdAt: Optional[datetime] = Field(default=None)


class ArtBrief_SourceRef(BaseModel):
    titleMetaHash: str
    designSpecHash: str
    manuscriptSampleUsed: bool

    @field_validator("titleMetaHash")
    @classmethod
    def _validate_titleMetaHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^sha256:[a-f0-9]{64}$", v):
            raise ValueError("titleMetaHash does not match required pattern")
        return v

    @field_validator("designSpecHash")
    @classmethod
    def _validate_designSpecHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^sha256:[a-f0-9]{64}$", v):
            raise ValueError("designSpecHash does not match required pattern")
        return v


class Contributor_Role(str, Enum):
    AUTHOR = "author"
    EDITOR = "editor"
    TRANSLATOR = "translator"
    ILLUSTRATOR = "illustrator"
    FOREWORDBY = "forewordBy"
    INTRODUCTIONBY = "introductionBy"


class Contributor(BaseModel):
    role: Contributor_Role
    givenName: Optional[str] = Field(default=None)
    familyName: Optional[str] = Field(default=None)
    displayName: str


class Metadata_Series(BaseModel):
    name: str
    volume: Optional[int] = Field(default=None)


class Metadata(BaseModel):
    title: Optional[str] = Field(default=None)
    subtitle: Optional[str] = Field(default=None)
    contributors: Optional[list[Contributor]] = Field(default=None)
    language: Optional[str] = Field(default=None)
    isbn: Optional[str] = Field(default=None)
    series: Optional[Metadata_Series] = Field(default=None)

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-z]{2,3}(-[A-Z]{2})?$", v):
            raise ValueError("language does not match required pattern")
        return v

    @field_validator("isbn")
    @classmethod
    def _validate_isbn(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^(978|979)[0-9]{10}$", v):
            raise ValueError("isbn does not match required pattern")
        return v


class SourceRefLink(BaseModel):
    docxId: str
    contentHash: Optional[str] = Field(default=None)

    @field_validator("contentHash")
    @classmethod
    def _validate_contentHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-f0-9]{64}$", v):
            raise ValueError("contentHash does not match required pattern")
        return v


class Part_Type(str, Enum):
    PART = "part"


class Part_Attrs(BaseModel):
    number: Optional[int] = Field(default=None)
    title: str
    id: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-zA-Z0-9_-]+$", v):
            raise ValueError("id does not match required pattern")
        return v


class Chapter_Type(str, Enum):
    CHAPTER = "chapter"


class Chapter_Attrs_StartsOn(str, Enum):
    RECTO = "recto"
    VERSO = "verso"
    ANY = "any"


class Chapter_Attrs(BaseModel):
    number: int
    title: Optional[str] = Field(default=None)
    startsOn: Optional[Chapter_Attrs_StartsOn] = Field(default=None)
    id: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-zA-Z0-9_-]+$", v):
            raise ValueError("id does not match required pattern")
        return v


class Paragraph_Type(str, Enum):
    PARAGRAPH = "paragraph"


class Paragraph_Attrs_Role(str, Enum):
    NORMAL = "normal"
    CHAPTER_OPENING = "chapter-opening"
    FIRST_PARAGRAPH = "first-paragraph"
    LAST_PARAGRAPH = "last-paragraph"
    CONTINUED = "continued"
    ATTRIBUTION = "attribution"
    SOURCE = "source"


class Paragraph_Attrs_Alignment(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


class Paragraph_Attrs(BaseModel):
    role: Optional[Paragraph_Attrs_Role] = Field(default=None)
    indent: Optional[float] = Field(default=None)
    alignment: Optional[Paragraph_Attrs_Alignment] = Field(default=None)
    language: Optional[str] = Field(default=None)

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-z]{2,3}(-[A-Z]{2})?$", v):
            raise ValueError("language does not match required pattern")
        return v


class Text_Type(str, Enum):
    TEXT = "text"


class Mark_Type(str, Enum):
    EMPHASIS = "emphasis"
    STRONG = "strong"
    SMALLCAPS = "smallCaps"
    UNDERLINE = "underline"
    STRIKETHROUGH = "strikethrough"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"
    CODE = "code"
    LINK = "link"


class Mark_Attrs(BaseModel):
    href: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)


class Mark(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Mark_Type = Field(..., alias="type")
    attrs: Optional[Mark_Attrs] = Field(default=None)


class Text(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Text_Type = Field(..., alias="type")
    text: str
    marks: Optional[list[Mark]] = Field(default=None)


class Emphasis_Type(str, Enum):
    EMPHASIS = "emphasis"


class Emphasis_Attrs_Role(str, Enum):
    EMPHASIS = "emphasis"
    TITLE_OF_WORK = "title-of-work"
    FOREIGN = "foreign"
    THOUGHT = "thought"


class Emphasis_Attrs(BaseModel):
    role: Optional[Emphasis_Attrs_Role] = Field(default=None)


class Emphasis(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Emphasis_Type = Field(..., alias="type")
    attrs: Optional[Emphasis_Attrs] = Field(default=None)
    content: list[InlineNode]


class Strong_Type(str, Enum):
    STRONG = "strong"


class Strong(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Strong_Type = Field(..., alias="type")
    content: list[InlineNode]


class Link_Type(str, Enum):
    LINK = "link"


class Link_Attrs_Target(str, Enum):
    SELF = "_self"
    BLANK = "_blank"
    TOP = "_top"


class Link_Attrs(BaseModel):
    href: str
    title: Optional[str] = Field(default=None)
    target: Optional[Link_Attrs_Target] = Field(default=None)


class Link(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Link_Type = Field(..., alias="type")
    attrs: Link_Attrs
    content: list[InlineNode]


class Superscript_Type(str, Enum):
    SUPERSCRIPT = "superscript"


class Superscript(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Superscript_Type = Field(..., alias="type")
    content: list[InlineNode]


class Subscript_Type(str, Enum):
    SUBSCRIPT = "subscript"


class Subscript(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Subscript_Type = Field(..., alias="type")
    content: list[InlineNode]


class SmallCaps_Type(str, Enum):
    SMALLCAPS = "smallCaps"


class SmallCaps(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: SmallCaps_Type = Field(..., alias="type")
    content: list[InlineNode]


class CodeInline_Type(str, Enum):
    CODEINLINE = "codeInline"


class CodeInline(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: CodeInline_Type = Field(..., alias="type")
    text: str


class HardBreak_Type(str, Enum):
    HARDBREAK = "hardBreak"


class HardBreak(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: HardBreak_Type = Field(..., alias="type")


class IndexEntry_Type(str, Enum):
    INDEXENTRY = "indexEntry"


class IndexEntry_Attrs(BaseModel):
    term: str
    subterm: Optional[str] = Field(default=None)
    see: Optional[str] = Field(default=None)
    seeAlso: Optional[str] = Field(default=None)


class IndexEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: IndexEntry_Type = Field(..., alias="type")
    attrs: IndexEntry_Attrs


class CrossReference_Type(str, Enum):
    CROSSREFERENCE = "crossReference"


class CrossReference_Attrs_Display(str, Enum):
    NUMBER = "number"
    TITLE = "title"
    PAGE = "page"
    NUMBER_AND_TITLE = "number-and-title"


class CrossReference_Attrs(BaseModel):
    target: str
    display: Optional[CrossReference_Attrs_Display] = Field(default=None)


class CrossReference(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: CrossReference_Type = Field(..., alias="type")
    attrs: CrossReference_Attrs
    content: list[InlineNode]


InlineNode = Union[Text, Emphasis, Strong, Link, Superscript, Subscript, SmallCaps, CodeInline, HardBreak, IndexEntry, CrossReference]


class Paragraph(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Paragraph_Type = Field(..., alias="type")
    attrs: Optional[Paragraph_Attrs] = Field(default=None)
    content: list[InlineNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Heading_Type(str, Enum):
    HEADING = "heading"


class Heading_Attrs_Role(str, Enum):
    SECTION = "section"
    SUBSECTION = "subsection"
    SUBSUBSECTION = "subsubsection"
    RUNNING_HEAD = "running-head"


class Heading_Attrs(BaseModel):
    level: int
    role: Optional[Heading_Attrs_Role] = Field(default=None)


class Heading(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Heading_Type = Field(..., alias="type")
    attrs: Heading_Attrs
    content: list[InlineNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Blockquote_Type(str, Enum):
    BLOCKQUOTE = "blockquote"


class Blockquote_Attrs_Role(str, Enum):
    PULL_QUOTE = "pull-quote"
    EXTRACT = "extract"
    LETTER = "letter"
    PRAYER = "prayer"


class Blockquote_Attrs(BaseModel):
    role: Optional[Blockquote_Attrs_Role] = Field(default=None)
    source: Optional[str] = Field(default=None)


class Blockquote(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Blockquote_Type = Field(..., alias="type")
    attrs: Optional[Blockquote_Attrs] = Field(default=None)
    content: list[Paragraph]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Verse_Type(str, Enum):
    VERSE = "verse"


class Verse_Attrs_Role(str, Enum):
    POEM = "poem"
    SONG_LYRICS = "song-lyrics"


class Verse_Attrs(BaseModel):
    role: Optional[Verse_Attrs_Role] = Field(default=None)


class Verse_ContentItem_Type(str, Enum):
    LINE = "line"


class Verse_ContentItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Verse_ContentItem_Type = Field(..., alias="type")
    content: list[InlineNode]


class Verse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Verse_Type = Field(..., alias="type")
    attrs: Optional[Verse_Attrs] = Field(default=None)
    content: list[Verse_ContentItem]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class List_Type(str, Enum):
    LIST = "list"


class List_Attrs_ListType(str, Enum):
    ORDERED = "ordered"
    UNORDERED = "unordered"


class List_Attrs(BaseModel):
    listType: List_Attrs_ListType
    start: Optional[int] = Field(default=None)
    tight: Optional[bool] = Field(default=None)


class List_ContentItem_Type(str, Enum):
    LIST_ITEM = "list-item"


class List_ContentItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: List_ContentItem_Type = Field(..., alias="type")
    content: list[BlockNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class List(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: List_Type = Field(..., alias="type")
    attrs: List_Attrs
    content: list[List_ContentItem]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Table_Type(str, Enum):
    TABLE = "table"


class Table_Attrs_ColgroupItem_Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class Table_Attrs_ColgroupItem(BaseModel):
    width: Optional[str] = Field(default=None)
    align: Optional[Table_Attrs_ColgroupItem_Align] = Field(default=None)


class Table_Attrs(BaseModel):
    caption: Optional[str] = Field(default=None)
    colgroup: Optional[list[Table_Attrs_ColgroupItem]] = Field(default=None)


class TableRow_Type(str, Enum):
    TABLE_ROW = "table-row"


class TableRow_Attrs(BaseModel):
    header: Optional[bool] = Field(default=None)


class TableCell_Type(str, Enum):
    TABLE_CELL = "table-cell"


class TableCell_Attrs_Align(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TableCell_Attrs(BaseModel):
    colspan: Optional[int] = Field(default=None)
    rowspan: Optional[int] = Field(default=None)
    align: Optional[TableCell_Attrs_Align] = Field(default=None)


class TableCell(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: TableCell_Type = Field(..., alias="type")
    attrs: Optional[TableCell_Attrs] = Field(default=None)
    content: list[BlockNode]


class TableRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: TableRow_Type = Field(..., alias="type")
    attrs: Optional[TableRow_Attrs] = Field(default=None)
    content: list[TableCell]


class Table(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Table_Type = Field(..., alias="type")
    attrs: Optional[Table_Attrs] = Field(default=None)
    content: list[TableRow]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Figure_Type(str, Enum):
    FIGURE = "figure"


BookAST_Sha256 = str


class MediaRef_MediaType(str, Enum):
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_SVG_XML = "image/svg+xml"
    IMAGE_TIFF = "image/tiff"


class MediaRef(BaseModel):
    hash: BookAST_Sha256
    mediaType: MediaRef_MediaType
    originalName: Optional[str] = Field(default=None)


class Figure_Attrs_Placement(str, Enum):
    INLINE = "inline"
    FLOAT_LEFT = "float-left"
    FLOAT_RIGHT = "float-right"
    FULL_PAGE = "full-page"


class Figure_Attrs(BaseModel):
    mediaRef: MediaRef
    caption: Optional[str] = Field(default=None)
    altText: Optional[str] = Field(default=None)
    width: Optional[str] = Field(default=None)
    placement: Optional[Figure_Attrs_Placement] = Field(default=None)


class Figure(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Figure_Type = Field(..., alias="type")
    attrs: Figure_Attrs
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Footnote_Type(str, Enum):
    FOOTNOTE = "footnote"


class Footnote_Attrs(BaseModel):
    number: Optional[int] = Field(default=None)
    symbol: Optional[str] = Field(default=None)


class Footnote(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Footnote_Type = Field(..., alias="type")
    attrs: Optional[Footnote_Attrs] = Field(default=None)
    content: list[InlineNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Epigraph_Type(str, Enum):
    EPIGRAPH = "epigraph"


class Epigraph_Attrs(BaseModel):
    source: Optional[str] = Field(default=None)


class Epigraph(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Epigraph_Type = Field(..., alias="type")
    attrs: Optional[Epigraph_Attrs] = Field(default=None)
    content: list[Paragraph]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class SceneBreak_Type(str, Enum):
    SCENEBREAK = "sceneBreak"


class SceneBreak_Attrs_Ornament(str, Enum):
    DINKUS = "dinkus"
    ASTERISM = "asterism"
    FLEURON = "fleuron"
    BLANK_LINE = "blank-line"
    SECTION_SYMBOL = "section-symbol"


class SceneBreak_Attrs(BaseModel):
    ornament: Optional[SceneBreak_Attrs_Ornament] = Field(default=None)


class SceneBreak(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: SceneBreak_Type = Field(..., alias="type")
    attrs: Optional[SceneBreak_Attrs] = Field(default=None)
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Dialogue_Type(str, Enum):
    DIALOGUE = "dialogue"


class Dialogue_Attrs(BaseModel):
    speaker: Optional[str] = Field(default=None)


class Dialogue(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Dialogue_Type = Field(..., alias="type")
    attrs: Optional[Dialogue_Attrs] = Field(default=None)
    content: list[Paragraph]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Sidebar_Type(str, Enum):
    SIDEBAR = "sidebar"


class Sidebar(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Sidebar_Type = Field(..., alias="type")
    content: list[BlockNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Code_Type(str, Enum):
    CODE = "code"


class Code_Attrs(BaseModel):
    language: Optional[str] = Field(default=None)
    numbered: Optional[bool] = Field(default=None)


class Code(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Code_Type = Field(..., alias="type")
    attrs: Optional[Code_Attrs] = Field(default=None)
    content: str
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Equation_Type(str, Enum):
    EQUATION = "equation"


class Equation_Attrs(BaseModel):
    numbered: Optional[bool] = Field(default=None)
    label: Optional[str] = Field(default=None)


class Equation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Equation_Type = Field(..., alias="type")
    attrs: Optional[Equation_Attrs] = Field(default=None)
    content: str
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class PageBreak_Type(str, Enum):
    PAGEBREAK = "pageBreak"


class PageBreak_Attrs_BreakType(str, Enum):
    PAGE = "page"
    COLUMN = "column"
    SECTION = "section"
    RECTO = "recto"
    VERSO = "verso"


class PageBreak_Attrs(BaseModel):
    breakType: Optional[PageBreak_Attrs_BreakType] = Field(default=None)


class PageBreak(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: PageBreak_Type = Field(..., alias="type")
    attrs: Optional[PageBreak_Attrs] = Field(default=None)
    sourceRef: Optional[SourceRefLink] = Field(default=None)


BlockNode = Union[Paragraph, Heading, Blockquote, Verse, List, Table, Figure, Footnote, Epigraph, SceneBreak, Dialogue, Sidebar, Code, Equation, PageBreak]


class Chapter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Chapter_Type = Field(..., alias="type")
    attrs: Chapter_Attrs
    content: list[BlockNode]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


class Part(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Part_Type = Field(..., alias="type")
    attrs: Part_Attrs
    content: list[Chapter]
    sourceRef: Optional[SourceRefLink] = Field(default=None)


BodyNode = Union[Part, Chapter]


FrontMatterNode = Union[BlockNode, dict[str, Any]]


BackMatterNode = Union[BlockNode, dict[str, Any]]


OutputProfile_Sha256 = str


BuildManifest_Sha256 = str


class OverrideOp_SourceRef(BaseModel):
    docxId: str
    contentHash: Optional[str] = Field(default=None)
    fallbackText: Optional[str] = Field(default=None)

    @field_validator("contentHash")
    @classmethod
    def _validate_contentHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-f0-9]{64}$", v):
            raise ValueError("contentHash does not match required pattern")
        return v


class OverrideOp_Op(str, Enum):
    RECLASSIFY = "reclassify"
    SPLIT = "split"
    MERGE = "merge"
    PROMOTE = "promote"
    DEMOTE = "demote"
    DELETE = "delete"
    INSERT = "insert"
    RETITLE = "retitle"
    RENAME = "rename"
    SET_ATTR = "set_attr"
    FLAG_AMBIGUITY = "flag_ambiguity"
    RESOLVE_AMBIGUITY = "resolve_ambiguity"


class OverrideOp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    sourceRef: OverrideOp_SourceRef
    op: OverrideOp_Op
    path: Optional[str] = Field(default=None)
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = Field(default=None)
    value: Optional[Any] = Field(default=None)
    rationale: Optional[str] = Field(default=None)
    actor: str
    at: datetime

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^ov-[a-zA-Z0-9_-]+$", v):
            raise ValueError("id does not match required pattern")
        return v


class TrimSize_Unit(str, Enum):
    MM = "mm"
    IN = "in"


class TrimSize(BaseModel):
    width: float
    height: float
    unit: Optional[TrimSize_Unit] = Field(default=None)


class FontRef(BaseModel):
    family: str
    style: Optional[str] = Field(default=None)


class Typography_BodyAlignment(str, Enum):
    JUSTIFIED = "justified"
    RAGGED_RIGHT = "ragged-right"
    RAGGED_LEFT = "ragged-left"


class Typography(BaseModel):
    bodyFont: FontRef
    headingFont: Optional[FontRef] = Field(default=None)
    bodySize: float
    leading: float
    scaleRatio: Optional[float] = Field(default=None)
    measure: int
    bodyAlignment: Optional[Typography_BodyAlignment] = Field(default=None)
    paragraphIndent: Optional[float] = Field(default=None)
    paragraphSpacing: Optional[float] = Field(default=None)
    opticalMargins: Optional[bool] = Field(default=None)


class Grid_Type(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"
    SPLIT = "split"


class Grid(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type_: Optional[Grid_Type] = Field(default=None, alias="type")
    baselineIncrement: Optional[float] = Field(default=None)
    linesPerPage: Optional[int] = Field(default=None)


class Margins(BaseModel):
    top: float
    bottom: float
    inside: float
    outside: float
    gutter: Optional[float] = Field(default=None)


class Folio_Position(str, Enum):
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_OUTSIDE = "bottom-outside"
    TOP_CENTER = "top-center"
    TOP_OUTSIDE = "top-outside"
    NONE = "none"


class Folio_Style(str, Enum):
    ARABIC = "arabic"
    ROMAN_LOWER = "roman-lower"
    ROMAN_UPPER = "roman-upper"
    NONE = "none"


class Folio_SuppressOnItem(str, Enum):
    CHAPTER_OPENING = "chapter-opening"
    CHAPTER_OPENING_RECTO = "chapter-opening-recto"
    FULL_BLEED = "full-bleed"
    TITLE_PAGE = "title-page"
    PART_OPENING = "part-opening"


class Folio_Weight(str, Enum):
    REGULAR = "regular"
    MEDIUM = "medium"
    SEMIBOLD = "semibold"
    BOLD = "bold"


class Folio_Case(str, Enum):
    NONE = "none"
    UPPERCASE = "uppercase"
    LOWERCASE = "lowercase"
    SMALL_CAPS = "small-caps"


class Folio(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position: Optional[Folio_Position] = Field(default=None)
    style: Optional[Folio_Style] = Field(default=None)
    suppressOn: Optional[list[Folio_SuppressOnItem]] = Field(default=None)
    startNumber: Optional[int] = Field(default=None)
    prefix: Optional[str] = Field(default=None)
    suffix: Optional[str] = Field(default=None)
    sizeDelta: Optional[float] = Field(default=None)
    weight: Optional[Folio_Weight] = Field(default=None)
    case_: Optional[Folio_Case] = Field(default=None, alias="case")
    tracking: Optional[float] = Field(default=None)


class RunningHeads_RectoSource(str, Enum):
    CHAPTER_TITLE = "chapter-title"
    PART_TITLE = "part-title"
    BOOK_TITLE = "book-title"
    NONE = "none"


class RunningHeads_VersoSource(str, Enum):
    BOOK_TITLE = "book-title"
    CHAPTER_TITLE = "chapter-title"
    AUTHOR = "author"
    NONE = "none"


class RunningHeads_Style(str, Enum):
    CENTERED = "centered"
    OUTER_MARGIN = "outer-margin"
    INNER_MARGIN = "inner-margin"
    SHOWN_AND_SHOULDER = "shown-and-shoulder"


class RunningHeads_SuppressOnItem(str, Enum):
    CHAPTER_OPENING = "chapter-opening"
    FULL_BLEED = "full-bleed"
    TITLE_PAGE = "title-page"
    PART_OPENING = "part-opening"


class RunningHeads_Weight(str, Enum):
    REGULAR = "regular"
    MEDIUM = "medium"
    SEMIBOLD = "semibold"
    BOLD = "bold"


class RunningHeads_Case(str, Enum):
    NONE = "none"
    UPPERCASE = "uppercase"
    LOWERCASE = "lowercase"
    SMALL_CAPS = "small-caps"


class RunningHeads(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rectoSource: Optional[RunningHeads_RectoSource] = Field(default=None)
    versoSource: Optional[RunningHeads_VersoSource] = Field(default=None)
    style: Optional[RunningHeads_Style] = Field(default=None)
    suppressOn: Optional[list[RunningHeads_SuppressOnItem]] = Field(default=None)
    separator: Optional[str] = Field(default=None)
    sizeDelta: Optional[float] = Field(default=None)
    weight: Optional[RunningHeads_Weight] = Field(default=None)
    case_: Optional[RunningHeads_Case] = Field(default=None, alias="case")
    tracking: Optional[float] = Field(default=None)


class ChapterOpenings_StartsOn(str, Enum):
    RECTO = "recto"
    VERSO = "verso"
    ANY = "any"


class ChapterOpenings_TitleTreatment(str, Enum):
    CENTERED = "centered"
    RECTO_ONLY = "recto-only"
    SHOULDER_HEAD = "shoulder-head"
    NONE = "none"


class ChapterOpenings_FirstParagraphStyle(str, Enum):
    NO_INDENT = "no-indent"
    SMALL_CAPS = "small-caps"
    ALL_CAPS = "all-caps"
    NORMAL = "normal"


class ChapterOpenings(BaseModel):
    startsOn: Optional[ChapterOpenings_StartsOn] = Field(default=None)
    dropCap: Optional[bool] = Field(default=None)
    dropCapLines: Optional[int] = Field(default=None)
    titleTreatment: Optional[ChapterOpenings_TitleTreatment] = Field(default=None)
    ornament: Optional[str] = Field(default=None)
    firstParagraphStyle: Optional[ChapterOpenings_FirstParagraphStyle] = Field(default=None)


class FontSpec_Source(str, Enum):
    BUNDLED_OFL = "bundled_ofl"
    TENANT_UPLOAD = "tenant_upload"
    LICENSED_SERVER = "licensed_server"


class FontSpec(BaseModel):
    family: str
    style: Optional[str] = Field(default=None)
    weight: Optional[int] = Field(default=None)
    opticalSize: Optional[float] = Field(default=None)
    source: FontSpec_Source
    licenseRef: Optional[str] = Field(default=None)


class Ornaments(BaseModel):
    dinkus: Optional[str] = Field(default=None)
    chapterOrnament: Optional[str] = Field(default=None)
    sectionSymbol: Optional[str] = Field(default=None)


class Hyphenation(BaseModel):
    language: Optional[str] = Field(default=None)
    zone: Optional[float] = Field(default=None)
    shortestWord: Optional[int] = Field(default=None)
    consecutiveHyphens: Optional[int] = Field(default=None)
    dictionaryVersion: Optional[str] = Field(default=None)

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-z]{2,3}(-[A-Z]{2})?$", v):
            raise ValueError("language does not match required pattern")
        return v


class Colors(BaseModel):
    text: Optional[str] = Field(default=None)
    paper: Optional[str] = Field(default=None)
    accent: Optional[str] = Field(default=None)
    link: Optional[str] = Field(default=None)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^#[0-9a-fA-F]{6}$", v):
            raise ValueError("text does not match required pattern")
        return v

    @field_validator("paper")
    @classmethod
    def _validate_paper(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^#[0-9a-fA-F]{6}$", v):
            raise ValueError("paper does not match required pattern")
        return v

    @field_validator("accent")
    @classmethod
    def _validate_accent(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^#[0-9a-fA-F]{6}$", v):
            raise ValueError("accent does not match required pattern")
        return v

    @field_validator("link")
    @classmethod
    def _validate_link(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^#[0-9a-fA-F]{6}$", v):
            raise ValueError("link does not match required pattern")
        return v


class SpecMetadata(BaseModel):
    author: Optional[str] = Field(default=None)
    createdAt: Optional[datetime] = Field(default=None)
    updatedAt: Optional[datetime] = Field(default=None)
    version: Optional[int] = Field(default=None)


class PreflightCheck_Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


class PreflightCheck_Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PreflightCheck(BaseModel):
    code: str
    status: PreflightCheck_Status
    severity: PreflightCheck_Severity
    humanMessage: str
    suggestedFix: Optional[str] = Field(default=None)
    sourceRef: Optional[str] = Field(default=None)
    value: Optional[Any] = Field(default=None)
    expected: Optional[Any] = Field(default=None)
    policyUrl: Optional[str] = Field(default=None)


class ToolchainDigest_Icc(BaseModel):
    cmyk: Optional[BuildManifest_Sha256] = Field(default=None)
    rgb: Optional[BuildManifest_Sha256] = Field(default=None)


class ToolchainDigest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    images: dict[str, str]
    fonts: Optional[BuildManifest_Sha256] = Field(default=None)
    icc: Optional[ToolchainDigest_Icc] = Field(default=None)
    hyphen: Optional[dict[str, str]] = Field(default=None)
    engines: dict[str, str]
    schema_: Optional[str] = Field(default=None, alias="schema")
    exemplarSetHash: Optional[BuildManifest_Sha256] = Field(default=None)
    ruleSetVersion: Optional[str] = Field(default=None)
    promptVersion: Optional[str] = Field(default=None)
    modelId: Optional[str] = Field(default=None)


class StageRecord_Status(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CACHED = "cached"


class StageRecord_InputsItem(BaseModel):
    kind: str
    hash: BuildManifest_Sha256


class StageRecord_OutputsItem(BaseModel):
    kind: str
    hash: BuildManifest_Sha256


class Diagnostic_Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Diagnostic(BaseModel):
    code: str
    severity: Diagnostic_Severity
    humanMessage: str
    suggestedFix: Optional[str] = Field(default=None)
    sourceRef: Optional[str] = Field(default=None)


class StageError_Kind(str, Enum):
    BAD_INPUT = "bad_input"
    POLICY_VIOLATION = "policy_violation"
    ENGINE_BUG = "engine_bug"
    INFRA = "infra"
    EXTERNAL_LIMIT = "external_limit"


class StageError(BaseModel):
    kind: StageError_Kind
    message: str
    diagnostics: Optional[list[Diagnostic]] = Field(default=None)
    retryCount: Optional[int] = Field(default=None)
    retryable: Optional[bool] = Field(default=None)


class StageRecord(BaseModel):
    name: str
    version: int
    status: StageRecord_Status
    cacheHit: Optional[bool] = Field(default=None)
    cacheKey: Optional[BuildManifest_Sha256] = Field(default=None)
    inputs: Optional[list[StageRecord_InputsItem]] = Field(default=None)
    outputs: Optional[list[StageRecord_OutputsItem]] = Field(default=None)
    metrics: Optional[dict[str, float]] = Field(default=None)
    warnings: Optional[list[Diagnostic]] = Field(default=None)
    error: Optional[StageError] = Field(default=None)
    durationMs: Optional[int] = Field(default=None)
    startedAt: Optional[datetime] = Field(default=None)
    completedAt: Optional[datetime] = Field(default=None)


class ArtifactRef(BaseModel):
    hash: BuildManifest_Sha256
    mediaType: str
    size: int
    path: Optional[str] = Field(default=None)


class ClassifiedNode_Classification(str, Enum):
    CHAPTER_TITLE = "chapter-title"
    HEADING_1 = "heading-1"
    HEADING_2 = "heading-2"
    HEADING_3 = "heading-3"
    PARAGRAPH = "paragraph"
    CHAPTER_OPENING = "chapter-opening"
    FIRST_PARAGRAPH = "first-paragraph"
    BLOCKQUOTE = "blockquote"
    EPIGRAPH = "epigraph"
    VERSE = "verse"
    DIALOGUE = "dialogue"
    SCENE_BREAK = "scene-break"
    DINKUS = "dinkus"
    FRONT_HALF_TITLE = "front-half-title"
    FRONT_TITLE_PAGE = "front-title-page"
    FRONT_COPYRIGHT = "front-copyright"
    FRONT_DEDICATION = "front-dedication"
    FRONT_TOC = "front-toc"
    FRONT_FOREWORD = "front-foreword"
    FRONT_PREFACE = "front-preface"
    FRONT_ACKNOWLEDGMENTS = "front-acknowledgments"
    FRONT_PROLOGUE = "front-prologue"
    BACK_EPILOGUE = "back-epilogue"
    BACK_AFTERWORD = "back-afterword"
    BACK_APPENDIX = "back-appendix"
    BACK_NOTES = "back-notes"
    BACK_BIBLIOGRAPHY = "back-bibliography"
    BACK_INDEX = "back-index"
    BACK_ABOUT_AUTHOR = "back-about-author"
    BACK_ALSO_BY = "back-also-by"
    BACK_COLOPHON = "back-colophon"
    FOOTNOTE = "footnote"
    ENDNOTE = "endnote"
    TABLE = "table"
    TABLE_CAPTION = "table-caption"
    FIGURE = "figure"
    FIGURE_CAPTION = "figure-caption"
    UNORDERED_LIST = "unordered-list"
    ORDERED_LIST = "ordered-list"
    SIDEBAR = "sidebar"
    CODE_BLOCK = "code-block"
    EQUATION = "equation"
    PAGE_BREAK = "page-break"
    SECTION_BREAK = "section-break"
    TITLE_OF_WORK = "title-of-work"
    FOREIGN_TERM = "foreign-term"
    EMPHASIS = "emphasis"
    UNCERTAIN = "uncertain"


class ClassifiedNode(BaseModel):
    sourceRef: str
    classification: ClassifiedNode_Classification
    confidence: float


class TrimSizeSuggestion_Rationale(str, Enum):
    GENRE_TYPICAL = "genre-typical"
    PAGE_COUNT_OPTIMAL = "page-count-optimal"
    VENDOR_POPULAR = "vendor-popular"


class TrimSizeSuggestion(BaseModel):
    width: Optional[float] = Field(default=None)
    height: Optional[float] = Field(default=None)
    rationale: Optional[TrimSizeSuggestion_Rationale] = Field(default=None)


class Proposal_Type(str, Enum):
    RECLASSIFY = "reclassify"
    MERGE_CHAPTERS = "merge_chapters"
    SPLIT_CHAPTER = "split_chapter"
    ADJUST_HEADING_LEVEL = "adjust_heading_level"
    IDENTIFY_FRONT_MATTER = "identify_front_matter"
    IDENTIFY_BACK_MATTER = "identify_back_matter"
    FLAG_AMBIGUITY = "flag_ambiguity"
    SUGGEST_TITLE = "suggest_title"


class Proposal_SourceRef(BaseModel):
    docxId: str
    contentHash: Optional[str] = Field(default=None)

    @field_validator("contentHash")
    @classmethod
    def _validate_contentHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-f0-9]{64}$", v):
            raise ValueError("contentHash does not match required pattern")
        return v


class Proposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type_: Proposal_Type = Field(..., alias="type")
    sourceRef: Proposal_SourceRef
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = Field(default=None)
    value: Optional[Union[str, int, bool, float]] = Field(default=None)
    rationale: str
    confidence: Optional[float] = Field(default=None)
    evidence: Optional[list[str]] = Field(default=None)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^pr-[a-zA-Z0-9_-]+$", v):
            raise ValueError("id does not match required pattern")
        return v


class PageEntry_Side(str, Enum):
    RECTO = "recto"
    VERSO = "verso"


class PageEntry_Type(str, Enum):
    NORMAL = "normal"
    CHAPTER_OPENING = "chapter-opening"
    PART_OPENING = "part-opening"
    FULL_BLEED = "full-bleed"
    TITLE_PAGE = "title-page"
    BLANK = "blank"


class PageEntry_ParaRangesItem(BaseModel):
    paraIndex: int
    linesOnPage: int


class PageEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pageNumber: int
    folio: int
    side: PageEntry_Side
    widthPt: float
    heightPt: float
    chapterId: str
    sectionId: Optional[str] = Field(default=None)
    type_: Optional[PageEntry_Type] = Field(default=None, alias="type")
    contentStart: Optional[float] = Field(default=None)
    hasOrphans: Optional[bool] = Field(default=None)
    hasWidows: Optional[bool] = Field(default=None)
    hasRunts: Optional[bool] = Field(default=None)
    wordCount: Optional[int] = Field(default=None)
    defectScore: Optional[float] = Field(default=None)
    paraRanges: Optional[list[PageEntry_ParaRangesItem]] = Field(default=None)


class ChapterEntry_StartsOn(str, Enum):
    RECTO = "recto"
    VERSO = "verso"
    ANY = "any"


class ChapterEntry(BaseModel):
    chapterId: str
    number: int
    title: Optional[str] = Field(default=None)
    startPage: int
    endPage: int
    pageCount: int
    startsOn: Optional[ChapterEntry_StartsOn] = Field(default=None)


class Composition(str, Enum):
    RULE_OF_THIRDS = "rule-of-thirds"
    CENTERED = "centered"
    SYMMETRICAL = "symmetrical"
    DIAGONAL = "diagonal"
    NEGATIVE_SPACE_DOMINANT = "negative-space-dominant"
    FULL_BLEED_TEXTURE = "full-bleed-texture"
    SILHOUETTE = "silhouette"
    CLOSE_CROP = "close-crop"
    WIDE_ESTABLISHING = "wide-establishing"


class PaletteTone(str, Enum):
    DESATURATED_BLUE = "desaturated-blue"
    WARM_AMBER = "warm-amber"
    BONE_WHITE = "bone-white"
    CHARCOAL = "charcoal"
    BLOOD_RED = "blood-red"
    FOREST_GREEN = "forest-green"
    DUSTY_ROSE = "dusty-rose"
    DEEP_VIOLET = "deep-violet"
    MUTED_OCHRE = "muted-ochre"
    STARK_BLACK = "stark-black"
    COOL_TEAL = "cool-teal"
    BURNT_SIENNA = "burnt-sienna"
    PALE_GOLD = "pale-gold"
    SLATE_GREY = "slate-grey"
    IVORY = "ivory"


class Lighting(str, Enum):
    LOW_KEY = "low-key"
    HIGH_KEY = "high-key"
    BACKLIT = "backlit"
    GOLDEN_HOUR = "golden-hour"
    OVERCAST_FLAT = "overcast-flat"
    HARD_NOON = "hard-noon"
    CANDLELIT = "candlelit"
    MOONLIT = "moonlit"
    STUDIO_SOFT = "studio-soft"
    HARSH_FLUORESCENT = "harsh-fluorescent"


class Medium(str, Enum):
    PAINTERLY_DIGITAL = "painterly-digital"
    PHOTOGRAPHIC = "photographic"
    WATERCOLOR = "watercolor"
    LINOCUT = "linocut"
    COLLAGE = "collage"
    FLAT_VECTOR = "flat-vector"
    GOUACHE = "gouache"
    INK_WASH = "ink-wash"
    OIL_PAINTING = "oil-painting"
    RISOGRAPH = "risograph"
    V_3D_RENDER = "3d-render"


class Mood(str, Enum):
    TENSE = "tense"
    MELANCHOLIC = "melancholic"
    WHIMSICAL = "whimsical"
    OMINOUS = "ominous"
    HOPEFUL = "hopeful"
    NOSTALGIC = "nostalgic"
    CLINICAL = "clinical"
    ROMANTIC = "romantic"
    COLD = "cold"
    URGENT = "urgent"
    SERENE = "serene"


class GenreSignal(str, Enum):
    LITERARY_THRILLER = "literary-thriller"
    COZY_MYSTERY = "cozy-mystery"
    EPIC_FANTASY = "epic-fantasy"
    HARD_SCIFI = "hard-scifi"
    ROMANCE = "romance"
    LITERARY_FICTION = "literary-fiction"
    MEMOIR = "memoir"
    HORROR = "horror"
    HISTORICAL_FICTION = "historical-fiction"
    YA_CONTEMPORARY = "ya-contemporary"
    BUSINESS_NONFICTION = "business-nonfiction"
    SELF_HELP = "self-help"
    TRUE_CRIME = "true-crime"
    POETRY = "poetry"


class TypeZone(str, Enum):
    UPPER_THIRD = "upper-third"
    LOWER_THIRD = "lower-third"
    CENTER_BAND = "center-band"
    NONE = "none"


class NegativeTag(str, Enum):
    TEXT = "text"
    READABLE_TYPOGRAPHY = "readable-typography"
    LOGO = "logo"
    WATERMARK = "watermark"
    PHOTOREALISTIC_FACE = "photorealistic-face"
    REAL_PERSON_LIKENESS = "real-person-likeness"
    BRAND_MARKS = "brand-marks"
    SIGNATURE = "signature"
    CLUTTER = "clutter"
    LOW_CONTRAST_CENTER = "low-contrast-center"
    EXTRA_LIMBS = "extra-limbs"
    DISTORTED_ANATOMY = "distorted-anatomy"


class BookAST(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: BookAST_SchemaRef = Field(..., alias="schema")
    metadata: Optional[Metadata] = Field(default=None)
    frontMatter: Optional[list[FrontMatterNode]] = Field(default=None)
    body: list[BodyNode]
    backMatter: Optional[list[BackMatterNode]] = Field(default=None)
    integrityHash: str
    sourceRef: BookAST_SourceRef

    @field_validator("integrityHash")
    @classmethod
    def _validate_integrityHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^sha256:[a-f0-9]{64}$", v):
            raise ValueError("integrityHash does not match required pattern")
        return v


class OverrideSet_Schema(str, Enum):
    OVERRIDES_1 = "overrides/1"


class OverrideSet_OrphanedOpsItem_Reason(str, Enum):
    NO_SOURCE_REF = "no_source_ref"
    CONTENT_MISMATCH = "content_mismatch"
    FUZZY_MATCH_FAILED = "fuzzy_match_failed"


class OverrideSet_OrphanedOpsItem(BaseModel):
    op: OverrideOp
    reason: OverrideSet_OrphanedOpsItem_Reason


class OverrideSet(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: OverrideSet_Schema = Field(..., alias="schema")
    documentId: str
    astVersion: int
    ops: list[OverrideOp]
    orphanedOps: Optional[list[OverrideSet_OrphanedOpsItem]] = Field(default=None)
    createdAt: Optional[datetime] = Field(default=None)
    updatedAt: Optional[datetime] = Field(default=None)


class DesignSpec_Schema(str, Enum):
    DESIGNSPEC_1 = "designspec/1"


class DesignSpec_PreferredEngine(str, Enum):
    CHROME_PAGEDJS = "chrome-pagedjs"
    TYPST = "typst"
    PRINCE = "prince"


class DesignSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: DesignSpec_Schema = Field(..., alias="schema")
    name: str
    templateId: Optional[str] = Field(default=None)
    preferredEngine: Optional[DesignSpec_PreferredEngine] = Field(default=None)
    trimSize: TrimSize
    typography: Typography
    grid: Grid
    margins: Margins
    folio: Optional[Folio] = Field(default=None)
    runningHeads: Optional[RunningHeads] = Field(default=None)
    chapterOpenings: Optional[ChapterOpenings] = Field(default=None)
    fonts: list[FontSpec]
    ornaments: Optional[Ornaments] = Field(default=None)
    hyphenation: Optional[Hyphenation] = Field(default=None)
    colors: Optional[Colors] = Field(default=None)
    metadata: Optional[SpecMetadata] = Field(default=None)


class OutputProfile_Schema(str, Enum):
    PROFILE_1 = "profile/1"


class OutputProfile_Vendor(str, Enum):
    KDP = "kdp"
    INGRAMSPARK = "ingramspark"
    LULU = "lulu"
    BARNESNOBLE = "barnesnoble"
    GENERIC = "generic"


class OutputProfile_TrimSize_Unit(str, Enum):
    MM = "mm"
    IN = "in"


class OutputProfile_TrimSize(BaseModel):
    width: float
    height: float
    unit: Optional[OutputProfile_TrimSize_Unit] = Field(default=None)


class OutputProfile_Bleed(BaseModel):
    all: Optional[float] = Field(default=None)
    top: Optional[float] = Field(default=None)
    bottom: Optional[float] = Field(default=None)
    inside: Optional[float] = Field(default=None)
    outside: Optional[float] = Field(default=None)


class OutputProfile_PdfSpec_Version(str, Enum):
    V_1_3 = "1.3"
    V_1_4 = "1.4"
    V_1_5 = "1.5"
    V_1_6 = "1.6"
    V_1_7 = "1.7"
    V_2_0 = "2.0"


class OutputProfile_PdfSpec_Standard(str, Enum):
    NONE = "none"
    PDFX_1A = "pdfx-1a"
    PDFX_3 = "pdfx-3"
    PDFX_4 = "pdfx-4"
    PDFA_2B = "pdfa-2b"


class OutputProfile_PdfSpec_ColorSpace(str, Enum):
    CMYK = "cmyk"
    GRAY = "gray"
    RGB = "rgb"


class OutputProfile_PdfSpec_OutputIntent(BaseModel):
    iccProfileHash: OutputProfile_Sha256
    iccProfileName: Optional[str] = Field(default=None)
    registryUrl: Optional[str] = Field(default=None)


class OutputProfile_PdfSpec(BaseModel):
    version: Optional[OutputProfile_PdfSpec_Version] = Field(default=None)
    standard: OutputProfile_PdfSpec_Standard
    colorSpace: OutputProfile_PdfSpec_ColorSpace
    outputIntent: Optional[OutputProfile_PdfSpec_OutputIntent] = Field(default=None)


class OutputProfile_CoverSpec_PaperType(str, Enum):
    MATTE = "matte"
    GLOSSY = "glossy"
    PREMIUM_MATTE = "premium-matte"
    LINEN = "linen"
    NONE = "none"


class OutputProfile_CoverSpec_Ink(str, Enum):
    FULL_COLOR = "full-color"
    BLACK_AND_WHITE = "black-and-white"


class OutputProfile_CoverSpec_Finish(str, Enum):
    PERFECT_BOUND = "perfect-bound"
    SADDLE_STITCH = "saddle-stitch"
    CASE_BOUND = "case-bound"
    SPIRAL = "spiral"


class OutputProfile_CoverSpec(BaseModel):
    paperType: Optional[OutputProfile_CoverSpec_PaperType] = Field(default=None)
    ink: Optional[OutputProfile_CoverSpec_Ink] = Field(default=None)
    finish: Optional[OutputProfile_CoverSpec_Finish] = Field(default=None)


class OutputProfile_ProofSpec(BaseModel):
    watermark: Optional[bool] = Field(default=None)
    dpi: Optional[int] = Field(default=None)
    sizeBudgetBytes: Optional[int] = Field(default=None)


class OutputProfile_DeliverySpec_InteriorFormatItem(str, Enum):
    PDF = "pdf"
    EPUB = "epub"
    IDML = "idml"
    ONIX = "onix"


class OutputProfile_DeliverySpec_CoverFormatItem(str, Enum):
    PDF = "pdf"
    JPEG = "jpeg"


class OutputProfile_DeliverySpec(BaseModel):
    interiorFormat: Optional[list[OutputProfile_DeliverySpec_InteriorFormatItem]] = Field(default=None)
    coverFormat: Optional[list[OutputProfile_DeliverySpec_CoverFormatItem]] = Field(default=None)


class OutputProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: OutputProfile_Schema = Field(..., alias="schema")
    name: str
    vendor: OutputProfile_Vendor
    vendorProfileVersion: Optional[str] = Field(default=None)
    trimSize: OutputProfile_TrimSize
    bleed: Optional[OutputProfile_Bleed] = Field(default=None)
    pdfSpec: OutputProfile_PdfSpec
    coverSpec: Optional[OutputProfile_CoverSpec] = Field(default=None)
    proofSpec: Optional[OutputProfile_ProofSpec] = Field(default=None)
    deliverySpec: Optional[OutputProfile_DeliverySpec] = Field(default=None)
    minPages: Optional[int] = Field(default=None)
    maxPages: Optional[int] = Field(default=None)
    pageSizeMultiple: Optional[int] = Field(default=None)
    validatedAt: Optional[datetime] = Field(default=None)


class PreflightReport_Schema(str, Enum):
    PREFLIGHT_1 = "preflight/1"


class PreflightReport_Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class PreflightReport_Summary(BaseModel):
    passed: int
    failed: int
    warnings: Optional[int] = Field(default=None)
    policyViolations: Optional[int] = Field(default=None)


class PreflightReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: PreflightReport_Schema = Field(..., alias="schema")
    status: PreflightReport_Status
    profileId: Optional[str] = Field(default=None)
    pageCount: Optional[int] = Field(default=None)
    checks: list[PreflightCheck]
    summary: PreflightReport_Summary
    profileVersion: Optional[str] = Field(default=None)
    vendorTemplate: Optional[str] = Field(default=None)
    createdAt: Optional[datetime] = Field(default=None)


class BuildManifest_Schema(str, Enum):
    MANIFEST_1 = "manifest/1"


class BuildManifest_Mode(str, Enum):
    PROOF = "proof"
    FINAL = "final"


class BuildManifest_PreflightStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


class BuildManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: BuildManifest_Schema = Field(..., alias="schema")
    buildId: str
    tenantId: Optional[str] = Field(default=None)
    titleId: Optional[str] = Field(default=None)
    documentId: Optional[str] = Field(default=None)
    overrideSetId: Optional[str] = Field(default=None)
    designSpecId: Optional[str] = Field(default=None)
    profileIds: Optional[list[str]] = Field(default=None)
    mode: Optional[BuildManifest_Mode] = Field(default=None)
    toolchain: ToolchainDigest
    stageVersions: Optional[dict[str, int]] = Field(default=None)
    stages: list[StageRecord]
    artifacts: Optional[dict[str, ArtifactRef]] = Field(default=None)
    preflightStatus: Optional[BuildManifest_PreflightStatus] = Field(default=None)
    preflightReport: Optional[ArtifactRef] = Field(default=None)
    pages: Optional[int] = Field(default=None)
    reproductionKey: BuildManifest_Sha256
    startedAt: Optional[datetime] = Field(default=None)
    completedAt: Optional[datetime] = Field(default=None)
    version: Optional[int] = Field(default=None)

    @field_validator("buildId")
    @classmethod
    def _validate_buildId(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^bld-[a-zA-Z0-9_-]+$", v):
            raise ValueError("buildId does not match required pattern")
        return v


class ClassificationResult_Schema(str, Enum):
    CLASSIFICATION_1 = "classification/1"


class ClassificationResult_ModelInfo_SchemaVersion(str, Enum):
    CLASSIFICATION_1 = "classification/1"


class ClassificationResult_ModelInfo(BaseModel):
    modelId: str
    promptVersion: Optional[str] = Field(default=None)
    schemaVersion: Optional[ClassificationResult_ModelInfo_SchemaVersion] = Field(default=None)
    cacheHit: Optional[bool] = Field(default=None)
    costUsd: Optional[float] = Field(default=None)


class ClassificationResult_Suggestions_Genre(str, Enum):
    FICTION = "fiction"
    NONFICTION = "nonfiction"
    MEMOIR = "memoir"
    POETRY = "poetry"
    ACADEMIC = "academic"
    TECHNICAL = "technical"
    CHILDRENS = "childrens"
    DRAMA = "drama"


class ClassificationResult_Suggestions(BaseModel):
    genre: Optional[ClassificationResult_Suggestions_Genre] = Field(default=None)
    trimSizeSuggestion: Optional[TrimSizeSuggestion] = Field(default=None)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: ClassificationResult_Schema = Field(..., alias="schema")
    nodes: list[ClassifiedNode]
    confidences: Optional[dict[str, float]] = Field(default=None)
    modelInfo: ClassificationResult_ModelInfo
    suggestions: Optional[ClassificationResult_Suggestions] = Field(default=None)


class AgentProposal_Schema(str, Enum):
    AGENT_PROPOSAL_1 = "agent-proposal/1"


class AgentProposal_StateChanges(BaseModel):
    ruleSetVersion: Optional[str] = Field(default=None)
    exemplarSetHash: Optional[str] = Field(default=None)

    @field_validator("exemplarSetHash")
    @classmethod
    def _validate_exemplarSetHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-f0-9]{64}$", v):
            raise ValueError("exemplarSetHash does not match required pattern")
        return v


class AgentProposal_Evaluation(BaseModel):
    judgeModelId: Optional[str] = Field(default=None)
    scores: Optional[dict[str, float]] = Field(default=None)
    expertReviewed: Optional[bool] = Field(default=None)
    accepted: Optional[bool] = Field(default=None)


class AgentProposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: AgentProposal_Schema = Field(..., alias="schema")
    agentId: str
    agentVersion: Optional[str] = Field(default=None)
    promptVersion: Optional[str] = Field(default=None)
    modelId: Optional[str] = Field(default=None)
    proposals: list[Proposal]
    stateChanges: Optional[AgentProposal_StateChanges] = Field(default=None)
    evaluation: Optional[AgentProposal_Evaluation] = Field(default=None)
    costUsd: Optional[float] = Field(default=None)
    latencyMs: Optional[int] = Field(default=None)
    createdAt: Optional[datetime] = Field(default=None)


class PageMap_Schema(str, Enum):
    PAGEMAP_1 = "pagemap/1"


class PageMap(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: PageMap_Schema = Field(..., alias="schema")
    pages: list[PageEntry]
    chapters: list[ChapterEntry]


class ArtBrief(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: ArtBrief_SchemaRef = Field(..., alias="schema")
    concept: str
    subject: str
    composition: Composition
    palette: list[PaletteTone]
    lighting: Lighting
    medium: Medium
    mood: Mood
    genreSignals: list[GenreSignal]
    typeZone: TypeZone
    negative: list[NegativeTag]
    sourceRef: ArtBrief_SourceRef


class ArtProvenance_Schema(str, Enum):
    ART_PROVENANCE_1 = "art-provenance/1"


class ArtProvenance_BillingUnit(str, Enum):
    IMAGE = "image"
    MEGAPIXEL = "megapixel"
    TOKEN = "token"


class ArtProvenance_ReplayClass(str, Enum):
    SEEDED = "seeded"
    ARTIFACT_ONLY = "artifact-only"


class ArtProvenance(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: ArtProvenance_Schema = Field(..., alias="schema")
    modelId: str
    canonicalSlug: str
    providerSlug: str
    billingUnit: ArtProvenance_BillingUnit
    costUsd: float
    artBriefHash: str
    imageHash: str
    seed: Optional[Any] = Field(default=None)
    replayClass: ArtProvenance_ReplayClass
    resolution: str
    aspectRatio: str
    sampleUsed: bool
    createdAt: datetime

    @field_validator("artBriefHash")
    @classmethod
    def _validate_artBriefHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^sha256:[a-f0-9]{64}$", v):
            raise ValueError("artBriefHash does not match required pattern")
        return v

    @field_validator("imageHash")
    @classmethod
    def _validate_imageHash(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^sha256:[a-f0-9]{64}$", v):
            raise ValueError("imageHash does not match required pattern")
        return v


class CoverVerdict_Schema(str, Enum):
    COVER_VERDICT_1 = "cover-verdict/1"


class CoverVerdict_Winner(str, Enum):
    A = "a"
    B = "b"
    TIE = "tie"


class CoverVerdict_ModelInfo(BaseModel):
    modelId: str
    providerSlug: str
    promptVersion: str
    reasoningConfigSha256: Optional[str] = Field(default=None)

    @field_validator("reasoningConfigSha256")
    @classmethod
    def _validate_reasoningConfigSha256(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match("^[a-f0-9]{64}$", v):
            raise ValueError("reasoningConfigSha256 does not match required pattern")
        return v


class CoverVerdict(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: CoverVerdict_Schema = Field(..., alias="schema")
    winner: CoverVerdict_Winner
    modelInfo: CoverVerdict_ModelInfo



for _name, _obj in list(globals().items()):
    if isinstance(_obj, type) and issubclass(_obj, BaseModel):
        try:
            _obj.model_rebuild()
        except Exception:
            pass
