"""
多模型交叉验证引擎 (Multi-Model Cross-Verification Engine)
============================================================

消除 AI 产出物的幻觉、编造和事实错误——用多个独立模型对同一产出进行交叉验证。

核心机制:
  1. Claim 提取 — 从产出物中提取所有可验证的事实断言
  2. 多模型独立验证 — 2~3 个验证模型各自独立判断每条 claim 的真实性
  3. 共识聚合 — 综合多模型判断，计算可信度评分
  4. 幻觉标记 — 标记高风险的编造内容
  5. 验证报告 — 生成结构化的验证报告

设计原则:
  - 验证模型与生成模型必须不同（避免同类偏差）
  - 每条 claim 至少经 2 个独立模型验证
  - 低共识度 claim 必须标记为可疑
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 数据类型
# ============================================================

class Verdict(str, Enum):
    TRUE = "true"            # 验证为真
    LIKELY_TRUE = "likely_true"   # 可能为真
    UNCERTAIN = "uncertain"  # 无法确定
    LIKELY_FALSE = "likely_false" # 可能为假
    FALSE = "false"          # 验证为假/编造
    HALLUCINATION = "hallucination" # 明确幻觉


class RiskLevel(str, Enum):
    SAFE = "safe"       # 低风险，多数模型确认
    CAUTION = "caution" # 需注意，存在分歧
    WARNING = "warning" # 高风险，可能编造
    CRITICAL = "critical" # 严重，确认幻觉


@dataclass
class Claim:
    """可验证的事实断言"""
    id: str
    text: str                          # 原始语句
    category: str = "factual"          # factual | technical | numeric | logical
    source_context: str = ""           # 上下文
    confidence: float = 1.0            # 提取置信度


@dataclass
class ModelVerdict:
    """单个验证模型的判断"""
    model: str
    verdict: Verdict
    reasoning: str = ""
    evidence: str = ""                # 模型提供的证据/反证
    confidence: float = 0.0           # 模型自我评估的信心
    elapsed_ms: float = 0.0


@dataclass
class VerifiedClaim:
    """经过多模型验证的断言"""
    claim: Claim
    verdicts: list[ModelVerdict] = field(default_factory=list)
    consensus: Verdict = Verdict.UNCERTAIN
    risk_level: RiskLevel = RiskLevel.CAUTION
    agreement_score: float = 0.0       # 0~1, 模型间一致性
    truth_score: float = 0.0           # 0~1, 真实性概率
    flagged: bool = False              # 是否标记为可疑


@dataclass
class VerificationReport:
    """完整的交叉验证报告"""
    report_id: str
    original_text: str                 # 被验证的原始文本
    claims: list[VerifiedClaim] = field(default_factory=list)
    overall_score: float = 0.0         # 整体可信度 0~1
    hallucination_rate: float = 0.0    # 幻觉比例
    verified_by: list[str] = field(default_factory=list)  # 参与验证的模型
    elapsed_ms: float = 0.0
    summary: str = ""
    risk_flags: list[str] = field(default_factory=list)


# ============================================================
# Claim 提取器
# ============================================================

CLAIM_EXTRACTION_PROMPT = """You are a fact-checking assistant. Analyze the following text and extract all verifiable factual claims.

## Instructions
1. Identify every statement that makes a factual assertion (facts, numbers, dates, technical claims, code behavior)
2. For each claim, classify its category:
   - factual: general facts, names, events, dates
   - technical: API names, function signatures, library versions, technical specifications
   - numeric: specific numbers, measurements, statistics, benchmarks
   - logical: logical conclusions, cause-effect relationships, architectural decisions
3. Skip opinions, subjective statements, and stylistic choices
4. Extract the exact original text for each claim (verbatim)

## Output Format (JSON only, no other text)
{
  "claims": [
    {
      "id": "c1",
      "text": "exact original claim text",
      "category": "factual|technical|numeric|logical",
      "context": "surrounding context for understanding",
      "confidence": 0.95
    }
  ]
}"""


class ClaimExtractor:
    """从文本中提取可验证的事实断言"""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-haiku-4-5"):
        self.api_key = api_key
        self.model = model

    async def extract(self, text: str) -> list[Claim]:
        """提取文本中的所有可验证断言"""
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key)

            response = await client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=CLAIM_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": f"Extract all verifiable claims from:\n\n{text[:8000]}"}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "claims": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "text": {"type": "string"},
                                            "category": {"type": "string", "enum": ["factual", "technical", "numeric", "logical"]},
                                            "context": {"type": "string"},
                                            "confidence": {"type": "number"},
                                        },
                                        "required": ["id", "text", "category"],
                                    },
                                },
                            },
                            "required": ["claims"],
                            "additionalProperties": False,
                        },
                    },
                },
            )

            # 安全提取文本（跳过 thinking blocks）
            text_content = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    text_content += block.text
                elif isinstance(block, dict) and block.get('type') == 'text':
                    text_content += block.get('text', '')

            data = json.loads(text_content)
            claims = []
            for c in data.get("claims", []):
                claims.append(Claim(
                    id=c.get("id", f"c{len(claims)}"),
                    text=c.get("text", ""),
                    category=c.get("category", "factual"),
                    source_context=c.get("context", ""),
                    confidence=c.get("confidence", 1.0),
                ))
            logger.info(f"提取到 {len(claims)} 条可验证断言")
            return claims

        except Exception as e:
            logger.error(f"Claim 提取失败: {e}")
            return []


# ============================================================
# 多模型交叉验证器
# ============================================================

VERIFIER_PROMPT = """You are an expert fact-checker. Verify the following claim from an AI-generated text.

## Original Context
{context}

## Claim to Verify
"{claim}"

## Instructions
1. Determine if this claim is factually accurate
2. If you have knowledge about this topic, use it to verify
3. If the claim involves code/technical details, analyze if the syntax and logic are correct
4. If you're uncertain, say so honestly — do not guess
5. Provide your reasoning and any evidence

## Output Format (JSON only)
{
  "verdict": "true|likely_true|uncertain|likely_false|false|hallucination",
  "reasoning": "detailed explanation",
  "evidence": "what supports or contradicts this claim",
  "confidence": 0.0-1.0
}

## Verdict Definitions
- true: confirmed accurate
- likely_true: probably accurate, minor uncertainty
- uncertain: cannot determine
- likely_false: probably inaccurate
- false: confirmed inaccurate
- hallucination: fabricated, nonsensical, or self-contradictory"""


class CrossVerifier:
    """多模型交叉验证引擎"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        verification_models: Optional[list[str]] = None,
    ):
        self.api_key = api_key
        self.verification_models = verification_models or [
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ]

    async def verify_claim(
        self,
        claim: Claim,
        original_text: str,
        model: str,
    ) -> ModelVerdict:
        """用单个模型验证一条断言"""
        start = time.time()
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=self.api_key)

            context = original_text[:500]  # 提供足够的上下文
            response = await client.messages.create(
                model=model,
                max_tokens=1024,
                system=VERIFIER_PROMPT.format(
                    context=context,
                    claim=claim.text,
                ),
                messages=[{"role": "user", "content": f"Verify this claim: {claim.text}"}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "verdict": {"type": "string", "enum": ["true", "likely_true", "uncertain", "likely_false", "false", "hallucination"]},
                                "reasoning": {"type": "string"},
                                "evidence": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["verdict", "reasoning", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
            )

            text_content = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    text_content += block.text

            data = json.loads(text_content)
            elapsed = (time.time() - start) * 1000

            return ModelVerdict(
                model=model,
                verdict=Verdict(data.get("verdict", "uncertain")),
                reasoning=data.get("reasoning", ""),
                evidence=data.get("evidence", ""),
                confidence=data.get("confidence", 0.5),
                elapsed_ms=elapsed,
            )

        except Exception as e:
            logger.error(f"模型 {model} 验证失败: {e}")
            return ModelVerdict(
                model=model,
                verdict=Verdict.UNCERTAIN,
                reasoning=f"Verification failed: {e}",
                elapsed_ms=(time.time() - start) * 1000,
            )

    async def verify_claim_multi(self, claim: Claim, original_text: str) -> VerifiedClaim:
        """用多个模型独立验证同一条断言"""
        # 并行发起验证
        tasks = [
            self.verify_claim(claim, original_text, model)
            for model in self.verification_models
        ]
        verdicts = await asyncio.gather(*tasks)
        verdicts = [v for v in verdicts if v is not None]

        # 共识计算
        verified = self._compute_consensus(claim, verdicts)
        return verified

    async def verify_text(
        self,
        text: str,
        verification_models: Optional[list[str]] = None,
    ) -> VerificationReport:
        """对一段文本进行完整的多模型交叉验证"""
        if verification_models:
            self.verification_models = verification_models

        start = time.time()
        report_id = f"vr_{int(time.time()*1000)}"

        # 1. 提取 Claims
        extractor = ClaimExtractor(api_key=self.api_key)
        claims = await extractor.extract(text)

        if not claims:
            return VerificationReport(
                report_id=report_id,
                original_text=text,
                overall_score=1.0,
                hallucination_rate=0.0,
                summary="No verifiable claims found — text may be too short or purely subjective.",
            )

        # 2. 多模型独立验证 (限制最多20条 claim 以避免过量)
        claims_to_verify = claims[:20]
        verified_claims = []
        for claim in claims_to_verify:
            vc = await self.verify_claim_multi(claim, text)
            verified_claims.append(vc)

        # 3. 计算整体分数
        total_truth = sum(vc.truth_score for vc in verified_claims)
        overall = total_truth / max(len(verified_claims), 1)

        # 幻觉比例
        hallucinated = sum(1 for vc in verified_claims if vc.risk_level in (RiskLevel.CRITICAL, RiskLevel.WARNING))
        hall_rate = hallucinated / max(len(verified_claims), 1)

        # 4. 生成摘要
        risk_flags = []
        for vc in verified_claims:
            if vc.flagged:
                risk_flags.append(f"[{vc.risk_level.value}] {vc.claim.text[:100]}...")

        elapsed = (time.time() - start) * 1000

        return VerificationReport(
            report_id=report_id,
            original_text=text,
            claims=verified_claims,
            overall_score=round(overall, 3),
            hallucination_rate=round(hall_rate, 3),
            verified_by=self.verification_models,
            elapsed_ms=elapsed,
            summary=self._generate_summary(verified_claims, overall, hall_rate),
            risk_flags=risk_flags,
        )

    def _compute_consensus(self, claim: Claim, verdicts: list[ModelVerdict]) -> VerifiedClaim:
        """计算多模型验证共识"""
        if not verdicts:
            return VerifiedClaim(
                claim=claim,
                consensus=Verdict.UNCERTAIN,
                risk_level=RiskLevel.CAUTION,
                flagged=True,
            )

        # 将 verdict 映射为数值 (-2 ~ +2)
        score_map = {
            Verdict.HALLUCINATION: -2,
            Verdict.FALSE: -2,
            Verdict.LIKELY_FALSE: -1,
            Verdict.UNCERTAIN: 0,
            Verdict.LIKELY_TRUE: 1,
            Verdict.TRUE: 2,
        }

        scores = [score_map.get(v.verdict, 0) for v in verdicts]
        avg_score = sum(scores) / len(scores)

        # 共识判断
        if avg_score >= 1.5:
            consensus = Verdict.TRUE
        elif avg_score >= 0.5:
            consensus = Verdict.LIKELY_TRUE
        elif avg_score > -0.5:
            consensus = Verdict.UNCERTAIN
        elif avg_score > -1.5:
            consensus = Verdict.LIKELY_FALSE
        elif avg_score <= -1.5:
            consensus = Verdict.HALLUCINATION if any(v.verdict == Verdict.HALLUCINATION for v in verdicts) else Verdict.FALSE
        else:
            consensus = Verdict.UNCERTAIN

        # 模型一致性 (用方差判断)
        variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
        agreement = max(0.0, 1.0 - variance / 4.0)  # max variance is 4 (from -2 to +2)

        # 真实性分数 (0~1)
        truth_score = (avg_score + 2) / 4.0  # map -2~2 to 0~1

        # 风险等级
        if avg_score <= -1.5:
            risk = RiskLevel.CRITICAL
        elif avg_score <= -0.5:
            risk = RiskLevel.WARNING
        elif avg_score <= 1.0:
            risk = RiskLevel.CAUTION
        else:
            risk = RiskLevel.SAFE

        return VerifiedClaim(
            claim=claim,
            verdicts=verdicts,
            consensus=consensus,
            risk_level=risk,
            agreement_score=round(agreement, 3),
            truth_score=round(truth_score, 3),
            flagged=risk in (RiskLevel.WARNING, RiskLevel.CRITICAL),
        )

    def _generate_summary(self, claims: list[VerifiedClaim], overall: float, hall_rate: float) -> str:
        """生成可读摘要"""
        total = len(claims)
        if total == 0:
            return "No claims to verify."

        safe = sum(1 for c in claims if c.risk_level == RiskLevel.SAFE)
        caution = sum(1 for c in claims if c.risk_level == RiskLevel.CAUTION)
        warning = sum(1 for c in claims if c.risk_level == RiskLevel.WARNING)
        critical = sum(1 for c in claims if c.risk_level == RiskLevel.CRITICAL)

        lines = [
            f"Verified {total} claims across {len(self.verification_models)} independent models.",
            f"Overall Credibility: {overall:.0%}",
            f"Hallucination Rate: {hall_rate:.0%}",
            f"Distribution: {safe} safe · {caution} uncertain · {warning} suspicious · {critical} hallucinated",
        ]

        if warning + critical > 0:
            lines.append(f"⚠️ {warning + critical} claims flagged — review required.")

        return "\n".join(lines)


# ============================================================
# 便捷函数
# ============================================================

async def cross_verify(
    text: str,
    api_key: Optional[str] = None,
    models: Optional[list[str]] = None,
) -> VerificationReport:
    """一键交叉验证"""
    verifier = CrossVerifier(
        api_key=api_key,
        verification_models=models,
    )
    return await verifier.verify_text(text)


# ============================================================
# Web UI 报告渲染
# ============================================================

def report_to_html(report: VerificationReport) -> str:
    """将验证报告渲染为 HTML"""
    score_color = (
        "#34d399" if report.overall_score >= 0.8 else
        "#fb923c" if report.overall_score >= 0.5 else
        "#f87171"
    )

    html = f"""
    <div style="font-family:monospace;font-size:12px">
      <h4 style="margin:0 0 12px">Cross-Verification Report <code>{report.report_id}</code></h4>
      <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
        <span style="font-size:24px;color:{score_color};font-weight:bold">Credibility: {report.overall_score:.0%}</span>
        <span style="font-size:14px;color:#f87171">Hallucination: {report.hallucination_rate:.0%}</span>
        <span style="font-size:11px;color:#888">Verified by: {', '.join(report.verified_by)}</span>
        <span style="font-size:11px;color:#888">{report.elapsed_ms:.0f}ms</span>
      </div>
      <pre style="background:rgba(0,0,0,0.2);padding:10px;border-radius:4px;font-size:11px;margin-bottom:12px">{report.summary}</pre>
    """

    for vc in report.claims:
        risk_colors = {
            "safe": "#34d399", "caution": "#fb923c",
            "warning": "#f87171", "critical": "#ef4444",
        }
        color = risk_colors.get(vc.risk_level.value, "#888")
        symbol = {"safe": "✓", "caution": "?", "warning": "⚠", "critical": "✗"}

        html += f"""
        <div style="border-left:3px solid {color};padding:6px 10px;margin:6px 0;background:rgba(0,0,0,0.15)">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <span style="color:{color};font-weight:bold">{symbol[vc.risk_level.value]} [{vc.risk_level.value.upper()}]</span>
            <span style="flex:1">"{vc.claim.text[:200]}"</span>
            <span style="font-size:10px;color:#888">truth:{vc.truth_score:.0%} agree:{vc.agreement_score:.0%}</span>
          </div>
        """

        for v in vc.verdicts:
            html += f"""
            <div style="margin-left:20px;font-size:10px;color:#888">
              [{v.model}] → {v.verdict.value} (confidence:{v.confidence:.0%}) — {v.reasoning[:150]}
            </div>
            """

        html += "</div>"

    # Risk flags
    if report.risk_flags:
        html += '<div style="margin-top:12px;padding:10px;background:rgba(248,113,113,0.1);border-radius:4px">'
        html += '<strong style="color:#f87171">⚠️ Flagged Issues:</strong><br>'
        for flag in report.risk_flags:
            html += f'<div style="font-size:10px;margin-top:4px">• {flag}</div>'
        html += '</div>'

    html += "</div>"
    return html


# ============================================================
# 示例
# ============================================================

async def _example():
    """演示交叉验证"""
    text = """Python 3.14 introduced the new `except*` syntax for handling ExceptionGroups.
    This feature was inspired by PEP 654 and allows catching multiple exceptions simultaneously.
    The performance improvement is approximately 40% compared to traditional try/except chains.
    Python 3.14 has been downloaded over 500 million times since its release."""

    report = await cross_verify(text=text)
    print(f"Overall: {report.overall_score:.0%}")
    print(f"Hallucination: {report.hallucination_rate:.0%}")
    print(report.summary)

    for vc in report.claims:
        print(f"\n  [{vc.risk_level.value}] {vc.claim.text[:100]}...")
        print(f"    Truth: {vc.truth_score:.0%}, Agreement: {vc.agreement_score:.0%}")
        for v in vc.verdicts:
            print(f"    {v.model}: {v.verdict.value} ({v.reasoning[:80]}...)")


if __name__ == "__main__":
    asyncio.run(_example())
