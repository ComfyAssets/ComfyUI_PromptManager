"""PromptTracker implementation for robust prompt-to-image tracking.

This module maintains the association between PromptManager nodes and
generated images by tracking execution flow through ComfyUI's pipeline.
"""

import asyncio
import json
import threading
import time
from uuid import uuid4
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Import with fallbacks
try:
    from promptmanager.loggers import get_logger  # type: ignore
except ImportError:  # pragma: no cover
    from loggers import get_logger  # type: ignore

logger = get_logger("promptmanager.tracking.prompt_tracker")


@dataclass
class TrackingData:
    """Data structure for tracking prompt execution."""

    node_id: str
    unique_id: str
    prompt_text: str
    negative_prompt: str = ""
    workflow_data: Dict[str, Any] = field(default_factory=dict)
    execution_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    connected_nodes: Set[str] = field(default_factory=set)
    images_generated: List[str] = field(default_factory=list)
    confidence_score: float = 1.0
    workflow_key: Optional[str] = None  # Workflow-level identifier for first-prompt-wins


class PromptTracker:
    """Tracks prompts through ComfyUI execution pipeline.
    
    This class maintains the association between PromptManager nodes
    and the images they generate, handling multiple nodes and concurrent
    executions correctly.
    """
    
    def __init__(self, db_path: str = "prompts.db", thumbnail_service=None):
        """Initialize the PromptTracker.

        Args:
            db_path: Path to the database file
            thumbnail_service: Optional EnhancedThumbnailService for auto-generation
        """
        self.db_path = db_path
        self.db_instance = None  # Will be set by get_prompt_tracker if provided
        self.thumbnail_service = thumbnail_service  # For auto-generating thumbnails

        # Thread-safe tracking storage
        self._lock = threading.RLock()
        self._active_prompts: Dict[str, TrackingData] = {}
        self._execution_graph: Dict[str, Set[str]] = {}
        self._node_outputs: Dict[str, Any] = {}

        # Metrics for validation
        self.metrics = {
            "total_tracked": 0,
            "successful_pairs": 0,
            "failed_pairs": 0,
            "multi_node_workflows": 0,
            "avg_confidence": 0.0,
            "skipped_prompts": 0,  # Detailer prompts skipped via first-prompt-wins
        }

    
    def register_prompt(
        self,
        node_id: str,
        unique_id: str,
        prompt: str,
        negative_prompt: str = "",
        workflow: Optional[Dict] = None,
        extra_data: Optional[Dict] = None
    ) -> str:
        """Register a prompt from a PromptManager node.

        Implements first-prompt-wins strategy: Only the first prompt registered
        for a unique_id will be tracked. Subsequent prompts (e.g., from detailer
        nodes) will be ignored to ensure we capture the main generation prompt.

        Args:
            node_id: The node's ID in the workflow
            unique_id: ComfyUI's unique execution ID
            prompt: The positive prompt text
            negative_prompt: The negative prompt text
            workflow: The complete workflow data
            extra_data: Additional metadata

        Returns:
            Execution ID for this prompt (or "skipped" if already registered)
        """
        with self._lock:
            # FIRST-PROMPT-WINS: If prompt already registered for this unique_id, skip it
            if unique_id in self._active_prompts:
                # Update metrics
                self.metrics["skipped_prompts"] += 1
                return "skipped"  # Return special value to indicate skip

            # Clean up old entries when a new workflow starts
            # Only clean if we have entries from a different workflow (older than 60 seconds)
            if self._active_prompts:
                current_time = time.time()
                all_old = all(current_time - t.timestamp > 60 for t in self._active_prompts.values())
                if all_old:
                    self._active_prompts.clear()
                    self._execution_graph.clear()

            tracking = TrackingData(
                node_id=node_id,
                unique_id=unique_id,
                prompt_text=prompt,
                negative_prompt=negative_prompt,
                workflow_data=workflow or {},
                metadata=extra_data or {}
            )

            # Store by unique_id for retrieval during save
            self._active_prompts[unique_id] = tracking

            # Update execution graph
            if node_id not in self._execution_graph:
                self._execution_graph[node_id] = set()

            # Update metrics
            self.metrics["total_tracked"] += 1

            # Count multi-node workflows
            prompt_nodes = [k for k in self._execution_graph.keys()
                          if k.startswith("PromptManager")]
            if len(prompt_nodes) > 1:
                self.metrics["multi_node_workflows"] += 1

            return tracking.execution_id
    
    def register_connection(self, from_node: str, to_node: str) -> None:
        """Register a connection between nodes in the execution graph.
        
        Args:
            from_node: Source node ID
            to_node: Destination node ID
        """
        with self._lock:
            if from_node not in self._execution_graph:
                self._execution_graph[from_node] = set()
            self._execution_graph[from_node].add(to_node)
            
            # Update connected nodes for active prompts
            for tracking in self._active_prompts.values():
                if tracking.node_id == from_node:
                    tracking.connected_nodes.add(to_node)
    
    def get_prompt_for_save(
        self,
        save_node_id: str,
        unique_id: Optional[str] = None
    ) -> Optional[TrackingData]:
        """Get the prompt data for a SaveImage node.

        Args:
            save_node_id: The SaveImage node's ID
            unique_id: Optional unique ID if provided by ComfyUI

        Returns:
            TrackingData if found, None otherwise
        """
        with self._lock:
            # Direct lookup if unique_id provided
            if unique_id:
                if unique_id in self._active_prompts:
                    return self._active_prompts[unique_id]

                # Check if it's a type mismatch issue (string vs int)
                for key in self._active_prompts.keys():
                    if str(key) == str(unique_id):
                        logger.warning(f"Type mismatch in unique_id lookup: key={key} (type={type(key)}), unique_id={unique_id} (type={type(unique_id)})")
                        return self._active_prompts[key]

            # Fallback: if caller passed a PromptManager node_id as save_node_id, use the most recent entry
            if save_node_id and save_node_id.startswith("PromptManager"):
                candidates = [t for t in self._active_prompts.values() if t.node_id == save_node_id]
                if candidates:
                    return max(candidates, key=lambda t: t.timestamp)
            
            # Otherwise, trace through the graph
            prompt_sources = self._find_prompt_sources(save_node_id)
            
            if not prompt_sources:
                logger.warning(f"No prompt source found for SaveImage {save_node_id}")
                return None
            
            # If multiple sources, use the FIRST (oldest) one with highest confidence
            # This implements first-prompt-wins for the SaveImage fallback path
            best_match = None
            best_score = 0.0
            best_timestamp = float('inf')  # Use oldest when scores are tied

            for source_id in prompt_sources:
                for tracking in self._active_prompts.values():
                    if tracking.node_id == source_id:
                        score = self._calculate_confidence(tracking, save_node_id)
                        # Prefer higher score, or if scores are equal, prefer earlier timestamp (first prompt)
                        if score > best_score or (score == best_score and tracking.timestamp < best_timestamp):
                            best_score = score
                            best_match = tracking
                            best_timestamp = tracking.timestamp

            if best_match:
                best_match.confidence_score = best_score

            return best_match

    def debug_active_ids(self) -> list:
        """Return a snapshot of active unique_ids for debugging."""
        with self._lock:
            return list(self._active_prompts.keys())
    
    def record_image_saved(
        self,
        image_path: str,
        tracking_data: TrackingData,
        metadata: Optional[Dict] = None
    ) -> None:
        """Record that an image was saved with associated prompt.

        Args:
            image_path: Path to the saved image
            tracking_data: The tracking data for this image
            metadata: Additional metadata to store
        """
        from pathlib import Path

        try:
            normalized_path = str(Path(image_path).expanduser().resolve())
        except Exception:
            normalized_path = str(Path(image_path).absolute())

        with self._lock:
            tracking_data.images_generated.append(normalized_path)

            # Get the prompt_id from the tracking data metadata
            prompt_id = tracking_data.metadata.get('prompt_id')

            # DEBUG: Log what we have
            import os
            if os.getenv("PROMPTMANAGER_DEBUG", "0") == "1":
                print(f"\n🔍 [record_image_saved] Image: {Path(image_path).name}")
                print(f"   unique_id: {tracking_data.unique_id}")
                print(f"   node_id: {tracking_data.node_id}")
                print(f"   metadata keys: {list(tracking_data.metadata.keys())}")
                print(f"   prompt_id from metadata: {prompt_id}")

            if prompt_id:
                # Use the stored database instance if available, otherwise create new one
                if hasattr(self, 'db_instance') and self.db_instance:
                    db = self.db_instance
                    print(f"   📚 Using stored database instance")
                else:
                    from ..database import PromptDatabase
                    db = PromptDatabase()
                    print(f"   ⚠️ Warning: Creating new database instance (not optimal)")

                metadata_payload = {
                    "unique_id": tracking_data.unique_id,
                    "node_id": tracking_data.node_id,
                    "confidence_score": tracking_data.confidence_score,
                    "generation_time": datetime.fromtimestamp(tracking_data.timestamp).isoformat(),
                    "workflow_data": tracking_data.workflow_data,
                }
                if metadata:
                    metadata_payload.update(metadata)

                params = metadata_payload.get("parameters")
                if isinstance(params, dict):
                    params.setdefault("absolute_path", normalized_path)
                    params.setdefault("relative_path", Path(normalized_path).name)
                else:
                    metadata_payload["parameters"] = {"absolute_path": normalized_path}

                # Link the image to the prompt and get database ID for thumbnail generation
                db_id = db.link_image_to_prompt(
                    prompt_id=prompt_id,
                    image_path=normalized_path,
                    metadata=metadata_payload
                )
            else:
                # Track the image even without prompt_id (store metadata for later linking)
                if not hasattr(tracking_data, 'pending_images'):
                    tracking_data.metadata['pending_images'] = []
                tracking_data.metadata['pending_images'].append({
                    'path': normalized_path,
                    'metadata': metadata,
                    'timestamp': time.time()
                })
                db_id = None  # No DB ID yet for pending images

            # Update metrics
            self.metrics["successful_pairs"] += 1

            # AUTO-GENERATE THUMBNAILS: Generate thumbnails immediately after image save
            if self.thumbnail_service and db_id is not None:
                try:
                    # Generate thumbnails in background with ID-based naming
                    self._auto_generate_thumbnails(normalized_path, db_id)
                except Exception as e:
                    logger.warning(f"Auto-thumbnail generation failed for {Path(normalized_path).name}: {e}")
                    # Don't fail the entire operation if thumbnail generation fails
    
    def _auto_generate_thumbnails(self, image_path: str, db_id: int):
        """Generate thumbnails for a newly saved image in background.

        Respects user's thumbnail size preferences from config.
        Only generates thumbnails for sizes that are enabled in settings.
        Uses ID-based thumbnail naming for consistency.

        Args:
            image_path: Path to the image file
            db_id: Database ID of the image record (for ID-based thumbnail naming)
        """
        try:
            import hashlib
            from pathlib import Path
            from ..services.enhanced_thumbnail_service import ThumbnailTask, ThumbnailStatus
            from ..config import config
            
            source_path = Path(image_path)
            if not source_path.exists():
                logger.debug(f"Skipping auto-thumbnail: file not found {image_path}")
                return
            
            # Check if auto-generation is enabled
            auto_generate = config.get('thumbnail.auto_generate', True)
            if not auto_generate:
                logger.debug(f"Auto-generation disabled in settings, skipping {source_path.name}")
                return
            
            # Get enabled sizes from config
            enabled_sizes_str = config.get('thumbnail.enabled_sizes', '')
            if enabled_sizes_str:
                # Parse comma-separated list
                enabled_sizes = [s.strip() for s in enabled_sizes_str.split(',') if s.strip()]
            else:
                # Default to small, medium, large (not xlarge to save space)
                enabled_sizes = ['small', 'medium', 'large']
            
            if not enabled_sizes:
                logger.debug(f"No thumbnail sizes enabled, skipping {source_path.name}")
                return
            
            # Check if it's a video or image
            is_video = self.thumbnail_service.ffmpeg.is_video(source_path)
            
            # Generate thumbnails only for enabled sizes
            for size_name, size_dims in self.thumbnail_service.base_generator.SIZES.items():
                # Skip if this size is not enabled
                if size_name not in enabled_sizes:
                    logger.debug(f"Skipping {size_name} thumbnail (disabled in settings)")
                    continue

                # Use ID-based thumbnail naming: {db_id}_{size}.jpg
                thumbnail_path = self.thumbnail_service.thumbnail_dir / size_name / f"{db_id}_{size_name}.jpg"
                thumbnail_path.parent.mkdir(parents=True, exist_ok=True)

                # Create task with database ID for ID-based naming
                task = ThumbnailTask(
                    image_id=hashlib.md5(str(source_path).encode()).hexdigest(),
                    source_path=source_path,
                    thumbnail_path=thumbnail_path,
                    size=size_dims,
                    format='jpg',
                    size_name=size_name,
                    is_video=is_video,
                    db_id=db_id  # ID-based thumbnail naming
                )
                
                # Generate thumbnail synchronously (it's already in a background worker thread)
                result = self.thumbnail_service._generate_single_thumbnail(task)

                if result.status == ThumbnailStatus.GENERATED:
                    # Update database with ID-based thumbnail path
                    try:
                        from ..database.utils import get_db_connection
                        with get_db_connection(self.db.db_path) as conn:
                            cursor = conn.cursor()
                            column_name = f'thumbnail_{size_name}_path'
                            cursor.execute(f"""
                                UPDATE generated_images
                                SET {column_name} = ?
                                WHERE id = ?
                            """, (str(thumbnail_path), db_id))
                            conn.commit()
                        logger.debug(f"Auto-generated {size_name} thumbnail for {source_path.name} -> {db_id}_{size_name}.jpg")
                    except Exception as db_err:
                        logger.error(f"Failed to update database thumbnail path: {db_err}")
                elif result.status == ThumbnailStatus.FAILED:
                    logger.debug(f"Failed to auto-generate {size_name} thumbnail: {result.error}")
                    
        except Exception as e:
            logger.error(f"Error in _auto_generate_thumbnails: {e}", exc_info=True)
    
    def _find_prompt_sources(self, target_node: str) -> Set[str]:
        """Find all PromptManager nodes that connect to a target node.
        
        Args:
            target_node: The node to trace back from
            
        Returns:
            Set of PromptManager node IDs
        """
        sources = set()
        visited = set()
        
        def trace_back(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            
            # Check if this is a PromptManager
            if "PromptManager" in node_id:
                sources.add(node_id)
            
            # Find nodes that connect to this one
            for from_node, connections in self._execution_graph.items():
                if node_id in connections:
                    trace_back(from_node)
        
        trace_back(target_node)
        return sources
    
    def _calculate_confidence(
        self,
        tracking: TrackingData,
        save_node: str
    ) -> float:
        """Calculate confidence score for prompt-to-image association.
        
        Args:
            tracking: The tracking data
            save_node: The SaveImage node ID
            
        Returns:
            Confidence score between 0 and 1
        """
        score = 1.0
        
        # Reduce confidence if there are multiple prompt sources
        prompt_sources = self._find_prompt_sources(save_node)
        if len(prompt_sources) > 1:
            score *= 0.8
        
        # Increase confidence if there's a direct connection
        if save_node in tracking.connected_nodes:
            score *= 1.2
        
        # Consider time factor (more recent = higher confidence)
        age = time.time() - tracking.timestamp
        if age > 60:  # Over 1 minute old
            score *= 0.9
        
        # Clamp between 0 and 1
        return min(1.0, max(0.0, score))
    
    def clear_workflow_tracking(self) -> None:
        """Clear all tracking data for a new workflow."""
        with self._lock:
            self._active_prompts.clear()
            self._execution_graph.clear()
            self._node_outputs.clear()

    def cleanup_old_tracking(self, max_age_seconds: int = 3600) -> int:
        """Clean up old tracking data from abandoned workflows.

        Args:
            max_age_seconds: Maximum age in seconds (default: 3600 = 1 hour)
            
        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            current_time = time.time()
            to_remove = []
            
            for unique_id, tracking in self._active_prompts.items():
                if current_time - tracking.timestamp > max_age_seconds:
                    to_remove.append(unique_id)
            
            for unique_id in to_remove:
                del self._active_prompts[unique_id]

            return len(to_remove)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get tracking metrics for validation.
        
        Returns:
            Dictionary of metrics
        """
        with self._lock:
            accuracy = 0.0
            if self.metrics["total_tracked"] > 0:
                accuracy = (self.metrics["successful_pairs"] / 
                          self.metrics["total_tracked"]) * 100
            
            return {
                **self.metrics,
                "accuracy_rate": accuracy,
                "active_prompts": len(self._active_prompts),
                "graph_nodes": len(self._execution_graph),
            }
    
    def reset_metrics(self) -> None:
        """Reset tracking metrics."""
        with self._lock:
            self.metrics = {
                "total_tracked": 0,
                "successful_pairs": 0,
                "failed_pairs": 0,
                "multi_node_workflows": 0,
                "avg_confidence": 0.0,
                "skipped_prompts": 0,
            }
    
    async def start_cleanup_task(self) -> None:
        """Start background task for periodic cleanup."""
        while True:
            await asyncio.sleep(60)  # Run every minute
            self.cleanup_old_tracking()