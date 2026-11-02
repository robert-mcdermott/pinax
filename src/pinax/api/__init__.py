# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import (
    access,
    admin,
    audit,
    capacity,
    categories,
    costs,
    explore,
    health,
    identity,
    plans,
    review,
    signin,
    tags,
    tokens,
    views,
)

router = APIRouter(prefix="/api")
for module in (health, identity, signin, explore, admin, audit, access, costs, capacity, categories, plans, review,
               tags, tokens, views):
    router.include_router(module.router)
