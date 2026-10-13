# -*- coding: utf-8 -*-
"""
模块 a · OCR 层（可插拔）

把医疗记录图片转成"按阅读顺序排序"的文本块。
设计原则：
- 后端可插拔（RapidOCR / PaddleOCR / EasyOCR / 云端 OCR），默认 RapidOCR(ONNX)，
  中文识别好、无 torch 依赖、可离线。
- 统一输出结构：每行 = {text, confidence, box}，按 (y, x) 阅读顺序排序，
  并做"同一行块合并"，尽量还原表格/半结构化区域的物理位置。
- 对文档拍照类图片做轻度预处理（限制最长边、灰度可选），提高识别稳定度。

对外主接口：ocr_image(path) -> list[OcrLine]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple


@dataclass
class OcrLine:
    """一行识别结果。"""
    text: str
    confidence: float
    box: List[List[float]] = field(default_factory=list)  # 左上/右上/右下/左下 4 点


class BaseOCREngine:
    """OCR 后端抽象基类。"""

    name = "base"

    def recognize(self, image_path: str) -> List[OcrLine]:
        raise NotImplementedError


class RapidOCREngine(BaseOCREngine):
    """RapidOCR(onnxruntime) 后端，中文效果好、依赖轻。"""

    name = "rapidocr"

    def __init__(self, max_side: int = 2400, lang: str = "ch"):
        self.max_side = max_side
        self.lang = lang
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # det/cls/rec 模型首次会下载到默认缓存；可传 model_dir 固定路径
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image_path: str) -> List[OcrLine]:
        from PIL import Image
        import numpy as np

        engine = self._get_engine()
        img = Image.open(image_path).convert("RGB")
        if max(img.size) > self.max_side:
            ratio = self.max_side / max(img.size)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)))
        arr = np.array(img)

        result, _ = engine(arr)
        lines: List[OcrLine] = []
        if not result:
            return lines
        for item in result:
            box, text, score = item
            try:
                conf = float(score)
            except (TypeError, ValueError):
                conf = -1.0
            # box 可能为 numpy 数组，转成 list
            box_list = [[float(p[0]), float(p[1])] for p in box]
            lines.append(OcrLine(text=str(text).strip(), confidence=conf, box=box_list))
        return lines


def _top_left(box: List[List[float]]) -> Tuple[float, float]:
    ys = [p[1] for p in box]
    xs = [p[0] for p in box]
    return min(ys), min(xs)


def _height(box: List[List[float]]) -> float:
    ys = [p[1] for p in box]
    return max(ys) - min(ys)


def sort_and_merge_lines(
    lines: List[OcrLine],
    line_gap_ratio: float = 0.5,
) -> List[OcrLine]:
    """
    按阅读顺序（先上后下、同行走左到右）排序，并把同一行的块合并成一个 OcrLine。
    这对表格/半结构化区域尤其重要（如费用清单的一行 = 项目名 + 金额 + 类别）。
    """
    if not lines:
        return lines

    # 先按 y 排序
    lines_sorted = sorted(lines, key=lambda l: _top_left(l.box))
    merged: List[OcrLine] = []
    for ln in lines_sorted:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        py = _top_left(prev.box)
        cy = _top_left(ln.box)
        avg_h = (_height(prev.box) + _height(ln.box)) / 2
        # 认为同一行：中心 y 差小于行高的 line_gap_ratio
        prev_cy = (min(p[1] for p in prev.box) + max(p[1] for p in prev.box)) / 2
        cur_cy = (min(p[1] for p in ln.box) + max(p[1] for p in ln.box)) / 2
        if abs(cur_cy - prev_cy) < avg_h * line_gap_ratio:
            # 合并：文字按 x 顺序拼接，置信度取平均
            if _top_left(ln.box)[0] < _top_left(prev.box)[0]:
                new_text = ln.text + prev.text
                new_box = ln.box
            else:
                new_text = prev.text + ln.text
                new_box = prev.box
            merged[-1] = OcrLine(
                text=new_text,
                confidence=(prev.confidence + ln.confidence) / 2,
                box=new_box,
            )
        else:
            merged.append(ln)
    # 同一行内可能仍有多段（合并后），最终按 (y, x) 重排一次
    merged.sort(key=lambda l: _top_left(l.box))
    return merged


def ocr_image(
    image_path: str,
    engine: Optional[BaseOCREngine] = None,
    sort: bool = True,
) -> List[OcrLine]:
    """OCR 单张图片，返回按阅读顺序排序的行。"""
    if engine is None:
        engine = RapidOCREngine()
    lines = engine.recognize(image_path)
    if sort:
        lines = sort_and_merge_lines(lines)
    return lines


def ocr_to_text(lines: List[OcrLine]) -> str:
    """把 OCR 行拼成纯文本（每行一条）。"""
    return "\n".join(ln.text for ln in lines if ln.text)


def ocr_to_dicts(lines: List[OcrLine]) -> List[dict]:
    return [asdict(ln) for ln in lines]


if __name__ == "__main__":
    import sys
    p = sys.argv[1]
    if not os.path.exists(p):
        print("file not found:", p)
        sys.exit(1)
    eng = RapidOCREngine()
    ls = ocr_image(p, engine=eng)
    for ln in ls:
        print(f"[{ln.confidence:.3f}] {ln.text}")
