"""DeepTeam adapter (pinned deepteam==1.0.10): expand an attack set with DeepTeam's attack enhancers.

DeepTeam is used only to *generate* attack variants; scanning, detection and scoring stay in our engine,
so results remain deterministic and comparable. Variants are saved as a new, versioned custom attack set.

- Encoding enhancers (Base64, ROT13, Leetspeak) are local and free.
- Rewriting enhancers (Roleplay, Multilingual, ...) ask a simulator LLM - the project's judge model via
  OpenRouter - to rewrite each attack. Their cost is metered from DeepEval's per-call cost.
- A rewrite that drops the attack's code word, or that the simulator refused (returned unchanged), is
  discarded: our detector could not attribute a success to it.

Telemetry is opted out (DEEPTEAM_TELEMETRY_OPT_OUT=YES, set in app/__init__.py and the Dockerfile).
"""
from __future__ import annotations

import asyncio
import gc
import importlib
import logging
from dataclasses import dataclass
from importlib.metadata import version as pkg_version

from app.security.detectors import norm
from app.security.library import attack_fields

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Enhancer:
    key: str
    cls: str  # class name in deepteam.attacks.single_turn
    label: str
    technique: str
    needs_llm: bool
    description: str


ENHANCERS: dict[str, Enhancer] = {e.key: e for e in [
    Enhancer("base64", "Base64", "Base64", "encoding", False, "Encodes the attack in Base64 to slip past text filters."),
    Enhancer("rot13", "ROT13", "ROT13", "encoding", False, "Rotates every letter by 13 places."),
    Enhancer("leetspeak", "Leetspeak", "Leetspeak", "encoding", False, "Swaps letters for look-alike digits (E→3, A→4)."),
    Enhancer("roleplay", "Roleplay", "Role-play", "role_play", True, "Wraps the attack in a persona or fictional scene."),
    Enhancer("multilingual", "Multilingual", "Multilingual", "multilingual", True, "Translates the attack into another language."),
    Enhancer("prompt_injection", "PromptInjection", "Prompt injection", "direct", True, "Adds instruction-override framing."),
    Enhancer("system_override", "SystemOverride", "System override", "direct", True, "Pretends to be a system or admin message."),
    Enhancer("authority_escalation", "AuthorityEscalation", "Authority escalation", "social", True, "Claims elevated authority or permissions."),
    Enhancer("emotional_manipulation", "EmotionalManipulation", "Emotional manipulation", "social", True, "Adds urgency or emotional pressure."),
    Enhancer("prompt_probing", "PromptProbing", "Prompt probing", "extraction", True, "Rephrases as a probe for hidden instructions."),
]}

# typical simulator calls per rewrite (generate + compliance check + type check); retries can triple it
CALLS_PER_REWRITE, MAX_RETRIES = 3, 3
TOKENS_IN, TOKENS_OUT = 900, 250


def version() -> str:
    return pkg_version("deepteam")


def plan(attacks: list[dict], enhancers: list[str], max_variants: int) -> list[tuple[dict, Enhancer]]:
    """(attack, enhancer) pairs to generate. Only single-turn attacks are expanded."""
    pairs = [(a, ENHANCERS[e]) for a in attacks if len(a["turns"]) == 1 for e in enhancers]
    return pairs[:max_variants]


def estimate_usd(pairs: list[tuple[dict, Enhancer]], price: tuple[float, float] | None) -> tuple[float | None, float | None]:
    """(typical, maximum) simulator cost for the rewriting enhancers."""
    llm = sum(1 for _, e in pairs if e.needs_llm)
    if llm == 0:
        return 0.0, 0.0
    if price is None:
        return None, None
    per_call = TOKENS_IN * price[0] + TOKENS_OUT * price[1]
    return llm * CALLS_PER_REWRITE * per_call, llm * CALLS_PER_REWRITE * MAX_RETRIES * per_call


def simulator_model(judge):
    """DeepEval OpenRouter model that records the cost of every call DeepTeam makes."""
    from deepeval.models import OpenRouterModel

    class Metered(OpenRouterModel):
        total_cost = 0.0

        def _count(self, res):
            if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], (int, float)):
                self.total_cost += float(res[1] or 0)
            return res

        def generate(self, *a, **k):
            return self._count(super().generate(*a, **k))

        async def a_generate(self, *a, **k):
            return self._count(await super().a_generate(*a, **k))

    return Metered(
        model=judge.model, api_key=judge.api_key, base_url=judge.base_url, temperature=0.7,
        cost_per_input_token=judge.price_in_per_token, cost_per_output_token=judge.price_out_per_token,
    )


async def enhance(text: str, enh: Enhancer, model) -> str:
    mod = importlib.import_module("deepteam.attacks.single_turn")
    inst = getattr(mod, enh.cls)()
    if enh.needs_llm:
        return await inst.a_enhance(text, simulator_model=model)
    return inst.enhance(text)


async def generate(pairs: list[tuple[dict, Enhancer]], model, concurrency: int = 4) -> tuple[list[dict], list[str]]:
    """Returns (variant attack definitions, notes about discarded pairs)."""
    sem = asyncio.Semaphore(concurrency)
    notes: list[str] = []

    async def one(a: dict, e: Enhancer) -> dict | None:
        turn = a["turns"][0]
        async with sem:
            try:
                text = (await enhance(turn, e, model)) or ""
            except Exception as ex:  # noqa: BLE001 - one failed rewrite never fails the whole set
                notes.append(f"{a['name']} × {e.label}: failed ({type(ex).__name__})")
                return None
        if not text.strip() or text.strip() == turn.strip():
            notes.append(f"{a['name']} × {e.label}: simulator returned it unchanged (likely refused)")
            return None
        # Only enforced when the original turn carried the code word (indirect attacks keep it in the
        # poisoned document; encoded attacks carry it encoded).
        if e.needs_llm and a.get("marker") and norm(a["marker"]) in norm(turn) and norm(a["marker"]) not in norm(text):
            notes.append(f"{a['name']} × {e.label}: rewrite dropped the code word {a['marker']}")
            return None
        return attack_fields({
            **a, "key": f"{a['key']}-{e.key}"[:100], "name": f"{a['name']} · {e.label}"[:200],
            "technique": e.technique, "turns": [text],
        })

    results = await asyncio.gather(*(one(a, e) for a, e in pairs))
    return [r for r in results if r], notes


# ------------------------------------------------------------------ worker job
def run_job(set_id: int) -> None:
    asyncio.run(_run(set_id))


async def _run(set_id: int) -> None:
    from sqlalchemy.orm import Session

    from app.core.db import session_factory
    from app.core.redact import redact
    from app.models import Attack, AttackSet

    db: Session = session_factory()()
    try:
        s = db.get(AttackSet, set_id)
        if s is None or s.status != "generating":
            return
        g = dict(s.generation)
        try:
            src = db.get(AttackSet, g["source_set_id"])
            if src is None:
                raise RuntimeError("The source attack set was deleted")
            base = [{c.name: getattr(a, c.name) for c in Attack.__table__.columns} for a in src.attacks]
            pairs = plan(base, g["enhancers"], g["max_variants"])
            model = None
            if any(e.needs_llm for _, e in pairs):
                model = simulator_model(_judge_settings(db, g["judge_config_id"]))
            variants, notes = await generate(pairs, model)
            s.attacks = [Attack(**v) for v in variants]
            g.update(variants=len(variants), notes=notes[:50],
                     cost_usd=round(model.total_cost, 6) if model is not None else 0.0)
            s.status = "ready" if variants else "failed"
            if not variants:
                g["error"] = "No usable variants were generated" + (f": {notes[0]}" if notes else "")
        except Exception as e:  # noqa: BLE001 - surfaced on the set, secrets redacted
            log.exception("DeepTeam generation for set %s failed", set_id)
            s.status, g["error"] = "failed", redact(f"{type(e).__name__}: {e}")[:500]
        s.generation = g
        db.commit()
    finally:
        db.close()
        gc.collect()
        await asyncio.sleep(0.05)


def _judge_settings(db, judge_config_id: int | None):
    from app.core.config import get_settings
    from app.core.security import decrypt_secret
    from app.evaluators.base import JudgeSettings
    from app.models import JudgeConfig, Provider
    from app.providers import openrouter

    j = db.get(JudgeConfig, judge_config_id) if judge_config_id else None
    if j is None:
        raise RuntimeError("Rewriting enhancers need a judge model (it was deleted)")
    p = db.get(Provider, j.provider_id)
    price = openrouter.model_price(j.model)
    s = get_settings()
    return JudgeSettings(
        model=j.model, api_key=decrypt_secret(p.encrypted_credentials), base_url=s.openrouter_base_url,
        price_in_per_token=price[0] if price else None, price_out_per_token=price[1] if price else None,
        judge_config_id=j.id,
    )
