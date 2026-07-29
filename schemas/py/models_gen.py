# Auto-generated from JSON Schema — do not edit manually.
# Run `pnpm gen` from schemas/ to regenerate.
#
# WARNING: This is a starter codegen. For production, use a proper
# codegen pipeline (datamodel-code-generator, quicktype, etc.).
# The source of truth is the JSON Schema files in schemas/*/.

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Any, Optional, Union
import re


class Sha256(str, Enum):
    """Hex-encoded SHA-256 hash (pattern: ^[a-f0-9]{64}$)"""


class StageName(str, Enum):
    ACQUIRE = "acquire"
    SANITIZE = "sanitize"
    LEGACY_CONVERT = "legacy-convert"
    EXTRACT = "extract"
    MEDIA_NORMALIZE = "media-normalize"
    STRUCTURE_RULES = "structure-rules"
    STRUCTURE_LLM = "structure-llm"
    AST_ASSEMBLE = "ast-assemble"
    RESOLVE = "resolve"
    DESIGN_COMPILE = "design-compile"
    PAGINATE = "paginate"
    TYPO_OPTIMIZE = "typo-optimize"
    PARITY_PAD = "parity-pad"
    FINISH = "finish"
    PREFLIGHT = "preflight"
    RASTERIZE = "rasterize"
    COVER = "cover"
    EPUB = "epub"
    IDML = "idml"
    ONIX = "onix"
    PACKAGE = "package"


class ErrorKind(str, Enum):
    BAD_INPUT = "bad_input"
    POLICY_VIOLATION = "policy_violation"
    ENGINE_BUG = "engine_bug"
    INFRA = "infra"
    EXTERNAL_LIMIT = "external_limit"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class MediaType(str, Enum):
    APPLICATION_PDF = "application/pdf"
    APPLICATION_EPUB = "application/epub+zip"
    APPLICATION_IDML = "application/vnd.adobe.indesign-idml-package"
    APPLICATION_XML = "application/xml"
    APPLICATION_JSON = "application/json"
    TEXT_HTML = "text/html"
    TEXT_CSS = "text/css"
    TEXT_PLAIN = "text/plain"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_SVG = "image/svg+xml"
    IMAGE_TIFF = "image/tiff"
    FONT_TTF = "font/ttf"
    FONT_OTF = "font/otf"
    FONT_WOFF2 = "font/woff2"


class RenderEngine(str, Enum):
    CHROME_PAGEDJS = "chrome-pagedjs"
    TYPST = "typst"
    PRINCE = "prince"
    TEX = "tex"


class PageSide(str, Enum):
    RECTO = "recto"
    VERSO = "verso"


class PrintColorSpace(str, Enum):
    CMYK = "cmyk"
    GRAY = "gray"
    RGB = "rgb"


class Diagnostic(BaseModel):
    code: str
    severity: Severity
    humanMessage: str
    suggestedFix: Optional[str] = None
    sourceRef: Optional[str] = None


class SourceRef(BaseModel):
    docxId: str
    contentHash: Optional[str] = None
    fallbackText: Optional[str] = None

    @field_validator("contentHash")
    @classmethod
    def validate_contentHash(cls, v: str) -> str:
        if v is not None and not re.match(r"^[a-f0-9]{64}$", v):
            raise ValueError("contentHash must be a 64-char hex string")
        return v


class TrimSize(BaseModel):
    widthMm: float
    heightMm: float


# ── AST types ────────────────────────────────────────────────────

class ContributorRole(str, Enum):
    AUTHOR = "author"
    EDITOR = "editor"
    TRANSLATOR = "translator"
    ILLUSTRATOR = "illustrator"
    FOREWORD_BY = "forewordBy"
    INTRODUCTION_BY = "introductionBy"


class Contributor(BaseModel):
    role: ContributorRole
    givenName: Optional[str] = None
    familyName: Optional[str] = None
    displayName: str


class SourceRefLink(BaseModel):
    docxId: str
    contentHash: Optional[str] = None


class Metadata(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    contributors: Optional[list[Contributor]] = None
    language: Optional[str] = None
    isbn: Optional[str] = None
    series: Optional[dict] = None


class ASourceRef(BaseModel):
    manuscriptId: str
    manuscriptVersion: Optional[int] = None
    inferenceVersion: int
    inferenceModelId: Optional[str] = None
    createdAt: Optional[str] = None


class MediaRef(BaseModel):
    hash: str
    mediaType: str
    originalName: Optional[str] = None


class Text(BaseModel):
    type: str = "text"
    text: str
    marks: Optional[list[dict]] = None


class InlineNode(BaseModel):
    type: str
    text: Optional[str] = None
    content: Optional[list["InlineNode"]] = None
    attrs: Optional[dict] = None


class BlockNode(BaseModel):
    type: str
    attrs: Optional[dict] = None
    content: Optional[list["BlockNode"]] = None
    sourceRef: Optional[SourceRefLink] = None


class Chapter(BaseModel):
    type: str = "chapter"
    attrs: dict
    content: list[BlockNode]
    sourceRef: Optional[SourceRefLink] = None


class Part(BaseModel):
    type: str = "part"
    attrs: dict
    content: list[Chapter]
    sourceRef: Optional[SourceRefLink] = None


class BookAst(BaseModel):
    schema: str = "ast/1"
    metadata: Optional[Metadata] = None
    frontMatter: Optional[list[BlockNode]] = None
    body: list[BlockNode | Chapter | Part]
    backMatter: Optional[list[BlockNode]] = None
    integrityHash: str
    sourceRef: ASourceRef


# ── Override types ───────────────────────────────────────────────

class OverrideOpType(str, Enum):
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
    id: str
    sourceRef: SourceRef
    op: OverrideOpType
    path: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    value: Optional[Any] = None
    rationale: Optional[str] = None
    actor: str
    at: str


class OrphanedOp(BaseModel):
    op: OverrideOp
    reason: str


class OverrideSet(BaseModel):
    schema: str = "overrides/1"
    documentId: str
    astVersion: int
    ops: list[OverrideOp]
    orphanedOps: Optional[list[OrphanedOp]] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


# ── DesignSpec types ─────────────────────────────────────────────

class FontSource(str, Enum):
    BUNDLED_OFL = "bundled_ofl"
    TENANT_UPLOAD = "tenant_upload"
    LICENSED_SERVER = "licensed_server"


class FontSpec(BaseModel):
    family: str
    style: Optional[str] = None
    weight: Optional[int] = None
    opticalSize: Optional[float] = None
    source: FontSource
    licenseRef: Optional[str] = None


class FontRef(BaseModel):
    family: str
    style: Optional[str] = None


class TrimSizeSpec(BaseModel):
    width: float
    height: float
    unit: str = "mm"


class Typography(BaseModel):
    bodyFont: FontRef
    headingFont: Optional[FontRef] = None
    bodySize: float
    leading: float
    scaleRatio: Optional[float] = None
    measure: int
    bodyAlignment: str = "justified"
    paragraphIndent: Optional[float] = None
    paragraphSpacing: Optional[float] = None
    opticalMargins: bool = True


class Grid(BaseModel):
    type: str = "single"
    baselineIncrement: Optional[float] = None
    linesPerPage: Optional[int] = None


class Margins(BaseModel):
    top: float
    bottom: float
    inside: float
    outside: float
    gutter: Optional[float] = None


class Folio(BaseModel):
    position: str = "bottom-center"
    style: str = "arabic"
    suppressOn: Optional[list[str]] = None
    startNumber: int = 1
    prefix: Optional[str] = None
    suffix: Optional[str] = None


class ChapterOpenings(BaseModel):
    startsOn: str = "recto"
    dropCap: bool = True
    dropCapLines: int = 3
    titleTreatment: str = "centered"
    ornament: Optional[str] = None
    firstParagraphStyle: str = "no-indent"


class DesignSpec(BaseModel):
    schema: str = "designspec/1"
    name: str
    templateId: Optional[str] = None
    preferredEngine: str = "typst"
    trimSize: TrimSizeSpec
    typography: Typography
    grid: Grid
    margins: Margins
    folio: Optional[Folio] = None
    runningHeads: Optional[dict] = None
    chapterOpenings: Optional[ChapterOpenings] = None
    fonts: list[FontSpec]
    ornaments: Optional[dict] = None
    hyphenation: Optional[dict] = None
    colors: Optional[dict] = None
    metadata: Optional[dict] = None


# ── Profile types ────────────────────────────────────────────────

class PdfStandard(str, Enum):
    NONE = "none"
    PDFX_1A = "pdfx-1a"
    PDFX_3 = "pdfx-3"
    PDFX_4 = "pdfx-4"
    PDFA_2B = "pdfa-2b"


class PdfSpec(BaseModel):
    version: str = "1.7"
    standard: PdfStandard
    colorSpace: PrintColorSpace
    outputIntent: Optional[dict] = None


class OutputProfile(BaseModel):
    schema: str = "profile/1"
    name: str
    vendor: str
    vendorProfileVersion: Optional[str] = None
    trimSize: TrimSizeSpec
    bleed: Optional[dict] = None
    pdfSpec: PdfSpec
    coverSpec: Optional[dict] = None
    proofSpec: Optional[dict] = None
    deliverySpec: Optional[dict] = None
    minPages: Optional[int] = None
    maxPages: Optional[int] = None
    pageSizeMultiple: int = 4
    validatedAt: Optional[str] = None


# ── Preflight types ──────────────────────────────────────────────

class PreflightCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


class PreflightCheck(BaseModel):
    code: str
    status: PreflightCheckStatus
    severity: Severity
    humanMessage: str
    suggestedFix: Optional[str] = None
    sourceRef: Optional[str] = None
    value: Optional[Any] = None
    expected: Optional[Any] = None
    policyUrl: Optional[str] = None


class PreflightSummary(BaseModel):
    passed: int
    failed: int
    warnings: Optional[int] = None
    policyViolations: Optional[int] = None


class PreflightReport(BaseModel):
    schema: str = "preflight/1"
    status: str
    profileId: Optional[str] = None
    pageCount: Optional[int] = None
    checks: list[PreflightCheck]
    summary: PreflightSummary
    profileVersion: Optional[str] = None
    vendorTemplate: Optional[str] = None
    createdAt: Optional[str] = None


# ── Manifest types ───────────────────────────────────────────────

class ArtifactRefManifest(BaseModel):
    hash: str
    mediaType: str
    size: int
    path: Optional[str] = None


class ToolchainDigest(BaseModel):
    images: dict[str, str]
    fonts: Optional[str] = None
    icc: Optional[dict[str, str]] = None
    hyphen: Optional[dict[str, str]] = None
    engines: dict[str, str]
    schema: Optional[str] = None
    exemplarSetHash: Optional[str] = None
    ruleSetVersion: Optional[str] = None
    promptVersion: Optional[str] = None
    modelId: Optional[str] = None


class StageErrorManifest(BaseModel):
    kind: ErrorKind
    message: str
    diagnostics: Optional[list[Diagnostic]] = None
    retryCount: int = 0
    retryable: bool = False


class StageRecord(BaseModel):
    name: str
    version: int
    status: str
    cacheHit: Optional[bool] = None
    cacheKey: Optional[str] = None
    inputs: Optional[list[dict]] = None
    outputs: Optional[list[dict]] = None
    metrics: Optional[dict[str, float]] = None
    warnings: Optional[list[Diagnostic]] = None
    error: Optional[StageErrorManifest] = None
    durationMs: Optional[int] = None
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None


class BuildManifest(BaseModel):
    schema: str = "manifest/1"
    buildId: str
    tenantId: Optional[str] = None
    titleId: Optional[str] = None
    documentId: Optional[str] = None
    overrideSetId: Optional[str] = None
    designSpecId: Optional[str] = None
    profileIds: Optional[list[str]] = None
    mode: Optional[str] = None
    toolchain: ToolchainDigest
    stageVersions: Optional[dict[str, int]] = None
    stages: list[StageRecord]
    artifacts: Optional[dict[str, ArtifactRefManifest]] = None
    preflightStatus: Optional[str] = None
    preflightReport: Optional[ArtifactRefManifest] = None
    pages: Optional[int] = None
    reproductionKey: str
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    version: int = 1


# ── Classification types ─────────────────────────────────────────

class ClassificationNodeType(str, Enum):
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
    UNCERTAIN = "uncertain"


class ClassifiedNode(BaseModel):
    sourceRef: str
    classification: ClassificationNodeType
    confidence: float


class ClassificationModelInfo(BaseModel):
    modelId: str
    promptVersion: Optional[str] = None
    schemaVersion: Optional[str] = None
    cacheHit: Optional[bool] = None
    costUsd: Optional[float] = None


class ClassificationResult(BaseModel):
    schema: str = "classification/1"
    nodes: list[ClassifiedNode]
    confidences: Optional[dict[str, float]] = None
    modelInfo: ClassificationModelInfo
    suggestions: Optional[dict] = None


# ── Agent Proposal types ─────────────────────────────────────────

class ProposalType(str, Enum):
    RECLASSIFY = "reclassify"
    MERGE_CHAPTERS = "merge_chapters"
    SPLIT_CHAPTER = "split_chapter"
    ADJUST_HEADING_LEVEL = "adjust_heading_level"
    IDENTIFY_FRONT_MATTER = "identify_front_matter"
    IDENTIFY_BACK_MATTER = "identify_back_matter"
    FLAG_AMBIGUITY = "flag_ambiguity"
    SUGGEST_TITLE = "suggest_title"


class Proposal(BaseModel):
    id: str
    type: ProposalType
    sourceRef: SourceRef
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    value: Optional[Any] = None
    rationale: str
    confidence: Optional[float] = None
    evidence: Optional[list[str]] = None


class Evaluation(BaseModel):
    judgeModelId: Optional[str] = None
    scores: Optional[dict[str, float]] = None
    expertReviewed: Optional[bool] = None
    accepted: Optional[bool] = None


class AgentProposal(BaseModel):
    schema: str = "agent-proposal/1"
    agentId: str
    agentVersion: Optional[str] = None
    promptVersion: Optional[str] = None
    modelId: Optional[str] = None
    proposals: list[Proposal]
    stateChanges: Optional[dict] = None
    evaluation: Optional[Evaluation] = None
    costUsd: Optional[float] = None
    latencyMs: Optional[int] = None
    createdAt: Optional[str] = None


# ── PageMap types ────────────────────────────────────────────────

class PageEntry(BaseModel):
    pageNumber: int
    folio: int
    side: PageSide
    widthPt: float
    heightPt: float
    chapterId: str
    sectionId: Optional[str] = None
    type: Optional[str] = None
    contentStart: Optional[float] = None
    hasOrphans: Optional[bool] = None
    hasWidows: Optional[bool] = None
    hasRunts: Optional[bool] = None
    wordCount: Optional[int] = None
    defectScore: Optional[float] = None
    paraRanges: Optional[list[dict]] = None


class ChapterEntry(BaseModel):
    chapterId: str
    number: int
    title: Optional[str] = None
    startPage: int
    endPage: int
    pageCount: int
    startsOn: Optional[str] = None


class PageMap(BaseModel):
    schema: str = "pagemap/1"
    pages: list[PageEntry]
    chapters: list[ChapterEntry]
