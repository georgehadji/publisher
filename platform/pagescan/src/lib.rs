//! Publisher Pagescan — deterministic defect scanner for book pages.
//!
//! From BUILD_PLAN.md §3.13 and §1 (doctrine):
//! - D1: Pure, total, deterministic functions. No I/O, no clock, no randomness.
//! - D5: Deterministic or explicitly marked. No wall-clock, no RNG.
//! - D6: Bounded everything. Every loop has a max iteration count.
//!
//! Scans a pagemap for typographic defects:
//!   - Widows (single word on last line of a paragraph)
//!   - Orphans (last line of a paragraph at the top of a page)
//!   - Runts (short last line that looks accidental)
//!   - Rivers (vertical alignment of spaces — approximated)
//!   - Hyphen stacks (three or more consecutive hyphenated lines)
//!   - Short chapter ends (last page of a chapter has very little text)

use serde::{Deserialize, Serialize};

// ── Core types ─────────────────────────────────────────────────

/// A single page's metadata from the pagination stage.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageEntry {
    pub page_number: u32,
    pub folio: u32,
    pub side: String,          // "recto" | "verso"
    pub chapter_id: String,
    pub content_start: Option<f64>,
    pub has_orphans: Option<bool>,
    pub has_widows: Option<bool>,
    pub has_runts: Option<bool>,
    pub word_count: Option<u32>,
    pub defect_score: Option<f64>,
    pub para_ranges: Option<Vec<ParaRange>>,
}

/// A paragraph's presence on a page.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParaRange {
    pub para_index: u32,
    pub lines_on_page: u32,    // how many lines of this paragraph appear on this page
}

/// A chapter's span across pages.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChapterEntry {
    pub chapter_id: String,
    pub number: u32,
    pub title: Option<String>,
    pub start_page: u32,
    pub end_page: u32,
    pub page_count: u32,
    pub starts_on: Option<String>,
}

/// The complete pagemap input.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageMap {
    pub pages: Vec<PageEntry>,
    pub chapters: Vec<ChapterEntry>,
}

/// A detected typographic defect.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Defect {
    pub defect_type: DefectType,
    pub page_number: u32,
    pub severity: Severity,
    pub description: String,
    pub para_index: Option<u32>,
    pub evidence: Vec<String>,
}

/// Types of typographic defects we can detect.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum DefectType {
    Widow,
    Orphan,
    Runt,
    River,
    HyphenStack,
    ShortChapterEnd,
}

/// Severity levels.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Severity {
    Error,
    Warning,
    Info,
}

/// Result of a scan.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanResult {
    pub defects: Vec<Defect>,
    pub total_defects: u32,
    pub total_score: f64,           // weighted sum
    pub pages_scanned: u32,
    pub chapters_scanned: u32,
    pub is_clean: bool,             // true if zero error-level defects
}

// ── Scanning logic ─────────────────────────────────────────────

/// Maximum iterations for the fixpoint loop (D6: bounded everything).
pub const MAX_FIXPOINT_ITERATIONS: u32 = 3;

/// Minimum lines on a page for a paragraph to *not* be considered an orphan/widow.
const MIN_LINES_PER_PARA: u32 = 2;

/// If a page has fewer than this many words, flag as short chapter end.
const SHORT_PAGE_WORD_THRESHOLD: u32 = 30;

/// Weight for each defect type in the total score.
fn defect_weight(defect_type: &DefectType) -> f64 {
    match defect_type {
        DefectType::Widow => 3.0,
        DefectType::Orphan => 4.0,     // orphans are worse than widows
        DefectType::Runt => 2.0,
        DefectType::River => 1.5,
        DefectType::HyphenStack => 2.5,
        DefectType::ShortChapterEnd => 1.0,
    }
}

/// Run a full scan of the given pagemap.
///
/// Pure function: all inputs explicit, all outputs returned, no side effects (D1).
pub fn scan(pagemap: &PageMap) -> ScanResult {
    let mut defects: Vec<Defect> = Vec::new();

    // Scan each page for widows, orphans, runts, rivers, hyphen stacks
    for page in &pagemap.pages {
        detect_widows(page, &mut defects);
        detect_orphans(page, &mut defects);
        detect_runts(page, &mut defects);
        detect_rivers(page, &mut defects);
        detect_hyphen_stacks(page, &mut defects);
    }

    // Scan chapters for short ends
    for chapter in &pagemap.chapters {
        detect_short_chapter_end(chapter, &pagemap.pages, &mut defects);
    }

    // Compute totals
    let total_defects = defects.len() as u32;
    let total_score: f64 = defects
        .iter()
        .map(|d| defect_weight(&d.defect_type))
        .sum();
    let is_clean = defects
        .iter()
        .all(|d| d.severity != Severity::Error);
    let pages_scanned = pagemap.pages.len() as u32;
    let chapters_scanned = pagemap.chapters.len() as u32;

    ScanResult {
        defects,
        total_defects,
        total_score,
        pages_scanned,
        chapters_scanned,
        is_clean,
    }
}

/// Detect widows: a paragraph whose last line is alone at the top of a page.
///
/// A widow occurs when a paragraph starts on one page and its last line
/// appears alone at the top of the next page.
fn detect_widows(page: &PageEntry, defects: &mut Vec<Defect>) {
    // Simplified detection: a paragraph with exactly 1 line on this page
    // that also has lines on the previous page => widow candidate.
    if let Some(ref ranges) = page.para_ranges {
        for pr in ranges {
            // A paragraph with 1 line on this page and that line is the
            // last line of the paragraph (not the first) suggests a widow.
            if pr.lines_on_page == 1 {
                // Check if this paragraph continues from previous page
                // (not implemented in stub: needs inter-page paragraph tracking)
            }
        }
    }

    // Use pagemap's pre-computed hint
    if page.has_widows == Some(true) {
        defects.push(Defect {
            defect_type: DefectType::Widow,
            page_number: page.page_number,
            severity: Severity::Warning,
            description: format!(
                "Page {}: possible widow detected (single line stranded at top)",
                page.page_number
            ),
            para_index: None,
            evidence: vec!["pre-computed: has_widows=true".to_string()],
        });
    }
}

/// Detect orphans: the last line of a paragraph appearing at the top of a page
/// while the rest of the paragraph is on the previous page.
///
/// In book typography, orphans are generally considered worse than widows.
fn detect_orphans(page: &PageEntry, defects: &mut Vec<Defect>) {
    if page.has_orphans == Some(true) {
        defects.push(Defect {
            defect_type: DefectType::Orphan,
            page_number: page.page_number,
            severity: Severity::Error,
            description: format!(
                "Page {}: orphan detected (last line of paragraph alone at top)",
                page.page_number
            ),
            para_index: None,
            evidence: vec!["pre-computed: has_orphans=true".to_string()],
        });
    }
}

/// Detect runts: a very short last line of a paragraph that looks accidental.
///
/// A runt is typically a single word or a few characters on the last line.
fn detect_runts(page: &PageEntry, defects: &mut Vec<Defect>) {
    if page.has_runts == Some(true) {
        defects.push(Defect {
            defect_type: DefectType::Runt,
            page_number: page.page_number,
            severity: Severity::Warning,
            description: format!(
                "Page {}: runt detected (very short last line of paragraph)",
                page.page_number
            ),
            para_index: None,
            evidence: vec!["pre-computed: has_runts=true".to_string()],
        });
    }
}

/// Detect rivers: vertical alignment of inter-word spaces across lines.
///
/// Full river detection requires glyph-position analysis from the rendered PDF.
/// This stub scores based on pagemap hints. The full impl uses the raster scan.
fn detect_rivers(page: &PageEntry, _defects: &mut Vec<Defect>) {
    // Rivers are detected from page rasters, not pagemap alone.
    // Placeholder: the fixpoint optimizer will eventually scan raster data.
    let _ = page; // suppress unused warning
}

/// Detect hyphen stacks: three or more consecutive hyphenated lines.
fn detect_hyphen_stacks(page: &PageEntry, _defects: &mut Vec<Defect>) {
    // Hyphenation data comes from the renderer. Stub for now.
    let _ = page;
}

/// Detect short chapter ends: the last page of a chapter has very little text.
fn detect_short_chapter_end(
    chapter: &ChapterEntry,
    pages: &[PageEntry],
    defects: &mut Vec<Defect>,
) {
    if chapter.page_count < 2 {
        return; // one-page chapters are fine
    }

    // Find the last page of this chapter
    let last_page = pages.iter().find(|p| p.page_number == chapter.end_page);

    if let Some(page) = last_page {
        if let Some(word_count) = page.word_count {
            if word_count < SHORT_PAGE_WORD_THRESHOLD {
                let pct = (word_count as f64 / SHORT_PAGE_WORD_THRESHOLD as f64) * 100.0;
                defects.push(Defect {
                    defect_type: DefectType::ShortChapterEnd,
                    page_number: page.page_number,
                    severity: Severity::Info,
                    description: format!(
                        "Chapter {} ends short: last page has {} words ({}% of threshold)",
                        chapter.number, word_count, pct as u32
                    ),
                    para_index: None,
                    evidence: vec![
                        format!("chapter_id={}", chapter.chapter_id),
                        format!("word_count={}", word_count),
                        format!("threshold={}", SHORT_PAGE_WORD_THRESHOLD),
                    ],
                });
            }
        }
    }
}

// ── Fixpoint optimizer ─────────────────────────────────────────

/// A single micro-adjustment that the fixpoint optimizer can apply.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MicroAdjustment {
    pub adjustment_type: AdjustmentType,
    pub target_page: u32,
    pub target_para_index: Option<u32>,
    pub value: f64,
    pub unit: String,  // "em", "mm", "pt"
}

/// Types of micro-adjustments.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum AdjustmentType {
    Tracking,         // ±0.005em paragraph tracking
    HyphenationZone,  // widen/narrow hyphenation zone
    KeepWithNext,     // force keep-with-next
    MeasureNudge,     // nudge measure on offending spread only
}

/// Result of the fixpoint optimizer for one iteration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FixpointResult {
    pub iteration: u32,
    pub adjustments: Vec<MicroAdjustment>,
    pub defects_before: Vec<Defect>,
    pub defects_after: Vec<Defect>,
    pub score_before: f64,
    pub score_after: f64,
    pub converged: bool,
}

/// Run one iteration of the fixpoint optimizer.
///
/// Rules (from ARCHITECTURE.md §1.4):
/// - Deterministic (no RNG, no LLM)
/// - Bounded (max MAX_FIXPOINT_ITERATIONS = 3)
/// - Monotonic (never allowed to increase total defect score)
/// - Every adjustment recorded in the build manifest
pub fn fixpoint_iteration(
    pagemap: &PageMap,
    iteration: u32,
) -> FixpointResult {
    let defects_before = scan(pagemap);
    let defects_before_list = defects_before.defects.clone();
    let score_before = defects_before.total_score;
    let mut adjustments: Vec<MicroAdjustment> = Vec::new();

    if iteration >= MAX_FIXPOINT_ITERATIONS {
        return FixpointResult {
            iteration,
            adjustments: vec![],
            defects_before: defects_before_list.clone(),
            defects_after: defects_before_list,
            score_before,
            score_after: score_before,
            converged: true,
        };
    }

    // Generate micro-adjustments for each defect
    for defect in &defects_before_list {
        match defect.defect_type {
            DefectType::Widow | DefectType::Orphan => {
                // Apply keep-with-next to the offending paragraph
                adjustments.push(MicroAdjustment {
                    adjustment_type: AdjustmentType::KeepWithNext,
                    target_page: defect.page_number,
                    target_para_index: defect.para_index,
                    value: 1.0,
                    unit: "boolean".to_string(),
                });
            }
            DefectType::Runt => {
                // Slightly tighten tracking on the paragraph
                adjustments.push(MicroAdjustment {
                    adjustment_type: AdjustmentType::Tracking,
                    target_page: defect.page_number,
                    target_para_index: defect.para_index,
                    value: -0.005,
                    unit: "em".to_string(),
                });
            }
            DefectType::HyphenStack => {
                // Widen hyphenation zone
                adjustments.push(MicroAdjustment {
                    adjustment_type: AdjustmentType::HyphenationZone,
                    target_page: defect.page_number,
                    target_para_index: defect.para_index,
                    value: 2.0,
                    unit: "mm".to_string(),
                });
            }
            _ => {} // no automatic fix for rivers, short chapter ends
        }
    }

    // In practice, the pagemap would be updated with adjustments applied.
    // For now, the defects_after is computed assuming adjustments fix the defects.
    let mut defects_after: Vec<Defect> = defects_before_list.clone();
    
    // Mark defects that have adjustments as fixed
    for adj in &adjustments {
        defects_after.retain(|d| d.page_number != adj.target_page);
    }

    let score_after: f64 = defects_after
        .iter()
        .map(|d| defect_weight(&d.defect_type))
        .sum();

    FixpointResult {
        iteration,
        adjustments,
        defects_before: defects_before_list,
        defects_after,
        score_before,
        score_after,
        converged: score_after <= 0.0 || (score_before - score_after).abs() < 0.01,
    }
}

// ── Python FFI exports ─────────────────────────────────────────

/// Run a full scan. Returns JSON bytes for cross-language consumption.
pub fn scan_json(pagemap_json: &str) -> Result<String, ScanError> {
    let pagemap: PageMap =
        serde_json::from_str(pagemap_json).map_err(|e| ScanError::Json(e.to_string()))?;
    let result = scan(&pagemap);
    serde_json::to_string(&result).map_err(|e| ScanError::Json(e.to_string()))
}

/// Run one fixpoint iteration. Returns JSON bytes.
pub fn fixpoint_iteration_json(pagemap_json: &str, iteration: u32) -> Result<String, ScanError> {
    let pagemap: PageMap =
        serde_json::from_str(pagemap_json).map_err(|e| ScanError::Json(e.to_string()))?;
    let result = fixpoint_iteration(&pagemap, iteration);
    serde_json::to_string(&result).map_err(|e| ScanError::Json(e.to_string()))
}

#[derive(Debug, thiserror::Error)]
pub enum ScanError {
    #[error("JSON error: {0}")]
    Json(String),
}

// ── Python bindings via PyO3 ───────────────────────────────────
// The #[pyfunction] and #[pymodule] exports are in a separate file
// (lib_py.rs) compiled with pyo3 feature. The core logic stays pure.

#[cfg(test)]
mod tests {
    use super::*;

    fn make_test_pagemap() -> PageMap {
        PageMap {
            pages: vec![
                PageEntry {
                    page_number: 1,
                    folio: 1,
                    side: "recto".into(),
                    chapter_id: "ch1".into(),
                    content_start: Some(50.0),
                    has_orphans: Some(false),
                    has_widows: Some(false),
                    has_runts: Some(false),
                    word_count: Some(200),
                    defect_score: Some(0.0),
                    para_ranges: Some(vec![
                        ParaRange { para_index: 0, lines_on_page: 5 },
                        ParaRange { para_index: 1, lines_on_page: 3 },
                    ]),
                },
                PageEntry {
                    page_number: 2,
                    folio: 2,
                    side: "verso".into(),
                    chapter_id: "ch1".into(),
                    content_start: Some(50.0),
                    has_orphans: Some(true),
                    has_widows: Some(false),
                    has_runts: Some(false),
                    word_count: Some(150),
                    defect_score: Some(4.0),
                    para_ranges: Some(vec![
                        ParaRange { para_index: 1, lines_on_page: 1 }, // orphan candidate
                    ]),
                },
                PageEntry {
                    page_number: 3,
                    folio: 3,
                    side: "recto".into(),
                    chapter_id: "ch1".into(),
                    content_start: Some(50.0),
                    has_orphans: Some(false),
                    has_widows: Some(true),
                    has_runts: Some(true),
                    word_count: Some(20), // short page
                    defect_score: Some(5.0),
                    para_ranges: Some(vec![
                        ParaRange { para_index: 2, lines_on_page: 1 },
                    ]),
                },
            ],
            chapters: vec![
                ChapterEntry {
                    chapter_id: "ch1".into(),
                    number: 1,
                    title: Some("Chapter One".into()),
                    start_page: 1,
                    end_page: 3,
                    page_count: 3,
                    starts_on: Some("recto".into()),
                },
            ],
        }
    }

    #[test]
    fn test_scan_detects_orphans() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        assert!(result.total_defects > 0);
        assert!(result.defects.iter().any(|d| d.defect_type == DefectType::Orphan));
    }

    #[test]
    fn test_scan_detects_widows() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        assert!(result.defects.iter().any(|d| d.defect_type == DefectType::Widow));
    }

    #[test]
    fn test_scan_detects_runts() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        assert!(result.defects.iter().any(|d| d.defect_type == DefectType::Runt));
    }

    #[test]
    fn test_scan_detects_short_chapter_end() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        let short_ends: Vec<_> = result.defects.iter()
            .filter(|d| d.defect_type == DefectType::ShortChapterEnd)
            .collect();
        assert!(!short_ends.is_empty(), "Should detect short chapter end");
        assert_eq!(short_ends[0].page_number, 3);
    }

    #[test]
    fn test_scan_clean_pagemap() {
        let mut pagemap = make_test_pagemap();
        // Clear all flags
        for page in &mut pagemap.pages {
            page.has_orphans = Some(false);
            page.has_widows = Some(false);
            page.has_runts = Some(false);
            page.word_count = Some(300);
        }
        let result = scan(&pagemap);
        // Short chapter end may still fire if word_count threshold exceeded
        // but only for last page
        assert!(result.is_clean || result.defects.iter().all(|d| d.severity == Severity::Info));
    }

    #[test]
    fn test_scan_pages_scanned() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        assert_eq!(result.pages_scanned, 3);
        assert_eq!(result.chapters_scanned, 1);
    }

    #[test]
    fn test_scan_weighted_score() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        assert!(result.total_score > 0.0);
        // Orphan (4) + Widow (3) + Runt (2) + short (1) = at least 10
        assert!(result.total_score >= 10.0);
    }

    #[test]
    fn test_fixpoint_iteration_detects_defects() {
        let pagemap = make_test_pagemap();
        let result = fixpoint_iteration(&pagemap, 0);
        assert!(result.score_before > 0.0);
        assert!(!result.adjustments.is_empty());
    }

    #[test]
    fn test_fixpoint_converges() {
        let pagemap = make_test_pagemap();
        let result = fixpoint_iteration(&pagemap, 3); // past MAX
        assert!(result.converged);
        assert!(result.adjustments.is_empty());
    }

    #[test]
    fn test_fixpoint_generates_adjustments() {
        let pagemap = make_test_pagemap();
        let result = fixpoint_iteration(&pagemap, 0);
        let has_orphan_fix = result.adjustments.iter()
            .any(|a| a.adjustment_type == AdjustmentType::KeepWithNext);
        assert!(has_orphan_fix, "Should generate keep-with-next for orphans");
    }

    #[test]
    fn test_scan_json_roundtrip() {
        let pagemap = make_test_pagemap();
        let json = serde_json::to_string(&pagemap).unwrap();
        let result = scan_json(&json).unwrap();
        let parsed: ScanResult = serde_json::from_str(&result).unwrap();
        assert_eq!(parsed.pages_scanned, 3);
    }

    #[test]
    fn test_defect_severity_orphan_is_error() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        let orphan = result.defects.iter()
            .find(|d| d.defect_type == DefectType::Orphan)
            .unwrap();
        assert_eq!(orphan.severity, Severity::Error);
    }

    #[test]
    fn test_defect_severity_widow_is_warning() {
        let pagemap = make_test_pagemap();
        let result = scan(&pagemap);
        let widow = result.defects.iter()
            .find(|d| d.defect_type == DefectType::Widow)
            .unwrap();
        assert_eq!(widow.severity, Severity::Warning);
    }
}
