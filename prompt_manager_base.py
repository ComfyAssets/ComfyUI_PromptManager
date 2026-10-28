"""
PromptManagerBase: Shared logic for PromptManager node variants.

Provides database initialization, prompt saving, hashing, tag parsing,
search, gallery system management, and cleanup — extracted from the
duplicate code in prompt_manager.py and prompt_manager_text.py.
"""

import os
from typing import Any, Dict, List, Optional

try:
    from .utils.logging_config import get_logger
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from utils.logging_config import get_logger

try:
    from .database.operations import PromptDatabase
    from .utils.comfyui_integration import get_comfyui_integration
    from .utils.image_monitor import get_image_monitor
    from .utils.prompt_tracker import get_prompt_tracker
    from .utils.hashing import generate_prompt_hash
    from .utils.prompt_graph import resolve_text, run_prompt_nodes
    from .utils.run_keys import executing_run_id, prompt_node_key
    from .utils.usage_tracking import PENDING_FIRST_USE
    from .utils.cached_runs import (
        CACHED_RUNS,
        Role,
        Seen,
        own_counts,
        stored_facts,
    )
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database.operations import PromptDatabase
    from utils.comfyui_integration import get_comfyui_integration
    from utils.image_monitor import get_image_monitor
    from utils.prompt_tracker import get_prompt_tracker
    from utils.hashing import generate_prompt_hash
    from utils.prompt_graph import resolve_text, run_prompt_nodes
    from utils.run_keys import executing_run_id, prompt_node_key
    from utils.usage_tracking import PENDING_FIRST_USE
    from utils.cached_runs import (
        CACHED_RUNS,
        Role,
        Seen,
        own_counts,
        stored_facts,
    )


class PromptManagerBase:
    """Mixin providing shared prompt management logic for ComfyUI nodes.

    Handles database connection, prompt saving with deduplication,
    tag parsing, search, gallery system lifecycle, and cleanup.
    Subclasses only need to define ComfyUI-specific class attributes
    (INPUT_TYPES, RETURN_TYPES, FUNCTION) and their execution method.
    """

    def __init__(self, logger_name: str = "prompt_manager.node"):
        self.logger = get_logger(logger_name)
        self.logger.debug(f"Initializing {self.__class__.__name__} node")

        self.db = PromptDatabase()
        self.prompt_tracker = get_prompt_tracker(self.db)
        self.image_monitor = get_image_monitor(self.db, self.prompt_tracker)
        self.comfyui_integration = get_comfyui_integration()

        self._start_gallery_system()
        self.logger.debug(f"{self.__class__.__name__} node initialization completed")

    def _save_prompt_to_database(
        self,
        text: str,
        category: Optional[str] = None,
        tags: Optional[list] = None,
        count_run: bool = False,
    ) -> Optional[int]:
        """Save the prompt to the SQLite database.

        Runs are normally counted by the queue hook (utils/usage_tracking.py), so a
        save only counts one when asked to (text the hook could not see, unless
        this job already counted the node from ComfyUI's cache) or when the hook
        queued this prompt before it existed. The two are independent: the
        pending catch-up is written even when ``count_run`` is False.

        Args:
            text: The prompt text
            category: Optional category
            tags: List of tags
            count_run: Count this execution as a run of the prompt

        Returns:
            The prompt ID if saved successfully, None otherwise
        """
        try:
            prompt_hash = self._generate_hash(text)
            self.logger.debug(f"Generated hash for prompt: {prompt_hash[:16]}...")

            existing = self.db.get_prompt_by_hash(prompt_hash)
            if existing:
                self.logger.info(
                    f"Found existing prompt with ID {existing['id']}, updating metadata"
                )
                if any([category, tags]):
                    self.db.update_prompt_metadata(
                        prompt_id=existing["id"], category=category, tags=tags
                    )
                    self.logger.debug("Updated metadata for existing prompt")
                if count_run:
                    self._record_use(existing["id"])
                return existing["id"]

            self.logger.debug(
                f"Saving new prompt with category: {category}, tags: {tags}"
            )
            prompt_id = self.db.save_prompt(
                text=text, category=category, tags=tags, prompt_hash=prompt_hash
            )

            if prompt_id:
                self.logger.debug(f"Successfully saved new prompt with ID: {prompt_id}")
                pending = getattr(self, "pending_first_use", PENDING_FIRST_USE)
                runs = pending.consume(prompt_hash) + (1 if count_run else 0)
                if runs:
                    self._record_use(prompt_id, times=runs)
            else:
                self.logger.warning("Failed to save prompt - no ID returned")

            return prompt_id

        except Exception as e:
            self.logger.error(f"Error saving prompt to database: {e}")
            return None

    def _prompt_role(
        self, prompt_graph: Any, unique_id: Any, text: str
    ) -> Dict[str, bool]:
        """How this node's execution relates to the run, from the queued graph.

        Returns flags:
            is_run_prompt: feeds a sampler's positive input (images belong to it)
            seen_at_queue: the queue hook resolved exactly this text, so it already
                counted the run and the image's metadata can identify the prompt
            known: the graph and node id were available
        """
        if not isinstance(prompt_graph, dict) or unique_id is None:
            return {"is_run_prompt": True, "seen_at_queue": False, "known": False}
        node_id = str(unique_id)
        resolved = resolve_text(prompt_graph, node_id)
        return {
            "is_run_prompt": node_id in run_prompt_nodes(prompt_graph),
            "seen_at_queue": resolved is not None
            and generate_prompt_hash(resolved) == generate_prompt_hash(text),
            "known": True,
        }

    def _track_prompt_execution(
        self,
        text: str,
        encoding_text: str,
        category: Optional[str],
        tags: Optional[list],
        additional_data: Dict[str, Any],
        prompt_graph: Any = None,
        unique_id: Any = None,
    ) -> Optional[int]:
        """Save the prompt and, for the run's positive prompt, register it for linking.

        Negative-prompt nodes are saved but never become the prompt their images are
        linked to. Text the queue hook could resolve is counted there and linked from
        the image's own metadata; anything else (PromptSearchList batches, unknown
        text nodes, or a resolution that doesn't match) is counted here and pushed
        onto the FIFO queue. Without graph context the pre-3.2.4 behaviour is kept.
        """
        role = self._prompt_role(prompt_graph, unique_id, text)
        batch_item = (
            role["known"] and role["is_run_prompt"] and not role["seen_at_queue"]
        )
        counts = own_counts(
            type(self).__name__,
            stored_facts(text),
            Role(
                frozenset({"text"} if role["known"] and role["is_run_prompt"] else ())
            ),
            Seen(text=role["seen_at_queue"]),
        )
        prompt_id = self._save_prompt_to_database(
            text=text,
            category=category,
            tags=tags,
            count_run=bool(counts) and not self._replayed(unique_id),
        )
        if role["is_run_prompt"] and not prompt_id:
            # The save failed: an older entry of this key must not stand in (#342)
            self._mark_untracked(prompt_graph, unique_id)
        elif role["is_run_prompt"]:
            self.prompt_tracker.set_current_prompt(
                prompt_text=encoding_text,
                additional_data={**additional_data, "prompt_id": prompt_id},
                push_to_queue=batch_item or not role["known"],
                **self._run_identity(prompt_graph, unique_id),
            )
        return prompt_id

    def _cached_runs(self) -> Any:
        return getattr(self, "cached_runs", CACHED_RUNS)

    def _replayed(self, unique_id: Any) -> bool:
        """This job already counted the node from ComfyUI's cache (#347).

        A RAM-pressure cache can free the entry after ``execution_cached``, so
        the node executes in a job that replayed it: its own count is skipped.
        """
        try:
            return unique_id is not None and self._cached_runs().replayed(unique_id)
        except Exception as error:
            self.logger.debug(f"Could not check the cached-run replay: {error}")
            return False

    def _record_cached_item(
        self, unique_id: Any, outputs: tuple, text: Any, negative_text: Any = None
    ) -> None:
        """Remember this execution's outputs and stored text for cached jobs (#347)."""
        if unique_id is None:
            return
        try:
            self._cached_runs().record_item(
                unique_id, outputs, stored_facts(text, negative_text)
            )
        except Exception as error:
            self.logger.debug(f"Could not record the cached-run receipt: {error}")

    @staticmethod
    def _run_identity(prompt_graph: Any, unique_id: Any) -> Dict[str, Any]:
        """The tracker's proof that an image belongs to this execution (#342)."""
        return {
            "run_key": prompt_node_key(prompt_graph, unique_id),
            "run_id": executing_run_id(),
        }

    def _skip_empty_prompt(self, prompt_graph: Any, unique_id: Any) -> None:
        """The ``text`` editor is empty: save and track nothing (#342).

        Prepend and append text are deliberately never stored as the prompt, so
        an empty editor has no prompt to save, count or link. For the run's
        positive node this is logged at info and the node's run key is marked
        empty (``_mark_untracked``).
        """
        self._mark_untracked(
            prompt_graph,
            unique_id,
            "Prompt text is empty (prepend and append text are not stored): "
            "nothing saved or tracked, and this run's images stay unlinked",
        )

    def _mark_untracked(
        self, prompt_graph: Any, unique_id: Any, message: Optional[str] = None
    ) -> None:
        """This execution of a positive node has no prompt to link (#342).

        An empty editor, or a save or tracking failure. The node's run key is
        marked with a placeholder, so no earlier prompt of that key can be
        linked to this run's images, and a batch item keeps its queue place.
        ``message`` is logged at info for a positive node.
        """
        try:
            role = self._prompt_role(prompt_graph, unique_id, "")
            if not role["is_run_prompt"]:
                if message:
                    self.logger.debug("Empty text on a non-positive node; not saved")
                return
            if message:
                self.logger.info(message)
            self.prompt_tracker.mark_empty(
                push_to_queue=role["known"],
                **self._run_identity(prompt_graph, unique_id),
            )
        except Exception as error:
            # Tracking must never block generation
            self.logger.warning(f"Could not mark the untracked prompt: {error}")

    def _record_use(self, prompt_id: int, times: int = 1) -> None:
        """Count runs; a tracking failure must never block generation."""
        try:
            self.db.record_prompt_use(prompt_id, times=times)
        except Exception as e:
            self.logger.warning(f"Could not record use of prompt {prompt_id}: {e}")

    def _inject_lora_trigger_words(self, text: str) -> str:
        """Append LoRA trigger words if integration is enabled.

        Returns the text unchanged if the integration is disabled or
        LoraManager is not installed.
        """
        try:
            from .py.config import IntegrationConfig
        except ImportError:
            try:
                from py.config import IntegrationConfig
            except ImportError:
                return text

        if (
            not IntegrationConfig.LORA_MANAGER_ENABLED
            or not IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED
        ):
            return text

        try:
            from .py.lora_utils import get_trigger_cache, inject_trigger_words
        except ImportError:
            try:
                from py.lora_utils import get_trigger_cache, inject_trigger_words
            except ImportError:
                return text

        cache = get_trigger_cache()

        # Lazy-load cache on first use; a failed load waits before the next try
        # (#355: never a rescan of LoraManager's folders on every run).
        retry_due = getattr(cache, "retry_due", lambda: True)
        if not cache.is_loaded and IntegrationConfig.LORA_MANAGER_PATH and retry_due():
            cache.load(IntegrationConfig.LORA_MANAGER_PATH)

        modified, injected = inject_trigger_words(text, cache)
        if injected:
            self.logger.info(f"Injected trigger words: {', '.join(injected)}")
        return modified

    def _generate_hash(self, text: str) -> str:
        """The prompt's deduplication hash (see utils.hashing.generate_prompt_hash)."""
        return generate_prompt_hash(text)

    def _parse_tags(self, tags_string: str) -> Optional[list]:
        """Parse comma-separated tags string into a list.

        Args:
            tags_string: Comma-separated string of tags

        Returns:
            List of parsed tags, or None if no valid tags found
        """
        if not tags_string or not tags_string.strip():
            return None

        tags = [tag.strip() for tag in tags_string.split(",") if tag.strip()]
        return tags if tags else None

    def _search_prompts(self, search_text: str = "") -> List[Dict[str, Any]]:
        """Search for past prompts by text content.

        Args:
            search_text: Text to search for in prompt database

        Returns:
            List of matching prompt dictionaries with metadata
        """
        try:
            if not search_text or not search_text.strip():
                return []

            try:
                from .py.config import PromptManagerConfig

                max_results = PromptManagerConfig.MAX_SEARCH_RESULTS
            except Exception:
                max_results = 100

            results = self.db.search_prompts(
                text=search_text.strip(),
                category=None,
                tags=None,
                rating_min=None,
                limit=max_results,
            )

            return results

        except Exception as e:
            self.logger.error(f"Error searching prompts: {e}")
            return []

    def search_prompts_api(self, search_text: str = "") -> List[Dict[str, Any]]:
        """API method for JavaScript UI to search prompts."""
        return self._search_prompts(search_text=search_text)

    def get_recent_prompts_api(self, limit: int = 20) -> List[Dict[str, Any]]:
        """API method for JavaScript UI to get recent prompts."""
        try:
            return self.db.get_recent_prompts(limit=limit)
        except Exception as e:
            self.logger.error(f"Error getting recent prompts: {e}")
            return []

    def _start_gallery_system(self):
        """Initialize and start the gallery monitoring system."""
        try:
            self.logger.debug("Starting gallery system...")
            self.image_monitor.start_monitoring()
            self.logger.debug("Gallery system started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start gallery system: {e}")
            self.logger.warning("Gallery features will be disabled")

    def get_gallery_status(self) -> Dict[str, Any]:
        """Get status of the gallery system."""
        return {
            "image_monitor": self.image_monitor.get_status(),
            "prompt_tracker": self.prompt_tracker.get_status(),
        }

    def cleanup_gallery_system(self):
        """Clean up gallery system resources.

        Note: The image monitor is a singleton shared across all node instances,
        so we intentionally do NOT stop it here. It should run for the entire
        ComfyUI session. Stopping it would kill monitoring for all nodes.
        """
        self.logger.debug("Gallery system cleaned up (monitor continues running)")
