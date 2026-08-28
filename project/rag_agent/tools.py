from langchain_core.tools import tool
import config
from core.execution_logger import log_error, log_tool_end, log_tool_start


class ToolFactory:

    def __init__(self, retriever, record_retrieval=None):
        self.retriever = retriever
        self.record_retrieval = record_retrieval

    def _search_child_chunks(self, query: str, limit: int = config.DEFAULT_RETRIEVAL_K):
        """Search document excerpts for evidence related to the user question.

        Use this as the first retrieval step. Results include parent IDs, file
        names, and short child-chunk excerpts. If excerpts are relevant but too
        fragmented to answer confidently, call retrieve_parent_chunks with the
        returned parent_id.

        Args:
            query: Focused search query with concrete keywords from the question.
            limit: Maximum number of child chunks to return.
        """
        log_tool_start("search_child_chunks", {"query": query, "limit": limit})
        try:
            hits = self.retriever.search(query, k=limit)
            if self.record_retrieval:
                self.record_retrieval(hits)

            if not hits:
                content = "NO_RELEVANT_CHUNKS"
                log_tool_end("search_child_chunks", content)
                return content, []

            content = config.CHILD_CHUNK_SEPARATOR.join([
                f"Parent ID: {h.parent_id}\nFile Name: {h.source}\nContent: {h.content.strip()}"
                for h in hits
            ])
            log_tool_end("search_child_chunks", content)
            return content, hits

        except Exception as e:
            log_error("search_child_chunks", e)
            content = f"RETRIEVAL_ERROR: {str(e)}"
            log_tool_end("search_child_chunks", content)
            return content, []

    def _retrieve_parent_chunks(self, parent_id: str):
        """Retrieve the full parent chunk for a relevant child search result.

        Use this only after search_child_chunks returns a relevant parent_id and
        the child excerpt needs more surrounding context. Do not call this for
        parent IDs already available in compressed context.

        Args:
            parent_id: Parent chunk ID returned by search_child_chunks.
        """
        log_tool_start("retrieve_parent_chunks", {"parent_id": parent_id})
        try:
            hit = self.retriever.get_parent(parent_id)
            if not hit:
                content = "NO_PARENT_DOCUMENT"
                log_tool_end("retrieve_parent_chunks", content)
                return content, []

            content = (
                f"Parent ID: {hit.parent_id}\n"
                f"File Name: {hit.source}\n"
                f"Content: {hit.content.strip()}"
            )
            log_tool_end("retrieve_parent_chunks", content)
            return content, [hit]

        except Exception as e:
            log_error("retrieve_parent_chunks", e)
            content = f"PARENT_RETRIEVAL_ERROR: {str(e)}"
            log_tool_end("retrieve_parent_chunks", content)
            return content, []

    def create_tools(self) -> list:
        """Create and return the list of tools."""
        search_tool = tool(
            "search_child_chunks", response_format="content_and_artifact"
        )(self._search_child_chunks)
        retrieve_tool = tool(
            "retrieve_parent_chunks", response_format="content_and_artifact"
        )(self._retrieve_parent_chunks)
        return [search_tool, retrieve_tool]
