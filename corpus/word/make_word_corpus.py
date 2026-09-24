"""
Author the Word-produced manuscripts in this folder, by driving Word itself.

WHY WORD AND NOT python-docx. python-docx writes the XML its author expected;
Word writes the XML ingest actually meets: tracked revisions wrapped in `w:ins`,
fields split across `w:fldChar` runs, content controls in `w:sdt`, text boxes
duplicated into a VML fallback, equations in OMML, endnotes in their own part.
Every one of those is a container a hand-rolled reader can walk past without
noticing, and the synthetic corpus (`corpus/manuscripts/`, AST JSON) never
exercised any of it.

The .docx files are committed; this script is how they were made, not a build
step. It needs Windows, Microsoft Word and pywin32, so CI cannot run it:

    python corpus/word/make_word_corpus.py

Personal information is stripped on save (`RemovePersonalInformation`), so the
tracked-change and comment authors are Word's anonymous "Author", not whoever
ran this.
"""

from __future__ import annotations

from pathlib import Path

import win32com.client

HERE = Path(__file__).resolve().parent

# Word constants (the COM typelib is not loaded, so they are spelled out).
HEADING_1, NORMAL = -2, -1
PAGE_BREAK, SECTION_NEXT_PAGE = 7, 2
RICH_TEXT_CONTROL = 1
TEXTBOX_HORIZONTAL = 1
STORY_END = 6
DOCX = 16

PROSE = (
    "The house stood at the end of a road that no map admitted to, and the people "
    "who lived there had long since stopped expecting visitors. Rain came in from "
    "the sea most afternoons; the shutters were closed against it, and the lamps "
    "were lit early, so that from the hill the windows looked like a row of coals "
    "banked for the night. Nobody could say when the last letter had arrived. "
)
GREEK = (
    "Το σπίτι βρισκόταν στο τέλος ενός δρόμου που κανένας χάρτης δεν παραδεχόταν, "
    "και οι άνθρωποι που ζούσαν εκεί είχαν πάψει από καιρό να περιμένουν επισκέπτες. "
    "Η βροχή ερχόταν από τη θάλασσα τα περισσότερα απογεύματα· τα παντζούρια έκλειναν, "
    "και οι λάμπες άναβαν νωρίς, ώστε από τον λόφο τα παράθυρα έμοιαζαν με κάρβουνα. "
)


class Author:
    """Thin wrapper over a Word Selection, so the manuscripts below read as prose."""

    def __init__(self, word, doc):
        self.doc, self.sel = doc, word.Selection

    def style(self, style: int) -> None:
        self.sel.Style = self.doc.Styles(style)

    def typed(self, text: str) -> None:
        self.sel.TypeText(text)

    def end_para(self) -> None:
        self.sel.TypeParagraph()

    def para(self, text: str, style: int = NORMAL) -> None:
        self.style(style)
        self.typed(text)
        self.end_para()
        self.style(NORMAL)

    def heading(self, text: str) -> None:
        self.para(text, HEADING_1)

    def to_end(self) -> None:
        self.sel.EndKey(STORY_END)

    def here(self):
        return self.doc.Range(self.sel.Start, self.sel.Start)

    def page_break(self) -> None:
        self.sel.InsertBreak(PAGE_BREAK)


def note(a: Author, notes, text: str) -> None:
    """A footnote or endnote at the cursor. The text goes on the note's range:
    passed as `Add(Text=...)` through late-bound COM, Word drops it silently."""
    notes.Add(a.here()).Range.Text = text


def tracked(a: Author, keep: str, insert: str, delete: str) -> None:
    """One paragraph with a tracked insertion and a tracked deletion in it."""
    a.typed(keep)
    a.doc.TrackRevisions = True
    a.typed(insert)
    a.doc.TrackRevisions = False
    start = a.sel.Start
    a.typed(delete)
    end = a.sel.Start
    a.doc.TrackRevisions = True
    a.doc.Range(start, end).Delete()
    a.doc.TrackRevisions = False
    a.to_end()
    a.typed(PROSE)
    a.end_para()


def novel(a: Author) -> None:
    a.para("THE HOUSE AT THE END OF THE ROAD")
    a.para("a novel")
    a.page_break()
    a.para("For everyone who stopped expecting visitors.")
    a.page_break()
    a.para("CONTENTS")
    toc_at = a.here()
    a.end_para()
    a.page_break()

    a.heading("CHAPTER ONE")
    a.typed(PROSE * 2 + "The letter, when it came, was addressed to nobody.")
    note(a, a.doc.Footnotes, "The postmark was illegible, which was itself a kind of message.")
    a.to_end()
    a.typed(" " + PROSE)
    a.end_para()
    tracked(a, "She read it twice before she understood it. ",
            "It had been written in a hurry. ", "It was very short. ")
    a.typed("A well")
    a.typed(chr(30))  # non-breaking hyphen
    a.typed("known name was signed at the bottom, in a hand she half")
    a.typed(chr(31))  # optional (soft) hyphen
    a.typed("recognised. " + PROSE)
    a.end_para()
    commented = a.sel.Start
    a.typed("Nobody in the house would admit to having written it. " + PROSE)
    a.doc.Comments.Add(a.doc.Range(commented, commented + 6), "Is this the right word?")
    a.to_end()
    a.end_para()

    a.sel.InsertBreak(SECTION_NEXT_PAGE)
    a.heading("CHAPTER TWO")
    a.typed("The answer, she decided, was in the town, and the town was reached by ")
    a.doc.Hyperlinks.Add(Anchor=a.here(), Address="https://example.org/road",
                         TextToDisplay="the old coast road")
    a.to_end()
    a.typed(". " + PROSE * 2)
    a.end_para()
    start = a.sel.Start
    a.typed("A sentence the author wrapped in a content control, "
            "as publishers' templates routinely do.")
    a.doc.ContentControls.Add(RICH_TEXT_CONTROL, a.doc.Range(start, a.sel.Start))
    a.to_end()
    a.end_para()
    a.para("It was dark by the time she reached the harbour. " + PROSE)
    a.heading("ΚΕΦΑΛΑΙΟ ΤΡΙΤΟ")
    a.para(GREEK * 2)
    a.typed(GREEK)
    note(a, a.doc.Endnotes, "Η σημείωση αυτή βρίσκεται στο τέλος του βιβλίου.")
    a.to_end()
    a.end_para()
    a.heading("COLOPHON")
    a.para("Set in a typeface chosen for its patience.")

    a.doc.TablesOfContents.Add(toc_at, UseHeadingStyles=True,
                               UpperHeadingLevel=1, LowerHeadingLevel=1)


def technical(a: Author) -> None:
    a.para("FIELD NOTES ON COASTAL EROSION")
    a.page_break()
    a.heading("CHAPTER ONE")
    a.para(PROSE * 2)
    a.para("The rate of retreat is measured along fixed transects, summarised below.")
    table = a.doc.Tables.Add(a.here(), 3, 3)
    rows = (("Site", "Retreat (m/yr)", "Notes"), ("North cove", "0.4", "stable"),
            ("Lighthouse", "1.9", ""))
    for r, cells in enumerate(rows, start=1):
        for c, text in enumerate(cells, start=1):
            table.Cell(r, c).Range.Text = text
    inner = table.Cell(3, 3).Range
    inner.Collapse(1)  # wdCollapseStart
    nested = table.Cell(3, 3).Tables.Add(inner, 2, 2)
    for (r, c), text in {(1, 1): "winter", (1, 2): "2.6",
                         (2, 1): "summer", (2, 2): "1.2"}.items():
        nested.Cell(r, c).Range.Text = text
    a.to_end()
    a.para("Rates are averaged over the whole survey period. " + PROSE)

    a.heading("CHAPTER TWO")
    anchor = a.here()
    a.para(PROSE * 2)
    box = a.doc.Shapes.AddTextbox(TEXTBOX_HORIZONTAL, 300, 100, 180, 90, anchor)
    box.TextFrame.TextRange.Text = "Sidebar: a text box the author floated beside the prose."
    a.to_end()
    a.typed("A storm wave does disproportionate damage. " + PROSE)
    note(a, a.doc.Endnotes, "After the linear wave theory of Airy.")
    a.to_end()
    a.end_para()


def equation(a: Author) -> None:
    """Kept apart from `technical`: ingest refuses an equation outright (nothing
    downstream renders one), so this file pins the refusal, not a build."""
    a.heading("CHAPTER ONE")
    a.typed(PROSE + "The energy of a breaking wave grows with the square of its height: ")
    start = a.sel.Start
    a.typed("E=1/8ρgH^2")
    a.doc.OMaths.Add(a.doc.Range(start, a.sel.Start))
    a.doc.OMaths(1).BuildUp()
    a.to_end()
    a.typed(".")
    a.end_para()


def _png(path: Path) -> Path:
    """A small solid PNG, written with the stdlib so the script needs no image library."""
    import struct
    import zlib

    width = height = 16
    rows = b"".join(b"\x00" + b"\x80\x60\x40" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


def bold(a: Author, text: str) -> None:
    """A heading as academic manuscripts set one: bold, Normal style, no heading style."""
    a.style(NORMAL)
    a.sel.Font.Bold = True
    a.typed(text)
    a.end_para()
    a.sel.Font.Bold = False


def thesis(a: Author) -> None:
    """The conventions of the first real manuscript ingested (a Greek academic book),
    reproduced with synthetic prose -- the book itself is not ours to publish.

    Its headings are bold Normal-style lines with typed numbers ("4.3.1 ..."), its
    contents page is typed by hand, its prologue carries Word list numbering, a
    footnote holds a photograph, two SmartArt diagrams carry text, and its
    bibliography entries are styled `Heading 1`/`Heading 3`. Every one of those
    broke ingest: the whole book was one chapter titled by a citation."""
    a.para("ΤΟ ΣΠΙΤΙ ΣΤΗΝ ΑΚΡΗ ΤΟΥ ΔΡΟΜΟΥ")
    a.para("Μελέτη")
    a.page_break()
    bold(a, "Περιεχόμενα")
    for line in ("1. Πρόλογος", "2. Εισαγωγή", "2.1 Η βροχή και η θάλασσα",
                 "3. Η επιστολή", "Επιλογικά εξαγόμενα", "Βιβλιογραφία"):
        a.para(line)
    a.page_break()

    a.style(NORMAL)
    a.sel.Font.Bold = True
    a.typed("Πρόλογος")
    a.sel.Range.ListFormat.ApplyNumberDefault()
    a.end_para()
    a.sel.Range.ListFormat.RemoveNumbers()
    a.sel.Font.Bold = False
    a.para(GREEK * 2)

    bold(a, "2. Εισαγωγή")
    a.typed(GREEK * 2)
    note(a, a.doc.Footnotes, "Το φυτό που περιγράφεται, όπως φωτογραφήθηκε.")
    photo = _png(HERE / "_note-photo.png")
    try:
        a.doc.Footnotes(1).Range.InlineShapes.AddPicture(str(photo))
    finally:
        photo.unlink()
    a.to_end()
    a.end_para()
    bold(a, "2.1 Η βροχή και η θάλασσα")
    a.para(GREEK)
    a.para("1. Ένα αριθμημένο σημείο μέσα στο κείμενο, όχι επικεφαλίδα: " + GREEK)
    bold(a, "Πρόκληση")
    anchor = a.here()
    a.para(GREEK)
    art = a.doc.Shapes.AddSmartArt(a.doc.Application.SmartArtLayouts(1), 50, 50, 300, 150, anchor)
    for i, label in enumerate(("Ερώτηση", "Έλεγχος", "Ορισμός"), start=1):
        art.SmartArt.AllNodes(i).TextFrame2.TextRange.Text = label
    a.to_end()

    bold(a, "3. Η επιστολή")
    a.para(GREEK * 2)
    bold(a, "Επιλογικά εξαγόμενα")
    a.para(GREEK * 2)
    bold(a, "Βιβλιογραφία")
    a.para("Αλεξίου, Α. (2001). Ένα βιβλίο. Αθήνα: Εκδόσεις Δοκιμή.")
    a.para("Βασιλείου, Β. (1996). «Ένα άρθρο». Περιοδικό 27: 11-31.", HEADING_1)
    a.para("Γεωργίου, Γ. (2010). Ένα ακόμη βιβλίο. Θεσσαλονίκη: Εκδόσεις Δοκιμή.", -4)  # Heading 3
    a.para("Δημητρίου, Δ. (1980). Το τελευταίο βιβλίο. Αθήνα: Εκδόσεις Δοκιμή.")


def build(name: str, write) -> None:
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        doc = word.Documents.Add()
        write(Author(word, doc))
        doc.RemovePersonalInformation = True
        doc.SaveAs2(str(HERE / name), FileFormat=DOCX)
        doc.Close(False)
    finally:
        word.Quit()
    print("wrote", HERE / name)


if __name__ == "__main__":
    build("word-novel.docx", novel)
    build("word-technical.docx", technical)
    build("word-equation.docx", equation)
    build("word-thesis.docx", thesis)
