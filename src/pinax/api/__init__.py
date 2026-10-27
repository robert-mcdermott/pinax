# SPDX-License-Identifier: Apache-2.0
"""JSON API under /api."""

from fastapi import APIRouter

from . import access, admin, explore, identity

router = APIRouter(prefix="/api")
for module in (identity, explore, admin, access):
    router.include_router(module.router)
