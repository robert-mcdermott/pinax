# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import access, admin, explore, health, identity, signin

router = APIRouter(prefix="/api")
for module in (health, identity, signin, explore, admin, access):
    router.include_router(module.router)
