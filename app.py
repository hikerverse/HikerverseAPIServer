from typing import Dict, Any

import numpy as np
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from contextlib import asynccontextmanager
import asyncio

from starlette.applications import Starlette
import logging

from hikerverseuniverse.universe_api import get_all_celestials


@asynccontextmanager
async def lifespan(app: FastAPI):
    # initialize global data and a lock for concurrency safety
    logging.getLogger("uvicorn").info("Loading global celestial data...")
    celestial_data_: Dict[str, Any] = get_all_celestials()

    if celestial_data_['success']:
        _cd = np.array(celestial_data_['data'])
        app.state.global_data = {"universe": {"celestials": _cd, "spacecraft": {}}}
        logging.getLogger("uvicorn").info(f"Global celestial data loaded ({len(_cd)} items).")
        app.state.lock = asyncio.Lock()
    else:
        logging.getLogger("uvicorn").error("Failed to load global celestial data.")
    try:
        yield
    finally:
        # cleanup on shutdown
        app.state.global_data = None
        app.state.lock = None

app: FastAPI = FastAPI(default_response_class=ORJSONResponse, lifespan=lifespan)
