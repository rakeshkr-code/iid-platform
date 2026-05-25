"""Robust figure/caption linking for Docling documents.

This module is built for downstream RAG / agentic LLM pipelines where you need a
stable association between pictures and their captions across messy PDF layouts:

- multi-column grids
- asymmetric grids
- captions wider than the figure
- captions split across multiple text blocks
- figures with missing or partial Docling caption links

Core idea
---------
1. Trust Docling's direct caption links first.
2. Normalize every bbox into a single internal coordinate system.
3. Merge nearby caption-like text blocks into candidate caption clusters.
4. Score candidate figure/caption pairs using geometry + layout blockers.
5. Be conservative: prefer "unmatched" over a wrong match.

This code is dependency-free apart from Docling's document objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple

from pathlib import Path

import io
import sys
import time
import fitz
from PIL import Image
from IPython.display import display


# =============================================================================
# Geometry
# =============================================================================


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in a unified top-left coordinate system.

    The internal representation always follows this convention:

    - x increases to the right
    - y increases downward
    - top <= bottom
    - left <= right

    That makes the code intuitive for page-layout logic:

    - a text box below a figure has a larger `top` value
    - a text box above a figure has a smaller `bottom` value
    """

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def cx(self) -> float:
        """X-axis center of the rectangle."""
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        """Y-axis center of the rectangle."""
        return (self.top + self.bottom) / 2.0

    def expand(self, dx: float, dy: float) -> "Rect":
        return Rect(
            left=self.left - dx,
            top=self.top - dy,
            right=self.right + dx,
            bottom=self.bottom + dy,
        )

    def x_overlap(self, other: "Rect") -> float:
        return max(0.0, min(self.right, other.right) - max(self.left, other.left))

    def y_overlap(self, other: "Rect") -> float:
        return max(0.0, min(self.bottom, other.bottom) - max(self.top, other.top))

    def intersection_area(self, other: "Rect") -> float:
        return self.x_overlap(other) * self.y_overlap(other)

    def horizontal_overlap_ratio(self, other: "Rect") -> float:
        """Horizontal overlap divided by the smaller width.

        This is more permissive than exact edge equality and works well for
        captions that are slightly wider than the figure.
        """

        denom = max(1e-9, min(self.width, other.width))
        return self.x_overlap(other) / denom

    def contains_x(self, x: float) -> bool:
        return self.left <= x <= self.right


@dataclass(frozen=True)
class PageFigure:
    """Normalized figure extracted from Docling."""

    ref: str
    page_no: int
    bbox: Rect
    raw: Any


@dataclass(frozen=True)
class PageText:
    """Normalized text item extracted from Docling."""

    ref: str
    page_no: int
    bbox: Rect
    text: str
    label: str
    raw: Any

    @property
    def is_caption_like(self) -> bool:
        """Return True for explicit captions and caption-like fallback text."""

        t = self.text.strip()
        if not t:
            return False
        if self.label.lower() == "caption":
            return True
        # Conservative fallback for mis-labeled captions.
        prefixes = ("fig", "figure", "plate", "photo", "pic", "image")
        return t[:8].strip().lower().startswith(prefixes)


@dataclass(frozen=True)
class CaptionCluster:
    """One candidate caption block, possibly merged from multiple text items."""

    refs: Tuple[str, ...]
    page_no: int
    bbox: Rect
    text: str
    labels: Tuple[str, ...]
    raw_items: Tuple[Any, ...]

    @property
    def is_explicit_caption(self) -> bool:
        return any(lbl.lower() == "caption" for lbl in self.labels)


@dataclass(frozen=True)
class CaptionAssociation:
    """Final figure-to-caption result."""

    figure_ref: str
    page_no: int
    figure_bbox: Rect
    caption_ref: Optional[str]
    caption_text: Optional[str]
    caption_bbox: Optional[Rect]
    source: str
    score: float


@dataclass(frozen=True)
class LinkerConfig:
    """Tunable thresholds for figure-caption linking."""

    trust_docling_direct_links: bool = True

    # Figure-caption distance thresholds in normalized page coordinates.
    max_below_gap: float = 180.0
    max_above_gap: float = 140.0

    # Minimum horizontal alignment signal.
    min_horizontal_overlap_ratio: float = 0.08
    max_center_dx_ratio: float = 0.55

    # Expand figure x-span to support wider captions in asymmetric layouts.
    figure_expand_x_ratio: float = 0.20
    figure_expand_x_min: float = 10.0

    # Small tolerance to cope with OCR/layout jitter.
    vertical_tolerance: float = 4.0

    # Direct Docling caption links get first priority.
    direct_caption_bonus: float = 10_000.0
    caption_label_bonus: float = 80.0

    # Penalize very long captions only slightly.
    long_caption_penalty_per_200_chars: float = 2.0

    # Caption clustering.
    cluster_vertical_gap: float = 12.0
    cluster_x_overlap_ratio: float = 0.15

    # Prevent a caption from crossing through another figure lane.
    blocker_penalty: float = 35.0


# =============================================================================
# Docling normalization helpers
# =============================================================================


def _ref_from_obj(obj: Any) -> str:
    if hasattr(obj, "self_ref"):
        return str(getattr(obj, "self_ref"))
    raise AttributeError("Object does not expose self_ref")


def _text_from_obj(obj: Any) -> str:
    text = getattr(obj, "text", None)
    if text is None:
        text = getattr(obj, "orig", "")
    return str(text)


def _page_no_from_prov(obj: Any) -> int:
    prov = getattr(obj, "prov", None)
    if not prov:
        raise ValueError(f"No provenance found for {obj!r}")
    page_no = getattr(prov[0], "page_no", None)
    if page_no is None:
        raise ValueError(f"No page number found for {obj!r}")
    return int(page_no)


def _page_size_map(doc: Any) -> Dict[int, float]:
    """Return page heights keyed by page number.

    If page heights are unavailable, an empty mapping is returned and the bbox
    normalizer falls back to a safer min/max normalization.
    """

    out: Dict[int, float] = {}
    pages = getattr(doc, "pages", None) or {}
    if isinstance(pages, Mapping):
        for page_no, page in pages.items():
            size = getattr(page, "size", None)
            if size is None:
                continue
            height = getattr(size, "height", None)
            if height is None:
                continue
            out[int(page_no)] = float(height)
    return out


def _raw_bbox_to_rect(bbox: Any, page_height: Optional[float]) -> Rect:
    """Normalize a Docling bbox into the internal top-left coordinate system.

    Docling provenance may come from PDFs with `CoordOrigin.BOTTOMLEFT` or other
    origins. When page height is known, we transform everything to a top-left
    system (`y` increases downward) so layout logic stays consistent.

    If page height is unavailable, we fall back to min/max normalization.
    """

    def _get(obj: Any, *names: str) -> float:
        for name in names:
            if isinstance(obj, Mapping) and name in obj:
                return float(obj[name])
            if hasattr(obj, name):
                return float(getattr(obj, name))
        raise AttributeError(f"bbox does not expose any of {names}")

    l = _get(bbox, "l", "left")
    t = _get(bbox, "t", "top")
    r = _get(bbox, "r", "right")
    b = _get(bbox, "b", "bottom")

    left = min(l, r)
    right = max(l, r)

    # Try to detect coordinate origin from the bbox if available.
    coord_origin = None
    if hasattr(bbox, "coord_origin"):
        coord_origin = str(getattr(bbox, "coord_origin"))
    elif isinstance(bbox, Mapping) and "coord_origin" in bbox:
        coord_origin = str(bbox["coord_origin"])

    # With a known page height, convert all y-values to top-left coordinates.
    if page_height is not None:
        # In bottom-left PDF coordinates, larger raw y is visually higher.
        # Mapping to top-left is done by y' = page_height - y.
        # This also works safely for most mixed provenance cases.
        y1 = page_height - max(t, b)
        y2 = page_height - min(t, b)
        top = min(y1, y2)
        bottom = max(y1, y2)
        return Rect(left=left, top=top, right=right, bottom=bottom)

    # Fallback: assume numeric labels are already usable and just sort them.
    top = min(t, b)
    bottom = max(t, b)
    return Rect(left=left, top=top, right=right, bottom=bottom)


def _bbox_from_prov(obj: Any, page_height: Optional[float]) -> Rect:
    prov = getattr(obj, "prov", None)
    if not prov:
        raise ValueError(f"No provenance found for {obj!r}")
    bbox = getattr(prov[0], "bbox", None)
    if bbox is None:
        raise ValueError(f"No bbox found for {obj!r}")
    return _raw_bbox_to_rect(bbox, page_height)


def _text_block_signature(rect: Rect) -> Tuple[float, float, float]:
    """Small signature used for clustering nearby text blocks."""

    return (round(rect.top, 1), round(rect.left, 1), round(rect.right, 1))


# =============================================================================
# Main linker
# =============================================================================


class DoclingFigureCaptionLinker:
    """Conservative figure-caption linker for Docling documents.

    The linker uses a two-stage strategy:

    1. Use Docling's own figure->caption links when they exist.
    2. For unlinked figures, search the same page for the best caption cluster.

    The output is intentionally clean and page-aware so it can be serialized into
    a metadata store or used as part of a downstream RAG ingestion pipeline.
    """

    def __init__(self, config: Optional[LinkerConfig] = None) -> None:
        self.config = config or LinkerConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def link(self, doc: Any) -> List[CaptionAssociation]:
        """Return one association per figure in the Docling document."""

        page_heights = _page_size_map(doc)
        figures, texts = self._collect_items(doc, page_heights)

        figures_by_page: Dict[int, List[PageFigure]] = {}
        texts_by_page: Dict[int, List[PageText]] = {}
        for fig in figures:
            figures_by_page.setdefault(fig.page_no, []).append(fig)
        for txt in texts:
            texts_by_page.setdefault(txt.page_no, []).append(txt)

        results: List[CaptionAssociation] = []
        for page_no in sorted(figures_by_page):
            page_figures = sorted(figures_by_page[page_no], key=lambda x: (x.bbox.top, x.bbox.left))
            page_texts = texts_by_page.get(page_no, [])
            caption_clusters = self._build_caption_clusters(page_texts)
            results.extend(self._link_page(page_no, page_figures, caption_clusters))
        return results

    def to_dict(self, associations: Sequence[CaptionAssociation]) -> List[Dict[str, Any]]:
        """Serialize associations into JSON-friendly dictionaries."""

        out: List[Dict[str, Any]] = []
        for a in associations:
            out.append(
                {
                    "page_no": a.page_no,
                    "figure_ref": a.figure_ref,
                    "figure_bbox": _rect_to_dict(a.figure_bbox),
                    "caption_ref": a.caption_ref,
                    "caption_text": a.caption_text,
                    "caption_bbox": _rect_to_dict(a.caption_bbox) if a.caption_bbox else None,
                    "source": a.source,
                    "score": a.score,
                }
            )
        return out

    # ------------------------------------------------------------------
    # Collection and normalization
    # ------------------------------------------------------------------

    def _collect_items(self, doc: Any, page_heights: Mapping[int, float]) -> Tuple[List[PageFigure], List[PageText]]:
        figures: List[PageFigure] = []
        texts: List[PageText] = []

        for picture in getattr(doc, "pictures", []):
            try:
                page_no = _page_no_from_prov(picture)
                bbox = _bbox_from_prov(picture, page_heights.get(page_no))
            except Exception:
                continue
            figures.append(PageFigure(ref=_ref_from_obj(picture), page_no=page_no, bbox=bbox, raw=picture))

        for text_item in getattr(doc, "texts", []):
            try:
                page_no = _page_no_from_prov(text_item)
                bbox = _bbox_from_prov(text_item, page_heights.get(page_no))
            except Exception:
                continue
            label = str(getattr(text_item, "label", "")).split(".")[-1]
            texts.append(
                PageText(
                    ref=_ref_from_obj(text_item),
                    page_no=page_no,
                    bbox=bbox,
                    text=_text_from_obj(text_item),
                    label=label,
                    raw=text_item,
                )
            )
        return figures, texts

    # ------------------------------------------------------------------
    # Caption clustering
    # ------------------------------------------------------------------

    def _build_caption_clusters(self, texts: Sequence[PageText]) -> List[CaptionCluster]:
        """Merge adjacent caption-like text blocks into caption clusters.

        This matters when a single caption is split into multiple text items by the
        layout model or OCR.
        """

        cfg = self.config
        candidates = [t for t in texts if t.is_caption_like]
        if not candidates:
            return []

        # Sort top-to-bottom, then left-to-right.
        candidates = sorted(candidates, key=lambda t: (t.bbox.top, t.bbox.left, t.bbox.right))
        clusters: List[List[PageText]] = []

        for txt in candidates:
            placed = False
            for cluster in clusters:
                last = cluster[-1]
                # Same page, close vertically, and reasonably aligned in X.
                vertical_gap = txt.bbox.top - last.bbox.bottom
                same_lane = last.bbox.horizontal_overlap_ratio(txt.bbox) >= cfg.cluster_x_overlap_ratio
                if 0.0 <= vertical_gap <= cfg.cluster_vertical_gap and same_lane:
                    cluster.append(txt)
                    placed = True
                    break
            if not placed:
                clusters.append([txt])

        merged: List[CaptionCluster] = []
        for cluster in clusters:
            bbox = _union_bbox([t.bbox for t in cluster])
            refs = tuple(t.ref for t in cluster)
            labels = tuple(t.label for t in cluster)
            raw_items = tuple(t.raw for t in cluster)
            text = " ".join(t.text.strip() for t in cluster if t.text.strip()).strip()
            merged.append(CaptionCluster(refs=refs, page_no=cluster[0].page_no, bbox=bbox, text=text, labels=labels, raw_items=raw_items))

        return merged

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------

    def _link_page(
        self,
        page_no: int,
        figures: Sequence[PageFigure],
        caption_clusters: Sequence[CaptionCluster],
    ) -> List[CaptionAssociation]:
        used_caption_refs: set[str] = set()
        results: List[CaptionAssociation] = []

        for fig in figures:
            direct = self._direct_caption_for_figure(fig.raw, caption_clusters)

            # Current behavior: trust Docling's own link.
            if self.config.trust_docling_direct_links and direct is not None:
                results.append(
                    CaptionAssociation(
                        figure_ref=fig.ref,
                        page_no=page_no,
                        figure_bbox=fig.bbox,
                        caption_ref=direct.refs[0] if len(direct.refs) == 1 else " | ".join(direct.refs),
                        caption_text=direct.text,
                        caption_bbox=direct.bbox,
                        source="docling_direct",
                        score=self.config.direct_caption_bonus,
                    )
                )
                used_caption_refs.update(direct.refs)
                continue

            # New behavior: even if Docling direct link exists, still search best caption cluster.
            best = self._best_caption_candidate(fig, caption_clusters, used_caption_refs, figures)
            if best is None:
                results.append(
                    CaptionAssociation(
                        figure_ref=fig.ref,
                        page_no=page_no,
                        figure_bbox=fig.bbox,
                        caption_ref=None,
                        caption_text=None,
                        caption_bbox=None,
                        source="unmatched",
                        score=0.0,
                    )
                )
                continue

            cap, score, source = best
            results.append(
                CaptionAssociation(
                    figure_ref=fig.ref,
                    page_no=page_no,
                    figure_bbox=fig.bbox,
                    caption_ref=cap.refs[0] if len(cap.refs) == 1 else " | ".join(cap.refs),
                    caption_text=cap.text,
                    caption_bbox=cap.bbox,
                    source=source,
                    score=score,
                )
            )
            used_caption_refs.update(cap.refs)

        return results

    def _direct_caption_for_figure(
        self,
        picture_obj: Any,
        caption_clusters: Sequence[CaptionCluster],
    ) -> Optional[CaptionCluster]:
        """Return a caption cluster directly linked by Docling, if any."""

        direct_refs: List[str] = []

        # Prefer captions[] when populated.
        for ref_item in (getattr(picture_obj, "captions", None) or []):
            cref = getattr(ref_item, "cref", None)
            if cref:
                direct_refs.append(str(cref))

        # Fall back to child text refs that are caption-like.
        for ref_item in (getattr(picture_obj, "children", None) or []):
            cref = getattr(ref_item, "cref", None)
            if cref:
                direct_refs.append(str(cref))

        if not direct_refs:
            return None

        # Match any cluster that contains one of these refs.
        direct_set = set(direct_refs)
        for cluster in caption_clusters:
            if any(ref in direct_set for ref in cluster.refs):
                return cluster
        return None

    # ------------------------------------------------------------------
    # Candidate scoring
    # ------------------------------------------------------------------

    def _best_caption_candidate(
        self,
        fig: PageFigure,
        caption_clusters: Sequence[CaptionCluster],
        used_caption_refs: Iterable[str],
        all_figures: Sequence[PageFigure],
    ) -> Optional[Tuple[CaptionCluster, float, str]]:
        used = set(used_caption_refs)

        below = self._best_in_direction(fig, caption_clusters, used, all_figures, direction="below")
        above = self._best_in_direction(fig, caption_clusters, used, all_figures, direction="above")

        if below is None:
            return above
        if above is None:
            return below
        return below if below[1] >= above[1] else above

    def _best_in_direction(
        self,
        fig: PageFigure,
        caption_clusters: Sequence[CaptionCluster],
        used_caption_refs: set[str],
        all_figures: Sequence[PageFigure],
        direction: str,
    ) -> Optional[Tuple[CaptionCluster, float, str]]:
        cfg = self.config
        candidates: List[Tuple[CaptionCluster, float]] = []

        for cap in caption_clusters:
            if any(r in used_caption_refs for r in cap.refs):
                continue

            if direction == "below":
                gap = cap.bbox.top - fig.bbox.bottom
                if gap < -cfg.vertical_tolerance or gap > cfg.max_below_gap:
                    continue
                if not self._is_plausible_below(fig.bbox, cap.bbox):
                    continue
                blocker = self._blocker_penalty(fig, cap.bbox, all_figures, direction="below")
            else:
                gap = fig.bbox.top - cap.bbox.bottom
                if gap < -cfg.vertical_tolerance or gap > cfg.max_above_gap:
                    continue
                if not self._is_plausible_above(fig.bbox, cap.bbox):
                    continue
                blocker = self._blocker_penalty(fig, cap.bbox, all_figures, direction="above")

            score = self._score_candidate(fig, cap, gap=gap, direction=direction) - blocker
            if score > 0:
                candidates.append((cap, score))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[1], -self._gap_for_direction(fig.bbox, x[0].bbox, direction)), reverse=True)
        best = candidates[0]
        return best[0], best[1], f"geometric_{direction}"

    def _score_candidate(self, fig: PageFigure, cap: CaptionCluster, gap: float, direction: str) -> float:
        """Score a figure/caption pair.

        High score means a better match.
        The score prefers:
        - horizontal overlap or near-overlap
        - close x-centers
        - small vertical gap
        - explicit caption labels
        - captions that sit in the same visual column/lane
        """

        cfg = self.config
        fb = fig.bbox
        cb = cap.bbox

        # Expand figure x-span to handle captions wider than the picture.
        expand_x = max(cfg.figure_expand_x_min, cfg.figure_expand_x_ratio * fb.width)
        expanded = fb.expand(dx=expand_x, dy=0.0)

        overlap_ratio = fb.horizontal_overlap_ratio(cb)
        expanded_overlap = expanded.horizontal_overlap_ratio(cb)
        center_dx = abs(fb.cx - cb.cx)
        center_dx_ratio = center_dx / max(1e-9, max(fb.width, cb.width))

        gap_limit = cfg.max_below_gap if direction == "below" else cfg.max_above_gap
        gap_term = max(0.0, 1.0 - min(1.0, gap / max(1e-9, gap_limit)))

        score = 0.0
        score += 145.0 * min(1.0, expanded_overlap)
        score += 90.0 * min(1.0, overlap_ratio)
        score += 55.0 * max(0.0, 1.0 - min(1.0, center_dx_ratio / max(1e-9, cfg.max_center_dx_ratio)))
        score += 24.0 * gap_term

        # Bonus for explicit caption labels in any merged component.
        if cap.is_explicit_caption:
            score += cfg.caption_label_bonus

        # Small penalty for very long captions.
        score -= cfg.long_caption_penalty_per_200_chars * (len(cap.text.strip()) / 200.0)

        # Direction bias. Captions are more often below than above.
        if direction == "below":
            score += 10.0
        else:
            score += 4.0

        # Extra reward if the caption sits inside the x-band of the expanded figure.
        if expanded.left <= cb.cx <= expanded.right:
            score += 12.0 if direction == "below" else 8.0

        return score

    def _is_plausible_below(self, fig: Rect, cap: Rect) -> bool:
        """Quick geometry gate for a below-caption candidate."""

        cfg = self.config
        expand_x = max(cfg.figure_expand_x_min, cfg.figure_expand_x_ratio * fig.width)
        expanded = fig.expand(dx=expand_x, dy=0.0)

        if cap.top < fig.bottom - cfg.vertical_tolerance:
            return False

        overlap_ratio = fig.horizontal_overlap_ratio(cap)
        if overlap_ratio >= cfg.min_horizontal_overlap_ratio:
            return True

        if expanded.contains_x(cap.cx):
            return True

        if cap.contains_x(fig.cx):
            return True

        return False

    def _is_plausible_above(self, fig: Rect, cap: Rect) -> bool:
        """Quick geometry gate for an above-caption candidate."""

        cfg = self.config
        expand_x = max(cfg.figure_expand_x_min, cfg.figure_expand_x_ratio * fig.width)
        expanded = fig.expand(dx=expand_x, dy=0.0)

        if cap.bottom > fig.top + cfg.vertical_tolerance:
            return False

        overlap_ratio = fig.horizontal_overlap_ratio(cap)
        if overlap_ratio >= cfg.min_horizontal_overlap_ratio:
            return True

        if expanded.contains_x(cap.cx):
            return True

        if cap.contains_x(fig.cx):
            return True

        return False

    def _gap_for_direction(self, fig: Rect, cap: Rect, direction: str) -> float:
        if direction == "below":
            return max(0.0, cap.top - fig.bottom)
        return max(0.0, fig.top - cap.bottom)

    def _blocker_penalty(self, fig: PageFigure, cap: Rect, all_figures: Sequence[PageFigure], direction: str) -> float:
        """Penalize a candidate if another figure lies between figure and caption."""

        fb = fig.bbox
        if direction == "below":
            y1, y2 = fb.bottom, cap.top
        else:
            y1, y2 = cap.bottom, fb.top

        if y2 <= y1:
            return 0.0

        corridor = Rect(left=min(fb.left, cap.left), top=y1, right=max(fb.right, cap.right), bottom=y2)
        penalty = 0.0

        for other in all_figures:
            if other.ref == fig.ref:
                continue
            ob = other.bbox
            if ob.bottom <= y1 or ob.top >= y2:
                continue
            if ob.x_overlap(corridor) <= 0:
                continue
            penalty += self.config.blocker_penalty

        return penalty


# =============================================================================
# Convenience API
# =============================================================================


def _rect_to_dict(rect: Optional[Rect]) -> Optional[Dict[str, float]]:
    if rect is None:
        return None
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.width,
        "height": rect.height,
    }


def _union_bbox(rects: Sequence[Rect]) -> Rect:
    """Return the minimal bounding rectangle covering all rects."""

    if not rects:
        raise ValueError("Cannot union an empty rect list")
    left = min(r.left for r in rects)
    top = min(r.top for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    return Rect(left=left, top=top, right=right, bottom=bottom)


def link_figures_to_captions(doc: Any, config: Optional[LinkerConfig] = None) -> List[CaptionAssociation]:
    """One-shot helper for the most common workflow."""

    return DoclingFigureCaptionLinker(config=config).link(doc)

# =============================================================================
def render_picture(
        picitem: PictureItem, 
        pdf: fitz.Document,
        zoom:int = 2
    ) -> fitz.Pixmap:
    """
    Render a cropped image from a PDF page based on a Docling PictureItem.
    This function extracts the region of a PDF corresponding to the bounding box stored in a PictureItem 
    and renders it as a raster image.
    Args:
        picitem (PictureItem):
            A Docling PictureItem containing provenance data with page number and bounding box coordinates.
        pdf (fitz.Document):
            An open PyMuPDF PDF document.
        zoom (int, optional):
            Scaling factor for rendering resolution. Default is 2.0.
    Returns:
        fitz.Pixmap:
            A rasterized image (Pixmap) of the clipped region.
    """
    # Convert 1-based page number → 0-based
    page_no = picitem.prov[0].page_no - 1
    page = pdf[page_no]
    
    bbox = picitem.prov[0].bbox
    page_height = page.rect.height

    # Coordinate transform (BOTTOMLEFT → TOPLEFT)
    x0 = bbox.l
    x1 = bbox.r
    y0 = page_height - bbox.t
    y1 = page_height - bbox.b

    rect = fitz.Rect(x0, y0, x1, y1)

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=rect)

    return pix

def get_caption(picitem, docdict):
    texts = []
    for child in picitem.children:
        idx = int(child.cref.split('/')[-1])
        # texts.append(docdict['texts'][idx]['text'])  # TypeError: 'TextItem' object is not subscriptable
        texts.append(docdict['texts'][idx].text)
    return " ".join(texts)

def export_page_figures(
    doc,
    pdf: fitz.Document,
    page_no: int,
    output_dir: str = "figure_exports",
    zoom: int = 2,
    mode: Literal["display", "save", "both"] = "display",
    show_caption: bool = True,
    save_caption_txt: bool = True,
    ) -> None:
    """
    Display and/or save figures with captions from a specific PDF page.

    Figures are rendered from the source PDF using Docling picture
    provenance metadata. Captions are resolved using the figure-caption
    linker output.

    Args:
        doc:
            Parsed Docling document.

        pdf (fitz.Document):
            Open PyMuPDF document.

        page_no (int):
            1-based page number to process.

        output_dir (str, optional):
            Directory used when saving outputs.

        zoom (int, optional):
            Rendering scale factor.

        mode (Literal["display", "save", "both"], optional):
            Output behavior:
            - "display": show inline in notebook
            - "save": save to disk
            - "both": display and save

        show_caption (bool, optional):
            Print captions during display.

        save_caption_txt (bool, optional):
            Save captions as `.txt` files in save mode.

    Returns:
        None
    """

    # ---------------------------------------------------------
    # Prepare output directory if saving is enabled
    # ---------------------------------------------------------

    save_enabled = mode in {"save", "both"}
    display_enabled = mode in {"display", "both"}

    output_path = Path(output_dir)

    if save_enabled:
        output_path.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------
    # Figure-caption associations
    # ---------------------------------------------------------

    associations = extract_figure_caption_map(doc)

    assoc_map = {
        row["figure_ref"]: row
        for row in associations
    }

    exported_count = 0

    # ---------------------------------------------------------
    # Iterate figures
    # ---------------------------------------------------------

    for picitem in doc.pictures:

        if not picitem.prov:
            continue

        pic_page = picitem.prov[0].page_no

        if pic_page != page_no:
            continue

        fig_ref = str(picitem.self_ref)

        # -----------------------------------------------------
        # Render image
        # -----------------------------------------------------

        pix = render_picture(
            picitem=picitem,
            pdf=pdf,
            zoom=zoom,
        )

        # -----------------------------------------------------
        # Lookup caption
        # -----------------------------------------------------

        assoc = assoc_map.get(fig_ref, {})

        caption = assoc.get("caption_text")

        if not caption:
            caption = "[No caption found]"

        # -----------------------------------------------------
        # DISPLAY MODE
        # -----------------------------------------------------

        if display_enabled:

            img = Image.open(io.BytesIO(pix.tobytes("png")))

            if "ipykernel" in sys.modules:
                display(img)      # notebook
            else:
                img.show()        # normal .py script

            print(f"Figure Ref : {fig_ref}")

            if show_caption:
                print(f"Caption    : {caption}")

            print()

        # -----------------------------------------------------
        # SAVE MODE
        # -----------------------------------------------------

        if save_enabled:

            image_filename = (
                f"page_{page_no}_figure_{exported_count:04d}.png"
            )

            image_path = output_path / image_filename

            pix.save(str(image_path))

            print(f"Saved image: {image_path}")

            if save_caption_txt:

                txt_filename = (
                    f"page_{page_no}_figure_{exported_count:04d}.txt"
                )

                txt_path = output_path / txt_filename

                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(caption)

                print(f"Saved text : {txt_path}")

            print()

        exported_count += 1

    print(f"Processed {exported_count} figure(s) from page {page_no}.")

# =============================================================================

def extract_figure_caption_map(doc: Any, config: Optional[LinkerConfig] = None) -> List[Dict[str, Any]]:
    """Convenience wrapper that returns JSON-friendly dictionaries."""

    linker = DoclingFigureCaptionLinker(config=config)
    return linker.to_dict(linker.link(doc))


# =============================================================================
# Example usage
# =============================================================================

# if __name__ == "__main__":
#     # Example only.
#     #
#     from docling.document_converter import DocumentConverter

#     data_folder_path = Path("/mnt/c/Users/Rakesh-PC/Documents/1_GitHubSync_SSH/iid-platform/sample_data/named")
#     file_path = data_folder_path / "govt-food-data-report-large-table-heavy.pdf"

#     converter = DocumentConverter()
#     result = converter.convert(file_path)
#     doc = result.document
#     #
#     pairs = extract_figure_caption_map(doc)
#     for row in pairs:
#         print(row)
#     pass

# ----------------------------------------------------------------------------

if __name__ == "__main__":

    import time
    import fitz

    from docling.document_converter import DocumentConverter

    data_folder_path = Path(
        "/mnt/c/Users/Rakesh-PC/Documents/1_GitHubSync_SSH/iid-platform/sample_data/named"
    )

    # file_path = data_folder_path / "govt-food-data-report-large-table-heavy.pdf"
    file_path = data_folder_path / "nature-alberta-spring-2026-magazine-medium-mix-multi-col-text.pdf"

    # =========================================================
    # TOTAL TIMER
    # =========================================================
    total_start = time.perf_counter()

    # =========================================================
    # DOCUMENT CONVERSION TIMER
    # =========================================================
    convert_start = time.perf_counter()

    converter = DocumentConverter()
    result = converter.convert(file_path)

    doc = result.document

    convert_end = time.perf_counter()

    print(f"\nDocument conversion took: "
          f"{convert_end - convert_start:.2f} seconds")

    # =========================================================
    # OPEN PDF
    # =========================================================
    fitz_pdf = fitz.open(file_path)

    # =========================================================
    # FIGURE + CAPTION EXPORT TIMER
    # =========================================================
    export_start = time.perf_counter()

    export_page_figures(
        doc,
        fitz_pdf,
        page_no=10,
        zoom=3,
        mode="display",
    )

    export_end = time.perf_counter()

    print(f"\nFigure/caption export took: "
          f"{export_end - export_start:.2f} seconds")

    # =========================================================
    # TOTAL TIME
    # =========================================================
    total_end = time.perf_counter()

    print(f"\nTOTAL PIPELINE TIME: "
          f"{total_end - total_start:.2f} seconds")