"""
Cached query search tool using Azure AI Search.
"""

import logging
import os
import re
from typing import Any

from agent_framework import ai_function
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)

# Confidence threshold for using cached queries
CONFIDENCE_THRESHOLD = float(os.getenv("QUERY_CONFIDENCE_THRESHOLD", "0.75"))


class AzureSearchClient:
    """
    Async context manager for Azure AI Search operations with vector embeddings.

    This is a simplified inline version. For the full implementation,
    see src/api/util/search_client.py
    """

    def __init__(self, index_name: str, vector_field: str = "content_vector"):
        self.index_name = index_name
        self.endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
        self.vector_field = vector_field
        self._credential: DefaultAzureCredential | None = None
        self._search_client: SearchClient | None = None
        self._openai_client: AsyncAzureOpenAI | None = None

        # Parse AI project endpoint for embeddings
        project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        match = re.match(r'(https://[^/]+)', project_endpoint)
        self._ai_base_endpoint = match.group(1) if match else ""
        self._embedding_deployment = os.getenv("AZURE_AI_EMBEDDING_DEPLOYMENT", "embedding-small")

    async def __aenter__(self):
        if not self.endpoint:
            raise ValueError("AZURE_SEARCH_ENDPOINT environment variable is required")

        self._credential = DefaultAzureCredential()
        self._search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=self._credential,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._search_client:
            await self._search_client.close()
        if self._openai_client:
            await self._openai_client.close()
        if self._credential:
            await self._credential.close()

    async def get_embeddings(self, text: str) -> list[float] | None:
        """Generate embeddings using Azure OpenAI."""
        if not self._ai_base_endpoint:
            logger.warning("No AI endpoint configured for embeddings")
            return None

        try:
            assert self._credential is not None, "Client not initialized"
            token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")

            if self._openai_client is None:
                self._openai_client = AsyncAzureOpenAI(
                    azure_endpoint=self._ai_base_endpoint,
                    azure_ad_token=token.token,
                    api_version="2024-06-01",
                )

            response = await self._openai_client.embeddings.create(
                model=self._embedding_deployment,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Failed to get embeddings: %s", e)
            return None

    async def hybrid_search(
        self,
        query: str,
        select: list[str],
        top: int = 5,
    ) -> list[dict[str, Any]]:
        """Execute hybrid (vector + keyword) search."""
        assert self._search_client is not None, "Client not initialized"

        embeddings = await self.get_embeddings(query)
        if embeddings is None:
            raise RuntimeError("Failed to generate embeddings")

        vector_query = VectorizedQuery(
            vector=embeddings,
            k_nearest_neighbors=top,
            fields=self.vector_field,
        )

        results = await self._search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            select=select,
            top=top,
        )

        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches


@ai_function
async def search_cached_queries(user_question: str) -> dict[str, Any]:
    """
    Search for pre-tested SQL queries that match the user's question.

    This function uses semantic search to find previously validated SQL queries
    that answer similar questions. If a high-confidence match is found,
    the cached query should be used instead of generating a new one.

    Args:
        user_question: The user's natural language question about the data

    Returns:
        A dictionary containing:
        - has_high_confidence_match: Whether a cached query above threshold was found
        - best_match: The best matching cached query (if any)
        - all_matches: All matches with their scores
    """
    logger.info("Searching cached queries for: %s", user_question[:100])

    try:
        async with AzureSearchClient(
            index_name="queries",
            vector_field="content_vector"
        ) as client:
            results = await client.hybrid_search(
                query=user_question,
                select=["question", "query", "reasoning"],
                top=3,
            )

        if not results:
            return {
                "has_high_confidence_match": False,
                "best_match": None,
                "all_matches": [],
                "message": "No cached queries found"
            }

        best_match = results[0]
        has_high_confidence = best_match["score"] >= CONFIDENCE_THRESHOLD

        logger.info(
            "Found %d cached queries. Best score: %.3f (threshold: %.2f)",
            len(results),
            best_match["score"],
            CONFIDENCE_THRESHOLD
        )

        return {
            "has_high_confidence_match": has_high_confidence,
            "best_match": best_match if has_high_confidence else None,
            "all_matches": results,
            "threshold": CONFIDENCE_THRESHOLD
        }

    except Exception as e:
        logger.error("Error searching cached queries: %s", e)
        return {
            "has_high_confidence_match": False,
            "best_match": None,
            "all_matches": [],
            "error": str(e)
        }
