# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import access, admin, costs, explore, health, identity, signin

router = APIRouter(prefix="/api")
for module in (health, identity, signin, explore, admin, access, costs):
    router.include_router(module.router)
