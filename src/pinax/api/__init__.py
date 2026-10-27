# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import access, admin, explore, health, identity

router = APIRouter(prefix="/api")
for module in (health, identity, explore, admin, access):
    router.include_router(module.router)
