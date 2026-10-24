"""Start the API using validated environment configuration."""

import uvicorn

from agentflow.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "agentflow.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
