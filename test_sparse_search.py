import asyncio
from app.services import mongodb_service as mdb
from app.core.acl import atlas_search_pipeline

async def t():
    p = atlas_search_pipeline("chunks_search", "your exact term", "org_X", 4, ["executive"], 5)
    print(await mdb.col("chunks").aggregate(p).to_list(5))
asyncio.run(t())