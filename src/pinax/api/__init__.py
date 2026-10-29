# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import access, admin, capacity, costs, explore, health, identity, plans, signin, tags

router = APIRouter(prefix="/api")
for module in (health, identity, signin, explore, admin, access, costs, capacity, plans, tags):
    router.include_router(module.router)
