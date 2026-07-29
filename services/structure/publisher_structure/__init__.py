"""
Publisher Structure Service

Rules-first structure inference with LLM disambiguation for low-confidence nodes.
"""

from .rules import parse_html, classify_blocks, find_low_confidence, build_ast_draft, compute_text_integrity
from .inference import InferenceGateway, InferenceRequest, InferenceResult, ModelTier, RouteConfig
from .overrides import OverrideOp, OverrideSet, rebase_overrides, apply_overrides
