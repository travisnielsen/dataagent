"""
Reusable Azure AI Search client for vector/hybrid search operations.

This module provides a shared search client that can be used across
multiple tools that need vector embeddings and Azure AI Search.
"""

import logging
import os
import re
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)

# Azure AI Search settings
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")

# Azure OpenAI / AI Foundry settings for embeddings
AI_PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
EMBEDDING_DEPLOYMENT = os.getenv("AZURE_AI_EMBEDDING_DEPLOYMENT", "embedding-small")


class AzureSearchClient:
    """
    Async context manager for Azure AI Search operations with vector embeddings.

    Handles credential lifecycle, embedding generation, and search operations.
    Designed to be reused across multiple search tools.

    Usage:
        async with AzureSearchClient(index_name="my-index") as client:
            results = await client.hybrid_search(
                query="user question",
                select=["field1", "field2"],
                top=5,
            )
    """

    def __init__(
        self,
        index_name: str,
        endpoint: str | None = None,
        vector_field: str = "content_vector",
    ):
        """
        Initialize the search client.

        Args:
            index_name: Name of the Azure AI Search index to query
            endpoint: Search service endpoint (defaults to AZURE_SEARCH_ENDPOINT env var)
            vector_field: Name of the vector field in the index for semantic search
        """
        self.index_name = index_name
        self.endpoint = endpoint or SEARCH_ENDPOINT
        self.vector_field = vector_field

        self._credential: DefaultAzureCredential | None = None
        self._search_client: Any = None
        self._openai_client: Any = None

    async def __aenter__(self) -> "AzureSearchClient":
        """Set up clients on context entry."""
        if not self.endpoint:
            raise ValueError(
                "Azure Search endpoint not configured. "
                "Set AZURE_SEARCH_ENDPOINT environment variable."
            )

        self._credential = DefaultAzureCredential()
        self._search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=self._credential,
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up clients on context exit."""
        if self._search_client is not None:
            await self._search_client.close()
            self._search_client = None

        if self._openai_client is not None:
            await self._openai_client.close()
            self._openai_client = None

        if self._credential is not None:
            await self._credential.close()
            self._credential = None

    async def get_embeddings(self, text: str) -> list[float] | None:
        """
        Generate vector embeddings for text using Azure OpenAI.

        Uses the embedding model deployed in the AI Foundry project.

        Args:
            text: Text to generate embeddings for

        Returns:
            List of floats representing the embedding vector, or None if failed
        """
        if self._credential is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        try:
            project_endpoint = AI_PROJECT_ENDPOINT

            if not project_endpoint:
                logger.warning("AZURE_AI_PROJECT_ENDPOINT not set, cannot generate embeddings")
                return None

            # Extract the AI Services base endpoint from the project endpoint
            match = re.match(r'(https://[^/]+)', project_endpoint)
            if not match:
                logger.warning("Could not extract base endpoint from AZURE_AI_PROJECT_ENDPOINT")
                return None
            base_endpoint = match.group(1)

            # Get token for Azure Cognitive Services
            token = await self._credential.get_token("https://cognitiveservices.azure.com/.default")

            # Create or reuse OpenAI client
            if self._openai_client is None:
                self._openai_client = AsyncAzureOpenAI(
                    azure_endpoint=base_endpoint,
                    azure_ad_token=token.token,
                    api_version="2024-06-01",
                )

            response = await self._openai_client.embeddings.create(
                model=EMBEDDING_DEPLOYMENT,
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
        embeddings: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a hybrid (vector + keyword) search.

        Args:
            query: Text query for keyword search and embedding generation
            select: List of fields to return in results
            top: Number of results to return
            embeddings: Pre-computed embeddings (if None, will be generated)

        Returns:
            List of search results with scores
        """
        if self._search_client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        # Generate embeddings if not provided
        if embeddings is None:
            embeddings = await self.get_embeddings(query)

        if embeddings is None:
            raise RuntimeError(
                "Failed to generate embeddings for semantic search. "
                "Check AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_EMBEDDING_DEPLOYMENT configuration."
            )

        # Create vector query
        vector_query = VectorizedQuery(
            vector=embeddings,
            k_nearest_neighbors=top,
            fields=self.vector_field,
        )

        # Execute hybrid search
        results = await self._search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            select=select,
            top=top,
        )

        # Collect results
        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches

    async def vector_search(
        self,
        query: str,
        select: list[str],
        top: int = 5,
        embeddings: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a pure vector (semantic) search without keyword matching.

        Args:
            query: Text query for embedding generation
            select: List of fields to return in results
            top: Number of results to return
            embeddings: Pre-computed embeddings (if None, will be generated)

        Returns:
            List of search results with scores
        """
        if self._search_client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        # Generate embeddings if not provided
        if embeddings is None:
            embeddings = await self.get_embeddings(query)

        if embeddings is None:
            raise RuntimeError(
                "Failed to generate embeddings for semantic search. "
                "Check AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_EMBEDDING_DEPLOYMENT configuration."
            )

        # Create vector query
        vector_query = VectorizedQuery(
            vector=embeddings,
            k_nearest_neighbors=top,
            fields=self.vector_field,
        )

        # Execute vector-only search
        results = await self._search_client.search(
            search_text=None,
            vector_queries=[vector_query],
            select=select,
            top=top,
        )

        # Collect results
        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches

    async def keyword_search(
        self,
        query: str,
        select: list[str],
        top: int = 5,
        filter_expression: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a pure keyword search without vector matching.

        Args:
            query: Text query for keyword search
            select: List of fields to return in results
            top: Number of results to return
            filter_expression: Optional OData filter expression

        Returns:
            List of search results with scores
        """
        if self._search_client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        # Execute keyword-only search
        results = await self._search_client.search(
            search_text=query,
            select=select,
            top=top,
            filter=filter_expression,
        )

        # Collect results
        matches = []
        async for result in results:
            match = {field: result.get(field, "") for field in select}
            match["score"] = result.get("@search.score", 0)
            matches.append(match)

        return matches
