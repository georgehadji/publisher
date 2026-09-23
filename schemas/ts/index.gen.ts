// Auto-generated from JSON Schema — do not edit manually.
// Run `node codegen/generate.mjs` from schemas/ to regenerate.

import { z } from 'zod';

export const BookAST_SchemaRefSchema = z.enum(["ast/1"]);
export type BookAST_SchemaRef = z.infer<typeof BookAST_SchemaRefSchema>;

export const ArtBrief_SchemaRefSchema = z.enum(["art-brief/1"]);
export type ArtBrief_SchemaRef = z.infer<typeof ArtBrief_SchemaRefSchema>;

export const BookAST_SourceRefSchema = z.object({
  "manuscriptId": z.string().min(1).max(128),
  "manuscriptVersion": z.number().int().min(1).optional(),
  "inferenceVersion": z.number().int().min(1),
  "inferenceModelId": z.string().optional(),
  "createdAt": z.string().datetime().optional(),
});
export type BookAST_SourceRef = z.infer<typeof BookAST_SourceRefSchema>;

export const ArtBrief_SourceRefSchema = z.object({
  "titleMetaHash": z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$")),
  "designSpecHash": z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$")),
  "manuscriptSampleUsed": z.boolean(),
});
export type ArtBrief_SourceRef = z.infer<typeof ArtBrief_SourceRefSchema>;

export const Contributor_RoleSchema: z.ZodTypeAny = z.enum(["author", "editor", "translator", "illustrator", "forewordBy", "introductionBy"]);
export type Contributor_Role = z.infer<typeof Contributor_RoleSchema>;

export const ContributorSchema = z.object({
  "role": z.lazy(() => Contributor_RoleSchema),
  "givenName": z.string().max(256).optional(),
  "familyName": z.string().max(256).optional(),
  "displayName": z.string().max(256),
});
export type Contributor = z.infer<typeof ContributorSchema>;

export const Metadata_SeriesSchema: z.ZodTypeAny = z.object({
  "name": z.string().max(256),
  "volume": z.number().int().min(1).optional(),
});
export type Metadata_Series = z.infer<typeof Metadata_SeriesSchema>;

export const MetadataSchema = z.object({
  "title": z.string().max(1024).optional(),
  "subtitle": z.string().max(1024).optional(),
  "contributors": z.array(ContributorSchema).max(200).optional(),
  "language": z.string().regex(new RegExp("^[a-z]{2,3}(-[A-Z]{2})?$")).optional(),
  "isbn": z.string().regex(new RegExp("^(978|979)[0-9]{10}$")).optional(),
  "series": z.lazy(() => Metadata_SeriesSchema).optional(),
});
export type Metadata = z.infer<typeof MetadataSchema>;

export const SourceRefLinkSchema = z.object({
  "docxId": z.string().max(128),
  "contentHash": z.string().regex(new RegExp("^[a-f0-9]{64}$")).optional(),
});
export type SourceRefLink = z.infer<typeof SourceRefLinkSchema>;

export const ConfidenceSchema = z.number().min(0).max(1);
export type Confidence = z.infer<typeof ConfidenceSchema>;

export const Part_TypeSchema: z.ZodTypeAny = z.enum(["part"]);
export type Part_Type = z.infer<typeof Part_TypeSchema>;

export const Part_AttrsSchema: z.ZodTypeAny = z.object({
  "number": z.number().int().min(1).optional(),
  "title": z.string().max(512),
  "id": z.string().regex(new RegExp("^[a-zA-Z0-9_-]+$")),
});
export type Part_Attrs = z.infer<typeof Part_AttrsSchema>;

export const Chapter_TypeSchema: z.ZodTypeAny = z.enum(["chapter"]);
export type Chapter_Type = z.infer<typeof Chapter_TypeSchema>;

export const Chapter_Attrs_StartsOnSchema: z.ZodTypeAny = z.enum(["recto", "verso", "any"]);
export type Chapter_Attrs_StartsOn = z.infer<typeof Chapter_Attrs_StartsOnSchema>;

export const Chapter_AttrsSchema: z.ZodTypeAny = z.object({
  "number": z.number().int().min(1),
  "title": z.string().max(512).optional(),
  "startsOn": z.lazy(() => Chapter_Attrs_StartsOnSchema).optional(),
  "id": z.string().regex(new RegExp("^[a-zA-Z0-9_-]+$")),
});
export type Chapter_Attrs = z.infer<typeof Chapter_AttrsSchema>;

export const Paragraph_TypeSchema: z.ZodTypeAny = z.enum(["paragraph"]);
export type Paragraph_Type = z.infer<typeof Paragraph_TypeSchema>;

export const Paragraph_Attrs_RoleSchema: z.ZodTypeAny = z.enum(["normal", "chapter-opening", "first-paragraph", "last-paragraph", "continued", "attribution", "source"]);
export type Paragraph_Attrs_Role = z.infer<typeof Paragraph_Attrs_RoleSchema>;

export const Paragraph_Attrs_AlignmentSchema: z.ZodTypeAny = z.enum(["left", "center", "right", "justify"]);
export type Paragraph_Attrs_Alignment = z.infer<typeof Paragraph_Attrs_AlignmentSchema>;

export const Paragraph_AttrsSchema: z.ZodTypeAny = z.object({
  "role": z.lazy(() => Paragraph_Attrs_RoleSchema).optional(),
  "indent": z.number().min(0).optional(),
  "alignment": z.lazy(() => Paragraph_Attrs_AlignmentSchema).optional(),
  "language": z.string().regex(new RegExp("^[a-z]{2,3}(-[A-Z]{2})?$")).optional(),
});
export type Paragraph_Attrs = z.infer<typeof Paragraph_AttrsSchema>;

export const Text_TypeSchema: z.ZodTypeAny = z.enum(["text"]);
export type Text_Type = z.infer<typeof Text_TypeSchema>;

export const Mark_TypeSchema: z.ZodTypeAny = z.enum(["emphasis", "strong", "smallCaps", "underline", "strikethrough", "superscript", "subscript", "code", "link"]);
export type Mark_Type = z.infer<typeof Mark_TypeSchema>;

export const Mark_AttrsSchema: z.ZodTypeAny = z.object({
  "href": z.string().url().optional(),
  "title": z.string().max(256).optional(),
});
export type Mark_Attrs = z.infer<typeof Mark_AttrsSchema>;

export const MarkSchema = z.object({
  "type": z.lazy(() => Mark_TypeSchema),
  "attrs": z.lazy(() => Mark_AttrsSchema).optional(),
});
export type Mark = z.infer<typeof MarkSchema>;

export const TextSchema = z.object({
  "type": z.lazy(() => Text_TypeSchema),
  "text": z.string(),
  "marks": z.array(MarkSchema).max(10).optional(),
});
export type Text = z.infer<typeof TextSchema>;

export const Emphasis_TypeSchema: z.ZodTypeAny = z.enum(["emphasis"]);
export type Emphasis_Type = z.infer<typeof Emphasis_TypeSchema>;

export const Emphasis_Attrs_RoleSchema: z.ZodTypeAny = z.enum(["emphasis", "title-of-work", "foreign", "thought"]);
export type Emphasis_Attrs_Role = z.infer<typeof Emphasis_Attrs_RoleSchema>;

export const Emphasis_AttrsSchema: z.ZodTypeAny = z.object({
  "role": z.lazy(() => Emphasis_Attrs_RoleSchema).optional(),
});
export type Emphasis_Attrs = z.infer<typeof Emphasis_AttrsSchema>;

export const EmphasisSchema = z.object({
  "type": z.lazy(() => Emphasis_TypeSchema),
  "attrs": z.lazy(() => Emphasis_AttrsSchema).optional(),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Emphasis = z.infer<typeof EmphasisSchema>;

export const Strong_TypeSchema: z.ZodTypeAny = z.enum(["strong"]);
export type Strong_Type = z.infer<typeof Strong_TypeSchema>;

export const StrongSchema = z.object({
  "type": z.lazy(() => Strong_TypeSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Strong = z.infer<typeof StrongSchema>;

export const Link_TypeSchema: z.ZodTypeAny = z.enum(["link"]);
export type Link_Type = z.infer<typeof Link_TypeSchema>;

export const Link_Attrs_TargetSchema: z.ZodTypeAny = z.enum(["_self", "_blank", "_top"]);
export type Link_Attrs_Target = z.infer<typeof Link_Attrs_TargetSchema>;

export const Link_AttrsSchema: z.ZodTypeAny = z.object({
  "href": z.string().url(),
  "title": z.string().max(256).optional(),
  "target": z.lazy(() => Link_Attrs_TargetSchema).optional(),
});
export type Link_Attrs = z.infer<typeof Link_AttrsSchema>;

export const LinkSchema = z.object({
  "type": z.lazy(() => Link_TypeSchema),
  "attrs": z.lazy(() => Link_AttrsSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Link = z.infer<typeof LinkSchema>;

export const Superscript_TypeSchema: z.ZodTypeAny = z.enum(["superscript"]);
export type Superscript_Type = z.infer<typeof Superscript_TypeSchema>;

export const SuperscriptSchema = z.object({
  "type": z.lazy(() => Superscript_TypeSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Superscript = z.infer<typeof SuperscriptSchema>;

export const Subscript_TypeSchema: z.ZodTypeAny = z.enum(["subscript"]);
export type Subscript_Type = z.infer<typeof Subscript_TypeSchema>;

export const SubscriptSchema = z.object({
  "type": z.lazy(() => Subscript_TypeSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Subscript = z.infer<typeof SubscriptSchema>;

export const SmallCaps_TypeSchema: z.ZodTypeAny = z.enum(["smallCaps"]);
export type SmallCaps_Type = z.infer<typeof SmallCaps_TypeSchema>;

export const SmallCapsSchema = z.object({
  "type": z.lazy(() => SmallCaps_TypeSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type SmallCaps = z.infer<typeof SmallCapsSchema>;

export const CodeInline_TypeSchema: z.ZodTypeAny = z.enum(["codeInline"]);
export type CodeInline_Type = z.infer<typeof CodeInline_TypeSchema>;

export const CodeInlineSchema = z.object({
  "type": z.lazy(() => CodeInline_TypeSchema),
  "text": z.string(),
});
export type CodeInline = z.infer<typeof CodeInlineSchema>;

export const HardBreak_TypeSchema: z.ZodTypeAny = z.enum(["hardBreak"]);
export type HardBreak_Type = z.infer<typeof HardBreak_TypeSchema>;

export const HardBreakSchema = z.object({
  "type": z.lazy(() => HardBreak_TypeSchema),
});
export type HardBreak = z.infer<typeof HardBreakSchema>;

export const IndexEntry_TypeSchema: z.ZodTypeAny = z.enum(["indexEntry"]);
export type IndexEntry_Type = z.infer<typeof IndexEntry_TypeSchema>;

export const IndexEntry_AttrsSchema: z.ZodTypeAny = z.object({
  "term": z.string().max(128),
  "subterm": z.string().max(128).optional(),
  "see": z.string().max(128).optional(),
  "seeAlso": z.string().max(128).optional(),
});
export type IndexEntry_Attrs = z.infer<typeof IndexEntry_AttrsSchema>;

export const IndexEntrySchema = z.object({
  "type": z.lazy(() => IndexEntry_TypeSchema),
  "attrs": z.lazy(() => IndexEntry_AttrsSchema),
});
export type IndexEntry = z.infer<typeof IndexEntrySchema>;

export const CrossReference_TypeSchema: z.ZodTypeAny = z.enum(["crossReference"]);
export type CrossReference_Type = z.infer<typeof CrossReference_TypeSchema>;

export const CrossReference_Attrs_DisplaySchema: z.ZodTypeAny = z.enum(["number", "title", "page", "number-and-title"]);
export type CrossReference_Attrs_Display = z.infer<typeof CrossReference_Attrs_DisplaySchema>;

export const CrossReference_AttrsSchema: z.ZodTypeAny = z.object({
  "target": z.string().max(64),
  "display": z.lazy(() => CrossReference_Attrs_DisplaySchema).optional(),
});
export type CrossReference_Attrs = z.infer<typeof CrossReference_AttrsSchema>;

export const CrossReferenceSchema = z.object({
  "type": z.lazy(() => CrossReference_TypeSchema),
  "attrs": z.lazy(() => CrossReference_AttrsSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type CrossReference = z.infer<typeof CrossReferenceSchema>;

export const InlineNodeSchema: z.ZodTypeAny = z.union([TextSchema, EmphasisSchema, StrongSchema, LinkSchema, SuperscriptSchema, SubscriptSchema, SmallCapsSchema, CodeInlineSchema, HardBreakSchema, IndexEntrySchema, CrossReferenceSchema]);
export type InlineNode = z.infer<typeof InlineNodeSchema>;

export const ParagraphSchema = z.object({
  "type": z.lazy(() => Paragraph_TypeSchema),
  "attrs": z.lazy(() => Paragraph_AttrsSchema).optional(),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Paragraph = z.infer<typeof ParagraphSchema>;

export const Heading_TypeSchema: z.ZodTypeAny = z.enum(["heading"]);
export type Heading_Type = z.infer<typeof Heading_TypeSchema>;

export const Heading_Attrs_RoleSchema: z.ZodTypeAny = z.enum(["section", "subsection", "subsubsection", "running-head"]);
export type Heading_Attrs_Role = z.infer<typeof Heading_Attrs_RoleSchema>;

export const Heading_AttrsSchema: z.ZodTypeAny = z.object({
  "level": z.number().int().min(1).max(6),
  "role": z.lazy(() => Heading_Attrs_RoleSchema).optional(),
});
export type Heading_Attrs = z.infer<typeof Heading_AttrsSchema>;

export const HeadingSchema = z.object({
  "type": z.lazy(() => Heading_TypeSchema),
  "attrs": z.lazy(() => Heading_AttrsSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Heading = z.infer<typeof HeadingSchema>;

export const Blockquote_TypeSchema: z.ZodTypeAny = z.enum(["blockquote"]);
export type Blockquote_Type = z.infer<typeof Blockquote_TypeSchema>;

export const Blockquote_Attrs_RoleSchema: z.ZodTypeAny = z.enum(["pull-quote", "extract", "letter", "prayer"]);
export type Blockquote_Attrs_Role = z.infer<typeof Blockquote_Attrs_RoleSchema>;

export const Blockquote_AttrsSchema: z.ZodTypeAny = z.object({
  "role": z.lazy(() => Blockquote_Attrs_RoleSchema).optional(),
  "source": z.string().max(256).optional(),
});
export type Blockquote_Attrs = z.infer<typeof Blockquote_AttrsSchema>;

export const BlockquoteSchema = z.object({
  "type": z.lazy(() => Blockquote_TypeSchema),
  "attrs": z.lazy(() => Blockquote_AttrsSchema).optional(),
  "content": z.array(ParagraphSchema).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Blockquote = z.infer<typeof BlockquoteSchema>;

export const Verse_TypeSchema: z.ZodTypeAny = z.enum(["verse"]);
export type Verse_Type = z.infer<typeof Verse_TypeSchema>;

export const Verse_Attrs_RoleSchema: z.ZodTypeAny = z.enum(["poem", "song-lyrics"]);
export type Verse_Attrs_Role = z.infer<typeof Verse_Attrs_RoleSchema>;

export const Verse_AttrsSchema: z.ZodTypeAny = z.object({
  "role": z.lazy(() => Verse_Attrs_RoleSchema).optional(),
});
export type Verse_Attrs = z.infer<typeof Verse_AttrsSchema>;

export const Verse_ContentItem_TypeSchema: z.ZodTypeAny = z.enum(["line"]);
export type Verse_ContentItem_Type = z.infer<typeof Verse_ContentItem_TypeSchema>;

export const Verse_ContentItemSchema: z.ZodTypeAny = z.object({
  "type": z.lazy(() => Verse_ContentItem_TypeSchema),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
});
export type Verse_ContentItem = z.infer<typeof Verse_ContentItemSchema>;

export const VerseSchema = z.object({
  "type": z.lazy(() => Verse_TypeSchema),
  "attrs": z.lazy(() => Verse_AttrsSchema).optional(),
  "content": z.array(z.lazy(() => Verse_ContentItemSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Verse = z.infer<typeof VerseSchema>;

export const List_TypeSchema: z.ZodTypeAny = z.enum(["list"]);
export type List_Type = z.infer<typeof List_TypeSchema>;

export const List_Attrs_ListTypeSchema: z.ZodTypeAny = z.enum(["ordered", "unordered"]);
export type List_Attrs_ListType = z.infer<typeof List_Attrs_ListTypeSchema>;

export const List_AttrsSchema: z.ZodTypeAny = z.object({
  "listType": z.lazy(() => List_Attrs_ListTypeSchema),
  "start": z.number().int().min(1).optional(),
  "tight": z.boolean().optional(),
});
export type List_Attrs = z.infer<typeof List_AttrsSchema>;

export const List_ContentItem_TypeSchema: z.ZodTypeAny = z.enum(["list-item"]);
export type List_ContentItem_Type = z.infer<typeof List_ContentItem_TypeSchema>;

export const List_ContentItemSchema: z.ZodTypeAny = z.object({
  "type": z.lazy(() => List_ContentItem_TypeSchema),
  "content": z.array(z.lazy(() => BlockNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type List_ContentItem = z.infer<typeof List_ContentItemSchema>;

export const ListSchema = z.object({
  "type": z.lazy(() => List_TypeSchema),
  "attrs": z.lazy(() => List_AttrsSchema),
  "content": z.array(z.lazy(() => List_ContentItemSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type List = z.infer<typeof ListSchema>;

export const Table_TypeSchema: z.ZodTypeAny = z.enum(["table"]);
export type Table_Type = z.infer<typeof Table_TypeSchema>;

export const Table_Attrs_ColgroupItem_AlignSchema: z.ZodTypeAny = z.enum(["left", "center", "right"]);
export type Table_Attrs_ColgroupItem_Align = z.infer<typeof Table_Attrs_ColgroupItem_AlignSchema>;

export const Table_Attrs_ColgroupItemSchema: z.ZodTypeAny = z.object({
  "width": z.string().optional(),
  "align": z.lazy(() => Table_Attrs_ColgroupItem_AlignSchema).optional(),
});
export type Table_Attrs_ColgroupItem = z.infer<typeof Table_Attrs_ColgroupItemSchema>;

export const Table_AttrsSchema: z.ZodTypeAny = z.object({
  "caption": z.string().max(512).optional(),
  "colgroup": z.array(z.lazy(() => Table_Attrs_ColgroupItemSchema)).max(100).optional(),
});
export type Table_Attrs = z.infer<typeof Table_AttrsSchema>;

export const TableRow_TypeSchema: z.ZodTypeAny = z.enum(["table-row"]);
export type TableRow_Type = z.infer<typeof TableRow_TypeSchema>;

export const TableRow_AttrsSchema: z.ZodTypeAny = z.object({
  "header": z.boolean().optional(),
});
export type TableRow_Attrs = z.infer<typeof TableRow_AttrsSchema>;

export const TableCell_TypeSchema: z.ZodTypeAny = z.enum(["table-cell"]);
export type TableCell_Type = z.infer<typeof TableCell_TypeSchema>;

export const TableCell_Attrs_AlignSchema: z.ZodTypeAny = z.enum(["left", "center", "right"]);
export type TableCell_Attrs_Align = z.infer<typeof TableCell_Attrs_AlignSchema>;

export const TableCell_AttrsSchema: z.ZodTypeAny = z.object({
  "colspan": z.number().int().min(1).max(20).optional(),
  "rowspan": z.number().int().min(1).max(20).optional(),
  "align": z.lazy(() => TableCell_Attrs_AlignSchema).optional(),
});
export type TableCell_Attrs = z.infer<typeof TableCell_AttrsSchema>;

export const TableCellSchema = z.object({
  "type": z.lazy(() => TableCell_TypeSchema),
  "attrs": z.lazy(() => TableCell_AttrsSchema).optional(),
  "content": z.array(z.lazy(() => BlockNodeSchema)).min(1),
});
export type TableCell = z.infer<typeof TableCellSchema>;

export const TableRowSchema = z.object({
  "type": z.lazy(() => TableRow_TypeSchema),
  "attrs": z.lazy(() => TableRow_AttrsSchema).optional(),
  "content": z.array(TableCellSchema).min(1),
});
export type TableRow = z.infer<typeof TableRowSchema>;

export const TableSchema = z.object({
  "type": z.lazy(() => Table_TypeSchema),
  "attrs": z.lazy(() => Table_AttrsSchema).optional(),
  "content": z.array(TableRowSchema).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Table = z.infer<typeof TableSchema>;

export const Figure_TypeSchema: z.ZodTypeAny = z.enum(["figure"]);
export type Figure_Type = z.infer<typeof Figure_TypeSchema>;

export const BookAST_Sha256Schema = z.string().regex(new RegExp("^[a-f0-9]{64}$"));
export type BookAST_Sha256 = z.infer<typeof BookAST_Sha256Schema>;

export const MediaRef_MediaTypeSchema: z.ZodTypeAny = z.enum(["image/png", "image/jpeg", "image/svg+xml", "image/tiff"]);
export type MediaRef_MediaType = z.infer<typeof MediaRef_MediaTypeSchema>;

export const MediaRefSchema = z.object({
  "hash": BookAST_Sha256Schema,
  "mediaType": z.lazy(() => MediaRef_MediaTypeSchema),
  "originalName": z.string().max(256).optional(),
});
export type MediaRef = z.infer<typeof MediaRefSchema>;

export const Figure_Attrs_PlacementSchema: z.ZodTypeAny = z.enum(["inline", "float-left", "float-right", "full-page"]);
export type Figure_Attrs_Placement = z.infer<typeof Figure_Attrs_PlacementSchema>;

export const Figure_AttrsSchema: z.ZodTypeAny = z.object({
  "mediaRef": MediaRefSchema,
  "caption": z.string().max(1024).optional(),
  "altText": z.string().max(2048).optional(),
  "width": z.string().optional(),
  "placement": z.lazy(() => Figure_Attrs_PlacementSchema).optional(),
});
export type Figure_Attrs = z.infer<typeof Figure_AttrsSchema>;

export const FigureSchema = z.object({
  "type": z.lazy(() => Figure_TypeSchema),
  "attrs": z.lazy(() => Figure_AttrsSchema),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Figure = z.infer<typeof FigureSchema>;

export const Footnote_TypeSchema: z.ZodTypeAny = z.enum(["footnote"]);
export type Footnote_Type = z.infer<typeof Footnote_TypeSchema>;

export const Footnote_AttrsSchema: z.ZodTypeAny = z.object({
  "number": z.number().int().min(1).optional(),
  "symbol": z.string().max(4).optional(),
});
export type Footnote_Attrs = z.infer<typeof Footnote_AttrsSchema>;

export const FootnoteSchema = z.object({
  "type": z.lazy(() => Footnote_TypeSchema),
  "attrs": z.lazy(() => Footnote_AttrsSchema).optional(),
  "content": z.array(z.lazy(() => InlineNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Footnote = z.infer<typeof FootnoteSchema>;

export const Epigraph_TypeSchema: z.ZodTypeAny = z.enum(["epigraph"]);
export type Epigraph_Type = z.infer<typeof Epigraph_TypeSchema>;

export const Epigraph_AttrsSchema: z.ZodTypeAny = z.object({
  "source": z.string().max(256).optional(),
});
export type Epigraph_Attrs = z.infer<typeof Epigraph_AttrsSchema>;

export const EpigraphSchema = z.object({
  "type": z.lazy(() => Epigraph_TypeSchema),
  "attrs": z.lazy(() => Epigraph_AttrsSchema).optional(),
  "content": z.array(ParagraphSchema).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Epigraph = z.infer<typeof EpigraphSchema>;

export const SceneBreak_TypeSchema: z.ZodTypeAny = z.enum(["sceneBreak"]);
export type SceneBreak_Type = z.infer<typeof SceneBreak_TypeSchema>;

export const SceneBreak_Attrs_OrnamentSchema: z.ZodTypeAny = z.enum(["dinkus", "asterism", "fleuron", "blank-line", "section-symbol"]);
export type SceneBreak_Attrs_Ornament = z.infer<typeof SceneBreak_Attrs_OrnamentSchema>;

export const SceneBreak_AttrsSchema: z.ZodTypeAny = z.object({
  "ornament": z.lazy(() => SceneBreak_Attrs_OrnamentSchema).optional(),
});
export type SceneBreak_Attrs = z.infer<typeof SceneBreak_AttrsSchema>;

export const SceneBreakSchema = z.object({
  "type": z.lazy(() => SceneBreak_TypeSchema),
  "attrs": z.lazy(() => SceneBreak_AttrsSchema).optional(),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type SceneBreak = z.infer<typeof SceneBreakSchema>;

export const Dialogue_TypeSchema: z.ZodTypeAny = z.enum(["dialogue"]);
export type Dialogue_Type = z.infer<typeof Dialogue_TypeSchema>;

export const Dialogue_AttrsSchema: z.ZodTypeAny = z.object({
  "speaker": z.string().max(128).optional(),
});
export type Dialogue_Attrs = z.infer<typeof Dialogue_AttrsSchema>;

export const DialogueSchema = z.object({
  "type": z.lazy(() => Dialogue_TypeSchema),
  "attrs": z.lazy(() => Dialogue_AttrsSchema).optional(),
  "content": z.array(ParagraphSchema).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Dialogue = z.infer<typeof DialogueSchema>;

export const Sidebar_TypeSchema: z.ZodTypeAny = z.enum(["sidebar"]);
export type Sidebar_Type = z.infer<typeof Sidebar_TypeSchema>;

export const SidebarSchema = z.object({
  "type": z.lazy(() => Sidebar_TypeSchema),
  "content": z.array(z.lazy(() => BlockNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Sidebar = z.infer<typeof SidebarSchema>;

export const Code_TypeSchema: z.ZodTypeAny = z.enum(["code"]);
export type Code_Type = z.infer<typeof Code_TypeSchema>;

export const Code_AttrsSchema: z.ZodTypeAny = z.object({
  "language": z.string().max(32).optional(),
  "numbered": z.boolean().optional(),
});
export type Code_Attrs = z.infer<typeof Code_AttrsSchema>;

export const CodeSchema = z.object({
  "type": z.lazy(() => Code_TypeSchema),
  "attrs": z.lazy(() => Code_AttrsSchema).optional(),
  "content": z.string(),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Code = z.infer<typeof CodeSchema>;

export const Equation_TypeSchema: z.ZodTypeAny = z.enum(["equation"]);
export type Equation_Type = z.infer<typeof Equation_TypeSchema>;

export const Equation_AttrsSchema: z.ZodTypeAny = z.object({
  "numbered": z.boolean().optional(),
  "label": z.string().max(64).optional(),
});
export type Equation_Attrs = z.infer<typeof Equation_AttrsSchema>;

export const EquationSchema = z.object({
  "type": z.lazy(() => Equation_TypeSchema),
  "attrs": z.lazy(() => Equation_AttrsSchema).optional(),
  "content": z.string(),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Equation = z.infer<typeof EquationSchema>;

export const PageBreak_TypeSchema: z.ZodTypeAny = z.enum(["pageBreak"]);
export type PageBreak_Type = z.infer<typeof PageBreak_TypeSchema>;

export const PageBreak_Attrs_BreakTypeSchema: z.ZodTypeAny = z.enum(["page", "column", "section", "recto", "verso"]);
export type PageBreak_Attrs_BreakType = z.infer<typeof PageBreak_Attrs_BreakTypeSchema>;

export const PageBreak_AttrsSchema: z.ZodTypeAny = z.object({
  "breakType": z.lazy(() => PageBreak_Attrs_BreakTypeSchema).optional(),
});
export type PageBreak_Attrs = z.infer<typeof PageBreak_AttrsSchema>;

export const PageBreakSchema = z.object({
  "type": z.lazy(() => PageBreak_TypeSchema),
  "attrs": z.lazy(() => PageBreak_AttrsSchema).optional(),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type PageBreak = z.infer<typeof PageBreakSchema>;

export const BlockNodeSchema: z.ZodTypeAny = z.union([ParagraphSchema, HeadingSchema, BlockquoteSchema, VerseSchema, ListSchema, TableSchema, FigureSchema, FootnoteSchema, EpigraphSchema, SceneBreakSchema, DialogueSchema, SidebarSchema, CodeSchema, EquationSchema, PageBreakSchema]);
export type BlockNode = z.infer<typeof BlockNodeSchema>;

export const ChapterSchema = z.object({
  "type": z.lazy(() => Chapter_TypeSchema),
  "attrs": z.lazy(() => Chapter_AttrsSchema),
  "content": z.array(z.lazy(() => BlockNodeSchema)).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
  "confidence": ConfidenceSchema.optional(),
});
export type Chapter = z.infer<typeof ChapterSchema>;

export const PartSchema = z.object({
  "type": z.lazy(() => Part_TypeSchema),
  "attrs": z.lazy(() => Part_AttrsSchema),
  "content": z.array(ChapterSchema).min(1),
  "sourceRef": SourceRefLinkSchema.optional(),
});
export type Part = z.infer<typeof PartSchema>;

export const BodyNodeSchema = z.union([PartSchema, ChapterSchema]);
export type BodyNode = z.infer<typeof BodyNodeSchema>;

export const FrontMatterNodeSchema = z.union([z.lazy(() => BlockNodeSchema), z.record(z.string(), z.unknown())]);
export type FrontMatterNode = z.infer<typeof FrontMatterNodeSchema>;

export const BackMatterNodeSchema = z.union([z.lazy(() => BlockNodeSchema), z.record(z.string(), z.unknown())]);
export type BackMatterNode = z.infer<typeof BackMatterNodeSchema>;

export const OutputProfile_Sha256Schema = z.string().regex(new RegExp("^[a-f0-9]{64}$"));
export type OutputProfile_Sha256 = z.infer<typeof OutputProfile_Sha256Schema>;

export const BuildManifest_Sha256Schema = z.string().regex(new RegExp("^[a-f0-9]{64}$"));
export type BuildManifest_Sha256 = z.infer<typeof BuildManifest_Sha256Schema>;

export const OverrideOp_SourceRefSchema: z.ZodTypeAny = z.object({
  "docxId": z.string().max(128),
  "contentHash": z.string().regex(new RegExp("^[a-f0-9]{64}$")).optional(),
  "fallbackText": z.string().max(256).optional(),
});
export type OverrideOp_SourceRef = z.infer<typeof OverrideOp_SourceRefSchema>;

export const OverrideOp_OpSchema: z.ZodTypeAny = z.enum(["reclassify", "split", "merge", "promote", "demote", "delete", "insert", "retitle", "rename", "set_attr", "flag_ambiguity", "resolve_ambiguity"]);
export type OverrideOp_Op = z.infer<typeof OverrideOp_OpSchema>;

export const OverrideOpSchema = z.object({
  "id": z.string().regex(new RegExp("^ov-[a-zA-Z0-9_-]+$")),
  "sourceRef": z.lazy(() => OverrideOp_SourceRefSchema),
  "op": z.lazy(() => OverrideOp_OpSchema),
  "path": z.string().max(256).optional(),
  "from": z.string().max(64).optional(),
  "to": z.string().max(64).optional(),
  "value": z.unknown().optional(),
  "rationale": z.string().max(4096).optional(),
  "actor": z.string().max(64),
  "at": z.string().datetime(),
});
export type OverrideOp = z.infer<typeof OverrideOpSchema>;

export const TrimSize_UnitSchema: z.ZodTypeAny = z.enum(["mm", "in"]);
export type TrimSize_Unit = z.infer<typeof TrimSize_UnitSchema>;

export const TrimSizeSchema = z.object({
  "width": z.number().min(76).max(340),
  "height": z.number().min(100).max(340),
  "unit": z.lazy(() => TrimSize_UnitSchema).optional(),
});
export type TrimSize = z.infer<typeof TrimSizeSchema>;

export const FontRefSchema = z.object({
  "family": z.string().max(128),
  "style": z.string().max(64).optional(),
});
export type FontRef = z.infer<typeof FontRefSchema>;

export const Typography_BodyAlignmentSchema: z.ZodTypeAny = z.enum(["justified", "ragged-right", "ragged-left"]);
export type Typography_BodyAlignment = z.infer<typeof Typography_BodyAlignmentSchema>;

export const TypographySchema = z.object({
  "bodyFont": FontRefSchema,
  "headingFont": FontRefSchema.optional(),
  "bodySize": z.number().min(6).max(20),
  "leading": z.number().min(8).max(36),
  "scaleRatio": z.number().min(1).max(3).optional(),
  "measure": z.number().int().min(30).max(90),
  "bodyAlignment": z.lazy(() => Typography_BodyAlignmentSchema).optional(),
  "paragraphIndent": z.number().min(0).max(5).optional(),
  "paragraphSpacing": z.number().min(0).max(3).optional(),
  "opticalMargins": z.boolean().optional(),
});
export type Typography = z.infer<typeof TypographySchema>;

export const Grid_TypeSchema: z.ZodTypeAny = z.enum(["single", "double", "split"]);
export type Grid_Type = z.infer<typeof Grid_TypeSchema>;

export const GridSchema = z.object({
  "type": z.lazy(() => Grid_TypeSchema).optional(),
  "baselineIncrement": z.number().min(4).max(36).optional(),
  "linesPerPage": z.number().int().min(10).max(100).optional(),
});
export type Grid = z.infer<typeof GridSchema>;

export const MarginsSchema = z.object({
  "top": z.number().min(5).max(60),
  "bottom": z.number().min(5).max(60),
  "inside": z.number().min(5).max(60),
  "outside": z.number().min(5).max(60),
  "gutter": z.number().min(0).max(30).optional(),
});
export type Margins = z.infer<typeof MarginsSchema>;

export const Folio_PositionSchema: z.ZodTypeAny = z.enum(["bottom-center", "bottom-outside", "top-center", "top-outside", "none"]);
export type Folio_Position = z.infer<typeof Folio_PositionSchema>;

export const Folio_StyleSchema: z.ZodTypeAny = z.enum(["arabic", "roman-lower", "roman-upper", "none"]);
export type Folio_Style = z.infer<typeof Folio_StyleSchema>;

export const Folio_SuppressOnItemSchema: z.ZodTypeAny = z.enum(["chapter-opening", "chapter-opening-recto", "full-bleed", "title-page", "part-opening"]);
export type Folio_SuppressOnItem = z.infer<typeof Folio_SuppressOnItemSchema>;

export const Folio_WeightSchema: z.ZodTypeAny = z.enum(["regular", "medium", "semibold", "bold"]);
export type Folio_Weight = z.infer<typeof Folio_WeightSchema>;

export const Folio_CaseSchema: z.ZodTypeAny = z.enum(["none", "uppercase", "lowercase", "small-caps"]);
export type Folio_Case = z.infer<typeof Folio_CaseSchema>;

export const FolioSchema = z.object({
  "position": z.lazy(() => Folio_PositionSchema).optional(),
  "style": z.lazy(() => Folio_StyleSchema).optional(),
  "suppressOn": z.array(z.lazy(() => Folio_SuppressOnItemSchema)).max(10).optional(),
  "startNumber": z.number().int().min(1).optional(),
  "prefix": z.string().max(16).optional(),
  "suffix": z.string().max(16).optional(),
  "sizeDelta": z.number().min(-12).max(12).optional(),
  "weight": z.lazy(() => Folio_WeightSchema).optional(),
  "case": z.lazy(() => Folio_CaseSchema).optional(),
  "tracking": z.number().min(-200).max(500).optional(),
});
export type Folio = z.infer<typeof FolioSchema>;

export const RunningHeads_RectoSourceSchema: z.ZodTypeAny = z.enum(["chapter-title", "part-title", "book-title", "none"]);
export type RunningHeads_RectoSource = z.infer<typeof RunningHeads_RectoSourceSchema>;

export const RunningHeads_VersoSourceSchema: z.ZodTypeAny = z.enum(["book-title", "chapter-title", "author", "none"]);
export type RunningHeads_VersoSource = z.infer<typeof RunningHeads_VersoSourceSchema>;

export const RunningHeads_StyleSchema: z.ZodTypeAny = z.enum(["centered", "outer-margin", "inner-margin", "shown-and-shoulder"]);
export type RunningHeads_Style = z.infer<typeof RunningHeads_StyleSchema>;

export const RunningHeads_SuppressOnItemSchema: z.ZodTypeAny = z.enum(["chapter-opening", "full-bleed", "title-page", "part-opening"]);
export type RunningHeads_SuppressOnItem = z.infer<typeof RunningHeads_SuppressOnItemSchema>;

export const RunningHeads_WeightSchema: z.ZodTypeAny = z.enum(["regular", "medium", "semibold", "bold"]);
export type RunningHeads_Weight = z.infer<typeof RunningHeads_WeightSchema>;

export const RunningHeads_CaseSchema: z.ZodTypeAny = z.enum(["none", "uppercase", "lowercase", "small-caps"]);
export type RunningHeads_Case = z.infer<typeof RunningHeads_CaseSchema>;

export const RunningHeadsSchema = z.object({
  "rectoSource": z.lazy(() => RunningHeads_RectoSourceSchema).optional(),
  "versoSource": z.lazy(() => RunningHeads_VersoSourceSchema).optional(),
  "style": z.lazy(() => RunningHeads_StyleSchema).optional(),
  "suppressOn": z.array(z.lazy(() => RunningHeads_SuppressOnItemSchema)).max(10).optional(),
  "separator": z.string().max(8).optional(),
  "sizeDelta": z.number().min(-12).max(12).optional(),
  "weight": z.lazy(() => RunningHeads_WeightSchema).optional(),
  "case": z.lazy(() => RunningHeads_CaseSchema).optional(),
  "tracking": z.number().min(-200).max(500).optional(),
});
export type RunningHeads = z.infer<typeof RunningHeadsSchema>;

export const ChapterOpenings_StartsOnSchema: z.ZodTypeAny = z.enum(["recto", "verso", "any"]);
export type ChapterOpenings_StartsOn = z.infer<typeof ChapterOpenings_StartsOnSchema>;

export const ChapterOpenings_TitleTreatmentSchema: z.ZodTypeAny = z.enum(["centered", "recto-only", "shoulder-head", "none"]);
export type ChapterOpenings_TitleTreatment = z.infer<typeof ChapterOpenings_TitleTreatmentSchema>;

export const ChapterOpenings_FirstParagraphStyleSchema: z.ZodTypeAny = z.enum(["no-indent", "small-caps", "all-caps", "normal"]);
export type ChapterOpenings_FirstParagraphStyle = z.infer<typeof ChapterOpenings_FirstParagraphStyleSchema>;

export const ChapterOpeningsSchema = z.object({
  "startsOn": z.lazy(() => ChapterOpenings_StartsOnSchema).optional(),
  "dropCap": z.boolean().optional(),
  "dropCapLines": z.number().int().min(2).max(5).optional(),
  "titleTreatment": z.lazy(() => ChapterOpenings_TitleTreatmentSchema).optional(),
  "ornament": z.string().max(64).optional(),
  "firstParagraphStyle": z.lazy(() => ChapterOpenings_FirstParagraphStyleSchema).optional(),
});
export type ChapterOpenings = z.infer<typeof ChapterOpeningsSchema>;

export const FontSpec_SourceSchema: z.ZodTypeAny = z.enum(["bundled_ofl", "tenant_upload", "licensed_server"]);
export type FontSpec_Source = z.infer<typeof FontSpec_SourceSchema>;

export const FontSpecSchema = z.object({
  "family": z.string().max(128),
  "style": z.string().max(64).optional(),
  "weight": z.number().int().min(100).max(900).optional(),
  "opticalSize": z.number().min(6).max(72).optional(),
  "source": z.lazy(() => FontSpec_SourceSchema),
  "licenseRef": z.string().max(128).optional(),
});
export type FontSpec = z.infer<typeof FontSpecSchema>;

export const OrnamentsSchema = z.object({
  "dinkus": z.string().max(64).optional(),
  "chapterOrnament": z.string().max(64).optional(),
  "sectionSymbol": z.string().max(64).optional(),
});
export type Ornaments = z.infer<typeof OrnamentsSchema>;

export const HyphenationSchema = z.object({
  "language": z.string().regex(new RegExp("^[a-z]{2,3}(-[A-Z]{2})?$")).optional(),
  "zone": z.number().min(0).max(20).optional(),
  "shortestWord": z.number().int().min(3).max(10).optional(),
  "consecutiveHyphens": z.number().int().min(1).max(5).optional(),
  "dictionaryVersion": z.string().max(64).optional(),
});
export type Hyphenation = z.infer<typeof HyphenationSchema>;

export const ColorsSchema = z.object({
  "text": z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")).optional(),
  "paper": z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")).optional(),
  "accent": z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")).optional(),
  "link": z.string().regex(new RegExp("^#[0-9a-fA-F]{6}$")).optional(),
});
export type Colors = z.infer<typeof ColorsSchema>;

export const SpecMetadataSchema = z.object({
  "author": z.string().max(128).optional(),
  "createdAt": z.string().datetime().optional(),
  "updatedAt": z.string().datetime().optional(),
  "version": z.number().int().min(1).optional(),
});
export type SpecMetadata = z.infer<typeof SpecMetadataSchema>;

export const PreflightCheck_StatusSchema: z.ZodTypeAny = z.enum(["pass", "fail", "warn", "skip"]);
export type PreflightCheck_Status = z.infer<typeof PreflightCheck_StatusSchema>;

export const PreflightCheck_SeveritySchema: z.ZodTypeAny = z.enum(["error", "warning", "info"]);
export type PreflightCheck_Severity = z.infer<typeof PreflightCheck_SeveritySchema>;

export const PreflightCheckSchema = z.object({
  "code": z.string().max(64),
  "status": z.lazy(() => PreflightCheck_StatusSchema),
  "severity": z.lazy(() => PreflightCheck_SeveritySchema),
  "humanMessage": z.string().max(1024),
  "suggestedFix": z.string().max(1024).optional(),
  "sourceRef": z.string().max(128).optional(),
  "value": z.unknown().optional(),
  "expected": z.unknown().optional(),
  "policyUrl": z.string().url().optional(),
});
export type PreflightCheck = z.infer<typeof PreflightCheckSchema>;

export const ToolchainDigest_IccSchema: z.ZodTypeAny = z.object({
  "cmyk": BuildManifest_Sha256Schema.optional(),
  "rgb": BuildManifest_Sha256Schema.optional(),
});
export type ToolchainDigest_Icc = z.infer<typeof ToolchainDigest_IccSchema>;

export const ToolchainDigestSchema = z.object({
  "images": z.record(z.string(), z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$"))),
  "fonts": BuildManifest_Sha256Schema.optional(),
  "icc": z.lazy(() => ToolchainDigest_IccSchema).optional(),
  "hyphen": z.record(z.string(), z.string()).optional(),
  "engines": z.record(z.string(), z.string()),
  "schema": z.string().max(32).optional(),
  "exemplarSetHash": BuildManifest_Sha256Schema.optional(),
  "ruleSetVersion": z.string().max(32).optional(),
  "promptVersion": z.string().max(32).optional(),
  "modelId": z.string().max(64).optional(),
});
export type ToolchainDigest = z.infer<typeof ToolchainDigestSchema>;

export const StageRecord_StatusSchema: z.ZodTypeAny = z.enum(["completed", "skipped", "failed", "cached"]);
export type StageRecord_Status = z.infer<typeof StageRecord_StatusSchema>;

export const StageRecord_InputsItemSchema: z.ZodTypeAny = z.object({
  "kind": z.string().max(64),
  "hash": BuildManifest_Sha256Schema,
});
export type StageRecord_InputsItem = z.infer<typeof StageRecord_InputsItemSchema>;

export const StageRecord_OutputsItemSchema: z.ZodTypeAny = z.object({
  "kind": z.string().max(64),
  "hash": BuildManifest_Sha256Schema,
});
export type StageRecord_OutputsItem = z.infer<typeof StageRecord_OutputsItemSchema>;

export const Diagnostic_SeveritySchema: z.ZodTypeAny = z.enum(["error", "warning", "info"]);
export type Diagnostic_Severity = z.infer<typeof Diagnostic_SeveritySchema>;

export const DiagnosticSchema = z.object({
  "code": z.string().max(64),
  "severity": z.lazy(() => Diagnostic_SeveritySchema),
  "humanMessage": z.string().max(1024),
  "suggestedFix": z.string().max(1024).optional(),
  "sourceRef": z.string().max(128).optional(),
});
export type Diagnostic = z.infer<typeof DiagnosticSchema>;

export const StageError_KindSchema: z.ZodTypeAny = z.enum(["bad_input", "policy_violation", "engine_bug", "infra", "external_limit"]);
export type StageError_Kind = z.infer<typeof StageError_KindSchema>;

export const StageErrorSchema = z.object({
  "kind": z.lazy(() => StageError_KindSchema),
  "message": z.string().max(2048),
  "diagnostics": z.array(DiagnosticSchema).max(50).optional(),
  "retryCount": z.number().int().min(0).optional(),
  "retryable": z.boolean().optional(),
});
export type StageError = z.infer<typeof StageErrorSchema>;

export const StageRecordSchema = z.object({
  "name": z.string().max(64),
  "version": z.number().int().min(1),
  "status": z.lazy(() => StageRecord_StatusSchema),
  "cacheHit": z.boolean().optional(),
  "cacheKey": BuildManifest_Sha256Schema.optional(),
  "inputs": z.array(z.lazy(() => StageRecord_InputsItemSchema)).max(50).optional(),
  "outputs": z.array(z.lazy(() => StageRecord_OutputsItemSchema)).max(50).optional(),
  "metrics": z.record(z.string(), z.number()).optional(),
  "warnings": z.array(DiagnosticSchema).max(100).optional(),
  "error": StageErrorSchema.optional(),
  "durationMs": z.number().int().min(0).optional(),
  "startedAt": z.string().datetime().optional(),
  "completedAt": z.string().datetime().optional(),
});
export type StageRecord = z.infer<typeof StageRecordSchema>;

export const ArtifactRefSchema = z.object({
  "hash": BuildManifest_Sha256Schema,
  "mediaType": z.string().max(128),
  "size": z.number().int().min(0),
  "path": z.string().max(512).optional(),
});
export type ArtifactRef = z.infer<typeof ArtifactRefSchema>;

export const ClassifiedNode_ClassificationSchema: z.ZodTypeAny = z.enum(["chapter-title", "heading-1", "heading-2", "heading-3", "paragraph", "chapter-opening", "first-paragraph", "blockquote", "epigraph", "verse", "dialogue", "scene-break", "dinkus", "front-half-title", "front-title-page", "front-copyright", "front-dedication", "front-toc", "front-foreword", "front-preface", "front-acknowledgments", "front-prologue", "back-epilogue", "back-afterword", "back-appendix", "back-notes", "back-bibliography", "back-index", "back-about-author", "back-also-by", "back-colophon", "footnote", "endnote", "table", "table-caption", "figure", "figure-caption", "unordered-list", "ordered-list", "sidebar", "code-block", "equation", "page-break", "section-break", "title-of-work", "foreign-term", "emphasis", "uncertain"]);
export type ClassifiedNode_Classification = z.infer<typeof ClassifiedNode_ClassificationSchema>;

export const ClassifiedNodeSchema = z.object({
  "sourceRef": z.string().max(128),
  "classification": z.lazy(() => ClassifiedNode_ClassificationSchema),
  "confidence": z.number().min(0).max(1),
});
export type ClassifiedNode = z.infer<typeof ClassifiedNodeSchema>;

export const TrimSizeSuggestion_RationaleSchema: z.ZodTypeAny = z.enum(["genre-typical", "page-count-optimal", "vendor-popular"]);
export type TrimSizeSuggestion_Rationale = z.infer<typeof TrimSizeSuggestion_RationaleSchema>;

export const TrimSizeSuggestionSchema = z.object({
  "width": z.number().optional(),
  "height": z.number().optional(),
  "rationale": z.lazy(() => TrimSizeSuggestion_RationaleSchema).optional(),
});
export type TrimSizeSuggestion = z.infer<typeof TrimSizeSuggestionSchema>;

export const Proposal_TypeSchema: z.ZodTypeAny = z.enum(["reclassify", "merge_chapters", "split_chapter", "adjust_heading_level", "identify_front_matter", "identify_back_matter", "flag_ambiguity", "suggest_title"]);
export type Proposal_Type = z.infer<typeof Proposal_TypeSchema>;

export const Proposal_SourceRefSchema: z.ZodTypeAny = z.object({
  "docxId": z.string().max(128),
  "contentHash": z.string().regex(new RegExp("^[a-f0-9]{64}$")).optional(),
});
export type Proposal_SourceRef = z.infer<typeof Proposal_SourceRefSchema>;

export const ProposalSchema = z.object({
  "id": z.string().regex(new RegExp("^pr-[a-zA-Z0-9_-]+$")),
  "type": z.lazy(() => Proposal_TypeSchema),
  "sourceRef": z.lazy(() => Proposal_SourceRefSchema),
  "from": z.string().max(64).optional(),
  "to": z.string().max(64).optional(),
  "value": z.union([z.string().max(256), z.number().int(), z.boolean(), z.number()]).optional(),
  "rationale": z.string().max(4096),
  "confidence": z.number().min(0).max(1).optional(),
  "evidence": z.array(z.string().max(1024)).max(20).optional(),
});
export type Proposal = z.infer<typeof ProposalSchema>;

export const PageEntry_SideSchema: z.ZodTypeAny = z.enum(["recto", "verso"]);
export type PageEntry_Side = z.infer<typeof PageEntry_SideSchema>;

export const PageEntry_TypeSchema: z.ZodTypeAny = z.enum(["normal", "chapter-opening", "part-opening", "full-bleed", "title-page", "blank"]);
export type PageEntry_Type = z.infer<typeof PageEntry_TypeSchema>;

export const PageEntry_ParaRangesItemSchema: z.ZodTypeAny = z.object({
  "paraIndex": z.number().int().min(0),
  "linesOnPage": z.number().int().min(1),
});
export type PageEntry_ParaRangesItem = z.infer<typeof PageEntry_ParaRangesItemSchema>;

export const PageEntrySchema = z.object({
  "pageNumber": z.number().int().min(1),
  "folio": z.number().int().min(1),
  "side": z.lazy(() => PageEntry_SideSchema),
  "widthPt": z.number().min(100),
  "heightPt": z.number().min(100),
  "chapterId": z.string().max(64),
  "sectionId": z.string().max(64).optional(),
  "type": z.lazy(() => PageEntry_TypeSchema).optional(),
  "contentStart": z.number().min(0).optional(),
  "hasOrphans": z.boolean().optional(),
  "hasWidows": z.boolean().optional(),
  "hasRunts": z.boolean().optional(),
  "wordCount": z.number().int().min(0).optional(),
  "defectScore": z.number().min(0).optional(),
  "paraRanges": z.array(z.lazy(() => PageEntry_ParaRangesItemSchema)).max(200).optional(),
});
export type PageEntry = z.infer<typeof PageEntrySchema>;

export const ChapterEntry_StartsOnSchema: z.ZodTypeAny = z.enum(["recto", "verso", "any"]);
export type ChapterEntry_StartsOn = z.infer<typeof ChapterEntry_StartsOnSchema>;

export const ChapterEntrySchema = z.object({
  "chapterId": z.string().max(64),
  "number": z.number().int().min(1),
  "title": z.string().max(512).optional(),
  "startPage": z.number().int().min(1),
  "endPage": z.number().int().min(1),
  "pageCount": z.number().int().min(1),
  "startsOn": z.lazy(() => ChapterEntry_StartsOnSchema).optional(),
});
export type ChapterEntry = z.infer<typeof ChapterEntrySchema>;

export const CompositionSchema = z.enum(["rule-of-thirds", "centered", "symmetrical", "diagonal", "negative-space-dominant", "full-bleed-texture", "silhouette", "close-crop", "wide-establishing"]);
export type Composition = z.infer<typeof CompositionSchema>;

export const PaletteToneSchema = z.enum(["desaturated-blue", "warm-amber", "bone-white", "charcoal", "blood-red", "forest-green", "dusty-rose", "deep-violet", "muted-ochre", "stark-black", "cool-teal", "burnt-sienna", "pale-gold", "slate-grey", "ivory"]);
export type PaletteTone = z.infer<typeof PaletteToneSchema>;

export const LightingSchema = z.enum(["low-key", "high-key", "backlit", "golden-hour", "overcast-flat", "hard-noon", "candlelit", "moonlit", "studio-soft", "harsh-fluorescent"]);
export type Lighting = z.infer<typeof LightingSchema>;

export const MediumSchema = z.enum(["painterly-digital", "photographic", "watercolor", "linocut", "collage", "flat-vector", "gouache", "ink-wash", "oil-painting", "risograph", "3d-render"]);
export type Medium = z.infer<typeof MediumSchema>;

export const MoodSchema = z.enum(["tense", "melancholic", "whimsical", "ominous", "hopeful", "nostalgic", "clinical", "romantic", "cold", "urgent", "serene"]);
export type Mood = z.infer<typeof MoodSchema>;

export const GenreSignalSchema = z.enum(["literary-thriller", "cozy-mystery", "epic-fantasy", "hard-scifi", "romance", "literary-fiction", "memoir", "horror", "historical-fiction", "ya-contemporary", "business-nonfiction", "self-help", "true-crime", "poetry"]);
export type GenreSignal = z.infer<typeof GenreSignalSchema>;

export const TypeZoneSchema = z.enum(["upper-third", "lower-third", "center-band", "none"]);
export type TypeZone = z.infer<typeof TypeZoneSchema>;

export const NegativeTagSchema = z.enum(["text", "readable-typography", "logo", "watermark", "photorealistic-face", "real-person-likeness", "brand-marks", "signature", "clutter", "low-contrast-center", "extra-limbs", "distorted-anatomy"]);
export type NegativeTag = z.infer<typeof NegativeTagSchema>;

export const BookASTSchema = z.object({
  "schema": BookAST_SchemaRefSchema,
  "metadata": MetadataSchema.optional(),
  "frontMatter": z.array(FrontMatterNodeSchema).optional(),
  "body": z.array(BodyNodeSchema),
  "backMatter": z.array(BackMatterNodeSchema).optional(),
  "integrityHash": z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$")),
  "sourceRef": BookAST_SourceRefSchema,
});
export type BookAST = z.infer<typeof BookASTSchema>;

export const OverrideSet_SchemaSchema: z.ZodTypeAny = z.enum(["overrides/1"]);
export type OverrideSet_Schema = z.infer<typeof OverrideSet_SchemaSchema>;

export const OverrideSet_OrphanedOpsItem_ReasonSchema: z.ZodTypeAny = z.enum(["no_source_ref", "content_mismatch", "fuzzy_match_failed"]);
export type OverrideSet_OrphanedOpsItem_Reason = z.infer<typeof OverrideSet_OrphanedOpsItem_ReasonSchema>;

export const OverrideSet_OrphanedOpsItemSchema: z.ZodTypeAny = z.object({
  "op": OverrideOpSchema,
  "reason": z.lazy(() => OverrideSet_OrphanedOpsItem_ReasonSchema),
});
export type OverrideSet_OrphanedOpsItem = z.infer<typeof OverrideSet_OrphanedOpsItemSchema>;

export const OverrideSetSchema = z.object({
  "schema": z.lazy(() => OverrideSet_SchemaSchema),
  "documentId": z.string().min(1).max(128),
  "astVersion": z.number().int().min(1),
  "ops": z.array(OverrideOpSchema).max(10000),
  "orphanedOps": z.array(z.lazy(() => OverrideSet_OrphanedOpsItemSchema)).optional(),
  "createdAt": z.string().datetime().optional(),
  "updatedAt": z.string().datetime().optional(),
});
export type OverrideSet = z.infer<typeof OverrideSetSchema>;

export const DesignSpec_SchemaSchema: z.ZodTypeAny = z.enum(["designspec/1"]);
export type DesignSpec_Schema = z.infer<typeof DesignSpec_SchemaSchema>;

export const DesignSpec_PreferredEngineSchema: z.ZodTypeAny = z.enum(["chrome-pagedjs", "typst", "prince"]);
export type DesignSpec_PreferredEngine = z.infer<typeof DesignSpec_PreferredEngineSchema>;

export const DesignSpecSchema = z.object({
  "schema": z.lazy(() => DesignSpec_SchemaSchema),
  "name": z.string().max(128),
  "templateId": z.string().max(64).optional(),
  "preferredEngine": z.lazy(() => DesignSpec_PreferredEngineSchema).optional(),
  "trimSize": TrimSizeSchema,
  "typography": TypographySchema,
  "grid": GridSchema,
  "margins": MarginsSchema,
  "folio": FolioSchema.optional(),
  "runningHeads": RunningHeadsSchema.optional(),
  "chapterOpenings": ChapterOpeningsSchema.optional(),
  "fonts": z.array(FontSpecSchema).max(20),
  "ornaments": OrnamentsSchema.optional(),
  "hyphenation": HyphenationSchema.optional(),
  "colors": ColorsSchema.optional(),
  "metadata": SpecMetadataSchema.optional(),
});
export type DesignSpec = z.infer<typeof DesignSpecSchema>;

export const OutputProfile_SchemaSchema: z.ZodTypeAny = z.enum(["profile/1"]);
export type OutputProfile_Schema = z.infer<typeof OutputProfile_SchemaSchema>;

export const OutputProfile_VendorSchema: z.ZodTypeAny = z.enum(["kdp", "ingramspark", "lulu", "barnesnoble", "generic"]);
export type OutputProfile_Vendor = z.infer<typeof OutputProfile_VendorSchema>;

export const OutputProfile_TrimSize_UnitSchema: z.ZodTypeAny = z.enum(["mm", "in"]);
export type OutputProfile_TrimSize_Unit = z.infer<typeof OutputProfile_TrimSize_UnitSchema>;

export const OutputProfile_TrimSizeSchema: z.ZodTypeAny = z.object({
  "width": z.number().min(76).max(340),
  "height": z.number().min(100).max(340),
  "unit": z.lazy(() => OutputProfile_TrimSize_UnitSchema).optional(),
});
export type OutputProfile_TrimSize = z.infer<typeof OutputProfile_TrimSizeSchema>;

export const OutputProfile_BleedSchema: z.ZodTypeAny = z.object({
  "all": z.number().min(0).max(25).optional(),
  "top": z.number().min(0).max(25).optional(),
  "bottom": z.number().min(0).max(25).optional(),
  "inside": z.number().min(0).max(25).optional(),
  "outside": z.number().min(0).max(25).optional(),
});
export type OutputProfile_Bleed = z.infer<typeof OutputProfile_BleedSchema>;

export const OutputProfile_PdfSpec_VersionSchema: z.ZodTypeAny = z.enum(["1.3", "1.4", "1.5", "1.6", "1.7", "2.0"]);
export type OutputProfile_PdfSpec_Version = z.infer<typeof OutputProfile_PdfSpec_VersionSchema>;

export const OutputProfile_PdfSpec_StandardSchema: z.ZodTypeAny = z.enum(["none", "pdfx-1a", "pdfx-3", "pdfx-4", "pdfa-2b"]);
export type OutputProfile_PdfSpec_Standard = z.infer<typeof OutputProfile_PdfSpec_StandardSchema>;

export const OutputProfile_PdfSpec_ColorSpaceSchema: z.ZodTypeAny = z.enum(["cmyk", "gray", "rgb"]);
export type OutputProfile_PdfSpec_ColorSpace = z.infer<typeof OutputProfile_PdfSpec_ColorSpaceSchema>;

export const OutputProfile_PdfSpec_OutputIntentSchema: z.ZodTypeAny = z.object({
  "iccProfileHash": OutputProfile_Sha256Schema,
  "iccProfileName": z.string().max(128).optional(),
  "registryUrl": z.string().url().optional(),
});
export type OutputProfile_PdfSpec_OutputIntent = z.infer<typeof OutputProfile_PdfSpec_OutputIntentSchema>;

export const OutputProfile_PdfSpecSchema: z.ZodTypeAny = z.object({
  "version": z.lazy(() => OutputProfile_PdfSpec_VersionSchema).optional(),
  "standard": z.lazy(() => OutputProfile_PdfSpec_StandardSchema),
  "colorSpace": z.lazy(() => OutputProfile_PdfSpec_ColorSpaceSchema),
  "outputIntent": z.lazy(() => OutputProfile_PdfSpec_OutputIntentSchema).optional(),
});
export type OutputProfile_PdfSpec = z.infer<typeof OutputProfile_PdfSpecSchema>;

export const OutputProfile_CoverSpec_PaperTypeSchema: z.ZodTypeAny = z.enum(["matte", "glossy", "premium-matte", "linen", "none"]);
export type OutputProfile_CoverSpec_PaperType = z.infer<typeof OutputProfile_CoverSpec_PaperTypeSchema>;

export const OutputProfile_CoverSpec_InkSchema: z.ZodTypeAny = z.enum(["full-color", "black-and-white"]);
export type OutputProfile_CoverSpec_Ink = z.infer<typeof OutputProfile_CoverSpec_InkSchema>;

export const OutputProfile_CoverSpec_FinishSchema: z.ZodTypeAny = z.enum(["perfect-bound", "saddle-stitch", "case-bound", "spiral"]);
export type OutputProfile_CoverSpec_Finish = z.infer<typeof OutputProfile_CoverSpec_FinishSchema>;

export const OutputProfile_CoverSpecSchema: z.ZodTypeAny = z.object({
  "paperType": z.lazy(() => OutputProfile_CoverSpec_PaperTypeSchema).optional(),
  "ink": z.lazy(() => OutputProfile_CoverSpec_InkSchema).optional(),
  "finish": z.lazy(() => OutputProfile_CoverSpec_FinishSchema).optional(),
});
export type OutputProfile_CoverSpec = z.infer<typeof OutputProfile_CoverSpecSchema>;

export const OutputProfile_ProofSpecSchema: z.ZodTypeAny = z.object({
  "watermark": z.boolean().optional(),
  "dpi": z.number().int().optional(),
  "sizeBudgetBytes": z.number().int().min(0).optional(),
});
export type OutputProfile_ProofSpec = z.infer<typeof OutputProfile_ProofSpecSchema>;

export const OutputProfile_DeliverySpec_InteriorFormatItemSchema: z.ZodTypeAny = z.enum(["pdf", "epub", "idml", "onix"]);
export type OutputProfile_DeliverySpec_InteriorFormatItem = z.infer<typeof OutputProfile_DeliverySpec_InteriorFormatItemSchema>;

export const OutputProfile_DeliverySpec_CoverFormatItemSchema: z.ZodTypeAny = z.enum(["pdf", "jpeg"]);
export type OutputProfile_DeliverySpec_CoverFormatItem = z.infer<typeof OutputProfile_DeliverySpec_CoverFormatItemSchema>;

export const OutputProfile_DeliverySpecSchema: z.ZodTypeAny = z.object({
  "interiorFormat": z.array(z.lazy(() => OutputProfile_DeliverySpec_InteriorFormatItemSchema)).max(4).optional(),
  "coverFormat": z.array(z.lazy(() => OutputProfile_DeliverySpec_CoverFormatItemSchema)).max(2).optional(),
});
export type OutputProfile_DeliverySpec = z.infer<typeof OutputProfile_DeliverySpecSchema>;

export const OutputProfileSchema = z.object({
  "schema": z.lazy(() => OutputProfile_SchemaSchema),
  "name": z.string().max(128),
  "vendor": z.lazy(() => OutputProfile_VendorSchema),
  "vendorProfileVersion": z.string().max(32).optional(),
  "trimSize": z.lazy(() => OutputProfile_TrimSizeSchema),
  "bleed": z.lazy(() => OutputProfile_BleedSchema).optional(),
  "pdfSpec": z.lazy(() => OutputProfile_PdfSpecSchema),
  "coverSpec": z.lazy(() => OutputProfile_CoverSpecSchema).optional(),
  "proofSpec": z.lazy(() => OutputProfile_ProofSpecSchema).optional(),
  "deliverySpec": z.lazy(() => OutputProfile_DeliverySpecSchema).optional(),
  "minPages": z.number().int().min(1).optional(),
  "maxPages": z.number().int().min(1).optional(),
  "pageSizeMultiple": z.number().int().min(1).optional(),
  "validatedAt": z.string().datetime().optional(),
});
export type OutputProfile = z.infer<typeof OutputProfileSchema>;

export const PreflightReport_SchemaSchema: z.ZodTypeAny = z.enum(["preflight/1"]);
export type PreflightReport_Schema = z.infer<typeof PreflightReport_SchemaSchema>;

export const PreflightReport_StatusSchema: z.ZodTypeAny = z.enum(["pass", "fail", "warn"]);
export type PreflightReport_Status = z.infer<typeof PreflightReport_StatusSchema>;

export const PreflightReport_SummarySchema: z.ZodTypeAny = z.object({
  "passed": z.number().int().min(0),
  "failed": z.number().int().min(0),
  "warnings": z.number().int().min(0).optional(),
  "policyViolations": z.number().int().min(0).optional(),
});
export type PreflightReport_Summary = z.infer<typeof PreflightReport_SummarySchema>;

export const PreflightReportSchema = z.object({
  "schema": z.lazy(() => PreflightReport_SchemaSchema),
  "status": z.lazy(() => PreflightReport_StatusSchema),
  "profileId": z.string().max(64).optional(),
  "pageCount": z.number().int().min(1).optional(),
  "checks": z.array(PreflightCheckSchema).max(200),
  "summary": z.lazy(() => PreflightReport_SummarySchema),
  "profileVersion": z.string().max(32).optional(),
  "vendorTemplate": z.string().max(64).optional(),
  "createdAt": z.string().datetime().optional(),
});
export type PreflightReport = z.infer<typeof PreflightReportSchema>;

export const BuildManifest_SchemaSchema: z.ZodTypeAny = z.enum(["manifest/1"]);
export type BuildManifest_Schema = z.infer<typeof BuildManifest_SchemaSchema>;

export const BuildManifest_ModeSchema: z.ZodTypeAny = z.enum(["proof", "final"]);
export type BuildManifest_Mode = z.infer<typeof BuildManifest_ModeSchema>;

export const BuildManifest_PreflightStatusSchema: z.ZodTypeAny = z.enum(["pass", "fail", "not_run"]);
export type BuildManifest_PreflightStatus = z.infer<typeof BuildManifest_PreflightStatusSchema>;

export const BuildManifestSchema = z.object({
  "schema": z.lazy(() => BuildManifest_SchemaSchema),
  "buildId": z.string().regex(new RegExp("^bld-[a-zA-Z0-9_-]+$")),
  "tenantId": z.string().max(64).optional(),
  "titleId": z.string().max(64).optional(),
  "documentId": z.string().max(64).optional(),
  "overrideSetId": z.string().max(64).optional(),
  "designSpecId": z.string().max(64).optional(),
  "profileIds": z.array(z.string().max(64)).max(10).optional(),
  "mode": z.lazy(() => BuildManifest_ModeSchema).optional(),
  "toolchain": ToolchainDigestSchema,
  "stageVersions": z.record(z.string(), z.number().int().min(1)).optional(),
  "stages": z.array(StageRecordSchema).max(100),
  "artifacts": z.record(z.string(), ArtifactRefSchema).optional(),
  "preflightStatus": z.lazy(() => BuildManifest_PreflightStatusSchema).optional(),
  "preflightReport": ArtifactRefSchema.optional(),
  "pages": z.number().int().min(1).optional(),
  "reproductionKey": BuildManifest_Sha256Schema,
  "startedAt": z.string().datetime().optional(),
  "completedAt": z.string().datetime().optional(),
  "version": z.number().int().min(1).optional(),
});
export type BuildManifest = z.infer<typeof BuildManifestSchema>;

export const ClassificationResult_SchemaSchema: z.ZodTypeAny = z.enum(["classification/1"]);
export type ClassificationResult_Schema = z.infer<typeof ClassificationResult_SchemaSchema>;

export const ClassificationResult_ModelInfo_SchemaVersionSchema: z.ZodTypeAny = z.enum(["classification/1"]);
export type ClassificationResult_ModelInfo_SchemaVersion = z.infer<typeof ClassificationResult_ModelInfo_SchemaVersionSchema>;

export const ClassificationResult_ModelInfoSchema: z.ZodTypeAny = z.object({
  "modelId": z.string().max(128),
  "promptVersion": z.string().max(32).optional(),
  "schemaVersion": z.lazy(() => ClassificationResult_ModelInfo_SchemaVersionSchema).optional(),
  "cacheHit": z.boolean().optional(),
  "costUsd": z.number().min(0).optional(),
});
export type ClassificationResult_ModelInfo = z.infer<typeof ClassificationResult_ModelInfoSchema>;

export const ClassificationResult_Suggestions_GenreSchema: z.ZodTypeAny = z.enum(["fiction", "nonfiction", "memoir", "poetry", "academic", "technical", "childrens", "drama"]);
export type ClassificationResult_Suggestions_Genre = z.infer<typeof ClassificationResult_Suggestions_GenreSchema>;

export const ClassificationResult_SuggestionsSchema: z.ZodTypeAny = z.object({
  "genre": z.lazy(() => ClassificationResult_Suggestions_GenreSchema).optional(),
  "trimSizeSuggestion": TrimSizeSuggestionSchema.optional(),
});
export type ClassificationResult_Suggestions = z.infer<typeof ClassificationResult_SuggestionsSchema>;

export const ClassificationResultSchema = z.object({
  "schema": z.lazy(() => ClassificationResult_SchemaSchema),
  "nodes": z.array(ClassifiedNodeSchema).max(50000),
  "confidences": z.record(z.string(), z.number().min(0).max(1)).optional(),
  "modelInfo": z.lazy(() => ClassificationResult_ModelInfoSchema),
  "suggestions": z.lazy(() => ClassificationResult_SuggestionsSchema).optional(),
});
export type ClassificationResult = z.infer<typeof ClassificationResultSchema>;

export const AgentProposal_SchemaSchema: z.ZodTypeAny = z.enum(["agent-proposal/1"]);
export type AgentProposal_Schema = z.infer<typeof AgentProposal_SchemaSchema>;

export const AgentProposal_StateChangesSchema: z.ZodTypeAny = z.object({
  "ruleSetVersion": z.string().max(32).optional(),
  "exemplarSetHash": z.string().regex(new RegExp("^[a-f0-9]{64}$")).optional(),
});
export type AgentProposal_StateChanges = z.infer<typeof AgentProposal_StateChangesSchema>;

export const AgentProposal_EvaluationSchema: z.ZodTypeAny = z.object({
  "judgeModelId": z.string().max(128).optional(),
  "scores": z.record(z.string(), z.number().min(0).max(1)).optional(),
  "expertReviewed": z.boolean().optional(),
  "accepted": z.boolean().optional(),
});
export type AgentProposal_Evaluation = z.infer<typeof AgentProposal_EvaluationSchema>;

export const AgentProposalSchema = z.object({
  "schema": z.lazy(() => AgentProposal_SchemaSchema),
  "agentId": z.string().max(128),
  "agentVersion": z.string().max(32).optional(),
  "promptVersion": z.string().max(32).optional(),
  "modelId": z.string().max(128).optional(),
  "proposals": z.array(ProposalSchema).max(500),
  "stateChanges": z.lazy(() => AgentProposal_StateChangesSchema).optional(),
  "evaluation": z.lazy(() => AgentProposal_EvaluationSchema).optional(),
  "costUsd": z.number().min(0).optional(),
  "latencyMs": z.number().int().min(0).optional(),
  "createdAt": z.string().datetime().optional(),
});
export type AgentProposal = z.infer<typeof AgentProposalSchema>;

export const PageMap_SchemaSchema: z.ZodTypeAny = z.enum(["pagemap/1"]);
export type PageMap_Schema = z.infer<typeof PageMap_SchemaSchema>;

export const PageMapSchema = z.object({
  "schema": z.lazy(() => PageMap_SchemaSchema),
  "pages": z.array(PageEntrySchema).min(1),
  "chapters": z.array(ChapterEntrySchema).min(1),
});
export type PageMap = z.infer<typeof PageMapSchema>;

export const ArtBriefSchema = z.object({
  "schema": ArtBrief_SchemaRefSchema,
  "concept": z.string().min(1).max(500),
  "subject": z.string().min(1).max(300),
  "composition": CompositionSchema,
  "palette": z.array(PaletteToneSchema).min(1).max(5),
  "lighting": LightingSchema,
  "medium": MediumSchema,
  "mood": MoodSchema,
  "genreSignals": z.array(GenreSignalSchema).min(1).max(3),
  "typeZone": TypeZoneSchema,
  "negative": z.array(NegativeTagSchema).min(1).max(12),
  "sourceRef": ArtBrief_SourceRefSchema,
});
export type ArtBrief = z.infer<typeof ArtBriefSchema>;

export const ArtProvenance_SchemaSchema: z.ZodTypeAny = z.enum(["art-provenance/1"]);
export type ArtProvenance_Schema = z.infer<typeof ArtProvenance_SchemaSchema>;

export const ArtProvenance_BillingUnitSchema: z.ZodTypeAny = z.enum(["image", "megapixel", "token"]);
export type ArtProvenance_BillingUnit = z.infer<typeof ArtProvenance_BillingUnitSchema>;

export const ArtProvenance_ReplayClassSchema: z.ZodTypeAny = z.enum(["seeded", "artifact-only"]);
export type ArtProvenance_ReplayClass = z.infer<typeof ArtProvenance_ReplayClassSchema>;

export const ArtProvenanceSchema = z.object({
  "schema": z.lazy(() => ArtProvenance_SchemaSchema),
  "modelId": z.string().max(128),
  "canonicalSlug": z.string().max(128),
  "providerSlug": z.string().max(64),
  "billingUnit": z.lazy(() => ArtProvenance_BillingUnitSchema),
  "costUsd": z.number().min(0),
  "artBriefHash": z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$")),
  "imageHash": z.string().regex(new RegExp("^sha256:[a-f0-9]{64}$")),
  "seed": z.union([z.number().int(), z.unknown()]).optional(),
  "replayClass": z.lazy(() => ArtProvenance_ReplayClassSchema),
  "resolution": z.string().max(16),
  "aspectRatio": z.string().max(16),
  "sampleUsed": z.boolean(),
  "createdAt": z.string().datetime(),
});
export type ArtProvenance = z.infer<typeof ArtProvenanceSchema>;

export const CoverVerdict_SchemaSchema: z.ZodTypeAny = z.enum(["cover-verdict/1"]);
export type CoverVerdict_Schema = z.infer<typeof CoverVerdict_SchemaSchema>;

export const CoverVerdict_WinnerSchema: z.ZodTypeAny = z.enum(["a", "b", "tie"]);
export type CoverVerdict_Winner = z.infer<typeof CoverVerdict_WinnerSchema>;

export const CoverVerdict_ModelInfoSchema: z.ZodTypeAny = z.object({
  "modelId": z.string().max(128),
  "providerSlug": z.string().max(64),
  "promptVersion": z.string().max(32),
  "reasoningConfigSha256": z.string().regex(new RegExp("^[a-f0-9]{64}$")).optional(),
});
export type CoverVerdict_ModelInfo = z.infer<typeof CoverVerdict_ModelInfoSchema>;

export const CoverVerdictSchema = z.object({
  "schema": z.lazy(() => CoverVerdict_SchemaSchema),
  "winner": z.lazy(() => CoverVerdict_WinnerSchema),
  "modelInfo": z.lazy(() => CoverVerdict_ModelInfoSchema),
});
export type CoverVerdict = z.infer<typeof CoverVerdictSchema>;
