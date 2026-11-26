"""Read-only catalogs: metric registry and the OpenRouter model list (for pickers)."""
from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.evaluators.applicability import METRICS
from app.evaluators.runner import engine_versions
from app.providers import openrouter
from app.schemas import CatalogModel, EngineImplOut, MetricDefOut

router = APIRouter(tags=["catalog"], dependencies=[Depends(current_user)])


@router.get("/metrics", response_model=list[MetricDefOut])
def metrics() -> list[MetricDefOut]:
    versions = engine_versions()
    return [
        MetricDefOut(
            name=m.name, label=m.label, description=m.description,
            higher_is_better=m.higher_is_better, default_threshold=m.default_threshold,
            engines=[
                EngineImplOut(
                    engine=i.engine, engine_metric_name=i.engine_metric_name,
                    engine_version=versions.get(i.engine, "unknown"), requires=sorted(i.requires),
                )
                for i in m.impls
            ],
        )
        for m in METRICS.values()
    ]


@router.get("/catalog/models", response_model=list[CatalogModel])
def models() -> list[CatalogModel]:
    prices = openrouter._load_pricing()
    return [
        CatalogModel(
            id=mid, price_in_per_mtok=p[0] * 1e6, price_out_per_mtok=p[1] * 1e6,
            name=meta.get("name") or mid, kind=meta.get("kind", "chat"), supports_tools=bool(meta.get("tools")),
            context_length=meta.get("context"),
        )
        for mid, p in sorted(prices.items())
        for meta in [openrouter.model_meta(mid)]
    ]
